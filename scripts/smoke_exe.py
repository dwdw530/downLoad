"""Windows packaged-app smoke test using a local server and isolated data.

Run from the project root: python -B scripts/smoke_exe.py
Artifacts are retained in the printed temporary directory for diagnosis.
The test clicks its isolated application window and restores the cursor afterward.
"""
import argparse
import ctypes
from ctypes import wintypes
import hashlib
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

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from downloader.core.download_engine import DownloadEngine
from downloader.database.db_manager import DatabaseManager
from downloader.utils.config import ConfigManager

PAYLOAD = bytes(range(251)) * 1024


class SmokeHandler(BaseHTTPRequestHandler):
    def log_message(self, *_args):
        pass

    def do_GET(self):
        header = self.headers.get('Range')
        start, end = 0, len(PAYLOAD) - 1
        if header and self.path == '/range':
            start, end = map(int, re.fullmatch(r'bytes=(\d+)-(\d+)', header).groups())
            self.send_response(206)
            self.send_header('Content-Range', f'bytes {start}-{end}/{len(PAYLOAD)}')
        else:
            self.send_response(200)
        body = PAYLOAD[start:end + 1]
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        try:
            self.wfile.write(body)
        except (ConnectionError, OSError):
            pass


user32 = ctypes.windll.user32
user32.SetProcessDPIAware()
kernel32 = ctypes.windll.kernel32
user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
user32.GetClientRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
user32.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
user32.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
user32.GetAncestor.argtypes = [wintypes.HWND, wintypes.UINT]
user32.GetAncestor.restype = wintypes.HWND
user32.SetForegroundWindow.argtypes = [wintypes.HWND]
user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
user32.GetForegroundWindow.restype = wintypes.HWND
user32.WindowFromPoint.argtypes = [wintypes.POINT]
user32.WindowFromPoint.restype = wintypes.HWND
user32.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int,
                                ctypes.c_int, ctypes.c_int, wintypes.UINT]
kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
kernel32.OpenProcess.restype = wintypes.HANDLE
kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
kernel32.QueryFullProcessImageNameW.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR,
                                              ctypes.POINTER(wintypes.DWORD)]
CALLBACK = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
user32.EnumWindows.argtypes = [CALLBACK, wintypes.LPARAM]
user32.EnumChildWindows.argtypes = [wintypes.HWND, CALLBACK, wintypes.LPARAM]


def describe(hwnd):
    text = ctypes.create_unicode_buffer(512)
    name = ctypes.create_unicode_buffer(128)
    rect = wintypes.RECT()
    user32.GetWindowTextW(hwnd, text, len(text))
    user32.GetClassNameW(hwnd, name, len(name))
    user32.GetWindowRect(hwnd, ctypes.byref(rect))
    return {'hwnd': hwnd, 'title': text.value, 'class': name.value,
            'rect': [rect.left, rect.top, rect.right, rect.bottom]}


def enum_children(hwnd):
    result = []
    callback = CALLBACK(lambda child, _: result.append(describe(child)) or True)
    user32.EnumChildWindows(hwnd, callback, 0)
    return result


def app_windows(exe):
    result = []
    def visit(hwnd, _):
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        handle = kernel32.OpenProcess(0x1000, False, pid.value)
        if handle:
            try:
                path = ctypes.create_unicode_buffer(32768)
                length = wintypes.DWORD(len(path))
                if kernel32.QueryFullProcessImageNameW(handle, 0, path, ctypes.byref(length)):
                    if Path(path.value) == exe:
                        result.append(describe(hwnd))
            finally:
                kernel32.CloseHandle(handle)
        return True
    user32.EnumWindows(CALLBACK(visit), 0)
    return result


def wait_for(predicate, timeout=20):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        result = predicate()
        if result:
            return result
        time.sleep(0.05)
    raise AssertionError('Timed out waiting for packaged application')


