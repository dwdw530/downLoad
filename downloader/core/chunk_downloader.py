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
from contextlib import contextmanager
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
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

# Retry-After 只在这两个状态上生效（RFC 6585 §4 允许 429 携带）
RETRY_AFTER_STATUS = frozenset({429, 503})
# 自动重试等待窗口（秒）：只约束新重试，不是整条下载的硬时限
RETRY_WINDOW_SECONDS = 300.0
# 恶意超长数字头不保留原文，直接按“远超预算”处理
RETRY_AFTER_MAX_DIGITS = 20
RETRY_AFTER_HUGE_SECONDS = 1e12

RETRY_BUDGET_MESSAGE = '本次自动重试已结束，已保留下载数据，可稍后重试'
RETRY_SERVER_WAIT_MESSAGE = '服务器要求等待时间超出本次重试上限，已保留下载数据，可稍后重试'


def parse_retry_after(value, now=None) -> Optional[float]:
    """解析 Retry-After，返回相对秒数；非法值返回 None。

    支持非负整数（含 0）与合法 HTTP 日期。过去时间视为 0；
    非法数字/日期/拼接值全部回退原指数退避。合法超大整数按“远超预算”返回，
    不会被裁短成 1 秒后重发。
    """
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    if re.fullmatch(r'[0-9]+', text):
        # 长度保护看有效数字，不把合法的前导零（包括长串 0）误判为超长等待。
        digits = text.lstrip('0') or '0'
        if len(digits) > RETRY_AFTER_MAX_DIGITS:
            return RETRY_AFTER_HUGE_SECONDS
        try:
            return float(int(digits))
        except ValueError:
            return None

    if len(text) > 128:
        return None
    try:
        target = parsedate_to_datetime(text)
    except (TypeError, ValueError, OverflowError):
        return None
    if target is None:
        return None
    if target.tzinfo is None:
        # HTTP 日期按 GMT/UTC 解释，不能用本机时区猜测。
        target = target.replace(tzinfo=timezone.utc)
    else:
        target = target.astimezone(timezone.utc)
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    else:
        current = current.astimezone(timezone.utc)
    return max(0.0, (target - current).total_seconds())


