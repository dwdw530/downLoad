"""Real Tk widgets/event loops; dialogs and data belong only to these tests."""
import gc
import hashlib
from pathlib import Path
import threading
import time
import tkinter
import traceback
from types import SimpleNamespace
from unittest.mock import Mock, patch

import customtkinter as ctk

from downloader.ui.main_window import (MainWindow, AddTaskDialog, DeleteTaskDialog, CloseConfirmDialog,
                                      ShutdownConfirmDialog)
from downloader.ui.history_dialog import HistoryDialog
from downloader.ui.settings_dialog import SettingsDialog
from downloader.ui.task_detail_window import TaskDetailWindow
from downloader.ui.clipboard_dialog import ClipboardLinkDialog
from downloader.utils.config import ConfigManager
from downloader.utils.file_utils import format_size, format_remaining
from test_features import FeatureFixture, PAYLOAD


class GuiFeatures(FeatureFixture):
    def setUp(self):
        super().setUp()
        # 反复创建/销毁 Tk 根窗口会留下循环引用。自动 GC 若落在 HTTP 工作线程，
        # Font.__del__ 会等待 Tk 主线程，而同步 wait_task 不泵事件，形成假网络超时。
        # 本测试在主线程显式回收，运行期间仅关闭循环 GC（引用计数仍正常工作）。
        self.gc_was_enabled = gc.isenabled()
        gc.collect()
        gc.disable()
        self.patches = [
            patch('downloader.ui.main_window.TrayManager.start'),
            patch('downloader.ui.main_window.TrayManager.notify_download_complete'),
            patch('tkinter.messagebox.showinfo'),
            patch('tkinter.messagebox.showerror'),
            patch('tkinter.messagebox.askyesno', return_value=True),
        ]
        self.mocks = [item.start() for item in self.patches]
        self.app = MainWindow(self.manager)
        self.app.withdraw()
        self.callback_errors = []
        self.app.report_callback_exception = lambda *args: self.callback_errors.append(
            ''.join(traceback.format_exception(*args)))
        self.pump(0.3)

    def tearDown(self):
        try:
            for timer in self.app.tk.call('after', 'info'):
                self.app.after_cancel(timer)
            self.app._exit_app()
        finally:
            for item in reversed(self.patches):
                item.stop()
            super().tearDown()
            self.app = None
            gc.collect()
            if self.gc_was_enabled:
                gc.enable()
        self.assertEqual(self.callback_errors, [])

    def pump(self, seconds=0.1):
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            self.app.update()
            time.sleep(0.01)

    def wait_tasks(self, count=1, timeout=15):
        """等后台添加线程把任务落库（添加已改为后台执行，不再同步建任务）"""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            tasks = self.db.get_all_tasks()
            if len(tasks) >= count:
                return tasks
            self.pump(0.1)
        return self.db.get_all_tasks()

    def dialog_action(self, action, dialog_type, fill):
        errors = []
        def respond():
            dialog = next(w for w in self.app.winfo_children() if isinstance(w, dialog_type))
            try:
                fill(dialog)
            except BaseException as exc:
                errors.append(exc)
                dialog.destroy()
        self.app.after(250, respond)
        action()
        if errors:
            raise errors[0]
        self.pump()

    def test_destroyed_dialogs_ignore_delayed_titlebar_and_focus_callbacks(self):
        config = ConfigManager(str(self.root / 'dialog-config.json'))
        for factory in (lambda: AddTaskDialog(self.app), lambda: CloseConfirmDialog(self.app, config),
                        lambda: HistoryDialog(self.app, self.db)):
            dialog = factory()
            dialog.destroy()
            dialog._revert_withdraw_after_windows_set_titlebar_color()
            dialog.focus_set()
            dialog.focus()
        self.pump(0.5)

    def test_main_window_keeps_full_resolution_icon_photo(self):
        self.assertEqual(self.app._window_icon_photo.width(), 256)
        self.assertEqual(self.app._window_icon_photo.height(), 256)
        self.pump(0.5)
        self.assertIn(str(self.app._window_icon_photo), self.app.tk.call('image', 'names'))

    def test_add_download_progress_history_and_delete_record(self):
        def fill(dialog):
            dialog.url_text.insert('1.0', self.base_url + '/range')
            dialog.save_entry.insert(0, str(self.root / 'chosen'))
            dialog.hash_entry.insert(0, hashlib.sha256(PAYLOAD).hexdigest())
            dialog.hash_type_var.set('sha256')
            dialog._on_confirm()
        self.dialog_action(self.app._on_add_task, AddTaskDialog, fill)
        task_id = self.wait_tasks()[0]['task_id']
        task = self.wait_task(task_id)
        self.pump()
        self.assertEqual(task['hash_verified'], 1)
        self.assertEqual(Path(task['save_path']).read_bytes(), PAYLOAD)
        widgets = self.app.task_widgets[task_id]
        self.assertEqual(widgets['progress_bar'].get(), 1)
        self.assertEqual(widgets['action_btn'].cget('state'), 'disabled')
        def inspect_history(dialog):
            self.assertEqual(len(self.db.get_history()), 1)
            self.assertTrue(dialog.history_frame.winfo_children())
            self.mocks[2].reset_mock()
            self.mocks[4].reset_mock()
            dialog._on_clear_history()
            self.assertEqual(self.db.get_history(), [])
            self.mocks[4].assert_called_once()
            self.mocks[2].assert_not_called()
            self.assertTrue(any(w.cget('text') == '暂无下载历史记录'
                                for w in dialog.history_frame.winfo_children()))
            dialog.destroy()
        self.dialog_action(self.app._on_history, HistoryDialog, inspect_history)
        self.dialog_action(lambda: self.app._on_delete_task(task_id), DeleteTaskDialog,
                           lambda dialog: dialog._on_confirm())
        self.assertIsNone(self.db.get_task(task_id))
        self.assertNotIn(task_id, self.app.task_widgets)
        self.assertTrue(Path(task['save_path']).is_file())

    def test_cancel_clear_history_does_not_delete_or_refresh(self):
        dialog = HistoryDialog(self.app, self.db)
        self.mocks[4].return_value = False
        with patch.object(self.db, 'clear_history') as clear, patch.object(dialog, '_load_history') as refresh:
            dialog._on_clear_history()
            clear.assert_not_called()
            refresh.assert_not_called()
        self.mocks[2].assert_not_called()
        self.mocks[3].assert_not_called()
        dialog.destroy()

    def test_failed_clear_history_keeps_error_feedback_without_success_popup(self):
        dialog = HistoryDialog(self.app, self.db)
        for error in (None, OSError('database unavailable')):
            with self.subTest(error=error), \
                    patch.object(self.db, 'clear_history', return_value=False, side_effect=error) as clear, \
                    patch.object(dialog, '_load_history') as refresh:
                self.mocks[3].reset_mock()
                dialog._on_clear_history()
                clear.assert_called_once()
                refresh.assert_not_called()
                self.mocks[3].assert_called_once()
                self.mocks[2].assert_not_called()
        dialog.destroy()

    def test_delete_files_also_removes_single_thread_temporary_data(self):
        task_id = self.create('/no-range')
        task = self.db.get_task(task_id)
        output = Path(task['save_path'])
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(b'old output')
        temporary = Path(self.config.temp_dir) / f'{task_id}.tmp'
        temporary.parent.mkdir(parents=True, exist_ok=True)
        temporary.write_bytes(b'partial data')
        self.db.update_task_status(task_id, 'paused')
        self.app._on_task_added(task_id)
        def fill(dialog):
            dialog.delete_file_var.set(True)
            dialog._on_confirm()
        self.dialog_action(lambda: self.app._on_delete_task(task_id), DeleteTaskDialog, fill)
        self.assertFalse(output.exists())
        self.assertFalse(temporary.exists())
        self.assertIsNone(self.db.get_task(task_id))

    def test_settings_save_applies_runtime_values_and_persists(self):
        def fill(dialog):
            dialog.thread_slider.set(6)
            dialog.concurrent_slider.set(4)
            dialog.timeout_entry.delete(0, 'end')
            dialog.timeout_entry.insert(0, '9')
            dialog.speed_limit_entry.delete(0, 'end')
            dialog.speed_limit_entry.insert(0, '128')
            dialog.proxy_enabled_var.set(True)
            dialog._on_proxy_toggle()
            dialog.http_proxy_entry.insert(0, self.base_url)
            dialog._on_save()
        self.dialog_action(self.app._on_settings, SettingsDialog, fill)
        reloaded = ConfigManager(self.config.config_path)
        self.assertEqual(reloaded.thread_count, 6)
        self.assertEqual(self.manager.max_concurrent, 4)
        self.assertEqual(reloaded.timeout, 9)
        self.assertEqual(reloaded.speed_limit, 128 * 1024)
        self.assertEqual(reloaded.proxies['http'], self.base_url)

    def test_settings_save_applies_temp_dir_immediately(self):
        new_dir = self.root / 'new-parts'

        def fill(dialog):
            dialog.temp_entry.delete(0, 'end')
            dialog.temp_entry.insert(0, str(new_dir))
            dialog._on_save()

        self.dialog_action(self.app._on_settings, SettingsDialog, fill)
        # 保存时已直接写回共享的 config 对象，新任务不需要重启就能用新目录
        self.assertEqual(Path(self.config.temp_dir), new_dir)
        self.assertTrue(new_dir.is_dir())
        self.assertEqual(Path(ConfigManager(self.config.config_path).temp_dir), new_dir)

    def test_settings_runtime_sync_retargets_queued_tasks(self):
        task_id = self.create('/range')
        new_dir = self.root / 'runtime-parts'
        self.app._apply_saved_settings({'temp_dir': str(new_dir)})
        self.assertEqual(Path(self.config.temp_dir), new_dir)
        for chunk in self.db.get_chunks(task_id):
            self.assertEqual(Path(chunk['temp_file']).parent, new_dir)

    def test_settings_rejects_zero_timeout(self):
        dialog = SettingsDialog(self.app, self.config, on_save_callback=self.app._on_settings_saved)
        self.pump(0.25)
        dialog.timeout_entry.delete(0, 'end')
        dialog.timeout_entry.insert(0, '0')
        dialog._on_save()
        self.assertGreater(self.config.timeout, 0)
        self.assertTrue(dialog.winfo_exists())
        dialog.destroy()

    def test_settings_does_not_report_success_when_file_write_fails(self):
        callback = Mock()
        dialog = SettingsDialog(self.app, self.config, on_save_callback=callback)
        self.pump(0.25)
        self.mocks[2].reset_mock()
        before = self.config.get_all()
        dialog.thread_slider.set(12)
        with patch.object(self.config, 'save', return_value=False):
            dialog._on_save()
        self.assertEqual(self.config.get_all(), before)
        callback.assert_not_called()
        self.mocks[2].assert_not_called()
        self.assertTrue(dialog.winfo_exists())
        dialog.destroy()

    def test_reset_settings_updates_runtime_concurrency(self):
        self.config.max_concurrent_downloads = 1
        self.manager.set_max_concurrent(1)
        dialog = SettingsDialog(self.app, self.config, on_save_callback=self.app._on_settings_saved)
        self.pump(0.25)
        dialog._on_reset()
        self.pump()
        self.assertEqual(self.manager.max_concurrent, ConfigManager.DEFAULT_CONFIG['max_concurrent_downloads'])
        dialog.destroy()

    def test_close_dialog_remembers_minimize_choice(self):
        def fill(dialog):
            dialog.action_var.set('minimize')
            dialog.remember_var.set(True)
            dialog._on_confirm()
        self.dialog_action(self.app._on_window_close, CloseConfirmDialog, fill)
        self.assertEqual(ConfigManager(self.config.config_path).get('close_behavior'), 'minimize')
        self.assertEqual(self.app.state(), 'withdrawn')

    def test_tray_exit_is_queued_for_the_tk_thread(self):
        fake = SimpleNamespace(_ui_events=self.app._ui_events, _show_from_tray=Mock(), _exit_app=Mock())
        with patch('downloader.ui.main_window.TrayManager') as tray_class:
            MainWindow._init_tray(fake)
            callback = tray_class.return_value.set_exit_callback.call_args.args[0]
        worker = threading.Thread(target=callback)
        worker.start()
        worker.join(0.5)
        self.assertFalse(worker.is_alive())
        fake._exit_app.assert_not_called()
        self.pump()
        fake._exit_app.assert_called_once()

    def test_pause_clears_displayed_speed(self):
        task_id = self.create()
        self.app._on_task_added(task_id)
        self.app._on_task_progress(task_id, 8192, len(PAYLOAD), 4096)
        self.pump()
        self.app._on_pause_task(task_id)
        self.pump()
        self.assertEqual(self.db.get_task(task_id)['status'], 'paused')
        self.assertEqual(self.app.task_widgets[task_id]['speed_label'].cget('text'), '0 B/s')

    def test_settings_persist_after_download_actions(self):
        def fill(dialog):
            dialog.after_open_file_var.set(True)
            dialog.after_shutdown_var.set(True)
            dialog._on_save()
        self.dialog_action(self.app._on_settings, SettingsDialog, fill)
        reloaded = ConfigManager(self.config.config_path)
        self.assertEqual(reloaded.after_download,
                         {'open_file': True, 'open_folder': False, 'shutdown': True})

    def test_completion_actions_stay_off_until_enabled(self):
        # 不需要真实下载：动作开关关闭时，完成回调就应该什么都不做
        task_id = self.create('/tiny')
        self.app._on_task_added(task_id)
        self.pump()
        self.app._on_task_status_changed(task_id, 'completed', '')
        self.pump(0.3)
        self.assertEqual(self.config.after_download,
                         {'open_file': False, 'open_folder': False, 'shutdown': False})
        with patch('downloader.ui.main_window.os.startfile') as startfile, \
                patch('downloader.ui.main_window.subprocess.Popen') as popen:
            self.app._on_task_status_changed(task_id, 'completed', '')
            self.pump(0.3)
        startfile.assert_not_called()
        popen.assert_not_called()

    def test_completion_actions_open_file_and_folder_when_enabled(self):
        task_id = self.create('/range')
        self.assertTrue(self.manager.start_task(task_id))
        task = self.wait_task(task_id, timeout=20)
        self.pump(0.3)  # 先把真实完成事件排空，确保下面统计的只是本次触发
        self.config.after_download = {'open_file': True, 'open_folder': True}
        with patch('downloader.ui.main_window.os.startfile') as startfile, \
                patch('downloader.ui.main_window.subprocess.Popen') as popen:
            self.app._on_task_status_changed(task_id, 'completed', '')
            self.pump(0.3)
        startfile.assert_called_once_with(task['save_path'])
        self.assertEqual(popen.call_args.args[0][0], 'explorer')

    def test_verified_failure_does_not_trigger_completion_actions(self):
        task_id = self.create('/tiny')
        self.app._on_task_added(task_id)
        self.pump()
        self.config.after_download = {'open_file': True, 'open_folder': True, 'shutdown': True}
        with patch('downloader.ui.main_window.os.startfile') as startfile, \
                patch('downloader.ui.main_window.subprocess.Popen') as popen:
            self.app._on_task_status_changed(task_id, 'verify_failed', 'hash mismatch')
            self.pump(0.3)
        startfile.assert_not_called()
        popen.assert_not_called()

    def test_shutdown_waits_for_the_last_active_task(self):
        task_id = self.create('/tiny')
        self.app._on_task_added(task_id)
        self.pump()
        self.config.after_download = {'shutdown': True}
        with patch.object(self.manager, 'get_all_tasks', return_value=[{'status': 'downloading'}]), \
                patch('downloader.ui.main_window.ShutdownConfirmDialog') as dialog_class, \
                patch.object(self.app, 'wait_window') as wait_window:
            self.app._handle_download_completed(task_id)
        dialog_class.assert_not_called()
        wait_window.assert_not_called()

        with patch.object(self.manager, 'get_all_tasks', return_value=[{'status': 'completed'}]), \
                patch('downloader.ui.main_window.ShutdownConfirmDialog') as dialog_class, \
                patch.object(self.app, 'wait_window') as wait_window:
            self.app._handle_download_completed(task_id)
        dialog_class.assert_called_once()
        wait_window.assert_called_once()

    def test_shutdown_dialog_runs_callback_only_after_countdown(self):
        calls = []
        dialog = ShutdownConfirmDialog(self.app, on_shutdown=lambda: calls.append('shutdown'), countdown=0)
        self.pump(0.3)
        self.assertEqual(calls, ['shutdown'])
        self.assertFalse(dialog.winfo_exists())

    def test_shutdown_dialog_closing_cancels_shutdown(self):
        calls = []
        dialog = ShutdownConfirmDialog(self.app, on_shutdown=lambda: calls.append('shutdown'), countdown=1)
        self.pump(0.1)
        dialog.destroy()
        self.pump(1.5)
        self.assertEqual(calls, [])

    def test_task_card_shows_size_and_remaining_time(self):
        task_id = self.create('/tiny')
        self.app._on_task_added(task_id)
        self.pump()
        total = len(PAYLOAD)
        self.app._on_task_status_changed(task_id, 'downloading', '')
        self.app._on_task_progress(task_id, 8192, total, 4096)
        self.pump()
        widgets = self.app.task_widgets[task_id]
        self.assertEqual(widgets['size_label'].cget('text'),
                         f"已下载 {format_size(8192)} / {format_size(total)}")
        self.assertEqual(widgets['eta_label'].cget('text'), '剩余 00:06')
        self.app._on_task_status_changed(task_id, 'paused', '')
        self.pump()
        self.assertEqual(widgets['eta_label'].cget('text'), '剩余 --')
        self.assertEqual(widgets['speed_label'].cget('text'), '0 B/s')

    def test_add_dialog_switches_hash_state_with_url_lines(self):
        dialog = AddTaskDialog(self.app)
        self.pump(0.25)
        self.assertEqual(dialog.hash_entry.cget('state'), 'normal')
        dialog.url_text.insert('1.0', 'https://example.invalid/1\nhttps://example.invalid/2')
        dialog._on_url_changed()
        self.assertEqual(dialog.hash_entry.cget('state'), 'disabled')
        self.assertEqual(dialog.hash_type_menu.cget('state'), 'disabled')
        self.assertTrue(dialog.batch_hint_label.cget('text'))
        dialog.url_text.delete('1.0', 'end')
        dialog.url_text.insert('1.0', 'https://example.invalid/1')
        dialog._on_url_changed()
        self.assertEqual(dialog.hash_entry.cget('state'), 'normal')
        self.assertEqual(dialog.batch_hint_label.cget('text'), '')
        dialog.destroy()

    def test_add_dialog_enter_in_textbox_keeps_dialog_open(self):
        dialog = AddTaskDialog(self.app)
        self.pump(0.25)
        inner_text = next(w for w in dialog.url_text.winfo_children() if w.winfo_class() == 'Text')
        self.assertIsNone(dialog._on_return(SimpleNamespace(widget=inner_text)))
        self.assertTrue(dialog.winfo_exists())
        self.assertFalse(dialog.confirmed)
        # 多行框以外的控件回车才提交
        dialog.url_text.insert('1.0', self.base_url + '/range')
        dialog._on_return(SimpleNamespace(widget=dialog.save_entry))
        self.assertFalse(dialog.winfo_exists())
        self.assertTrue(dialog.confirmed)

    def test_start_later_creates_paused_task_without_queue_scheduling(self):
        def fill(dialog):
            dialog.url_text.insert('1.0', self.base_url + '/range')
            dialog._on_confirm(True)
        with patch.object(self.manager, '_try_start_next_task') as schedule:
            self.dialog_action(self.app._on_add_task, AddTaskDialog, fill)
            self.pump(0.3)
            tasks = self.wait_tasks()
        schedule.assert_not_called()
        task = tasks[0]
        self.assertEqual(task['status'], 'paused')
        self.assertEqual(self.app.task_widgets[task['task_id']]['action_btn'].cget('text'), '▶ 继续')

    def test_start_now_creates_pending_task_and_starts_queue(self):
        def fill(dialog):
            dialog.url_text.insert('1.0', self.base_url + '/range')
            dialog._on_confirm(False)
        self.dialog_action(self.app._on_add_task, AddTaskDialog, fill)
        task = self.wait_tasks()[0]
        self.assertIn(task['status'], ('pending', 'downloading', 'verifying', 'completed'))
        self.assertNotEqual(task['status'], 'paused')

    def test_batch_add_creates_tasks_and_reports_failures(self):
        def fill(dialog):
            dialog.url_text.insert(
                '1.0', f"{self.base_url}/range\n{self.base_url}/tiny\n{self.base_url}/not-found")
            dialog._on_confirm()
        # 本用例验证批量创建/汇总；真实下载由核心回归覆盖，避免探测与下载争用本地服务。
        with patch.object(self.manager, '_try_start_next_task'):
            self.dialog_action(self.app._on_add_task, AddTaskDialog, fill)
            deadline = time.monotonic() + 10
            summaries = []
            while time.monotonic() < deadline and not summaries:
                self.pump(0.2)
                summaries = [call.args for call in self.mocks[2].call_args_list
                             if call.args and call.args[0] == '添加结果']
        self.assertEqual(len(summaries), 1, '后台批量添加未返回结果汇总')
        self.assertEqual({task['filename'] for task in self.db.get_all_tasks()}, {'range', 'tiny'})
        self.assertIn('成功添加 2 个任务', summaries[0][1])
        self.assertIn('失败 1 个', summaries[0][1])

    def test_batch_add_skips_duplicate_urls(self):
        def fill(dialog):
            dialog.url_text.insert('1.0', f"{self.base_url}/range\n{self.base_url}/range")
            dialog._on_confirm()
        self.dialog_action(self.app._on_add_task, AddTaskDialog, fill)
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline and self.mocks[2].call_args is None:
            self.pump(0.1)
        self.assertEqual(len(self.db.get_all_tasks()), 1)
        self.assertIn('已跳过 1 条重复链接', self.mocks[2].call_args.args[1])

    def test_search_and_hide_completed_filter_tasks(self):
        # 只走状态回调，不真跑下载：过滤只看 task_widgets 里缓存的 status
        completed = self.create('/tiny', filename='alpha.bin')
        pending = self.create('/tiny', filename='beta.bin')
        self.app._on_task_added(completed)
        self.app._on_task_added(pending)
        self.pump(0.3)
        self.app._on_task_status_changed(completed, 'completed', '')
        self.pump(0.3)
        self.assertEqual(len(self.app.task_widgets), 2)

        self.app.hide_completed_var.set(True)
        self.app._on_hide_completed_toggle()
        self.assertFalse(self.app.task_widgets[completed]['frame'].winfo_manager())
        self.assertTrue(self.app.task_widgets[pending]['frame'].winfo_manager())
        self.assertEqual(self.app.filter_hint_label.cget('text'), '显示 1 / 2')

        self.app.search_entry.insert(0, 'zzz')
        self.app._apply_task_filter()
        self.assertTrue(self.app.empty_label.winfo_manager())
        self.assertEqual(self.app.empty_label.cget('text'), '没有匹配的任务')

        self.app.search_entry.delete(0, 'end')
        self.app.search_entry.insert(0, 'beta')
        self.app._apply_task_filter()
        self.assertFalse(self.app.empty_label.winfo_manager())
        self.assertTrue(self.app.task_widgets[pending]['frame'].winfo_manager())

        self.assertTrue(ConfigManager(self.config.config_path).ui_hide_completed)

    # ---------- 左侧分类树 ----------

    def _inject_task(self, task_id, filename, url, status='pending'):
        """直建库记录 + UI 卡片，不真跑下载（分类只看卡片缓存）"""
        self.db.create_task_with_chunks(task_id, url, filename,
                                        str(self.root / filename), status=status)
        self.app._add_task_widget(self.db.get_task(task_id))
        return task_id

    def test_type_category_filters_tasks_by_extension(self):
        video = self._inject_task('t-video', 'movie.mp4', 'http://cdn.example.com/movie.mp4')
        document = self._inject_task('t-doc', 'paper.pdf', 'http://files.example.org/paper.pdf')
        self.assertEqual(self.app.task_widgets[video]['category'], 'video')

        self.app._on_category_select('type:video')
        self.pump(0.1)
        self.assertTrue(self.app.task_widgets[video]['frame'].winfo_manager())
        self.assertFalse(self.app.task_widgets[document]['frame'].winfo_manager())
        self.assertEqual(self.app.filter_hint_label.cget('text'), '显示 1 / 2')

        self.app._on_category_select('type:other')
        self.pump(0.1)
        self.assertTrue(self.app.empty_label.winfo_manager())
        self.assertEqual(self.app.empty_label.cget('text'), '没有匹配的任务')

    def test_status_category_counts_and_persistence(self):
        self._inject_task('t-run', 'a.bin', 'http://a.com/a.bin', status='downloading')
        self._inject_task('t-done', 'b.bin', 'http://a.com/b.bin', status='completed')
        self.assertIn('(2)', self.app.category_buttons['all'].cget('text'))
        self.assertIn('(1)', self.app.category_buttons['status:downloading'].cget('text'))
        self.assertIn('(1)', self.app.category_buttons['status:completed'].cget('text'))

        self.app._on_category_select('status:completed')
        self.pump(0.1)
        self.assertTrue(self.app.task_widgets['t-done']['frame'].winfo_manager())
        self.assertFalse(self.app.task_widgets['t-run']['frame'].winfo_manager())

        # “隐藏已完成”与分类叠加时唯一命中项也被隐掉，应显示空态
        self.app.hide_completed_var.set(True)
        self.app._on_hide_completed_toggle()
        self.assertTrue(self.app.empty_label.winfo_manager())

        self.assertEqual(ConfigManager(self.config.config_path).ui_active_category, 'status:completed')

    def test_host_category_tracks_url_host_and_falls_back(self):
        self._inject_task('t-host', 'a.bin', 'https://dl.example.com/a.bin')
        self.assertIn('host:dl.example.com', self.app.category_buttons)

        self.app._on_category_select('host:dl.example.com')
        self.pump(0.1)
        self.assertEqual(self.app.filter_hint_label.cget('text'), '显示 1 / 1')

        # 任务删除后站点分组消失，选中项回退到「全部」
        self.app.task_widgets['t-host']['frame'].destroy()
        del self.app.task_widgets['t-host']
        self.db.delete_task('t-host')
        self.app._apply_task_filter()
        self.app._refresh_sidebar()
        self.assertEqual(self.app._active_category, 'all')
        self.assertNotIn('host:dl.example.com', self.app.category_buttons)

    # ---------- 右键菜单与手动排序 ----------

    def _menu_command_index(self, menu, label):
        for index in range(menu.index('end') + 1):
            if menu.type(index) == 'command' and menu.entrycget(index, 'label') == label:
                return index
        raise AssertionError(f'菜单项不存在: {label}')

    def test_context_menu_lists_actions_and_sorts_tasks(self):
        self._inject_task('t1', 'a.bin', 'http://a.com/a.bin')
        self._inject_task('t2', 'b.bin', 'http://a.com/b.bin')
        self.assertEqual(list(self.app.task_widgets), ['t1', 't2'])

        menu = self.app._build_task_menu('t2')
        try:
            for label in ('▶ 开始下载', '📂 打开文件', '📁 打开所在文件夹', '🔗 复制下载链接',
                          '⬆ 上移', '⏫ 置顶', '🗑 删除任务'):
                self._menu_command_index(menu, label)
            menu.invoke(self._menu_command_index(menu, '⬆ 上移'))
        finally:
            menu.destroy()
        self.pump(0.2)

        self.assertEqual(list(self.app.task_widgets), ['t2', 't1'])
        self.assertEqual([task['task_id'] for task in self.db.get_all_tasks()], ['t2', 't1'])
        self.assertEqual(self.app.task_widgets['t1']['frame'].winfo_manager(), 'pack')

        # 已经在最前的任务不能再上移/置顶
        first_menu = self.app._build_task_menu('t2')
        try:
            self.assertEqual(first_menu.entrycget(self._menu_command_index(first_menu, '⬆ 上移'), 'state'),
                             'disabled')
            self.assertEqual(first_menu.entrycget(self._menu_command_index(first_menu, '⏫ 置顶'), 'state'),
                             'disabled')
        finally:
            first_menu.destroy()

    def test_context_menu_copy_link_does_not_trigger_clipboard_popup(self):
        self._inject_task('t1', 'a.bin', 'http://a.com/a.bin')
        menu = self.app._build_task_menu('t1')
        try:
            menu.invoke(self._menu_command_index(menu, '🔗 复制下载链接'))
        finally:
            menu.destroy()
        self.assertEqual(self.app.clipboard_get(), 'http://a.com/a.bin')
        self.assertEqual(self.app._clipboard_last_seen, 'http://a.com/a.bin')

    def test_context_menu_disables_start_on_completed_task(self):
        self._inject_task('t1', 'a.bin', 'http://a.com/a.bin', status='completed')
        menu = self.app._build_task_menu('t1')
        try:
            self.assertEqual(menu.entrycget(self._menu_command_index(menu, '▶ 开始下载'), 'state'),
                             'disabled')
        finally:
            menu.destroy()

    # ---------- 主题切换 ----------

    def test_settings_dialog_switches_theme_and_persists(self):
        try:
            def fill(dialog):
                dialog.appearance_segment.set('浅色')
                dialog._on_save()
            self.dialog_action(self.app._on_settings, SettingsDialog, fill)
            self.pump(0.3)
            self.assertEqual(ConfigManager(self.config.config_path).ui_appearance_mode, 'light')
            self.assertEqual(ctk.get_appearance_mode(), 'Light')
        finally:
            ctk.set_appearance_mode('dark')

    def test_settings_dialog_accepts_follow_system_theme(self):
        def fill(dialog):
            dialog.appearance_segment.set('跟随系统')
            dialog._on_save()
        self.dialog_action(self.app._on_settings, SettingsDialog, fill)
        self.pump(0.3)
        self.assertEqual(ConfigManager(self.config.config_path).ui_appearance_mode, 'system')

    def test_settings_dialog_theme_survives_reset(self):
        self.config.ui_appearance_mode = 'light'
        self.assertTrue(self.config.save())
        try:
            dialog = SettingsDialog(self.app, self.config, on_save_callback=self.app._on_settings_saved)
            self.pump(0.25)
            self.assertEqual(dialog.appearance_segment.get(), '浅色')
            dialog._on_reset()
            self.pump(0.25)
            self.assertEqual(ConfigManager(self.config.config_path).ui_appearance_mode, 'dark')
            self.assertEqual(ctk.get_appearance_mode(), 'Dark')
            dialog.destroy()
        finally:
            ctk.set_appearance_mode('dark')