def click(hwnd):
    # Tk resolves pointer targets from the real cursor, so posted mouse messages
    # alone do not invoke its widgets. Interact only with this test process.
    root = user32.GetAncestor(hwnd, 2)
    user32.ShowWindow(root, 4)
    user32.SetWindowPos(root, wintypes.HWND(-1), 0, 0, 0, 0, 0x0013)
    user32.SetForegroundWindow(root)
    time.sleep(0.1)
    left, top, right, bottom = describe(hwnd)['rect']
    point = wintypes.POINT((left + right) // 2, (top + bottom) // 2)
    target = user32.WindowFromPoint(point)
    assert user32.GetAncestor(target, 2) == root, f'Test control is not exposed: {describe(target)}'
    assert user32.SetCursorPos(point.x, point.y)
    user32.mouse_event(0x0002, 0, 0, 0, 0)
    user32.mouse_event(0x0004, 0, 0, 0, 0)


def capture(hwnd, path):
    from PIL import Image
    gdi = ctypes.windll.gdi32
    user32.GetWindowDC.argtypes = [wintypes.HWND]
    user32.GetWindowDC.restype = wintypes.HDC
    user32.ReleaseDC.argtypes = [wintypes.HWND, wintypes.HDC]
    user32.PrintWindow.argtypes = [wintypes.HWND, wintypes.HDC, wintypes.UINT]
    gdi.CreateCompatibleDC.argtypes = [wintypes.HDC]
    gdi.CreateCompatibleDC.restype = wintypes.HDC
    gdi.CreateCompatibleBitmap.argtypes = [wintypes.HDC, ctypes.c_int, ctypes.c_int]
    gdi.CreateCompatibleBitmap.restype = wintypes.HBITMAP
    gdi.SelectObject.argtypes = [wintypes.HDC, wintypes.HGDIOBJ]
    gdi.SelectObject.restype = wintypes.HGDIOBJ
    gdi.DeleteObject.argtypes = [wintypes.HGDIOBJ]
    gdi.DeleteDC.argtypes = [wintypes.HDC]
    class Header(ctypes.Structure):
        _fields_ = [('size', wintypes.DWORD), ('width', wintypes.LONG), ('height', wintypes.LONG),
                    ('planes', wintypes.WORD), ('bits', wintypes.WORD), ('compression', wintypes.DWORD),
                    ('image_size', wintypes.DWORD), ('x', wintypes.LONG), ('y', wintypes.LONG),
                    ('used', wintypes.DWORD), ('important', wintypes.DWORD)]
    gdi.GetDIBits.argtypes = [wintypes.HDC, wintypes.HBITMAP, wintypes.UINT, wintypes.UINT,
                            ctypes.c_void_p, ctypes.POINTER(Header), wintypes.UINT]
    rect = describe(hwnd)['rect']
    width, height = rect[2] - rect[0], rect[3] - rect[1]
    dc = user32.GetWindowDC(hwnd)
    memory_dc = gdi.CreateCompatibleDC(dc)
    bitmap = gdi.CreateCompatibleBitmap(dc, width, height)
    old = gdi.SelectObject(memory_dc, bitmap)
    try:
        if not user32.PrintWindow(hwnd, memory_dc, 2):
            raise AssertionError('Could not capture test window')
        info = Header(ctypes.sizeof(Header), width, -height, 1, 32, 0, 0, 0, 0, 0, 0)
        pixels = ctypes.create_string_buffer(width * height * 4)
        gdi.GetDIBits(memory_dc, bitmap, 0, height, pixels, ctypes.byref(info), 0)
        Image.frombuffer('RGB', (width, height), pixels, 'raw', 'BGRX', 0, 1).save(path)
    finally:
        gdi.SelectObject(memory_dc, old)
        gdi.DeleteObject(bitmap)
        gdi.DeleteDC(memory_dc)
        user32.ReleaseDC(hwnd, dc)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--exe', type=Path, default=ROOT / 'dist' / 'daw下载器.exe')
    parser.add_argument('--inspect', action='store_true')
    args = parser.parse_args()
    original_foreground = user32.GetForegroundWindow()
    original_pointer = wintypes.POINT()
    user32.GetCursorPos(ctypes.byref(original_pointer))
    workspace = Path(tempfile.mkdtemp(prefix='downloader-exe-smoke-'))
    app_dir = workspace / 'app'
    app_dir.mkdir()
    launch_dir = workspace / 'unrelated-working-directory'
    launch_dir.mkdir()
    exe = app_dir / args.exe.name
    shutil.copy2(args.exe, exe)
    server = ThreadingHTTPServer(('127.0.0.1', 0), SmokeHandler)
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()
    config = ConfigManager(str(app_dir / 'data' / 'config.json'))
    config.download_dir = str(workspace / 'downloads')
    config.set('temp_dir', str(workspace / 'parts'))
    config.set('timeout', 3)
    config.set('retry_times', 1)
    config.set('close_behavior', 'exit')
    config.thread_count = 4
    config.max_concurrent_downloads = 1
    config.speed_limit = 128 * 1024
    config.save()
    db = DatabaseManager(str(app_dir / 'data' / 'downloads.db'))
    engine = DownloadEngine(db, config)
    task_ids = []
    for endpoint in ('range', 'no-range'):
        task_id = engine.create_download_task(f'http://127.0.0.1:{server.server_port}/{endpoint}',
                                             filename=endpoint + '.bin',
                                             expected_hash=hashlib.sha256(PAYLOAD).hexdigest(), hash_type='sha256')
        assert task_id
        db.update_task_status(task_id, 'paused')
        task_ids.append(task_id)
    startup = subprocess.STARTUPINFO()
    startup.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    startup.wShowWindow = 0
    print(f'Artifacts: {workspace}', flush=True)
    process = None
    try:
        with (workspace / 'stdout.log').open('w', encoding='utf-8') as out, \
                (workspace / 'stderr.log').open('w', encoding='utf-8') as err:
            def launch():
                nonlocal process
                process = subprocess.Popen([str(exe)], cwd=launch_dir, startupinfo=startup, stdout=out, stderr=err)
                window = wait_for(lambda: next((w for w in app_windows(exe) if w['title'] == 'daw下载器 v1.0'), None))
                time.sleep(0.8)
                user32.SendMessageW.restype = ctypes.c_ssize_t
                user32.GetClassLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int]
                user32.GetClassLongPtrW.restype = ctypes.c_size_t
                # Tk default icons are class icons, not necessarily WM_GETICON properties.
                assert (user32.SendMessageW(window['hwnd'], 0x007F, 1, 0)
                        or user32.GetClassLongPtrW(window['hwnd'], -14)), 'Window large icon is missing'
                assert (user32.SendMessageW(window['hwnd'], 0x007F, 0, 0)
                        or user32.GetClassLongPtrW(window['hwnd'], -34)), 'Window small icon is missing'
                return window
            window = launch()
            children = enum_children(window['hwnd'])
            (workspace / 'windows.json').write_text(json.dumps([window] + children, ensure_ascii=False, indent=2), encoding='utf-8')
            capture(window['hwnd'], workspace / 'main-window.png')
            if args.inspect:
                print(f'Window: {window}; child controls: {len(children)}', flush=True)
            else:
                def restart(window):
                    user32.PostMessageW(window['hwnd'], 0x0010, 0, 0)
                    assert process.wait(20) == 0
                    return launch()
                window = run_download_checks(exe, window, db, task_ids, workspace, restart)
            user32.PostMessageW(window['hwnd'], 0x0010, 0, 0)
            assert process.wait(20) == 0
        assert not (launch_dir / 'data' / 'downloads.db').exists(), 'Database incorrectly follows working directory'
        assert not (workspace / 'stderr.log').read_text(encoding='utf-8').strip()
        print('PASS: packaged application startup, isolated data location and clean exit', flush=True)
    except Exception:
        print('Test process windows:', app_windows(exe), flush=True)
        print('Task state:', [(t['filename'], t['status'], t['downloaded_size']) for t in db.get_all_tasks()], flush=True)
        for window in app_windows(exe):
            if window['title'] == 'daw下载器 v1.0':
                capture(window['hwnd'], workspace / 'failure.png')
        raise
    finally:
        if process and process.poll() is None:
            for window in app_windows(exe):
                if window['title'] == 'daw下载器 v1.0':
                    user32.PostMessageW(window['hwnd'], 0x0010, 0, 0)
            process.wait(20)
        server.shutdown()
        server.server_close()
        server_thread.join(2)
        user32.SetCursorPos(original_pointer.x, original_pointer.y)
        user32.SetForegroundWindow(original_foreground)


