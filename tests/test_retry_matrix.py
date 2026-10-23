# -*- coding: utf-8 -*-
"""二轮整改：B01/B02/B04 与 R09–R12 的真实 HTTP/可控时钟回归。

全部使用隔离临时配置、SQLite、分块与成品；不接触真实 data/dist。
"""
import os
import re
import tempfile
import threading
import time
import unittest
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

from downloader.core.chunk_downloader import ChunkDownloadError, ChunkDownloader, RetryBudget
from downloader.core.download_engine import DownloadEngine
from downloader.database.db_manager import DatabaseManager
from downloader.utils.config import ConfigManager


PAYLOAD = bytes(range(256)) * 8  # 2048 字节，两个 1024 字节分块


class _PlanHandler(BaseHTTPRequestHandler):
    """按 Range 起始字节执行计划；支持屏障、状态码记录和慢速整流。"""

    protocol_version = 'HTTP/1.0'

    def log_message(self, *args):
        pass

    def do_GET(self):
        header = self.headers.get('Range')
        match = re.fullmatch(r'bytes=(\d+)-(\d+)', header or '')
        start = int(match.group(1)) if match else None
        end = int(match.group(2)) if match else None
        self.server.requests.append({'range': header, 'start': start, 'end': end,
                                     'at': time.monotonic()})
        try:
            if start == 0 and end == 0 and self.server.probe:
                self._probe()
                return
            # 屏障：例如 fatal 分块必须等 protocol 分块真正返回后再发。
            gate = self.server.gates.get(start)
            if gate is not None:
                gate.wait(10)
            plan = self.server.plan.get(start, ('body',))
            kind = plan[0]
            if kind == 'protocol200':
                self._full_200(start)
            elif kind == 'once503':
                self.server.counts[start] = self.server.counts.get(start, 0) + 1
                if self.server.counts[start] == 1:
                    self._status(header, 503, {'Retry-After': plan[1]})
                elif start is None:
                    self._full_200(start)
                else:
                    self._range_body(start, end)
            elif kind == 'slow':
                self._slow_body(start, end, plan[1], plan[2])
            elif kind == 'status':
                self._status(header, plan[1], plan[2] if len(plan) > 2 else {})
            elif start is None:
                self._full_200(start)
            else:
                self._range_body(start, end)
        except (ConnectionResetError, ConnectionAbortedError, BrokenPipeError):
            pass
        finally:
            done = self.server.dones.get(start)
            if done is not None:
                done.set()

    def _probe(self):
        self.send_response(206)
        self.send_header('Content-Range', f'bytes 0-0/{len(PAYLOAD)}')
        self.send_header('ETag', '"stable"')
        self.send_header('Content-Length', '1')
        self.end_headers()
        self.server.sent.append((0, 206))
        self.wfile.write(PAYLOAD[:1])

    def _full_200(self, start):
        self.send_response(200)
        self.send_header('Content-Length', str(len(PAYLOAD)))
        self.end_headers()
        self.server.sent.append((start, 200))
        self.wfile.write(PAYLOAD)

    def _range_body(self, start, end):
        body = PAYLOAD[start:end + 1]
        self.send_response(206)
        self.send_header('Content-Range', f'bytes {start}-{end}/{len(PAYLOAD)}')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.server.sent.append((start, 206))
        self.wfile.write(body)

    def _slow_body(self, start, end, pieces, delay):
        body = PAYLOAD[start:end + 1]
        self.send_response(206)
        self.send_header('Content-Range', f'bytes {start}-{end}/{len(PAYLOAD)}')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.server.sent.append((start, 206))
        step = max(1, len(body) // max(1, pieces))
        for offset in range(0, len(body), step):
            self.wfile.write(body[offset:offset + step])
            self.wfile.flush()
            time.sleep(delay)

    def _status(self, header, code, headers):
        self.send_response(code)
        for key, value in headers.items():
            self.send_header(key, value)
        self.send_header('Content-Length', '0')
        self.end_headers()
        self.server.sent.append((header, code))


def _reset(server, probe=False):
    server.requests = []
    server.sent = []
    server.counts = {}
    server.plan = {}
    server.gates = {}
    server.dones = {}
    server.probe = probe


class _FakeClock:
    def __init__(self, start=1000.0):
        self.now = start

    def __call__(self):
        return self.now


class RetryGateTests(unittest.TestCase):
    """B02/R11/R12：共享窗口门禁与“成功流可越界”。"""

    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(('127.0.0.1', 0), _PlanHandler)
        _reset(cls.server)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.base = f'http://127.0.0.1:{cls.server.server_port}'

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(2)

    def setUp(self):
        _reset(self.server)
        self.temp = tempfile.TemporaryDirectory(prefix='practical-gate-')
        self.env_patch = patch.dict(os.environ, {'NO_PROXY': '*', 'no_proxy': '*'})
        self.env_patch.start()

    def tearDown(self):
        self.env_patch.stop()
        self.temp.cleanup()

    def _downloader(self, budget, **kwargs):
        return ChunkDownloader(
            kwargs.pop('chunk_id', 0), 'task', self.base + '/never', 0, len(PAYLOAD) - 1,
            os.path.join(self.temp.name, 'part0'), timeout=3, **kwargs)

    def test_b02_expired_inherited_window_sends_no_request(self):
        # 首次 transient 在 1000 开启 300 秒窗口，not-before 1002 已成过去，当前 1301
        clock = _FakeClock(1000.0)
        budget = RetryBudget(300, clock)
        budget.note_transient()
        clock.now = 1301.0
        downloader = self._downloader(budget, retry_times=3,
                                      retry_budget=budget, retry_not_before=1002.0,
                                      clock=clock)
        self.assertFalse(downloader.download())
        self.assertEqual(self.server.requests, [])
        self.assertIn('自动重试已结束', downloader.error_message())

    def test_b02_gate_does_not_depend_on_future_not_before(self):
        clock = _FakeClock(1000.0)
        budget = RetryBudget(1, clock)
        budget.note_transient()
        clock.now = 1005.0
        downloader = self._downloader(budget, retry_times=3, retry_budget=budget, clock=clock)
        self.assertFalse(downloader.download())
        self.assertEqual(self.server.requests, [])

    def test_b01_singlethread_refusal_keeps_existing_tmp(self):
        # 继承长等待的单线程：拒绝启动时不得先截断现存 tmp（同一真实路径）
        clock = _FakeClock(1000.0)
        budget = RetryBudget(300, clock)
        budget.note_transient()
        downloader = self._downloader(budget, retry_times=3, retry_budget=budget,
                                      retry_not_before=clock() + 3600, clock=clock,
                                      use_range=False)
        self.assertFalse(downloader.use_range)
        path = Path(downloader.temp_file)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b'9' * 9)
        self.assertFalse(downloader.download())
        self.assertEqual(path.read_bytes(), b'9' * 9)
        self.assertEqual(downloader.downloaded_bytes, 9)
        self.assertEqual(self.server.requests, [])
        self.assertIn('服务器要求', downloader.error_message())

    def test_b01_oversized_tmp_is_not_truncated_before_final_gate(self):
        for use_range in (False, True):
            with self.subTest(use_range=use_range):
                clock = _FakeClock(1000.0)
                budget = RetryBudget(1, clock)
                budget.note_transient()
                downloader = self._downloader(
                    budget, retry_times=1, retry_budget=budget, clock=clock,
                    use_range=use_range)
                path = Path(downloader.temp_file)
                original_bytes = b'x' * (len(PAYLOAD) + 1)
                path.write_bytes(original_bytes)
                original_wait = downloader._wait_if_paused_or_cancelled
                calls = 0

                def cross_deadline_before_attempt():
                    nonlocal calls
                    calls += 1
                    if calls == 2:
                        clock.now = 1002.0
                    return original_wait()

                with patch.object(downloader, '_wait_if_paused_or_cancelled',
                                  cross_deadline_before_attempt):
                    self.assertFalse(downloader.download(resume=True))
                self.assertEqual(path.read_bytes(), original_bytes)
                self.assertEqual(downloader.downloaded_bytes, len(original_bytes))
                self.assertEqual(self.server.requests, [])
                self.assertIn('自动重试已结束', downloader.error_message())

    def test_b01_rejected_nonrange_retry_keeps_actual_byte_count(self):
        clock = _FakeClock(1000.0)
        budget = RetryBudget(1, clock)
        budget.note_transient()
        downloader = self._downloader(
            budget, retry_times=1, retry_budget=budget, clock=clock, use_range=False)
        path = Path(downloader.temp_file)
        path.write_bytes(b'kept-partial')
        original_wait = downloader._wait_if_paused_or_cancelled
        calls = 0

        def cross_deadline_at_request_gate():
            nonlocal calls
            calls += 1
            if calls == 3:
                clock.now = 1002.0
            return original_wait()

        with patch.object(downloader, '_wait_if_paused_or_cancelled',
                          cross_deadline_at_request_gate):
            self.assertFalse(downloader.download(resume=True))
        self.assertEqual(path.read_bytes(), b'kept-partial')
        self.assertEqual(downloader.downloaded_bytes, len(b'kept-partial'))
        self.assertEqual(self.server.requests, [])

    def test_b01_rejected_start_hook_is_not_bypassed_on_retry(self):
        calls = []

        def reject_stale_run():
            calls.append(True)
            raise ChunkDownloadError('下载会话已停止，已保留下载数据', 'transient')

        downloader = self._downloader(
            None, retry_times=2, use_range=False, pre_start_hook=reject_stale_run)
        path = Path(downloader.temp_file)
        path.write_bytes(b'kept-partial')
        with patch.object(downloader, '_wait_before_retry', return_value=True):
            self.assertFalse(downloader.download())
        self.assertEqual(len(calls), 2)
        self.assertEqual(path.read_bytes(), b'kept-partial')
        self.assertEqual(self.server.requests, [])

    def test_r11_streaming_success_may_finish_after_window(self):
        self.server.plan = {0: ('slow', 5, 0.12)}
        budget = RetryBudget(0.2, time.monotonic)
        budget.note_transient()  # 窗口会先于下载结束到期
        path = os.path.join(self.temp.name, 'slow.part0')
        downloader = ChunkDownloader(
            0, 'task', self.base + '/slow', 0, len(PAYLOAD) - 1, path,
            timeout=5, retry_times=1, retry_budget=budget, total_size=len(PAYLOAD))
        started = time.monotonic()
        self.assertTrue(downloader.download())
        self.assertGreaterEqual(time.monotonic() - started, 0.5)
        with open(path, 'rb') as handle:
            self.assertEqual(handle.read(), PAYLOAD)

    def test_r12_shared_window_is_not_reset_per_chunk(self):
        clock = _FakeClock(1000.0)
        budget = RetryBudget(10, clock)
        budget.note_transient()  # 第一块在 1000 开启窗口
        clock.now = 1030.0
        deadline_after_first = budget.deadline
        budget.note_transient()  # 第二块晚失败，不能重置窗口
        self.assertEqual(budget.deadline, deadline_after_first)
        downloader = self._downloader(budget, chunk_id=1, retry_times=3,
                                      retry_budget=budget, clock=clock)
        self.assertFalse(downloader.download())
        self.assertEqual(self.server.requests, [])


