"""未知大小与零字节普通文件下载（方案 A）。

覆盖设计档 docs/designs/unknown-size-file-download.md 的 P01~P04、D01、D02、D04、
F01/F02、B02 等关键场景；浏览器右键入口保持原拒绝行为（B01/B02）。
"""
import hashlib
import os
import tempfile
import threading
import time
import unittest
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import Mock, patch

from requests.structures import CaseInsensitiveDict

from downloader.browser.security import normalize_context, protect
from downloader.core.chunk_downloader import ChunkDownloader
from downloader.core.download_engine import DownloadEngine
from downloader.core.task_manager import TaskManager
from downloader.database.db_manager import DatabaseManager
from downloader.utils.config import ConfigManager


PAYLOAD = bytes(range(253)) * 97  # 24541 字节，非 8192 整数倍，便于发现截断


class UnknownSizeHandler(BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'

    def log_message(self, *_args):
        pass

    def _write(self, data: bytes):
        """客户端提前关连接时不要刷一屏 traceback"""
        try:
            self.wfile.write(data)
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            self.close_connection = True

    def _no_length(self, body: bytes):
        self.send_response(200)
        self.send_header('Content-Type', 'application/octet-stream')
        self.send_header('Connection', 'close')
        self.end_headers()
        if body:
            self._write(body)
        self.close_connection = True

    def _known(self, body: bytes, status: int = 200):
        self.send_response(status)
        self.send_header('Content-Type', 'application/octet-stream')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        if body:
            self._write(body)

    def do_GET(self):
        is_probe = self.headers.get('Range') == 'bytes=0-0'
        self.server.requests.append((self.path, self.headers.get('Range')))

        if self.path == '/no-length':
            self._no_length(PAYLOAD)
        elif self.path == '/empty-no-length':
            self._no_length(b'')
        elif self.path == '/chunked':
            self.send_response(200)
            self.send_header('Content-Type', 'application/octet-stream')
            self.send_header('Transfer-Encoding', 'chunked')
            self.end_headers()
            for piece in (PAYLOAD[:15000], PAYLOAD[15000:]):
                self._write(f'{len(piece):x}\r\n'.encode() + piece + b'\r\n')
            self._write(b'0\r\n\r\n')
        elif self.path == '/empty':
            self._known(b'')
        elif self.path == '/plain':
            # 有大小、不支持 Range 的普通文件：旧行为必须不变
            self._known(PAYLOAD)
        elif self.path == '/unknown-then-known':
            # 探测时不给长度，正式请求才给 Length
            self._no_length(b'') if is_probe else self._known(PAYLOAD)
        elif self.path == '/short-body':
            # 探测无长度，正式请求声明完整长度却只发一部分
            if is_probe:
                self._no_length(b'')
            else:
                self.send_response(200)
                self.send_header('Content-Length', str(len(PAYLOAD)))
                self.end_headers()
                self._write(PAYLOAD[:4096])
                self.close_connection = True
        elif self.path == '/gzip':
            self.send_response(200)
            self.send_header('Content-Type', 'application/octet-stream')
            self.send_header('Content-Encoding', 'gzip')
            self.send_header('Content-Length', '10')
            self.end_headers()
        elif self.path == '/broken-chunked':
            # chunked 缺结束块：流不可能正常结束
            self.send_response(200)
            self.send_header('Content-Type', 'application/octet-stream')
            self.send_header('Transfer-Encoding', 'chunked')
            self.end_headers()
            self._write(b'5\r\nhello\r\n')
            self.close_connection = True
        elif self.path == '/drop-once':
            # 只统计正式请求：探测（bytes=0-0）不能计入，否则第一次正式 GET 时计数已不是 0。
            # 第一次正式响应发超过 8 KB 的错误数据，并且用缺结束块的 chunked 分帧，
            # 这样无法被当成“正常 EOF”；第二次才返回完整内容，用来验证重试会清空残片。
            if is_probe:
                self._no_length(b'')
                return
            with self.server.lock:
                hit = self.server.real_hits.get(self.path, 0)
                self.server.real_hits[self.path] = hit + 1
            if hit == 0:
                junk = b'z' * 20000
                self.send_response(200)
                self.send_header('Content-Type', 'application/octet-stream')
                self.send_header('Transfer-Encoding', 'chunked')
                self.end_headers()
                self._write(f'{len(junk):x}\r\n'.encode() + junk + b'\r\n')
                self.close_connection = True
            else:
                self._no_length(PAYLOAD)
        elif self.path == '/etag-change':
            # 探测阶段给 v1，正式下载响应才变成 v2，验证身份校验会失败
            self.send_response(200)
            self.send_header('Content-Type', 'application/octet-stream')
            self.send_header('ETag', '"v1"' if is_probe else '"v2"')
            self.send_header('Connection', 'close')
            self.end_headers()
            self._write(PAYLOAD)
            self.close_connection = True
        elif self.path == '/missing':
            self.send_error(404)
        else:
            self.send_error(404)


class UnknownSizeFixture(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(('127.0.0.1', 0), UnknownSizeHandler)
        cls.server.requests = []
        cls.server.hits = {}
        cls.server.real_hits = {}
        cls.server.lock = threading.Lock()
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.base_url = f'http://127.0.0.1:{cls.server.server_port}'

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(2)

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='daw-unknown-')
        self.addCleanup(self.temp.cleanup)
        with self.server.lock:
            self.server.hits.clear()
            self.server.real_hits.clear()
        self.root = Path(self.temp.name)
        self.config = ConfigManager(str(self.root / 'config.json'))
        self.config.download_dir = str(self.root / 'out')
        self.config.set('temp_dir', str(self.root / 'parts'))
        self.config.set('timeout', 5)
        self.config.set('retry_times', 1)
        self.config.thread_count = 4
        self.env_patch = patch.dict(os.environ, {'NO_PROXY': '*', 'no_proxy': '*'})
        self.env_patch.start()
        self.addCleanup(self.env_patch.stop)
        self.db = DatabaseManager(str(self.root / 'downloads.db'))
        self.engine = DownloadEngine(self.db, self.config)
        self.manager = TaskManager(self.engine, self.db, max_concurrent=1)
        self.addCleanup(self.manager.shutdown)

    def add(self, path, **kwargs):
        task_id = self.engine.create_download_task(self.base_url + path, **kwargs)
        self.assertIsNotNone(task_id, self.engine.last_add_detail)
        return task_id

    def wait(self, task_id, status='completed', timeout=15):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            task = self.db.get_task(task_id)
            if task['status'] == status:
                return task
            if status != 'failed' and task['status'] in ('failed', 'verify_failed', 'cancelled'):
                self.fail(f'任务未按预期完成: {task}')
            time.sleep(0.02)
        self.fail(f'等待超时: {self.db.get_task(task_id)}')

    def run_task(self, task_id, status='completed'):
        self.assertTrue(self.manager.start_task(task_id))
        return self.wait(task_id, status)


class ProbeTests(UnknownSizeFixture):
    """探测分类：自身入口放行、浏览器入口与原失败分类不变"""

    def probe(self, path, request_context=None):
        metadata = {}
        if request_context is not None:
            # 生产链路里上下文都已经过 normalize_context，这里保持一致
            request_context = normalize_context(self.base_url + path, request_context)
        result = self.engine.check_url_support_range(
            self.base_url + path, request_context, metadata=metadata)
        return result, metadata

    def test_no_length_probe_is_success_for_own_entry(self):
        (ranges, size), metadata = self.probe('/no-length')
        self.assertEqual((ranges, size), (False, 0))
        self.assertTrue(metadata['probe_ok'])
        self.assertEqual(metadata['length_kind'], 'unknown')
        self.assertIsNone(metadata['reason'])

    def test_empty_length_probe_is_success_for_own_entry(self):
        (ranges, size), metadata = self.probe('/empty')
        self.assertEqual((ranges, size), (False, 0))
        self.assertTrue(metadata['probe_ok'])
        self.assertEqual(metadata['length_kind'], 'empty')

    def test_browser_context_still_rejects_no_length_and_empty(self):
        for path in ('/no-length', '/empty', '/empty-no-length'):
            with self.subTest(path=path):
                (ranges, size), metadata = self.probe(path, {'headers': {}})
                self.assertEqual((ranges, size), (False, 0))
                self.assertFalse(metadata['probe_ok'])
                self.assertEqual(metadata['reason'], 'no_length')

    def test_known_and_range_results_keep_old_shape(self):
        self.assertEqual(self.probe('/plain')[0], (False, len(PAYLOAD)))
        (ranges, size), metadata = self.probe('/plain')
        self.assertEqual(metadata['length_kind'], 'known')
        self.assertEqual(metadata['reason'], None)

    def test_failures_stay_failures(self):
        for path, reason in (('/missing', 'http_4xx'), ('/gzip', 'encoding_not_supported')):
            with self.subTest(path=path):
                (ranges, size), metadata = self.probe(path)
                self.assertEqual(size, 0)
                self.assertFalse(metadata['probe_ok'])
                self.assertEqual(metadata['reason'], reason)

    def test_probe_markers_do_not_leak_between_calls(self):
        # 复用同一个 metadata 字典：成功标记不能被上一次成功串到这次失败上
        metadata = {}
        self.engine.check_url_support_range(self.base_url + '/no-length', None, metadata=metadata)
        self.assertTrue(metadata['probe_ok'])
        self.engine.check_url_support_range(self.base_url + '/missing', None, metadata=metadata)
        self.assertFalse(metadata['probe_ok'])
        self.assertIsNone(metadata['length_kind'])


class CreateTaskTests(UnknownSizeFixture):
    """建任务：自身入口放行，浏览器入口与非新类别保持原拒绝"""

    def test_no_length_task_is_created_as_single_thread(self):
        task = self.db.get_task(self.add('/no-length'))
        self.assertEqual(task['total_size'], 0)
        self.assertFalse(task['support_range'])
        self.assertEqual(task['thread_count'], 1)
        self.assertEqual(task['download_type'], 'file')
        self.assertIsNone(task['browser_context'])

    def test_empty_length_task_is_created(self):
        task = self.db.get_task(self.add('/empty'))
        self.assertEqual(task['total_size'], 0)

    def test_browser_context_creates_nothing(self):
        before = len(self.db.get_all_tasks())
        for path in ('/no-length', '/empty', '/empty-no-length'):
            with self.subTest(path=path):
                self.assertIsNone(self.engine.create_download_task(
                    self.base_url + path, request_context={'headers': {}}))
                self.assertEqual(self.engine.last_add_error, 'no_length')
        self.assertEqual(len(self.db.get_all_tasks()), before)

    def test_http_and_encoding_failures_never_create_tasks(self):
        for path, reason in (('/missing', 'http_4xx'), ('/gzip', 'encoding_not_supported')):
            with self.subTest(path=path):
                self.assertIsNone(self.engine.create_download_task(self.base_url + path))
                self.assertEqual(self.engine.last_add_error, reason)


class UnknownSizeDownloadTests(UnknownSizeFixture):
    """端到端：真流式写入、EOF 完成、真实大小回填"""

    def test_no_length_download_writes_exact_bytes_and_backfills_size(self):
        task = self.run_task(self.add('/no-length'))
        self.assertEqual(Path(task['save_path']).read_bytes(), PAYLOAD)
        self.assertEqual(task['total_size'], len(PAYLOAD))
        self.assertEqual(task['downloaded_size'], len(PAYLOAD))
        self.assertEqual(task['support_range'], 0)
        self.assertEqual(task['thread_count'], 1)
        # 正式请求不能带 Range；探测请求只能是 bytes=0-0
        real_requests = [item for item in self.server.requests if item[0] == '/no-length']
        self.assertIn(None, [item[1] for item in real_requests], real_requests)
        self.assertEqual({item[1] for item in real_requests} - {None}, {'bytes=0-0'}, real_requests)

    def test_chunked_download_completes_on_stream_end(self):
        task = self.run_task(self.add('/chunked'))
        self.assertEqual(Path(task['save_path']).read_bytes(), PAYLOAD)
        self.assertEqual(task['total_size'], len(PAYLOAD))

    def test_no_length_empty_response_produces_empty_file(self):
        task = self.run_task(self.add('/empty-no-length'))
        self.assertEqual(Path(task['save_path']).read_bytes(), b'')
        self.assertEqual(task['total_size'], 0)
        self.assertEqual(task['status'], 'completed')

    def test_explicit_zero_length_produces_empty_file(self):
        task = self.run_task(self.add('/empty'))
        self.assertEqual(Path(task['save_path']).read_bytes(), b'')
        self.assertEqual(task['total_size'], 0)
        self.assertEqual(task['status'], 'completed')

    def test_unknown_then_known_length_is_checked_against_real_response(self):
        task = self.run_task(self.add('/unknown-then-known'))
        self.assertEqual(Path(task['save_path']).read_bytes(), PAYLOAD)
        self.assertEqual(task['total_size'], len(PAYLOAD))

    def test_short_body_never_completes(self):
        task_id = self.add('/short-body')
        task = self.run_task(task_id, status='failed')
        self.assertFalse(os.path.exists(task['save_path']), '短流不能被发布成成品')

    def test_leftover_temp_bytes_do_not_fake_success(self):
        task_id = self.add('/no-length')
        task = self.db.get_task(task_id)
        temp_file = Path(self.config.temp_dir) / f'{task_id}.tmp'
        temp_file.parent.mkdir(parents=True, exist_ok=True)
        temp_file.write_bytes(b'stale-junk-bytes')
        self.run_task(task_id)
        self.assertEqual(Path(task['save_path']).read_bytes(), PAYLOAD)

    def test_known_size_non_range_target_still_rejected_with_browser_context(self):
        # 浏览器入口对无长度链接仍然不建任务，链接不会因为新功能漏放
        self.assertIsNone(self.engine.create_download_task(
            self.base_url + '/no-length', request_context={'headers': {}}))

    def test_hash_verification_works_for_empty_file(self):
        digest = hashlib.md5(b'').hexdigest()
        task = self.run_task(self.add('/empty', expected_hash=digest, hash_type='md5'))
        self.assertEqual(task['hash_verified'], 1)
        self.assertEqual(task['total_size'], 0)

    def test_hash_mismatch_is_verify_failed_for_empty_file(self):
        task = self.run_task(self.add('/empty', expected_hash='0' * 32, hash_type='md5'),
                             status='verify_failed')
        self.assertEqual(task['hash_verified'], -1)


class KnownSizeRegressionTests(UnknownSizeFixture):
    """既有有大小、非 Range 链路不受新分支影响"""

    def test_plain_known_size_download_is_unchanged(self):
        task = self.run_task(self.add('/plain'))
        self.assertEqual(Path(task['save_path']).read_bytes(), PAYLOAD)
        self.assertEqual(task['total_size'], len(PAYLOAD))
        self.assertFalse(task['support_range'])


class HeaderEdgeCaseTests(UnknownSizeFixture):
    """缺失的 Content-Length 与非法值必须区分（Codex 审查 P2-1）"""

    def _fake_response(self, headers):
        response = Mock()
        response.status_code = 200
        response.headers = CaseInsensitiveDict(headers)
        response.url = self.base_url + '/mock'
        response.raise_for_status = lambda: None
        response.__enter__ = lambda _self: response
        response.__exit__ = lambda *_args: False
        return response

    def test_blank_content_length_is_not_treated_as_missing(self):
        for value in ('', '   '):
            with self.subTest(value=repr(value)):
                metadata = {}
                with patch('requests.get', return_value=self._fake_response({'Content-Length': value})):
                    result = self.engine.check_url_support_range(self.base_url + '/mock', None,
                                                                metadata=metadata)
                self.assertEqual(result, (False, 0))
                self.assertFalse(metadata['probe_ok'], '空的 Length 不能进未知大小模式')
                self.assertEqual(metadata['reason'], 'no_length')

    def test_missing_content_length_header_still_means_unknown(self):
        metadata = {}
        with patch('requests.get', return_value=self._fake_response({})):
            result = self.engine.check_url_support_range(self.base_url + '/mock', None,
                                                        metadata=metadata)
        self.assertEqual(result, (False, 0))
        self.assertTrue(metadata['probe_ok'])
        self.assertEqual(metadata['length_kind'], 'unknown')

    def test_non_numeric_content_length_is_rejected(self):
        metadata = {}
        with patch('requests.get',
                   return_value=self._fake_response({'Content-Length': 'abc'})):
            result = self.engine.check_url_support_range(self.base_url + '/mock', None,
                                                        metadata=metadata)
        self.assertEqual(result, (False, 0))
        self.assertFalse(metadata['probe_ok'])


class UnknownSizeFrameValidationTests(UnknownSizeFixture):
    """正式响应里的非法/冲突分帧信息必须拒绝（Codex 审查 P2-1、P2-2）"""

    def _downloader(self, headers, body=b'hello'):
        temp = tempfile.TemporaryDirectory(prefix='daw-frame-')
        self.addCleanup(temp.cleanup)
        downloader = ChunkDownloader(
            chunk_id=0, task_id='frame-task', url=self.base_url + '/mock',
            start_byte=0, end_byte=-1, temp_file=os.path.join(temp.name, 'x.tmp'),
            use_range=False, total_size=0, unknown_size=True,
            user_agent='test', timeout=5, retry_times=1,
        )
        response = Mock()
        response.status_code = 200
        response.headers = CaseInsensitiveDict(headers)
        response.iter_content = lambda chunk_size=8192: iter([body] if body else [])
        response.__enter__ = lambda _self: response
        response.__exit__ = lambda *_args: False
        return downloader, patch('requests.get', return_value=response)

    def test_conflicting_transfer_encoding_and_length_is_rejected(self):
        downloader, patcher = self._downloader(
            {'Transfer-Encoding': 'chunked', 'Content-Length': str(len(b'hello'))})
        with patcher:
            self.assertFalse(downloader.download())
        self.assertEqual(downloader.error_kind(), 'protocol')

    def test_blank_content_length_in_real_response_is_rejected(self):
        downloader, patcher = self._downloader({'Content-Length': ''})
        with patcher:
            self.assertFalse(downloader.download())
        self.assertEqual(downloader.error_kind(), 'protocol')

    def test_normal_stream_without_length_still_succeeds(self):
        downloader, patcher = self._downloader({})
        with patcher:
            self.assertTrue(downloader.download())
        self.assertEqual(downloader.downloaded_bytes, len(b'hello'))

    def test_unknown_mode_rejects_inconsistent_constructor_arguments(self):
        temp = tempfile.TemporaryDirectory(prefix='daw-frame-')
        self.addCleanup(temp.cleanup)
        with self.assertRaises(ValueError):
            ChunkDownloader(chunk_id=0, task_id='bad', url='http://example.test/x',
                            start_byte=0, end_byte=99, temp_file=os.path.join(temp.name, 'x.tmp'),
                            use_range=False, total_size=100, unknown_size=True)


class EofGuardTests(UnknownSizeFixture):
    """共享校验函数的零哨兵条件（Codex 审查 P2-3）"""

    def _make_task(self, total_size, payload):
        task_id = str(uuid.uuid4())
        save_path = os.path.join(self.config.download_dir, f'{task_id}.bin')
        os.makedirs(self.config.download_dir, exist_ok=True)
        with open(save_path, 'wb') as handle:
            handle.write(payload)
        self.assertTrue(self.db.create_task_with_chunks(
            task_id=task_id, url='http://example.test/x', filename='x.bin', save_path=save_path,
            total_size=total_size, support_range=False, thread_count=1, status='downloading'))
        self.engine.task_run_ids[task_id] = 1
        return task_id, save_path

    def test_known_size_task_cannot_bypass_check_with_eof_flag(self):
        task_id, save_path = self._make_task(100, b'0123456789')
        self.engine._verify_and_finish(task_id, save_path, run_id=1, eof_confirmed=True)
        task = self.db.get_task(task_id)
        self.assertEqual(task['status'], 'failed')
        self.assertEqual(task['total_size'], 100, '已知大小不能被回填成实际字节数')
        self.assertIn('大小', task.get('error_message') or '')

    def test_zero_sentinel_task_backfills_actual_size(self):
        task_id, save_path = self._make_task(0, b'0123456789')
        self.engine._verify_and_finish(task_id, save_path, run_id=1, eof_confirmed=True)
        task = self.db.get_task(task_id)
        self.assertEqual(task['status'], 'completed')
        self.assertEqual(task['total_size'], 10)

    def test_browser_context_task_cannot_use_eof_flag(self):
        task_id, save_path = self._make_task(0, b'0123456789')
        self.db.update_task_status(task_id, 'downloading')
        with self.db._lock:
            conn = self.db._get_connection()
            try:
                with conn:
                    conn.execute('UPDATE download_tasks SET browser_context = ? WHERE task_id = ?',
                                 (protect({'url': 'http://example.test/x', 'headers': {}, 'media': False}),
                                  task_id))
            finally:
                conn.close()
        self.engine._verify_and_finish(task_id, save_path, run_id=1, eof_confirmed=True)
        task = self.db.get_task(task_id)
        self.assertEqual(task['status'], 'failed')
        self.assertEqual(task['total_size'], 0)


class UnknownSizeRetryAndResumeTests(UnknownSizeFixture):
    """未知流：失败重试要清空残片，暂停恢复不能拼接旧内容"""

    def test_retry_after_broken_stream_clears_stale_bytes(self):
        self.config.set('retry_times', 2)
        task = self.run_task(self.add('/drop-once'))
        self.assertEqual(Path(task['save_path']).read_bytes(), PAYLOAD)
        self.assertEqual(task['total_size'], len(PAYLOAD))
        # 必须真的发生过重试：正式 GET 恰好两次（不把探测算进来）
        real_requests = [item for item in self.server.requests
                        if item[0] == '/drop-once' and item[1] is None]
        self.assertEqual(len(real_requests), 2, real_requests)
        # 重试必须清空第一次写入的 20 KB 错数据，不能把两份内容拼在一起
        self.assertNotIn(b'zzzz', Path(task['save_path']).read_bytes())

    def test_broken_chunked_stream_never_completes(self):
        self.config.set('retry_times', 1)
        task_id = self.add('/broken-chunked')
        task = self.run_task(task_id, status='failed')
        self.assertFalse(os.path.exists(task['save_path']), '缺结束块的流不能被发布')

    def test_etag_change_during_download_fails(self):
        task_id = self.add('/etag-change')
        task = self.run_task(task_id, status='failed')
        self.assertIn('版本', task.get('error_message') or '')
        self.assertFalse(os.path.exists(task['save_path']))

    def test_pause_and_resume_keeps_same_stream(self):
        self.config.set('speed_limit', 8192)  # 压慢一点，续流期间才有窗口插暂停
        task_id = self.add('/no-length')
        self.assertTrue(self.manager.start_task(task_id))
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            if int(self.db.get_task(task_id).get('downloaded_size') or 0) >= 2048:
                break
            time.sleep(0.02)
        self.assertTrue(self.engine.pause_download(task_id))
        time.sleep(0.6)
        paused = int(self.db.get_task(task_id).get('downloaded_size') or 0)
        self.assertGreater(paused, 0, '暂停前应该已经下到一些字节')
        time.sleep(0.6)
        self.assertEqual(int(self.db.get_task(task_id).get('downloaded_size') or 0), paused,
                         '暂停后不应继续写盘')
        self.assertTrue(self.engine.resume_download(task_id))
        task = self.wait(task_id)
        self.assertEqual(Path(task['save_path']).read_bytes(), PAYLOAD)
        self.assertEqual(task['total_size'], len(PAYLOAD))
        # 同响应恢复：正式请求（无 Range）只应该发过一次，不能重开连接从头下
        real_requests = [item for item in self.server.requests if item[0] == '/no-length']
        self.assertEqual([item[1] for item in real_requests].count(None), 1, real_requests)

    def test_restart_after_pause_redownloads_from_zero(self):
        task_id = self.add('/no-length')
        self.db.update_task_status(task_id, 'paused')
        temp_file = Path(self.config.temp_dir) / f'{task_id}.tmp'
        temp_file.parent.mkdir(parents=True, exist_ok=True)
        temp_file.write_bytes(b'x' * 5000)
        self.db.update_task_progress(task_id, 5000, 0)
        self.run_task(task_id)
        self.assertEqual(Path(self.db.get_task(task_id)['save_path']).read_bytes(), PAYLOAD)

    def test_browser_task_resume_is_rejected_when_probe_turns_unknown(self):
        task_id = str(uuid.uuid4())
        save_path = os.path.join(self.config.download_dir, 'browser.bin')
        context = {'url': self.base_url + '/no-length', 'headers': {}, 'media': False}
        self.assertTrue(self.db.create_task_with_chunks(
            task_id=task_id, url=self.base_url + '/no-length', filename='browser.bin',
            save_path=save_path, total_size=len(PAYLOAD), support_range=False, thread_count=1,
            browser_context=protect(normalize_context(self.base_url + '/no-length', context)),
            status='downloading'))
        self.engine.task_run_ids[task_id] = 1
        self.assertFalse(self.engine.start_download(task_id, resume=True))
        task = self.db.get_task(task_id)
        self.assertEqual(task['status'], 'failed')
        self.assertFalse(os.path.exists(save_path))


if __name__ == '__main__':
    unittest.main()
