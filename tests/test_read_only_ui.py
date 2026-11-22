"""Read-only UI and window-stability regressions with disposable records and paths."""
import ctypes
from ctypes import wintypes
from datetime import datetime, timedelta
import gc
from pathlib import Path
import tempfile
import time
import tkinter
import traceback
import unittest
from unittest.mock import patch

import customtkinter as ctk

from downloader.core.download_engine import DownloadEngine
from downloader.core.task_manager import TaskManager
from downloader.database.db_manager import DatabaseManager
from downloader.ui.main_window import MainWindow
from downloader.ui.settings_dialog import SettingsDialog
from downloader.ui import ctk_helpers, task_presentation
from downloader.utils.config import ConfigManager


class EstimatedCompletionTests(unittest.TestCase):
    now = datetime(2026, 11, 22, 15, 30)

    def estimate(self, downloaded=0, total=600, speed=1, status='downloading', now=None):
        return task_presentation.format_estimated_completion(
            downloaded, total, speed, status, now=self.now if now is None else now)

    def test_today(self):
        self.assertEqual(self.estimate(), '预计今天 15:40')

    def test_midnight_and_later_dates(self):
        self.assertEqual(self.estimate(now=datetime(2026, 11, 22, 23, 55)),
                         '预计明天 00:05')
        self.assertEqual(self.estimate(total=3 * 86400), '预计 11/25 15:30')
        self.assertEqual(self.estimate(now=datetime(2026, 12, 31, 23, 55)),
                         '预计明天 00:05')

    def test_only_downloading_has_an_estimate(self):
        for status in ('pending', 'paused', 'completed', 'failed', 'cancelled',
                       'verifying', 'verify_failed', '', None):
            with self.subTest(status=status):
                self.assertEqual(self.estimate(status=status), '预计 --')

    def test_unknown_invalid_and_finished_bytes_have_no_estimate(self):
        for downloaded, total in ((None, 600), (-1, 600), (-0.5, 600), ('bad', 600),
                                  (0, None), (0, 0), (0, -1), (0, float('nan')),
                                  (0, float('inf')), (600, 600), (601, 600)):
            with self.subTest(downloaded=downloaded, total=total):
                self.assertEqual(self.estimate(downloaded, total), '预计 --')
        self.assertEqual(self.estimate('0', '600'), '预计今天 15:40')

    def test_invalid_or_zero_speed_has_no_estimate(self):
        for speed in (None, 0, -1, 'bad', float('nan'), float('inf'), float('-inf')):
            with self.subTest(speed=speed):
                self.assertEqual(self.estimate(speed=speed), '预计 --')

    def test_unrepresentable_dates_do_not_break_progress_rendering(self):
        self.assertEqual(self.estimate(total=10 ** 400), '预计 --')
        self.assertEqual(self.estimate(speed=1e-300), '预计 --')
        self.assertEqual(self.estimate(now=datetime.max - timedelta(seconds=1)), '预计 --')


