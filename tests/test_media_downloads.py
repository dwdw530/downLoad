"""Media output safety, subtitle availability and persisted output choices."""
import io
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from downloader.browser.bridge import BridgeServer
from downloader.core.download_engine import DownloadEngine
from downloader.core.media_options import (
    describe_media_result, normalize_media_options)
from downloader.core.task_manager import TaskManager
from downloader.core.youtube_downloader import YoutubeDownloader
from downloader.database.db_manager import DatabaseManager
from downloader.utils.config import ConfigManager

MODULE = 'downloader.core.youtube_downloader'
URL = 'https://www.youtube.com/watch?v=abcdefghijk'


class MediaDownloadTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        with patch('downloader.utils.config.get_state_root',
                   return_value=str(self.root)):
            self.config = ConfigManager()
        self.config.temp_dir = str(self.root / 'parts')
        self.config.download_dir = str(self.root / 'out')
        self.config.set('timeout', 3)
        self.config.set('retry_times', 1)
        self.db = DatabaseManager(str(self.root / 'tasks.db'))

    def worker(self, options, suffix, streams, subtitles=()):
        options = normalize_media_options(options)
        task: dict = dict(
            task_id='media', url=URL, video_height=2160,
            save_path=str(self.root / 'out' / ('movie' + suffix)),
            media_options=json.dumps(options))
        self.db.create_task_with_chunks(
            'media', URL, 'movie' + suffix, task['save_path'],
            download_type='youtube', video_height=2160, media_options=options)
        completed, failed = Mock(), Mock()
        with patch(MODULE + '.require_tools', return_value=self.root):
            worker = YoutubeDownloader(
                task, self.config, Mock(), completed, failed, lambda: True,
                lambda ext, subs: self.db.reserve_task_path(
                    'media', ext, subs))
        worker.folder.mkdir(parents=True)
        output = worker.folder / ('video' + suffix)
        output.write_bytes(b'complete media')
        for language in subtitles:
            (worker.folder / f'video.{language}.srt').write_text(
                '1\n00:00:00,000 --> 00:00:01,000\nSubtitle\n',
                encoding='utf-8')
        process = Mock(stdout=io.StringIO(
            '__DAW_OUTPUT__' + json.dumps(str(output)) + '\n'),
            wait=Mock(return_value=0))
        probe = SimpleNamespace(returncode=0, stdout=json.dumps({
            'streams': streams, 'format': {'duration': '10'}}))
        with patch(MODULE + '.subprocess.Popen', return_value=process), \
                patch(MODULE + '.subprocess.run', return_value=probe):
            worker.run()
        stored = self.db.get_task('media')
        assert stored is not None
        return completed, failed, stored

    def test_native_audio_has_no_video_and_keeps_its_extension(self):
        completed, failed, task = self.worker(
            {'mode': 'audio'}, '.opus',
            [{'codec_type': 'audio', 'codec_name': 'opus'}])
        completed.assert_called_once()
        failed.assert_not_called()
        self.assertEqual(Path(task['save_path']).suffix, '.opus')
        self.assertEqual(Path(task['save_path']).read_bytes(),
                         b'complete media')
        self.assertEqual(completed.call_args.args[1]['audio_codec'], 'opus')

    def test_audio_request_never_accepts_muxed_video(self):
        completed, failed, task = self.worker(
            {'mode': 'audio'}, '.m4a',
            [{'codec_type': 'video'}, {'codec_type': 'audio'}])
        completed.assert_not_called()
        failed.assert_called_once()
        self.assertFalse(Path(task['save_path']).exists())

    def test_subtitles_are_saved_beside_the_renamed_media(self):
        (self.root / 'out').mkdir()
        original = self.root / 'out' / 'movie.en.srt'
        original.write_text('existing subtitle', encoding='utf-8')
        completed, failed, task = self.worker(
            {'subtitles': 'en'}, '.mp4',
            [{'codec_type': 'video'}, {'codec_type': 'audio'}], ('en',))
        failed.assert_not_called()
        result = completed.call_args.args[1]
        self.assertFalse(result['subtitle_missing'])
        self.assertEqual(original.read_text(), 'existing subtitle')
        destination = Path(task['save_path'])
        self.assertEqual(destination.name, 'movie (1).mp4')
        self.assertEqual(Path(result['subtitles'][0]).stem,
                         destination.stem + '.en')
        self.assertIn('Subtitle', Path(result['subtitles'][0]).read_text())

    def test_missing_requested_language_is_distinct_from_video_failure(self):
        completed, failed, task = self.worker(
            {'subtitles': 'zh,en'}, '.mp4',
            [{'codec_type': 'video'}, {'codec_type': 'audio'}], ('en',))
        failed.assert_not_called()
        completed.assert_called_once()
        self.assertTrue(completed.call_args.args[1]['subtitle_missing'])
        self.assertTrue(Path(task['save_path']).is_file())

    def test_source_video_reports_real_resolution_and_codec(self):
        completed, failed, task = self.worker(
            {'profile': 'source'}, '.mkv',
            [{'codec_type': 'video', 'codec_name': 'vp9',
              'width': 3840, 'height': 2160},
             {'codec_type': 'audio', 'codec_name': 'opus'}])
        failed.assert_not_called()
        result = completed.call_args.args[1]
        self.assertEqual((result['width'], result['height']), (3840, 2160))
        self.assertEqual(result['video_codec'], 'vp9')
        summary = describe_media_result(json.dumps(result))
        self.assertIn('3840×2160', summary)
        self.assertIn('vp9', summary)
        self.assertTrue(Path(task['save_path']).is_file())

    def test_bridge_choices_survive_reopening_the_database(self):
        manager = TaskManager(DownloadEngine(self.db, self.config), self.db)
        self.addCleanup(manager.shutdown)
        bridge = BridgeServer(manager, Mock(), self.root / 'endpoint')
        self.addCleanup(bridge.close)
        with patch('downloader.core.download_engine.require_tools'), \
                patch('downloader.core.download_engine.YoutubeDownloader'):
            result = bridge.dispatch({
                'action': 'download', 'kind': 'youtube', 'url': URL,
                'request_id': 'audio-choice-request-12345678', 'height': 2160,
                'media_options': {'mode': 'audio', 'audio_format': 'mp3',
                                  'subtitles': 'en'}})
            self.assertTrue(result['ok'])
            manager.pause_task(result['task_id'])
        reopened = DatabaseManager(self.db.db_path)
        task = reopened.get_task(result['task_id'])
        assert task is not None
        options = json.loads(task['media_options'])
        self.assertEqual(options['mode'], 'audio')
        self.assertEqual(options['audio_format'], 'mp3')
        self.assertEqual(options['subtitles'], 'en')
        self.assertEqual(task['video_height'], 2160)
        self.assertTrue(task['filename'].endswith('.mp3'))
        self.assertEqual(task['status'], 'paused')
