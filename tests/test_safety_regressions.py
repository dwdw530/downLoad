"""Safety regressions: isolated files/SQLite/local HTTP; never operate on user data."""
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack
import hashlib
from http.server import ThreadingHTTPServer
from pathlib import Path
import re
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from downloader.core.chunk_downloader import ChunkDownloader
from downloader.core.download_engine import DownloadEngine
from downloader.core.task_manager import TaskManager
from downloader.database.db_manager import DatabaseManager
from downloader.ui.main_window import MainWindow, ShutdownConfirmDialog
from downloader.ui.task_detail_window import TaskDetailWindow
from downloader.utils.file_utils import get_filename_from_url, publish_download, validate_filename
from test_features import FeatureFixture
from test_download_regressions import DownloadHandler, PAYLOAD


class VersionedHandler(DownloadHandler):
    def do_GET(self):
        if not self.path.startswith('/version'):
            return super().do_GET()
        data, etag, modified = self.server.resource
        requested = self.headers.get('Range')
        self.server.requests.append((self.path, requested, self.headers.get('If-Range')))
        if self.headers.get('If-Match') and self.headers['If-Match'] != etag:
            self.send_error(412)
            return
        if (self.headers.get('If-Unmodified-Since')
                and self.headers['If-Unmodified-Since'] != modified):
            self.send_error(412)
            return
        start, end = 0, len(data) - 1
        validator = self.headers.get('If-Range')
        if requested and (not validator or validator in (etag, modified)):
            start, end = map(int, re.fullmatch(r'bytes=(\d+)-(\d+)', requested).groups())
            end = min(end, len(data) - 1)
            self.send_response(206)
            self.send_header('Content-Range', f'bytes {start}-{end}/{len(data)}')
        else:
            self.send_response(200)
        if etag:
            self.send_header('ETag', etag)
        if modified:
            self.send_header('Last-Modified', modified)
        body = data[start:end + 1]
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            pass