class RetryBudget:
    """同一个 task run 内所有分块、内外层共享的自动重试窗口。

    窗口从第一次 transient 开始，不按块/轮重置；不落库、不跨 run 共享。
    只保存首次失败时间与截止时间，不引入全站限流或后台调度。
    """

    def __init__(self, window_seconds: float = RETRY_WINDOW_SECONDS, clock=None):
        try:
            window = float(window_seconds)
        except (TypeError, ValueError):
            window = RETRY_WINDOW_SECONDS
        self.window_seconds = max(0.0, window)
        self._clock = clock or time.monotonic
        self._deadline = None
        self._lock = threading.Lock()

    @property
    def deadline(self):
        with self._lock:
            return self._deadline

    def note_transient(self):
        """记录第一次 transient；后续调用不重置窗口。"""
        with self._lock:
            if self._deadline is None:
                self._deadline = self._clock() + self.window_seconds
            return self._deadline

    def expired(self, at=None) -> bool:
        deadline = self.deadline
        if deadline is None:
            return False
        current = self._clock() if at is None else at
        return current >= deadline


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

    def __init__(self, message: str, kind: str = 'transient', retry_not_before: float = 0.0):
        super().__init__(message)
        self.message = message
        self.kind = kind
        # 服务器要求的绝对等待点（monotonic）；0 表示没有有效 Retry-After
        self.retry_not_before = float(retry_not_before or 0.0)


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
                 unknown_size: bool = False,
                 retry_budget: Optional[RetryBudget] = None,
                 retry_not_before: float = 0.0,
                 clock=None,
                 pre_start_hook: Optional[Callable] = None):
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

        # 重试预算与跨轮 not-before：由引擎创建/传递，纯内部调度用。
        self._clock = clock or time.monotonic
        self.retry_budget = retry_budget
        self.retry_not_before = float(retry_not_before or 0.0)
        # 安全提交钩子：只在所有拒绝条件都已通过、即将发首个请求前调用。
        # 降级单线程用它把旧块清理/进度重置推迟到真正开始整流时。
        self._pre_start_hook = pre_start_hook
        self._pending_reset = False

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
        # 被协调器停止（另一块 fatal）时不要先清空临时文件/字节：已完成块和
        # 其他块的真实字节必须保留。先同步实际大小再退出。
        if not self._wait_if_paused_or_cancelled():
            self._sync_downloaded_from_temp()
            return False
        self.last_error = None
        self._reported_bytes = 0
        self._reported_at = 0.0

        # 重试门禁必须早于任何破坏性重置：拒绝启动时不能截断/清零现有断点。
        # 降级单线程/重建下载器时也可能带着同一 run 的共享窗口与 not-before。
        if self.retry_not_before > self._clock():
            if not self._wait_for_retry_not_before():
                self._sync_downloaded_from_temp()
                return False
        else:
            blocked = self._retry_block_reason()
            if blocked:
                self.last_error = ChunkDownloadError(blocked, 'transient')
                self._sync_downloaded_from_temp()
                return False

        # 重新下载和非Range请求都要丢弃旧内容，但截断必须推迟到“即将发请求”
        # 的安全提交点：启动延迟/等待中暂停跨截止而被拒绝时，原 tmp 必须保留。
        self._pending_reset = bool(os.path.exists(self.temp_file)
                                   and (not resume or not self.use_range))

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

            # 超长临时文件也只能在最终请求门禁通过后重置。等待/调度可能已经
            # 跨过共享窗口，不能在此先截断，再以“已保留数据”的原因拒绝请求。
            if expected_size is not None and self.downloaded_bytes > expected_size:
                self._pending_reset = True

            actual_start = (self.start_byte if self._pending_reset
                            else self.start_byte + self.downloaded_bytes)

            # 本地快捷成功只属于固定大小分块；未知大小哪怕有残片也必须真实发请求。
            # 待重置的旧内容不能当成已完成分块。
            if (not self._pending_reset and expected_size is not None
                    and self.downloaded_bytes == expected_size):
                self._report_progress(force=True)
                return True

            try:
                if self._download_chunk(actual_start):
                    return True
                # False 只代表暂停/取消，不是错误，不能进重试
                return False
            except ChunkDownloadError as error:
                self.last_error = error
                if error.retry_not_before:
                    self.retry_not_before = max(self.retry_not_before, error.retry_not_before)
                if error.kind == 'transient':
                    self._note_retry_budget()
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
                self._note_retry_budget()
                logger.warning("分块%s下载异常（尝试%s/%s）: %s", self.chunk_id, attempt, attempts, detail)
                if attempt >= attempts:
                    return False
                if not self._wait_before_retry(attempt):
                    return False

    def _retry_block_reason(self) -> Optional[str]:
        """共享重试窗口门禁：返回终止原因，None 表示可以自动重试。

        只约束新自动请求的启动/等待，不是已发出响应的读取硬时限；
        与 not-before 是否仍在未来无关：继承的窗口已过期也必须拒绝。
        """
        budget = self.retry_budget
        if budget is None:
            return None
        deadline = budget.deadline
        if deadline is None:
            return None
        if self.retry_not_before and self.retry_not_before >= deadline:
            return RETRY_SERVER_WAIT_MESSAGE
        if budget.expired():
            return RETRY_BUDGET_MESSAGE
        return None

    def _wait_for_retry_not_before(self) -> bool:
        """首次请求前等待尚未到期的绝对 not-before；预算不足则结束。"""
        budget = self.retry_budget
        deadline = budget.deadline if budget is not None else None
        if deadline is not None and self.retry_not_before >= deadline:
            self.last_error = ChunkDownloadError(RETRY_SERVER_WAIT_MESSAGE, 'transient')
            return False
        if deadline is not None and self._clock() >= deadline:
            self.last_error = ChunkDownloadError(RETRY_BUDGET_MESSAGE, 'transient')
            return False
        while self._clock() < self.retry_not_before:
            if not self._wait_if_paused_or_cancelled():
                return False
            if budget is not None and budget.expired():
                self.last_error = ChunkDownloadError(RETRY_BUDGET_MESSAGE, 'transient')
                return False
            time.sleep(min(0.2, max(0.0, self.retry_not_before - self._clock())))
        if budget is not None and budget.expired():
            self.last_error = ChunkDownloadError(RETRY_BUDGET_MESSAGE, 'transient')
            return False
        return self._wait_if_paused_or_cancelled()

    def _note_retry_budget(self):
        """第一次 transient 时启动共享窗口（同一 run 内不重置）。"""
        budget = self.retry_budget
        if budget is not None:
            budget.note_transient()

    def _sync_downloaded_from_temp(self):
        """按临时文件真实大小同步字节；用于停止/取消时保留数据。"""
        try:
            self.downloaded_bytes = os.path.getsize(self.temp_file) if os.path.exists(self.temp_file) else 0
        except OSError:
            self.downloaded_bytes = 0

    def _wait_before_retry(self, attempt: int) -> bool:
        """指数退避与 Retry-After 取最大值（不相加）；等待期间可暂停/取消。"""
        delay = min(RETRY_BACKOFF_MAX, RETRY_BACKOFF_BASE * (2 ** (attempt - 1)))
        budget = self.retry_budget
        deadline = budget.deadline if budget is not None else None
        now = self._clock()
        not_before = self.retry_not_before

        # 服务端要求的等待已达到/超过窗口边界：不截短、不抢发，直接结束自动重试。
        if deadline is not None and not_before >= deadline:
            self.last_error = ChunkDownloadError(RETRY_SERVER_WAIT_MESSAGE, 'transient')
            return False
        if deadline is not None and now >= deadline:
            self.last_error = ChunkDownloadError(RETRY_BUDGET_MESSAGE, 'transient')
            return False

        target = max(now + delay, not_before)
        if deadline is not None and target >= deadline:
            message = (RETRY_SERVER_WAIT_MESSAGE if not_before >= deadline
                       else RETRY_BUDGET_MESSAGE)
            self.last_error = ChunkDownloadError(message, 'transient')
            return False

        while self._clock() < target:
            if not self._wait_if_paused_or_cancelled():
                return False
            if budget is not None and budget.expired():
                self.last_error = ChunkDownloadError(RETRY_BUDGET_MESSAGE, 'transient')
                return False
            time.sleep(min(0.2, max(0.0, target - self._clock())))
        if budget is not None and budget.expired():
            self.last_error = ChunkDownloadError(RETRY_BUDGET_MESSAGE, 'transient')
            return False
        return self._wait_if_paused_or_cancelled()

    def _http_error(self, response) -> ChunkDownloadError:
        """普通响应与浏览器 HTTPError 共用状态分类，不保留敏感异常原文。"""
        error = ChunkDownloadError(f'服务器返回 HTTP {response.status_code}',
                                   classify_status(response.status_code))
        if response.status_code in RETRY_AFTER_STATUS:
            seconds = parse_retry_after(response.headers.get('Retry-After'))
            if seconds is not None:
                error.retry_not_before = self._clock() + seconds
                logger.info('服务器要求等待 %.0f 秒: chunk=%s, task=%s, status=%s',
                            seconds, self.chunk_id, self.task_id, response.status_code)
        return error

    @contextmanager
    def _request_response(self, headers):
        """保持浏览器安全请求路径，并接住它在 yield 前抛出的 HTTPError。"""
        get = requests.get if self.request_context is None else lambda url, **kw: browser_get(url, self.request_context, **kw)
        try:
            with get(self.url, headers=headers, stream=True, timeout=self.timeout,
                     proxies=self.proxies) as response:
                yield response
        except requests.HTTPError as error:
            # Response 对 4xx/5xx 的 bool 为 False，不能用 if response 判断是否存在。
            if error.response is None:
                raise ChunkDownloadError('服务器请求失败', 'transient') from None
            raise self._http_error(error.response) from None

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
            self._pending_reset = True

        # 艹，发请求前也要尊重暂停，不然会出现“明明暂停了还在重试打服务器”
        if not self._wait_if_paused_or_cancelled():
            return False

        # 门禁紧邻实际新请求：等待跨过截止、暂停后调度延迟也在这里被拦住。
        blocked = self._retry_block_reason()
        if blocked:
            self.last_error = ChunkDownloadError(blocked, 'transient')
            raise self.last_error

        # 安全提交点：至此所有拒绝条件都已通过，才允许删旧块/重置进度/截断 tmp。
        # 钩子先执行（可能因为 run 已失效拒绝），只有它成功后才会截断本地文件。
        if self._pending_reset or self._pre_start_hook is not None:
            hook = self._pre_start_hook
            if hook is not None:
                hook()
                self._pre_start_hook = None
            if self._pending_reset:
                with open(self.temp_file, 'wb'):
                    pass
                self.downloaded_bytes = 0
                self._pending_reset = False
                start = self.start_byte

        headers = {'User-Agent': self.user_agent, 'Accept-Encoding': 'identity'}
        if self.use_range:
            headers['Range'] = f'bytes={start}-{self.end_byte}'
            if self.etag or self.last_modified:
                headers['If-Range'] = self.etag or self.last_modified
        elif self.etag:
            headers['If-Match'] = self.etag
        elif self.last_modified:
            headers['If-Unmodified-Since'] = self.last_modified

        # 发起请求
        with self._request_response(headers) as response:
            expected_status = 206 if self.use_range else 200
            if response.status_code != expected_status:
                if self.use_range and response.status_code == 200:
                    # 服务端不认 Range，多线程拿到的是整份文件，继续拼就是脏数据
                    raise ChunkDownloadError('服务器未按分块请求返回，无法多线程下载', 'protocol')
                raise self._http_error(response)

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