class RetryMatrixTests(unittest.TestCase):
    """B01/B04 与 R09/R10：真实本地 HTTP + 隔离 SQLite/分块/成品。"""

    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(('127.0.0.1', 0), _PlanHandler)
        _reset(cls.server, probe=True)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.base = f'http://127.0.0.1:{cls.server.server_port}'

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(2)

    def setUp(self):
        _reset(self.server, probe=True)
        self.temp = tempfile.TemporaryDirectory(prefix='practical-matrix-')
        self.env_patch = patch.dict(os.environ, {'NO_PROXY': '*', 'no_proxy': '*'})
        self.env_patch.start()
        self.config = ConfigManager(os.path.join(self.temp.name, 'config.json'))
        self.config.set('temp_dir', os.path.join(self.temp.name, 'parts'))
        self.config.download_dir = os.path.join(self.temp.name, 'out')
        self.config.set('timeout', 3)
        self.config.set('retry_times', 1)
        self.config.thread_count = 2
        self.db = DatabaseManager(os.path.join(self.temp.name, 'db.sqlite'))
        self.engine = DownloadEngine(self.db, self.config)

    def tearDown(self):
        self.engine.shutdown()
        self.env_patch.stop()
        self.temp.cleanup()

    def make_task(self, retry_times=1, thread_count=2):
        self.config.set('retry_times', retry_times)
        self.config.thread_count = thread_count
        task_id = self.engine.create_download_task(self.base + '/file')
        self.assertIsNotNone(task_id)
        return task_id

    def preset_bytes(self, task_id, size=512):
        """写入已有断点字节；返回 {续传起点: 路径}（Range 会从起点开始）。"""
        files = {}
        for chunk in self.db.get_chunks(task_id):
            path = Path(chunk['temp_file'])
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b'x' * size)
            self.db.update_chunk_progress(chunk['chunk_id'], size)
            files[int(chunk['start_byte']) + size] = path
        # 任务层真实进度同步为两块之和，拒绝启动时必须原样保留
        self.db.update_task_progress(task_id, size * len(files), 0)
        return files

    def run_chunks(self, task_id):
        task = self.db.get_task(task_id)
        task['_request_context'] = None
        run_id = self.engine._next_task_run_id(task_id)
        self.assertTrue(self.engine._start_multithread_download(task, True, run_id))
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            if self.db.get_task(task_id)['status'] in (
                    'failed', 'completed', 'verify_failed', 'cancelled', 'paused'):
                break
            time.sleep(0.02)
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline and (
                task_id in self.engine.thread_pools or task_id in self.engine.active_downloaders):
            time.sleep(0.02)
        return self.db.get_task(task_id)

    def assert_no_fallback_get(self):
        self.assertEqual([item for item in self.server.requests if item['range'] is None], [])

    def test_b01_protocol_plus_long_wait_keeps_chunk_bytes(self):
        task_id = self.make_task(retry_times=1)
        files = self.preset_bytes(task_id)
        # 两块都失败：512 起点的 protocol(200)，1536 起点的 503 长等待
        self.server.plan = {512: ('protocol200',), 1536: ('status', 503, {'Retry-After': '3600'})}
        task = self.run_chunks(task_id)

        self.assertEqual(task['status'], 'failed')
        self.assertIn('保留', task['error_message'] or '')
        self.assert_no_fallback_get()
        for start, path in files.items():
            self.assertEqual(path.stat().st_size, 512, f'起点 {start} 的分块字节被清空')
        for chunk in self.db.get_chunks(task_id):
            self.assertEqual(int(chunk['downloaded_bytes'] or 0), 512)
        self.assertFalse(os.path.exists(task['save_path']))

    def test_b01_reverse_without_gate_loses_chunk_bytes(self):
        # 反向验证：仅在内存里禁用拒绝门禁与等待保护，必须复现“先毁掉旧数据再失败”。
        task_id = self.make_task(retry_times=1)
        files = self.preset_bytes(task_id)
        self.server.plan = {512: ('protocol200',),
                            1536: ('status', 503, {'Retry-After': '3600'}),
                            None: ('status', 503)}
        with patch.object(DownloadEngine, '_retry_block_reason', return_value=None), \
                patch.object(ChunkDownloader, '_retry_block_reason', return_value=None), \
                patch.object(ChunkDownloader, '_wait_for_retry_not_before', return_value=True):
            task = self.run_chunks(task_id)
        self.assertEqual(task['status'], 'failed')
        # 门禁被禁用后旧块已在请求前被清理，证明正向断言能抓住回退
        remaining = [path.stat().st_size for path in files.values() if path.exists()]
        self.assertEqual(sum(remaining), 0)
        self.assertEqual([entry for entry in self.server.sent if entry == (None, 503)],
                         [(None, 503)])

    def test_r09_protocol_fallback_waits_once_and_finishes_exact_bytes(self):
        task_id = self.make_task(retry_times=1)
        files = self.preset_bytes(task_id)
        self.server.plan = {512: ('protocol200',),
                            1536: ('status', 503, {'Retry-After': '2'})}
        task = self.run_chunks(task_id)

        self.assertEqual(task['status'], 'completed')
        self.assertEqual(Path(task['save_path']).read_bytes(), PAYLOAD)
        fallback = [item for item in self.server.requests if item['range'] is None]
        transient = [item for item in self.server.requests
                     if item['range'] == 'bytes=1536-2047']
        self.assertEqual(len(fallback), 1)
        self.assertEqual(len(transient), 1)
        interval = fallback[0]['at'] - transient[0]['at']
        self.assertGreaterEqual(interval, 1.9)
        self.assertLess(interval, 2.9)
        self.assertIn((None, 200), self.server.sent)
        self.assertTrue(all(not path.exists() for path in files.values()))
        self.assertEqual(task['downloaded_size'], len(PAYLOAD))
        self.assertNotIn(task_id, self.engine.thread_pools)
        self.assertNotIn(task_id, self.engine.active_downloaders)

    def test_b01_startup_delay_past_deadline_keeps_chunks(self):
        """初检通过、worker 启动前窗口到期：不得先毁掉旧块/进度。"""
        task_id = self.make_task(retry_times=1)
        files = self.preset_bytes(task_id)
        self.server.plan = {512: ('protocol200',),
                            1536: ('status', 503, {'Retry-After': '2'})}
        original = ChunkDownloader.download

        def delayed(downloader, resume=False):
            # 只延后降级的非 Range worker，模拟“初检通过到真正启动”的调度延迟
            if not downloader.use_range:
                time.sleep(3.0)
            return original(downloader, resume)

        with patch('downloader.core.download_engine.CHUNK_RETRY_DEADLINE', 2.5), \
                patch.object(ChunkDownloader, 'download', delayed):
            task = self.run_chunks(task_id)

        self.assertEqual(task['status'], 'failed')
        message = task['error_message'] or ''
        self.assertIn('自动重试已结束', message)
        self.assertIn('保留', message)
        self.assert_no_fallback_get()
        for path in files.values():
            self.assertEqual(path.stat().st_size, 512)
        for chunk in self.db.get_chunks(task_id):
            self.assertEqual(int(chunk['downloaded_bytes'] or 0), 512)
        self.assertEqual(int(self.db.get_task(task_id)['downloaded_size'] or 0), 1024)
        self.assertFalse(os.path.exists(task['save_path']))
        self.assertNotIn(task_id, self.engine.thread_pools)
        self.assertNotIn(task_id, self.engine.active_downloaders)

    def test_b01_pause_across_deadline_in_real_fallback_keeps_chunks(self):
        """真实降级：等待中暂停跨过窗口后恢复，不发非 Range 请求也不丢断点。"""
        task_id = self.make_task(retry_times=1)
        files = self.preset_bytes(task_id)
        self.server.plan = {512: ('protocol200',),
                            1536: ('status', 503, {'Retry-After': '2'})}

        with patch('downloader.core.download_engine.CHUNK_RETRY_DEADLINE', 2.5):
            self.assertTrue(self.engine.start_download(task_id, resume=True))
            deadline = time.monotonic() + 10
            # 等到 503(临时错误) 已到、且降级的非 Range worker 已登记，再真实暂停
            while time.monotonic() < deadline:
                active = self.engine.active_downloaders.get(task_id) or []
                if (any(entry[1] == 503 for entry in self.server.sent)
                        and any(not downloader.use_range for downloader in active)):
                    break
                time.sleep(0.02)
            active = self.engine.active_downloaders.get(task_id) or []
            self.assertTrue(any(not downloader.use_range for downloader in active))
            self.assertTrue(self.engine.pause_download(task_id))
            time.sleep(2.7)
            self.assertTrue(self.engine.resume_download(task_id))

            deadline = time.monotonic() + 15
            while time.monotonic() < deadline:
                if self.db.get_task(task_id)['status'] in (
                        'failed', 'completed', 'verify_failed', 'cancelled'):
                    break
                time.sleep(0.02)
            deadline = time.monotonic() + 10
            while time.monotonic() < deadline and (
                    task_id in self.engine.thread_pools
                    or task_id in self.engine.active_downloaders):
                time.sleep(0.02)

        task = self.db.get_task(task_id)
        self.assertEqual(task['status'], 'failed')
        message = task['error_message'] or ''
        self.assertIn('自动重试已结束', message)
        self.assertIn('保留', message)
        self.assert_no_fallback_get()
        for path in files.values():
            self.assertEqual(path.stat().st_size, 512)
        for chunk in self.db.get_chunks(task_id):
            self.assertEqual(int(chunk['downloaded_bytes'] or 0), 512)
        self.assertEqual(int(task['downloaded_size'] or 0), 1024)
        self.assertFalse(os.path.exists(task['save_path']))
        self.assertNotIn(task_id, self.engine.thread_pools)
        self.assertNotIn(task_id, self.engine.active_downloaders)
        self.assertNotIn(task_id, self.engine.chunk_progress)

    def test_b01_rejected_fallback_stops_progress_monitor_without_losing_bytes(self):
        self.test_b01_startup_delay_past_deadline_keeps_chunks()
        task_id = self.db.get_all_tasks()[0]['task_id']
        self.assertNotIn(task_id, self.engine.chunk_progress)
        with patch.object(self.db, 'update_task_progress',
                          wraps=self.db.update_task_progress) as update:
            time.sleep(1.1)  # 旧监控的一秒周期必须安静退出。
        self.assertEqual(update.call_count, 0)
        self.assertEqual(self.db.get_task(task_id)['downloaded_size'], 1024)

    def test_b04_fatal_beats_protocol_with_barrier(self):
        protocol_done = threading.Event()
        task_id = self.make_task(retry_times=3)
        files = self.preset_bytes(task_id)
        # fatal 请求到达后先等 protocol 真正返回，固定“protocol 先完成”的顺序
        self.server.plan = {512: ('status', 403, {}), 1536: ('protocol200',)}
        self.server.gates = {512: protocol_done}
        self.server.dones = {1536: protocol_done}
        task = self.run_chunks(task_id)

        # 夹具确实分别发出 200(protocol) 与 403(fatal)
        self.assertIn((1536, 200), self.server.sent)
        self.assertIn(('bytes=512-1023', 403), self.server.sent)
        self.assertEqual(len([item for item in self.server.requests
                              if item['range'] == 'bytes=512-1023']), 1)
        self.assertEqual(task['status'], 'failed')
        self.assertIn('403', task['error_message'] or '')
        self.assert_no_fallback_get()
        for path in files.values():
            self.assertEqual(path.stat().st_size, 512)
        for chunk in self.db.get_chunks(task_id):
            self.assertEqual(int(chunk['downloaded_bytes'] or 0), 512)
        self.assertFalse(os.path.exists(task['save_path']))
        self.assertNotIn(task_id, self.engine.thread_pools)
        self.assertNotIn(task_id, self.engine.active_downloaders)

    def test_b04_fatal_plus_transient_counts_and_bytes(self):
        task_id = self.make_task(retry_times=2)
        files = self.preset_bytes(task_id)
        self.server.plan = {512: ('status', 403, {}), 1536: ('status', 503, {})}
        task = self.run_chunks(task_id)

        fatal_gets = [item for item in self.server.requests if item['range'] == 'bytes=512-1023']
        transient_gets = [item for item in self.server.requests if item['range'] == 'bytes=1536-2047']
        self.assertEqual(len(fatal_gets), 1)
        self.assertGreaterEqual(len(transient_gets), 1)
        self.assertEqual(task['status'], 'failed')
        self.assert_no_fallback_get()
        for path in files.values():
            self.assertEqual(path.stat().st_size, 512)
        self.assertFalse(os.path.exists(task['save_path']))

    def test_r09_cross_round_not_before_and_byte_restore(self):
        self.server.plan = {0: ('once503', '2'), 1024: ('body',)}
        task_id = self.make_task(retry_times=1)
        task = self.run_chunks(task_id)

        self.assertEqual(task['status'], 'completed')
        self.assertEqual(Path(task['save_path']).read_bytes(), PAYLOAD)
        chunk0 = [item for item in self.server.requests if item['range'] == 'bytes=0-1023']
        self.assertEqual(len(chunk0), 2)
        interval = chunk0[1]['at'] - chunk0[0]['at']
        # 外层首轮退避 1 秒与 Retry-After 2 秒取最大，不能相加成 3 秒
        self.assertGreaterEqual(interval, 1.9)
        self.assertLess(interval, 2.9)

    def test_r10_pause_during_server_wait_sends_no_more_get(self):
        self.server.plan = {0: ('status', 503, {'Retry-After': '5'})}
        self.config.set('retry_times', 3)
        task_id = str(uuid.uuid4())
        save_path = os.path.join(self.temp.name, 'out', 'pause.bin')
        self.assertTrue(self.db.create_task_with_chunks(
            task_id, self.base + '/pause', 'pause.bin', save_path,
            total_size=len(PAYLOAD), support_range=True, thread_count=1))
        task = self.db.get_task(task_id)
        task['_request_context'] = None
        self.db.update_task_status(task_id, 'downloading')
        run_id = self.engine._next_task_run_id(task_id)
        self.assertTrue(self.engine._start_singlethread_download(task, False, run_id))

        deadline = time.monotonic() + 10
        while time.monotonic() < deadline and not self.server.requests:
            time.sleep(0.01)
        self.assertTrue(self.server.requests)
        self.assertTrue(self.engine.pause_download(task_id))
        time.sleep(1.2)
        self.assertEqual(len(self.server.requests), 1)
        self.assertEqual(self.db.get_task(task_id)['status'], 'paused')
        self.assertNotIn(task_id, self.engine.active_downloaders)
        self.assertNotIn(task_id, self.engine.thread_pools)

    def test_r10_cancel_and_shutdown_during_wait_send_no_more_get(self):
        for action in ('cancel', 'shutdown'):
            with self.subTest(action=action):
                self.server.requests.clear()
                self.server.sent.clear()
                self.server.plan = {0: ('status', 503, {'Retry-After': '5'})}
                task_id = str(uuid.uuid4())
                save_path = os.path.join(self.temp.name, 'out', f'{action}.bin')
                self.assertTrue(self.db.create_task_with_chunks(
                    task_id, self.base + '/' + action, action + '.bin', save_path,
                    total_size=len(PAYLOAD), support_range=True, thread_count=1))
                self.config.set('retry_times', 3)
                self.db.update_task_status(task_id, 'downloading')
                task = self.db.get_task(task_id)
                run_id = self.engine._next_task_run_id(task_id)
                self.assertTrue(self.engine._start_singlethread_download(task, False, run_id))
                deadline = time.monotonic() + 5
                while time.monotonic() < deadline and not any(
                        entry[1] == 503 for entry in self.server.sent):
                    time.sleep(0.01)
                self.assertTrue(any(entry[1] == 503 for entry in self.server.sent))

                started = time.monotonic()
                if action == 'cancel':
                    self.assertTrue(self.engine.cancel_download(task_id))
                    self.assertEqual(self.db.get_task(task_id)['status'], 'cancelled')
                else:
                    self.engine.shutdown()
                    self.assertNotIn(self.db.get_task(task_id)['status'],
                                     ('completed', 'failed', 'verify_failed'))
                self.assertLess(time.monotonic() - started, 1.5)
                self.assertEqual(len(self.server.requests), 1)
                self.assertNotIn(task_id, self.engine.active_downloaders)
                self.assertNotIn(task_id, self.engine.thread_pools)
                self.assertFalse(Path(save_path).exists())


if __name__ == '__main__':
    unittest.main()
