# -*- coding: utf-8 -*-
"""应用日志：写文件（可回退目录），绝不占用 stdout。

stdout 是 BrowserBridge 的原生消息协议通道，任何 handler 都不允许写入 stdout。
"""
import logging
import os
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

LOGGER_NAME = 'downloader'
LOG_FILENAME = 'app.log'
MAX_BYTES = 2 * 1024 * 1024
BACKUP_COUNT = 3
_FORMAT = '%(asctime)s %(levelname)s [%(name)s] %(message)s'
_configured = False


def get_logger(suffix=None):
    """获取应用 logger（子级沿用 downloader 命名空间）"""
    return logging.getLogger(LOGGER_NAME if not suffix else LOGGER_NAME + '.' + suffix)


def safe_error(error, sensitive=False):
    """敏感请求（异常里可能含 URL 或 Cookie）只记录类型名，避免凭据落盘"""
    return type(error).__name__ if sensitive else str(error)


def log_directory(root=None):
    """返回可写日志目录：优先程序目录，装到只读位置时回退用户本地目录"""
    if root is None:
        # 延迟导入，避免与 config 形成循环依赖
        from downloader.utils.config import get_app_root
        root = get_app_root()
    candidates = [Path(root) / 'logs']
    local_app_data = os.environ.get('LOCALAPPDATA')
    if local_app_data:
        candidates.append(Path(local_app_data) / 'LaoWangDownloader' / 'logs')
    else:
        candidates.append(Path.home() / '.daw-downloader' / 'logs')

    for candidate in candidates:
        try:
            candidate.mkdir(parents=True, exist_ok=True)
            probe = candidate / '.write-check'
            probe.write_text('', encoding='utf-8')
            probe.unlink()
            return candidate
        except OSError:
            continue
    return None


def _resolve_level(level=None):
    value = level if level is not None else os.environ.get('DAW_LOG_LEVEL', 'INFO')
    if isinstance(value, int):
        return value
    return getattr(logging, str(value).upper(), logging.INFO)


def reset_logging():
    """移除已配置的 handler，允许重新初始化（测试用）"""
    global _configured
    logger = logging.getLogger(LOGGER_NAME)
    for handler in list(logger.handlers):
        logger.removeHandler(handler)
        handler.close()
    _configured = False


def setup_logging(level=None, root=None, force=False):
    """配置文件日志。

    Args:
        level: 日志级别，缺省读 DAW_LOG_LEVEL，再缺省 INFO
        root: 程序根目录（测试用），缺省取 get_app_root()
        force: 重新配置（测试用）
    Returns:
        配置后的 logger
    """
    global _configured
    logger = logging.getLogger(LOGGER_NAME)
    if _configured and not force:
        return logger

    reset_logging()
    # 不向 root 传播，避免第三方库或外部 logging 配置重复输出
    logger.propagate = False
    logger.setLevel(_resolve_level(level))
    logger.addHandler(logging.NullHandler())

    directory = log_directory(root)
    if directory:
        # delay=True：延迟到首次写入才占用文件句柄
        file_handler = RotatingFileHandler(directory / LOG_FILENAME, maxBytes=MAX_BYTES,
                                           backupCount=BACKUP_COUNT, encoding='utf-8',
                                           delay=True)
        file_handler.setFormatter(logging.Formatter(_FORMAT))
        logger.addHandler(file_handler)

    # 冻结环境（console=False）没有控制台，只留文件；stdout 永不写入
    if not getattr(sys, 'frozen', False):
        stream_handler = logging.StreamHandler(sys.stderr)
        stream_handler.setFormatter(logging.Formatter('%(levelname)s [%(name)s] %(message)s'))
        logger.addHandler(stream_handler)

    _configured = True
    return logger
