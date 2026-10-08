"""Exercise the packaged native host and cold-start GUI without registry changes."""
import argparse
import hashlib
import io
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from downloader.browser.native_host import read_message, write_message
from downloader.database.db_manager import DatabaseManager
from downloader.utils.config import ConfigManager
from smoke_exe import app_windows, wait_for, user32, capture


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--app-dir', type=Path, help='Test a disposable installed directory directly')
    args = parser.parse_args()
    if args.app_dir and (args.app_dir.resolve() / 'data').exists():
        parser.error('--app-dir requires a disposable installation without an existing data directory')
    workspace = Path(tempfile.mkdtemp(prefix='browser-bridge-exe-'))
    app_dir = args.app_dir.resolve() if args.app_dir else workspace / 'app'
    app_dir.mkdir(exist_ok=True)
    exe = app_dir / 'daw下载器.exe'
    host = app_dir / 'BrowserBridge.exe'
    if not args.app_dir:
        for binary in (exe, host):
            shutil.copy2(ROOT / 'dist' / binary.name, binary)
    config = ConfigManager(str(app_dir / 'data/config.json'))
    config.download_dir = str(workspace / 'downloads')
    config.set('temp_dir', str(workspace / 'parts'))
    config.set('close_behavior', 'exit')
    config.set('timeout', 3)
    config.save()
    video = (ROOT / 'output/playwright/sample.webm').read_bytes()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def do_GET(self):
            if self.headers.get('Cookie') != 'session=test-secret':
                self.send_error(403)
                return
            start, end = 0, len(video) - 1
            if self.headers.get('Range'):
                start, end = map(int, self.headers['Range'][6:].split('-'))
            self.send_response(206)
            self.send_header('Content-Type', 'video/webm')
            self.send_header('Content-Range', f'bytes {start}-{end}/{len(video)}')
            self.send_header('Content-Length', str(end - start + 1))
            self.end_headers()
            self.wfile.write(video[start:end + 1])

    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    foreground = user32.GetForegroundWindow()

    def native(message):
        stream = io.BytesIO()
        write_message(stream, message)
        result = subprocess.run([str(host), 'chrome-extension://kpknpdpemebgalcfiolkcpaafddelcjj/'],
                                input=stream.getvalue(), capture_output=True, timeout=100,
                                creationflags=subprocess.CREATE_NO_WINDOW, cwd=workspace)
        assert result.returncode == 0, result.stderr
        assert not result.stderr, result.stderr
        return read_message(io.BytesIO(result.stdout))

    try:
        response = native({'action':'ping'})
        assert not response['ok'] and response['installed']
        message = {'action':'download', 'request_id':str(uuid.uuid4()), 'kind':'webm',
                   'url':f'http://127.0.0.1:{server.server_port}/sample.webm', 'filename':'sample.webm',
                   'headers':{'Cookie':'session=test-secret'}}
        response = native(message)
        assert response['ok'], response
        db = DatabaseManager(str(app_dir / 'data/downloads.db'))
        wait_for(lambda: db.get_task(response['task_id'])['status'] == 'completed')
        task = db.get_task(response['task_id'])
        assert Path(task['save_path']).read_bytes() == video
        assert 'test-secret' not in task['browser_context']
        assert native(message) == response
        assert len(db.get_all_tasks()) == 1
        window = wait_for(lambda: next((w for w in app_windows(exe) if w['title']=='daw下载器 v1.0'),None))
        capture(window['hwnd'], workspace / 'video-completed.png')
        second = subprocess.run([str(exe)], capture_output=True, timeout=20,
                                creationflags=subprocess.CREATE_NO_WINDOW)
        assert second.returncode == 0
        assert len([w for w in app_windows(exe) if w['title']=='daw下载器 v1.0']) == 1
        print('PASS: packaged native framing, cold GUI start, authenticated video, byte equality, dedupe and single instance')
        print('Video SHA256:', hashlib.sha256(video).hexdigest())
        print('Artifacts:', workspace)
    finally:
        for window in app_windows(exe):
            if window['title'] == 'daw下载器 v1.0':
                user32.PostMessageW(window['hwnd'], 0x0010, 0, 0)
        wait_for(lambda: not app_windows(exe), timeout=30)
        server.shutdown()
        server.server_close()
        if foreground:
            user32.SetForegroundWindow(foreground)


if __name__ == '__main__':
    main()
