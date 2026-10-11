"""Disk publication checks: disposable directories only; no user download data."""
from contextlib import contextmanager
import errno
import os
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from downloader.utils.file_utils import publish_download
from test_features import FeatureFixture
from test_download_regressions import PAYLOAD


class PublishDownloadTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='daw-publish-test-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / 'source.bin'
        self.destination = self.root / 'out' / 'result.bin'
        self.source.write_bytes(PAYLOAD)
        self.rename = os.rename

    def cross_volume_rename(self, source, destination):
        if Path(source) == self.source:
            raise OSError(errno.EXDEV, 'cross-device link')
        return self.rename(source, destination)

    def assert_no_staging(self):
        self.assertEqual(list(self.destination.parent.glob('.daw-publish-*')), [])

    def test_same_volume_renames_without_opening_or_copying_data(self):
        with patch('downloader.utils.file_utils.open', side_effect=AssertionError('unexpected file copy')), \
                patch('downloader.utils.file_utils.tempfile.NamedTemporaryFile',
                      side_effect=AssertionError('unexpected staging file')), \
                patch('downloader.utils.file_utils.os.rename', wraps=self.rename) as rename:
            self.assertTrue(publish_download(str(self.source), str(self.destination)))
        self.assertEqual(rename.call_count, 1)
        self.assertFalse(self.source.exists())
        self.assertEqual(self.destination.read_bytes(), PAYLOAD)
        self.assert_no_staging()

    def test_same_volume_existing_destination_is_never_overwritten(self):
        self.destination.parent.mkdir()
        self.destination.write_bytes(b'user file')
        with patch('downloader.utils.file_utils.tempfile.NamedTemporaryFile') as staging:
            with self.assertRaises(FileExistsError):
                publish_download(str(self.source), str(self.destination))
        staging.assert_not_called()
        self.assertEqual(self.destination.read_bytes(), b'user file')
        self.assertEqual(self.source.read_bytes(), PAYLOAD)

    def test_permission_failure_does_not_fall_back_to_copy(self):
        with patch('downloader.utils.file_utils.os.rename', side_effect=PermissionError(errno.EACCES, 'denied')), \
                patch('downloader.utils.file_utils.tempfile.NamedTemporaryFile') as staging:
            with self.assertRaises(PermissionError):
                publish_download(str(self.source), str(self.destination))
        staging.assert_not_called()
        self.assertTrue(self.source.exists())
        self.assertFalse(self.destination.exists())

    def test_cross_volume_copies_once_and_publishes_only_after_copy(self):
        calls = []
        def rename(source, destination):
            calls.append((source, destination))
            if Path(source) != self.source:
                self.assertEqual(Path(source).parent, self.destination.parent)
                self.assertEqual(Path(source).read_bytes(), PAYLOAD)
                self.assertFalse(self.destination.exists())
            return self.cross_volume_rename(source, destination)
        with patch('downloader.utils.file_utils.os.rename', side_effect=rename):
            self.assertTrue(publish_download(str(self.source), str(self.destination)))
        self.assertEqual(len(calls), 2)
        self.assertEqual(self.destination.read_bytes(), PAYLOAD)
        self.assertFalse(self.source.exists())
        self.assert_no_staging()

    def test_cross_volume_conflict_during_final_rename_preserves_original(self):
        def rename(source, destination):
            if Path(source) != self.source:
                self.destination.write_bytes(b'concurrent user file')
            return self.cross_volume_rename(source, destination)
        with patch('downloader.utils.file_utils.os.rename', side_effect=rename):
            with self.assertRaises(FileExistsError):
                publish_download(str(self.source), str(self.destination))
        self.assertEqual(self.destination.read_bytes(), b'concurrent user file')
        self.assertEqual(self.source.read_bytes(), PAYLOAD)
        self.assert_no_staging()

    def test_cancel_before_publication_keeps_source(self):
        with self.assertRaises(InterruptedError):
            publish_download(str(self.source), str(self.destination), lambda: False)
        self.assertEqual(self.source.read_bytes(), PAYLOAD)
        self.assertFalse(self.destination.exists())

    def test_cancel_during_same_volume_rename_rolls_back_to_resume_source(self):
        active = [True]
        def rename(source, destination):
            self.rename(source, destination)
            active[0] = False
        with patch('downloader.utils.file_utils.os.rename', side_effect=rename):
            with self.assertRaises(InterruptedError):
                publish_download(str(self.source), str(self.destination), lambda: active[0])
        self.assertEqual(self.source.read_bytes(), PAYLOAD)
        self.assertFalse(self.destination.exists())

    def test_cancel_during_cross_volume_copy_removes_only_staging(self):
        checks = [0]
        def current():
            checks[0] += 1
            return checks[0] < 4  # 预检查、首次改名、第一块写入后停止
        with patch('downloader.utils.file_utils.os.rename', side_effect=self.cross_volume_rename):
            with self.assertRaises(InterruptedError):
                publish_download(str(self.source), str(self.destination), current)
        self.assertEqual(self.source.read_bytes(), PAYLOAD)
        self.assertFalse(self.destination.exists())
        self.assert_no_staging()

    def test_disk_full_during_copy_keeps_source_and_removes_partial_output(self):
        named_temporary_file = tempfile.NamedTemporaryFile
        @contextmanager
        def full_disk(**kwargs):
            with named_temporary_file(**kwargs) as stream:
                with patch.object(stream, 'write', side_effect=OSError(errno.ENOSPC, 'disk full')):
                    yield stream
        with patch('downloader.utils.file_utils.os.rename', side_effect=self.cross_volume_rename), \
                patch('downloader.utils.file_utils.tempfile.NamedTemporaryFile', side_effect=full_disk):
            with self.assertRaises(OSError) as error:
                publish_download(str(self.source), str(self.destination))
        self.assertEqual(error.exception.errno, errno.ENOSPC)
        self.assertEqual(self.source.read_bytes(), PAYLOAD)
        self.assertFalse(self.destination.exists())
        self.assert_no_staging()

    def test_source_cleanup_failure_does_not_reclassify_complete_output_as_failed(self):
        remove = os.remove
        def cleanup(path):
            if Path(path) == self.source:
                raise PermissionError(errno.EACCES, 'source temporarily locked')
            remove(path)
        with patch('downloader.utils.file_utils.os.rename', side_effect=self.cross_volume_rename), \
                patch('downloader.utils.file_utils.os.remove', side_effect=cleanup):
            self.assertTrue(publish_download(str(self.source), str(self.destination)))
        self.assertEqual(self.source.read_bytes(), PAYLOAD)
        self.assertEqual(self.destination.read_bytes(), PAYLOAD)
        self.assert_no_staging()

    def test_real_cross_volume_copy_when_a_second_volume_is_available(self):
        drive = Path(__file__).resolve().anchor
        if os.stat(drive).st_dev == os.stat(self.root).st_dev:
            self.skipTest('test directory and repository are on the same volume')
        try:
            target_temp = tempfile.TemporaryDirectory(prefix='daw-cross-volume-test-', dir=drive)
        except PermissionError:
            self.skipTest('second volume root is not writable')
        with target_temp as directory:
            target = Path(directory) / 'result.bin'
            self.assertTrue(publish_download(str(self.source), str(target)))
            self.assertEqual(target.read_bytes(), PAYLOAD)
            self.assertFalse(self.source.exists())
            self.assertEqual(list(Path(directory).glob('.daw-publish-*')), [])