def click_toolbar(hwnd, index):
    # Identify the five fixed-width toolbar buttons from native rectangles.
    rect = wintypes.RECT()
    user32.GetClientRect(hwnd, ctypes.byref(rect))
    scale = rect.right / 900
    children = enum_children(hwnd)
    buttons = [c for c in children if c['class'] == 'TkChild'
               and abs(c['rect'][2] - c['rect'][0] - 100 * scale) < 2
               and abs(c['rect'][3] - c['rect'][1] - 28 * scale) < 2]
    top = min(c['rect'][1] for c in buttons)
    rectangles = sorted(set(tuple(c['rect']) for c in buttons if c['rect'][1] == top))
    assert len(rectangles) == 5, f'Unexpected toolbar layout: {rectangles}'
    left, top, right, bottom = rectangles[index]
    label = next(c for c in children if c['class'] == 'Static'
                 and left <= c['rect'][0] and c['rect'][2] <= right
                 and top <= c['rect'][1] and c['rect'][3] <= bottom)
    click(label['hwnd'])


def dismiss_info(exe):
    dialog = wait_for(lambda: next((w for w in app_windows(exe) if w['title'] == '提示'), None), timeout=10)
    button = next(c for c in enum_children(dialog['hwnd']) if c['class'] == 'Button')
    user32.PostMessageW(button['hwnd'], 0x00F5, 0, 0)  # BM_CLICK uses the dialog's real button ID.
    wait_for(lambda: all(w['hwnd'] != dialog['hwnd'] for w in app_windows(exe)))


