"""Isolated video handoff, credential scope, native framing and restart tests."""
import io
import errno
import json
from pathlib import Path
import struct
import tempfile
import threading
import time
import unittest
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace
from unittest.mock import Mock, patch

import requests

from downloader.browser.bridge import BridgeServer, call_desktop, video_filename
from downloader.browser.native_host import read_message, write_message
from downloader.browser.registration import extension_id
from downloader.browser.security import browser_get, http_url, normalize_context, protect, unprotect
from downloader.core.download_engine import DownloadEngine
from downloader.core.task_manager import TaskManager
from downloader.database.db_manager import DatabaseManager

PAYLOAD = b'\x00\x00\x00\x18ftypmp42' + bytes(range(256)) * 256


class VideoHandler(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass

    def do_GET(self):
        self.server.seen.append((self.path, dict(self.headers)))
        if self.path == '/redirect':
            self.send_response(302)
            self.send_header('Location', self.server.target + '/movie.mp4')
            self.end_headers()
            return
        if self.path == '/private.mp4' and (self.headers.get('Cookie') != 'session=secret'
                                          or self.headers.get('Referer') != 'https://page.test/watch'):
            self.send_error(403)
            return
        start, end = 0, len(PAYLOAD) - 1
        range_header = self.headers.get('Range')
        if range_header:
            start, end = map(int, range_header[6:].split('-'))
        body = PAYLOAD[start:end + 1]
        self.send_response(206 if range_header else 200)
        self.send_header('Content-Type', 'text/html' if self.path == '/login.mp4' else 'video/mp4')
        self.send_header('Content-Length', str(len(body)))
        if range_header:
            self.send_header('Content-Range', f'bytes {start}-{end}/{len(PAYLOAD)}')
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            pass


class BrowserIntegration(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.servers = []
        for _ in range(2):
            server = ThreadingHTTPServer(('127.0.0.1', 0), VideoHandler)
            server.seen = []
            threading.Thread(target=server.serve_forever, daemon=True).start()
            cls.servers.append(server)
        cls.base = f'http://127.0.0.1:{cls.servers[0].server_port}'
        cls.other = f'http://127.0.0.1:{cls.servers[1].server_port}'
        cls.servers[0].target = cls.other

    @classmethod
    def tearDownClass(cls):
        for server in cls.servers:
            server.shutdown()
            server.server_close()

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='browser-video-test-')
        self.root = Path(self.temp.name)
        self.config = SimpleNamespace(temp_dir=str(self.root / 'temp'), download_dir=str(self.root / 'out'),
            thread_count=4, timeout=2, retry_times=1, user_agent='VideoTest', speed_limit=0,
            proxies={'http':'', 'https':''})
        self.db = DatabaseManager(str(self.root / 'test.db'))
        self.engine = DownloadEngine(self.db, self.config)
        self.manager = TaskManager(self.engine, self.db)
        self.show = Mock()
        self.bridge = BridgeServer(self.manager, self.show, path=self.root / 'endpoint')
        self.bridge.start()

    def tearDown(self):
        self.bridge.close()
        self.manager.shutdown()
        time.sleep(1.05)
        self.temp.cleanup()

    def message(self, path='/private.mp4'):
        return {'action':'download', 'request_id':str(uuid.uuid4()), 'url':self.base + path,
                'kind':'mp4', 'filename':'lesson.mp4',
                'headers':{'Cookie':'session=secret', 'Referer':'https://page.test/watch'}}

    def wait_complete(self, task_id):
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline:
            task = self.db.get_task(task_id)
            if task['status'] in ('completed', 'failed'):
                self.assertEqual(task['status'], 'completed', task.get('error_message'))
                self.assertEqual(Path(task['save_path']).read_bytes(), PAYLOAD)
                return
            time.sleep(.02)
        self.fail('Video task did not finish')

    def test_private_video_handoff_and_duplicate_receipt(self):
        message = self.message()
        result = call_desktop(message, self.bridge.path)
        self.assertTrue(result['ok'], result)
        self.assertEqual(result, call_desktop(message, self.bridge.path))
        self.assertEqual(len(self.db.get_all_tasks()), 1)
        self.wait_complete(result['task_id'])
        self.assertNotIn('session=secret', self.db.get_task(result['task_id'])['browser_context'])
        self.assertTrue(self.show.called)

    def test_same_filename_does_not_overwrite_existing_video(self):
        first = call_desktop(self.message(), self.bridge.path)
        second = call_desktop(self.message(), self.bridge.path)
        self.assertNotEqual(first['filename'], second['filename'])
        self.wait_complete(first['task_id'])
        self.wait_complete(second['task_id'])

    def test_restart_uses_user_encrypted_request_context(self):
        self.manager._scheduling_paused = True
        result = call_desktop(self.message(), self.bridge.path)
        self.manager.shutdown()
        self.engine = DownloadEngine(self.db, self.config)
        self.manager = TaskManager(self.engine, self.db)
        self.assertTrue(self.manager.start_task(result['task_id']))
        self.wait_complete(result['task_id'])

    def test_cross_origin_redirect_strips_credentials_and_referrer(self):
        url = self.base + '/redirect'
        context = normalize_context(url, {'headers':self.message()['headers']})
        with browser_get(url, context, stream=True, timeout=2, proxies=self.config.proxies) as response:
            self.assertEqual(response.content, PAYLOAD)
        headers = self.servers[1].seen[-1][1]
        self.assertNotIn('Cookie', headers)
        self.assertNotIn('Referer', headers)

    def test_login_html_is_not_saved_as_video(self):
        result = call_desktop(self.message('/login.mp4'), self.bridge.path)
        self.assertFalse(result['ok'])
        self.assertEqual(self.db.get_all_tasks(), [])

    def test_web_pages_and_wrong_tokens_cannot_submit(self):
        url = f'http://127.0.0.1:{self.bridge.server.server_port}/v1'
        result = requests.post(url, json={'action':'ping'}, timeout=2, proxies=self.config.proxies)
        self.assertEqual(result.status_code, 403)
        result = requests.post(url, json={'action':'ping'}, headers={
            'Authorization':'Bearer '+self.bridge.token, 'Origin':'https://evil.test'}, timeout=2, proxies=self.config.proxies)
        self.assertEqual(result.status_code, 403)


class BrowserValidation(unittest.TestCase):
    def test_endpoint_publish_handles_redirected_profile_storage(self):
        with tempfile.TemporaryDirectory() as directory:
            bridge = BridgeServer(Mock(), Mock(), Path(directory) / 'endpoint')
            try:
                with patch('downloader.browser.bridge.os.replace', side_effect=OSError(errno.EXDEV, 'cross device')):
                    bridge.start()
                self.assertTrue(call_desktop({'action':'ping'}, bridge.path)['ok'])
            finally:
                bridge.close()

    def test_dpapi_roundtrip(self):
        payload = {'headers':{'Cookie':'session=secret'}}
        encoded = protect(payload)
        self.assertNotIn('secret', encoded)
        self.assertEqual(unprotect(encoded), payload)

    def test_native_framing_and_truncation(self):
        stream = io.BytesIO()
        value = {'action':'ping', 'title':'视频'}
        write_message(stream, value)
        stream.seek(0)
        self.assertEqual(read_message(stream), value)
        self.assertIsNone(read_message(stream))
        with self.assertRaises(EOFError):
            read_message(io.BytesIO(b'\x10\x00'))
        with self.assertRaises(ValueError):
            read_message(io.BytesIO(struct.pack('<I', 2**30)))

    def test_bad_urls_and_header_injection(self):
        for url in ('file:///a.mp4', 'https://user:secret@example.com/a.mp4', 'javascript:alert(1)'):
            with self.assertRaises(ValueError):
                http_url(url)
        with self.assertRaises(ValueError):
            normalize_context('https://example.com/a.mp4', {'headers':{'Cookie':'x\r\nHost: evil'}})
        context = normalize_context('https://example.com/a.mp4', {'headers':{'Range':'bytes=0-', 'Host':'evil'}})
        self.assertEqual(context['headers'], {})

    def test_filename_and_unsupported_media(self):
        result = video_filename({'url':'https://example.com/a.mp4','kind':'mp4','filename':'../../evil.exe'})
        self.assertNotIn('/', result)
        self.assertTrue(result.endswith('.mp4'))
        self.assertEqual(video_filename({'url':'https://x.test/NUL.mp4','kind':'mp4'}), '_NUL.mp4')
        with self.assertRaises(ValueError):
            video_filename({'url':'https://x.test/a.m3u8','kind':'hls'})

    def test_stable_extension_id(self):
        manifest = json.loads((Path(__file__).resolve().parents[1] / 'chrome-extension/manifest.json').read_text(encoding='utf-8'))
        self.assertEqual(extension_id(manifest), 'kpknpdpemebgalcfiolkcpaafddelcjj')


if __name__ == '__main__':
    unittest.main()
