"""Douyin page selection, scoped browser credentials and complete audio/video output."""
import io
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from downloader.browser.bridge import BridgeServer, video_filename
from downloader.browser.native_host import forward
from downloader.browser.security import protect, unprotect
from downloader.core.download_engine import DownloadEngine
from downloader.core.task_manager import TaskManager
from downloader.core.youtube_downloader import YoutubeDownloader, VIDEO_KINDS, douyin_url, failure_message, video_request_context
from downloader.database.db_manager import DatabaseManager

URL = 'https://www.douyin.com/video/7686532267488840975'
MODULE = 'downloader.core.youtube_downloader'
COOKIE = dict(name='session', value='douyin-private-token', domain='.douyin.com', path='/',
              secure=True, hostOnly=False)
FORMAT = dict(url='https://v26-web.douyinvod.com/signed/movie/?token=format-private-token',
              width=640, height=360, vcodec='h264')
VIDEO = dict(id='7686532267488840975', duration=5, formats=[FORMAT])


class DouyinTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.context = {'headers': {'User-Agent': 'test-browser', 'Referer': URL + '?tracking=drop',
                                    'Cookie': 'unscoped=drop'}, 'cookies': [COOKIE], 'video': VIDEO}
        self.config = SimpleNamespace(temp_dir=str(self.root / 'parts'), timeout=3, retry_times=1,
                                      speed_limit=0, proxies={}, download_dir=str(self.root / 'out'))

    def test_single_video_validation(self):
        for value in (URL, URL + '/?recommend=1#comment', URL.replace('www.', ''),
                      'https://www.douyin.com/?recommend=1&modal_id=7686532267488840975'):
            self.assertEqual(douyin_url(value), URL)
        for value in ('https://www.douyin.com/?recommend=1', 'https://www.douyin.com/?modal_id=',
                      'https://www.douyin.com/?modal_id=123&modal_id=456',
                      URL.replace('7686532267488840975', '0'), URL.replace('7686532267488840975', '01'),
                      URL.replace('7686532267488840975', '1' * 26), URL.replace('/video/', '/user/'),
                      URL.replace('.com', '.com.evil.test'), URL.replace('.com', '.com:444'),
                      URL.replace('www.', 'user:secret@www.'), URL.replace('https:', 'file:'), URL + '\n',
                      'https://live.douyin.com/123', 'https://v.douyin.com/short/', None):
            with self.subTest(url=value), self.assertRaises(ValueError):
                douyin_url(value)
        self.assertEqual(video_filename({'kind': 'douyin', 'url': URL}),
                         'Douyin-7686532267488840975.mp4')

    def test_queue_keeps_scoped_credentials_across_pause_and_restart(self):
        db = DatabaseManager(str(self.root / 'tasks.db'))
        manager = TaskManager(DownloadEngine(db, self.config), db)
        self.addCleanup(manager.shutdown)
        bridge = BridgeServer(manager, Mock(), self.root / 'endpoint')
        self.addCleanup(bridge.close)
        worker = Mock()
        with patch('downloader.core.download_engine.require_tools'), \
                patch('downloader.core.download_engine.YoutubeDownloader', return_value=worker):
            message = dict(action='download', kind='douyin', url=URL + '?recommend=1', height=480,
                           request_id='douyin-request-123456789', **self.context)
            result = bridge.dispatch(message)
            self.assertTrue(result['ok'])
            self.assertEqual(bridge.dispatch(message), result)
            task_id = result['task_id']
            task = db.get_task(task_id)
            self.assertEqual(task['download_type'], 'douyin')
            self.assertEqual(task['video_height'], 480)
            self.assertEqual(task['url'], URL)
            self.assertNotIn(COOKIE['value'], task['browser_context'])
            self.assertNotIn('format-private-token', task['browser_context'])
            context = unprotect(task['browser_context'])
            self.assertEqual(context['cookies'], [COOKIE])
            self.assertEqual(context['video']['formats'][0]['url'], FORMAT['url'])
            self.assertEqual(context['headers'], {'User-Agent': 'test-browser', 'Referer': 'https://www.douyin.com/'})
            with self.assertRaises(ValueError):
                bridge.dispatch({**message, 'request_id': 'invalid-request-123456789',
                                 'cookies': [{**COOKIE, 'domain': '.unrelated.test'}]})
            self.assertEqual(len(db.get_all_tasks()), 1)
            self.assertTrue(manager.pause_task(task_id))
            worker.cancel.assert_called_once()
            db.update_task_progress(task_id, 50, 0)
            manager.shutdown()
            restarted = TaskManager(DownloadEngine(db, self.config), db)
            self.addCleanup(restarted.shutdown)
            self.assertEqual(db.get_task(task_id)['downloaded_size'], 50)
            self.assertEqual(unprotect(db.get_task(task_id)['browser_context']), context)
            self.assertTrue(restarted.resume_task(task_id))
            self.assertTrue(restarted.cancel_task(task_id))

    def test_cold_and_running_bridges_advertise_the_same_capabilities(self):
        with patch('downloader.browser.native_host.call_desktop', side_effect=OSError), \
                patch(MODULE + '.require_tools'):
            cold = forward({'action': 'ping'})
        bridge = BridgeServer(Mock(), Mock(), self.root / 'endpoint')
        self.addCleanup(bridge.close)
        with patch('downloader.browser.bridge.require_tools'):
            running = bridge.dispatch({'action': 'ping'})
        self.assertTrue(cold['installed'])
        self.assertEqual(cold['capabilities'], list(VIDEO_KINDS))
        self.assertEqual(cold['capabilities'], running['capabilities'])

    def run_worker(self, name, streams, duration='5', launch_error=False):
        completed, failed = Mock(), Mock()
        task = dict(task_id=name, url=URL, download_type='douyin', video_height=720,
                    browser_context=protect(self.context), save_path=str(self.root / (name + '.mp4')))
        with patch(MODULE + '.require_tools', return_value=self.root):
            worker = YoutubeDownloader(task, self.config, Mock(), completed, failed, lambda: True)
        worker.folder.mkdir(parents=True)
        (worker.folder / 'video.mp4').write_bytes(b'video')
        jars = []
        infos = []

        def launch(command, **kwargs):
            self.assertEqual(command[-2], '--load-info-json')
            info = Path(command[-1])
            infos.append(info)
            metadata = json.loads(info.read_text())
            self.assertEqual(metadata['id'], VIDEO['id'])
            self.assertEqual(metadata['formats'][0]['url'], FORMAT['url'])
            self.assertNotIn(URL, command)
            self.assertNotIn('format-private-token', ' '.join(command))
            self.assertIn('--no-playlist', command)
            self.assertNotIn('--force-generic-extractor', command)
            self.assertIn('height<=720', command[command.index('--format') + 1])
            self.assertIn('User-Agent:test-browser', command)
            self.assertIn('Referer:https://www.douyin.com/', command)
            self.assertNotIn(COOKIE['value'], ' '.join(command))
            jar = Path(command[command.index('--cookies') + 1])
            jars.append(jar)
            self.assertIn('.douyin.com\tTRUE\t/\tTRUE\t0\tsession\tdouyin-private-token', jar.read_text())
            self.assertNotIn('unscoped=drop', jar.read_text())
            if launch_error:
                raise OSError('launch failed')
            return Mock(stdout=io.StringIO(''), wait=Mock(return_value=0))

        probe = SimpleNamespace(returncode=0, stdout=json.dumps(
            {'streams': [{'codec_type': kind} for kind in streams], 'format': {'duration': duration}}))
        with patch(MODULE + '.subprocess.Popen', side_effect=launch), \
                patch(MODULE + '.subprocess.run', return_value=probe):
            worker.run()
        self.assertTrue(jars)
        self.assertTrue(all(not jar.exists() for jar in jars))
        self.assertTrue(infos)
        self.assertTrue(all(not info.exists() for info in infos))
        return completed, failed, Path(task['save_path'])

    def test_complete_audio_video_and_temporary_cookie_cleanup(self):
        completed, failed, output = self.run_worker('complete', ('video', 'audio'))
        completed.assert_called_once_with(5)
        failed.assert_not_called()
        self.assertTrue(output.is_file())

    def test_missing_tracks_or_duration_never_publish_success(self):
        for name, streams, duration in (('silent', ('video',), '5'), ('audio', ('audio',), '5'),
                                        ('zero', ('video', 'audio'), '0'), ('fragment', ('video', 'audio'), '1'),
                                        ('nan', ('video', 'audio'), 'nan'), ('infinite', ('video', 'audio'), 'inf')):
            with self.subTest(case=name):
                completed, failed, output = self.run_worker(name, streams, duration)
                completed.assert_not_called()
                failed.assert_called_once()
                self.assertFalse(output.exists())

    def test_launch_failure_still_removes_cookie_file(self):
        completed, failed, output = self.run_worker('launch-error', ('video', 'audio'), launch_error=True)
        completed.assert_not_called()
        failed.assert_called_once()
        self.assertFalse(output.exists())

    def test_expired_access_context_has_actionable_sanitized_error(self):
        for detail in ('Fresh cookies (not necessarily logged in) are needed', 'login required'):
            message = failure_message([detail + ' https://example.test/?token=secret'], 'douyin')
            self.assertIn('刷新视频页面', message)
            self.assertNotIn('secret', message)

    def test_browser_formats_are_scoped_to_this_video_and_cannot_inject_paths(self):
        context = video_request_context(URL, 'douyin', {**self.context,
            'video': {**VIDEO, '_filename': 'C:/unsafe.exe', 'requested_downloads': [{'url': 'file:///unsafe'}]}})
        self.assertNotIn('_filename', context['video'])
        self.assertNotIn('requested_downloads', context['video'])
        self.assertEqual(context['video']['formats'][0]['url'], FORMAT['url'])
        invalid = [None, {**VIDEO, 'id': '123'}, {**VIDEO, 'duration': 0}, {**VIDEO, 'duration': float('nan')},
                   {**VIDEO, 'duration': True}, {**VIDEO, 'formats': []}, {**VIDEO, 'formats': [FORMAT] * 65}]
        for url in ('file:///movie.mp4', 'https://user:secret@v26-web.douyinvod.com/movie',
                    'https://douyinvod.com.evil.test/movie.mp4', 'https://v26-web.douyinvod.com:444/movie',
                    'https://v26-web.douyinvod.com/media-video-avc1/',
                    'https://v26-web.douyinvod.com/media-audio-und-mp4a/', FORMAT['url'] + '&range=0-99'):
            invalid.append({**VIDEO, 'formats': [{**FORMAT, 'url': url}]})
        invalid.append({**VIDEO, 'formats': [{**FORMAT, 'width': True}]})
        for video in invalid:
            with self.subTest(video=video), self.assertRaises(ValueError):
                video_request_context(URL, 'douyin', {**self.context, 'video': video})


if __name__ == '__main__':
    unittest.main()