def run_download_checks(exe, window, db, task_ids, workspace, restart):
    first, second = task_ids
    click_toolbar(window['hwnd'], 2)
    dismiss_info(exe)
    wait_for(lambda: db.get_task(first)['status'] == 'downloading' and db.get_task(first)['downloaded_size'] > 0)
    click_toolbar(window['hwnd'], 1)
    dismiss_info(exe)
    wait_for(lambda: all(db.get_task(task_id)['status'] == 'paused' for task_id in task_ids))
    time.sleep(1.1)  # Allow the one-second status bar refresh before the screenshot.
    before_restart = db.get_task(first)['downloaded_size']
    assert 0 < before_restart < len(PAYLOAD)
    capture(window['hwnd'], workspace / 'paused.png')
    window = restart(window)
    assert db.get_task(first)['status'] == 'paused'
    assert db.get_task(first)['downloaded_size'] >= before_restart
    assert db.get_task(second)['status'] == 'paused'
    click_toolbar(window['hwnd'], 2)
    dismiss_info(exe)
    wait_for(lambda: all(db.get_task(task_id)['status'] == 'completed' for task_id in task_ids), timeout=25)
    for task_id in task_ids:
        task = db.get_task(task_id)
        assert Path(task['save_path']).read_bytes() == PAYLOAD
        assert task['downloaded_size'] == len(PAYLOAD)
        assert task['hash_verified'] == 1
    assert len(db.get_history()) == 2
    assert all(0 <= h['download_time'] < 30 for h in db.get_history())
    time.sleep(0.3)
    capture(window['hwnd'], workspace / 'completed.png')
    (workspace / 'result.json').write_text(json.dumps({'tasks': db.get_all_tasks(), 'history': db.get_history()},
                                                     ensure_ascii=False, indent=2), encoding='utf-8')
    print('PASS: native buttons, pause/resume, restart recovery, queue, Range/non-Range files, SHA256 and history', flush=True)
    return window


if __name__ == '__main__':
    main()