class SafetyCoreTests(FeatureFixture):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(('127.0.0.1', 0), VersionedHandler)
        cls.server.requests = []
        cls.server_thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.server_thread.start()
        cls.base_url = f'http://127.0.0.1:{cls.server.server_port}'

    def setUp(self):
        super().setUp()
        self.server.resource = (PAYLOAD, '"v1"', None)
        self.server.requests.clear()

    def seed_partial(self, task_id):
        chunks = self.db.get_chunks(task_id)
        if chunks:
            path = Path(chunks[0]['temp_file'])
            size = chunks[0]['end_byte'] - chunks[0]['start_byte'] + 1
        else:
            path = Path(self.config.temp_dir) / (task_id + '.tmp')
            size = 1031
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(PAYLOAD[:size])
        self.db.update_task_status(task_id, 'paused')
        return path

    def test_unsafe_url_and_explicit_filename_rejected_before_network(self):
        names = ('../outside.txt', r'..\outside.txt', 'C:outside.txt', 'NUL.txt', 'CON',
                 'COM1.zip', 'LPT9', 'file.txt:secret', 'trailing.', 'trailing ', 'bad\x00name')
        with patch.object(self.engine, 'check_url_support_range') as probe:
            for name in names:
                with self.subTest(name=name):
                    self.assertIsNone(self.engine.create_download_task(self.base_url + '/range', filename=name))
            for encoded in ('%2e%2e%2foutside.txt', '%2e%2e%5coutside.txt', '%43%3aoutside.txt'):
                self.assertIsNone(self.engine.create_download_task(self.base_url + '/' + encoded))
            probe.assert_not_called()
        self.assertEqual(self.db.get_all_tasks(), [])
        self.assertEqual(get_filename_from_url('https://example.test/%E4%B8%AD%E6%96%87.zip'), '中文.zip')
        self.assertEqual(validate_filename('normal (1).zip'), 'normal (1).zip')

    def test_same_names_reserve_distinct_paths_and_keep_existing_file(self):
        folder = Path(self.config.download_dir)
        folder.mkdir()
        original = folder / 'report.zip'
        original.write_bytes(b'user file')
        first = self.create(filename='report.zip')
        second = self.create('/headless', filename='report.zip')
        self.assertEqual(self.db.get_task(first)['filename'], 'report (1).zip')
        self.assertEqual(self.db.get_task(second)['filename'], 'report (2).zip')
        self.manager.start_task(first)
        self.wait_task(first)
        self.wait_task(second)
        self.assertEqual(original.read_bytes(), b'user file')

    def test_concurrent_task_creation_reserves_names_in_transaction(self):
        with ThreadPoolExecutor(max_workers=4) as pool:
            ids = list(pool.map(lambda _: self.create(filename='same.bin'), range(4)))
        paths = [self.db.get_task(tid)['save_path'] for tid in ids]
        self.assertEqual(len(set(paths)), 4)

    def test_external_file_created_after_task_add_gets_new_name(self):
        tid = self.create(filename='late.bin')
        original = Path(self.db.get_task(tid)['save_path'])
        original.parent.mkdir(parents=True)
        original.write_bytes(b'user file')
        self.assertTrue(self.manager.start_task(tid))
        finished = self.wait_task(tid)
        self.assertEqual(finished['filename'], 'late (1).bin')
        self.assertEqual(original.read_bytes(), b'user file')
        self.assertEqual(Path(finished['save_path']).read_bytes(), PAYLOAD)

    def test_destination_race_never_overwrites_external_file(self):
        tid = self.create(filename='racing.bin')
        target = Path(self.db.get_task(tid)['save_path'])
        def hashing(*_args):
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(b'external file')
            return hashlib.md5(PAYLOAD).hexdigest()
        with patch('downloader.core.download_engine.calculate_file_hash', side_effect=hashing):
            self.assertTrue(self.manager.start_task(tid))
            self.wait_task(tid, 'failed')
        self.assertEqual(target.read_bytes(), b'external file')
        self.assertTrue(Path(self.db.get_chunks(tid)[0]['temp_file']).exists())

    def test_unchanged_etag_reuses_partial_chunks_after_database_reopen(self):
        tid = self.create('/version')
        first = self.seed_partial(tid)
        reopened = DatabaseManager(str(self.root / 'downloads.db'))
        self.assertEqual(reopened.get_task(tid)['resource_etag'], '"v1"')
        self.server.requests.clear()
        self.assertTrue(self.manager.resume_task(tid))
        finished = self.wait_task(tid)
        self.assertEqual(Path(finished['save_path']).read_bytes(), PAYLOAD)
        requests = self.server.requests
        self.assertTrue(any(validator == '"v1"' for _, _, validator in requests))
        first_end = len(PAYLOAD) // 4 - 1
        self.assertFalse(any(rng == f'bytes=0-{first_end}' for _, rng, _ in requests))
        deadline = time.monotonic() + 2
        while first.exists() and time.monotonic() < deadline:
            time.sleep(0.01)
        self.assertFalse(first.exists())

    def test_changed_version_resets_partial_chunks_for_same_and_different_sizes(self):
        for length in (len(PAYLOAD), len(PAYLOAD) + 1000):
            with self.subTest(length=length):
                self.server.resource = (PAYLOAD, '"v1"', None)
                tid = self.create('/version', filename=f'v-{length}.bin')
                self.seed_partial(tid)
                changed = b'B' * length
                self.server.resource = (changed, '"v2"', None)
                self.assertTrue(self.manager.resume_task(tid))
                finished = self.wait_task(tid)
                self.assertEqual(Path(finished['save_path']).read_bytes(), changed)
                self.assertEqual(finished['total_size'], length)
                self.assertEqual(finished['resource_etag'], '"v2"')

    def test_changed_last_modified_resets_partial_download(self):
        self.server.resource = (PAYLOAD, None, 'Wed, 01 Jan 2025 00:00:00 GMT')
        tid = self.create('/version')
        self.seed_partial(tid)
        changed = b'Z' * len(PAYLOAD)
        self.server.resource = (changed, None, 'Thu, 02 Jan 2025 00:00:00 GMT')
        self.assertTrue(self.manager.resume_task(tid))
        self.assertEqual(Path(self.wait_task(tid)['save_path']).read_bytes(), changed)

    def test_no_validator_or_weak_etag_restarts_with_one_full_request(self):
        for etag in (None, 'W/"v1"'):
            with self.subTest(etag=etag):
                self.server.resource = (PAYLOAD, etag, None)
                tid = self.create('/version')
                self.seed_partial(tid)
                self.server.resource = (b'X' * len(PAYLOAD), etag, None)
                self.server.requests.clear()
                self.assertTrue(self.manager.resume_task(tid))
                finished = self.wait_task(tid)
                self.assertEqual(Path(finished['save_path']).read_bytes(), b'X' * len(PAYLOAD))
                self.assertEqual(finished['thread_count'], 1)
                self.assertEqual([rng for _, rng, _ in self.server.requests], ['bytes=0-0', None])

    def test_version_changes_after_probe_fail_without_publishing_mixed_file(self):
        tid = self.create('/version')
        self.seed_partial(tid)
        original = self.engine._start_multithread_download
        def changed(task, resume, run_id):
            self.server.resource = (b'N' * len(PAYLOAD), '"v2"', None)
            return original(task, resume, run_id)
        with patch.object(self.engine, '_start_multithread_download', side_effect=changed):
            self.assertTrue(self.manager.resume_task(tid))
            failed = self.wait_task(tid, 'failed')
        self.assertFalse(Path(failed['save_path']).exists())

    def test_bad_range_total_rejected_before_appending(self):
        path = self.root / 'part.tmp'
        path.write_bytes(b'AAAA')
        worker = ChunkDownloader(1, 't', 'https://example.test/file', 0, 7, str(path),
                                 total_size=8, etag='"v1"')
        worker.downloaded_bytes = 4
        response = Mock(status_code=206, headers={'Content-Range': 'bytes 4-7/12', 'ETag': '"v1"'})
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=False)
        with patch('downloader.core.chunk_downloader.requests.get', return_value=response):
            with self.assertRaises(ValueError):
                worker._download_chunk(4)
        self.assertEqual(path.read_bytes(), b'AAAA')

    def test_pause_during_hash_waits_for_worker_and_preserves_resume_data(self):
        tid = self.create('/version')
        entered, release = threading.Event(), threading.Event()
        def hashing(*_args):
            entered.set()
            release.wait(5)
            return hashlib.md5(PAYLOAD).hexdigest()
        stopper = None
        try:
            with patch('downloader.core.download_engine.calculate_file_hash', side_effect=hashing):
                self.assertTrue(self.manager.start_task(tid))
                self.assertTrue(entered.wait(3))
                stopper = threading.Thread(target=self.manager.pause_all)
                stopper.start()
                time.sleep(0.1)
                self.assertTrue(stopper.is_alive())
                release.set()
                stopper.join(4)
            self.assertFalse(stopper.is_alive())
            task = self.db.get_task(tid)
            self.assertEqual(task['status'], 'paused')
            self.assertFalse(Path(task['save_path']).exists())
            self.assertTrue(all(Path(c['temp_file']).exists() for c in self.db.get_chunks(tid)))
            self.assertTrue(self.manager.resume_task(tid))
            self.assertEqual(Path(self.wait_task(tid)['save_path']).read_bytes(), PAYLOAD)
        finally:
            release.set()
            if stopper:
                stopper.join(5)

    def test_stale_verification_callback_cannot_complete_a_new_run(self):
        tid = self.create('/version')
        self.db.update_task_status(tid, 'downloading')
        run_id = self.engine._next_task_run_id(tid)
        stage = self.root / 'staged.bin'
        stage.write_bytes(PAYLOAD)
        def new_run(*_args):
            self.engine._next_task_run_id(tid)
            return hashlib.md5(PAYLOAD).hexdigest()
        with patch('downloader.core.download_engine.calculate_file_hash', side_effect=new_run):
            self.engine._verify_and_finish(tid, str(stage), run_id)
        self.assertNotEqual(self.db.get_task(tid)['status'], 'completed')
        self.assertTrue(stage.exists())
        self.assertEqual(self.db.get_history(), [])

    def test_full_queue_retry_enters_pending_and_eventually_starts(self):
        first, retry = self.create(filename='first.bin'), self.create(filename='retry.bin')
        self.db.update_task_status(retry, 'failed')
        def start(tid, resume=False):
            self.db.update_task_status(tid, 'downloading')
            return True
        with patch.object(self.engine, 'start_download', side_effect=start) as launch:
            self.assertTrue(self.manager.start_task(first))
            self.assertTrue(self.manager.start_task(retry))
            self.assertEqual(self.db.get_task(retry)['status'], 'pending')
            self.assertEqual(launch.call_count, 1)
            self.db.update_task_status(first, 'completed')
            self.manager._on_engine_status_change(first, 'completed', '')
            self.assertEqual(launch.call_args.args, (retry,))

    def test_resume_all_honors_manual_order(self):
        first, second = self.create(filename='old.bin'), self.create(filename='new.bin')
        for tid in (first, second):
            self.db.update_task_status(tid, 'paused')
        self.manager.move_task_to_top(second)
        with patch.object(self.manager, 'resume_task', return_value=True) as resume:
            self.assertEqual(self.manager.resume_all(), 2)
        self.assertEqual([c.args[0] for c in resume.call_args_list], [second, first])

    def test_pending_probe_is_activity_and_counter_released_on_error(self):
        before, busy = self.manager.get_activity_state()
        self.assertFalse(busy)
        def error(*_args, **_kwargs):
            generation, busy = self.manager.get_activity_state()
            self.assertTrue(busy)
            self.assertGreater(generation, before)
            raise ValueError('probe failed')
        with patch.object(self.engine, 'create_download_task', side_effect=error):
            with self.assertRaises(ValueError):
                self.manager.add_task('https://example.test/file')
        self.assertFalse(self.manager.get_activity_state()[1])


