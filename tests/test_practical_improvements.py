# -*- coding: utf-8 -*-
"""四项实用改进：F 原因展示 / R 重试边界 / C 暂停分类 / P 进度显示。

全部使用独立临时 SQLite、配置、分块与本地 HTTP；不接触真实 data/dist。
"""
import gc
import os
import re
import subprocess
import sys
import tempfile
import threading
import time
import traceback
import unittest
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

import customtkinter as ctk

from downloader.core.chunk_downloader import (
    ChunkDownloader, RetryBudget, parse_retry_after,
    RETRY_BUDGET_MESSAGE, RETRY_SERVER_WAIT_MESSAGE)
from downloader.core.download_engine import DownloadEngine
from downloader.core.task_manager import TaskManager
from downloader.database.db_manager import DatabaseManager
from downloader.ui.main_window import MainWindow
from downloader.ui.task_presentation import (
    describe_task_problem, format_task_progress, is_empty_file_completed,
    sanitize_problem_text)
from downloader.utils.config import ConfigManager
from downloader.utils.file_utils import format_progress_texts


PAYLOAD = bytes(range(256)) * 8  # 2048 字节，够两个分块


# ---------------------------------------------------------------- F / P 纯函数

class PresentationTests(unittest.TestCase):
    def test_http_403_is_explained_without_echoing_secrets(self):
        raw = ('Authorization: Bearer secret-token-value Cookie: session=abc; theme=dark '
               'GET https://user:pass@private.example.com:8443/a?signature=deadbeef '
               'from C:\\Users\\alice\\Downloads\\file.bin at 192.168.1.9'
               'HTTP 403')
        reason, advice = describe_task_problem('failed', raw)
        self.assertEqual(reason, '服务器拒绝访问（HTTP 403）')
        self.assertIn('授权', advice)
        for secret in ('secret-token-value', 'session=abc', 'user:pass',
                       'private.example.com', 'signature=deadbeef', 'C:\\Users',
                       '192.168.1.9'):
            self.assertNotIn(secret, reason + advice)
        sanitized = sanitize_problem_text(raw)
        for secret in ('secret-token-value', 'session=abc', 'private.example.com',
                       'signature=deadbeef', 'C:\\Users', '192.168.1.9'):
            self.assertNotIn(secret, sanitized)

    def test_unknown_exception_uses_fixed_chinese_fallback(self):
        for raw in ('Traceback <object at 0x1234> https://host/token=abc',
                    'boom\nsecond line', ''):
            reason, advice = describe_task_problem('failed', raw)
            self.assertTrue(reason)
            self.assertTrue(advice)
            self.assertNotIn('host', reason + advice)
            self.assertNotIn('token', reason + advice)
            self.assertNotIn('Traceback', reason + advice)

    def test_manual_pause_and_restart_messages_do_not_show_problem(self):
        for message in ('', '已暂停', '程序重启自动纠偏：下载状态已重置为暂停',
                        '程序退出自动暂停'):
            self.assertEqual(describe_task_problem('paused', message), ('', ''))

    def test_space_paused_and_verify_failed_show_problem(self):
        reason, advice = describe_task_problem(
            'paused', '下载目录空间不足：需要 2.00 GB，剩余 1.00 GB')
        self.assertEqual(reason, '磁盘空间不足')
        self.assertIn('释放', advice)
        self.assertEqual(describe_task_problem('verify_failed', '') [0], '文件校验未通过')

    def test_active_states_never_reuse_old_reason(self):
        for status in ('pending', 'downloading', 'verifying', 'completed', 'cancelled'):
            self.assertEqual(describe_task_problem(status, 'HTTP 403'), ('', ''))

    def test_retry_budget_messages_are_recognized(self):
        self.assertEqual(describe_task_problem('failed', RETRY_BUDGET_MESSAGE)[0],
                         '自动重试已结束')
        self.assertEqual(describe_task_problem('failed', RETRY_SERVER_WAIT_MESSAGE)[0],
                         '服务器要求较长等待')

    def test_progress_rules_for_unknown_known_and_empty(self):
        self.assertEqual(format_task_progress(100, 0, 'downloading'), (0.0, '--'))
        self.assertEqual(format_task_progress(0, 0, 'pending'), (0.0, '--'))
        self.assertEqual(format_task_progress(50, 200, 'downloading'), (0.25, '25.0%'))
        self.assertEqual(format_task_progress(300, 200, 'downloading'), (1.0, '100.0%'))
        self.assertEqual(format_task_progress(0, 0, 'completed'), (1.0, '已完成'))
        # 0 不代表空文件完成，除非状态确实 completed
        self.assertEqual(format_task_progress(0, 0, 'failed'), (0.0, '--'))
        self.assertEqual(format_task_progress(5, 0, 'completed'), (0.0, '--'))

    def test_empty_file_size_line_and_speed(self):
        self.assertEqual(format_progress_texts(0, 0, 123.0, 'completed'),
                         ('已完成 · 0 B', '剩余 --', '0 B/s'))
        self.assertEqual(format_progress_texts(0, 0, 123.0, 'paused')[0],
                         '已下载 0 B / 未知')

    def test_b05_invalid_downloaded_keeps_valid_total(self):
        """B05：转换失败不得把有效 total 一并归零，也不得冒充空文件完成。"""
        for value in ('bad', '', ' ', -0.5, float('nan'), float('inf'), -1, '-1',
                      '1.5', '1e3', None, [0]):
            with self.subTest(value=value):
                self.assertEqual(format_task_progress(value, 100, 'completed'),
                                 (0.0, '0.0%'))
                self.assertEqual(format_task_progress(value, 0, 'completed'), (0.0, '--'))
                self.assertNotEqual(format_progress_texts(value, 0, 0, 'completed')[0],
                                    '已完成 · 0 B')
                self.assertFalse(is_empty_file_completed(value, 0, 'completed'))

    def test_b05_invalid_total_is_not_empty_completion(self):
        for value in ('bad', '', -0.5, float('nan'), float('inf'), -1, '-1',
                      '1.5', '1e3', None, [0]):
            with self.subTest(value=value):
                self.assertEqual(format_task_progress(0, value, 'completed'), (0.0, '--'))
                self.assertFalse(is_empty_file_completed(0, value, 'completed'))

    def test_b05_real_zero_and_contradictory_sizes(self):
        self.assertEqual(format_task_progress(0, 0, 'completed'), (1.0, '已完成'))
        self.assertEqual(format_task_progress('0', '0', 'completed'), (1.0, '已完成'))
        self.assertTrue(is_empty_file_completed(0, 0, 'completed'))
        # 矛盾大小/非终态不得冒充空文件
        self.assertEqual(format_task_progress(0, 0, 'failed'), (0.0, '--'))
        self.assertEqual(format_task_progress(1, 0, 'completed'), (0.0, '--'))
        self.assertFalse(is_empty_file_completed(1, 0, 'completed'))
        self.assertFalse(is_empty_file_completed(0, 1, 'completed'))


