"""Download regressions using a local HTTP server and isolated SQLite files."""
import errno
import os
from pathlib import Path
from queue import SimpleQueue
import re
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace
from unittest.mock import Mock, patch

from downloader.core.chunk_downloader import ChunkDownloader
from downloader.core.download_engine import DownloadEngine
from downloader.core.task_manager import TaskManager
from downloader.database.db_manager import DatabaseManager
from downloader.ui.main_window import MainWindow


PAYLOAD = bytes(range(251)) * 131


class DownloadHandler(BaseHTTPRequestHandler):
    def log_message(self, *_args):
        pass

    def do_HEAD(self):
        if self.path == '/headless':
            self.send_error(405)
            return
        self.send_response(200)
        self.send_header('Content-Length', str(len(PAYLOAD)))
        self.send_header('Accept-Ranges', 'bytes')
        self.end_headers()

    def do_GET(self):
        range_header = self.headers.get('Range')
        self.server.requests.append((self.path, range_header, self.headers.get('Accept-Encoding')))
        if self.path == '/not-found':
            self.send_error(404)
            return
        start, end = 0, len(PAYLOAD) - 1
        ignores_range = self.path == '/unreliable' and range_header != 'bytes=0-0'
        if (range_header and not ignores_range
                and self.path not in ('/no-range', '/ignored-range', '/short', '/oversized')):
            match = re.fullmatch(r'bytes=(\d+)-(\d+)', range_header)
            start, end = map(int, match.groups())
            self.send_response(206)
            if self.path != '/missing-range':
                reported_start = start + 1 if self.path == '/bad-range' else start
                self.send_header('Content-Range', f'bytes {reported_start}-{end}/{len(PAYLOAD)}')
        else:
            self.send_response(200)
        body = PAYLOAD[start:end + 1]
        if self.path == '/short':
            body = body[:-7]
        elif self.path == '/oversized':
            body += b'extra bytes'
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        if self.path == '/truncated-once' and start == 0:
            body = body[:8192]
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            pass


