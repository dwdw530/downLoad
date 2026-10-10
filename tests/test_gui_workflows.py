"""Real Tk widgets/event loops; dialogs and data belong only to these tests."""
import hashlib
from pathlib import Path
import threading
import time
import tkinter
import traceback
from types import SimpleNamespace
from unittest.mock import Mock, patch

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
        self.assertEqual(self.callback_errors, [])

    def pump(self, seconds=0.1):
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            self.app.update()
            time.sleep(0.01)

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
        task_id = self.db.get_all_tasks()[0]['task_id']
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
        schedule.assert_not_called()
        task = self.db.get_all_tasks()[0]
        self.assertEqual(task['status'], 'paused')
        self.assertEqual(self.app.task_widgets[task['task_id']]['action_btn'].cget('text'), '▶ 继续')

    def test_start_now_creates_pending_task_and_starts_queue(self):
        def fill(dialog):
            dialog.url_text.insert('1.0', self.base_url + '/range')
            dialog._on_confirm(False)
        self.dialog_action(self.app._on_add_task, AddTaskDialog, fill)
        task = self.db.get_all_tasks()[0]
        self.assertIn(task['status'], ('pending', 'downloading', 'completed'))
        self.assertNotEqual(task['status'], 'paused')

    def test_batch_add_creates_tasks_and_reports_failures(self):
        def fill(dialog):
            dialog.url_text.insert(
                '1.0', f"{self.base_url}/range\n{self.base_url}/tiny\n{self.base_url}/not-found")
            dialog._on_confirm()
        self.dialog_action(self.app._on_add_task, AddTaskDialog, fill)
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline and len(self.db.get_all_tasks()) < 2:
            self.pump(0.2)
        self.pump(0.5)
        self.assertEqual({task['filename'] for task in self.db.get_all_tasks()}, {'range', 'tiny'})
        summaries = [call.args for call in self.mocks[2].call_args_list
                     if call.args and call.args[0] == '添加结果']
        self.assertEqual(len(summaries), 1)
        self.assertIn('成功添加 2 个任务', summaries[0][1])
        self.assertIn('失败 1 个', summaries[0][1])

    def test_batch_add_skips_duplicate_urls(self):
        def fill(dialog):
            dialog.url_text.insert('1.0', f"{self.base_url}/range\n{self.base_url}/range")
            dialog._on_confirm()
        self.dialog_action(self.app._on_add_task, AddTaskDialog, fill)
        self.pump(0.3)
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
        frame = self.app.task_widgets[task_id]['frame']
        canvas = self._frame_canvas(frame)
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
        self.app._on_task_status_changed(task_id, 'downloading', '')
        self.app._on_task_progress(task_id, 100, 1000, 2048)
        self.app._on_task_progress(task_id, 200, 1000, 4096)
        self.pump(0.3)
        self.assertEqual(len(self.app._speed_history[task_id]), 2)
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

