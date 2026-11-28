"""Real ordinary-file restart on Darwin, with only disposable local HTTP and files."""
import hashlib
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import threading
import time
import unittest

DATA = bytes(range(256)) * 8192


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_HEAD(self):
        self.send_file(False)

    def do_GET(self):
        self.send_file(True)

    def send_file(self, body):
        value = self.headers.get('Range', '')
        match = re.fullmatch(r'bytes=(\d+)-(\d*)', value)
        start, end = (int(match[1]), int(match[2] or len(DATA)-1)) if match else (0, len(DATA)-1)
        self.send_response(206 if match else 200)
        self.send_header('Content-Type', 'application/octet-stream')
        self.send_header('Accept-Ranges', 'bytes')
        self.send_header('ETag', '"local-stable"')
        self.send_header('Content-Length', str(end - start + 1))
        if match:
            self.send_header('Content-Range', f'bytes {start}-{end}/{len(DATA)}')
        self.end_headers()
        if body:
            self.server.starts.append(start)
            try:
                for pos in range(start, end + 1, 16384):
                    self.wfile.write(DATA[pos:min(end+1, pos+16384)])
                    time.sleep(.006)
            except (ConnectionError, OSError):
                pass


def stage(url, restarting):
    from downloader.core.download_engine import DownloadEngine
    from downloader.core.task_manager import TaskManager
    from downloader.database.db_manager import DatabaseManager
    from downloader.utils.config import ConfigManager
    config, db = ConfigManager(), DatabaseManager()
    config.thread_count = 2
    manager = TaskManager(DownloadEngine(db, config), db)
    try:
        if restarting:
            task, = db.get_all_tasks()
            assert task['status'] == 'paused' and 0 < task['downloaded_size'] < len(DATA), task['status']
            tid = task['task_id']
            assert manager.resume_task(tid)
        else:
            tid = manager.add_task(url)
            assert tid
        deadline = time.monotonic() + 12
        while time.monotonic() < deadline:
            task = db.get_task(tid)
            if restarting and task['status'] == 'completed':
                output = Path(task['save_path'])
                assert hashlib.sha256(output.read_bytes()).digest() == hashlib.sha256(DATA).digest()
                return
            if not restarting:
                partial = sum(p.stat().st_size for p in Path(config.temp_dir).glob('*.part*'))
                if 0 < partial < len(DATA):
                    return  # same shutdown path used when the UI quits
            assert task['status'] not in ('failed', 'verify_failed'), task['error_message']
            time.sleep(.01)
        raise TimeoutError(task['status'])
    finally:
        manager.shutdown()


@unittest.skipUnless(sys.platform == 'darwin', 'macOS filesystem and user paths')
class MacLocalTests(unittest.TestCase):
    def test_restart_uses_saved_bytes_and_preserves_payload(self):
        with tempfile.TemporaryDirectory() as temp, ThreadingHTTPServer(('127.0.0.1', 0), Handler) as server:
            server.starts = []
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                env = {**os.environ, 'HOME': temp, 'NO_PROXY': '127.0.0.1',
                       'PYTHONPATH': str(Path(__file__).resolve().parents[1])}
                url = f'http://127.0.0.1:{server.server_port}/restart.bin'
                for step in ('pause', 'resume'):
                    server.starts.clear()
                    subprocess.run([sys.executable, __file__, step, url], env=env, check=True, timeout=18)
                self.assertTrue(any(s not in (0, len(DATA)//2) for s in server.starts), server.starts)
            finally:
                server.shutdown()
                thread.join()

    def test_absent_external_volume_cannot_be_created_on_startup_disk(self):
        import uuid
        from downloader.utils.file_utils import check_download_volumes, ensure_dir
        path = '/Volumes/daw-test-' + uuid.uuid4().hex + '/downloads'
        self.assertFalse(check_download_volumes([(path, '下载目录')])[0])
        with self.assertRaises(OSError):
            ensure_dir(path)
        self.assertFalse(Path(path).exists())


if __name__ == '__main__':
    if sys.argv[1:2] in (['pause'], ['resume']):
        stage(sys.argv[2], sys.argv[1] == 'resume')
    else:
        unittest.main()
