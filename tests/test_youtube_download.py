"""Offline coverage of the YouTube worker, release files and task lifecycle."""
import io
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from downloader.core.youtube_downloader import YoutubeDownloader, require_tools, youtube_url
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