class DiskUsageEngineTests(FeatureFixture):
    def test_merge_stages_beside_destination_and_never_copies_after_hash(self):
        tid = self.create(filename='multi.bin')
        target = Path(self.db.get_task(tid)['save_path'])
        observations = []
        def publish(source, destination, is_current):
            stage = Path(source)
            chunks = self.db.get_chunks(tid)
            self.assertEqual(stage.parent, target.parent)
            self.assertEqual(stage.read_bytes(), PAYLOAD)
            self.assertFalse(target.exists())
            self.assertEqual(sum(Path(c['temp_file']).stat().st_size for c in chunks), len(PAYLOAD))
            self.assertEqual(stage.stat().st_size + sum(Path(c['temp_file']).stat().st_size for c in chunks),
                             2 * len(PAYLOAD))
            observations.append(source)
            with patch('downloader.utils.file_utils.open', side_effect=AssertionError('extra full-file copy')):
                return publish_download(source, destination, is_current)
        with patch('downloader.core.download_engine.publish_download', side_effect=publish):
            self.assertTrue(self.manager.start_task(tid))
            self.wait_task(tid)
        self.assertEqual(len(observations), 1)
        self.assertEqual(target.read_bytes(), PAYLOAD)
        deadline = time.monotonic() + 2
        while tid in self.engine.thread_pools and time.monotonic() < deadline:
            time.sleep(0.01)
        self.assertNotIn(tid, self.engine.thread_pools)
        self.assertFalse(any(Path(c['temp_file']).exists() for c in self.db.get_chunks(tid)))
        self.assertEqual(list(target.parent.glob('.daw-merge-*')), [])
        self.assertFalse((Path(self.config.temp_dir) / f'{tid}.merged').exists())

    def test_single_thread_same_volume_has_only_one_file_at_publication(self):
        self.config.thread_count = 1
        tid = self.create(filename='single.bin')
        target = Path(self.db.get_task(tid)['save_path'])
        observations = []
        def publish(source, destination, is_current):
            self.assertEqual(Path(source).stat().st_size, len(PAYLOAD))
            self.assertFalse(target.exists())
            observations.append(source)
            with patch('downloader.utils.file_utils.open', side_effect=AssertionError('extra full-file copy')):
                return publish_download(source, destination, is_current)
        with patch('downloader.core.download_engine.publish_download', side_effect=publish):
            self.assertTrue(self.manager.start_task(tid))
            self.wait_task(tid)
        self.assertEqual(len(observations), 1)
        self.assertEqual(target.read_bytes(), PAYLOAD)
        self.assertFalse(Path(observations[0]).exists())

    def test_merge_failure_cleans_target_staging_and_retains_all_chunks(self):
        tid = self.create(filename='failed.bin')
        target = Path(self.db.get_task(tid)['save_path'])
        def fail_merge(chunks, staged, **_kwargs):
            Path(staged).write_bytes(b'partial merge')
            return False
        with patch('downloader.core.download_engine.merge_chunks', side_effect=fail_merge):
            self.assertTrue(self.manager.start_task(tid))
            self.wait_task(tid, 'failed')
        self.manager.shutdown()
        self.assertFalse(target.exists())
        self.assertEqual(list(target.parent.glob('.daw-merge-*')), [])
        self.assertTrue(all(Path(c['temp_file']).exists() for c in self.db.get_chunks(tid)))

    def test_pause_during_merge_cleans_staging_and_resume_reuses_chunks(self):
        tid = self.create(filename='paused.bin')
        target = Path(self.db.get_task(tid)['save_path'])
        entered, release = threading.Event(), threading.Event()
        def pause_merge(chunks, staged, **_kwargs):
            Path(staged).write_bytes(b'partial merge')
            entered.set()
            release.wait(5)
            return False
        stopper = None
        try:
            with patch('downloader.core.download_engine.merge_chunks', side_effect=pause_merge):
                self.assertTrue(self.manager.start_task(tid))
                self.assertTrue(entered.wait(3))
                stopper = threading.Thread(target=self.manager.pause_all)
                stopper.start()
                time.sleep(0.1)
                self.assertTrue(stopper.is_alive())
                release.set()
                stopper.join(4)
            self.assertFalse(stopper.is_alive())
            self.assertEqual(self.db.get_task(tid)['status'], 'paused')
            self.assertFalse(target.exists())
            self.assertEqual(list(target.parent.glob('.daw-merge-*')), [])
            self.assertTrue(all(Path(c['temp_file']).exists() for c in self.db.get_chunks(tid)))
            self.assertTrue(self.manager.resume_task(tid))
            self.assertEqual(Path(self.wait_task(tid)['save_path']).read_bytes(), PAYLOAD)
        finally:
            release.set()
            if stopper:
                stopper.join(5)


if __name__ == '__main__':
    unittest.main()
