"""Independent file-handoff regressions using local HTTP and disposable data only."""
from concurrent.futures import ThreadPoolExecutor
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import re
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
from urllib.parse import urlsplit
import uuid

import requests

from downloader.browser.bridge import BridgeServer, call_desktop
from downloader.browser.native_host import forward
from downloader.browser.security import browser_get, normalize_context, unprotect
from downloader.core.download_engine import DownloadEngine
from downloader.core.task_manager import TaskManager
from downloader.database.db_manager import DatabaseManager


FILE_BYTES = b'daw-file-fixture\n' + bytes(range(256)) * 256


class FileHandler(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass

    def do_GET(self):
        self.server.seen.append((self.path, dict(self.headers)))
        path = urlsplit(self.path).path
        redirects = {
            '/redirect-local': '/report.pdf',
            '/redirect-cross': self.server.other + '/report.pdf',
            '/redirect-stream': '/master%2Em3u8',
            '/redirect-cdn': 'https://media.douyinvod.com/video.mp4',
        }
        if path in redirects:
            self.send_response(302)
            self.send_header('Location', redirects[path])
            self.end_headers()
            return
        if path == '/private.pdf' and (self.headers.get('Cookie') != 'session=file-fixture'
                                      or self.headers.get('Referer') != self.server.page):
            self.send_error(403)
            return
        payload = b'' if path == '/empty' else FILE_BYTES
        size_known = path != '/unknown'
        mime = {
            '.pdf': 'application/pdf', '.zip': 'application/zip',
            '.docx': 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
            '.exe': 'application/vnd.microsoft.portable-executable', '.txt': 'text/plain',
            '.json': 'application/json', '.mp4': 'video/mp4',
        }.get(Path(path).suffix, 'application/octet-stream')
        if path == '/login':
            mime = 'text/html; charset=utf-8'
        start, end = 0, len(payload) - 1
        match = re.fullmatch(r'bytes=(\d+)-(\d*)', self.headers.get('Range', '')) if size_known and payload else None
        if match:
            start = int(match[1])
            end = min(int(match[2]) if match[2] else end, end)
        body = payload[start:end + 1]
        self.send_response(206 if match else 200)
        if path != '/missing-type':
            self.send_header('Content-Type', mime)
        if size_known:
            self.send_header('Content-Length', str(len(body)))
        self.send_header('ETag', '"file-fixture-v1"')
        if match:
            self.send_header('Content-Range', f'bytes {start}-{end}/{len(payload)}')
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            pass


class BrowserFileDownloadTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.servers = [ThreadingHTTPServer(('127.0.0.1', 0), FileHandler) for _ in range(2)]
        for index, server in enumerate(cls.servers):
            server.seen = []
            server.other = f'http://127.0.0.1:{cls.servers[1-index].server_port}'
            server.page = f'http://127.0.0.1:{server.server_port}/downloads'
            threading.Thread(target=server.serve_forever, daemon=True).start()
        cls.base = f'http://127.0.0.1:{cls.servers[0].server_port}'

    @classmethod
    def tearDownClass(cls):
        for server in cls.servers:
            server.shutdown()
            server.server_close()

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='daw-file-review-')
        self.root = Path(self.temp.name)
        self.config = SimpleNamespace(
            temp_dir=str(self.root / 'temp'), download_dir=str(self.root / 'out'),
            thread_count=4, timeout=2, retry_times=1, user_agent='FileTest', speed_limit=0,
            proxies={'http': '', 'https': ''},
        )
        self.db = DatabaseManager(str(self.root / 'test.db'))
        self.engine = DownloadEngine(self.db, self.config)
        self.manager = TaskManager(self.engine, self.db)
        self.bridge = BridgeServer(self.manager, Mock(), path=self.root / 'endpoint')
        self.bridge.start()
        for server in self.servers:
            server.seen.clear()

    def tearDown(self):
        self.bridge.close()
        self.manager.shutdown()
        time.sleep(1.05)  # Existing progress-monitor interval; allow its final read to finish.
        self.temp.cleanup()

    def message(self, path='/report.pdf', **extra):
        return {
            'action': 'download_file', 'request_id': 'file-' + str(uuid.uuid4()),
            'url': self.base + path,
            'headers': {'Cookie': 'session=file-fixture', 'Referer': self.servers[0].page},
            **extra,
        }

    def send(self, path='/report.pdf', **extra):
        return call_desktop(self.message(path, **extra), self.bridge.path)

    def completed(self, result):
        self.assertTrue(result['ok'], result)
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            task = self.db.get_task(result['task_id'])
            if task['status'] in ('completed', 'failed', 'verify_failed'):
                self.assertEqual(task['status'], 'completed', task.get('error_message'))
                self.assertEqual(hashlib.sha256(Path(task['save_path']).read_bytes()).digest(),
                                 hashlib.sha256(FILE_BYTES).digest())
                return task
            time.sleep(.02)
        self.fail('File did not finish')

    def test_file_types_unknown_extension_and_missing_mime_keep_exact_bytes(self):
        for path in ('/report.pdf', '/archive.zip', '/report.docx', '/setup.exe', '/note.txt',
                     '/data.json', '/opaque', '/missing-type'):
            with self.subTest(path=path):
                task = self.completed(self.send(path))
                self.assertEqual(task['filename'], path[1:])
                self.assertEqual(task['download_type'], 'file')

    def test_signed_query_is_preserved_and_file_context_is_encrypted(self):
        suffix = '/private.pdf?sig=a%2Bb+c&part=1&part=2'
        task = self.completed(self.send(suffix))
        self.assertTrue(all(path == suffix for path, _ in self.servers[0].seen))
        self.assertNotIn('file-fixture', task['browser_context'])
        context = unprotect(task['browser_context'])
        self.assertIs(context['media'], False)
        self.assertIs(normalize_context(task['url'], context)['media'], False)

    def test_replay_and_simultaneous_replay_create_one_task(self):
        message = self.message()
        with ThreadPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(lambda _: call_desktop(message, self.bridge.path), range(4)))
        self.assertTrue(all(result == results[0] for result in results))
        self.assertEqual(len(self.db.get_all_tasks()), 1)
        self.completed(results[0])

    def test_file_and_video_receipts_have_separate_namespaces(self):
        common_id = str(uuid.uuid4())
        self.completed(self.send(request_id=common_id))
        result = call_desktop({'action': 'download', 'kind': 'mp4', 'url': self.base + '/movie.mp4',
                               'request_id': common_id}, self.bridge.path)
        self.completed(result)
        self.assertEqual(len(self.db.get_all_tasks()), 2)

    def test_existing_file_is_preserved_and_receipt_has_actual_filename(self):
        out = Path(self.config.download_dir)
        out.mkdir(exist_ok=True)
        (out / 'report.pdf').write_bytes(b'existing-user-file')
        first, second = self.send(), self.send()
        self.completed(first)
        self.completed(second)
        self.assertNotEqual(first['filename'], second['filename'])
        self.assertNotEqual(first['filename'], 'report.pdf')
        self.assertEqual((out / 'report.pdf').read_bytes(), b'existing-user-file')

    def test_restored_file_task_keeps_its_authenticated_file_mode(self):
        self.manager._scheduling_paused = True
        result = self.send('/private.pdf')
        self.manager.shutdown()
        self.engine = DownloadEngine(self.db, self.config)
        self.manager = TaskManager(self.engine, self.db)
        self.bridge.manager = self.manager
        self.assertTrue(self.manager.start_task(result['task_id']))
        self.assertIs(unprotect(self.completed(result)['browser_context'])['media'], False)

    def test_html_unknown_size_and_empty_files_are_not_created(self):
        for path in ('/login', '/unknown', '/empty'):
            self.assertFalse(self.send(path)['ok'], path)
        self.assertEqual(self.db.get_all_tasks(), [])

    def test_encoded_manifest_paths_and_redirects_cannot_bypass_file_guard(self):
        for path in ('/master%2Em3u8', '/manifest.%6Dpd', '/redirect-stream', '/redirect-cdn'):
            with self.subTest(path=path):
                self.assertFalse(self.send(path)['ok'], path)
        self.assertEqual(self.db.get_all_tasks(), [])

    def test_url_and_header_validation_do_not_create_or_leak_requests(self):
        for fields in (
            {'url': 'https://user:secret@files.test/a'}, {'url': 'file:///C:/a'},
            {'headers': {'Cookie': 'fixture\r\nHost: other'}},
            {'url': self.base + '/%2e%2e%2fescape.pdf'},
            {'url': self.base + '/NUL.pdf'},
        ):
            result = self.send(**fields)
            self.assertFalse(result['ok'], fields)
            self.assertNotIn('secret', str(result))
        self.assertEqual(self.db.get_all_tasks(), [])
        self.assertEqual(self.servers[0].seen, [])

    def test_cross_origin_redirect_drops_context_but_same_origin_keeps_it(self):
        self.completed(self.send('/redirect-local'))
        headers = next(headers for path, headers in self.servers[0].seen if path == '/report.pdf')
        self.assertEqual(headers.get('Cookie'), 'session=file-fixture')
        self.completed(self.send('/redirect-cross'))
        for _, headers in self.servers[1].seen:
            self.assertNotIn('Cookie', headers)
            self.assertNotIn('Referer', headers)

    def test_file_mode_keeps_https_downgrade_protection(self):
        response = requests.Response()
        response.status_code = 302
        response.headers['Location'] = 'http://files.test/report.pdf'
        response._content = b''
        response._content_consumed = True
        url = 'https://files.test/report.pdf'
        context = normalize_context(url, {'media': False, 'headers': {}})
        with patch('downloader.browser.security.requests.get', return_value=response) as get:
            with self.assertRaisesRegex(ValueError, 'downgrade'):
                with browser_get(url, context, stream=True):
                    self.fail('Downgraded request succeeded')
        self.assertEqual(get.call_count, 1)

    def test_file_feature_survives_missing_video_tools_and_ping_does_not_launch(self):
        with patch('downloader.browser.bridge.require_tools', side_effect=ValueError('missing')):
            running = self.bridge.dispatch({'action': 'ping'})
        with patch('downloader.browser.native_host.call_desktop', side_effect=OSError('stopped')), \
                patch('downloader.core.youtube_downloader.require_tools', side_effect=ValueError('missing')), \
                patch('downloader.browser.native_host.subprocess.Popen') as launch:
            cold = forward({'action': 'ping'})
        self.assertEqual(running['capabilities'], [])
        self.assertEqual(cold['capabilities'], [])
        self.assertEqual(running['features'], ['file_download_v1'])
        self.assertEqual(cold['features'], running['features'])
        launch.assert_not_called()
        self.completed(self.send())

    def test_video_messages_cannot_opt_out_of_media_mime_validation(self):
        result = call_desktop({'action': 'download', 'kind': 'mp4', 'media': False,
                               'url': self.base + '/report.pdf', 'request_id': str(uuid.uuid4())}, self.bridge.path)
        self.assertFalse(result['ok'])
        self.assertEqual(self.db.get_all_tasks(), [])

    def test_media_mode_is_boolean_and_existing_contexts_stay_media(self):
        url = self.base + '/report.pdf'
        self.assertIs(normalize_context(url, {'headers': {}})['media'], True)
        for value in (None, 0, 1, 'false', [], {}):
            with self.assertRaises(ValueError):
                normalize_context(url, {'media': value})

    def test_web_pages_cannot_bypass_native_transport(self):
        url = f'http://127.0.0.1:{self.bridge.server.server_port}/v1'
        response = requests.post(url, json=self.message(), timeout=2, proxies=self.config.proxies)
        self.assertEqual(response.status_code, 403)
        response = requests.post(url, json=self.message(), headers={
            'Authorization': 'Bearer ' + self.bridge.token, 'Origin': 'https://page.test'},
            timeout=2, proxies=self.config.proxies)
        self.assertEqual(response.status_code, 403)
        self.assertEqual(self.db.get_all_tasks(), [])


if __name__ == '__main__':
    unittest.main()
