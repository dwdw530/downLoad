"""Download a user-selected public YouTube video through the actual task/bridge APIs."""
import argparse
import io
import json
from pathlib import Path
import shutil
import subprocess
import sys
import time
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from downloader.browser.bridge import BridgeServer
from downloader.core.download_engine import DownloadEngine
from downloader.core.task_manager import TaskManager
from downloader.database.db_manager import DatabaseManager
from downloader.utils.config import ConfigManager
from downloader.core.youtube_downloader import x_url, bilibili_url


def page_kind(url):
    for kind, normalize in (('x', x_url), ('bilibili', bilibili_url)):
        try:
            normalize(url)
            return kind
        except ValueError:
            pass
    return 'youtube'


def page_filename(url):
    from downloader.browser.bridge import video_filename
    return video_filename({'kind': page_kind(url), 'url': url})


def packaged(url, release, height=720):
    from downloader.browser.native_host import read_message, write_message
    from smoke_exe import app_windows, wait_for, user32, capture

    kind = page_kind(url)
    directory = ROOT / 'output' / (kind + '-exe-' + uuid.uuid4().hex[:8])
    app = directory / 'app'
    app.mkdir(parents=True)
    for name in ('BrowserBridge.exe', 'daw\u4e0b\u8f7d\u5668.exe'):
        shutil.copy2(release / name, app / name)
    shutil.copytree(release / 'video-tools', app / 'video-tools')
    config = ConfigManager(str(app / 'data/config.json'))
    config.download_dir = str(directory / 'downloads')
    config.set('temp_dir', str(directory / 'parts'))
    config.set('close_behavior', 'exit')
    config.set('timeout', 30)
    config.save()
    exe = app / 'daw\u4e0b\u8f7d\u5668.exe'
    foreground = user32.GetForegroundWindow()

    def native(message):
        stream = io.BytesIO()
        write_message(stream, message)
        result = subprocess.run([str(app / 'BrowserBridge.exe'),
            'chrome-extension://kpknpdpemebgalcfiolkcpaafddelcjj/'], input=stream.getvalue(),
            capture_output=True, timeout=100, creationflags=subprocess.CREATE_NO_WINDOW, cwd=app)
        assert result.returncode == 0 and not result.stderr, result.stderr
        return read_message(io.BytesIO(result.stdout))

    try:
        ping = native({'action': 'ping'})
        assert ping.get('installed') and kind in ping.get('capabilities', []), ping
        message = {'action': 'download', 'kind': kind, 'url': url, 'height': height,
                   'filename': page_filename(url), 'request_id': str(uuid.uuid4())}
        result = native(message)
        assert result['ok'], result
        assert native(message) == result
        assert kind in native({'action': 'ping'})['capabilities']
        db = DatabaseManager(str(app / 'data/downloads.db'))
        task = wait_for(lambda: (t if (t := db.get_task(result['task_id'])) and
            t['status'] in ('completed', 'failed', 'verify_failed') else None), timeout=900)
        print(json.dumps(task, ensure_ascii=True), flush=True)
        assert task['status'] == 'completed', task['error_message']
        assert len(db.get_all_tasks()) == 1
        video = Path(task['save_path'])
        probe = subprocess.run([str(app / 'video-tools/ffprobe.exe'), '-v', 'error',
            '-show_entries', 'stream=codec_name,codec_type,width,height:format=duration,size', '-of', 'json',
            str(video)], capture_output=True, text=True, timeout=30, creationflags=subprocess.CREATE_NO_WINDOW)
        assert probe.returncode == 0, probe.stderr
        metadata = json.loads(probe.stdout)
        assert {'video', 'audio'} <= {s['codec_type'] for s in metadata['streams']}
        assert all(s.get('height', 0) <= height for s in metadata['streams'])
        assert float(metadata['format']['duration']) > 0
        print(probe.stdout, flush=True)
        decoded = subprocess.run([str(app / 'video-tools/ffmpeg.exe'), '-v', 'error', '-xerror',
            '-i', str(video), '-f', 'null', '-'], capture_output=True, timeout=300,
            creationflags=subprocess.CREATE_NO_WINDOW)
        assert decoded.returncode == 0 and not decoded.stderr, decoded.stderr
        window = wait_for(lambda: next((w for w in app_windows(exe) if w['title'] == 'daw\u4e0b\u8f7d\u5668 v1.0'), None))
        capture(window['hwnd'], directory / (kind + '-completed.png'))
        print('PASS: packaged native cold start, video download, dedupe, audio/video and full decode', flush=True)
        print('Artifacts:', directory, flush=True)
    finally:
        for window in app_windows(exe):
            if window['title'] == 'daw\u4e0b\u8f7d\u5668 v1.0':
                user32.PostMessageW(window['hwnd'], 0x0010, 0, 0)
        wait_for(lambda: not app_windows(exe), timeout=30)
        if foreground:
            user32.SetForegroundWindow(foreground)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('url')
    parser.add_argument('--exe', action='store_true', help='Validate the packaged release in an isolated directory')
    parser.add_argument('--release', type=Path, default=ROOT / 'dist')
    parser.add_argument('--height', type=int, choices=(480, 720, 1080), default=720)
    args = parser.parse_args()
    if args.exe:
        return packaged(args.url, args.release.resolve(), args.height)
    directory = ROOT / 'output' / (page_kind(args.url) + '-validation-' + uuid.uuid4().hex[:8])
    directory.mkdir(parents=True, exist_ok=True)
    config = ConfigManager(str(directory / 'config.json'))
    config.download_dir = str(directory / 'downloads')
    config.set('temp_dir', str(directory / 'parts'))
    config.set('timeout', 30)
    config.save()
    db = DatabaseManager(str(directory / 'tasks.db'))
    engine = DownloadEngine(db, config)
    manager = TaskManager(engine, db)
    bridge = BridgeServer(manager, lambda: None, directory / 'endpoint')
    last = 0

    def progress(task_id, done, size, speed):
        nonlocal last
        if time.monotonic() - last > 5:
            print(f'{done / 1048576:.1f}/{size / 1048576:.1f} MiB, {speed / 1048576:.1f} MiB/s', flush=True)
            last = time.monotonic()

    engine.set_progress_callback(progress)
    try:
        result = bridge.dispatch({'action':'download', 'kind':page_kind(args.url), 'url':args.url,
            'filename':page_filename(args.url), 'height':args.height, 'request_id':str(uuid.uuid4())})
        assert result['ok'], result
        print(json.dumps(result), flush=True)
        deadline = time.monotonic() + 900
        while time.monotonic() < deadline:
            task = db.get_task(result['task_id'])
            if task['status'] in ('completed', 'failed', 'verify_failed'):
                print(json.dumps(task, ensure_ascii=True), flush=True)
                assert task['status'] == 'completed', task['error_message']
                return
            time.sleep(.25)
        raise TimeoutError('YouTube validation timed out')
    finally:
        bridge.close()
        manager.shutdown()


if __name__ == '__main__':
    main()
