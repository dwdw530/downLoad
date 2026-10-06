"""Real Tk widgets/event loops; dialogs and data belong only to these tests."""
import hashlib
from pathlib import Path
import threading
import time
import traceback
from types import SimpleNamespace
from unittest.mock import Mock, patch

from downloader.ui.main_window import MainWindow, AddTaskDialog, DeleteTaskDialog, CloseConfirmDialog
from downloader.ui.history_dialog import HistoryDialog
from downloader.ui.settings_dialog import SettingsDialog
from downloader.utils.config import ConfigManager
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
            dialog.url_entry.insert(0, self.base_url + '/range')
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
