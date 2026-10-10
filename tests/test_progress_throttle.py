# -*- coding: utf-8 -*-
"""分块进度回调节流：少写库、收尾不丢最后一跳，以及 WAL 开关。"""
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from downloader.core import chunk_downloader
from downloader.core.chunk_downloader import ChunkDownloader
from downloader.database.db_manager import DatabaseManager


class FakeResponse:
    """替身响应：按 8 KB 吐数据，可在指定位置触发取消。"""

    def __init__(self, total, start=0, on_chunk=None, status_code=206):
        self.start = start
        self.length = total - start
        self.total = total
        self.status_code = status_code
        self.headers = {'Content-Range': f'bytes {start}-{total - 1}/{total}',
                        'Content-Encoding': 'identity'}
        self._on_chunk = on_chunk

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def raise_for_status(self):
        return None

    def iter_content(self, chunk_size=8192):
        sent = 0
        while sent < self.length:
            size = min(chunk_size, self.length - sent)
            sent += size
            if self._on_chunk:
                self._on_chunk(self.start + sent)
            yield b'\x00' * size


class ProgressThrottleTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def _downloader(self, total, reports, **kwargs):
        kwargs.setdefault('retry_times', 1)
        downloader = ChunkDownloader(chunk_id=1, task_id='task', url='http://example.com/file',
                                     start_byte=0, end_byte=total - 1,
                                     temp_file=str(self.root / 'part0'), **kwargs)
        downloader.set_progress_callback(lambda _chunk_id, size: reports.append(size))
        return downloader

    def test_reports_are_throttled_by_bytes(self):
        total = 4 * 1024 * 1024
        reports = []
        downloader = self._downloader(total, reports)

        with mock.patch.object(chunk_downloader.requests, 'get', return_value=FakeResponse(total)):
            self.assertTrue(downloader.download())

        # 未节流时会有 512 次回调
        self.assertEqual(512, total // 8192)
        self.assertLessEqual(len(reports), total // chunk_downloader.PROGRESS_INTERVAL_BYTES + 2)
        self.assertGreater(len(reports), 1)
        self.assertEqual(total, downloader.downloaded_bytes)
        self.assertEqual(total, reports[-1])

    def test_cancel_reports_final_bytes(self):
        total = 1024 * 1024
        reports = []
        downloader = self._downloader(total, reports)

        def on_chunk(sent):
            if sent >= 300 * 1024:
                downloader.cancel()

        response = FakeResponse(total, on_chunk=on_chunk)
        with mock.patch.object(chunk_downloader.requests, 'get', return_value=response):
            self.assertFalse(downloader.download())

        self.assertTrue(downloader.is_cancelled)
        self.assertGreater(reports[-1], 0)
        self.assertEqual(downloader.downloaded_bytes, reports[-1])

    def test_stream_error_does_not_report_phantom_bytes(self):
        class BrokenResponse(FakeResponse):
            def iter_content(self, chunk_size=8192):
                yield b'\x00' * 8192
                raise ConnectionError('boom')

        total = 64 * 1024
        reports = []
        downloader = self._downloader(total, reports)

        with mock.patch.object(chunk_downloader.requests, 'get', return_value=BrokenResponse(total)):
            self.assertFalse(downloader.download())

        self.assertTrue(all(0 < size <= total for size in reports), reports)
        self.assertEqual(8192, downloader.downloaded_bytes)
        self.assertEqual(downloader.downloaded_bytes, reports[-1])

    def test_resume_reports_complete_final_value(self):
        total = 512 * 1024
        reports = []
        downloader = self._downloader(total, reports)
        Path(downloader.temp_file).write_bytes(b'\x00' * 256 * 1024)

        response = FakeResponse(total, start=256 * 1024)
        with mock.patch.object(chunk_downloader.requests, 'get', return_value=response) as get:
            self.assertTrue(downloader.download(resume=True))

        self.assertTrue(get.called)
        self.assertEqual(total, downloader.downloaded_bytes)
        self.assertEqual(total, reports[-1])


class DatabaseWalTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def test_wal_mode_is_enabled(self):
        DatabaseManager(str(self.root / 'wal.db'))

        connection = sqlite3.connect(str(self.root / 'wal.db'))
        try:
            mode = connection.execute('PRAGMA journal_mode').fetchone()[0]
        finally:
            connection.close()
        self.assertEqual('wal', mode.lower())

    def test_unsupported_wal_is_tolerated(self):
        manager = DatabaseManager(str(self.root / 'plain.db'))
        cursor = mock.MagicMock()

        def execute(statement, *_args, **_kwargs):
            if str(statement).startswith('PRAGMA journal_mode'):
                raise sqlite3.DatabaseError('journal_mode not supported here')
            return mock.DEFAULT

        cursor.execute.side_effect = execute
        connection = mock.MagicMock()
        connection.cursor.return_value = cursor

        with mock.patch.object(manager, '_get_connection', return_value=connection):
            manager._init_database()  # 不应抛异常

        self.assertTrue(cursor.execute.called)


if __name__ == '__main__':
    unittest.main()
