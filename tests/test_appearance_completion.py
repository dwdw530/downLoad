"""透明度、真实分层窗口、轮次与退出门禁；只使用隔离数据。"""
import gc
import ctypes
import json
import tempfile
import threading
import time
import traceback
import unittest
from pathlib import Path
from queue import Empty
from unittest.mock import Mock, patch

from PIL import Image
from downloader.utils.config import ConfigManager, normalize_opacity
import downloader.ui.float_bar as floating
import downloader.ui.main_window as windows
from downloader.ui.settings_dialog import SettingsDialog
from test_features import FeatureFixture


class AppearanceConfigTests(unittest.TestCase):
    def test_opacity_normalizes_invalid_values_and_endpoints(self):
        for minimum in (30, 60):
            for value in (None, True, False, {}, '', 'bad', float('nan'), float('inf'), 10**400):
                with self.subTest(minimum=minimum, value=str(value)[:30]):
                    self.assertEqual(normalize_opacity(value, minimum), 100)
            for value, expected in ((0, minimum), (-80, minimum), (120, 100), ('81.5', 82), ('82.4', 82)):
                self.assertEqual(normalize_opacity(value, minimum), expected)

    def test_old_config_defaults_and_shutdown_are_preserved(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'config.json'
            path.write_text(json.dumps({'after_download': {'shutdown': True}}), encoding='utf-8')
            config = ConfigManager(str(path))
            self.assertEqual((config.ui_window_opacity, config.ui_float_bar_opacity), (100, 100))
            self.assertTrue(config.after_download['shutdown'])
            self.assertFalse(config.after_download['exit_app'])
            config.ui_window_opacity = 71.5
            config.ui_float_bar_opacity = 44
            self.assertTrue(config.save())
            again = ConfigManager(str(path))
            self.assertEqual((again.ui_window_opacity, again.ui_float_bar_opacity), (72, 44))

    def test_malformed_actions_prefer_exit_and_handle_non_dict(self):
        with tempfile.TemporaryDirectory() as directory:
            config = ConfigManager(str(Path(directory) / 'config.json'))
            for value in (None, True, 'shutdown', [True]):
                config.set('after_download', value)
                self.assertFalse(any(config.after_download.values()))
            config.set('after_download', {'exit_app': True, 'shutdown': True})
            self.assertTrue(config.after_download['exit_app'])
            self.assertFalse(config.after_download['shutdown'])
            config.after_download = {'exit_app': True, 'shutdown': True, 'open_file': True}
            self.assertFalse(config.get('after_download')['shutdown'])
            self.assertTrue(config.after_download['open_file'])


class AppearancePixelTests(unittest.TestCase):
    def test_exact_premultiplied_bytes_and_transparent_zero(self):
        image = Image.new('RGBA', (2, 1))
        image.putdata([(200, 100, 50, 128), (200, 100, 50, 0)])
        self.assertEqual(floating.premultiplied_bgra(image), bytes([25, 50, 100, 128, 0, 0, 0, 0]))

    def test_reverse_unpremultiplication_is_rejected(self):
        image = Image.new('RGBA', (1, 1), (200, 100, 50, 128))
        with patch.object(floating, 'premultiplied_bgra',
                          lambda im: im.convert('RGBa').convert('RGBA').tobytes('raw', 'BGRA')):
            with self.assertRaises(AssertionError):
                self.assertEqual(floating.premultiplied_bgra(image), bytes([25, 50, 100, 128]))

    def test_render_matrix_keeps_size_transparent_corners_and_solid_foreground(self):
        state = dict(status='downloading', speed=123456789, downloaded=10, total=20,
                     filename='很长的中文文件名用于验证布局' * 5, downloading=2, pending=1)
        for theme in floating.THEMES.values():
            for mode in ('normal', 'mini'):
                for scale in (1, 1.5, 2):
                    for opacity in (30, 100):
                        with self.subTest(mode=mode, scale=scale, opacity=opacity, theme=theme['card']):
                            image = floating.render_bar(mode=mode, theme=theme, state=state,
                                samples=[(1, 1), (2, 10), (3, 6)], hovered=False,
                                scale=scale, opacity=opacity)
                            expected = (158, 34) if mode == 'mini' else (280, 118)
                            self.assertEqual(image.size, tuple(round(v * scale) for v in expected))
                            self.assertEqual(image.getpixel((0, 0))[3], 0)
                            alpha = image.getchannel('A')
                            self.assertEqual(alpha.getextrema()[1], 255)
                            if opacity == 30:
                                self.assertIn(76, alpha.getdata())
                            raw = floating.premultiplied_bgra(image)
                            self.assertTrue(all(max(raw[i:i+3]) <= raw[i+3] for i in range(0, len(raw), 4)))


class IdleShutdownGateTests(FeatureFixture):
    def completed(self):
        task_id = self.create('/tiny', start_later=True)
        self.db.update_task_status(task_id, 'completed')
        return task_id

    def test_success_gate_rejects_all_subsequent_adds_and_starts(self):
        task_id = self.completed()
        generation = self.manager.get_activity_state()[0]
        self.assertTrue(self.manager.begin_idle_shutdown(generation, {task_id}))
        with patch.object(self.engine, 'create_download_task') as create:
            self.assertIsNone(self.manager.add_task(self.base_url + '/tiny'))
        create.assert_not_called()
        another = self.create('/tiny', start_later=True)
        self.assertFalse(self.manager.start_task(another))

    def test_generation_active_missing_and_failed_tasks_reject_gate(self):
        task_id = self.completed()
        self.assertFalse(self.manager.begin_idle_shutdown(-1, {task_id}))
        self.assertFalse(self.manager.begin_idle_shutdown(0, {'missing'}))
        for status in ('failed', 'verify_failed', 'paused', 'cancelled', 'pending', 'verifying'):
            self.db.update_task_status(task_id, status)
            self.assertFalse(self.manager.begin_idle_shutdown(0, {task_id}))
            self.assertFalse(self.manager._shutdown_started)

    def test_database_failure_does_not_close_gate(self):
        task_id = self.completed()
        with patch.object(self.db, 'get_all_tasks', side_effect=RuntimeError('read failed')):
            self.assertFalse(self.manager.begin_idle_shutdown(0, {task_id}))
        self.assertFalse(self.manager._shutdown_started)

    def test_new_add_racing_final_scan_cannot_enter_after_gate(self):
        task_id = self.completed()
        scanning, release, add_attempted = threading.Event(), threading.Event(), threading.Event()
        real_scan = self.db.get_all_tasks
        results = {}
        def scan():
            scanning.set()
            if not release.wait(3):
                raise RuntimeError('gate test timed out')
            return real_scan()
        def add():
            add_attempted.set()
            results['add'] = self.manager.add_task(self.base_url + '/tiny')
        with patch.object(self.db, 'get_all_tasks', side_effect=scan), \
                patch.object(self.engine, 'create_download_task') as create:
            closer = threading.Thread(target=lambda: results.update(
                gate=self.manager.begin_idle_shutdown(0, {task_id})))
            adder = threading.Thread(target=add)
            closer.start()
            self.assertTrue(scanning.wait(3))
            adder.start()
            self.assertTrue(add_attempted.wait(3))
            release.set()
            closer.join(3)
            adder.join(3)
            self.assertFalse(closer.is_alive())
            self.assertFalse(adder.is_alive())
        self.assertEqual(results, {'gate': True, 'add': None})
        create.assert_not_called()


class AppearanceCompletionGuiTests(FeatureFixture):
    def setUp(self):
        super().setUp()
        self._gc_enabled = gc.isenabled()
        gc.collect()
        gc.disable()
        self._patches = [patch('downloader.ui.main_window.TrayManager.start'),
                         patch('downloader.ui.main_window.TrayManager.notify_download_complete'),
                         patch('tkinter.messagebox.showerror'),
                         patch('tkinter.messagebox.askyesno', return_value=True)]
        for item in self._patches:
            item.start()
        self.app = windows.MainWindow(self.manager)
        self.app.withdraw()
        self.errors = []
        self.app.report_callback_exception = lambda *args: self.errors.append(
            ''.join(traceback.format_exception(*args)))
        self.pump(0.1)

    def tearDown(self):
        try:
            if not self.app._exiting:
                for job in self.app.tk.call('after', 'info'):
                    self.app.tk.call('after', 'cancel', job)
                self.app._exit_app()
            self.app = None
            gc.collect()
        finally:
            for item in reversed(self._patches):
                item.stop()
            super().tearDown()
            if self._gc_enabled:
                gc.enable()
        self.assertEqual(self.errors, [])

    def pump(self, duration=0.15):
        deadline = time.monotonic() + duration
        while time.monotonic() < deadline and not self.app._exiting:
            self.app.update()
            time.sleep(0.005)

    def enable(self):
        self.config.after_download = {'exit_app': True}
        self.app._sync_exit_action()

    def finish(self, status='completed'):
        task_id = self.create('/tiny', start_later=True)
        def complete(*args, **kwargs):
            self.db.update_task_status(task_id, status)
            self.manager._on_engine_status_change(task_id, status, '')
            return True
        with patch.object(self.engine, 'start_download', side_effect=complete):
            self.assertTrue(self.manager.start_task(task_id))
        return task_id

    def settings(self):
        return SettingsDialog(self.app, self.config, on_save_callback=self.app._on_settings_saved)

    def preview(self, dialog, window=70, floating=45):
        dialog.window_opacity_slider.set(window)
        dialog.float_opacity_slider.set(floating)
        dialog._on_opacity_change()
        self.pump(0.05)

    def test_preview_cancel_close_and_destroy_preserve_config_and_popups(self):
        before = Path(self.config.config_path).read_bytes()
        memory = self.config.get_all()
        for close in ('cancel', 'wm_close', 'destroy'):
            dialog = self.settings()
            self.preview(dialog)
            self.assertAlmostEqual(float(self.app.attributes('-alpha')), 0.7, places=2)
            self.assertEqual(float(dialog.attributes('-alpha')), 1.0)
            self.assertIsNone(self.app.float_bar)
            self.assertEqual(self.config.get_all(), memory)
            self.assertEqual(Path(self.config.config_path).read_bytes(), before)
            if close == 'cancel':
                dialog._on_cancel()
            elif close == 'wm_close':
                dialog.tk.call(dialog.protocol('WM_DELETE_WINDOW'))
            else:
                dialog.destroy()
            self.pump(0.1)
            self.assertEqual(float(self.app.attributes('-alpha')), 1.0)

    def test_save_failure_restores_runtime_memory_and_file_then_can_retry(self):
        dialog = self.settings()
        self.preview(dialog)
        before = Path(self.config.config_path).read_bytes()
        memory = self.config.get_all()
        dialog.timeout_entry.delete(0, 'end')
        dialog.timeout_entry.insert(0, '0')
        dialog._on_save()
        self.assertEqual(float(self.app.attributes('-alpha')), 1.0)
        self.assertEqual(Path(self.config.config_path).read_bytes(), before)
        self.assertEqual(self.config.get_all(), memory)
        dialog.timeout_entry.delete(0, 'end')
        dialog.timeout_entry.insert(0, str(self.config.timeout))
        with patch('downloader.utils.config.os.replace', side_effect=PermissionError('denied')):
            dialog._on_save()
        self.assertTrue(dialog.winfo_exists())
        self.assertEqual(self.config.get_all(), memory)
        self.assertEqual(Path(self.config.config_path).read_bytes(), before)
        self.assertEqual(float(self.app.attributes('-alpha')), 1.0)
        dialog._on_save()
        self.pump(0.1)
        self.assertAlmostEqual(float(self.app.attributes('-alpha')), 0.7, places=2)
        config = ConfigManager(self.config.config_path)
        self.assertEqual((config.ui_window_opacity, config.ui_float_bar_opacity), (70, 45))

    def test_reset_commits_new_cancel_baseline_and_failed_reset_does_not_apply(self):
        self.config.ui_window_opacity = 80
        self.config.ui_float_bar_opacity = 65
        self.app._preview_opacity(80, 65)
        dialog = self.settings()
        self.preview(dialog)
        with patch.object(self.config, 'reset', return_value=False):
            dialog._on_reset()
        self.assertAlmostEqual(float(self.app.attributes('-alpha')), 0.8, places=2)
        dialog._on_reset()
        self.preview(dialog, 60, 30)
        dialog._on_cancel()
        self.pump(0.1)
        self.assertEqual(float(self.app.attributes('-alpha')), 1.0)
        self.assertEqual((self.config.ui_window_opacity, self.config.ui_float_bar_opacity), (100, 100))

    def test_float_background_preview_native_push_and_mode_show_theme_preserve_opacity(self):
        self.config.ui_float_bar_opacity = 45
        self.app._set_float_bar_visible(True)
        bar = self.app.float_bar
        with patch.object(floating, 'premultiplied_bgra', wraps=floating.premultiplied_bgra) as convert:
            dialog = self.settings()
            self.preview(dialog, 65, 30)
            self.assertEqual(bar._opacity, 30)
            self.assertTrue(bar._last_push_succeeded)
            self.assertTrue(convert.called)
            dialog._on_cancel()
        user32, kernel32 = ctypes.windll.user32, ctypes.windll.kernel32
        kernel32.GetCurrentProcess.restype = ctypes.c_void_p
        user32.GetGuiResources.argtypes = [ctypes.c_void_p, ctypes.c_uint]
        process = kernel32.GetCurrentProcess()
        before = user32.GetGuiResources(process, 0)
        for _ in range(40):
            self.assertTrue(bar._surface.push(bar._hwnd, bar._image))
        self.assertLessEqual(user32.GetGuiResources(process, 0) - before, 1)
        self.assertEqual(bar._opacity, 45)
        for mode in ('mini', 'normal'):
            bar.set_mode(mode, persist=False)
            bar.hide()
            bar.show()
            bar.refresh_theme()
            self.assertEqual(bar._opacity, 45)
            self.assertTrue(bar._last_push_succeeded)

    def test_idle_history_and_verify_failure_do_not_open_exit(self):
        self.finish()
        self.finish('failed')
        self.finish('paused')
        self.pump()
        self.enable()
        self.app._on_task_status_changed('old', 'verify_failed', '')
        self.pump()
        self.assertFalse(self.app._exit_dialog_open)
        # 开启前已经完成、但启动事件还在 UI 队列里的任务不属于新轮次。
        self.config.after_download = {}
        self.app._sync_exit_action()
        self.finish()
        self.enable()
        self.pump()
        self.assertFalse(self.app._exit_dialog_open)

    def test_save_enabled_float_creates_it_with_saved_opacity_without_preview_opening(self):
        dialog = self.settings()
        self.preview(dialog, 75, 35)
        self.assertIsNone(self.app.float_bar)
        dialog.float_bar_var.set(True)
        dialog.float_bar_mode_segment.set('迷你')
        dialog.completion_action_segment.set('退出下载器')
        dialog._on_save()
        self.pump()
        self.assertEqual(self.app.float_bar._opacity, 35)
        self.assertEqual(self.app.float_bar._mode, 'mini')
        self.assertTrue(self.app.float_bar._last_push_succeeded)
        self.assertTrue(ConfigManager(self.config.config_path).after_download['exit_app'])
        self.assertFalse(self.app._exit_dialog_open)

    def test_nonlast_failure_pause_cancel_verify_failure_and_delete_block_round(self):
        self.enable()
        for status in ('failed', 'verify_failed', 'paused', 'cancelled', 'deleted'):
            with self.subTest(status=status):
                failed = self.finish('paused' if status == 'deleted' else status)
                if status == 'deleted':
                    self.manager.delete_task(failed)
                self.finish()
                self.pump()
                self.assertFalse(self.app._exit_dialog_open)
                self.assertEqual(self.app._exit_round_ids, set())

    def test_new_success_after_failed_round_ignores_old_history(self):
        self.enable()
        self.finish('failed')
        self.pump()
        self.finish()
        self.pump()
        self.assertTrue(self.app._exit_dialog_open)
        self.assertIn('10 秒', self.app._exit_dialog.count_label.cget('text'))
        self.assertEqual(float(self.app._exit_dialog.attributes('-alpha')), 1.0)
        self.app._exit_dialog.destroy()
        self.pump()
        self.assertFalse(self.app._exit_dialog_open)

    def test_cancel_and_duplicate_delayed_events_do_not_repeat(self):
        self.enable()
        task_id = self.finish()
        generation = self.manager.get_activity_state()[0]
        self.pump()
        self.app._exit_dialog.destroy()
        self.app._on_task_started(task_id, generation)
        self.app._on_task_status_changed(task_id, 'completed', '')
        self.pump()
        self.assertFalse(self.app._exit_dialog_open)
        self.finish()
        self.pump()
        self.assertTrue(self.app._exit_dialog_open)
        # 直接退出（例如托盘入口）而不是点击确认窗；子窗定时器仍在队列中。
        self.app._exit_app()
        self.assertTrue(self.app._exiting)
        self.assertTrue(self.manager._shutdown_started)

    def test_instant_new_task_invalidates_old_confirmation_even_when_idle(self):
        self.enable()
        self.finish()
        self.pump()
        self.assertTrue(self.app._exit_still_valid())
        self.finish()
        # 事件已收敛、数据库已空闲：不能靠队列非空或“还在下载”蒙混过关。
        while True:
            try:
                callback, args = self.app._ui_events.get_nowait()
            except Empty:
                break
            callback(*args)
        self.assertFalse(self.app._exit_still_valid())
        old_generation = self.app._exit_snapshot[1]
        with patch.object(self.manager, 'get_activity_state', return_value=(old_generation, False)):
            # 反向控制：忽略代数就会误判旧确认有效，证明这项门禁不可删除。
            self.assertTrue(self.app._exit_still_valid())
        with patch.object(self.app, '_exit_app') as exit_app:
            self.app._exit_dialog._shutdown_now()
        exit_app.assert_not_called()
        self.pump()
        self.assertTrue(self.app._exit_dialog_open)
        self.app._exit_dialog.destroy()

    def test_action_toggle_invalidates_confirmation_but_opacity_only_keeps_round(self):
        self.enable()
        self.finish()
        self.pump()
        self.config.ui_window_opacity = 70
        self.app._on_settings_saved(self.config.get_all())
        self.pump()
        self.assertTrue(self.app._exit_still_valid())
        self.config.after_download = {}
        self.app._on_settings_saved(self.config.get_all())
        self.config.after_download = {'exit_app': True}
        self.app._on_settings_saved(self.config.get_all())
        self.assertFalse(self.app._exit_still_valid())
        self.app._exit_dialog.destroy()
        self.pump()
        self.assertFalse(self.app._exit_dialog_open)

    def test_modal_and_background_work_delay_confirmation(self):
        self.enable()
        dialog = self.settings()
        self.finish()
        self.pump()
        self.assertFalse(self.app._exit_dialog_open)
        self.app._batch_add_pending = 1
        dialog.destroy()
        self.pump()
        self.assertFalse(self.app._exit_dialog_open)
        self.app._batch_add_pending = 0
        self.pump()
        self.assertTrue(self.app._exit_dialog_open)
        self.app._exit_dialog.destroy()

    def test_database_failure_and_final_generation_change_prevent_exit(self):
        self.enable()
        self.finish()
        self.pump()
        with patch.object(self.manager, 'get_task', side_effect=RuntimeError('read failure')):
            self.assertFalse(self.app._exit_still_valid())
        real_gate = self.manager.begin_idle_shutdown
        def raced(generation, ids):
            self.manager._activity_generation += 1
            return real_gate(generation, ids)
        with patch.object(self.manager, 'begin_idle_shutdown', side_effect=raced), \
                patch.object(self.app, '_exit_app') as exit_app:
            self.app._exit_dialog._shutdown_now()
        exit_app.assert_not_called()
        self.assertFalse(self.manager._shutdown_started)

    def test_new_modal_cancels_existing_exit_and_preserves_unsaved_settings(self):
        self.enable()
        self.finish()
        self.pump()
        confirmation = self.app._exit_dialog
        dialog = self.settings()
        self.preview(dialog)
        self.assertFalse(self.app._exit_still_valid())
        with patch.object(self.app, '_exit_app') as exit_app:
            confirmation._shutdown_now()
        exit_app.assert_not_called()
        self.assertTrue(dialog.winfo_exists())
        dialog.destroy()
        self.pump()
        self.assertFalse(self.app._exit_dialog_open)

    def test_real_http_download_and_immediate_exit_use_normal_cleanup_without_shutdown(self):
        self.enable()
        self.config.set('close_behavior', 'minimize')
        self.app.browser_bridge = Mock()
        self.app._set_float_bar_visible(True)
        bar = self.app.float_bar
        task_id = self.manager.add_task(self.base_url + '/range')
        deadline = time.monotonic() + 15
        while not self.app._exit_dialog_open and time.monotonic() < deadline:
            self.pump(0.1)
        self.assertEqual(self.db.get_task(task_id)['status'], 'completed')
        self.assertTrue(self.app._exit_dialog_open)
        saved = Path(self.db.get_task(task_id)['save_path']).read_bytes()
        with patch.object(self.app.tray_manager, 'stop') as stop, \
                patch('downloader.ui.main_window.subprocess.Popen') as popen:
            self.app._exit_dialog._shutdown_now()
        self.assertTrue(self.app._exiting)
        self.assertTrue(self.manager._shutdown_started)
        stop.assert_called_once()
        self.app.browser_bridge.close.assert_called_once()
        self.assertTrue(bar._destroyed)
        popen.assert_not_called()
        self.assertEqual(Path(self.db.get_task(task_id)['save_path']).read_bytes(), saved)

    def test_countdown_and_immediate_actions_share_validity_check(self):
        calls = []
        allowed = [True]
        dialog = windows.ExitConfirmDialog(self.app, on_exit=lambda: calls.append('exit'),
                                           countdown=1, can_exit=lambda: allowed[0])
        allowed[0] = False
        self.pump(1.15)
        self.assertEqual(calls, [])
        self.assertFalse(dialog.winfo_exists())
        dialog = windows.ExitConfirmDialog(self.app, on_exit=lambda: calls.append('exit'),
                                           countdown=1, can_exit=lambda: True)
        self.pump(1.15)
        self.assertEqual(calls, ['exit'])
        self.assertFalse(dialog.winfo_exists())


if __name__ == '__main__':
    unittest.main()
