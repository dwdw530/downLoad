# -*- coding: utf-8 -*-
"""本轮健壮性/限速改动的回归用例。

覆盖：Content-Disposition 取名、磁盘空间预检、共享限速、添加失败原因、
合并前空间不足、孤儿临时文件清扫、状态聚合与批量重试。

全部使用临时目录与本地 HTTP 夹具，不读写真实 dist 数据。
"""
import os
import sqlite3
import tempfile
import threading
import time
import unittest
import uuid
from datetime import datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from PIL import Image

from downloader.core.chunk_downloader import SpeedLimiter
from downloader.core.download_engine import DownloadEngine
from downloader.core.task_manager import TaskManager
from downloader.database.db_manager import DatabaseManager
from downloader.utils.config import ConfigManager
from downloader.utils.file_utils import check_free_space, parse_content_disposition

PAYLOAD = b'x' * 4096


def close_to(value, low, high):
    return low <= value <= high


class ContentDispositionTests(unittest.TestCase):
    """服务器文件名：filename* 优先，路径与保留名一律收敛"""

    def test_quoted_and_extended_forms(self):
        cases = {
            'attachment; filename="report.pdf"': 'report.pdf',
            "attachment; filename*=UTF-8''%E4%B8%AD%E6%96%87.zip": '中文.zip',
            "attachment; filename*=UTF-8''%E4%B8%AD%E6%96%87.zip; filename=\"ascii.zip\"": '中文.zip',
            'inline; filename=plain.bin': 'plain.bin',
            'attachment; filename="中文 空格.zip"': '中文 空格.zip',
        }
        for header, expected in cases.items():
            with self.subTest(header=header):
                self.assertEqual(parse_content_disposition(header), expected)

    def test_dangerous_names_are_reduced_or_rejected(self):
        # 带路径的只留最后一段，仍在下载目录内
        self.assertEqual(parse_content_disposition("attachment; filename=\"../../evil.exe\""), 'evil.exe')
        self.assertEqual(parse_content_disposition('attachment; filename="C:outside.txt"'), 'outside.txt')
        # 保留名、ADS、无法解码的名字直接放弃
        for header in ('attachment; filename="CON"', 'attachment; filename="COM1.zip"',
                       'attachment; filename="file.txt:secret"', 'attachment; filename="trailing."',
                       "attachment; filename*=nosuchcs''%E4.zip", 'attachment; filename=""',
                       'inline', ''):
            with self.subTest(header=header):
                self.assertIsNone(parse_content_disposition(header))

    def test_long_name_keeps_extension(self):
        name = parse_content_disposition('attachment; filename="' + 'a' * 200 + '.zip"')
        self.assertIsNotNone(name)
        self.assertLessEqual(len(name), 120)
        self.assertTrue(name.endswith('.zip'))


class FreeSpaceTests(unittest.TestCase):
    """空间预检：同盘合并需求、跨盘各算、读不到容量就放行"""

    def test_same_drive_requirements_are_summed(self):
        with patch('downloader.utils.file_utils.shutil.disk_usage',
                   return_value=SimpleNamespace(free=2000)):
            ok, detail = check_free_space([(os.getcwd(), 100, '临时目录'),
                                           (os.getcwd(), 100, '下载目录')])
        self.assertTrue(ok, detail)

        with patch('downloader.utils.file_utils.shutil.disk_usage',
                   return_value=SimpleNamespace(free=150)):
            ok, detail = check_free_space([(os.getcwd(), 100, '临时目录'),
                                           (os.getcwd(), 100, '下载目录')])
        self.assertFalse(ok)
        self.assertIn('空间不足', detail)
        self.assertIn('还差', detail)
        self.assertIn('临时目录与下载目录', detail)

    def test_unknown_capacity_never_blocks(self):
        with patch('downloader.utils.file_utils.shutil.disk_usage', side_effect=OSError('网络盘')):
            ok, detail = check_free_space([(os.getcwd(), 10 ** 12, '下载目录')])
        self.assertTrue(ok)
        self.assertIsNone(detail)

    def test_small_file_is_not_blocked_by_fixed_margin(self):
        with patch('downloader.utils.file_utils.shutil.disk_usage',
                   return_value=SimpleNamespace(free=2 * 1024 * 1024)):
            ok, _ = check_free_space([(os.getcwd(), 100 * 1024, '下载目录')])
        self.assertTrue(ok)