class DownloadRegressions(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(('127.0.0.1', 0), DownloadHandler)
        cls.server.requests = []
        cls.server_thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.server_thread.start()
        cls.base_url = f'http://127.0.0.1:{cls.server.server_port}'

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.server_thread.join(2)

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='downloader-regression-')
        self.root = Path(self.temp.name)
        self.db = DatabaseManager(str(self.root / 'downloads.db'))
        self.config = SimpleNamespace(
            temp_dir=str(self.root / 'temp'), download_dir=str(self.root / 'out'),
            thread_count=4, timeout=2, retry_times=1, user_agent='DownloaderTests',
            speed_limit=0, proxies={'http': '', 'https': ''},
        )
        self.engine = DownloadEngine(self.db, self.config)

    def tearDown(self):
        self.engine.shutdown()
        # Progress monitors use a one-second interval; let them exit before cleanup.
        time.sleep(1.05)
        self.temp.cleanup()

    def make_task(self, task_id='task', multithread=True, status='pending', save_path=None):
        count = 4 if multithread else 1
        chunks = self.engine._build_chunks(task_id, len(PAYLOAD), count) if multithread else []
        self.assertTrue(self.db.create_task_with_chunks(
            task_id, self.base_url + ('/range' if multithread else '/no-range'),
            task_id + '.bin', str(save_path or self.root / 'out' / (task_id + '.bin')),
            len(PAYLOAD), multithread, count, chunks,
        ))
        self.db.update_task_status(task_id, status)
        return task_id

    def wait_terminal(self, task_id):
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            task = self.db.get_task(task_id)
            if task['status'] in ('completed', 'failed', 'verify_failed', 'cancelled'):
                return task
            time.sleep(0.02)
        self.fail(f'Task stuck in {self.db.get_task(task_id)["status"]}')

    def worker(self, path='/range', end=None, use_range=True):
        return ChunkDownloader(
            1, 'chunk-task', self.base_url + path, 0,
            len(PAYLOAD) - 1 if end is None else end, str(self.root / 'chunk.tmp'),
            timeout=2, retry_times=1, proxies=self.config.proxies, use_range=use_range,
        )

    def assert_download(self, task_id):
        self.assertTrue(self.engine.start_download(task_id, resume=True))
        task = self.wait_terminal(task_id)
        self.assertEqual(task['status'], 'completed')
        self.assertEqual(Path(task['save_path']).read_bytes(), PAYLOAD)
        self.assertEqual(task['downloaded_size'], len(PAYLOAD))
        self.assertEqual(task['speed'], 0)

    def test_resume_merges_previously_completed_chunks(self):
        task_id = self.make_task(status='paused')
        chunk = self.db.get_chunks(task_id)[0]
        Path(chunk['temp_file']).parent.mkdir()
        data = PAYLOAD[chunk['start_byte']:chunk['end_byte'] + 1]
        Path(chunk['temp_file']).write_bytes(data)
        self.db.update_chunk_progress(chunk['chunk_id'], len(data), 'completed')
        self.assert_download(task_id)

    def test_resume_when_every_chunk_is_already_complete(self):
        task_id = self.make_task(status='paused')
        Path(self.config.temp_dir).mkdir()
        for chunk in self.db.get_chunks(task_id):
            data = PAYLOAD[chunk['start_byte']:chunk['end_byte'] + 1]
            Path(chunk['temp_file']).write_bytes(data)
            self.db.update_chunk_progress(chunk['chunk_id'], len(data), 'completed')
        self.assert_download(task_id)

    def test_missing_completed_chunk_is_downloaded_again(self):
        task_id = self.make_task(status='paused')
        chunk = self.db.get_chunks(task_id)[0]
        self.db.update_chunk_progress(chunk['chunk_id'], chunk['end_byte'] + 1, 'completed')
        self.assert_download(task_id)

    def test_short_response_is_not_success(self):
        self.assertFalse(self.worker('/short', use_range=False).download())

    def test_oversized_response_is_not_success(self):
        self.assertFalse(self.worker('/oversized', use_range=False).download())

    def test_invalid_range_response_is_not_written(self):
        for path in ('/bad-range', '/missing-range', '/ignored-range'):
            with self.subTest(path=path):
                worker = self.worker(path, end=31)
                self.assertFalse(worker.download())
                self.assertFalse(Path(worker.temp_file).exists())

    def test_fresh_download_replaces_existing_temporary_file(self):
        worker = self.worker()
        Path(worker.temp_file).write_bytes(b'x' * len(PAYLOAD))
        self.assertTrue(worker.download(resume=False))
        self.assertEqual(Path(worker.temp_file).read_bytes(), PAYLOAD)

    def test_oversized_temporary_file_is_redownloaded(self):
        worker = self.worker()
        Path(worker.temp_file).write_bytes(PAYLOAD + b'extra')
        self.assertTrue(worker.download(resume=True))
        self.assertEqual(Path(worker.temp_file).read_bytes(), PAYLOAD)

    def test_range_resume_requests_only_missing_bytes(self):
        worker = self.worker()
        Path(worker.temp_file).write_bytes(PAYLOAD[:1031])
        self.assertTrue(worker.download(resume=True))
        self.assertEqual(Path(worker.temp_file).read_bytes(), PAYLOAD)
        self.assertIn(('/range', f'bytes=1031-{len(PAYLOAD) - 1}', 'identity'), self.server.requests)

    def test_probe_handles_head_rejection_and_http_errors(self):
        self.assertEqual(self.engine.check_url_support_range(self.base_url + '/headless'), (True, len(PAYLOAD)))
        self.assertEqual(self.engine.check_url_support_range(self.base_url + '/not-found'), (False, 0))

    def test_output_error_marks_failed_and_releases_queue_slot(self):
        target = self.root / 'blocked'
        target.write_bytes(b'existing file')
        task_id = self.make_task(multithread=False, save_path=target / 'file.bin')
        manager = TaskManager(self.engine, self.db, max_concurrent=1)
        self.assertTrue(manager.start_task(task_id))
        self.assertEqual(self.wait_terminal(task_id)['status'], 'failed')
        self.assertNotIn(task_id, manager._running_tasks)
        self.assertEqual(target.read_bytes(), b'existing file')

    def test_single_download_can_move_across_volumes(self):
        task_id = self.make_task(multithread=False)
        with patch('os.rename', side_effect=OSError(errno.EXDEV, 'cross-device link')), \
                patch('os.replace', side_effect=OSError(errno.EXDEV, 'cross-device link')):
            self.assert_download(task_id)

    def test_broken_range_server_falls_back_to_complete_single_download(self):
        task_id = self.engine.create_download_task(self.base_url + '/unreliable', filename='fallback.bin')
        self.assertIsNotNone(task_id)
        self.assert_download(task_id)
        self.assertFalse(self.db.get_task(task_id)['support_range'])

    def test_interrupted_range_stream_retries_from_saved_offset(self):
        worker = self.worker('/truncated-once')
        worker.retry_times = 2
        self.assertTrue(worker.download())
        self.assertEqual(Path(worker.temp_file).read_bytes(), PAYLOAD)
        self.assertIn(('/truncated-once', f'bytes=8192-{len(PAYLOAD) - 1}', 'identity'), self.server.requests)

    def test_nonrange_resume_restarts_from_zero(self):
        worker = self.worker('/no-range', use_range=False)
        Path(worker.temp_file).write_bytes(b'x' * len(PAYLOAD))
        self.assertTrue(worker.download(resume=True))
        self.assertEqual(Path(worker.temp_file).read_bytes(), PAYLOAD)

    def test_cancel_waits_for_single_worker_to_release_file(self):
        task_id = self.make_task(multithread=False)
        entered, release, cancelled = threading.Event(), threading.Event(), threading.Event()

        def progress(_task_id, downloaded, *_args):
            if downloaded:
                entered.set()
                release.wait(3)

        self.engine.set_progress_callback(progress)
        self.assertTrue(self.engine.start_download(task_id))
        self.assertTrue(entered.wait(2))
        stopping = threading.Thread(target=lambda: (self.engine.cancel_download(task_id), cancelled.set()))
        stopping.start()
        try:
            self.assertFalse(cancelled.wait(0.1))
        finally:
            release.set()
            stopping.join(4)
        self.assertTrue(cancelled.is_set())
        self.assertEqual(self.db.get_task(task_id)['status'], 'cancelled')
        self.assertFalse(Path(self.db.get_task(task_id)['save_path']).exists())
        with open(Path(self.config.temp_dir) / (task_id + '.tmp'), 'ab') as output:
            output.write(b'file is closed')

    def test_pause_and_resume_range_task_preserves_file(self):
        task_id = self.make_task()
        entered, release = threading.Event(), threading.Event()
        original_progress = self.engine._on_chunk_progress

        def progress(chunk_id, downloaded):
            original_progress(chunk_id, downloaded)
            entered.set()
            release.wait(3)

        with patch.object(self.engine, '_on_chunk_progress', side_effect=progress):
            self.assertTrue(self.engine.start_download(task_id))
            self.assertTrue(entered.wait(2))
            stopping = threading.Thread(target=self.engine.pause_download, args=(task_id,))
            stopping.start()
            release.set()
            stopping.join(4)
        self.assertFalse(stopping.is_alive())
        self.assertEqual(self.db.get_task(task_id)['status'], 'paused')
        self.assertTrue(self.engine.resume_download(task_id))
        task = self.wait_terminal(task_id)
        self.assertEqual(task['status'], 'completed')
        self.assertEqual(Path(task['save_path']).read_bytes(), PAYLOAD)

    def test_expected_hash_is_not_skipped_when_hash_calculation_fails(self):
        task_id = self.make_task(multithread=False)
        self.db.set_expected_hash(task_id, '0' * 32, 'md5')
        with patch('downloader.core.download_engine.calculate_file_hash', return_value=''):
            self.assertTrue(self.engine.start_download(task_id))
            self.assertEqual(self.wait_terminal(task_id)['status'], 'verify_failed')

    def test_pause_all_does_not_start_queued_tasks(self):
        manager = TaskManager(self.engine, self.db, max_concurrent=1)
        self.make_task('running', multithread=False, status='downloading')
        self.make_task('queued', multithread=False)
        manager._running_tasks.add('running')
        with patch.object(self.engine, 'start_download', return_value=True) as start:
            manager.pause_all()
        start.assert_not_called()
        self.assertEqual(self.db.get_task('running')['status'], 'paused')
        self.assertEqual(self.db.get_task('queued')['status'], 'paused')

    def test_shutdown_does_not_start_queued_tasks(self):
        manager = TaskManager(self.engine, self.db, max_concurrent=1)
        self.make_task('running', multithread=False, status='downloading')
        self.make_task('queued', multithread=False)
        manager._running_tasks.add('running')
        with patch.object(self.engine, 'start_download', return_value=True) as start:
            manager.shutdown()
        start.assert_not_called()

    def test_concurrent_start_does_not_duplicate_task(self):
        manager = TaskManager(self.engine, self.db, max_concurrent=3)
        task_id = self.make_task(multithread=False)
        entered = threading.Event()
        release = threading.Event()

        def delayed_start(*_args, **_kwargs):
            entered.set()
            return release.wait(3)

        with patch.object(self.engine, 'start_download', side_effect=delayed_start) as start:
            first = threading.Thread(target=manager.start_task, args=(task_id,))
            first.start()
            self.assertTrue(entered.wait(2))
            try:
                self.assertFalse(manager.start_task(task_id))
            finally:
                release.set()
                first.join(4)
            self.assertEqual(start.call_count, 1)


