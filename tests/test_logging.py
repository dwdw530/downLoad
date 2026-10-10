# -*- coding: utf-8 -*-
"""日志配置：写文件、可回退目录，且绝不占用 stdout（桥接协议专用）。"""
import contextlib
import io
import logging
import os
import sys
import tempfile
import unittest
from logging.handlers import RotatingFileHandler
from pathlib import Path
from unittest import mock

from downloader.utils import app_log


class LoggingTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def tearDown(self):
        app_log.reset_logging()

    def _setup(self, **kwargs):
        kwargs.setdefault('root', str(self.root))
        kwargs.setdefault('level', logging.DEBUG)
        # 在重定向后创建 stderr handler，避免测试输出被日志污染
        with contextlib.redirect_stderr(io.StringIO()):
            return app_log.setup_logging(force=True, **kwargs)

    def _log_text(self, directory=None):
        path = (directory or self.root / 'logs') / app_log.LOG_FILENAME
        return path.read_text(encoding='utf-8')

    def test_writes_file_without_touching_stdout(self):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            logger = self._setup()
            logger.info('启动完成')
            logger.warning('分块失败: %s', 'task-1')

        self.assertEqual('', out.getvalue())
        content = self._log_text()
        self.assertIn('启动完成', content)
        self.assertIn('分块失败: task-1', content)
        for handler in logger.handlers:
            self.assertIsNot(getattr(handler, 'stream', None), sys.stdout)

    def test_submodule_loggers_share_the_same_file(self):
        with contextlib.redirect_stderr(io.StringIO()):
            self._setup()
            logging.getLogger('downloader.core.download_engine').error('子模块错误')

        content = self._log_text()
        self.assertIn('downloader.core.download_engine', content)
        self.assertIn('子模块错误', content)

    def test_falls_back_to_local_app_data_when_program_dir_is_not_writable(self):
        blocker = self.root / 'not-a-directory'
        blocker.write_text('x', encoding='utf-8')
        fallback = self.root / 'local'

        with mock.patch.dict(os.environ, {'LOCALAPPDATA': str(fallback)}):
            directory = app_log.log_directory(str(blocker))
            self.assertEqual(fallback / 'LaoWangDownloader' / 'logs', directory)
            self.assertTrue(directory.is_dir())
            with contextlib.redirect_stderr(io.StringIO()):
                logger = app_log.setup_logging(force=True, root=str(blocker), level=logging.INFO)
                logger.error('回退目录也能记录')

        self.assertIn('回退目录也能记录', self._log_text(directory))

    def test_frozen_environment_has_no_stream_handler(self):
        # 打包后无控制台，日志只落文件，避免任何意外输出干扰桥接协议
        with mock.patch.object(sys, 'frozen', True, create=True):
            logger = self._setup()
        streams = [getattr(handler, 'stream', None) for handler in logger.handlers]
        self.assertNotIn(sys.stderr, streams)
        self.assertNotIn(sys.stdout, streams)

    def test_safe_error_hides_sensitive_request_details(self):
        error = ValueError('http://user:secret@example.com/video.mp4?token=abc')

        self.assertEqual('ValueError', app_log.safe_error(error, sensitive=True))
        self.assertIn('example.com', app_log.safe_error(error))

    def test_setup_is_idempotent_and_keeps_one_file_handler(self):
        first = self._setup()
        second = app_log.setup_logging(root=str(self.root), level=logging.DEBUG)

        self.assertIs(first, second)
        file_handlers = [h for h in second.handlers if isinstance(h, RotatingFileHandler)]
        self.assertEqual(1, len(file_handlers))

        with contextlib.redirect_stderr(io.StringIO()):
            second.info('只写一次')
        self.assertEqual(1, self._log_text().count('只写一次'))

    def test_level_defaults_from_environment(self):
        with mock.patch.dict(os.environ, {'DAW_LOG_LEVEL': 'WARNING'}):
            logger = self._setup(level=None)
        self.assertEqual(logging.WARNING, logger.level)

        with contextlib.redirect_stderr(io.StringIO()):
            logger.info('不应写入')
            logger.warning('应该写入')
        content = self._log_text()
        self.assertNotIn('不应写入', content)
        self.assertIn('应该写入', content)

    def test_missing_log_directory_does_not_break_logging(self):
        with mock.patch.object(app_log, 'log_directory', return_value=None):
            with contextlib.redirect_stderr(io.StringIO()):
                logger = app_log.setup_logging(force=True, root=str(self.root), level=logging.INFO)
                logger.error('没有目录也不能抛异常')


if __name__ == '__main__':
    unittest.main()