class SharedSpeedLimiterTests(unittest.TestCase):
    """共享限速：单任务准确、多线程不叠加、改回 0 立即放开"""

    def test_single_limit_is_respected(self):
        limiter = SpeedLimiter(4096)
        started = time.monotonic()
        limiter.acquire(4096)
        elapsed = time.monotonic() - started
        self.assertTrue(close_to(elapsed, 0.8, 1.8), f'限速偏差过大: {elapsed:.3f}s')

    def test_threads_share_one_bucket(self):
        limiter = SpeedLimiter(8192)
        threads = [threading.Thread(target=limiter.acquire, args=(2048,)) for _ in range(4)]
        started = time.monotonic()
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        elapsed = time.monotonic() - started
        self.assertTrue(close_to(elapsed, 0.8, 1.8), f'多任务限速叠加了: {elapsed:.3f}s')

    def test_unlimited_returns_immediately(self):
        limiter = SpeedLimiter(0)
        started = time.monotonic()
        limiter.acquire(10 * 1024 * 1024)
        self.assertLess(time.monotonic() - started, 0.2)


class _Handler(BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'

    def log_message(self, *args):
        pass

    def _range_ok(self, disposition=None, body=None, etag='"v1"'):
        body = PAYLOAD if body is None else body
        self.send_response(206)
        self.send_header('Content-Range', f'bytes 0-{len(body) - 1}/{len(PAYLOAD)}')
        self.send_header('Content-Length', str(len(body)))
        self.send_header('ETag', etag)
        self.send_header('Accept-Ranges', 'bytes')
        if disposition:
            self.send_header('Content-Disposition', disposition)
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path == '/ok':
            self._range_ok(body=PAYLOAD[:1])
        elif self.path == '/cd':
            self._range_ok(disposition="attachment; filename*=UTF-8''%E4%B8%AD%E6%96%87.zip",
                           body=PAYLOAD[:1])
        elif self.path == '/redir':
            self.send_response(302)
            self.send_header('Location', '/files/real.zip')
            self.send_header('Content-Length', '0')
            self.end_headers()
        elif self.path == '/files/real.zip':
            self._range_ok(body=PAYLOAD[:1])
        elif self.path == '/not-found':
            self.send_error(404)
        elif self.path == '/login':
            body = b'<html>login</html>'
            self.send_response(403)
            self.send_header('Content-Type', 'text/html; charset=utf-8')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif self.path == '/no-length':
            self.send_response(200)
            self.send_header('Content-Type', 'application/octet-stream')
            self.send_header('Connection', 'close')
            self.end_headers()
            self.wfile.write(PAYLOAD)
            self.close_connection = True
        elif self.path == '/broken':
            self.send_error(503)
        else:
            self.send_error(404)


class EngineAdditionTests(unittest.TestCase):
    """引擎侧：失败原因分类、文件名来源、创建前空间检查、合并前暂停"""

    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(('127.0.0.1', 0), _Handler)
        cls.base = f'http://127.0.0.1:{cls.server.server_address[1]}'
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='daw-robust-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.config = ConfigManager(str(self.root / 'config.json'))
        self.config.download_dir = str(self.root / 'out')
        self.config.temp_dir = str(self.root / 'temp')
        self.config.thread_count = 2
        self.config.set('timeout', 5)
        self.engine = DownloadEngine(DatabaseManager(str(self.root / 'db.sqlite3')), self.config)

    def test_failure_reasons(self):
        cases = {
            '/not-found': ('http_4xx', '404'),
            '/login': ('html_page', '403'),
            '/no-length': ('no_length', '未提供文件大小'),
            '/broken': ('http_5xx', '503'),
        }
        for path, (reason, fragment) in cases.items():
            with self.subTest(path=path):
                self.assertIsNone(self.engine.create_download_task(self.base + path))
                self.assertEqual(self.engine.last_add_error, reason)
                self.assertIn(fragment, self.engine.last_add_detail or '')

    def test_content_disposition_wins_over_url(self):
        task_id = self.engine.create_download_task(self.base + '/cd')
        self.assertIsNotNone(task_id, self.engine.last_add_detail)
        self.assertEqual(self.engine.db.get_task(task_id)['filename'], '中文.zip')

    def test_redirect_target_is_used_when_no_disposition(self):
        task_id = self.engine.create_download_task(self.base + '/redir')
        self.assertIsNotNone(task_id, self.engine.last_add_detail)
        self.assertEqual(self.engine.db.get_task(task_id)['filename'], 'real.zip')

    def test_explicit_filename_is_never_overridden(self):
        task_id = self.engine.create_download_task(self.base + '/cd', filename='自定义.zip')
        self.assertIsNotNone(task_id)
        self.assertEqual(self.engine.db.get_task(task_id)['filename'], '自定义.zip')

    def test_invalid_filename_rejected_before_network(self):
        with patch.object(self.engine, 'check_url_support_range') as probe:
            self.assertIsNone(self.engine.create_download_task(self.base + '/ok', filename='../x.zip'))
        probe.assert_not_called()
        self.assertEqual(self.engine.last_add_error, 'invalid_filename')

    def test_insufficient_space_rejects_task(self):
        with patch('downloader.utils.file_utils.shutil.disk_usage',
                   return_value=SimpleNamespace(free=1024)):
            self.assertIsNone(self.engine.create_download_task(self.base + '/ok'))
        self.assertEqual(self.engine.last_add_error, 'disk_space')
        self.assertIn('空间不足', self.engine.last_add_detail or '')
        self.assertEqual(self.engine.db.get_all_tasks(), [])

    def test_merge_pauses_and_keeps_chunks_when_space_is_short(self):
        task_id = str(uuid.uuid4())
        chunk_file = os.path.join(self.config.temp_dir, f'{task_id}.part0')
        os.makedirs(self.config.temp_dir, exist_ok=True)
        with open(chunk_file, 'wb') as handle:
            handle.write(b'0123456789')
        save_path = os.path.join(self.config.download_dir, 'merged.bin')
        self.assertTrue(self.engine.db.create_task_with_chunks(
            task_id=task_id, url='http://example.test/x', filename='merged.bin', save_path=save_path,
            total_size=10, support_range=True, thread_count=1,
            chunks=[(0, 0, 9, chunk_file)], status='downloading'))
        self.engine.task_run_ids[task_id] = 1

        with patch('downloader.utils.file_utils.shutil.disk_usage',
                   return_value=SimpleNamespace(free=0)):
            self.engine._merge_and_finish(task_id, save_path,
                                          self.engine.db.get_chunks(task_id), run_id=1)

        task = self.engine.db.get_task(task_id)
        self.assertEqual(task['status'], 'paused')
        self.assertIn('空间不足', task.get('error_message') or '')
        self.assertTrue(os.path.exists(chunk_file), '分块不能被删除')


class OrphanTempSweepTests(unittest.TestCase):
    """孤儿清扫：无主 + 超期 + 顶层 UUID 文件才删"""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='daw-sweep-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.config = ConfigManager(str(self.root / 'config.json'))
        self.config.temp_dir = str(self.root / 'temp')
        os.makedirs(self.config.temp_dir, exist_ok=True)
        self.db = DatabaseManager(str(self.root / 'db.sqlite3'))
        self.engine = DownloadEngine(self.db, self.config)

    def _make(self, name, age_days):
        path = os.path.join(self.config.temp_dir, name)
        with open(path, 'wb') as handle:
            handle.write(b'0123456789')
        stamp = (datetime.now() - timedelta(days=age_days)).timestamp()
        os.utime(path, (stamp, stamp))
        return path

    def test_only_orphaned_and_expired_files_are_removed(self):
        orphan_old = self._make(f'{uuid.uuid4()}.part0', 8)
        orphan_new = self._make(f'{uuid.uuid4()}.tmp', 1)
        live_id = str(uuid.uuid4())
        live_file = self._make(f'{live_id}.part0', 8)
        other_file = self._make('not-a-task.txt', 8)
        directory = os.path.join(self.config.temp_dir, f'{uuid.uuid4()}.part0')
        os.makedirs(directory)

        self.assertTrue(self.db.create_task_with_chunks(
            task_id=live_id, url='http://example.test/a', filename='a.bin',
            save_path=str(self.root / 'a.bin'), total_size=10, support_range=True,
            thread_count=1, chunks=[(0, 0, 9, live_file)], status='paused'))

        removed, freed = self.engine.sweep_orphan_temp()
        self.assertEqual((removed, freed), (1, 10))
        self.assertFalse(os.path.exists(orphan_old))
        for path in (orphan_new, live_file, other_file):
            self.assertTrue(os.path.exists(path), f'误删: {path}')
        self.assertTrue(os.path.isdir(directory))


class StatisticsAndRetryTests(unittest.TestCase):
    """状态聚合与原实现一致；批量重试只碰 failed"""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='daw-stats-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        config = ConfigManager(str(self.root / 'config.json'))
        config.download_dir = str(self.root / 'out')
        config.temp_dir = str(self.root / 'temp')
        self.db = DatabaseManager(str(self.root / 'db.sqlite3'))
        self.engine = DownloadEngine(self.db, config)
        self.manager = TaskManager(self.engine, self.db, max_concurrent=5)

    def _create(self, status, index):
        task_id = str(uuid.uuid4())
        self.assertTrue(self.db.create_task_with_chunks(
            task_id=task_id, url='http://example.test/x', filename=f'{status}{index}.bin',
            save_path=str(self.root / f'{status}{index}.bin'), total_size=10,
            support_range=True, thread_count=1, status=status))
        return task_id

    def test_statistics_match_full_scan(self):
        for status, count in (('pending', 2), ('paused', 1), ('failed', 3),
                              ('completed', 4), ('cancelled', 1), ('verifying', 1)):
            for index in range(count):
                self._create(status, index)

        stats = self.manager.get_statistics()
        manual = {'total': 0, 'downloading': 0, 'pending': 0, 'paused': 0,
                  'completed': 0, 'failed': 0, 'cancelled': 0}
        for task in self.db.get_all_tasks():
            manual['total'] += 1
            if task['status'] in manual:
                manual[task['status']] += 1
        self.assertEqual(stats, manual)
        self.assertEqual(self.db.get_total_speed(), 0.0)

    def test_retry_failed_only_touches_failed_tasks(self):
        failed = [self._create('failed', index) for index in range(3)]
        pending = [self._create('pending', index) for index in range(2)]

        def fake_start(task_id, resume=False):
            self.db.update_task_status(task_id, 'downloading')
            return True

        with patch.object(self.engine, 'start_download', side_effect=fake_start) as start:
            started, skipped = self.manager.retry_failed()

        self.assertEqual((started, skipped), (3, 0))
        self.assertGreaterEqual(start.call_count, 3)
        for task_id in failed:
            self.assertEqual(self.db.get_task(task_id)['status'], 'downloading')
        for task_id in pending:
            self.assertIn(self.db.get_task(task_id)['status'], ('pending', 'downloading'))

        for task_id in failed:
            self.db.update_task_status(task_id, 'completed')
        self.assertEqual(self.manager.retry_failed(), (0, 0))


class CardPaintTests(unittest.TestCase):
    """悬浮条渲染：圆角必须真有 alpha 过渡（Tk Canvas / 色键都做不到）"""

    BUSY = {'speed': 2_350_000, 'downloaded': 41_000_000, 'total': 120_000_000,
            'filename': 'ubuntu-24.04-desktop-amd64.iso', 'downloading': 1, 'pending': 2,
            'status': 'downloading'}
    IDLE = {'speed': 0, 'downloaded': 0, 'total': 0, 'filename': '',
            'downloading': 0, 'pending': 0, 'status': 'idle'}

    def render(self, mode='normal', theme='dark', state=None, samples=()):
        from downloader.ui.float_bar import THEMES, render_bar
        return render_bar(mode=mode, theme=THEMES[theme],
                          state=state or self.IDLE, samples=list(samples),
                          hovered=False, scale=1.0)

    def test_edges_have_alpha_gradient(self):
        card = self.render()
        alphas = {pixel[3] for pixel in card.getdata()}
        soft = sum(1 for pixel in card.getdata() if 0 < pixel[3] < 255)
        self.assertIn(0, alphas, '圆角外侧必须完全透明')
        self.assertIn(255, alphas, '卡片内部必须完全不透明')
        self.assertGreater(len(alphas), 8, '边缘没有多级 alpha，说明没做抗锯齿')
        self.assertGreater(soft, 200, f'半透明像素太少: {soft}')

    def test_transparent_outside_is_fully_clear(self):
        card = self.render()
        # 四角最外侧像素必须 alpha=0，否则画到白底上就是一围黑边
        for x, y in ((0, 0), (card.width - 1, 0), (0, card.height - 1),
                     (card.width - 1, card.height - 1)):
            self.assertEqual(card.getpixel((x, y))[3], 0, f'({x},{y}) 应该完全透明')

    def test_mini_and_full_sizes_follow_scale(self):
        from downloader.ui.float_bar import BAR_HEIGHT, BAR_WIDTH, MINI_BAR_HEIGHT, MINI_BAR_WIDTH
        self.assertEqual(self.render('mini').size, (MINI_BAR_WIDTH, MINI_BAR_HEIGHT))
        self.assertEqual(self.render('normal').size, (BAR_WIDTH, BAR_HEIGHT))
        self.assertEqual(self.render('mini', ).size, (MINI_BAR_WIDTH, MINI_BAR_HEIGHT))

    def test_idle_state_has_no_accent_curve(self):
        """空闲时不能画出一条贴着底部的绿线（旧实现会）"""
        from downloader.ui.float_bar import THEMES
        accent = THEMES['dark']['accent'].lstrip('#')
        accent_rgb = tuple(int(accent[i:i + 2], 16) for i in (0, 2, 4))
        card = self.render(samples=[(index, 0) for index in range(30)])
        greens = sum(1 for pixel in card.getdata()
                     if pixel[3] > 200
                     and all(abs(pixel[channel] - accent_rgb[channel]) <= 12
                             for channel in range(3)))
        self.assertEqual(greens, 0, '空闲状态不应该有主色像素')

    def test_busy_state_draws_accent(self):
        from downloader.ui.float_bar import THEMES
        accent = THEMES['dark']['accent'].lstrip('#')
        accent_rgb = tuple(int(accent[i:i + 2], 16) for i in (0, 2, 4))
        card = self.render(state=self.BUSY, samples=[(i, 500_000 + i * 40_000) for i in range(30)])
        # 降采样后不保证像素“等于”主色，按容差统计
        greens = sum(1 for pixel in card.getdata()
                     if pixel[3] > 200
                     and all(abs(pixel[channel] - accent_rgb[channel]) <= 12
                             for channel in range(3)))
        self.assertGreater(greens, 50, '下载中应该有进度条/曲线主色')

    def test_theme_palettes_are_complete(self):
        from downloader.ui.float_bar import THEMES
        required = {'card', 'border', 'border_hover', 'highlight', 'track',
                    'text', 'muted', 'faint', 'accent', 'accent_dim', 'idle_dot'}
        self.assertEqual(set(THEMES), {'dark', 'light'})
        for name, theme in THEMES.items():
            self.assertEqual(required - set(theme), set(), f'{name} 主题缺颜色')


if __name__ == '__main__':
    unittest.main()