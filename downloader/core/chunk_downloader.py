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
import threading
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
    """速度限制器（共享限速，按虚拟时间轴排队）

    老王说：限速得按全局算，每个任务各限一份等于没限！

    多个分块/多个任务共享同一个实例：每次预扣占一段“导线时间”，
    后到的排在前一个后面。这样多个线程同时开工也不会叠加突破上限，
    且令牌计算全在锁内、sleep 放锁外，不会一条线程捂着锁把别人拖死。
    """

    # 单次预扣最多占 1/8 秒额度：低限速时也能及时响应暂停
    SLICE_DIVISOR = 8
    # 限速下限 1 KB/s：界面上误填 1（KB/s=1024）以下不至于把下载卡死
    MIN_LIMIT = 1024

    def __init__(self, bytes_per_second: int = 0):
        """
        Args:
            bytes_per_second: 每秒允许的字节数，0表示不限速
        """
        self.bytes_per_second = self._normalize(bytes_per_second)
        self._lock = threading.Lock()
        # 虚拟时钟：下一个可以开始写数据的时刻（不会预借未来的额度）
        self._deadline = time.monotonic()

    @classmethod
    def _normalize(cls, value) -> int:
        """统一限速值：负数/非法值归 0，小而正的值抬到下限"""
        try:
            value = int(value or 0)
        except (TypeError, ValueError):
            return 0
        return max(cls.MIN_LIMIT, value) if value > 0 else 0

    def set_limit(self, bytes_per_second: int):
        """动态设置限速；值没变时什么都不做"""
        limit = self._normalize(bytes_per_second)
        with self._lock:
            if limit == self.bytes_per_second:
                return
            self.bytes_per_second = limit
            # 换了限速就重新起算，不能沿用旧限速下的积压
            self._deadline = time.monotonic()

    def _reserve(self, count: int) -> float:
        """锁内排队：返回这段数据的发送时长（需要等待的秒数）"""
        with self._lock:
            rate = self.bytes_per_second
            if rate <= 0:
                return 0.0
            now = time.monotonic()
            # 闲置时时间轴已经过去，不能反过来预借未来额度（否则多线程会叠加超速）
            start = self._deadline if self._deadline > now else now
            finish = start + count / rate
            self._deadline = finish
            return max(0.0, finish - now)

    def acquire(self, bytes_count: int):
        """
        获取令牌（会阻塞直到有足够令牌）
        Args:
            bytes_count: 需要的字节数
        """
        remaining = max(0, int(bytes_count or 0))
        while remaining > 0:
            rate = self.bytes_per_second
            if rate <= 0:
                return  # 不限速，直接返回
            step = max(1, min(remaining, rate // self.SLICE_DIVISOR))
            wait = self._reserve(step)
            if wait > 0:
                time.sleep(wait)
            remaining -= step


class ChunkDownloader:
    """单个分块下载器"""

    def __init__(self, chunk_id: int, task_id: str, url: str,
                 start_byte: int, end_byte: int, temp_file: str,
                 timeout: int = 30, retry_times: int = 3,
                 user_agent: str = "PyDownloader/1.0",
                 speed_limit: int = 0,
                 proxies: dict = None,
                 use_range: bool = True, request_context=None,
                 total_size=None, etag=None, last_modified=None,
                 limiter: Optional[SpeedLimiter] = None,
                 unknown_size: bool = False):
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
            limiter: 共享限速器（多任务合计限速）；不传则按 speed_limit 自建一个
            unknown_size: 未知大小模式：仅限非 Range、单线程整流下载，完成判定为响应流正常结束
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

        # 未知大小模式：只允许「非 Range + 起点 0 + 零哨兵」组合，错误组合拒绝，
        # 不能退回可疑分块路径；expected_size 一律视为 None（流自然结束才算完成）。
        self.unknown_size = bool(unknown_size)
        if self.unknown_size and (self.use_range or self.start_byte != 0
                                  or self.end_byte != -1 or self.total_size not in (0, None)):
            raise ValueError('未知大小模式只允许非Range单线程整流下载')

        self.downloaded_bytes = 0  # 已下载字节数
        self.is_paused = False  # 暂停标志
        self.is_cancelled = False  # 取消标志
        self.last_error = None  # 最后一次失败原因（ChunkDownloadError）
        self._reported_bytes = 0  # 上次回调给外部（数据库/UI）的字节数
        self._reported_at = 0.0  # 上次回调时间

        # 速度限制器：有共享实例就复用，保证多任务合计不超限
        self.speed_limiter = limiter if limiter is not None else SpeedLimiter(speed_limit)

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

        expected_size = None if self.unknown_size else self.end_byte - self.start_byte + 1
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
            if ((expected_size is not None and self.downloaded_bytes > expected_size)
                    or (not self.use_range and self.downloaded_bytes)):
                with open(self.temp_file, 'wb'):
                    pass
                self.downloaded_bytes = 0

            actual_start = self.start_byte + self.downloaded_bytes

            # 本地快捷成功只属于固定大小分块；未知大小哪怕有残片也必须真实发请求。
            if expected_size is not None and self.downloaded_bytes == expected_size:
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

            expected_size = None if self.unknown_size else self.end_byte - self.start_byte + 1
            # 未知大小：本次响应带正 Length 时严格核对实际读取量，不借未知模式接受短流。
            response_length = None
            if self.unknown_size:
                # 冲突分帧信息（Transfer-Encoding: chunked 与 Content-Length 同时存在）：
                # urllib3 会忽略 Length 按 chunked 读，字节数恰好相等时会被误判成功，直接拒绝。
                encoding = (response.headers.get('Transfer-Encoding') or '').lower()
                has_length = 'Content-Length' in response.headers
                if has_length and 'chunked' in encoding:
                    raise ChunkDownloadError(
                        '服务器同时返回 Transfer-Encoding 与 Content-Length，无法确认文件边界', 'protocol')
                if has_length:
                    raw_length = (response.headers.get('Content-Length') or '').strip()
                    # 头存在但值为空/纯空白是非法值，不能当作“没有长度”放过
                    if raw_length == '':
                        raise ChunkDownloadError('服务器返回非法的Content-Length头', 'protocol')
                    try:
                        response_length = int(raw_length)
                    except ValueError:
                        raise ChunkDownloadError('服务器返回非法的Content-Length头', 'protocol')
                    if response_length < 0:
                        raise ChunkDownloadError('服务器返回非法的Content-Length头', 'protocol')
                    # 显式 0 也允许：方案 A 下真实空响应按正常 EOF 完成，不拒绝。
            mode = 'ab' if self.downloaded_bytes > 0 else 'wb'
            with open(self.temp_file, mode) as f:
                for data in response.iter_content(chunk_size=8192):
                    if not self._wait_if_paused_or_cancelled():
                        self._report_progress(force=True)
                        return False
                    if data:
                        if self.unknown_size:
                            if (response_length is not None
                                    and self.downloaded_bytes + len(data) > response_length):
                                raise ChunkDownloadError('服务器返回的数据超过Content-Length',
                                                         'protocol' if self.use_range else 'fatal')
                        elif self.downloaded_bytes + len(data) > expected_size:
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

            if self.unknown_size:
                # 未知大小只有响应流正常结束才算完成；按本次响应的实际 Length 严格核对。
                if response_length is not None and self.downloaded_bytes != response_length:
                    raise ChunkDownloadError('连接中断，数据未接收完整', 'transient')
            elif self.downloaded_bytes != expected_size:
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