class TaskDetailWindowTests(GuiFeatures):
    """双击卡片打开详情窗：速度曲线数据流与生命周期"""

    def _add_card(self, filename='detail.bin'):
        task_id = self.create('/tiny', filename=filename)
        self.app._on_task_added(task_id)
        self.pump(0.3)
        return task_id

    def _frame_canvas(self, frame):
        # CTkFrame 的直接子级里可能没有裸 Canvas，递归找第一个 CTkCanvas
        stack = [frame]
        while stack:
            widget = stack.pop(0)
            for child in widget.winfo_children():
                if child.winfo_class() == 'Canvas':
                    return child
                stack.append(child)
        raise AssertionError('task card has no canvas widget')

    def test_double_click_on_card_opens_detail_window(self):
        task_id = self._add_card()
        # 卡片新增了勾选框，其内部 canvas 不参与双击；直接验证文件名区域的绑定
        label = self.app.task_widgets[task_id]['filename_label']
        canvas = self._frame_canvas(label)
        # Tk 的 bind 脚本里拿不到可调用对象，改从绑定表反查：双击已挂即可，
        # 真正的打开逻辑由 test_second_open 等用例直接调 _on_open_detail 覆盖
        script = canvas.bind('<Double-Button-1>')
        self.assertTrue(script)
        # 从脚本里提取 tcl 回调名并直接调用（lambda 只收 event 一个参数）
        import re
        match = re.search(r'\[(\d+[^\s%]*)\s', script)
        self.assertIsNotNone(match, f'unexpected bind script: {script!r}')
        self.app.tk.eval(f'{match.group(1)} {{}}')
        self.pump(0.3)
        window = self.app._detail_windows[task_id]
        self.assertTrue(window.winfo_exists())
        self.assertIn('detail.bin', window.title())

    def test_action_button_stays_unbound(self):
        task_id = self._add_card()
        action_btn = self.app.task_widgets[task_id]['action_btn']
        button_canvas = self._frame_canvas(action_btn)
        self.assertFalse(button_canvas.bind('<Double-Button-1>'))

    def test_second_open_reuses_existing_window(self):
        task_id = self._add_card()
        self.app._on_open_detail(task_id)
        self.pump(0.2)
        first = self.app._detail_windows[task_id]
        self.app._on_open_detail(task_id)
        self.pump(0.2)
        self.assertIs(self.app._detail_windows[task_id], first)

    def test_progress_feeds_history_and_detail_window(self):
        task_id = self._add_card()
        with patch('downloader.ui.main_window.time.monotonic', return_value=100):
            self.app._update_task_status(task_id, 'downloading')
            self.app._update_task_progress(task_id, 100, 1000, 2048)
        with patch('downloader.ui.main_window.time.monotonic', return_value=101):
            self.app._update_task_progress(task_id, 200, 1000, 4096)
        self.assertEqual(list(self.app._speed_history[task_id]), [(100, 2048), (101, 4096)])
        # 详情窗未打开时不推送，打开后首屏取最新值，下一条进度事件再同步
        self.assertNotIn(task_id, self.app._detail_windows)
        self.app._on_open_detail(task_id)
        self.pump(0.2)
        window = self.app._detail_windows[task_id]
        self.assertIn('下载中', window.stats_label.cget('text'))
        self.app._on_task_progress(task_id, 300, 1000, 8192)
        self.pump(0.3)
        self.assertIn('8.00 KB/s', window.stats_label.cget('text'))

    def test_backfill_progress_without_downloading_status_not_recorded(self):
        # 启动回填走 _update_task_progress，但状态不是 downloading，不能污染曲线
        task_id = self._add_card()
        self.app._on_task_progress(task_id, 100, 1000, 2048)
        self.pump(0.3)
        self.assertNotIn(task_id, self.app._speed_history)

    def test_status_change_syncs_to_detail_window(self):
        task_id = self._add_card()
        self.app._on_task_status_changed(task_id, 'downloading', '')
        self.pump(0.2)
        self.app._on_open_detail(task_id)
        self.pump(0.2)
        window = self.app._detail_windows[task_id]
        self.assertIn('下载中', window.stats_label.cget('text'))
        self.app._on_task_status_changed(task_id, 'paused', '')
        self.pump(0.2)
        self.assertIn('已暂停', window.stats_label.cget('text'))
        self.assertIn('剩余 --', window.stats_label.cget('text'))

    def test_delete_task_closes_detail_window_and_clears_history(self):
        task_id = self._add_card()
        self.app._on_task_status_changed(task_id, 'downloading', '')
        self.app._on_task_progress(task_id, 100, 1000, 2048)
        self.pump(0.2)
        self.app._on_open_detail(task_id)
        self.pump(0.2)
        self.assertTrue(self.app._speed_history[task_id])
        self.dialog_action(lambda: self.app._on_delete_task(task_id), DeleteTaskDialog,
                           lambda dialog: dialog._on_confirm())
        self.pump(0.3)
        self.assertNotIn(task_id, self.app._speed_history)
        self.assertNotIn(task_id, self.app._detail_windows)
        self.assertFalse(self.app._detail_windows.get(task_id) and True)


