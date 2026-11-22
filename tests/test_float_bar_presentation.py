"""悬浮条只读展示口径与悬停生命周期；所有数据均位于临时目录。"""
import gc
import os
from collections import deque
from pathlib import Path
import tempfile
import time
import tkinter as tk
import traceback
from types import SimpleNamespace
from typing import Optional
import unittest

from downloader.database.db_manager import DatabaseManager
from downloader.ui.float_bar import (FloatBar, THEMES, _hint_text, _remaining_text,
                                     _speed_parts, bar_progress, render_bar)
from downloader.utils.config import ConfigManager


class FloatingTaskPresentationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='float-presentation-')
        self.addCleanup(self.temp.cleanup)
        self.db = DatabaseManager(str(Path(self.temp.name) / 'test.db'))
        self.bar = FloatBar.__new__(FloatBar)
        self.bar.task_manager = SimpleNamespace(
            db=self.db,
            get_downloading_tasks=lambda: self.db.get_all_tasks(status='downloading'),
            get_pending_tasks=lambda: self.db.get_all_tasks(status='pending'))
        self.bar._samples = deque()

    def task(self, task_id, status, *, downloaded=0, total: Optional[int] = 100, speed=0):
        self.assertTrue(self.db.create_task(task_id, 'https://fixture.invalid/file',
                                           task_id + '.bin', str(Path(self.temp.name) / task_id),
                                           total_size=total or 0, support_range=False, thread_count=1))
        if total is None:
            connection = self.db._get_connection()
            try:
                with connection:
                    connection.execute('UPDATE download_tasks SET total_size = NULL WHERE task_id = ?',
                                       (task_id,))
            finally:
                connection.close()
        self.db.update_task_status(task_id, status)
        self.db.update_task_progress(task_id, downloaded, speed)

    def test_total_rate_and_current_remaining_use_distinct_tasks(self):
        self.task('first', 'downloading', downloaded=4_000_000, total=10_000_000, speed=1_000_000)
        self.task('second', 'downloading', speed=9_000_000)
        self.task('queued', 'pending')
        self.bar._refresh_state()
        state = self.bar._state
        self.assertEqual(state['speed'], 10_000_000)
        self.assertEqual(state['current_speed'], 1_000_000)
        self.assertEqual((state['downloading'], state['pending']), (2, 1))
        self.assertEqual(state['filename'], 'first.bin')
        self.assertAlmostEqual(bar_progress(state), 0.4)
        self.assertEqual(_remaining_text(state), '00:06')

    def test_stalled_current_task_cannot_borrow_another_tasks_speed(self):
        self.task('stalled', 'downloading', downloaded=40, speed=0)
        self.task('moving', 'downloading', speed=90)
        self.bar._refresh_state()
        self.assertEqual(self.bar._state['speed'], 90)
        self.assertEqual(_remaining_text(self.bar._state), '--')

    def test_unknown_and_zero_totals_do_not_claim_zero_percent(self):
        for total in (None, 0):
            with self.subTest(total=total):
                self.task('unknown', 'downloading', total=total, downloaded=50, speed=10)
                self.bar._refresh_state()
                state = self.bar._state
                self.assertEqual(state['status'], 'downloading')
                self.assertEqual(state['total'], total)
                self.assertEqual(bar_progress(state), 0)
                self.assertIn('下载中 · --', _hint_text(state))
                self.assertEqual(_remaining_text(state), '--')
                self.db.delete_task('unknown')

    def test_verification_is_not_mistaken_for_waiting_or_completion(self):
        self.task('queued', 'pending')
        self.task('checking', 'verifying', downloaded=100)
        self.bar._refresh_state()
        state = self.bar._state
        self.assertEqual(state['status'], 'verifying')
        self.assertEqual(state['filename'], 'checking.bin')
        self.assertEqual(state['speed'], 0)
        self.assertIn('校验中 · 100.0%', _hint_text(state))
        self.assertEqual(_remaining_text(state), '--')

    def test_paused_tasks_keep_progress_without_stale_speed_or_eta(self):
        self.task('paused', 'paused', downloaded=45, speed=999)
        self.task('history', 'completed', downloaded=100)
        self.bar._refresh_state()
        state = self.bar._state
        self.assertEqual(state['speed'], 0)
        self.assertEqual(state['current_speed'], 0)
        self.assertAlmostEqual(bar_progress(state), 0.45)
        self.assertIn('已暂停 · 45.0%', _hint_text(state))
        self.assertEqual(_remaining_text(state), '--')

    def test_failed_downloads_and_verification_failures_remain_visible(self):
        for status, label in (('failed', '下载失败'), ('verify_failed', '校验失败')):
            with self.subTest(status=status):
                self.task('failed', status, downloaded=30)
                self.bar._refresh_state()
                self.assertIn(label + ' · 30.0%', _hint_text(self.bar._state))
                self.assertEqual(_remaining_text(self.bar._state), '--')
                self.db.delete_task('failed')

    def test_completed_and_cancelled_history_leave_widget_ready(self):
        self.task('finished', 'completed', downloaded=100, speed=99)
        self.task('cancelled', 'cancelled', downloaded=40, speed=50)
        self.bar._refresh_state()
        self.assertEqual(self.bar._state['speed'], 0)
        self.assertEqual(self.bar._state['filename'], '')
        self.assertIn('就绪', _hint_text(self.bar._state))
        self.assertIn('暂无下载任务', _hint_text(self.bar._state))

    def test_speed_units_keep_the_actual_rate_in_compact_text(self):
        for speed, expected in ((512, ('512', 'B/s')), (1536, ('1.50', 'KB/s')),
                                (1024**2, ('1.00', 'MB/s')), (10 * 1024**3, ('10.00', 'GB/s'))):
            with self.subTest(speed=speed):
                self.assertEqual(_speed_parts(speed), expected)

    def test_inactive_recent_history_does_not_look_like_live_transfer(self):
        theme = THEMES['dark']
        accent = tuple(int(theme['accent'][i:i+2], 16) for i in (1, 3, 5))
        for status in ('idle', 'paused'):
            with self.subTest(status=status):
                image = render_bar(mode='normal', theme=theme,
                                   state=dict(status=status, speed=0, downloaded=40, total=100),
                                   samples=[(1, 100), (2, 50), (3, 80)], hovered=False)
                chart = image.crop((14, 87, 185, 110))
                raw = chart.tobytes()
                active_pixels = sum(raw[offset + 3] > 200 and
                                    all(abs(raw[offset + i] - accent[i]) <= 12 for i in range(3))
                                    for offset in range(0, len(raw), 4))
                self.assertEqual(active_pixels, 0)