class SafetyLogicTests(unittest.TestCase):
    def shutdown_ui(self):
        manager = Mock()
        manager.get_activity_state.return_value = (10, False)
        manager.get_all_tasks.return_value = [{'status': 'completed'}]
        manager.engine.config.after_download = {'shutdown': True}
        ui = SimpleNamespace(task_manager=manager, _batch_add_pending=0, _shutdown_activity=10)
        ui._has_active_tasks = lambda: MainWindow._has_active_tasks(ui)
        ui._shutdown_still_valid = lambda: MainWindow._shutdown_still_valid(ui)
        return ui

    def test_shutdown_rechecks_new_active_and_even_already_finished_tasks(self):
        for generation, busy, status in ((11, True, 'downloading'), (11, False, 'completed'), (10, False, 'pending')):
            ui = self.shutdown_ui()
            ui.task_manager.get_activity_state.return_value = (generation, busy)
            ui.task_manager.get_all_tasks.return_value = [{'status': status}]
            with patch('downloader.ui.main_window.subprocess.Popen') as popen:
                MainWindow._perform_shutdown(ui)
            popen.assert_not_called()

    def test_shutdown_rechecks_batch_probe_and_setting(self):
        for reason in ('batch', 'probe', 'setting'):
            ui = self.shutdown_ui()
            if reason == 'batch':
                ui._batch_add_pending = 1
            elif reason == 'probe':
                ui.task_manager.get_activity_state.return_value = (10, True)
            else:
                ui.task_manager.engine.config.after_download['shutdown'] = False
            with patch('downloader.ui.main_window.subprocess.Popen') as popen:
                MainWindow._perform_shutdown(ui)
            popen.assert_not_called()

    def test_unchanged_idle_shutdown_invokes_mocked_command(self):
        with patch('downloader.ui.main_window.subprocess.Popen') as popen:
            MainWindow._perform_shutdown(self.shutdown_ui())
        self.assertEqual(popen.call_args.args[0], ['shutdown', '/s', '/t', '0'])

    def test_countdown_and_immediate_button_cancel_on_invalidated_activity(self):
        dialog = SimpleNamespace(winfo_exists=lambda: True, _can_shutdown=lambda: False,
                                 destroy=Mock(), _remaining=5, _on_shutdown=Mock())
        ShutdownConfirmDialog._tick(dialog)
        dialog.destroy.assert_called_once()
        ShutdownConfirmDialog._shutdown_now(dialog)
        dialog._on_shutdown.assert_not_called()

    def test_speed_samples_coalesce_by_second_and_keep_true_duration(self):
        widgets = {key: Mock() for key in ('progress_bar', 'progress_label', 'speed_label', 'size_label', 'eta_label')}
        widgets['status'] = 'downloading'
        ui = SimpleNamespace(task_widgets={'t': widgets}, _speed_history={}, _push_detail_update=Mock())
        for second in range(100, 118):
            with patch('downloader.ui.main_window.time.monotonic', return_value=second):
                for _ in range(10):
                    MainWindow._update_task_progress(ui, 't', 10, 100, 2048)
        self.assertEqual(len(ui._speed_history['t']), 18)
        canvas = Mock()
        canvas.winfo_width.return_value = 600
        canvas.winfo_height.return_value = 200
        window = SimpleNamespace(_closed=False, winfo_exists=lambda: True, canvas=canvas,
                                 _history=ui._speed_history['t'])
        TaskDetailWindow._redraw(window)
        self.assertIn('最近 17 秒', [call.kwargs.get('text') for call in canvas.create_text.call_args_list])
        with patch('downloader.ui.main_window.time.monotonic', return_value=400):
            MainWindow._update_task_progress(ui, 't', 10, 100, 2048)
        self.assertEqual(list(ui._speed_history['t']), [(400, 2048)])

    def test_publish_preserves_existing_target_and_retains_source_on_cancel(self):
        with tempfile.TemporaryDirectory() as root:
            source, dest = Path(root) / 'source.bin', Path(root) / 'dest.bin'
            source.write_bytes(b'download')
            dest.write_bytes(b'user')
            with self.assertRaises(FileExistsError):
                publish_download(str(source), str(dest))
            self.assertEqual(dest.read_bytes(), b'user')
            dest.unlink()
            with self.assertRaises(InterruptedError):
                publish_download(str(source), str(dest), lambda: False)
            self.assertFalse(dest.exists())
            self.assertEqual(source.read_bytes(), b'download')