# ---------------------------------------------------------------- R 解析与调度

class RetryAfterParseTests(unittest.TestCase):
    def test_seconds_and_whitespace(self):
        self.assertEqual(parse_retry_after('0'), 0.0)
        self.assertEqual(parse_retry_after('  7  '), 7.0)

    def test_http_dates_use_utc(self):
        now = datetime(2026, 10, 22, 10, 0, 0, tzinfo=timezone.utc)
        future = (now + timedelta(seconds=90)).strftime('%a, %d %b %Y %H:%M:%S GMT')
        past = (now - timedelta(seconds=90)).strftime('%a, %d %b %Y %H:%M:%S GMT')
        self.assertAlmostEqual(parse_retry_after(future, now), 90.0, places=1)
        self.assertEqual(parse_retry_after(past, now), 0.0)

    def test_invalid_values_fall_back(self):
        for value in (None, '', '-1', '1.5', 'NaN', '1 2', 'soon', '1e3'):
            self.assertIsNone(parse_retry_after(value), value)

    def test_huge_valid_number_is_not_truncated(self):
        self.assertGreater(parse_retry_after('9' * 40), 300)

    def test_leading_zeros_and_long_invalid_headers_are_safe(self):
        self.assertEqual(parse_retry_after('0' * 5000), 0.0)
        self.assertEqual(parse_retry_after('0' * 5000 + '7'), 7.0)
        self.assertGreater(parse_retry_after('9' * 5000), 300)
        self.assertIsNone(parse_retry_after('²' * 5000))
        self.assertIsNone(parse_retry_after('invalid ' * 1000))


class _FakeClock:
    def __init__(self, start=1000.0):
        self.now = start

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.now += max(0.0, float(seconds))