@unittest.skipUnless(os.name == 'nt', '悬浮条原生窗口需要 Windows 桌面')
class FloatingHintGuiTests(unittest.TestCase):
    def setUp(self):
        self.gc_was_enabled = gc.isenabled()
        gc.collect()
        gc.disable()
        self.temp = tempfile.TemporaryDirectory(prefix='float-hint-')
        root = Path(self.temp.name)
        self.config = ConfigManager(str(root / 'config.json'))
        self.config.set('ui_float_bar_pos', [80, 80])
        self.db = DatabaseManager(str(root / 'test.db'))
        manager = SimpleNamespace(db=self.db,
                                 get_downloading_tasks=lambda: self.db.get_all_tasks(status='downloading'),
                                 get_pending_tasks=lambda: self.db.get_all_tasks(status='pending'))
        self.root = tk.Tk()
        self.root.withdraw()
        self.errors = []
        self.root.report_callback_exception = lambda *args: self.errors.append(
            ''.join(traceback.format_exception(*args)))
        self.bar = FloatBar(self.root, manager, self.config)
        self.bar._cancel_tick()
        self.root.update()

    def tearDown(self):
        self.bar.destroy()
        self.root.destroy()
        self.temp.cleanup()
        gc.collect()
        if self.gc_was_enabled:
            gc.enable()
        self.assertEqual(self.errors, [])

    def pump(self, seconds):
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            self.root.update()
            time.sleep(0.005)

    def show_hint(self):
        self.bar._on_enter(None)
        deadline = time.monotonic() + 2
        while self.bar._hint_window is None and time.monotonic() < deadline:
            self.pump(0.02)
        hint = self.bar._hint_window
        if hint is None:
            raise AssertionError('悬停说明未显示')
        self.assertTrue(hint.winfo_ismapped())
        return hint

    def test_hint_explains_current_task_and_disappears_before_drag(self):
        hint = self.show_hint()
        self.assertTrue(self.bar.window.winfo_ismapped())
        start_x, start_y = self.bar._pos
        self.bar._on_press(SimpleNamespace(x_root=100, y_root=100))
        self.assertIsNone(self.bar._hint_window)
        self.assertFalse(hint.winfo_exists())
        self.bar._on_motion(SimpleNamespace(x_root=140, y_root=160))
        self.bar._on_enter(None)
        self.assertEqual(self.bar.window.cget('cursor'), 'fleur')
        self.bar._on_release(None)
        self.assertEqual(self.bar._pos, (start_x + 40, start_y + 60))
        self.assertEqual(self.config.get('ui_float_bar_pos'), list(self.bar._pos))

    def test_hide_cancels_pending_hint_and_visible_hint_without_changing_preference(self):
        self.config.ui_float_bar_enabled = True
        self.bar._on_enter(None)
        self.bar.hide()
        self.pump(0.65)
        self.assertIsNone(self.bar._hint_window)
        self.bar.show()
        self.bar._cancel_tick()
        self.show_hint()
        self.bar.hide()
        self.pump(0.05)
        self.assertIsNone(self.bar._hint_window)
        self.assertFalse(self.bar.window.winfo_ismapped())
        self.assertTrue(self.config.ui_float_bar_enabled)


if __name__ == '__main__':
    unittest.main()
