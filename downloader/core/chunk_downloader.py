# -*- coding: utf-8 -*-
"""
分块下载器
老王说：这玩意儿是核心中的核心，写不好整个下载器都白搭！
"""
import logging
import os
import re
import requests
from downloader.browser.security import browser_get
import time
from typing import Callable, Optional


logger = logging.getLogger(__name__)

# 进度上报阈值：攼够这么多字节或过了这么久才回调一次，
# 避免每 8 KB 写一次数据库（1 GB 文件约 13 万次）。
PROGRESS_INTERVAL_BYTES = 256 * 1024
PROGRESS_INTERVAL_SECONDS = 0.5

# 临时性失败的重试退避：1s → 2s → 4s …… 封顶 30s
RETRY_BACKOFF_BASE = 1.0
RETRY_BACKOFF_MAX = 30.0

# 可重试的 HTTP 状态（服务端临时性问题，等一等通常能好）
RETRYABLE_STATUS = frozenset({408, 425, 429, 500, 502, 503, 504, 507, 520, 521, 522, 524})


def classify_status(status_code: int) -> str:
    """把 HTTP 状态码分成临时性/致命两类，决定要不要重试"""
    return 'transient' if status_code in RETRYABLE_STATUS or status_code >= 500 else 'fatal'


class ChunkDownloadError(Exception):
    """
    分块下载失败，带失败类型：

    - transient：超时/断流/5xx/429，退避后重试有意义
    - fatal：403/404/410、文件版本变化，重试也是白搭
    - protocol：服务器不守 Range 或返回内容不符合协议，需要降级单线程
    """

    def __init__(self, message: str, kind: str = 'transient'):
        super().__init__(message)
        self.message = message
        self.kind = kind


class SpeedLimiter:
    """
    速度限制器（令牌桶算法）
    老王说：限速这事儿得用令牌桶，简单又好使！
    """

    def __init__(self, bytes_per_second: int = 0):
        """
        Args:
            bytes_per_second: 每秒允许的字节数，0表示不限速
        """
        self.bytes_per_second = bytes_per_second
        self._last_time = time.monotonic()
        self._tokens = 0  # 当前可用令牌（字节数）

    def set_limit(self, bytes_per_second: int):
        """动态设置限速"""
        self.bytes_per_second = bytes_per_second

    def acquire(self, bytes_count: int):
        """
        获取令牌（会阻塞直到有足够令牌）
        Args:
            bytes_count: 需要的字节数
        """
        if self.bytes_per_second <= 0:
            return  # 不限速，直接返回

        now = time.monotonic()
        elapsed = now - self._last_time
        self._last_time = now

        # 补充令牌（按时间比例）
        self._tokens += elapsed * self.bytes_per_second
        # 令牌上限为1秒的量，防止积攒太多导致突发流量
        self._tokens = min(self._tokens, self.bytes_per_second)

        # 如果令牌不够，等待
        if bytes_count > self._tokens:
            wait_time = (bytes_count - self._tokens) / self.bytes_per_second
            time.sleep(wait_time)
            # 等待产生的额度已经用于本次写入，不能再次计入下一次请求。
            self._last_time = time.monotonic()
            self._tokens = 0
        else:
            self._tokens -= bytes_count


