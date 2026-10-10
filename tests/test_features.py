"""Feature checks against a local HTTP origin/proxy and disposable user data."""
import hashlib
import os
from pathlib import Path
import tempfile
import threading
import time
import unittest
from http.server import ThreadingHTTPServer
from unittest.mock import patch
from urllib.parse import urlsplit

from downloader.core.chunk_downloader import SpeedLimiter
from downloader.core.download_engine import DownloadEngine
from downloader.core.task_manager import TaskManager
from downloader.database.db_manager import DatabaseManager
from downloader.utils.config import ConfigManager
from downloader.utils.file_utils import format_remaining
from test_download_regressions import DownloadHandler, PAYLOAD


class FeatureHandler(DownloadHandler):
    def do_GET(self):
        self.server.targets.append(self.path)
        self.path = urlsplit(self.path).path
        if self.path == '/redirect':
            self.send_response(302)
            self.send_header('Location', '/range')
            self.end_headers()
        elif self.path == '/tiny':
            self.send_response(206 if self.headers.get('Range') else 200)
            if self.headers.get('Range'):
                self.send_header('Content-Range', 'bytes 0-0/1')
            self.send_header('Content-Length', '1')
            self.end_headers()
            self.wfile.write(b'x')
        else:
            super().do_GET()


class FeatureFixture(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(('127.0.0.1', 0), FeatureHandler)
        cls.server.requests = []
        cls.server.targets = []
        cls.server_thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.server_thread.start()
        cls.base_url = f'http://127.0.0.1:{cls.server.server_port}'

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.server_thread.join(2)

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='downloader-features-')
        self.root = Path(self.temp.name)
        self.config = ConfigManager(str(self.root / 'config.json'))
        self.config.download_dir = str(self.root / 'out')
        self.config.set('temp_dir', str(self.root / 'parts'))
        self.config.set('timeout', 2)
        self.config.set('retry_times', 1)
        self.config.thread_count = 4
        # Explicitly isolate these checks from the machine's proxy configuration.
        self.env_patch = patch.dict(os.environ, {'NO_PROXY': '*', 'no_proxy': '*'})
        self.env_patch.start()
        self.db = DatabaseManager(str(self.root / 'downloads.db'))
        self.engine = DownloadEngine(self.db, self.config)
        self.manager = TaskManager(self.engine, self.db, max_concurrent=1)

    def tearDown(self):
        self.manager.shutdown()
        time.sleep(1.05)
        self.env_patch.stop()
        self.temp.cleanup()

    def create(self, path='/range', **kwargs):
        task_id = self.engine.create_download_task(self.base_url + path, **kwargs)
        self.assertIsNotNone(task_id)
        return task_id

    def wait_task(self, task_id, status='completed', timeout=8):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            task = self.db.get_task(task_id)
            if task['status'] == status:
                return task
            if task['status'] in ('failed', 'verify_failed', 'cancelled'):
                self.fail(f'Unexpected task result: {task}')
            time.sleep(0.02)
        self.fail(f'Timed out: {self.db.get_task(task_id)}')