class SafetyGuiTests(FeatureFixture):
    def setUp(self):
        super().setUp()
        self.stack = ExitStack()
        for target in ('downloader.ui.main_window.TrayManager.start',
                       'downloader.ui.main_window.TrayManager.notify_download_complete',
                       'tkinter.messagebox.showinfo', 'tkinter.messagebox.showerror'):
            self.stack.enter_context(patch(target))
        self.app = MainWindow(self.manager)
        self.app.withdraw()

    def tearDown(self):
        try:
            for timer in self.app.tk.call('after', 'info'):
                self.app.after_cancel(timer)
            self.app._exit_app()
        finally:
            self.stack.close()
            super().tearDown()

    def test_loading_all_tasks_preserves_saved_second_host(self):
        for tid, host in (('a', 'a.example.test'), ('b', 'b.example.test')):
            self.db.create_task(tid, f'https://{host}/file.zip', tid + '.zip', str(self.root / (tid + '.zip')))
        self.config.ui_active_category = 'host:b.example.test'
        self.app._active_category = self.config.ui_active_category
        self.app._load_existing_tasks()
        self.assertEqual(self.app._active_category, 'host:b.example.test')
        self.assertEqual(self.config.ui_active_category, 'host:b.example.test')
        self.assertFalse(self.app.task_widgets['a']['visible'])
        self.assertTrue(self.app.task_widgets['b']['visible'])

    def test_missing_saved_host_falls_back_after_loading_even_empty_database(self):
        self.config.ui_active_category = 'host:missing.example.test'
        self.app._active_category = self.config.ui_active_category
        self.app._load_existing_tasks()
        self.assertEqual(self.app._active_category, 'all')
        self.assertEqual(self.config.ui_active_category, 'all')

    def test_card_name_follows_reserved_retry_destination(self):
        tid = self.create(filename='report.zip')
        self.app._add_task_widget(self.db.get_task(tid))
        target = Path(self.db.get_task(tid)['save_path'])
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b'user')
        self.db.reserve_task_path(tid)
        self.app._update_task_status(tid, 'downloading')
        widgets = self.app.task_widgets[tid]
        self.assertEqual(widgets['filename'], 'report (1).zip')
        self.assertEqual(widgets['filename_label'].cget('text'), 'report (1).zip')


if __name__ == '__main__':
    unittest.main()
