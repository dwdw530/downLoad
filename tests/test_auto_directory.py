"""按文件类型自动分目录：纯函数、任务创建与浏览器入口的隔离用例。"""
import os
from pathlib import Path
import re
import tempfile
import threading
import time
import hashlib
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace
from unittest.mock import Mock, patch
import uuid

from downloader.browser.bridge import BridgeServer
from downloader.core.download_engine import DownloadEngine
from downloader.core.task_manager import TaskManager
from downloader.database.db_manager import DatabaseManager
from downloader.utils.config import ConfigManager
from downloader.utils.file_utils import (FILE_CATEGORY_LABELS, guess_file_category,
                                         resolve_download_directory)
from test_download_regressions import PAYLOAD


class Handler(BaseHTTPRequestHandler):
    """本地源：用 Content-Disposition 决定最终文件名，覆盖分类路由的关键路径。"""
    NAMES = {
        '/report': 'report.pdf',
        '/archive': 'bundle.zip',
        '/program': 'setup.exe',
        '/movie': 'movie.mp4',
    }

    def log_message(self, *_args):
        pass

    def do_GET(self):
        self.server.seen.append(self.path)
        path = self.path.split('?')[0]
        if path == '/redirect-doc':
            self.send_response(302)
            self.send_header('Location', '/report')
            self.end_headers()
            return
        if path == '/unsafe':
            self.send_error(404)
            return
        if path in ('/unknown', '/empty'):
            self.send_response(200)
            self.send_header('Content-Disposition', 'attachment; filename="stream.txt"')
            if path == '/empty':
                self.send_header('Content-Length', '0')
            self.end_headers()
            if path == '/unknown':
                self.wfile.write(PAYLOAD)
            return
        name = self.NAMES.get(path)
        range_header = self.headers.get('Range')
        start, end = 0, len(PAYLOAD) - 1
        if range_header and range_header != 'bytes=0-0':
            match = re.fullmatch(r'bytes=(\d+)-(\d*)', range_header)
            if match:
                start = int(match[1])
                end = int(match[2]) if match[2] else end
                self.send_response(206)
                self.send_header('Content-Range', f'bytes {start}-{end}/{len(PAYLOAD)}')
            else:
                self.send_response(200)
        else:
            self.send_response(200)
        body = PAYLOAD[start:end + 1]
        if name:
            if name.lower().endswith('.mp4'):
                self.send_header('Content-Type', 'video/mp4')
            self.send_header('Content-Disposition', f'attachment; filename="{name}"')
        self.send_header('ETag', '"auto-directory-v1"')
        self.send_header('Accept-Ranges', 'bytes')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)


class ResolveDirectoryTests(unittest.TestCase):
    def test_disabled_returns_base_untouched(self):
        self.assertEqual(resolve_download_directory('/base', 'a.pdf', False), '/base')
        self.assertEqual(resolve_download_directory('', 'a.pdf', False), '')

    def test_enabled_uses_fixed_category_labels_only(self):
        for filename, label in (('a.pdf', '文档'), ('a.MP4', '视频'), ('a.zip', '压缩包'),
                                ('a.exe', '程序'), ('a.mp3', '音频'), ('a.jpg', '其他'),
                                ('no-suffix', '其他')):
            with self.subTest(filename=filename):
                expected = os.path.join('/base', label)
                self.assertEqual(resolve_download_directory('/base', filename, True), expected)
                self.assertIn(guess_file_category(filename), FILE_CATEGORY_LABELS)

    def test_double_extension_and_uppercase_follow_existing_rules(self):
        self.assertEqual(resolve_download_directory('/b', 'report.pdf.exe', True),
                         os.path.join('/b', '程序'))
        self.assertEqual(resolve_download_directory('/b', 'ARCHIVE.ZIP', True),
                         os.path.join('/b', '压缩包'))

    def test_pure_function_does_not_create_directories(self):
        with tempfile.TemporaryDirectory() as name:
            base = os.path.join(name, 'not-created')
            target = resolve_download_directory(base, 'a.pdf', True)
            self.assertEqual(target, os.path.join(base, '文档'))
            self.assertFalse(os.path.exists(base))


class AutoDirectoryEngineTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        cls.server.seen = []
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.base = f'http://127.0.0.1:{cls.server.server_port}'

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(2)

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='daw-autodir-')
        self.root = Path(self.temp.name)
        self.config = ConfigManager(str(self.root / 'config.json'))
        self.config.download_dir = str(self.root / 'out')
        self.config.temp_dir = str(self.root / 'parts')
        self.config.set('timeout', 2)
        self.db = DatabaseManager(str(self.root / 'downloads.db'))
        self.env_patch = patch.dict(os.environ, {'NO_PROXY': '*', 'no_proxy': '*'})
        self.env_patch.start()
        self.engine = DownloadEngine(self.db, self.config)
        self.manager = TaskManager(self.engine, self.db)

    def tearDown(self):
        self.manager.shutdown()
        self.env_patch.stop()
        self.temp.cleanup()

    def create(self, path='/report', **kwargs):
        task_id = self.engine.create_download_task(self.base + path, **kwargs)
        self.assertIsNotNone(task_id)
        return task_id

    def wait_completed(self, task_id):
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline:
            task = self.db.get_task(task_id)
            if task['status'] == 'completed':
                return task
            self.assertNotIn(task['status'], ('failed', 'verify_failed', 'cancelled'))
            time.sleep(0.02)
        self.fail('分类下载未完成')

    def test_real_downloads_create_all_six_directories_without_changing_temp_root(self):
        self.config.auto_classify_downloads = True
        files = [('clip.MP4', '视频'), ('song.mp3', '音频'), ('report.pdf', '文档'),
                 ('bundle.ZIP', '压缩包'), ('tool.exe', '程序'), ('picture.jpg', '其他')]
        for filename, category in files:
            with self.subTest(filename=filename):
                task_id = self.create(filename=filename)
                self.assertTrue(self.manager.start_task(task_id))
                task = self.wait_completed(task_id)
                self.assertEqual(Path(task['save_path']), self.root / 'out' / category / filename)
                self.assertEqual(Path(task['save_path']).read_bytes(), PAYLOAD)
                self.assertEqual(task['actual_hash'], hashlib.md5(PAYLOAD).hexdigest())
        self.assertEqual(Path(self.config.temp_dir), self.root / 'parts')

    def test_unknown_size_and_empty_downloads_are_classified_with_actual_hashes(self):
        self.config.auto_classify_downloads = True
        for path, body in (('/unknown', PAYLOAD), ('/empty', b'')):
            with self.subTest(path=path):
                task_id = self.create(path)
                self.assertTrue(self.manager.start_task(task_id))
                task = self.wait_completed(task_id)
                self.assertEqual(Path(task['save_path']).parent, self.root / 'out' / '文档')
                self.assertEqual(Path(task['save_path']).read_bytes(), body)
                self.assertEqual(task['total_size'], len(body))
                self.assertEqual(task['actual_hash'], hashlib.md5(body).hexdigest())

    def test_concurrent_creations_reserve_distinct_paths_in_the_final_directory(self):
        self.config.auto_classify_downloads = True
        barrier = threading.Barrier(4)
        task_ids, errors = [], []

        def create():
            try:
                barrier.wait(3)
                task_ids.append(self.engine.create_download_task(self.base + '/report'))
            except BaseException as error:
                errors.append(error)

        workers = [threading.Thread(target=create) for _ in range(4)]
        for worker in workers:
            worker.start()
        for worker in workers:
            worker.join(6)
        self.assertFalse(any(worker.is_alive() for worker in workers))
        self.assertEqual(errors, [])
        self.assertEqual(len(task_ids), 4)
        self.assertNotIn(None, task_ids)
        paths = [Path(self.db.get_task(task_id)['save_path']) for task_id in task_ids]
        self.assertEqual(len(set(paths)), 4)
        self.assertEqual({path.parent for path in paths}, {self.root / 'out' / '文档'})
        for task_id in task_ids:
            self.assertTrue(self.manager.start_task(task_id))
        for task_id in task_ids:
            task = self.wait_completed(task_id)
            self.assertEqual(Path(task['save_path']).read_bytes(), PAYLOAD)

    def test_space_precheck_uses_classified_target_and_does_not_create_a_task_on_failure(self):
        self.config.auto_classify_downloads = True
        with patch.object(self.engine, '_space_requirements', wraps=self.engine._space_requirements) as requirements, \
                patch('downloader.core.download_engine.check_free_space',
                      return_value=(False, '空间不足')):
            self.assertIsNone(self.engine.create_download_task(self.base + '/report'))
        self.assertEqual(Path(requirements.call_args.args[1]),
                         self.root / 'out' / '文档' / 'report.pdf')
        self.assertEqual(self.db.get_all_tasks(), [])

    def test_content_disposition_filename_decides_the_directory(self):
        self.config.auto_classify_downloads = True
        self.assertEqual(Path(self.db.get_task(self.create('/report'))['save_path']),
                         self.root / 'out' / '文档' / 'report.pdf')
        self.assertEqual(Path(self.db.get_task(self.create('/archive'))['save_path']),
                         self.root / 'out' / '压缩包' / 'bundle.zip')
        self.assertEqual(Path(self.db.get_task(self.create('/program'))['save_path']),
                         self.root / 'out' / '程序' / 'setup.exe')

    def test_redirect_without_suffix_still_uses_content_disposition(self):
        self.config.auto_classify_downloads = True
        task = self.db.get_task(self.create('/redirect-doc'))
        self.assertEqual(Path(task['save_path']), self.root / 'out' / '文档' / 'report.pdf')

    def test_explicit_filename_wins_over_server_name(self):
        self.config.auto_classify_downloads = True
        task = self.db.get_task(self.create('/report', filename='clip.mp4'))
        self.assertEqual(Path(task['save_path']), self.root / 'out' / '视频' / 'clip.mp4')

    def test_explicit_directory_is_used_even_when_equal_to_default_root(self):
        self.config.auto_classify_downloads = True
        task = self.db.get_task(self.create('/report', save_path=self.config.download_dir))
        self.assertEqual(Path(task['save_path']), self.root / 'out' / 'report.pdf')
        other = self.root / 'manual'
        task = self.db.get_task(self.create('/archive', save_path=str(other)))
        self.assertEqual(Path(task['save_path']), other / 'bundle.zip')

    def test_disabled_keeps_old_flat_layout(self):
        self.assertFalse(self.config.auto_classify_downloads)
        task = self.db.get_task(self.create('/report'))
        self.assertEqual(Path(task['save_path']), self.root / 'out' / 'report.pdf')

    def test_existing_file_in_root_does_not_force_rename_in_category(self):
        self.config.auto_classify_downloads = True
        (self.root / 'out').mkdir(parents=True)
        (self.root / 'out' / 'report.pdf').write_bytes(b'user file')
        task = self.db.get_task(self.create('/report'))
        self.assertEqual(Path(task['save_path']), self.root / 'out' / '文档' / 'report.pdf')

    def test_same_category_name_collision_is_not_overwritten(self):
        self.config.auto_classify_downloads = True
        first = self.db.get_task(self.create('/report'))
        second = self.db.get_task(self.create('/report'))
        self.assertEqual(Path(first['save_path']).name, 'report.pdf')
        self.assertEqual(Path(second['save_path']).name, 'report (1).pdf')
        self.assertEqual(Path(second['save_path']).parent, self.root / 'out' / '文档')

    def test_old_task_keeps_saved_path_after_switch_is_enabled(self):
        task = self.db.get_task(self.create('/report'))
        original = task['save_path']
        self.config.auto_classify_downloads = True
        self.manager.start_task(task['task_id'])
        deadline = __import__('time').monotonic() + 8
        while __import__('time').monotonic() < deadline:
            current = self.db.get_task(task['task_id'])
            if current['status'] in ('completed', 'failed', 'verify_failed', 'cancelled'):
                break
            __import__('time').sleep(0.02)
        self.assertEqual(self.db.get_task(task['task_id'])['save_path'], original)
        current = self.wait_completed(task['task_id'])
        self.assertEqual(Path(current['save_path']).read_bytes(), PAYLOAD)

    def test_video_task_adds_only_one_category_layer(self):
        self.config.auto_classify_downloads = True
        with patch('downloader.core.download_engine.require_tools'):
            task_id = self.engine.create_youtube_task(
                'https://youtu.be/abcdefghijk', 'movie.mp4', None, kind='youtube')
        self.assertIsNotNone(task_id)
        self.assertEqual(Path(self.db.get_task(task_id)['save_path']),
                         self.root / 'out' / '视频' / 'movie.mp4')

    def test_video_with_explicit_directory_is_not_classified_twice(self):
        self.config.auto_classify_downloads = True
        manual = str(self.root / 'manual-video')
        with patch('downloader.core.download_engine.require_tools'):
            task_id = self.engine.create_youtube_task(
                'https://youtu.be/abcdefghijk', 'movie.mp4', manual, kind='youtube')
        self.assertEqual(Path(self.db.get_task(task_id)['save_path']),
                         Path(manual) / 'movie.mp4')

    def test_config_default_off_and_round_trip(self):
        self.assertFalse(ConfigManager(str(self.root / 'legacy.json')).auto_classify_downloads)
        self.config.auto_classify_downloads = True
        self.assertTrue(self.config.save())
        self.assertTrue(ConfigManager(str(self.root / 'config.json')).auto_classify_downloads)


