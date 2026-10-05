"""Authenticated loopback IPC. Only the registered native host knows the token."""
import ctypes
import errno
import hashlib
import http.client
import json
import os
from pathlib import Path
import re
import secrets
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import unquote, urlsplit

from downloader.browser.security import http_url, normalize_context, protect, unprotect
from downloader.utils.config import get_app_root
from downloader.core.youtube_downloader import youtube_url, require_tools

MAX_MESSAGE = 256 * 1024


def instance_key():
    value = os.path.normcase(os.path.abspath(get_app_root())) + os.path.expanduser('~')
    return hashlib.sha256(value.encode('utf-8')).hexdigest()[:24]


def endpoint_file():
    root = Path(os.environ.get('LOCALAPPDATA', str(Path.home()))) / 'LaoWangDownloader'
    return root / (instance_key() + '.bridge')


class InstanceGuard:
    def __init__(self):
        self.handle = None
        self.primary = True
        if os.name == 'nt':
            self.kernel = ctypes.WinDLL('kernel32', use_last_error=True)
            self.kernel.CreateMutexW.argtypes = [ctypes.c_void_p, ctypes.c_bool, ctypes.c_wchar_p]
            self.kernel.CreateMutexW.restype = ctypes.c_void_p
            self.kernel.CloseHandle.argtypes = [ctypes.c_void_p]
            self.handle = self.kernel.CreateMutexW(None, False, 'Local\\LaoWang-' + instance_key())
            if not self.handle:
                raise ctypes.WinError(ctypes.get_last_error())
            self.primary = ctypes.get_last_error() != 183

    def close(self):
        if self.handle:
            self.kernel.CloseHandle(self.handle)
            self.handle = None


def call_desktop(message, path=None, timeout=90):
    endpoint = unprotect(Path(path or endpoint_file()).read_text(encoding='ascii'))
    connection = http.client.HTTPConnection('127.0.0.1', int(endpoint['port']), timeout=timeout)
    try:
        body = json.dumps(message, ensure_ascii=True).encode('utf-8')
        if len(body) > MAX_MESSAGE:
            raise ValueError('Message too large')
        connection.request('POST', '/v1', body, {
            'Authorization': 'Bearer ' + endpoint['token'], 'Content-Type': 'application/json'})
        response = connection.getresponse()
        if response.status != 200:
            raise ConnectionError('Desktop bridge rejected the request')
        data = response.read(MAX_MESSAGE + 1)
        if len(data) > MAX_MESSAGE:
            raise ValueError('Response too large')
        return json.loads(data)
    finally:
        connection.close()


def video_filename(message):
    kind = message.get('kind')
    if kind == 'youtube':
        url = youtube_url(message.get('url'))
        message = {**message, 'kind': 'mp4', 'url': url,
                   'filename': message.get('filename') or 'YouTube-' + url.split('v=')[1]}
        kind = 'mp4'
    if kind not in ('mp4', 'webm'):
        raise ValueError('本版仅下载 MP4、WebM；分段视频暂不支持')
    url = http_url(message.get('url'))
    candidate = message.get('filename') or unquote(urlsplit(url).path.rsplit('/', 1)[-1]) or 'video'
    if not isinstance(candidate, str):
        raise ValueError('Invalid filename')
    candidate = re.sub(r'[<>:"/\\|?*\x00-\x1f\x7f]', '_', candidate).strip(' .')[:140]
    candidate = re.sub(r'\.(mp4|webm)$', '', candidate, flags=re.I).strip(' .') or 'video'
    if re.fullmatch(r'(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\..*)?', candidate, re.I):
        candidate = '_' + candidate
    return candidate + '.' + kind