class ClipboardMonitorTests(GuiFeatures):
    """剪贴板监视：触发条件、浮窗交互与忽略站点"""

    def setUp(self):
        super().setUp()
        # GuiFeatures.setUp 里主窗口是 withdraw 的，弹窗条件要求窗口可见；
        # 用 deiconify 并等一拍，避免影响其它用例对 withdrawn 状态的断言
        self.app.deiconify()
        self.pump(0.3)
        self.app._clipboard_last_seen = None

    def _check(self, text):
        self.app.clipboard_get = Mock(return_value=text)
        self.app._check_clipboard_once()

    def _popups(self):
        return [w for w in self.app.winfo_children() if isinstance(w, ClipboardLinkDialog)]

    def test_new_url_pops_dialog(self):
        self._check('https://example.com/file.zip')
        self.pump(0.2)
        popups = self._popups()
        self.assertEqual(len(popups), 1)
        self.assertIn('https://example.com/file.zip', popups[0].url_label.cget('text'))

    def test_same_content_does_not_popup_twice(self):
        self._check('https://example.com/file.zip')
        self.pump(0.2)
        self._check('https://example.com/file.zip')
        self.pump(0.2)
        self.assertEqual(len(self._popups()), 1)

    def test_non_url_text_never_popups(self):
        self._check('just some plain text')
        self.pump(0.2)
        self.assertEqual(len(self._popups()), 0)

    def test_disabled_monitor_never_popups(self):
        self.config.clipboard_monitor_enabled = False
        self._check('https://example.com/file.zip')
        self.pump(0.2)
        self.assertEqual(len(self._popups()), 0)

    def test_ignored_host_never_popups(self):
        self.config.clipboard_ignore_hosts = ['example.com']
        self._check('https://dl.example.com/file.zip')
        self.pump(0.2)
        self.assertEqual(len(self._popups()), 0)

    def test_modal_grab_suppresses_popup(self):
        blocker = tkinter.Toplevel(self.app)
        blocker.grab_set()
        self.pump(0.1)
        self._check('https://example.com/file.zip')
        self.pump(0.2)
        self.assertEqual(len(self._popups()), 0)
        blocker.grab_release()
        blocker.destroy()

    def test_hidden_window_suppresses_popup(self):
        self.app.withdraw()
        self.pump(0.1)
        self._check('https://example.com/file.zip')
        self.pump(0.2)
        self.assertEqual(len(self._popups()), 0)
        self.app.deiconify()
        self.pump(0.1)
    def test_popup_updates_url_in_place(self):
        self._check('https://example.com/1.zip')
        self.pump(0.2)
        self._check('https://example.com/2.zip')
        self.pump(0.2)
        popups = self._popups()
        self.assertEqual(len(popups), 1)
        self.assertIn('2.zip', popups[0].url_label.cget('text'))

    def test_ignore_site_button_persists_host(self):
        self._check('https://dl.example.com/file.zip')
        self.pump(0.2)
        popup = self._popups()[0]
        popup._on_ignore_click()
        self.pump(0.2)
        self.assertFalse(popup.winfo_exists())
        # 忽略的是完整主机名，不吞掉无关子域（匹配靠后缀规则）
        self.assertIn('dl.example.com', self.config.clipboard_ignore_hosts)
        # 配置文件也要落地，重启后忽略列表仍在
        reloaded = ConfigManager(self.config.config_path)
        self.assertIn('dl.example.com', reloaded.clipboard_ignore_hosts)

    def test_ignore_site_reuses_same_config_file_as_app(self):
        # 忽略站点必须写进应用正在用的那份配置，不然设置界面看不到
        self._check('https://dl.example.com/file.zip')
        self.pump(0.2)
        self._popups()[0]._on_ignore_click()
        self.pump(0.2)
        self.assertEqual(self.config.clipboard_ignore_hosts,
                         ConfigManager(self.config.config_path).clipboard_ignore_hosts)

    def test_add_button_invokes_callback_with_current_url(self):
        callback = Mock()
        popup = ClipboardLinkDialog(self.app, 'https://example.com/file.zip',
                                    on_add=callback, on_ignore_site=Mock())
        self.pump(0.2)
        popup._on_add_click()
        self.pump(0.2)
        callback.assert_called_once_with('https://example.com/file.zip')
        self.assertFalse(popup.winfo_exists())

    def test_clipboard_add_flow_creates_task_via_prefilled_dialog(self):
        url = self.base_url + '/range'
        self._check(url)
        self.pump(0.2)
        popup = self._popups()[0]

        def respond():
            dialog = next(w for w in self.app.winfo_children() if isinstance(w, AddTaskDialog))
            dialog._on_confirm(False)

        self.app.after(250, respond)
        popup._on_add_click()
        self.pump(0.5)
        tasks = self.db.get_all_tasks()
        self.assertEqual(len(tasks), 1)
        self.assertEqual(tasks[0]['url'], url)

    def test_add_dialog_prefills_initial_url(self):
        dialog = AddTaskDialog(self.app, initial_url='https://example.com/prefill.zip')
        self.pump(0.25)
        self.assertEqual(dialog.parse_urls(), ['https://example.com/prefill.zip'])
        dialog.destroy()