class AutoDirectoryBridgeTests(unittest.TestCase):
    """浏览器入口只调桌面 bridge，不改扩展或协议字段。"""

    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        cls.server.seen = []
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.base = f'http://127.0.0.1:{cls.server.server_port}'

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(2)

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='daw-autodir-bridge-')
        self.root = Path(self.temp.name)
        self.config = SimpleNamespace(
            temp_dir=str(self.root / 'temp'), download_dir=str(self.root / 'out'),
            thread_count=2, timeout=2, retry_times=1, user_agent='AutoDirTest', speed_limit=0,
            proxies={'http': '', 'https': ''}, auto_classify_downloads=True)
        self.db = DatabaseManager(str(self.root / 'test.db'))
        self.env_patch = patch.dict(os.environ, {'NO_PROXY': '*', 'no_proxy': '*'})
        self.env_patch.start()
        self.engine = DownloadEngine(self.db, self.config)
        self.manager = TaskManager(self.engine, self.db)
        self.bridge = BridgeServer(self.manager, Mock(), path=self.root / 'endpoint')
        self.bridge.start()

    def tearDown(self):
        self.bridge.close()
        self.manager.shutdown()
        self.env_patch.stop()
        self.temp.cleanup()

    def test_plain_right_click_file_lands_in_category_folder(self):
        result = self.bridge.download_file({
            'action': 'download_file', 'request_id': 'file-' + str(uuid.uuid4()),
            'url': self.base + '/report', 'headers': {}})
        self.assertTrue(result['ok'], result)
        task = self.db.get_task(result['task_id'])
        self.assertEqual(Path(task['save_path']), self.root / 'out' / '文档' / 'report.pdf')
        self.assertEqual(result['filename'], task['filename'])

    def test_video_dispatch_uses_one_category_and_receipt_is_stored_name(self):
        result = self.bridge.dispatch({
            'action': 'download', 'kind': 'mp4', 'url': self.base + '/movie',
            'request_id': 'video-' + str(uuid.uuid4())})
        self.assertTrue(result['ok'], result)
        task = self.db.get_task(result['task_id'])
        self.assertEqual(Path(task['save_path']), self.root / 'out' / '视频' / 'movie.mp4')
        self.assertEqual(result['filename'], task['filename'])

    def test_unknown_size_browser_file_is_still_rejected(self):
        for path in ('/unknown', '/empty'):
            result = self.bridge.download_file({
                'action': 'download_file', 'request_id': 'file-' + str(uuid.uuid4()),
                'url': self.base + path, 'headers': {}})
            self.assertFalse(result['ok'])
        self.assertEqual(self.db.get_all_tasks(), [])

    def test_video_receipt_uses_transactionally_renamed_filename(self):
        create = self.manager.add_task

        def reserve_competing_path(*args, **kwargs):
            directory = Path(kwargs['save_path'])
            directory.mkdir(parents=True, exist_ok=True)
            (directory / kwargs['filename']).write_bytes(b'keep-existing')
            return create(*args, **kwargs)

        with patch.object(self.manager, 'add_task', side_effect=reserve_competing_path):
            result = self.bridge.dispatch({
                'action': 'download', 'kind': 'mp4', 'url': self.base + '/movie',
                'request_id': 'video-' + str(uuid.uuid4())})
        self.assertTrue(result['ok'], result)
        task = self.db.get_task(result['task_id'])
        self.assertEqual(result['filename'], 'movie (1).mp4')
        self.assertEqual(task['filename'], result['filename'])
        self.assertEqual((self.root / 'out' / '视频' / 'movie.mp4').read_bytes(), b'keep-existing')


if __name__ == '__main__':
    unittest.main()