class CoreFeatures(FeatureFixture):
    def test_cancel_does_not_change_a_completed_download(self):
        task_id = self.create()
        self.manager.start_task(task_id)
        self.wait_task(task_id)
        self.assertFalse(self.manager.cancel_task(task_id))
        self.assertEqual(self.db.get_task(task_id)['status'], 'completed')

    def test_redirect_and_one_byte_download(self):
        for path, body in (('/redirect', PAYLOAD), ('/tiny', b'x')):
            with self.subTest(path=path):
                task_id = self.create(path)
                self.assertTrue(self.manager.start_task(task_id))
                task = self.wait_task(task_id)
                self.assertEqual(Path(task['save_path']).read_bytes(), body)
                self.assertLessEqual(task['thread_count'], len(body))

    def test_md5_and_sha256_verification(self):
        for algorithm in ('md5', 'sha256'):
            with self.subTest(algorithm=algorithm):
                digest = hashlib.new(algorithm, PAYLOAD).hexdigest()
                task_id = self.create(filename=algorithm + '.bin',
                                      expected_hash=digest.upper(), hash_type=algorithm)
                self.assertTrue(self.manager.start_task(task_id))
                task = self.wait_task(task_id)
                self.assertEqual(task['actual_hash'], digest)
                self.assertEqual(task['hash_verified'], 1)

    def test_failed_verification_releases_queue_and_can_retry(self):
        first = self.create(filename='first.bin', expected_hash='0' * 32)
        second = self.create(filename='second.bin')
        self.assertTrue(self.manager.start_task(first))
        self.wait_task(first, 'verify_failed')
        self.wait_task(second)
        self.db.set_expected_hash(first, hashlib.md5(PAYLOAD).hexdigest(), 'md5')
        self.assertTrue(self.manager.start_task(first))
        self.assertEqual(self.wait_task(first)['hash_verified'], 1)
        self.assertEqual(Path(self.db.get_task(first)['save_path']).read_bytes(), PAYLOAD)

    def test_real_http_proxy_handles_probe_and_all_chunks(self):
        self.config.set_proxy(True, self.base_url, self.base_url)
        target = 'http://download.invalid/proxy-file.bin'
        before = self.server.targets.count(target)
        task_id = self.engine.create_download_task(target)
        self.assertIsNotNone(task_id)
        self.assertTrue(self.manager.start_task(task_id))
        task = self.wait_task(task_id)
        self.assertEqual(Path(task['save_path']).read_bytes(), PAYLOAD)
        self.assertGreaterEqual(self.server.targets.count(target) - before, 5)

    def test_multithread_speed_limit_applies_to_whole_download(self):
        self.config.speed_limit = len(PAYLOAD)
        task_id = self.create()
        started = time.monotonic()
        self.manager.start_task(task_id)
        self.wait_task(task_id)
        self.assertGreaterEqual(time.monotonic() - started, 0.9)

    def test_history_duration_uses_utc_database_timestamps(self):
        task_id = self.create()
        self.manager.start_task(task_id)
        self.wait_task(task_id)
        record = self.db.get_history()[0]
        self.assertGreaterEqual(record['download_time'], 0)
        self.assertLess(record['download_time'], 10)
        self.assertGreater(record['avg_speed'], 0)
        self.db.clear_history()
        self.assertEqual(self.db.get_history(), [])
        self.assertIsNotNone(self.db.get_task(task_id))

    def test_restart_recovers_download_and_verification_states(self):
        first = self.create(filename='interrupted.bin')
        second = self.create(filename='verifying.bin')
        part = self.db.get_chunks(first)[0]
        Path(part['temp_file']).parent.mkdir(parents=True, exist_ok=True)
        Path(part['temp_file']).write_bytes(PAYLOAD[:100])
        self.db.update_task_status(first, 'downloading')
        self.db.update_task_status(second, 'verifying')
        self.manager = TaskManager(self.engine, self.db)
        self.assertEqual(self.db.get_task(first)['status'], 'paused')
        self.assertEqual(self.db.get_task(first)['downloaded_size'], 100)
        self.assertEqual(self.db.get_task(second)['status'], 'paused')
        self.assertTrue(self.manager.resume_task(first))
        self.assertEqual(Path(self.wait_task(first)['save_path']).read_bytes(), PAYLOAD)
        self.assertTrue(self.manager.resume_task(second))
        self.wait_task(second)

    def test_queue_fills_newly_available_concurrency_slots(self):
        task_ids = [self.create(filename=f'queued-{i}.bin') for i in range(3)]
        def start(task_id, resume=False):
            self.db.update_task_status(task_id, 'downloading')
            return True
        with patch.object(self.engine, 'start_download', side_effect=start):
            self.manager.start_task(task_ids[0])
            self.manager.set_max_concurrent(3)
            self.assertEqual(self.manager._running_tasks, set(task_ids))

    def test_task_delete_cascades_chunks_and_keeps_downloaded_file(self):
        task_id = self.create()
        task = self.db.get_task(task_id)
        Path(task['save_path']).parent.mkdir(parents=True, exist_ok=True)
        Path(task['save_path']).write_bytes(b'user output')
        self.assertTrue(self.manager.delete_task(task_id))
        self.assertIsNone(self.db.get_task(task_id))
        self.assertEqual(self.db.get_chunks(task_id), [])
        self.assertEqual(Path(task['save_path']).read_bytes(), b'user output')

    def test_task_and_chunks_creation_rolls_back_on_invalid_chunk(self):
        self.assertFalse(self.db.create_task_with_chunks(
            'bad', self.base_url, 'bad.bin', str(self.root / 'bad.bin'), 4, True, 2,
            [(0, 0, 1, 'first'), (1, 2, 3)],
        ))
        self.assertIsNone(self.db.get_task('bad'))
        self.assertEqual(self.db.get_chunks('bad'), [])

    def test_saved_settings_survive_reload(self):
        self.config.thread_count = 16
        self.config.max_concurrent_downloads = 5
        self.config.speed_limit = 1024
        self.config.set_proxy(True, self.base_url, self.base_url)
        self.assertTrue(self.config.save())
        reloaded = ConfigManager(self.config.config_path)
        self.assertEqual(reloaded.thread_count, 16)
        self.assertEqual(reloaded.max_concurrent_downloads, 5)
        self.assertEqual(reloaded.speed_limit, 1024)
        self.assertEqual(reloaded.proxies, {'http': self.base_url, 'https': self.base_url})