class UiCallbackRegressions(unittest.TestCase):
    def test_download_callbacks_do_not_wait_for_tk_main_thread(self):
        ui = SimpleNamespace(
            _ui_events=SimpleQueue(), task_manager=SimpleNamespace(get_task=lambda _task_id: {'task_id': 'task'}),
            _add_task_widget=Mock(), _update_task_status=Mock(), _update_task_progress=Mock(),
            after=Mock(side_effect=AssertionError('Worker thread must not call Tk')),
        )
        errors = []

        def worker():
            try:
                MainWindow._on_task_added(ui, 'task')
                MainWindow._on_task_status_changed(ui, 'task', 'downloading', '')
                MainWindow._on_task_progress(ui, 'task', 10, 100, 5)
            except Exception as error:
                errors.append(error)

        thread = threading.Thread(target=worker)
        thread.start()
        thread.join(2)
        self.assertFalse(thread.is_alive())
        self.assertEqual(errors, [])
        ui.after.assert_not_called()
        while not ui._ui_events.empty():
            callback, args = ui._ui_events.get_nowait()
            callback(*args)
        ui._add_task_widget.assert_called_once_with({'task_id': 'task'})
        ui._update_task_status.assert_called_once_with('task', 'downloading')
        ui._update_task_progress.assert_called_once_with('task', 10, 100, 5)


if __name__ == '__main__':
    unittest.main()