class RetryScheduleTests(unittest.TestCase):
    def _downloader(self, clock, budget=None, not_before=0.0):
        with tempfile.TemporaryDirectory() as temp:
            path = os.path.join(temp, 'part0')
            return ChunkDownloader(0, 'task', 'http://example.invalid/x', 0, 9, path,
                                   retry_times=2, retry_budget=budget,
                                   retry_not_before=not_before, clock=clock)

    def _wait(self, downloader, attempt=1):
        clock = downloader._clock
        with patch('downloader.core.chunk_downloader.time.sleep',
                   side_effect=clock.sleep):
            return downloader._wait_before_retry(attempt)

    def test_wait_uses_max_of_backoff_and_retry_after(self):
        clock = _FakeClock()
        budget = RetryBudget(300, clock)
        budget.note_transient()
        downloader = self._downloader(clock, budget, not_before=clock() + 2.0)
        start = clock()
        self.assertTrue(self._wait(downloader, 1))
        self.assertAlmostEqual(clock() - start, 2.0, places=3)

    def test_first_request_also_waits_for_not_before(self):
        clock = _FakeClock()
        budget = RetryBudget(300, clock)
        budget.note_transient()
        downloader = self._downloader(clock, budget, not_before=clock() + 2.0)
        start = clock()
        with patch('downloader.core.chunk_downloader.time.sleep',
                   side_effect=clock.sleep):
            self.assertTrue(downloader._wait_for_retry_not_before())
        self.assertAlmostEqual(clock() - start, 2.0, places=3)

    def test_cancel_during_wait_sends_no_extra_request(self):
        clock = _FakeClock()
        budget = RetryBudget(300, clock)
        budget.note_transient()
        downloader = self._downloader(clock, budget, not_before=clock() + 30)
        downloader.cancel()
        start = clock()
        with patch('downloader.core.chunk_downloader.time.sleep',
                   side_effect=clock.sleep):
            self.assertFalse(downloader._wait_for_retry_not_before())
        self.assertEqual(clock(), start)

    def test_server_wait_beyond_window_ends_retry(self):
        clock = _FakeClock()
        budget = RetryBudget(300, clock)
        budget.note_transient()
        downloader = self._downloader(clock, budget, not_before=clock() + 3600)
        start = clock()
        self.assertFalse(self._wait(downloader, 1))
        self.assertEqual(clock(), start)
        self.assertEqual(downloader.error_message(), RETRY_SERVER_WAIT_MESSAGE)

    def test_window_expired_ends_retry_with_budget_message(self):
        clock = _FakeClock()
        budget = RetryBudget(1, clock)
        budget.note_transient()
        clock.now += 2
        downloader = self._downloader(clock, budget)
        self.assertFalse(self._wait(downloader, 1))
        self.assertEqual(downloader.error_message(), RETRY_BUDGET_MESSAGE)


class _RetryHandler(BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'

    def log_message(self, *args):
        pass

    def do_GET(self):
        self.server.requests.append((time.monotonic(), self.path,
                                     self.headers.get('Range')))
        self._retry_after_single()

    def _retry_after_single(self):
        self.server.attempts += 1
        if self.server.attempts == 1:
            self.send_response(429)
            self.send_header('Retry-After', '2')
            self.send_header('Content-Length', '0')
            self.end_headers()
            return
        body = PAYLOAD
        self.send_response(206)
        self.send_header('Content-Type', 'application/octet-stream')
        self.send_header('Content-Range', f'bytes 0-{len(body) - 1}/{len(body)}')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)


class RetryHttpTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(('127.0.0.1', 0), _RetryHandler)
        cls.server.requests = []
        cls.server.attempts = 0
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.base = f'http://127.0.0.1:{cls.server.server_port}'

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(2)

    def setUp(self):
        self.server.requests.clear()
        self.server.attempts = 0
        self.temp = tempfile.TemporaryDirectory(prefix='practical-retry-')

    def tearDown(self):
        self.temp.cleanup()

    def test_real_short_retry_after_delays_second_get(self):
        clock = time.monotonic
        budget = RetryBudget(300, clock)
        downloader = ChunkDownloader(
            0, 'task', self.base + '/retry', 0, len(PAYLOAD) - 1,
            os.path.join(self.temp.name, 'part0'), timeout=3, retry_times=2,
            retry_budget=budget)
        self.assertTrue(downloader.download())
        # 排除探测：这里没有探测，只应有一次 429 + 一次 206
        self.assertEqual(len(self.server.requests), 2)
        first, second = self.server.requests
        self.assertGreaterEqual(second[0] - first[0], 1.9)


# ---------------------------------------------------------------- F / C / P GUI

class PracticalGuiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(('127.0.0.1', 0), _RetryHandler)
        cls.server.requests = []
        cls.server.attempts = 0
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.base = f'http://127.0.0.1:{cls.server.server_port}'

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(2)

    def setUp(self):
        self.gc_was_enabled = gc.isenabled()
        gc.collect()
        gc.disable()
        self.temp = tempfile.TemporaryDirectory(prefix='practical-gui-')
        self.root = Path(self.temp.name)
        self.env_patch = patch.dict(os.environ, {'NO_PROXY': '*', 'no_proxy': '*'})
        self.env_patch.start()
        self.config = ConfigManager(str(self.root / 'config.json'))
        self.config.download_dir = str(self.root / 'out')
        self.config.set('temp_dir', str(self.root / 'parts'))
        self.config.set('timeout', 2)
        self.config.set('retry_times', 1)
        self.db = DatabaseManager(str(self.root / 'downloads.db'))
        self.engine = DownloadEngine(self.db, self.config)
        self.manager = TaskManager(self.engine, self.db, max_concurrent=1)
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
                self.app.tk.call('after', 'cancel', timer)
            self.app._exit_app()
        finally:
            for item in reversed(self.patches):
                item.stop()
            self.env_patch.stop()
            self.manager.shutdown()
            self.app = None
            self.temp.cleanup()
            gc.collect()
            if self.gc_was_enabled:
                gc.enable()
        self.assertEqual(self.callback_errors, [])

    def pump(self, seconds=0.1):
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            self.app.update()
            time.sleep(0.01)

    def inject(self, task_id, filename='a.bin', status='pending', total=0):
        self.db.create_task_with_chunks(task_id, f'http://example.com/{filename}', filename,
                                        str(self.root / filename), total_size=total,
                                        status=status)
        self.app._add_task_widget(self.db.get_task(task_id))
        return task_id

    def test_failed_reason_card_detail_and_clear_on_new_status(self):
        task_id = self.inject('t-fail')
        secret = ('Authorization: Bearer top-secret Cookie: sid=abc '
                  'https://private.example.com/a?token=zzz HTTP 403')
        self.db.update_task_status(task_id, 'failed', secret)
        self.app._update_task_status(task_id, 'failed', secret)
        self.pump(0.1)
        widgets = self.app.task_widgets[task_id]
        text = widgets['reason_label'].cget('text')
        self.assertIn('HTTP 403', text)
        self.assertTrue(widgets['reason_label'].winfo_manager())
        for value in ('top-secret', 'sid=abc', 'private.example.com', 'token=zzz'):
            self.assertNotIn(value, text)

        self.app._on_open_detail(task_id)
        self.pump(0.2)
        window = self.app._detail_windows[task_id]
        self.assertTrue(window.problem_frame.winfo_manager())
        self.assertIn('HTTP 403', window.problem_reason_label.cget('text'))
        self.assertNotIn('top-secret', window.problem_advice_label.cget('text'))

        self.db.update_task_status(task_id, 'downloading', None)
        self.app._update_task_status(task_id, 'downloading', '')
        self.pump(0.1)
        self.assertFalse(widgets['reason_label'].winfo_manager())
        self.assertFalse(window.problem_frame.winfo_manager())

    def test_manual_pause_and_recover_show_no_problem(self):
        task_id = self.inject('t-pause')
        self.db.update_task_status(task_id, 'paused', '已暂停')
        self.app._update_task_status(task_id, 'paused', '已暂停')
        self.pump(0.1)
        self.assertFalse(self.app.task_widgets[task_id]['reason_label'].winfo_manager())

    def test_space_paused_is_visible_in_paused_category(self):
        task_id = self.inject('t-space')
        self.inject('t-run', 'b.bin', status='downloading')
        self.inject('t-wait', 'c.bin', status='pending')
        self.db.update_task_status(task_id, 'paused', '下载目录空间不足：需要 2 GB，剩余 1 GB')
        self.app._update_task_status(task_id, 'paused', '下载目录空间不足：需要 2 GB，剩余 1 GB')
        self.pump(0.1)
        self.assertIn('已暂停 (1)', self.app.category_buttons['status:paused'].cget('text'))
        self.app._on_category_select('status:paused')
        self.pump(0.1)
        self.assertTrue(self.app.task_widgets[task_id]['frame'].winfo_manager())
        self.assertFalse(self.app.task_widgets['t-run']['frame'].winfo_manager())
        self.assertFalse(self.app.task_widgets['t-wait']['frame'].winfo_manager())
        self.assertIn('磁盘空间不足',
                      self.app.task_widgets[task_id]['reason_label'].cget('text'))
        self.assertEqual(ConfigManager(self.config.config_path).ui_active_category,
                         'status:paused')

    def test_zero_byte_completed_card_and_detail_stay_consistent(self):
        task_id = self.inject('t-empty', status='completed', total=0)
        self.app._update_task_status(task_id, 'completed', '下载完成')
        self.app._update_task_progress(task_id, 0, 0, 0)  # 迟到事件不能倒退
        self.pump(0.1)
        widgets = self.app.task_widgets[task_id]
        self.assertEqual(widgets['progress_label'].cget('text'), '已完成')
        self.assertEqual(widgets['progress_bar'].get(), 1.0)
        self.assertEqual(widgets['size_label'].cget('text'), '已完成 · 0 B')
        self.assertEqual(self.db.get_task(task_id)['total_size'], 0)

        self.app._on_open_detail(task_id)
        self.pump(0.2)
        window = self.app._detail_windows[task_id]
        self.assertEqual(window.percent_label.cget('text'), '已完成')
        self.assertIn('已完成 · 0 B', window.stats_label.cget('text'))

    def test_zero_byte_progress_before_status_event(self):
        task_id = self.inject('t-empty2', status='pending', total=0)
        self.app._update_task_progress(task_id, 0, 0, 0)
        self.assertEqual(self.app.task_widgets[task_id]['progress_label'].cget('text'), '--')
        self.db.update_task_status(task_id, 'completed', None)
        self.app._update_task_status(task_id, 'completed', '下载完成')
        self.pump(0.1)
        widgets = self.app.task_widgets[task_id]
        self.assertEqual(widgets['progress_label'].cget('text'), '已完成')
        self.assertEqual(widgets['size_label'].cget('text'), '已完成 · 0 B')

    def test_detail_ignores_late_progress_for_known_completed(self):
        task_id = self.inject('t-known', status='completed', total=100)
        self.db.update_task_progress(task_id, 100, 0)
        self.app._update_task_status(task_id, 'completed', '下载完成')
        self.app._on_open_detail(task_id)
        self.pump(0.2)
        window = self.app._detail_windows[task_id]
        self.assertEqual(window.percent_label.cget('text'), '100.0%')
        window.update_progress(50, 100, 1024)
        self.assertEqual(window.percent_label.cget('text'), '100.0%')

    def test_unknown_size_stays_unknown_until_completion_event(self):
        task_id = self.inject('t-unknown', status='downloading', total=0)
        self.app._update_task_status(task_id, 'downloading', '')
        self.app._update_task_progress(task_id, 100, 0, 2048)
        self.pump(0.1)
        widgets = self.app.task_widgets[task_id]
        self.assertEqual(widgets['progress_label'].cget('text'), '--')
        self.assertEqual(widgets['progress_bar'].get(), 0.0)
        self.assertEqual(widgets['eta_label'].cget('text'), '剩余 --')
        self.assertIn('未知', widgets['size_label'].cget('text'))
        self.assertIn('2.00 KB/s', widgets['speed_label'].cget('text'))

    # ---------------- B03：库状态权威，旧 UI 事件不得覆盖 ----------------

    def _completed_card_with_detail(self, task_id, downloaded, total):
        self.db.update_task_status(task_id, 'completed')
        self.db.update_task_progress(task_id, downloaded, 0)
        self.app._update_task_status(task_id, 'completed', '下载完成')
        self.pump(0.1)
        self.app._on_open_detail(task_id)
        self.pump(0.2)
        return self.app._detail_windows[task_id]

    def test_b03_stale_downloading_and_progress_cannot_revert_completion(self):
        task_id = self.inject('t-b03', status='pending', total=100)
        window = self._completed_card_with_detail(task_id, 100, 100)
        widgets = self.app.task_widgets[task_id]
        self.assertEqual(widgets['progress_label'].cget('text'), '100.0%')
        self.assertTrue(widgets['frozen'])

        # 旧 downloading 重试事件 + 旧 0/0 进度，均来自上一轮
        self.app._update_task_status(task_id, 'downloading', '正在重试 2 个分块（第 1 轮）')
        self.pump(0.1)
        self.app._update_task_progress(task_id, 0, 0, 0)
        self.pump(0.1)

        self.assertEqual(widgets['status'], 'completed')
        self.assertEqual(widgets['progress_label'].cget('text'), '100.0%')
        self.assertEqual(widgets['progress_bar'].get(), 1.0)
        self.assertNotIn('重试', widgets['status_label'].cget('text'))
        self.assertTrue(widgets['frozen'])
        self.assertIn('已完成', window.stats_label.cget('text'))
        self.assertEqual(window.percent_label.cget('text'), '100.0%')

    def test_b03_empty_file_completion_survives_stale_event(self):
        task_id = self.inject('t-b03-empty', status='pending', total=0)
        self.db.update_task_status(task_id, 'completed')
        self.app._update_task_status(task_id, 'completed', '下载完成')
        self.pump(0.1)
        widgets = self.app.task_widgets[task_id]
        self.app._update_task_status(task_id, 'downloading', '')
        self.app._update_task_progress(task_id, 0, 0, 0)
        self.pump(0.1)
        self.assertEqual(widgets['progress_label'].cget('text'), '已完成')
        self.assertEqual(widgets['size_label'].cget('text'), '已完成 · 0 B')

    def test_b03_terminal_and_paused_states_ignore_stale_downloading(self):
        for status in ('verify_failed', 'cancelled', 'paused'):
            with self.subTest(status=status):
                task_id = self.inject(f't-b03-{status}', status='pending', total=100)
                self.db.update_task_status(task_id, status, '哈希不匹配' if status == 'verify_failed' else '')
                self.app._update_task_status(task_id, status, '哈希不匹配' if status == 'verify_failed' else '')
                self.pump(0.05)
                widgets = self.app.task_widgets[task_id]
                self.app._update_task_status(task_id, 'downloading', '正在重试 1 个分块（第 1 轮）')
                self.app._update_task_progress(task_id, 0, 100, 0)
                self.pump(0.05)
                self.assertEqual(widgets['status'], status)
                self.assertNotIn('重试', widgets['status_label'].cget('text'))
                self.assertTrue(widgets['status_label'].cget('text').startswith(
                    self.app._get_status_text(status)))

    def test_b03_real_new_run_and_same_status_notice_still_work(self):
        task_id = self.inject('t-b03-run', status='pending', total=100)
        self.db.update_task_status(task_id, 'downloading')
        self.app._update_task_status(task_id, 'downloading', '正在重试 2 个分块（第 1 轮）')
        self.pump(0.1)
        widgets = self.app.task_widgets[task_id]
        self.assertEqual(widgets['status'], 'downloading')
        self.assertIn('重试', widgets['status_label'].cget('text'))
        self.assertFalse(widgets['frozen'])
        self.app._update_task_progress(task_id, 40, 100, 1024)
        self.pump(0.1)
        self.assertEqual(widgets['progress_label'].cget('text'), '40.0%')

    def test_b03_completed_then_new_run_accepts_downloading(self):
        task_id = self.inject('t-b03-rerun', status='pending', total=100)
        self._completed_card_with_detail(task_id, 100, 100)
        widgets = self.app.task_widgets[task_id]
        # 真正的新 run：库先变 downloading，事件与库一致
        self.db.update_task_status(task_id, 'downloading')
        self.app._update_task_status(task_id, 'downloading', '')
        self.pump(0.1)
        self.assertEqual(widgets['status'], 'downloading')
        self.assertFalse(widgets['frozen'])
        self.app._update_task_progress(task_id, 0, 100, 0)
        self.pump(0.1)
        self.assertEqual(widgets['progress_label'].cget('text'), '0.0%')

    def test_b03_detail_ignores_late_progress_after_completion(self):
        task_id = self.inject('t-b03-detail', status='pending', total=100)
        window = self._completed_card_with_detail(task_id, 100, 100)
        # 下载字节不变但 total 变 0 的迟到数据，不能把详情退回未知
        window.update_progress(100, 0, 0)
        self.assertEqual(window.percent_label.cget('text'), '100.0%')
        self.assertIn('100 B / 100 B', window.stats_label.cget('text'))
        window.update_progress(0, 0, 0)
        self.assertEqual(window.percent_label.cget('text'), '100.0%')

    # ---------------- B05：严格校验贯穿真实 UI 入口与缓存 ----------------

    def _assert_invalid_not_empty_completion(self, widgets, label):
        self.assertNotEqual(widgets['progress_label'].cget('text'), '已完成', label)
        self.assertNotEqual(widgets['size_label'].cget('text'), '已完成 · 0 B', label)
        self.assertNotEqual(widgets['progress_bar'].get(), 1.0, label)

    def test_b05_invalid_bytes_through_card_status_and_progress(self):
        cases = [(-0.5, 0), ('bad', 0), (-1, 0), (-0.5, 100), ('bad', 100)]
        for index, (downloaded, total) in enumerate(cases):
            with self.subTest(downloaded=downloaded, total=total):
                task_id = self.inject(f't-b05-{index}', status='pending', total=total)
                self.db.update_task_progress(task_id, downloaded, 0)
                self.db.update_task_status(task_id, 'completed')
                # 状态事件（同时刷新卡片、缓存与详情）
                self.app._update_task_status(task_id, 'completed', '下载完成')
                self.pump(0.05)
                widgets = self.app.task_widgets[task_id]
                self._assert_invalid_not_empty_completion(widgets, f'status {downloaded}/{total}')
                if total == 100:
                    self.assertEqual(widgets['progress_label'].cget('text'), '0.0%')
                    self.assertIn('100 B', widgets['size_label'].cget('text'))
                else:
                    self.assertEqual(widgets['progress_label'].cget('text'), '--')
                    self.assertIn('未知', widgets['size_label'].cget('text'))

                self.app._on_open_detail(task_id)
                self.pump(0.1)
                window = self.app._detail_windows[task_id]
                self.assertNotEqual(window.percent_label.cget('text'), '已完成')
                self.assertNotIn('已完成 · 0 B', window.stats_label.cget('text'))

                # 迟到进度事件也不能把无效值变成满条/空文件完成
                self.db.update_task_status(task_id, 'downloading')
                self.app._update_task_status(task_id, 'downloading', '')
                self.app._update_task_progress(task_id, downloaded, total, 0)
                self.pump(0.05)
                self._assert_invalid_not_empty_completion(widgets, f'progress {downloaded}/{total}')

    def test_b05_invalid_bytes_in_detail_progress_and_recovery(self):
        task_id = self.inject('t-b05-detail', status='downloading', total=100)
        self.db.update_task_status(task_id, 'downloading')
        self.app._update_task_status(task_id, 'downloading', '')
        self.app._on_open_detail(task_id)
        self.pump(0.1)
        window = self.app._detail_windows[task_id]

        window.update_progress('bad', 100, 1024)
        self.assertEqual(window.percent_label.cget('text'), '0.0%')
        self.assertIn('100 B', window.stats_label.cget('text'))
        window.update_progress(-0.5, -1, 1024)
        self.assertEqual(window.percent_label.cget('text'), '--')
        self.assertNotIn('已完成 · 0 B', window.stats_label.cget('text'))

        # 后续真实有效值恢复正常，说明无效值没有被缓存成“已完成 0/0”
        window.update_progress(50, 100, 1024)
        self.assertEqual(window.percent_label.cget('text'), '50.0%')
        self.app._update_task_progress(task_id, 50, 100, 1024)
        self.pump(0.05)
        widgets = self.app.task_widgets[task_id]
        self.assertEqual(widgets['progress_label'].cget('text'), '50.0%')
        self.assertEqual(widgets['downloaded'], 50)
        self.assertEqual(widgets['total'], 100)

    def test_b05_real_zero_still_completes_after_invalid_cache(self):
        task_id = self.inject('t-b05-real-zero', status='downloading', total=100)
        self.db.update_task_status(task_id, 'downloading')
        self.app._update_task_status(task_id, 'downloading', '')
        self.app._update_task_progress(task_id, 'bad', -1, 0)
        self.pump(0.05)
        widgets = self.app.task_widgets[task_id]
        self.assertIsNone(widgets['downloaded'])
        self.assertIsNone(widgets['total'])

        # 真正 0/0 + completed 仍要满条/已完成 · 0 B，不能被上一次无效缓存影响
        self.db.update_task_status(task_id, 'completed')
        # 真实空文件完成：数据库哨兵仍是 total=0 / downloaded=0
        self.db.update_task_size(task_id, 0)
        self.db.update_task_progress(task_id, 0, 0)
        self.app._update_task_status(task_id, 'completed', '下载完成')
        self.pump(0.05)
        self.assertEqual(widgets['progress_label'].cget('text'), '已完成')
        self.assertEqual(widgets['size_label'].cget('text'), '已完成 · 0 B')
        self.assertEqual(widgets['downloaded'], 0)
        self.assertEqual(widgets['total'], 0)

    def test_b05_null_bytes_clear_open_detail_after_valid_empty_completion(self):
        task_id = self.inject('t-b05-null', status='completed', total=0)
        self.app._on_open_detail(task_id)
        self.pump(0.05)
        window = self.app._detail_windows[task_id]
        widgets = self.app.task_widgets[task_id]
        self.assertEqual(window.percent_label.cget('text'), '已完成')

        for downloaded, total in ((None, 0), (0, None), (None, 100)):
            with self.subTest(downloaded=downloaded, total=total):
                self.db.update_task_size(task_id, total)
                self.db.update_task_progress(task_id, downloaded, 0)
                self.app._update_task_status(task_id, 'completed', '下载完成')
                self.pump(0.05)
                self.assertEqual(window._downloaded, widgets['downloaded'])
                self.assertEqual(window._total, widgets['total'])
                self.assertEqual(window.percent_label.cget('text'),
                                 widgets['progress_label'].cget('text'))
                self.assertNotIn('已完成 · 0 B', window.stats_label.cget('text'))
                self._assert_invalid_not_empty_completion(widgets, 'NULL')

        self.db.update_task_size(task_id, 0)
        self.db.update_task_progress(task_id, 0, 0)
        self.app._update_task_status(task_id, 'completed', '下载完成')
        self.assertEqual(window.percent_label.cget('text'), '已完成')
        self.assertEqual(widgets['size_label'].cget('text'), '已完成 · 0 B')
        # 旧的仅状态调用不传字节，仍应保留当前有效数据。
        window.update_status('completed')
        self.assertEqual((window._downloaded, window._total), (0, 0))

    def test_b05_saved_invalid_bytes_render_on_card_and_detail_first_screen(self):
        for index, (downloaded, total) in enumerate(
                ((-0.5, 0), ('bad', 0), (None, 0), ('bad', 100), (0, None))):
            with self.subTest(downloaded=downloaded, total=total):
                task_id = f't-b05-first-screen-{index}'
                self.db.create_task_with_chunks(
                    task_id, 'http://example.com/first.bin', 'first.bin',
                    str(self.root / task_id), total_size=total, status='completed')
                self.db.update_task_progress(task_id, downloaded, 0)
                self.app._add_task_widget(self.db.get_task(task_id))
                self.app._on_open_detail(task_id)
                widgets = self.app.task_widgets[task_id]
                window = self.app._detail_windows[task_id]
                self._assert_invalid_not_empty_completion(widgets, 'first screen')
                self.assertEqual(window.percent_label.cget('text'),
                                 widgets['progress_label'].cget('text'))
                self.assertNotIn('已完成 · 0 B', window.stats_label.cget('text'))

    def check_problem_layout(self, scaling):
        """真实 Tk 尺寸检查，缩放在独立子进程中应用，不能污染其他 GUI 用例。"""
        task_id = self.inject('f09-layout', status='failed', total=100)
        self.db.update_task_status(task_id, 'failed', '服务器文件版本已变化，请重新下载')
        self.app._update_task_status(task_id, 'failed', '')
        self.app._on_open_detail(task_id)
        self.app.deiconify()
        window = self.app._detail_windows[task_id]
        window.geometry('480x360')
        widgets = self.app.task_widgets[task_id]
        reason = widgets['reason_label']
        buttons = widgets['button_frame']
        for mode in ('dark', 'light'):
            ctk.set_appearance_mode(mode)
            for geometry in ('900x600', '1100x720'):
                with self.subTest(mode=mode, geometry=geometry, scaling=scaling):
                    self.app.geometry(geometry)
                    width, height = map(int, geometry.split('x'))
                    window_scale = ctk.ScalingTracker.get_window_scaling(self.app)
                    # 150% 会与系统 DPI 叠加。请求超过当前屏幕时 Windows 会夹取，
                    # 用可容纳的视口验证布局，保留下面全部像素和控件边界断言。
                    width = min(width, int((self.app.winfo_screenwidth() - 80) / window_scale))
                    height = min(height, int((self.app.winfo_screenheight() - 80) / window_scale))
                    self.app.geometry(f'{width}x{height}')
                    target = (round(width * window_scale), round(height * window_scale))
                    deadline = time.monotonic() + 3
                    while time.monotonic() < deadline:
                        self.pump(0.05)
                        actual = (self.app.winfo_width(), self.app.winfo_height())
                        if all(abs(left - right) <= 2 for left, right in zip(actual, target)):
                            break
                    self.assertTrue(all(abs(left - right) <= 2
                                        for left, right in zip(actual, target)),
                                    (actual, target))
                    self.assertTrue(reason.winfo_ismapped())
                    self.assertLessEqual(reason.winfo_y() + reason.winfo_height(),
                                         buttons.winfo_y())
                    self.assertLessEqual(buttons.winfo_y() + buttons.winfo_height(),
                                         widgets['frame'].winfo_height())
                    self.assertLessEqual(reason.winfo_reqwidth(), reason.winfo_width())
                    self.assertEqual(widgets['status_label'].cget('text'), '失败')
                    self.assertLessEqual(
                        window.problem_frame.winfo_y() + window.problem_frame.winfo_height(),
                        window.canvas.winfo_y())
                    self.assertGreater(window.canvas.winfo_height(), 20)
                    self.assertLessEqual(window.problem_advice_label.winfo_reqwidth(),
                                         window.problem_advice_label.winfo_width())
                    paused = self.app.category_buttons['status:paused']
                    self.assertTrue(paused.winfo_ismapped())
                    self.assertLessEqual(paused.winfo_rooty() + paused.winfo_height(),
                                         self.app.winfo_rooty() + self.app.winfo_height())
        ctk.set_appearance_mode('dark')