class TransientUiTests(GuiFeatures):
    """桌面悬浮条与临时提示：显示/隐藏、拖动落盘、设置页开关、重试提示"""

    def test_toggle_shows_and_hides_float_bar(self):
        self.assertIsNone(self.app.float_bar)
        self.app._set_float_bar_visible(True)
        self.pump(0.2)
        self.assertIsNotNone(self.app.float_bar)
        self.assertTrue(self.app.float_bar.window.winfo_exists())
        self.app._set_float_bar_visible(False)
        self.pump(0.2)
        # 隐藏保留窗口对象，再次显示不用重建
        self.assertFalse(self.app.float_bar._visible)
        self.app._set_float_bar_visible(True)
        self.pump(0.2)
        self.assertTrue(self.app.float_bar._visible)

    def test_drag_updates_position_and_persists(self):
        self.app._set_float_bar_visible(True)
        self.pump(0.2)
        bar = self.app.float_bar
        start_x, start_y = bar._pos
        bar._press = (100, 100, start_x, start_y)
        bar._on_motion(SimpleNamespace(x_root=160, y_root=180))
        bar._on_release(SimpleNamespace(x_root=160, y_root=180))
        self.pump(0.2)
        # 松手后贴边/夹屏幕，位置一定在虚拟桌面内且已写回配置
        expected = bar.config.get('ui_float_bar_pos')
        self.assertEqual(len(expected), 2)
        self.assertEqual(bar._pos, (expected[0], expected[1]))
        left, top, right, bottom = bar._bounds()
        self.assertTrue(left <= bar._pos[0] <= right - bar._width)
        self.assertTrue(top <= bar._pos[1] <= bottom - bar._height)
        reloaded = ConfigManager(self.config.config_path)
        self.assertEqual(reloaded.ui_float_bar_pos, expected)

    def test_float_bar_uses_layered_alpha_rendering(self):
        """图形走分层窗口贴图：真 alpha，不能再退回 Canvas 多边形/色键（那会出毛边）"""
        self.app._set_float_bar_visible(True)
        self.pump(0.3)
        bar = self.app.float_bar
        self.assertIsNotNone(bar._image)
        self.assertEqual(bar._image.size, (bar._width, bar._height))
        alphas = {pixel[3] for pixel in bar._image.getdata()}
        self.assertIn(0, alphas)
        self.assertIn(255, alphas)
        self.assertGreater(len(alphas), 8)
        # 旧实现的痕迹：不再有色键窗口属性
        self.assertFalse(hasattr(bar, 'canvas'))
        bar._hovered = True
        bar._draw()
        self.pump(0.1)
        self.assertIsNotNone(bar._image)

    def test_float_bar_menu_font_matches_previous_style(self):
        """右键菜单字号/菜单项不能顺手改：全部文字放大过的坑已经有过了"""
        self.app._set_float_bar_visible(True)
        self.pump(0.3)
        bar = self.app.float_bar
        menu = bar._menu
        # 原生 Tk 字号：负值才是像素，正值是磅（写错会让菜单字变大）
        raw_size = int(str(menu.cget('font')).split()[-1])
        self.assertLess(raw_size, 0, '菜单字号必须用像素负值')
        self.assertEqual(raw_size, -max(10, round(12 * bar._scale)))

        labels = ['---' if menu.type(index) == 'separator' else menu.entrycget(index, 'label')
                  for index in range(menu.index('end') + 1)]
        self.assertEqual(labels, ['显示主窗口', '---', '迷你模式', '---', '暂停全部',
                                  '继续全部', '---', '设置…', '隐藏悬浮条', '---', '退出程序'])

    def test_hide_by_user_turns_off_setting(self):
        self.app._set_float_bar_visible(True)
        self.pump(0.2)
        self.app.float_bar.hide_by_user()
        self.pump(0.2)
        self.assertFalse(self.config.ui_float_bar_enabled)
        self.assertFalse(ConfigManager(self.config.config_path).ui_float_bar_enabled)
        self.assertFalse(self.app.float_bar._visible)

    def test_settings_saves_float_bar_flag_and_applies_immediately(self):
        def fill(dialog):
            dialog.float_bar_var.set(True)
            dialog._on_save()

        self.dialog_action(self.app._on_settings, SettingsDialog, fill)
        self.assertTrue(ConfigManager(self.config.config_path).ui_float_bar_enabled)
        self.pump(0.3)
        self.assertIsNotNone(self.app.float_bar)
        self.assertTrue(self.app.float_bar._visible)

    def test_settings_save_closes_without_popup_and_flashes_status(self):
        """保存设置不再弹"已保存"：直接关框，状态栏给一条不拦截的提示"""
        showinfo = self.mocks[2]

        self.dialog_action(self.app._on_settings, SettingsDialog, lambda dialog: dialog._on_save())
        self.pump(0.2)
        showinfo.assert_not_called()
        self.assertIsNotNone(self.app._status_flash)
        self.assertIn('设置已保存', self.app.status_label.cget('text'))
        # 闪一下就自己恢复，不残留提示
        self.app._flash_status('设置已保存', seconds=0.05)
        self.pump(1.3)
        self.assertIsNone(self.app._status_flash)

    def test_float_bar_mini_mode_shrinks_and_persists(self):
        self.app._set_float_bar_visible(True)
        self.pump(0.2)
        bar = self.app.float_bar
        full_size = (bar._width, bar._height)

        bar.set_mode('mini')
        self.pump(0.2)
        self.assertEqual(bar._mode, 'mini')
        self.assertLess(bar._width, full_size[0])
        self.assertLess(bar._height, full_size[1])
        self.assertEqual(ConfigManager(self.config.config_path).ui_float_bar_mode, 'mini')

        bar.set_mode('normal')
        self.pump(0.2)
        self.assertEqual((bar._width, bar._height), full_size)

    def test_settings_saves_float_bar_mode_and_applies_immediately(self):
        def fill(dialog):
            dialog.float_bar_var.set(True)
            dialog.float_bar_mode_segment.set('迷你')
            dialog._on_save()

        self.dialog_action(self.app._on_settings, SettingsDialog, fill)
        self.assertEqual(ConfigManager(self.config.config_path).ui_float_bar_mode, 'mini')
        self.pump(0.3)
        self.assertIsNotNone(self.app.float_bar)
        self.assertEqual(self.app.float_bar._mode, 'mini')

    # ==================== 失败分块重试的临时提示 ====================

    def _add_card(self, filename):
        task_id = self.create('/tiny', filename=filename)
        self.app._on_task_added(task_id)
        self.pump(0.3)
        return task_id

    def test_retry_notice_shows_then_clears_when_bytes_flow(self):
        task_id = self._add_card('retry.bin')
        label = self.app.task_widgets[task_id]['status_label']
        self.app._update_task_status(task_id, 'downloading', '正在重试 2 个分块（第 1 轮）')
        self.assertIn('正在重试 2 个分块', label.cget('text'))
        # 数据恢复流动后提示自动收回，不会一直挂在卡片上
        self.app._update_task_progress(task_id, 10, 100, 2048)
        self.assertEqual('下载中', label.cget('text'))

    def test_ordinary_status_message_is_not_shown(self):
        task_id = self._add_card('plain.bin')
        self.app._update_task_status(task_id, 'downloading', '开始下载')
        self.assertEqual('下载中', self.app.task_widgets[task_id]['status_label'].cget('text'))


