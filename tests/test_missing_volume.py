"""缺失下载盘/临时盘的启动门禁与安全错误分类；只使用隔离配置和数据库。"""
from pathlib import Path
import string
import tempfile
import unittest
from unittest.mock import Mock, patch

from downloader.core.download_engine import DownloadEngine
from downloader.core.youtube_downloader import YoutubeDownloader
from downloader.database.db_manager import DatabaseManager
from downloader.ui.task_presentation import describe_task_problem
from downloader.utils.config import ConfigManager
from downloader.utils.file_utils import check_download_volumes


class MissingVolumeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='daw-missing-volume-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.config = ConfigManager(str(self.root / 'config.json'))
        self.config.download_dir = str(self.root / 'out')
        self.config.temp_dir = str(self.root / 'parts')
        self.db = DatabaseManager(str(self.root / 'downloads.db'))
        self.engine = DownloadEngine(self.db, self.config)
        self.addCleanup(self.engine.shutdown)
        # 选真实未挂载盘符，不拔用户设备，也不改系统盘映射。
        missing = next((Path(f'{letter}:/daw-volume-test')
                        for letter in reversed(string.ascii_uppercase)
                        if not Path(f'{letter}:/').exists()), None)
        if missing is None:
            raise unittest.SkipTest('没有可用于缺盘测试的未挂载盘符')
        self.missing = missing

    def assert_disk_problem(self, message, label):
        reason, advice = describe_task_problem('failed', message)
        self.assertIn(label, reason)
        self.assertIn('磁盘不可用', reason)
        self.assertNotIn('网络', reason)
        self.assertIn('盘符', advice)
        self.assertNotIn(str(self.missing), reason + advice)

    def test_file_add_rejects_missing_volume_before_network_without_saving_task(self):
        for setting, label in (('download_dir', '下载目录'), ('temp_dir', '临时目录')):
            with self.subTest(setting=setting):
                previous = getattr(self.config, setting)
                setattr(self.config, setting, str(self.missing))
                with patch('downloader.core.download_engine.requests.get') as request:
                    self.assertIsNone(self.engine.create_download_task('https://example.test/file.bin'))
                request.assert_not_called()
                self.assertEqual(self.engine.last_add_error, 'disk_unavailable')
                self.assert_disk_problem(self.engine.last_add_detail, label)
                self.assertEqual(self.db.get_all_tasks(), [])
                self.assertEqual(getattr(self.config, setting), str(self.missing))
                setattr(self.config, setting, previous)

    def test_video_add_rejects_missing_volume_without_saving_task(self):
        for setting, label in (('download_dir', '下载目录'), ('temp_dir', '临时目录')):
            with self.subTest(setting=setting):
                previous = getattr(self.config, setting)
                setattr(self.config, setting, str(self.missing))
                with patch('downloader.core.download_engine.require_tools'):
                    with self.assertRaises(ValueError) as caught:
                        self.engine.create_youtube_task(
                            'https://x.com/user/status/123/video/1', 'video.mp4', None, kind='x')
                self.assert_disk_problem(str(caught.exception), label)
                self.assertEqual(self.db.get_all_tasks(), [])
                setattr(self.config, setting, previous)

    def test_start_and_resume_check_stored_paths_without_resetting_checkpoints(self):
        for location in ('destination', 'configured_temp', 'stored_chunk'):
            for status in ('pending', 'failed', 'paused'):
                with self.subTest(location=location, status=status):
                    task_id = f'{location}-{status}'
                    partial = self.root / (task_id + '.part0')
                    partial.write_bytes(b'kept')
                    old_chunk = (self.missing / (task_id + '.part1') if location == 'stored_chunk'
                                 else self.root / (task_id + '.part1'))
                    destination = (self.missing if location == 'destination' else self.root) / (task_id + '.bin')
                    old_temp = self.config.temp_dir
                    if location == 'configured_temp':
                        self.config.temp_dir = str(self.missing)
                    self.assertTrue(self.db.create_task_with_chunks(
                        task_id, 'https://example.test/file.bin', destination.name, str(destination),
                        total_size=16, support_range=True, thread_count=2,
                        chunks=[(0, 0, 7, str(partial)), (1, 8, 15, str(old_chunk))], status=status))
                    chunk = self.db.get_chunks(task_id)[0]
                    self.db.update_chunk_progress(chunk['chunk_id'], 4, 'paused')
                    self.db.update_task_progress(task_id, 4, 0)
                    before = self.db.get_chunks(task_id)
                    with patch('downloader.core.download_engine.requests.get') as request:
                        started = (self.engine.resume_download(task_id) if status == 'paused'
                                   else self.engine.start_download(task_id, resume=True))
                    self.assertFalse(started)
                    request.assert_not_called()
                    task = self.db.get_task(task_id)
                    assert task is not None
                    self.assertEqual(task['status'], 'failed')
                    self.assertEqual(task['save_path'], str(destination))
                    self.assertEqual(task['downloaded_size'], 4)
                    self.assertEqual(self.db.get_chunks(task_id), before)
                    self.assertEqual(partial.read_bytes(), b'kept')
                    self.assert_disk_problem(task['error_message'],
                                             '下载目录' if location == 'destination' else '临时目录')
                    self.config.temp_dir = old_temp

    def test_existing_video_checks_its_saved_destination_not_new_default(self):
        task_id = 'old-video'
        self.assertTrue(self.db.create_task_with_chunks(
            task_id, 'https://x.com/user/status/123/video/1', 'video.mp4',
            str(self.missing / 'video.mp4'), download_type='x', status='failed'))
        with patch('downloader.core.download_engine.YoutubeDownloader') as worker:
            self.assertFalse(self.engine.start_download(task_id, resume=True))
        worker.assert_not_called()
        task = self.db.get_task(task_id)
        assert task is not None
        self.assert_disk_problem(task['error_message'], '下载目录')

    def test_video_worker_rechecks_both_volumes_before_launching_tools(self):
        for location, label in (('destination', '下载目录'), ('temp', '临时目录')):
            with self.subTest(location=location):
                task = {'task_id': location, 'download_type': 'x',
                        'save_path': str((self.missing if location == 'destination' else self.root) / 'video.mp4')}
                failed, completed = Mock(), Mock()
                with patch('downloader.core.youtube_downloader.require_tools', return_value=self.root):
                    worker = YoutubeDownloader(task, self.config, Mock(), completed, failed, lambda: True)
                if location == 'temp':
                    worker.folder = self.missing / 'x' / location
                with patch('downloader.core.youtube_downloader.subprocess.Popen') as process:
                    worker.run()
                process.assert_not_called()
                completed.assert_not_called()
                failed.assert_called_once()
                self.assert_disk_problem(failed.call_args.args[0], label)

    def test_video_local_directory_error_does_not_claim_network_failure(self):
        obstacle = self.root / 'not-a-directory'
        obstacle.write_bytes(b'keep this file')
        self.config.temp_dir = str(obstacle)
        failed, completed = Mock(), Mock()
        task = {'task_id': 'local-error', 'download_type': 'x',
                'save_path': str(self.root / 'video.mp4')}
        with patch('downloader.core.youtube_downloader.require_tools', return_value=self.root):
            worker = YoutubeDownloader(task, self.config, Mock(), completed, failed, lambda: True)
        with patch('downloader.core.youtube_downloader.subprocess.Popen') as process:
            worker.run()
        process.assert_not_called()
        completed.assert_not_called()
        failed.assert_called_once()
        reason, _ = describe_task_problem('failed', failed.call_args.args[0])
        self.assertNotIn('网络', reason)
        self.assertNotIn('空间不足', reason)
        self.assertEqual(obstacle.read_bytes(), b'keep this file')

    def test_missing_subdirectory_is_not_confused_with_missing_volume(self):
        nested = self.root / 'not-created' / 'downloads'
        self.assertEqual(check_download_volumes([(str(nested), '下载目录')]), (True, None))
        self.assertFalse(nested.exists(), '预检不应创建目录或写入探针')

    def test_checking_network_in_advice_does_not_prove_network_failure(self):
        unknown = '视频下载失败，请检查网络、磁盘或下载组件'
        known = '视频下载网络失败，请检查代理或稍后重试'
        reason, _ = describe_task_problem('failed', unknown)
        self.assertNotIn('网络', reason)
        self.assertNotIn('空间不足', reason)
        self.assertIn('网络', describe_task_problem('failed', known)[0])
        self.assertEqual(describe_task_problem('paused', unknown), ('', ''))


if __name__ == '__main__':
    unittest.main()
