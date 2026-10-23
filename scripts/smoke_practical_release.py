"""Exercise the four practical improvements in the actual packaged EXE.

Only disposable data and local HTTP are used. Mouse/foreground are restored;
the original dist data and browser registration are never used or modified.
"""
import argparse
import ctypes
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from downloader.browser.security import normalize_context
from downloader.core.download_engine import DownloadEngine
from downloader.database.db_manager import DatabaseManager
from downloader.utils.config import ConfigManager
from smoke_exe import app_windows, capture, click_toolbar, user32, wait_for
from ctypes import wintypes

user32.ClientToScreen.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.POINT)]
user32.ClientToScreen.restype = wintypes.BOOL

PAYLOAD = bytes(range(256)) * 256
PART_PAYLOAD = PAYLOAD[:2048]


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass

    def do_GET(self):
        path = urlsplit(self.path).path
        header = self.headers.get('Range')
        probe = header == 'bytes=0-0'
        match = re.fullmatch(r'bytes=(\d+)-(\d+)', header or '')
        start, end = (int(match[1]), int(match[2])) if match else (0, len(PAYLOAD) - 1)
        payload = PART_PAYLOAD if path == '/fallback.bin' else PAYLOAD
        if path == '/empty.bin':
            payload = b''
        if not probe:
            self.server.formal.append({'path': path, 'range': header, 'at': time.monotonic()})
        if path == '/retry.bin' and not probe:
            if self.headers.get('Cookie') != 'session=release-fixture':
                return self.status(403)
            self.server.retry_requests += 1
            if self.server.retry_requests == 1:
                return self.status(503, '2')
        if path == '/fatal.bin' and not probe:
            return self.status(403)
        if path == '/fallback.bin' and not probe:
            if start == 1536:
                return self.status(503, '3600')
            if start == 512:
                return self.body(payload, 200)
        if path == '/unknown.bin':
            self.send_response(200)
            self.send_header('ETag', '"release-stable"')
            self.end_headers()
            try:
                if probe:
                    self.wfile.write(payload[:64])
                else:
                    self.wfile.write(payload[:8192])
                    self.wfile.flush()
                    self.server.release_unknown.wait(20)
                    self.wfile.write(payload[8192:])
            except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                pass
            return
        if payload and match:
            return self.body(payload[start:end + 1], 206, (start, end, len(payload)))
        self.body(payload, 200)

    def status(self, code, retry_after=None):
        self.send_response(code)
        self.send_header('Content-Length', '0')
        if retry_after is not None:
            self.send_header('Retry-After', retry_after)
        self.end_headers()

    def body(self, payload, status, content_range=None):
        self.send_response(status)
        self.send_header('ETag', '"release-stable"')
        self.send_header('Content-Type', 'application/octet-stream')
        self.send_header('Content-Length', str(len(payload)))
        if content_range:
            self.send_header('Content-Range', 'bytes %d-%d/%d' % content_range)
        self.end_headers()
        try:
            self.wfile.write(payload)
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            pass