class ReadOnlyUiTests(unittest.TestCase):
    def setUp(self):
        self.gc_was_enabled = gc.isenabled()
        gc.collect()
        gc.disable()
        self.temp = tempfile.TemporaryDirectory(prefix='read-only-ui-')
        self.root = Path(self.temp.name)
        self.config = ConfigManager(str(self.root / 'config.json'))
        self.config.download_dir = str(self.root / 'out')
        self.config.set('temp_dir', str(self.root / 'parts'))
        self.config.set('ui_float_bar_enabled', False)
        self.db = DatabaseManager(str(self.root / 'downloads.db'))
        self.engine = DownloadEngine(self.db, self.config)
        self.manager = TaskManager(self.engine, self.db, max_concurrent=1)
        self.patches = [patch('downloader.ui.main_window.TrayManager.start'),
                        patch('downloader.ui.main_window.TrayManager.notify_download_complete')]
        for item in self.patches:
            item.start()
        self.app = MainWindow(self.manager)
        self.app.withdraw()
        self.callback_errors = []
        self.app.report_callback_exception = lambda *args: self.callback_errors.append(
            ''.join(traceback.format_exception(*args)))
        self.pump()

    def tearDown(self):
        try:
            for timer in self.app.tk.call('after', 'info'):
                self.app.tk.call('after', 'cancel', timer)
            self.app._exit_app()
        finally:
            for item in reversed(self.patches):
                item.stop()
            self.manager.shutdown()
            self.temp.cleanup()
            self.app = None
            gc.collect()
            if self.gc_was_enabled:
                gc.enable()
        self.assertEqual(self.callback_errors, [])

    def pump(self, seconds=0.1):
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            self.app.update()
            time.sleep(0.01)

    def add_card(self, task_id, url=None, filename=None, status='paused', total=600):
        self.db.create_task_with_chunks(
            task_id, url or f'https://example.com/{task_id}.bin',
            filename or f'{task_id}.bin', str(self.root / f'{task_id}.bin'),
            total_size=total, status=status)
        self.app._add_task_widget(self.db.get_task(task_id))
        return self.app.task_widgets[task_id]

    def select(self, *task_ids):
        for task_id in task_ids:
            self.app.task_widgets[task_id]['select_box'].toggle()

    def copy_menu_index(self, menu):
        return next(index for index in range(menu.index('end') + 1)
                    if menu.type(index) == 'command'
                    and menu.entrycget(index, 'label').startswith('复制选中链接'))

    def test_estimate_is_shared_by_card_and_detail_and_clears_on_pause(self):
        widgets = self.add_card('active', status='downloading')
        with patch.object(task_presentation, 'datetime') as clock:
            clock.now.return_value = EstimatedCompletionTests.now
            self.app._on_open_detail('active')
            detail = self.app._detail_windows['active']
            self.app._render_task_progress('active', 0, 600, 1)
            self.assertEqual(widgets['eta_label'].cget('text'), '剩余 10:00')
            self.assertEqual(widgets['estimated_label'].cget('text'), '预计今天 15:40')
            self.assertTrue(detail.estimated_label.cget('text').startswith('预计今天 15:40'))
            self.assertIn('不含后续处理耗时', detail.estimated_label.cget('text'))
            self.db.update_task_status('active', 'paused')
            self.app._update_task_status('active', 'paused')
            self.app._update_task_progress('active', 100, 600, 2)
            self.assertEqual(widgets['estimated_label'].cget('text'), '预计 --')
            self.assertTrue(detail.estimated_label.cget('text').startswith('预计 --'))

    def test_terminal_and_unknown_progress_cannot_keep_an_old_estimate(self):
        widgets = self.add_card('active', status='downloading')
        self.app._render_task_progress('active', 0, 600, 1)
        self.assertNotEqual(widgets['estimated_label'].cget('text'), '预计 --')
        self.app._render_task_progress('active', None, 600, 1)
        self.assertEqual(widgets['estimated_label'].cget('text'), '预计 --')
        self.app._render_task_progress('active', 0, 0, 1)
        self.assertEqual(widgets['estimated_label'].cget('text'), '预计 --')
        self.db.update_task_status('active', 'completed')
        self.db.update_task_progress('active', 600, 0)
        self.app._update_task_status('active', 'completed')
        self.app._update_task_progress('active', 100, 600, 1)
        self.assertEqual(widgets['estimated_label'].cget('text'), '预计 --')

    def test_copy_preserves_list_order_signed_urls_duplicates_and_records(self):
        signed = 'https://example.com/file?token=a%2Fb%3D&x=1+2&x=3#part'
        first = self.add_card('first', url=signed)
        self.add_card('second', url='https://example.com/second.bin')
        self.add_card('third', url=signed)
        self.select('first', 'second', 'third')
        self.app._on_move_task('third', 'top')
        before = self.db.get_all_tasks()
        partial = self.root / 'parts' / 'keep.tmp'
        partial.parent.mkdir(parents=True, exist_ok=True)
        partial.write_bytes(b'keep existing resume bytes')
        with patch.object(self.app, 'clipboard_clear') as clear, \
                patch.object(self.app, 'clipboard_append') as append, \
                patch.object(self.manager, 'begin_batch_operation') as batch, \
                patch.object(self.manager, 'start_task') as start, \
                patch.object(self.manager, 'resume_task') as resume, \
                patch.object(self.manager, 'pause_task') as pause:
            menu = self.app._build_task_menu('first')
            menu.invoke(self.copy_menu_index(menu))
            menu.destroy()
            clear.assert_called_once_with()
            append.assert_called_once_with(f'{signed}\n{signed}\nhttps://example.com/second.bin')
            batch.assert_not_called()
            start.assert_not_called()
            resume.assert_not_called()
            pause.assert_not_called()
        self.assertEqual(self.db.get_all_tasks(), before)
        self.assertEqual(partial.read_bytes(), b'keep existing resume bytes')
        self.assertEqual(len(self.app._selected_task_ids), 3)
        self.assertIn('已复制 3 条下载链接', self.app.status_label.cget('text'))
        self.assertTrue(first['select_var'].get())

    def test_copy_is_limited_to_current_visible_selection(self):
        self.add_card('keep')
        self.add_card('hidden')
        self.add_card('not_selected', filename='keep-too.bin')
        self.select('keep', 'hidden')
        self.app.search_entry.insert(0, 'keep')
        self.app._apply_task_filter()
        self.assertEqual(self.app._selected_task_ids, {'keep'})
        # Even a stale selection arriving after filtering must not copy a hidden task.
        self.app._selected_task_ids.add('hidden')
        with patch.object(self.app, 'clipboard_clear'), \
                patch.object(self.app, 'clipboard_append') as append:
            self.app._on_copy_selected_links()
            append.assert_called_once_with('https://example.com/keep.bin')

    def test_single_selected_copy_does_not_reopen_clipboard_popup(self):
        url = 'https://example.com/selected.bin?signature=a%2Fb'
        self.add_card('single', url=url)
        self.select('single')
        self.app.deiconify()
        self.pump()
        with patch.object(self.app, 'clipboard_clear'), \
                patch.object(self.app, 'clipboard_append') as append, \
                patch.object(self.app, 'clipboard_get', return_value=url), \
                patch.object(self.app, '_show_clipboard_popup') as popup:
            self.app._on_copy_selected_links()
            self.app._check_clipboard_once()
            append.assert_called_once_with(url)
            popup.assert_not_called()
            self.assertEqual(self.app._clipboard_last_seen, url)

    def test_no_selection_busy_and_exit_do_not_copy_or_shift_menu_guards(self):
        self.add_card('task')
        with patch.object(self.app, 'clipboard_clear') as clear, \
                patch.object(self.app, 'clipboard_append') as append:
            menu = self.app._build_task_menu('task')
            self.assertEqual(menu.entrycget(self.copy_menu_index(menu), 'state'), 'disabled')
            menu.destroy()
            self.app._on_copy_selected_links()
            self.select('task')
            self.app._batch_busy = True
            menu = self.app._build_task_menu('task')
            for index in (0, 6, 7, 9, self.copy_menu_index(menu)):
                self.assertEqual(menu.entrycget(index, 'state'), 'disabled')
            menu.destroy()
            self.app._on_copy_selected_links()
            self.app._batch_busy = False
            self.app._exiting = True
            self.app._on_copy_selected_links()
            self.app._exiting = False
            clear.assert_not_called()
            append.assert_not_called()

    def test_clipboard_failure_reports_failure_without_success(self):
        self.add_card('task')
        self.select('task')
        with patch.object(self.app, 'clipboard_clear'), \
                patch.object(self.app, 'clipboard_append', side_effect=tkinter.TclError('busy')):
            self.app._on_copy_selected_links()
        self.assertIn('复制失败', self.app.status_label.cget('text'))
        self.assertNotIn('已复制', self.app.status_label.cget('text'))

    def test_theme_switch_keeps_multiple_details_visible_without_unmapping(self):
        self.app.deiconify()
        for task_id in ('first', 'second'):
            self.add_card(task_id, status='downloading')
            self.app._on_open_detail(task_id)
        self.pump(0.3)
        windows = [self.app, *self.app._detail_windows.values()]
        unmaps = []
        for window in windows:
            window.bind('<Unmap>', lambda event, target=window:
                        unmaps.append(str(target)) if event.widget == target else None, add='+')
        before = self.db.get_all_tasks()
        for mode in ('light', 'dark', 'light', 'dark'):
            ctk.set_appearance_mode(mode)
            self.pump(0.2)
            self.assertEqual([window.state() for window in windows], ['normal'] * 3)
        self.assertEqual(unmaps, [])
        self.assertEqual(self.db.get_all_tasks(), before)
        self.select('first', 'second')
        self.assertEqual(self.app._selected_task_ids, {'first', 'second'})
        with patch.object(self.app, 'clipboard_clear'), \
                patch.object(self.app, 'clipboard_append') as append:
            self.app._on_copy_selected_links()
            append.assert_called_once_with('https://example.com/first.bin\nhttps://example.com/second.bin')

    def test_settings_theme_save_keeps_an_open_detail_visible(self):
        self.app.deiconify()
        self.add_card('detail')
        self.app._on_open_detail('detail')
        self.pump(0.3)
        errors = []

        def save_settings():
            dialog = next(window for window in self.app.winfo_children()
                          if isinstance(window, SettingsDialog))
            try:
                dialog.appearance_segment.set('浅色')
                dialog._on_save()
            except Exception as error:
                errors.append(error)
                dialog.destroy()

        self.app.after(250, save_settings)
        self.app._on_settings()
        self.pump(0.3)
        self.assertEqual(errors, [])
        self.assertEqual(ctk.get_appearance_mode(), 'Light')
        self.assertEqual(ConfigManager(self.config.config_path).ui_appearance_mode, 'light')
        self.assertEqual(self.app.state(), 'normal')
        self.assertEqual(self.app._detail_windows['detail'].state(), 'normal')

    @unittest.skipUnless(hasattr(ctypes, 'windll'), 'Windows DWM titlebar attribute')
    def test_native_titlebar_attribute_tracks_dark_and_light(self):
        get_attribute = ctypes.windll.dwmapi.DwmGetWindowAttribute
        get_attribute.argtypes = [wintypes.HWND, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD]
        get_attribute.restype = ctypes.c_long
        self.app.deiconify()
        self.add_card('titlebar')
        self.app._on_open_detail('titlebar')
        self.pump(0.3)
        # 从实际主题切换开始检查，避免把旧详情窗首次映射的标题栏行为混入本回归。
        for mode in ('light', 'dark', 'light'):
            ctk.set_appearance_mode(mode)
            self.pump(0.2)
            for window in (self.app, self.app._detail_windows['titlebar']):
                value = ctypes.c_int()
                hwnd = ctk_helpers._get_window_parent(window.winfo_id())
                result = get_attribute(hwnd, 20, ctypes.byref(value), ctypes.sizeof(value))
                if result != 0:
                    result = get_attribute(hwnd, 19, ctypes.byref(value), ctypes.sizeof(value))
                self.assertEqual(result, 0)
                self.assertEqual(value.value, int(mode == 'dark'))
                self.assertEqual(window.state(), 'normal')

    def test_theme_preserves_window_states_and_tray_restore(self):
        self.app.deiconify()
        self.pump(0.2)
        self.app.state('zoomed')
        self.pump(0.2)
        ctk.set_appearance_mode('light')
        self.pump(0.2)
        self.assertEqual(self.app.state(), 'zoomed')
        self.app.state('normal')
        self.app.iconify()
        self.pump(0.2)
        ctk.set_appearance_mode('dark')
        self.pump(0.2)
        self.assertEqual(self.app.state(), 'iconic')
        self.app._show_from_tray()
        self.pump(0.2)
        self.assertEqual(self.app.state(), 'normal')
        self.app.withdraw()
        self.pump(0.2)
        ctk.set_appearance_mode('light')
        self.pump(0.2)
        self.assertEqual(self.app.state(), 'withdrawn')
        self.app._show_from_tray()
        self.pump(0.2)
        self.assertEqual(self.app.state(), 'normal')

    def test_theme_then_quick_close_does_not_leave_late_callbacks(self):
        self.app.deiconify()
        self.add_card('quick')
        self.app._on_open_detail('quick')
        self.pump(0.3)
        detail = self.app._detail_windows['quick']
        detail.focus_force()
        ctk.set_appearance_mode('light')
        detail._on_close()
        self.pump(0.2)
        self.assertEqual(self.app.state(), 'normal')
        self.app._on_open_detail('quick')
        self.pump(0.2)
        self.assertEqual(self.app._detail_windows['quick'].state(), 'normal')
        ctk.set_appearance_mode('dark')
        self.app._exit_app()
        self.assertFalse(self.app._window_alive())

    def test_card_and_detail_estimates_fit_existing_layout(self):
        # 深浅主题与缩放组合分别在独立进程中验证。
        widgets = self.add_card('layout', status='downloading')
        self.app.deiconify()
        self.app._on_open_detail('layout')
        detail = self.app._detail_windows['layout']
        detail.geometry('480x360')
        with patch.object(task_presentation, 'datetime') as clock:
            clock.now.return_value = EstimatedCompletionTests.now
            self.app._render_task_progress('layout', 0, 3 * 86400, 1)
        self.pump()
        estimate = widgets['estimated_label']
        buttons = widgets['button_frame']
        delete = widgets['delete_btn']
        self.assertTrue(estimate.winfo_viewable())
        self.assertLessEqual(delete.winfo_x() + delete.winfo_width(), estimate.winfo_x())
        self.assertLessEqual(estimate.winfo_x() + estimate.winfo_width(), buttons.winfo_width())
        self.assertGreaterEqual(estimate.winfo_width(), estimate.winfo_reqwidth())
        self.assertTrue(detail.estimated_label.winfo_viewable())
        self.assertGreaterEqual(detail.estimated_label.winfo_width(),
                                detail.estimated_label.winfo_reqwidth())
        self.assertLessEqual(detail.estimated_label.winfo_y()
                             + detail.estimated_label.winfo_height(), detail.canvas.winfo_y())


if __name__ == '__main__':
    unittest.main()
