"""User-bound Windows encryption and narrowly scoped browser request headers."""
import base64
import ctypes
import json
import os
from contextlib import contextmanager
from urllib.parse import urljoin, urlsplit

import requests


def http_url(value):
    if not isinstance(value, str) or len(value) > 16384 or any(ord(c) < 32 for c in value):
        raise ValueError('Invalid media URL')
    parsed = urlsplit(value)
    if parsed.scheme not in ('http', 'https') or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError('Only HTTP/HTTPS URLs without embedded credentials are supported')
    parsed.port  # Validate malformed ports before any network request.
    return value


def origin(value):
    parsed = urlsplit(http_url(value))
    return parsed.scheme, parsed.hostname.lower(), parsed.port or (443 if parsed.scheme == 'https' else 80)


def normalize_context(url, context):
    if context is None:
        return None
    if not isinstance(context, dict) or not isinstance(context.get('headers', {}), dict):
        raise ValueError('Invalid browser request context')
    headers = {}
    for key, value in context.get('headers', {}).items():
        canonical = {'user-agent': 'User-Agent', 'referer': 'Referer', 'cookie': 'Cookie'}.get(key.lower())
        if not canonical:
            continue
        if not isinstance(value, str) or len(value) > 32768 or any(ord(c) < 32 or ord(c) == 127 for c in value):
            raise ValueError('Invalid browser request header')
        value.encode('latin-1')
        headers[canonical] = value
    return {'url': http_url(url), 'headers': headers, 'media': True}


def normalize_stream_context(url, context):
    """Keep cookie scope for manifests whose fragments may use another CDN."""
    result = normalize_context(url, context or {})
    result['headers'].pop('Cookie', None)
    if result['headers'].get('Referer'):
        parsed = urlsplit(http_url(result['headers']['Referer']))
        result['headers']['Referer'] = f'{parsed.scheme}://{parsed.netloc}/'
    host = urlsplit(url).hostname.lower()
    cookies = (context or {}).get('cookies', [])
    if not isinstance(cookies, list) or len(cookies) > 300:
        raise ValueError('Invalid media cookies')
    result['cookies'] = []
    for cookie in cookies:
        if not isinstance(cookie, dict):
            raise ValueError('Invalid media cookie')
        values = {key: cookie.get(key, '') for key in ('name', 'value', 'domain', 'path')}
        if any(not isinstance(v, str) or len(v) > 32768 or any(ord(c) < 32 or ord(c) == 127 for c in v)
               for v in values.values()):
            raise ValueError('Invalid media cookie')
        domain = values['domain'].lower().lstrip('.')
        host_only = cookie.get('hostOnly', True) is not False
        if not domain or (host != domain and (host_only or not host.endswith('.' + domain))):
            raise ValueError('Cookie outside media domain')
        if not values['name'] or not values['path'].startswith('/'):
            raise ValueError('Invalid media cookie scope')
        result['cookies'].append({**values, 'domain': domain if host_only else '.' + domain,
                                  'hostOnly': host_only, 'secure': bool(cookie.get('secure'))})
    return result


class _Blob(ctypes.Structure):
    _fields_ = [('size', ctypes.c_uint32), ('data', ctypes.POINTER(ctypes.c_ubyte))]


def _crypt(data, decrypt=False):
    if os.name != 'nt':
        raise RuntimeError('Browser integration currently requires Windows DPAPI')
    buffer = ctypes.create_string_buffer(data)
    source = _Blob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte)))
    result = _Blob()
    crypt32 = ctypes.WinDLL('crypt32', use_last_error=True)
    kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    kernel32.LocalFree.restype = ctypes.c_void_p
    function = crypt32.CryptUnprotectData if decrypt else crypt32.CryptProtectData
    function.argtypes = [ctypes.POINTER(_Blob), ctypes.c_void_p, ctypes.c_void_p,
                         ctypes.c_void_p, ctypes.c_void_p, ctypes.c_uint32, ctypes.POINTER(_Blob)]
    function.restype = ctypes.c_int
    if not function(ctypes.byref(source), None, None, None, None, 1, ctypes.byref(result)):
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        return ctypes.string_at(result.data, result.size)
    finally:
        kernel32.LocalFree(result.data)


def protect(value):
    data = json.dumps(value, ensure_ascii=True).encode('utf-8')
    return base64.b64encode(_crypt(data)).decode('ascii')


def unprotect(value):
    return json.loads(_crypt(base64.b64decode(value, validate=True), decrypt=True))


@contextmanager
def browser_get(url, context, **kwargs):
    """Do not forward browser credentials or referrers across origins."""
    base_headers = dict(kwargs.pop('headers', {}))
    kwargs.pop('allow_redirects', None)
    current = http_url(url)
    for _ in range(6):
        headers = dict(base_headers)
        if origin(current) == origin(context['url']):
            headers.update(context['headers'])
        response = requests.get(current, headers=headers, allow_redirects=False, **kwargs)
        if response.status_code not in (301, 302, 303, 307, 308):
            try:
                response.raise_for_status()
                mime = response.headers.get('Content-Type', '').split(';')[0].strip().lower()
                if mime not in ('video/mp4', 'video/webm', 'application/octet-stream'):
                    raise ValueError('Response is not a supported MP4/WebM video')
                yield response
            finally:
                response.close()
            return
        location = response.headers.get('Location')
        response.close()
        if not location:
            raise ValueError('Redirect has no target')
        target = http_url(urljoin(current, location))
        if urlsplit(current).scheme == 'https' and urlsplit(target).scheme != 'https':
            raise ValueError('HTTPS downgrade rejected')
        current = target
    raise ValueError('Too many redirects')