class ChunkDownloader:
    """单个分块下载器"""

    def __init__(self, chunk_id: int, task_id: str, url: str,
                 start_byte: int, end_byte: int, temp_file: str,
                 timeout: int = 30, retry_times: int = 3,
                 user_agent: str = "PyDownloader/1.0",
                 speed_limit: int = 0,
                 proxies: dict = None,
                 use_range: bool = True, request_context=None,
                 total_size=None, etag=None, last_modified=None):
        """
        初始化分块下载器
        Args:
            chunk_id: 分块ID（数据库主键）
            task_id: 任务ID
            url: 下载链接
            start_byte: 起始字节
            end_byte: 结束字节
            temp_file: 临时文件路径
            timeout: 请求超时
            retry_times: 重试次数
            user_agent: User-Agent
            speed_limit: 速度限制（字节/秒），0表示不限速
            proxies: 代理配置，格式 {"http": "...", "https": "..."} 或 None
            use_range: 是否使用Range请求头
        """
        self.chunk_id = chunk_id
        self.task_id = task_id
        self.url = url
        self.start_byte = start_byte
        self.end_byte = end_byte
        self.temp_file = temp_file
        self.timeout = timeout
        self.retry_times = retry_times
        self.user_agent = user_agent
        self.proxies = proxies  # 代理配置
        self.use_range = use_range  # 是否使用Range头
        self.request_context = request_context
        self.total_size = total_size
        self.etag = etag
        self.last_modified = last_modified

        self.downloaded_bytes = 0  # 已下载字节数
        self.is_paused = False  # 暂停标志
        self.is_cancelled = False  # 取消标志
        self.last_error = None  # 最后一次失败原因（ChunkDownloadError）
        self._reported_bytes = 0  # 上次回调给外部（数据库/UI）的字节数
        self._reported_at = 0.0  # 上次回调时间

        # 速度限制器
        self.speed_limiter = SpeedLimiter(speed_limit)

        # 进度回调函数
        self.progress_callback: Optional[Callable] = None

    def set_progress_callback(self, callback: Callable):
        """
        设置进度回调函数
        Args:
            callback: 回调函数，签名为 callback(chunk_id, downloaded_bytes)
        """
        self.progress_callback = callback

    def error_kind(self) -> str:
        """最后一次失败的类别；没失败过按临时错误算"""
        return self.last_error.kind if self.last_error else 'transient'

    def error_message(self) -> str:
        """最后一次失败的文案；没失败过返回空串"""
        return self.last_error.message if self.last_error else ''

    def _report_progress(self, force: bool = False) -> bool:
        """按字节或时间阈值上报进度。

        Args:
            force: 收尾时刻（结束/暂停/取消）强制上报，保证数据库拿到最后一跳
        Returns:
            True 表示本次确实回调了
        """
        if not self.progress_callback:
            return False
        now = time.monotonic()
        if not force:
            if (self.downloaded_bytes - self._reported_bytes < PROGRESS_INTERVAL_BYTES
                    and now - self._reported_at < PROGRESS_INTERVAL_SECONDS):
                return False
        self._reported_bytes = self.downloaded_bytes
        self._reported_at = now
        self.progress_callback(self.chunk_id, self.downloaded_bytes)
        return True

    def download(self, resume: bool = False) -> bool:
        """
        执行下载
        Args:
            resume: 是否为断点续传
        Returns:
            True表示成功，False表示失败（原因见 last_error）
        """
        # 确保临时文件目录存在
        temp_dir = os.path.dirname(self.temp_file)
        if temp_dir and not os.path.exists(temp_dir):
            os.makedirs(temp_dir, exist_ok=True)

        expected_size = self.end_byte - self.start_byte + 1
        # 重新下载和非Range请求都必须丢弃旧内容。
        if os.path.exists(self.temp_file) and (not resume or not self.use_range):
            with open(self.temp_file, 'wb'):
                pass
        self.downloaded_bytes = 0
        self.last_error = None
        self._reported_bytes = 0
        self._reported_at = 0.0

        attempts = max(1, int(self.retry_times or 1))
        for attempt in range(1, attempts + 1):
            # 艹，暂停后不能继续疯狂重试，不然用户点了暂停还会被写成失败
            if not self._wait_if_paused_or_cancelled():
                return False

            # 艹，每次重试都必须按当前临时文件重算偏移，不然会重复写导致分块烂掉
            if os.path.exists(self.temp_file):
                self.downloaded_bytes = os.path.getsize(self.temp_file)
            else:
                self.downloaded_bytes = 0

            # 超长临时文件不能当作完整分块；非Range重试也必须从头写入。
            if self.downloaded_bytes > expected_size or (not self.use_range and self.downloaded_bytes):
                with open(self.temp_file, 'wb'):
                    pass
                self.downloaded_bytes = 0

            actual_start = self.start_byte + self.downloaded_bytes

            if self.downloaded_bytes == expected_size:
                self._report_progress(force=True)
                return True

            try:
                if self._download_chunk(actual_start):
                    return True
                # False 只代表暂停/取消，不是错误，不能进重试
                return False
            except ChunkDownloadError as error:
                self.last_error = error
                detail = type(error).__name__ if self.request_context else error.message
                logger.warning("分块%s下载失败（尝试%s/%s）: %s", self.chunk_id, attempt, attempts, detail)
                if error.kind != 'transient' or attempt >= attempts:
                    return False
                if not self._wait_before_retry(attempt):
                    return False
            except Exception as error:
                # 没归类的异常（连接重置、DNS 抖动等）按临时错误重试，别一次就判死
                detail = type(error).__name__ if self.request_context else str(error)
                self.last_error = ChunkDownloadError(detail, 'transient')
                logger.warning("分块%s下载异常（尝试%s/%s）: %s", self.chunk_id, attempt, attempts, detail)
                if attempt >= attempts:
                    return False
                if not self._wait_before_retry(attempt):
                    return False

        return False

    def _wait_before_retry(self, attempt: int) -> bool:
        """指数退避后重试；等待期间仍能响应暂停/取消"""
        delay = min(RETRY_BACKOFF_MAX, RETRY_BACKOFF_BASE * (2 ** (attempt - 1)))
        deadline = time.monotonic() + delay
        while time.monotonic() < deadline:
            if not self._wait_if_paused_or_cancelled():
                return False
            time.sleep(min(0.2, max(0.0, deadline - time.monotonic())))
        return self._wait_if_paused_or_cancelled()

    def _download_chunk(self, start: int) -> bool:
        """
        实际下载逻辑
        Args:
            start: 实际起始字节
        Returns:
            True表示该分块下完；False表示被暂停/取消（不是错误）
        Raises:
            ChunkDownloadError：真正的失败，带 transient/fatal/protocol 分类
        """
        # 艹，非Range请求不允许带历史偏移，必须从块起点重下，避免重复拼接
        if not self.use_range and start > self.start_byte:
            start = self.start_byte
            self.downloaded_bytes = 0

        headers = {'User-Agent': self.user_agent, 'Accept-Encoding': 'identity'}
        if self.use_range:
            headers['Range'] = f'bytes={start}-{self.end_byte}'
            if self.etag or self.last_modified:
                headers['If-Range'] = self.etag or self.last_modified
        elif self.etag:
            headers['If-Match'] = self.etag
        elif self.last_modified:
            headers['If-Unmodified-Since'] = self.last_modified

        # 艹，发请求前也要尊重暂停，不然会出现“明明暂停了还在重试打服务器”
        if not self._wait_if_paused_or_cancelled():
            return False

        # 发起请求
        get = requests.get if self.request_context is None else lambda url, **kw: browser_get(url, self.request_context, **kw)
        with get(
            self.url,
            headers=headers,
            stream=True,
            timeout=self.timeout,
            proxies=self.proxies  # 代理支持
        ) as response:
            expected_status = 206 if self.use_range else 200
            if response.status_code != expected_status:
                if self.use_range and response.status_code == 200:
                    # 服务端不认 Range，多线程拿到的是整份文件，继续拼就是脏数据
                    raise ChunkDownloadError('服务器未按分块请求返回，无法多线程下载', 'protocol')
                raise ChunkDownloadError(f'服务器返回 HTTP {response.status_code}',
                                         classify_status(response.status_code))

            if self.etag and response.headers.get('ETag', self.etag) != self.etag:
                raise ChunkDownloadError('服务器文件版本已变化，请重新下载', 'fatal')
            if (not self.etag and self.last_modified
                    and response.headers.get('Last-Modified', self.last_modified) != self.last_modified):
                raise ChunkDownloadError('服务器文件修改时间已变化，请重新下载', 'fatal')
            if response.headers.get('Content-Encoding', 'identity').lower() != 'identity':
                raise ChunkDownloadError('服务器返回压缩内容，无法按字节范围保存', 'protocol')
            if self.use_range:
                content_range = re.fullmatch(r'bytes (\d+)-(\d+)/(\d+|\*)',
                                             response.headers.get('Content-Range', ''))
                if (not content_range or int(content_range[1]) != start
                        or int(content_range[2]) != self.end_byte
                        or (self.total_size is not None and content_range[3] != str(self.total_size))):
                    raise ChunkDownloadError('服务器返回的Content-Range与请求不一致', 'protocol')

            expected_size = self.end_byte - self.start_byte + 1
            mode = 'ab' if self.downloaded_bytes > 0 else 'wb'
            with open(self.temp_file, mode) as f:
                for data in response.iter_content(chunk_size=8192):
                    if not self._wait_if_paused_or_cancelled():
                        self._report_progress(force=True)
                        return False
                    if data:
                        if self.downloaded_bytes + len(data) > expected_size:
                            raise ChunkDownloadError(
                                '服务器返回的数据超过预期分块大小',
                                'protocol' if self.use_range else 'fatal')
                        self.speed_limiter.acquire(len(data))
                        if not self._wait_if_paused_or_cancelled():
                            self._report_progress(force=True)
                            return False
                        f.write(data)
                        self.downloaded_bytes += len(data)
                        self._report_progress()

            if self.downloaded_bytes != expected_size:
                # 连接中途断了，缺的字节下次按偏移续传，属于临时性失败
                raise ChunkDownloadError('连接中断，数据未接收完整', 'transient')
            # 收尾必须补报一次，保证数据库拿到最终字节数
            self._report_progress(force=True)
            return True

    def _wait_if_paused_or_cancelled(self) -> bool:
        """阻塞等待暂停结束；若任务被取消则返回False"""
        while self.is_paused:
            time.sleep(0.1)
            if self.is_cancelled:
                return False
        return not self.is_cancelled

    def pause(self):
        """暂停下载"""
        self.is_paused = True

    def resume(self):
        """继续下载"""
        self.is_paused = False

    def cancel(self):
        """取消下载"""
        self.is_cancelled = True
        self.is_paused = False  # 取消暂停状态，让线程退出

    def get_progress(self) -> float:
        """
        获取下载进度
        Returns:
            进度百分比（0-100）
        """
        total = self.end_byte - self.start_byte + 1
        return (self.downloaded_bytes / total) * 100 if total > 0 else 0