class BatchSelectionTests(GuiFeatures):
    """多选批量：选择范围、筛选清理、删除记录与重复提交保护"""

    def _assert_slow_action_keeps_tk_alive(self, callback, operation):
        entered, release = threading.Event(), threading.Event()
        ticks, workers = [], []

        def slow(*args):
            workers.append(threading.get_ident())
            entered.set()
            release.wait(3)
            return True

        with patch.object(self.manager, operation, side_effect=slow) as call:
            try:
                callback()
                self.assertTrue(entered.wait(1))
                self.app.after(20, lambda: ticks.append(threading.get_ident()))
                self.pump(0.1)
                self.assertEqual(ticks, [threading.get_ident()])
                self.assertNotEqual(workers, ticks)
                self.assertTrue(self.app._batch_busy)
                self.app._update_task_status('async-live', 'paused', '')
                self.assertEqual(self.app.task_widgets['async-live']['action_btn'].cget('state'),
                                 'disabled')
                callback()
                self.assertEqual(call.call_count, 1)
            finally:
                release.set()
            self._wait_batch_idle()
        self.assertFalse(self.manager.get_activity_state()[1])

    def test_single_resume_probe_runs_off_tk_thread(self):
        task_id = self._inject('async-live', 'async-resume.bin', 'paused')
        self._assert_slow_action_keeps_tk_alive(
            lambda: self.app._on_start_task(task_id), 'resume_task')

    def test_single_pause_wait_runs_off_tk_thread(self):
        task_id = self._inject('async-live', 'async-pause.bin', 'paused')
        self._assert_slow_action_keeps_tk_alive(
            lambda: self.app._on_pause_task(task_id), 'pause_task')

    def test_pause_all_wait_runs_off_tk_thread(self):
        self._inject('async-live', 'async-pause-all.bin', 'paused')
        self._assert_slow_action_keeps_tk_alive(self.app._on_pause_all, 'pause_all')

    def test_resume_all_probe_runs_off_tk_thread(self):
        self._inject('async-live', 'async-resume-all.bin', 'paused')
        self._assert_slow_action_keeps_tk_alive(self.app._on_resume_all, 'resume_all')

    def _inject(self, task_id, filename, status='pending'):
        path = self.root / 'out' / filename
        self.db.create_task_with_chunks(task_id, f'http://example.com/{filename}', filename,
                                        str(path), status=status)
        self.app._add_task_widget(self.db.get_task(task_id))
        self.pump(0.1)
        return task_id

    def _wait_batch_idle(self, timeout=5):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline and self.app._batch_busy:
            self.pump(0.05)
        self.assertFalse(self.app._batch_busy, '批量操作未在预期时间内结束')

    def test_busy_survives_status_updates_and_new_cards(self):
        task_id = self._inject('busy-existing', 'existing.bin', 'paused')
        self.app._set_batch_busy(True)
        self.app._update_task_status(task_id, 'downloading', '正在下载')
        added = self._inject('busy-new', 'new.bin', 'paused')
        for selected in (task_id, added):
            widgets = self.app.task_widgets[selected]
            for control in ('action_btn', 'cancel_btn', 'delete_btn', 'select_box'):
                self.assertEqual(widgets[control].cget('state'), 'disabled', control)
        self.app._set_batch_busy(False)
        self.assertEqual(self.app.task_widgets[added]['action_btn'].cget('state'), 'normal')

    def test_busy_blocks_context_mutations_but_keeps_viewing_actions(self):
        first = self._inject('busy-menu-first', 'first.bin', 'paused')
        second = self._inject('busy-menu-second', 'second.bin', 'paused')
        self.app._set_batch_busy(True)
        menu = self.app._build_task_menu(second)
        try:
            for index in (0, 6, 7, 9):
                self.assertEqual(menu.entrycget(index, 'state'), 'disabled')
            self.assertEqual(menu.entrycget(2, 'state'), 'normal')
            self.assertEqual(menu.entrycget(4, 'state'), 'normal')
        finally:
            menu.destroy()
        # 防住批量开始前就已经弹出的旧菜单，而不仅是新建菜单的 disabled 外观。
        with patch.object(self.manager, 'resume_task') as resume, \
                patch.object(self.manager, 'pause_task') as pause, \
                patch.object(self.manager, 'cancel_task') as cancel, \
                patch.object(self.manager, 'delete_task') as delete, \
                patch.object(self.manager, 'move_task_to_top') as move, \
                patch('downloader.ui.main_window.DeleteTaskDialog') as dialog:
            self.app._on_start_task(first)
            self.app._on_pause_task(first)
            self.app._on_cancel_task(first)
            self.app._on_delete_task(first)
            self.app._on_move_task(second, 'top')
        for call in (resume, pause, cancel, delete, move, dialog):
            call.assert_not_called()

    def test_thread_start_failure_releases_batch_and_controls(self):
        task_id = self._inject('no-thread', 'worker.bin', 'paused')
        self.app._on_batch_select_all()
        with patch('downloader.ui.main_window.threading.Thread.start',
                   side_effect=RuntimeError('cannot start thread')):
            self.app._on_batch_action('start')
        self.assertFalse(self.app._batch_busy)
        self.assertFalse(self.manager.get_activity_state()[1])
        self.assertEqual(self.app.task_widgets[task_id]['action_btn'].cget('state'), 'normal')

    def test_delete_confirmation_cancel_leaves_everything_untouched(self):
        task_id = self._inject('cancel-delete', 'keep.bin', 'paused')
        self.app._on_batch_select_all()
        self.mocks[4].return_value = False
        with patch.object(self.manager, 'begin_batch_operation') as begin, \
                patch.object(self.manager, 'delete_task') as delete:
            self.app._on_batch_action('delete_records')
        begin.assert_not_called()
        delete.assert_not_called()
        self.assertIsNotNone(self.db.get_task(task_id))
        self.assertEqual(self.app._selected_task_ids, {task_id})

    def test_checkbox_invocation_does_not_open_detail_or_start_download(self):
        task_id = self._inject('checkbox-only', 'select.bin', 'paused')
        with patch.object(self.app, '_on_open_detail') as detail, \
                patch.object(self.manager, 'start_task') as start:
            self.app.task_widgets[task_id]['select_box'].toggle()
            self.pump(0.1)
        self.assertEqual(self.app._selected_task_ids, {task_id})
        detail.assert_not_called()
        start.assert_not_called()

    def test_slow_batch_keeps_tk_running_and_activity_busy_until_result(self):
        task_id = self._inject('slow-pause', 'pause.bin', 'downloading')
        self.app._on_batch_select_all()
        entered, release = threading.Event(), threading.Event()
        ticks = []

        def pause(_task_id):
            entered.set()
            if not release.wait(3):
                return False
            return True

        with patch.object(self.manager, 'pause_task', side_effect=pause):
            self.app._on_batch_action('pause')
            try:
                self.assertTrue(self.app._has_active_tasks())
                self.app.after(20, lambda: ticks.append(threading.get_ident()))
                self.pump(0.15)
                self.assertTrue(entered.is_set())
                self.assertEqual(ticks, [threading.get_ident()])
                self.assertTrue(self.manager.get_activity_state()[1])
            finally:
                release.set()
                self._wait_batch_idle()
        self.assertIn(task_id, self.app._selected_task_ids)

    def test_auto_directory_setting_cancel_failure_save_and_reset(self):
        dialog = SettingsDialog(self.app, self.config, on_save_callback=self.app._on_settings_saved)
        dialog.auto_classify_var.set(True)
        dialog._on_cancel()
        self.assertFalse(self.config.auto_classify_downloads)
        dialog = SettingsDialog(self.app, self.config, on_save_callback=self.app._on_settings_saved)
        self.pump(0.25)
        dialog.auto_classify_var.set(True)
        with patch.object(self.config, 'save', return_value=False):
            dialog._on_save()
        self.assertFalse(self.config.auto_classify_downloads)
        self.assertTrue(dialog.winfo_exists())
        dialog._on_save()
        self.pump(0.15)
        self.assertTrue(self.config.auto_classify_downloads)
        self.assertTrue(ConfigManager(self.config.config_path).auto_classify_downloads)
        dialog = SettingsDialog(self.app, self.config, on_save_callback=self.app._on_settings_saved)
        self.pump(0.25)
        self.assertTrue(dialog.auto_classify_var.get())
        # 重置的目录也隔离在测试根内，不能在用户默认下载目录建文件。
        defaults = dict(ConfigManager.DEFAULT_CONFIG, download_dir=str(self.root / 'reset-out'),
                        temp_dir=str(self.root / 'reset-parts'))
        with patch.object(ConfigManager, 'DEFAULT_CONFIG', defaults):
            dialog._on_reset()
            self.pump(0.1)
        self.assertFalse(dialog.auto_classify_var.get())
        self.assertFalse(self.config.auto_classify_downloads)
        self.assertFalse(ConfigManager(self.config.config_path).auto_classify_downloads)
        dialog.destroy()

    def test_selected_scope_follows_filter_and_is_not_restored(self):
        alpha = self._inject('b-alpha', 'alpha.bin')
        beta = self._inject('b-beta', 'beta.bin', status='completed')
        gamma = self._inject('b-gamma', 'gamma.bin')
        self.assertEqual(self.app.selection_label.cget('text'), '已选 0')

        self.app.search_entry.insert(0, 'alpha')
        self.app._apply_task_filter()
        self.app._on_batch_select_all()
        self.assertEqual(self.app._selected_task_ids, {alpha})

        self.app.search_entry.delete(0, 'end')
        self.app.search_entry.insert(0, 'zzz')
        self.app._apply_task_filter()
        self.assertEqual(self.app._selected_task_ids, set())
        self.assertFalse(self.app.task_widgets[alpha]['select_var'].get())

        self.app.search_entry.delete(0, 'end')
        self.app._apply_task_filter()
        self.assertEqual(self.app._selected_task_ids, set())
        # 新任务不自动进入选择
        self._inject('b-new', 'new.bin')
        self.assertEqual(self.app._selected_task_ids, set())
        self.assertFalse(self.app.task_widgets['b-new']['select_var'].get())
        self.assertEqual(self.app.task_widgets[beta]['status'], 'completed')
        self.assertIn(gamma, self.app.task_widgets)

    def test_delete_records_keeps_files_and_clears_cards_once(self):
        first = self._inject('b-one', 'one.bin')
        second = self._inject('b-two', 'two.bin')
        first = self.root / 'out' / 'one.bin'
        second = self.root / 'out' / 'two.bin'
        first.parent.mkdir(parents=True, exist_ok=True)
        first.write_bytes(b'keep-one')
        second.write_bytes(b'keep-two')
        self.app._on_batch_select_all()
        self.assertEqual(len(self.app._selected_task_ids), 2)

        self.mocks[4].reset_mock()
        self.app._on_batch_action('delete_records')
        self._wait_batch_idle()

        self.mocks[4].assert_called_once()
        self.assertEqual(self.mocks[4].call_args.args[0], '删除记录')
        self.assertEqual(self.db.get_all_tasks(), [])
        self.assertEqual(self.app.task_widgets, {})
        self.assertEqual(self.app._selected_task_ids, set())
        self.assertEqual(first.read_bytes(), b'keep-one')
        self.assertEqual(second.read_bytes(), b'keep-two')

    def test_double_click_submits_only_one_batch_with_frozen_scope(self):
        alpha = self._inject('b-a', 'alpha.bin', status='downloading')
        beta = self._inject('b-b', 'beta.bin', status='downloading')
        self.app._on_batch_select_all()
        calls = []

        def slow_operate(task_ids, action, reserved=False):
            calls.append((list(task_ids), action))
            time.sleep(0.2)
            self.manager.end_batch_operation()
            return {'action': action, 'total': len(task_ids), 'succeeded': [],
                    'skipped': [], 'failed': []}

        with patch.object(self.app.task_manager, 'batch_operate', side_effect=slow_operate):
            self.app._on_batch_action('pause')
            # 提交后立即改筛选，已提交快照不能跟着变化
            self.app.search_entry.insert(0, 'alpha')
            self.app._apply_task_filter()
            self.app._on_batch_action('pause')
            self._wait_batch_idle()

        self.assertEqual(len(calls), 1)
        self.assertEqual(set(calls[0][0]), {alpha, beta})
        self.assertEqual(calls[0][1], 'pause')