class TimingFeatures(unittest.TestCase):
    def test_default_database_location_is_independent_of_working_directory(self):
        from downloader.utils.config import get_app_root
        with patch.object(DatabaseManager, '_ensure_db_dir'), \
                patch.object(DatabaseManager, '_init_database'):
            database = DatabaseManager()
        self.assertEqual(Path(database.db_path), Path(get_app_root()) / 'data' / 'downloads.db')

    def test_speed_limit_does_not_credit_time_already_spent_waiting(self):
        clock = [0.0]
        def sleep(seconds):
            clock[0] += seconds
        with patch('downloader.core.chunk_downloader.time.time', side_effect=lambda: clock[0]), \
                patch('downloader.core.chunk_downloader.time.monotonic', side_effect=lambda: clock[0]), \
                patch('downloader.core.chunk_downloader.time.sleep', side_effect=sleep):
            limiter = SpeedLimiter(1024)
            for _ in range(4):
                limiter.acquire(1024)
        self.assertGreaterEqual(clock[0], 4)


class FormatRemainingTests(unittest.TestCase):
    """剩余时间格式化：拿不到速度就不编数字"""

    def test_unknown_or_zero_values_return_placeholder(self):
        for remaining, speed in ((0, 1024), (1024, 0), (1024, -1), (-5, 1024), (1024, None)):
            with self.subTest(remaining=remaining, speed=speed):
                self.assertEqual(format_remaining(remaining, speed), '--')

    def test_seconds_minutes_and_hours(self):
        self.assertEqual(format_remaining(1024, 1024), '00:01')
        self.assertEqual(format_remaining(24689, 4096), '00:06')
        self.assertEqual(format_remaining(60 * 1024, 1024), '01:00')
        self.assertEqual(format_remaining(3661 * 1024, 1024), '1:01:01')

    def test_over_one_day_is_capped(self):
        self.assertEqual(format_remaining(25 * 3600 * 1024, 1024), '超过 1 天')

    def test_completed_download_has_no_remaining_time(self):
        self.assertEqual(format_remaining(0, 1024 * 1024), '--')


if __name__ == '__main__':
    unittest.main()