class BridgeServer:
    def __init__(self, manager, show_window, path=None):
        self.manager = manager
        self.show_window = show_window
        self.path = Path(path or endpoint_file())
        self.token = secrets.token_urlsafe(32)
        self.lock = threading.Lock()
        self.receipts = {}
        self.closed = False
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def setup(self):
                super().setup()
                self.connection.settimeout(10)

            def log_message(self, *_args):
                pass

            def do_POST(self):
                try:
                    length = int(self.headers.get('Content-Length', '0'))
                    if not 0 < length <= MAX_MESSAGE:
                        self.send_error(413)
                        return
                    raw = self.rfile.read(length)
                    if len(raw) != length:
                        self.send_error(400)
                        return
                except (ValueError, OSError):
                    self.send_error(400)
                    return
                if (self.path != '/v1' or self.headers.get('Origin') is not None
                        or self.headers.get('Host') != f'127.0.0.1:{self.server.server_port}'
                        or not secrets.compare_digest(self.headers.get('Authorization', ''), 'Bearer ' + owner.token)):
                    self.send_error(403)
                    return
                try:
                    result = owner.dispatch(json.loads(raw))
                except Exception:
                    # Request URLs and headers can contain credentials; never echo exceptions.
                    result = {'ok': False, 'error': '视频任务接收失败，请检查链接、登录状态及文件大小'}
                data = json.dumps(result, ensure_ascii=True).encode('utf-8')
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', str(len(data)))
                self.end_headers()
                self.wfile.write(data)

        self.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.server.daemon_threads = True
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    def start(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        data = protect({'port': self.server.server_port, 'token': self.token})
        temporary = self.path.with_suffix('.' + secrets.token_hex(4) + '.tmp')
        try:
            temporary.write_text(data, encoding='ascii')
            try:
                os.replace(temporary, self.path)
            except OSError as error:
                if error.errno != errno.EXDEV and getattr(error, 'winerror', None) != 17:
                    raise
                # Redirected Windows profile storage can reject even same-directory renames.
                # Publish before starting the listener; clients retry incomplete startup reads.
                with self.path.open('w', encoding='ascii') as stream:
                    stream.write(data)
                    stream.flush()
                    os.fsync(stream.fileno())
            self.thread.start()
        except Exception:
            self.server.server_close()
            raise
        finally:
            if temporary.exists():
                temporary.unlink()

    def dispatch(self, message):
        if not isinstance(message, dict) or self.closed:
            return {'ok': False, 'error': '下载器正在退出'}
        action = message.get('action')
        if action in ('ping', 'activate'):
            if action == 'activate':
                self.show_window()
            try:
                require_tools()
                capabilities = ['youtube']
            except ValueError:
                capabilities = []
            return {'ok': True, 'version': 2, 'capabilities': capabilities}
        if action != 'download':
            return {'ok': False, 'error': '不支持的操作'}
        request_id = message.get('request_id', '')
        if not isinstance(request_id, str) or not re.fullmatch(r'[a-zA-Z0-9_-]{16,100}', request_id):
            raise ValueError('Invalid request id')
        with self.lock:
            if self.closed:
                return {'ok': False, 'error': '下载器正在退出'}
            if request_id in self.receipts:
                return self.receipts[request_id]
            filename = video_filename(message)
            is_youtube = message.get('kind') == 'youtube'
            url = youtube_url(message['url']) if is_youtube else http_url(message['url'])
            context = None if is_youtube else normalize_context(url, {'headers': message.get('headers', {})})
            directory = os.path.abspath(self.manager.engine.config.download_dir)
            occupied = {os.path.normcase(os.path.abspath(task['save_path'])) for task in self.manager.get_all_tasks()}
            base, extension = os.path.splitext(filename)
            for index in range(10000):
                name = filename if index == 0 else f'{base} ({index}){extension}'
                target = os.path.join(directory, name)
                if os.path.normcase(target) not in occupied and not os.path.exists(target):
                    break
            else:
                raise ValueError('Too many duplicate filenames')
            if is_youtube:
                task_id = self.manager.add_youtube_task(url, name, directory, message.get('height', 720))
            else:
                task_id = self.manager.add_task(url, filename=name, save_path=directory, request_context=context)
            if not task_id:
                return {'ok': False, 'error': '无法下载此视频：链接已失效、需登录或文件大小未知'}
            result = {'ok': True, 'task_id': task_id, 'filename': name}
            self.receipts[request_id] = result
            if len(self.receipts) > 1024:
                self.receipts.pop(next(iter(self.receipts)))
            self.show_window()
            return result

    def close(self):
        self.closed = True
        if self.thread.is_alive():
            self.server.shutdown()
            self.thread.join(2)
        self.server.server_close()
        # Wait for an in-flight task submission before the manager shuts down.
        with self.lock:
            pass
        try:
            if unprotect(self.path.read_text(encoding='ascii'))['token'] == self.token:
                self.path.unlink()
        except (OSError, ValueError):
            pass