class ProblemLayoutTests(unittest.TestCase):
    def test_f09_dark_light_and_resizing_at_100_and_150_percent(self):
        root = Path(__file__).resolve().parents[1]
        code = (
            "import faulthandler; faulthandler.dump_traceback_later(20); "
            "from test_practical_improvements import PracticalGuiTests; "
            "import sys, customtkinter as ctk; "
            "ctk.set_widget_scaling(float(sys.argv[1])); "
            "ctk.set_window_scaling(float(sys.argv[1])); "
            # 父 Tk 用例可能仍持有延迟渲染的系统剪贴板；父线程等待子进程时，
            # 子 Tk 的 clipboard_get 会等父线程 WM_RENDERFORMAT 而死锁。
            # 布局检查不涉及剪贴板，隔离这项外部依赖，真实剪贴板测试仍照旧。
            "from unittest.mock import patch; "
            "clipboard_patch = patch('downloader.ui.main_window.MainWindow.clipboard_get', return_value=''); "
            "clipboard_patch.start(); "
            "PracticalGuiTests.setUpClass(); "
            "case = PracticalGuiTests('test_failed_reason_card_detail_and_clear_on_new_status'); "
            "case.setUp()\n"
            "try: case.check_problem_layout(float(sys.argv[1]))\n"
            "finally: case.tearDown(); PracticalGuiTests.tearDownClass(); clipboard_patch.stop(); faulthandler.cancel_dump_traceback_later()")
        for scaling in (1.0, 1.5):
            with self.subTest(scaling=scaling):
                try:
                    result = subprocess.run(
                        [sys.executable, '-B', '-c', code, str(scaling)], cwd=root,
                        capture_output=True, text=True, encoding='utf-8', timeout=30,
                        env=dict(os.environ, PYTHONIOENCODING='utf-8',
                                 PYTHONPATH=os.pathsep.join((str(root), str(root / 'tests')))))
                except subprocess.TimeoutExpired as error:
                    diagnostic = error.stderr or b''
                    if isinstance(diagnostic, bytes):
                        diagnostic = diagnostic.decode('utf-8', errors='replace')
                    self.fail(f'布局子进程仍受 30 秒限制；超时堆栈：\n{diagnostic}')
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
