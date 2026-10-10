"""Offline coverage of the YouTube worker, release files and task lifecycle."""
import io
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from downloader.core.youtube_downloader import (YoutubeDownloader, require_tools, youtube_url, x_url, bilibili_url,
                                                video_page_url, sanitize_output, failure_message)
from downloader.browser.bridge import BridgeServer, video_filename
from downloader.core.download_engine import DownloadEngine
from downloader.core.task_manager import TaskManager
from downloader.database.db_manager import DatabaseManager
from scripts.build_exe import VIDEO_FILES, copy_video_tools, video_tool_files


URL = 'https://www.youtube.com/watch?v=ayl6TcSsre8'
MODULE = 'downloader.core.youtube_downloader'


class YoutubeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.tools = self.root / 'vendor/video'
        self.tools.mkdir(parents=True)
        for name in VIDEO_FILES:
            (self.tools / name).write_bytes(b'test fixture')
        self.config = SimpleNamespace(temp_dir=str(self.root / 'parts'), timeout=3, retry_times=1,
                                      speed_limit=1234, proxies={'https': 'http://127.0.0.1:7890'})
        self.task = {'task_id': 'test-task', 'url': URL + '&t=1s', 'video_height': 720,
                     'save_path': str(self.root / 'downloads/movie.mp4')}
        self.progress, self.completed, self.failed = Mock(), Mock(), Mock()
        self.worker = YoutubeDownloader(self.task, self.config, self.progress, self.completed,
                                        self.failed, lambda: True, self.tools)

    def test_url_validation(self):
        for value in (URL + '&t=1s&list=x', 'https://youtu.be/ayl6TcSsre8',
                      'https://m.youtube.com/shorts/ayl6TcSsre8'):
            self.assertEqual(youtube_url(value), URL)
        for value in ('file:///movie', 'https://youtube.com.evil.test/watch?v=ayl6TcSsre8',
                      'https://user:secret@youtube.com/watch?v=ayl6TcSsre8',
                      'https://youtube.com/playlist?list=x', URL.replace('ayl6TcSsre8', 'bad')):
            with self.assertRaises(ValueError):
                youtube_url(value)

    def test_x_url_validation_and_video_selection(self):
        expected = 'https://x.com/kyliaspeijcken/status/2106950444442595371/video/1'
        for value in (expected, expected.replace('/video/1', ''),
                      expected.replace('x.com', 'twitter.com') + '?s=20'):
            self.assertEqual(x_url(value), expected)
        self.assertTrue(x_url(expected.replace('/video/1', '/video/2')).endswith('/video/2'))
        for value in ('https://x.com/home', 'https://x.com/u/status/no',
                      expected.replace('x.com', 'x.com.evil.test'), expected.replace('https:', 'file:'),
                      expected.replace('x.com', 'user:secret@x.com'), expected.replace('/video/1', '/video/0'),
                      expected.replace('/video/1', '/photo/1'), expected.replace('x.com', 'x.com:444')):
            with self.assertRaises(ValueError):
                x_url(value)
        with self.assertRaises(ValueError):
            video_page_url(expected, 'hls')
        self.assertEqual(video_filename({'kind': 'x', 'url': expected}), 'X-2106950444442595371-video-1.mp4')

    def test_x_command_and_valid_silent_video(self):
        self.task.update(download_type='x', url='https://x.com/u/status/123/video/1')
        self.worker = YoutubeDownloader(self.task, self.config, self.progress, self.completed,
                                        self.failed, lambda: True, self.tools)
        command = self.worker.command()
        self.assertEqual(command[-1], self.task['url'])
        self.assertEqual(self.worker.folder.parent.name, 'x')
        self.assertFalse(any('cookie' in arg.lower() for arg in command))
        self.run_worker(streams=('video',))
        self.completed.assert_called_once()
        self.failed.assert_not_called()

    def test_bilibili_url_validation_and_part_selection(self):
        base = 'https://www.bilibili.com/video/BV1VbYk6FE6c/'
        for value in (base, base + '?spm_id_from=333.1007&trackid=test#reply',
                      base.replace('www.', 'm.') + '?p=1'):
            self.assertEqual(bilibili_url(value), base + '?p=1')
        self.assertEqual(video_page_url(base + '?p=2&t=5', 'bilibili'), base + '?p=2')
        self.assertEqual(bilibili_url('http://bilibili.com/video/av123'),
                         'https://www.bilibili.com/video/av123/?p=1')
        for value in (base + '?p=0', base + '?p=-1', base + '?p=', base + '?p=1.5',
                      base + '?p=1&p=2', base + '?p=100000', base + '?p=01',
                      base.replace('bilibili.com', 'bilibili.com.evil.test'),
                      base.replace('www.', 'user:secret@www.'), base.replace('.com', '.com:444'),
                      base.replace('https:', 'file:'), base.replace('BV1VbYk6FE6c', 'bad'),
                      base.replace('/video/BV1VbYk6FE6c/', '/bangumi/play/ep123'),
                      'https://live.bilibili.com/123', 'https://b23.tv/abcdef', None):
            with self.subTest(url=value), self.assertRaises(ValueError):
                bilibili_url(value)
        self.assertEqual(video_filename({'kind': 'bilibili', 'url': base + '?p=2'}),
                         'Bilibili-BV1VbYk6FE6c-P2.mp4')

    def test_bilibili_command_single_part_and_merge_validation(self):
        self.task.update(download_type='bilibili', url='https://www.bilibili.com/video/BV1VbYk6FE6c/?p=2&t=9')
        self.worker = YoutubeDownloader(self.task, self.config, self.progress, self.completed,
                                        self.failed, lambda: True, self.tools)
        command = self.worker.command()
        self.assertEqual(command[-1], 'https://www.bilibili.com/video/BV1VbYk6FE6c/?p=2')
        self.assertIn('--no-playlist', command)
        self.assertIn('height<=720', command[command.index('--format') + 1])
        self.assertEqual(self.worker.folder.parent.name, 'bilibili')
        self.assertFalse(any('cookie' in arg.lower() for arg in command))
        self.run_worker(streams=('video',))
        self.completed.assert_not_called()
        self.failed.assert_called_once()
        self.failed.reset_mock()
        self.run_worker()
        self.completed.assert_called_once()
        self.failed.assert_not_called()

    def test_bilibili_bridge_queue_dedupe_pause_restart_and_resume(self):
        db = DatabaseManager(str(self.root / 'bilibili.db'))
        self.config.download_dir = str(self.root / 'out')
        engine = DownloadEngine(db, self.config)
        manager = TaskManager(engine, db)
        self.addCleanup(manager.shutdown)
        bridge = BridgeServer(manager, Mock(), self.root / 'endpoint')
        self.addCleanup(bridge.close)
        worker = Mock()
        with patch('downloader.core.download_engine.require_tools'), \
                patch('downloader.core.download_engine.YoutubeDownloader', return_value=worker), \
                patch('downloader.browser.bridge.require_tools'):
            self.assertIn('bilibili', bridge.dispatch({'action': 'ping'})['capabilities'])
            message = {'action': 'download', 'kind': 'bilibili',
                       'url': 'https://www.bilibili.com/video/BV1VbYk6FE6c/?p=2&trackid=ignore',
                       'request_id': 'bilibili-request-123456789', 'height': 480,
                       'headers': {'Cookie': 'never-store'}}
            result = bridge.dispatch(message)
            self.assertTrue(result['ok'])
            self.assertEqual(bridge.dispatch(message), result)
            task_id = result['task_id']
            task = db.get_task(task_id)
            self.assertEqual(task['download_type'], 'bilibili')
            self.assertEqual(task['video_height'], 480)
            self.assertTrue(task['url'].endswith('?p=2'))
            self.assertFalse(task['browser_context'])
            self.assertTrue(manager.pause_task(task_id))
            worker.cancel.assert_called_once()
            db.update_task_progress(task_id, 50, 0)
            manager.shutdown()
            restarted = TaskManager(DownloadEngine(db, self.config), db)
            self.addCleanup(restarted.shutdown)
            self.assertEqual(db.get_task(task_id)['downloaded_size'], 50)
            self.assertTrue(restarted.resume_task(task_id))
            self.assertTrue(restarted.cancel_task(task_id))
            self.assertEqual(db.get_task(task_id)['status'], 'cancelled')

    def test_x_bridge_queue_dedupe_pause_restart_and_resume(self):
        db = DatabaseManager(str(self.root / 'x.db'))
        self.config.download_dir = str(self.root / 'out')
        engine = DownloadEngine(db, self.config)
        manager = TaskManager(engine, db)
        self.addCleanup(manager.shutdown)
        bridge = BridgeServer(manager, Mock(), self.root / 'endpoint')
        self.addCleanup(bridge.close)
        worker = Mock()
        with patch('downloader.core.download_engine.require_tools'), \
                patch('downloader.core.download_engine.YoutubeDownloader', return_value=worker):
            message = {'action': 'download', 'kind': 'x', 'url': 'https://x.com/u/status/123/video/1',
                       'request_id': 'x-request-123456789', 'height': 480, 'headers': {'Cookie': 'never-store'}}
            result = bridge.dispatch(message)
            self.assertTrue(result['ok'])
            self.assertEqual(bridge.dispatch(message), result)
            task_id = result['task_id']
            task = db.get_task(task_id)
            self.assertEqual(task['download_type'], 'x')
            self.assertEqual(task['video_height'], 480)
            self.assertFalse(task['browser_context'])
            self.assertTrue(manager.pause_task(task_id))
            worker.cancel.assert_called_once()
            db.update_task_progress(task_id, 50, 0)
            manager.shutdown()
            restarted = TaskManager(DownloadEngine(db, self.config), db)
            self.addCleanup(restarted.shutdown)
            self.assertEqual(db.get_task(task_id)['downloaded_size'], 50)
            self.assertTrue(restarted.resume_task(task_id))
            self.assertTrue(restarted.cancel_task(task_id))
            self.assertEqual(db.get_task(task_id)['status'], 'cancelled')

    def test_command_uses_local_tools_quality_proxy_and_no_account_cookies(self):
        command = self.worker.command()
        self.assertEqual(command[-2:], ['--', URL])
        self.assertIn('height<=720', command[command.index('--format') + 1])
        self.assertEqual(command[command.index('--proxy') + 1], self.config.proxies['https'])
        self.assertEqual(command[command.index('--limit-rate') + 1], '1234')
        self.assertIn('node:' + str(self.tools / 'node.exe'), command)
        self.assertIn('--ignore-config', command)
        self.assertIn('!is_live', command)
        self.assertFalse(any('cookie' in arg.lower() for arg in command))
        for height in (480, 720, 1080):
            self.task['video_height'] = height
            command = self.worker.command()
            self.assertIn(f'height<={height}', command[command.index('--format') + 1])
        self.task['video_height'] = 2160
        with self.assertRaises(ValueError):
            self.worker.command()

    def test_progress_aggregates_tracks_and_does_not_finish_before_merge(self):
        for name, done, total in [('video', 90, 100), ('audio', 10, 10), ('video', 100, 100)]:
            self.worker.consume_progress('__LW_PROGRESS__' + json.dumps(
                {'file': name, 'downloaded': done, 'total': total, 'speed': 25}))
        self.assertEqual(self.progress.call_args.args, (110, 111, 25))
        self.completed.assert_not_called()
        self.worker.stop.set()
        self.worker.consume_progress('__LW_PROGRESS__{}')
        self.assertEqual(self.progress.call_count, 3)

    def run_worker(self, streams=('video', 'audio'), code=0, log=''):
        self.worker.folder.mkdir(parents=True, exist_ok=True)
        (self.worker.folder / 'video.mp4').write_bytes(b'merged video')
        process = Mock(stdout=io.StringIO(log))
        process.wait.return_value = code
        probe = SimpleNamespace(returncode=0, stdout=json.dumps({
            'streams': [{'codec_type': kind} for kind in streams], 'format': {'duration': '10'}}))
        with patch(MODULE + '.subprocess.Popen', return_value=process), \
                patch(MODULE + '.subprocess.run', return_value=probe):
            self.worker.run()

    def test_complete_requires_audio_video_and_valid_duration(self):
        self.run_worker()
        self.assertEqual(Path(self.task['save_path']).read_bytes(), b'merged video')
        self.completed.assert_called_once_with(12)
        self.failed.assert_not_called()

    def test_silent_video_is_not_reported_as_complete(self):
        self.run_worker(streams=('video',))
        self.completed.assert_not_called()
        self.failed.assert_called_once()
        self.assertFalse(Path(self.task['save_path']).exists())

    def test_engine_failure_does_not_publish_partial_video_or_raw_urls(self):
        self.run_worker(code=1, log='ERROR: HTTP Error 403: https://cdn.test/?secret=token')
        self.completed.assert_not_called()
        self.assertNotIn('secret', self.failed.call_args.args[0])
        self.assertIn('403', self.failed.call_args.args[0])
        self.assertFalse(Path(self.task['save_path']).exists())

    def test_rate_limit_failure_is_logged_with_sanitized_output(self):
        with self.assertLogs(MODULE, level='INFO') as captured:
            self.run_worker(code=1, log='ERROR: HTTP Error 429: Too Many Requests https://cdn.test/?secret=token')
        self.completed.assert_not_called()
        self.assertIn('限流', self.failed.call_args.args[0])
        logged = '\n'.join(captured.output)
        self.assertIn('限流', logged)
        self.assertIn('<url>', logged)
        self.assertNotIn('secret', logged)

    def test_bot_check_still_reports_login_requirement(self):
        self.run_worker(code=1, log="ERROR: Sign in to confirm you're not a bot")
        message = self.failed.call_args.args[0]
        self.assertIn('登录', message)
        self.assertNotIn('限流', message)

    def test_sanitize_output_rewrites_links_and_credentials(self):
        text = sanitize_output(['ERROR: https://cdn.test/?secret=token', '', 'Cookie: session=abc123'])
        self.assertNotIn('secret', text)
        self.assertNotIn('abc123', text)
        self.assertIn('<url>', text)
        self.assertIn('<redacted>', text)

    def test_rate_limit_message_takes_priority_over_login_wording(self):
        message = failure_message(['ERROR: HTTP Error 429: Too Many Requests',
                                   "ERROR: Sign in to confirm you're not a bot"], 'youtube')
        self.assertIn('限流', message)

    def test_existing_destination_is_not_overwritten(self):
        destination = Path(self.task['save_path'])
        destination.parent.mkdir()
        destination.write_bytes(b'original')
        self.run_worker()
        self.assertEqual(destination.read_bytes(), b'original')
        self.failed.assert_called_once()
        self.completed.assert_not_called()

    def test_cancel_before_start_never_launches_or_completes(self):
        self.worker.cancel()
        with patch(MODULE + '.subprocess.Popen') as launch:
            self.worker.run()
        launch.assert_not_called()
        self.completed.assert_not_called()
        self.failed.assert_not_called()

    def test_release_preflight_and_copy_preserve_user_data(self):
        dist = self.root / 'dist'
        (dist / 'data').mkdir(parents=True)
        original = dist / 'data/config.json'
        original.write_bytes(b'user configuration')
        copy_video_tools(video_tool_files(self.root), dist)
        self.assertEqual(original.read_bytes(), b'user configuration')
        for name in VIDEO_FILES:
            self.assertEqual((dist / 'video-tools' / name).read_bytes(), (self.tools / name).read_bytes())
        (self.tools / 'node.exe').write_bytes(b'')
        with self.assertRaises(RuntimeError):
            video_tool_files(self.root)
        with self.assertRaises(ValueError):
            require_tools(self.tools)

    def test_queue_pause_restart_and_resume_preserve_youtube_task(self):
        db = DatabaseManager(str(self.root / 'tasks.db'))
        engine = DownloadEngine(db, self.config)
        manager = TaskManager(engine, db)
        self.addCleanup(manager.shutdown)
        worker = Mock()
        with patch('downloader.core.download_engine.require_tools'), \
                patch('downloader.core.download_engine.YoutubeDownloader', return_value=worker):
            task_id = manager.add_youtube_task(URL, 'movie.mp4', str(self.root), 480)
            self.assertEqual(db.get_task(task_id)['download_type'], 'youtube')
            self.assertEqual(db.get_task(task_id)['video_height'], 480)
            self.assertTrue(manager.pause_task(task_id))
            worker.cancel.assert_called_once()
            self.assertNotIn(task_id, engine.active_downloaders)
            self.assertTrue(manager.resume_task(task_id))
            self.assertTrue(manager.cancel_task(task_id))
            self.assertEqual(db.get_task(task_id)['status'], 'cancelled')
        manager.shutdown()
        restarted = TaskManager(DownloadEngine(db, self.config), db)
        self.addCleanup(restarted.shutdown)
        self.assertEqual(db.get_task(task_id)['download_type'], 'youtube')


if __name__ == '__main__':
    unittest.main()