def click_client(window, x, y, double=False):
    hwnd = window['hwnd']
    user32.ShowWindow(hwnd, 9)
    user32.SetWindowPos(hwnd, wintypes.HWND(-1), 0, 0, 0, 0, 0x0013)
    user32.SetForegroundWindow(hwnd)
    client = wintypes.RECT()
    user32.GetClientRect(hwnd, ctypes.byref(client))
    point = wintypes.POINT(round(x * client.right / 900), round(y * client.right / 900))
    assert user32.ClientToScreen(hwnd, ctypes.byref(point))
    user32.SetCursorPos(point.x, point.y)
    time.sleep(0.1)
    target = user32.WindowFromPoint(point)
    assert user32.GetAncestor(target, 2) == hwnd, 'Click is outside the isolated application'
    for _ in range(2 if double else 1):
        user32.mouse_event(0x0002, 0, 0, 0, 0)
        user32.mouse_event(0x0004, 0, 0, 0, 0)
        time.sleep(0.08)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--exe', type=Path, default=ROOT / 'dist' / 'daw下载器.exe')
    args = parser.parse_args()
    workspace = Path(tempfile.mkdtemp(prefix='daw-practical-exe-'))
    app_dir = workspace / 'app'
    app_dir.mkdir()
    exe = app_dir / args.exe.name
    shutil.copy2(args.exe, exe)
    config = ConfigManager(str(app_dir / 'data' / 'config.json'))
    config.download_dir = str(workspace / 'downloads')
    config.temp_dir = str(workspace / 'parts')
    config.thread_count = 1
    config.max_concurrent_downloads = 1
    config.set('retry_times', 2)
    config.set('timeout', 8)
    config.set('close_behavior', 'exit')
    config.set('clipboard_monitor_enabled', False)
    assert config.save()
    db = DatabaseManager(str(app_dir / 'data' / 'downloads.db'))
    engine = DownloadEngine(db, config)
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    server.formal = []
    server.retry_requests = 0
    server.release_unknown = threading.Event()
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f'http://127.0.0.1:{server.server_port}'
    saved = engine.create_download_task(base + '/saved.bin', filename='saved-error.bin')
    assert saved
    db.update_task_status(saved, 'failed',
                          'HTTP 403 Cookie: fixture-secret https://private.invalid/?token=fixture')
    tasks = {}
    for name in ('unknown', 'empty', 'retry', 'fatal'):
        context = None
        if name == 'retry':
            context = normalize_context(base + '/retry.bin?sig=fixture',
                                        {'media': False, 'headers': {'Cookie': 'session=release-fixture'}})
        task_id = engine.create_download_task(
            base + '/' + name + '.bin' + ('?sig=fixture' if context else ''),
            filename=name + '.bin', request_context=context)
        assert task_id
        db.update_task_status(task_id, 'paused')
        tasks[name] = task_id
    config.thread_count = 2
    fallback = engine.create_download_task(base + '/fallback.bin', filename='fallback.bin')
    assert fallback
    db.update_task_status(fallback, 'paused')
    chunks = db.get_chunks(fallback)
    assert len(chunks) == 2
    for chunk in chunks:
        path = Path(chunk['temp_file'])
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b'x' * 512)
        db.update_chunk_progress(chunk['chunk_id'], 512)
    db.update_task_progress(fallback, 1024, 0)
    # Task-specific thread counts are stored; keep the default simple for this run.
    config.thread_count = 1
    assert config.save()
    process = None
    foreground = user32.GetForegroundWindow()
    pointer = wintypes.POINT()
    user32.GetCursorPos(ctypes.byref(pointer))
    print('Artifacts:', workspace, flush=True)
    try:
        process = subprocess.Popen([str(exe)], cwd=workspace,
                                   creationflags=subprocess.CREATE_NO_WINDOW)
        window = wait_for(lambda: next((w for w in app_windows(exe)
                                       if w['title'] == 'daw下载器 v1.0'), None), timeout=30)
        user32.ShowWindow(window['hwnd'], 9)
        time.sleep(0.8)
        capture(window['hwnd'], workspace / 'saved-safe-reason.png')
        click_client(window, 450, 234, double=True)  # First card's observed progress track.
        detail = wait_for(lambda: next((w for w in app_windows(exe)
                                       if w['title'].startswith('任务详情')), None))
        time.sleep(0.3)
        capture(detail['hwnd'], workspace / 'saved-safe-detail.png')
        user32.PostMessageW(detail['hwnd'], 0x0010, 0, 0)
        click_client(window, 80, 193)  # Paused label observed in the packaged window.
        time.sleep(0.3)
        assert ConfigManager(config.config_path).ui_active_category == 'status:paused'
        capture(window['hwnd'], workspace / 'paused-category.png')
        click_toolbar(window['hwnd'], 2)
        wait_for(lambda: db.get_task(tasks['unknown'])['downloaded_size'] > 0)
        unknown = db.get_task(tasks['unknown'])
        assert unknown['status'] == 'downloading' and unknown['total_size'] == 0
        click_client(window, 80, 143)  # Downloading category.
        time.sleep(0.3)
        capture(window['hwnd'], workspace / 'unknown-progress.png')
        server.release_unknown.set()
        wait_for(lambda: all(db.get_task(tasks[name])['status'] == 'completed'
                             for name in ('unknown', 'empty', 'retry')), timeout=30)
        wait_for(lambda: db.get_task(tasks['fatal'])['status'] == 'failed', timeout=30)
        wait_for(lambda: db.get_task(fallback)['status'] == 'failed', timeout=30)
        for name, expected in (('unknown', PAYLOAD), ('empty', b''), ('retry', PAYLOAD)):
            task = db.get_task(tasks[name])
            assert Path(task['save_path']).read_bytes() == expected, name
            assert task['total_size'] == task['downloaded_size'] == len(expected), name
        retry = [r for r in server.formal if r['path'] == '/retry.bin']
        assert len(retry) == 2
        assert retry[1]['at'] - retry[0]['at'] >= 1.9
        assert len([r for r in server.formal if r['path'] == '/fatal.bin']) == 1
        assert not Path(db.get_task(tasks['fatal'])['save_path']).exists()
        assert 'HTTP 403' in db.get_task(tasks['fatal'])['error_message']
        assert db.get_task(fallback)['downloaded_size'] == 1024
        assert not Path(db.get_task(fallback)['save_path']).exists()
        assert all(Path(chunk['temp_file']).read_bytes() == b'x' * 512 for chunk in chunks)
        assert not any(r['path'] == '/fallback.bin' and r['range'] is None for r in server.formal)
        click_client(window, 80, 218)  # Completed category, including the empty file.
        time.sleep(0.5)
        capture(window['hwnd'], workspace / 'completed-empty-progress.png')
        user32.PostMessageW(window['hwnd'], 0x0010, 0, 0)
        assert process.wait(30) == 0
        report = {'exe': str(args.exe.resolve()), 'retry_interval': retry[1]['at'] - retry[0]['at'],
                  'tasks': db.get_all_tasks(), 'formal_requests': server.formal,
                  'checks': ['saved-safe-reason-and-detail', 'paused-category-persistence',
                             'unknown-and-empty-real-download', 'browser-503-wait-and-exact-bytes',
                             'fatal-single-request', 'fallback-refusal-keeps-1024-bytes', 'clean-exit']}
        (workspace / 'result.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
        print('PASS: packaged practical features, Retry-After, fatal refusal and preserved chunks', flush=True)
    finally:
        server.release_unknown.set()
        if process and process.poll() is None:
            for item in app_windows(exe):
                if item['title'] == 'daw下载器 v1.0':
                    user32.PostMessageW(item['hwnd'], 0x0010, 0, 0)
            process.wait(30)
        engine.shutdown()
        server.shutdown()
        server.server_close()
        thread.join(2)
        user32.SetCursorPos(pointer.x, pointer.y)
        if foreground:
            user32.SetForegroundWindow(foreground)


if __name__ == '__main__':
    main()
