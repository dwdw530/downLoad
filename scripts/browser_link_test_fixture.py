"""Disposable real-browser/EXE fixture with a unique native host; never registers the production host."""
import argparse
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import io
import json
from pathlib import Path
import secrets
import shutil
import sqlite3
import threading
from urllib.parse import urlsplit
import uuid
import winreg
import zipfile


ROOT = Path(__file__).resolve().parents[1]
HOST = 'com.laowang.downloader'
REGISTRY_ROOTS = [r'Software\Google\Chrome\NativeMessagingHosts',
                  r'Software\Chromium\NativeMessagingHosts',
                  r'Software\Microsoft\Edge\NativeMessagingHosts']


def registered_value(path):
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, path) as key:
            return winreg.QueryValueEx(key, '')[0]
    except FileNotFoundError:
        return None


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--app-dir', type=Path)
    parser.add_argument('--output-dir', type=Path, required=True)
    parser.add_argument('--stop', action='store_true')
    args = parser.parse_args()
    output = args.output_dir.resolve()
    if not output.is_relative_to((ROOT / 'output').resolve()):
        raise ValueError('Fixture output must remain in this project output directory')
    state_path = output / 'fixture.json'
    if args.stop:
        import urllib.request
        state = json.loads(state_path.read_text(encoding='utf-8'))
        request = urllib.request.Request(state['url'] + '/__control/stop', data=b'',
                                        headers={'X-Review-Token': state['control_token']}, method='POST')
        with urllib.request.urlopen(request, timeout=5) as response:
            print(response.read().decode(), flush=True)
        return
    app = args.app_dir.resolve()
    if not app.is_relative_to(output) or not (app / 'BrowserBridge.exe').is_file():
        raise ValueError('Use a separately built app inside the fixture output directory')
    if (app / 'data').exists():
        raise ValueError('Refusing to overwrite an existing app data directory')
    output.mkdir(parents=True, exist_ok=True)
    (app / 'data').mkdir()
    config = {'download_dir': str(output / 'files'), 'temp_dir': str(output / 'parts'),
              'thread_count': 4, 'max_concurrent_downloads': 2, 'retry_times': 1, 'timeout': 10,
              'close_behavior': 'exit', 'clipboard_monitor_enabled': False,
              'proxy': {'enabled': False, 'http': '', 'https': ''},
              'after_download': {'open_file': False, 'open_folder': False, 'shutdown': False}}
    (app / 'data/config.json').write_text(json.dumps(config), encoding='utf-8')
    extension = output / 'test-extension'
    shutil.copytree(app / 'chrome-extension', extension)
    host = HOST + '.review_' + uuid.uuid4().hex[:12]
    background_path = extension / 'background.js'
    background = background_path.read_text(encoding='utf-8')
    old = f'const HOST = "{HOST}";'
    if background.count(old) != 1:
        raise ValueError('Cannot isolate the native host without changing other extension behavior')
    background_path.write_text(background.replace(old, f'const HOST = "{host}";'), encoding='utf-8')
    import sys
    sys.path.insert(0, str(ROOT))
    from downloader.browser.registration import extension_id
    extension_manifest = json.loads((extension / 'manifest.json').read_text(encoding='utf-8'))
    manifest_path = output / 'test-native-host.json'
    manifest_path.write_text(json.dumps({'name': host, 'description': 'Isolated daw link review',
        'path': str(app / 'BrowserBridge.exe'), 'type': 'stdio',
        'allowed_origins': [f'chrome-extension://{extension_id(extension_manifest)}/']}, indent=2), encoding='utf-8')
    original = {base: registered_value(base + '\\' + HOST) for base in REGISTRY_ROOTS}
    registered = []
    stop = threading.Event()
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, 'w') as archive:
        archive.writestr(zipfile.ZipInfo('readme.txt', (2026, 10, 14, 0, 0, 0)),
                         b'Isolated right-click download acceptance sample.\n' * 2000)
    payload = buffer.getvalue()
    digest = hashlib.sha256(payload).hexdigest()
    control_token = secrets.token_urlsafe(24)
    observed = []
    html = b'''<!doctype html><meta charset="utf-8"><title>Codex Link Review</title>
<style>body{font:20px Arial;padding:35px}a{display:block;margin:25px 0}iframe{width:750px;height:160px}</style>
<h1>Codex Link Review - isolated fixtures</h1>
<a id="public" href="/public.zip">Public ZIP - normal browser download</a>
<a id="private" href="/private.zip?sig=a%2Bb+c&amp;part=1&amp;part=2">Private ZIP - right click to daw</a>
<a id="login" href="/login">HTML login response - reject</a>
<a id="stream" href="/master%2Em3u8">Encoded HLS - use video entry</a>
<iframe src="/frame"></iframe>'''

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def respond(self, body, mime, code=200, headers=None):
            self.send_response(code)
            self.send_header('Content-Type', mime)
            self.send_header('Content-Length', str(len(body)))
            for key, value in (headers or {}).items():
                self.send_header(key, value)
            self.end_headers()
            try:
                self.wfile.write(body)
            except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                pass

        def do_GET(self):
            path = urlsplit(self.path).path
            if path == '/__state':
                tasks = []
                database = app / 'data/downloads.db'
                if database.exists():
                    with sqlite3.connect(database) as connection:
                        connection.row_factory = sqlite3.Row
                        tasks = [dict(row) for row in connection.execute(
                            'SELECT task_id, filename, status, save_path FROM download_tasks ORDER BY created_at')]
                    for task in tasks:
                        file = Path(task.pop('save_path'))
                        task['sha256'] = hashlib.sha256(file.read_bytes()).hexdigest() if file.is_file() else None
                self.respond(json.dumps({'tasks': tasks, 'requests': observed, 'expected_sha256': digest}).encode(),
                             'application/json')
            elif path in ('/', '/frame', '/login'):
                body = html if path == '/' else b'<a id="frame-link" href="/iframe.zip">Iframe ZIP - right click to daw</a>' if path == '/frame' else b'<h1>Sign in</h1>'
                self.respond(body, 'text/html; charset=utf-8', headers={
                    'Set-Cookie': 'session=link-review; Path=/; SameSite=Lax'})
            elif path.endswith('.zip'):
                cookie_ok = self.headers.get('Cookie') == 'session=link-review'
                observed.append({'target': self.path, 'cookie_ok': cookie_ok,
                                 'referer': self.headers.get('Referer'), 'range': self.headers.get('Range')})
                if path == '/private.zip' and not cookie_ok:
                    self.respond(b'Forbidden', 'text/plain', 403)
                    return
                start, end = 0, len(payload) - 1
                value = self.headers.get('Range', '')
                if value.startswith('bytes='):
                    first, last = value[6:].split('-')
                    start, end = int(first), min(int(last) if last else end, end)
                headers = {'ETag': '"link-review-v1"', 'Accept-Ranges': 'bytes',
                           'Content-Disposition': 'attachment; filename="' + path[1:] + '"'}
                if value:
                    headers['Content-Range'] = f'bytes {start}-{end}/{len(payload)}'
                self.respond(payload[start:end+1], 'application/zip', 206 if value else 200, headers)
            else:
                self.respond(b'Not found', 'text/plain', 404)

        def do_POST(self):
            if self.path != '/__control/stop' or self.headers.get('X-Review-Token') != control_token:
                self.respond(b'Forbidden', 'text/plain', 403)
                return
            self.respond(b'{"stopping":true}', 'application/json')
            stop.set()

    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    serving = False
    try:
        for base in REGISTRY_ROOTS:
            key_path = base + '\\' + host
            try:
                with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path):
                    raise RuntimeError('Test host already exists; refusing to replace it')
            except FileNotFoundError:
                pass
            with winreg.CreateKey(winreg.HKEY_CURRENT_USER, key_path) as key:
                winreg.SetValueEx(key, '', 0, winreg.REG_SZ, str(manifest_path))
            registered.append(key_path)
        state = {'url': f'http://127.0.0.1:{server.server_port}', 'extension': str(extension),
                 'host': host, 'app_dir': str(app), 'output': str(output), 'control_token': control_token,
                 'expected_sha256': digest}
        state_path.write_text(json.dumps(state, indent=2), encoding='utf-8')
        print(json.dumps({key: value for key, value in state.items() if key != 'control_token'}), flush=True)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        serving = True
        stop.wait()
    finally:
        if serving:
            server.shutdown()
        server.server_close()
        for key_path in registered:
            if registered_value(key_path) == str(manifest_path):
                winreg.DeleteKey(winreg.HKEY_CURRENT_USER, key_path)
        preserved = all(registered_value(base + '\\' + HOST) == value for base, value in original.items())
        (output / 'cleanup.json').write_text(json.dumps({'production_registration_preserved': preserved,
            'test_host_removed': all(registered_value(key) is None for key in registered)}), encoding='utf-8')
        print('Fixture stopped; production registration preserved:', preserved, flush=True)


if __name__ == '__main__':
    main()
