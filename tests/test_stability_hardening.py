"""配置原子保存、探测中退出与后台操作；只用临时数据，不启动正式程序。"""
import json
import os
from pathlib import Path
from queue import SimpleQueue
import subprocess
import sys
import tempfile
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from downloader.core.download_engine import DownloadEngine
from downloader.core.task_manager import TaskManager
from downloader.database.db_manager import DatabaseManager
from downloader.ui.main_window import MainWindow
from downloader.utils.config import ConfigManager


class AtomicConfigTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='downloader-atomic-config-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.path = self.root / '中文 config.json'
        self.config = ConfigManager(str(self.path))
        self.config.download_dir = str(self.root / '原下载目录')
        self.assertTrue(self.config.save())
        self.original = self.path.read_bytes()
        self.config.download_dir = str(self.root / '新下载目录')

    def assert_original_preserved(self):
        self.assertEqual(self.path.read_bytes(), self.original)
        self.assertEqual(list(self.root.iterdir()), [self.path])
        self.assertEqual(ConfigManager(str(self.path)).download_dir,
                         str(self.root / '原下载目录'))

    def test_success_replaces_only_after_complete_json_is_written(self):
        replace = os.replace

        def publish(source, destination):
            self.assertEqual(Path(source).parent, self.root)
            self.assertEqual(self.path.read_bytes(), self.original)
            data = json.loads(Path(source).read_text(encoding='utf-8'))
            self.assertEqual(data['download_dir'], str(self.root / '新下载目录'))
            replace(source, destination)

        with patch('downloader.utils.config.os.replace', side_effect=publish) as publish_mock:
            self.assertTrue(self.config.save())
        publish_mock.assert_called_once()
        self.assertEqual(ConfigManager(str(self.path)).download_dir,
                         str(self.root / '新下载目录'))
        self.assertEqual(list(self.root.iterdir()), [self.path])

    def test_partial_serialization_failure_preserves_old_config(self):
        def broken_dump(data, stream, **kwargs):
            stream.write('{"partial":')
            raise OSError('simulated disk write failure')
        with patch('downloader.utils.config.json.dump', side_effect=broken_dump):
            self.assertFalse(self.config.save())
        self.assert_original_preserved()

    def test_flush_failure_preserves_old_config(self):
        with patch('downloader.utils.config.os.fsync', side_effect=OSError('disk full')):
            self.assertFalse(self.config.save())
        self.assert_original_preserved()

    def test_replace_failure_preserves_old_config_and_removes_temp(self):
        with patch('downloader.utils.config.os.replace', side_effect=PermissionError('locked')):
            self.assertFalse(self.config.save())
        self.assert_original_preserved()

    def test_temp_creation_failure_preserves_old_config(self):
        with patch('downloader.utils.config.tempfile.NamedTemporaryFile',
                   side_effect=PermissionError('read only')):
            self.assertFalse(self.config.save())
        self.assert_original_preserved()

    def test_relative_path_saves_without_changing_working_directory(self):
        # Windows 的临时目录和仓库可能不在同一盘；用子进程的独立 cwd 测相对路径，
        # 不改主测试进程的 cwd，以免影响并行 HTTP 夹具。
        code = ('from downloader.utils.config import ConfigManager; '
                'import sys; config = ConfigManager(sys.argv[1]); '
                'config.thread_count = 3; sys.exit(0 if config.save() else 1)')
        subprocess.run([sys.executable, '-B', '-c', code, self.path.name],
                       cwd=self.root, check=True,
                       env=dict(os.environ, PYTHONPATH=str(Path(__file__).resolve().parents[1])))
        self.assertEqual(ConfigManager(str(self.path)).thread_count, 3)
        self.assertEqual(list(self.root.iterdir()), [self.path])


class ShutdownDuringProbeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='downloader-shutdown-gate-')
        self.root = Path(self.temp.name)
        self.config = ConfigManager(str(self.root / 'config.json'))
        self.config.download_dir = str(self.root / 'out')
        self.config.set('temp_dir', str(self.root / 'parts'))
        self.db = DatabaseManager(str(self.root / 'downloads.db'))
        self.engine = DownloadEngine(self.db, self.config)
        self.manager = TaskManager(self.engine, self.db, max_concurrent=1)
        self.release = threading.Event()
        self.entered = threading.Event()
        self.closed = threading.Event()
        self.threads = []
        self.errors = []

    def tearDown(self):
        self.release.set()
        for thread in self.threads:
            thread.join(5)
        self.manager.shutdown()
        self.temp.cleanup()
        self.assertFalse(any(thread.is_alive() for thread in self.threads))
        self.assertEqual(self.errors, [])

    def task(self, status='pending'):
        self.assertTrue(self.db.create_task_with_chunks(
            'probe-task', 'http://example.invalid/probe', 'file.bin',
            str(self.root / 'out' / 'file.bin'), total_size=8,
            support_range=False, thread_count=1, status=status))
        return 'probe-task'

    def probe(self, url, request_context=None, metadata=None):
        self.entered.set()
        if not self.release.wait(5):
            raise TimeoutError('test probe was not released')
        if metadata is not None:
            metadata.update(probe_ok=True, length_kind='known', etag='"version-1"')
        return False, 8

    def launch(self, operation):
        def run():
            try:
                operation()
            except Exception as error:
                self.errors.append(error)
        thread = threading.Thread(target=run, daemon=True)
        self.threads.append(thread)
        thread.start()
        return thread

    def close(self):
        begin = self.engine.begin_shutdown
        def mark_closed():
            begin()
            self.closed.set()
        with patch.object(self.engine, 'begin_shutdown', side_effect=mark_closed):
            self.manager.shutdown()

    def assert_probe_does_not_launch_on_exit(self, resume=False):
        task_id = self.task('paused' if resume else 'pending')
        result = []
        operation = self.manager.resume_task if resume else self.manager.start_task
        with patch.object(self.engine, 'check_url_support_range', side_effect=self.probe), \
                patch.object(self.engine, '_start_singlethread_download') as download:
            starter = self.launch(lambda: result.append(operation(task_id)))
            self.assertTrue(self.entered.wait(2))
            stopper = self.launch(self.close)
            self.assertTrue(self.closed.wait(2))
            self.assertTrue(stopper.is_alive(), '退出必须等在途探测收敛')
            self.release.set()
            starter.join(3)
            stopper.join(3)
        download.assert_not_called()
        self.assertEqual(result, [False])
        self.assertEqual(self.db.get_task(task_id)['status'], 'paused')
        self.assertEqual(self.engine._starting_tasks, {})
        self.assertEqual(self.engine.thread_pools, {})
        self.assertFalse(self.manager.get_activity_state()[1])

    def test_start_probe_cannot_launch_after_shutdown_begins(self):
        self.assert_probe_does_not_launch_on_exit()

    def test_resume_probe_cannot_launch_after_shutdown_begins(self):
        self.assert_probe_does_not_launch_on_exit(resume=True)

    def test_probe_finishing_during_shutdown_keeps_task_paused(self):
        task_id = self.task()
        result = []
        begin = self.engine.begin_shutdown
        with patch.object(self.engine, 'check_url_support_range', side_effect=self.probe), \
                patch.object(self.engine, '_start_singlethread_download') as download:
            starter = self.launch(lambda: result.append(self.manager.start_task(task_id)))
            self.assertTrue(self.entered.wait(2))
            def close_then_finish_probe():
                begin()
                self.release.set()
                starter.join(3)
            # 模拟探测在关闭入口后、退出扫描任务之前恰好返回并释放运行名额。
            with patch.object(self.engine, 'begin_shutdown', side_effect=close_then_finish_probe):
                self.manager.shutdown()
        self.assertEqual(result, [False])
        download.assert_not_called()
        self.assertEqual(self.db.get_task(task_id)['status'], 'paused')

    def test_background_add_is_drained_and_saved_as_paused_without_late_callback(self):
        result = []
        added = Mock()
        self.manager.set_task_added_callback(added)
        with patch.object(self.engine, 'check_url_support_range', side_effect=self.probe), \
                patch.object(self.engine, '_start_singlethread_download') as download:
            adding = self.launch(lambda: result.append(self.manager.add_task(
                'http://example.invalid/late', filename='late.bin')))
            self.assertTrue(self.entered.wait(2))
            stopper = self.launch(self.close)
            self.assertTrue(self.closed.wait(2))
            self.assertTrue(stopper.is_alive())
            self.release.set()
            adding.join(3)
            stopper.join(3)
        self.assertEqual(len(result), 1)
        self.assertIsNotNone(result[0])
        self.assertEqual(self.db.get_task(result[0])['status'], 'paused')
        added.assert_not_called()
        download.assert_not_called()

    def test_pause_invalidates_a_probe_that_has_no_download_worker_yet(self):
        task_id = self.task()
        result = []
        with patch.object(self.engine, 'check_url_support_range', side_effect=self.probe), \
                patch.object(self.engine, '_start_singlethread_download') as download:
            starter = self.launch(lambda: result.append(self.manager.start_task(task_id)))
            self.assertTrue(self.entered.wait(2))
            self.assertTrue(self.manager.pause_task(task_id))
            self.release.set()
            starter.join(3)
        self.assertEqual(result, [False])
        download.assert_not_called()
        self.assertEqual(self.db.get_task(task_id)['status'], 'paused')

    def test_probe_error_releases_start_registration(self):
        task_id = self.task()
        with patch.object(self.engine, 'check_url_support_range', side_effect=OSError('unreachable')):
            self.assertFalse(self.manager.start_task(task_id))
        self.assertEqual(self.engine._starting_tasks, {})
        self.assertFalse(self.manager.get_activity_state()[1])

    def test_shutdown_rejects_new_add_start_and_live_nonrange_resume(self):
        task_id = self.task('paused')
        downloader = Mock()
        self.engine.active_downloaders[task_id] = [downloader]
        self.manager.shutdown()
        with patch.object(self.engine, 'create_download_task') as create, \
                patch.object(self.engine, 'create_youtube_task') as video:
            self.assertIsNone(self.manager.add_task('http://example.invalid/file'))
            self.assertIsNone(self.manager.add_youtube_task('http://example.invalid/video', 'v.mp4', None))
        create.assert_not_called()
        video.assert_not_called()
        self.assertFalse(self.engine.start_download(task_id))
        self.assertFalse(self.engine.resume_download(task_id))
        downloader.resume.assert_not_called()

    def test_pause_all_cannot_restore_scheduling_over_shutdown(self):
        self.task()
        def pause(_task_id):
            self.manager._shutdown_started = True
            return True
        with patch.object(self.manager, 'pause_task', side_effect=pause):
            self.assertEqual(self.manager.pause_all(), 1)
        self.assertTrue(self.manager._scheduling_paused)


