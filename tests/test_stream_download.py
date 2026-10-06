"""Stream task lifecycle, credential boundaries and non-lossy download options."""
import io
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from downloader.browser.bridge import BridgeServer
from downloader.browser.security import normalize_stream_context, protect, unprotect
from downloader.core.download_engine import DownloadEngine
from downloader.core.task_manager import TaskManager
from downloader.core.youtube_downloader import YoutubeDownloader, failure_message, video_task_url
from downloader.database.db_manager import DatabaseManager

URL = 'https://media.example.test/video/master.m3u8?signature=keep-exact'
COOKIE = dict(name='session', value='private-token', domain='media.example.test', path='/video/',
              secure=True, hostOnly=True)


class StreamTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.context = {'headers': {'Referer': 'https://page.example.test/watch?secret=hidden',
                                    'User-Agent': 'test', 'Cookie': 'unsafe=drop'}, 'cookies': [COOKIE]}
        self.config = SimpleNamespace(temp_dir=str(self.root / 'parts'), timeout=3, retry_times=1,
                                      speed_limit=0, proxies={}, download_dir=str(self.root / 'out'))

    def test_scoped_cookies_and_safe_referrer(self):
        context = normalize_stream_context(URL, self.context)
        self.assertEqual(context['headers'], {'Referer': 'https://page.example.test/', 'User-Agent': 'test'})
        self.assertEqual(context['cookies'], [COOKIE])
        for cookie in ({**COOKIE, 'domain': 'unrelated.test'}, {**COOKIE, 'value': 'bad\nheader'},
                       {**COOKIE, 'path': 'relative'}, {**COOKIE, 'domain': 'example.test'}):
            with self.subTest(cookie=cookie), self.assertRaises(ValueError):
                normalize_stream_context(URL, {'cookies': [cookie]})
        self.assertEqual(video_task_url(URL, 'hls'), URL)
        for url in ('file:///a.m3u8', 'https://user:secret@example.test/a.mpd'):
            with self.assertRaises(ValueError):
                video_task_url(url, 'dash')

    def test_stream_bridge_queue_encryption_pause_restart_resume(self):
        for kind in ('hls', 'dash'):
            db = DatabaseManager(str(self.root / (kind + '.db')))
            manager = TaskManager(DownloadEngine(db, self.config), db)
            self.addCleanup(manager.shutdown)
            bridge = BridgeServer(manager, Mock(), self.root / (kind + '.endpoint'))
            self.addCleanup(bridge.close)
            worker = Mock()
            with patch('downloader.core.download_engine.require_tools'), \
                    patch('downloader.core.download_engine.YoutubeDownloader', return_value=worker), \
                    patch('downloader.browser.bridge.require_tools'):
                self.assertIn(kind, bridge.dispatch({'action': 'ping'})['capabilities'])
                message = dict(action='download', kind=kind, url=URL, height=1080,
                               request_id=kind + '-1234567890123456', **self.context)
                result = bridge.dispatch(message)
                self.assertTrue(result['ok'])
                self.assertEqual(result, bridge.dispatch(message))
                task_id = result['task_id']
                task = db.get_task(task_id)
                self.assertEqual(task['download_type'], kind)
                self.assertEqual(task['video_height'], 1080)
                self.assertEqual(task['url'], URL)
                self.assertNotIn('private-token', task['browser_context'])
                self.assertEqual(unprotect(task['browser_context'])['cookies'], [COOKIE])
                self.assertTrue(manager.pause_task(task_id))
                worker.cancel.assert_called_once()
                db.update_task_progress(task_id, 50, 0)
                manager.shutdown()
                restarted = TaskManager(DownloadEngine(db, self.config), db)
                self.addCleanup(restarted.shutdown)
                self.assertEqual(db.get_task(task_id)['downloaded_size'], 50)
                self.assertTrue(restarted.resume_task(task_id))
                self.assertTrue(restarted.cancel_task(task_id))

    def test_command_credentials_cleanup_and_silent_video(self):
        for kind in ('hls', 'dash'):
            task = dict(task_id=kind, url=URL, download_type=kind, video_height=720,
                        browser_context=protect(self.context), save_path=str(self.root / (kind + '.mp4')))
            completed, failed = Mock(), Mock()
            with patch('downloader.core.youtube_downloader.require_tools', return_value=self.root):
                worker = YoutubeDownloader(task, self.config, Mock(), completed, failed, lambda: True)
            command = worker.command()
            self.assertEqual(command[-1], URL)
            self.assertIn('--abort-on-unavailable-fragments', command)
            self.assertIn('--remux-video', command)
            self.assertIn('!is_live', command)
            self.assertNotIn('--allow-unplayable-formats', command)
            self.assertNotIn('private-token', ' '.join(command))
            worker.folder.mkdir(parents=True)
            (worker.folder / 'video.mp4').write_bytes(b'video')
            jars = []

            def launch(args, **kwargs):
                jar = Path(args[args.index('--cookies') + 1])
                jars.append(jar)
                self.assertIn('media.example.test\tFALSE\t/video/\tTRUE', jar.read_text())
                self.assertIn('private-token', jar.read_text())
                return Mock(stdout=io.StringIO(''), wait=Mock(return_value=0))

            probe = SimpleNamespace(returncode=0, stdout=json.dumps(
                {'streams': [{'codec_type': 'video'}], 'format': {'duration': '5'}}))
            with patch('downloader.core.youtube_downloader.subprocess.Popen', side_effect=launch), \
                    patch('downloader.core.youtube_downloader.subprocess.run', return_value=probe):
                worker.run()
            completed.assert_called_once_with(5)
            failed.assert_not_called()
            self.assertTrue(all(not jar.exists() for jar in jars))

    def test_drm_error_does_not_echo_sensitive_urls(self):
        message = failure_message(['ERROR DRM https://example.test/?token=secret'])
        self.assertIn('DRM', message)
        self.assertNotIn('secret', message)


if __name__ == '__main__':
    unittest.main()