class AsyncTaskActionTests(unittest.TestCase):
    def setUp(self):
        self.main_thread = threading.get_ident()
        manager = Mock()
        manager.begin_batch_operation.return_value = True
        manager.get_task.return_value = {'status': 'paused'}
        self.ui = SimpleNamespace(
            task_manager=manager, _batch_busy=False, _exiting=False,
            _shutdown_pending=False, _ui_events=SimpleQueue(), _flash_status=Mock())
        self.ui._task_actions_blocked = lambda: self.ui._batch_busy or self.ui._exiting
        self.ui._set_batch_busy = self.set_busy
        self.ui._run_task_action = lambda *args: MainWindow._run_task_action(self.ui, *args)
        self.ui._finish_task_action = lambda *args: MainWindow._finish_task_action(self.ui, *args)

    def set_busy(self, value):
        self.assertEqual(threading.get_ident(), self.main_thread, 'worker 不能操作 Tk')
        self.ui._batch_busy = value

    def test_slow_operations_return_immediately_and_finish_only_on_ui_queue(self):
        cases = (('_on_start_task', 'resume_task', ('task',), True),
                 ('_on_pause_task', 'pause_task', ('task',), True),
                 ('_on_pause_all', 'pause_all', (), 2),
                 ('_on_resume_all', 'resume_all', (), 2))
        for callback, method, args, result in cases:
            with self.subTest(callback=callback):
                entered, release = threading.Event(), threading.Event()
                thread_ids = []
                def slow(*_args):
                    thread_ids.append(threading.get_ident())
                    entered.set()
                    release.wait(3)
                    return result
                operation = getattr(self.ui.task_manager, method)
                operation.reset_mock()
                operation.side_effect = slow
                try:
                    getattr(MainWindow, callback)(self.ui, *args)
                    self.assertTrue(entered.wait(1))
                    self.assertTrue(self.ui._batch_busy)
                    self.assertNotEqual(thread_ids, [self.main_thread])
                    getattr(MainWindow, callback)(self.ui, *args)
                    self.assertEqual(operation.call_count, 1)
                finally:
                    release.set()
                finish, values = self.ui._ui_events.get(timeout=2)
                self.assertTrue(self.ui._batch_busy, 'worker 返回不能自行启用 Tk 控件')
                finish(*values)
                self.assertFalse(self.ui._batch_busy)

    def test_failed_worker_reports_error_on_ui_thread_and_releases_busy(self):
        self.ui.task_manager.pause_task.side_effect = RuntimeError('sensitive-url-not-for-ui')
        with patch('downloader.ui.main_window.messagebox.showerror') as error:
            MainWindow._on_pause_task(self.ui, 'task')
            finish, values = self.ui._ui_events.get(timeout=2)
            error.assert_not_called()
            finish(*values)
        self.assertFalse(self.ui._batch_busy)
        self.ui.task_manager.end_batch_operation.assert_called_once_with()
        self.assertNotIn('sensitive', str(error.call_args))

    def test_thread_start_failure_releases_without_scheduling_on_tk_thread(self):
        with patch('downloader.ui.main_window.threading.Thread.start', side_effect=RuntimeError('no thread')), \
                patch('downloader.ui.main_window.messagebox.showerror'):
            MainWindow._on_pause_all(self.ui)
        self.assertFalse(self.ui._batch_busy)
        self.ui.task_manager.end_batch_operation.assert_called_once_with(schedule=False)
        self.ui.task_manager.pause_all.assert_not_called()

    def test_late_result_does_not_touch_destroyed_window(self):
        self.ui._exiting = True
        self.ui._set_batch_busy = Mock()
        with patch('downloader.ui.main_window.messagebox.showerror') as error:
            MainWindow._finish_task_action(self.ui, 'late', True)
        self.ui._set_batch_busy.assert_not_called()
        self.ui._flash_status.assert_not_called()
        error.assert_not_called()

    def test_exiting_event_drain_does_not_execute_or_reschedule(self):
        callback = Mock()
        self.ui._ui_events.put((callback, ()))
        self.ui._exiting = True
        self.ui.after = Mock()
        MainWindow._drain_ui_events(self.ui)
        callback.assert_not_called()
        self.ui.after.assert_not_called()


if __name__ == '__main__':
    unittest.main()
