# -*- coding: utf-8 -*-
"""
下载引擎
老王说：这是整个下载器的大脑，得写得聪明点！
"""
import logging
import os
import re
import tempfile
import threading
import uuid
import time
import requests
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Optional, Callable
from downloader.core.chunk_downloader import (
    ChunkDownloader, ChunkDownloadError, SpeedLimiter, RetryBudget,
    RETRY_BUDGET_MESSAGE, RETRY_SERVER_WAIT_MESSAGE)
from downloader.database.db_manager import DatabaseManager
from downloader.utils.config import ConfigManager
from downloader.utils.file_utils import (merge_chunks, get_filename_from_url, calculate_file_hash,
                                        validate_filename, publish_download, parse_content_disposition,
                                        check_free_space, format_size,
                                        resolve_download_directory)
from downloader.browser.security import browser_get, normalize_context, protect, unprotect
from downloader.core.youtube_downloader import YoutubeDownloader, video_task_url, require_tools, VIDEO_KINDS, CONTEXT_KINDS, video_request_context


logger = logging.getLogger(__name__)

# 失败分块重试：轮数与总时长双上限，避免断网时任务无限挂住；
# 协议性失败（服务端不守 Range）不在此列，仍然降级单线程。
CHUNK_RETRY_ROUNDS = 3
CHUNK_RETRY_DEADLINE = 300
CHUNK_RETRY_BACKOFF = (1, 3, 10)

# 孤儿临时文件：{task_id}.partN / {task_id}.tmp（顶层固定命名，不用递归）
ORPHAN_TEMP_PATTERN = re.compile(
    r'([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})\.(?:part\d+|tmp)')
ORPHAN_TEMP_MIN_AGE_DAYS = 7

# 添加任务失败原因的默认文案（引擎只产出代码与具体说明，界面直接展示）
ADD_ERROR_DETAILS = {
    'network': '网络连接失败，请检查网络或代理设置',
    'dns': '域名解析失败，请检查网络或代理设置',
    'timeout': '连接超时，请检查网络或代理设置',
    'http_4xx': '服务器拒绝了请求，链接可能已失效或需要登录',
    'http_5xx': '服务器暂时出错，请稍后重试',
    'html_page': '服务器返回的是网页而不是文件，可能需要先登录',
    'no_length': '服务器未提供文件大小，当前版本暂不支持这类链接',
    'encoding_not_supported': '服务器返回压缩内容，暂不支持按范围下载',
    'range_protocol': '服务器未按分块方式返回数据',
    'invalid_filename': '链接里的文件名不合法',
    'invalid_url': '链接格式不正确',
    'disk_space': '磁盘空间不足',
    'unknown': '无法获取文件大小或链接不可用',
}


class DownloadEngine:
    """下载引擎总控"""

    def __init__(self, db_manager: DatabaseManager, config_manager: ConfigManager):
        """
        初始化下载引擎
        Args:
            db_manager: 数据库管理器
            config_manager: 配置管理器
        """
        self.db = db_manager
        self.config = config_manager
        self.active_downloaders = {}  # {task_id: [ChunkDownloader, ...]}
        self.thread_pools = {}  # {task_id: ThreadPoolExecutor}
        self.cancelled_tasks = set()  # 艹，取消标记必须单独维护，不然后台线程会把状态回写炸掉
        self.task_run_ids = {}  # {task_id: run_id}，用于隔离旧下载会话，防止暂停/恢复串状态
        self._lifecycle = threading.Condition(threading.RLock())
        self._starting_tasks = {}  # 含网络探测阶段的在途启动/恢复，退出时也必须等待
        self._shutdown_requested = False
        # {task_id: {chunk_id: 已下载字节}}：进度监控只读内存，不再每秒查分块表
        self.chunk_progress = {}

        # 全局限速：所有普通文件分块共享一个令牌桶，speed_limit 是“总量”而不是每任务
        self.speed_limiter = SpeedLimiter(self.config.speed_limit)
        # 添加任务失败原因按线程隔离：UI 后台线程与 BrowserBridge 互不影响
        self._add_state = threading.local()

        # 回调函数
        self.progress_callback: Optional[Callable] = None
        self.status_callback: Optional[Callable] = None

    def _next_task_run_id(self, task_id: str) -> int:
        """生成任务新的下载会话ID"""
        with self._lifecycle:
            next_run_id = int(self.task_run_ids.get(task_id, 0)) + 1
            self.task_run_ids[task_id] = next_run_id
            return next_run_id

    def _is_current_task_run(self, task_id: str, run_id: int) -> bool:
        """判断当前逻辑是否仍属于任务最新会话"""
        return (not self._shutdown_requested
                and int(self.task_run_ids.get(task_id, 0)) == int(run_id))

    @contextmanager
    def _starting_download(self, task_id, new_run=True):
        """登记在途启动，网络等待不持锁，退出后拒绝新的登记。"""
        with self._lifecycle:
            if self._shutdown_requested:
                run_id = None
            else:
                run_id = (self._next_task_run_id(task_id) if new_run
                          else int(self.task_run_ids.get(task_id, 0)))
                self._starting_tasks[task_id] = self._starting_tasks.get(task_id, 0) + 1
        try:
            yield run_id
        finally:
            if run_id is not None:
                with self._lifecycle:
                    remaining = self._starting_tasks[task_id] - 1
                    if remaining:
                        self._starting_tasks[task_id] = remaining
                    else:
                        del self._starting_tasks[task_id]
                    self._lifecycle.notify_all()

    def begin_shutdown(self):
        """先封住启动入口与旧会话回调，再由 shutdown 等待和清理。"""
        with self._lifecycle:
            self._shutdown_requested = True

    def _get_task_status(self, task_id: str) -> Optional[str]:
        """读取任务当前状态（数据库真值）"""
        task = self.db.get_task(task_id)
        return task.get('status') if task else None

    def _is_task_paused(self, task_id: str) -> bool:
        """任务是否处于暂停状态"""
        return self._get_task_status(task_id) == 'paused'

    def _stop_active_task_workers(self, task_id: str, invalidate_run: bool = True, wait: bool = False):
        """停止任务的活跃下载线程与线程池（不改任务状态）"""
        # 先作废回调，再等待线程退出，避免停止期间触发合并或失败降级。
        if invalidate_run:
            self._next_task_run_id(task_id)
        downloaders = self.active_downloaders.pop(task_id, [])
        thread_pool = self.thread_pools.pop(task_id, None)
        for downloader in downloaders:
            downloader.cancel()
        if thread_pool:
            try:
                thread_pool.shutdown(wait=wait, cancel_futures=True)
            except Exception as e:
                logger.warning("关闭线程池失败: task=%s, err=%s", task_id, e)

    def set_progress_callback(self, callback: Callable):
        """
        设置进度回调
        Args:
            callback: 回调函数，签名为 callback(task_id, downloaded_size, total_size, speed)
        """
        self.progress_callback = callback

    def set_status_callback(self, callback: Callable):
        """
        设置状态回调
        Args:
            callback: 回调函数，签名为 callback(task_id, status, message)
        """
        self.status_callback = callback

    def check_url_support_range(self, url: str, request_context=None, metadata=None) -> tuple[bool, int]:
        """
        检查URL是否支持Range请求（分块下载）
        Args:
            url: 下载链接
            request_context: 浏览器上下文（可选）
            metadata: 可选输出字典；会写入 reason/status/error/etag/last_modified/filename/final_url
                      成功时另写 probe_ok=True 与 length_kind（known/unknown/empty）
        Returns:
            (是否支持Range, 文件大小)；拿不到大小时 size 为 0，原因看 metadata['reason']。
            200 且完全缺少 Content-Length 时自身入口算探测成功（length_kind=unknown），
            浏览器上下文仍按原 no_length 失败。
        """
        if metadata is not None:
            # 每次调用先重置：probe_ok 与 length_kind 只反映本次探测结果，
            # 不能把上一次成功留下的标记串到这次失败里。
            metadata.setdefault('reason', None)
            metadata.setdefault('status', None)
            metadata['probe_ok'] = False
            metadata['length_kind'] = None

        def mark(reason: str, status: int = None, detail: str = None):
            """把失败原因交给调用方，界面才能说清为什么加不进去"""
            if metadata is None:
                return
            metadata['reason'] = reason
            if status is not None:
                metadata['status'] = status
            metadata['error'] = detail or ADD_ERROR_DETAILS.get(reason, ADD_ERROR_DETAILS['unknown'])

        try:
            # 一次流式GET同时确定Range支持和真实大小，也兼容禁用HEAD的站点。
            headers = {'User-Agent': self.config.user_agent,
                       'Range': 'bytes=0-0', 'Accept-Encoding': 'identity'}
            get = requests.get if request_context is None else lambda url, **kw: browser_get(url, request_context, **kw)
            with get(url, headers=headers, stream=True,
                              timeout=self.config.timeout, allow_redirects=True,
                              proxies=self.config.proxies) as response:
                if metadata is not None:
                    # 重定向后的地址往往才带着真实文件名
                    metadata['final_url'] = response.url
                    content_type = (response.headers.get('Content-Type') or '').lower()
                    # 登录页/错误页可能也带 attachment 头，别把网页名字当成文件名
                    if 'text/html' not in content_type:
                        metadata['filename'] = parse_content_disposition(
                            response.headers.get('Content-Disposition'))
                    etag = response.headers.get('ETag', '')
                    metadata['etag'] = etag if etag.startswith('"') and etag.endswith('"') else None
                    metadata['last_modified'] = response.headers.get('Last-Modified') or None

                try:
                    response.raise_for_status()
                except requests.HTTPError:
                    status = response.status_code
                    is_html = 'text/html' in (response.headers.get('Content-Type') or '').lower()
                    # 401/403 拿到网页，基本就是登录页；404 的 HTML 错误页不算登录墙
                    if is_html and status in (401, 403):
                        reason = 'html_page'
                        detail = (f'服务器返回 HTTP {status}（返回的是网页，可能需要先在浏览器登录）')
                    else:
                        reason = 'http_5xx' if status and status >= 500 else 'http_4xx'
                        detail = f'服务器返回 HTTP {status}'
                    mark(reason, status, detail)
                    logger.error("检查URL失败: HTTP %s", status)
                    return False, 0

                if response.headers.get('Content-Encoding', 'identity').lower() != 'identity':
                    mark('encoding_not_supported')
                    return False, 0
                if response.status_code == 206:
                    match = re.fullmatch(r'bytes 0-0/(\d+)', response.headers.get('Content-Range', ''))
                    if match and int(match[1]) > 0:
                        if metadata is not None:
                            metadata['probe_ok'] = True
                            metadata['length_kind'] = 'known'
                        return True, int(match[1])
                    mark('range_protocol')
                    return False, 0
                if response.status_code == 200:
                    # 真正缺少 Content-Length 才算“长度未知”：空字符串/纯空白的头是非法值，
                    # 不能当作“没有这个头”放行，否则畸形的 Content-Length 也能混进新模式。
                    if 'Content-Length' not in response.headers:
                        if request_context is None:
                            if metadata is not None:
                                metadata['probe_ok'] = True
                                metadata['length_kind'] = 'unknown'
                            return False, 0
                        mark('no_length')
                        return False, 0
                    raw_length = (response.headers.get('Content-Length') or '').strip()
                    if raw_length == '':
                        mark('no_length')
                        return False, 0
                    try:
                        size = int(raw_length)
                    except ValueError:
                        # 头解析不了不等于“缺少头”，保持旧的通用失败分类
                        mark('unknown')
                        return False, 0
                    if size < 0:
                        mark('no_length')
                        return False, 0
                    if size == 0:
                        # 方案 A：显式零长度与未知长度同口径，自身入口放行，浏览器仍拒绝。
                        if request_context is None:
                            if metadata is not None:
                                metadata['probe_ok'] = True
                                metadata['length_kind'] = 'empty'
                            return False, 0
                        mark('no_length')
                        return False, 0
                    if metadata is not None:
                        metadata['probe_ok'] = True
                        metadata['length_kind'] = 'known'
                    return False, size
                mark('unknown', response.status_code)
                return False, 0
        except requests.Timeout:
            mark('timeout')
            logger.error("检查URL失败: 请求超时")
            return False, 0
        except requests.ConnectionError as error:
            text = str(error).lower()
            reason = 'dns' if ('name or service not known' in text or 'getaddrinfo' in text
                               or 'nodename nor servname' in text) else 'network'
            mark(reason)
            logger.error("检查URL失败: 连接失败")
            return False, 0
        except Exception as e:
            detail = type(e).__name__ if request_context else str(e)
            logger.error("检查URL失败: %s", detail)
            mark('unknown')
            return False, 0

    def create_download_task(self, url: str, filename: Optional[str] = None,
                            save_path: Optional[str] = None,
                            expected_hash: Optional[str] = None,
                            hash_type: str = "md5", request_context=None,
                            start_later: bool = False) -> Optional[str]:
        """
        创建下载任务
        Args:
            url: 下载链接
            filename: 文件名（可选，不提供则从URL提取）
            save_path: 保存路径（可选，不提供则使用默认下载目录）
            expected_hash: 预期哈希值（可选，用于下载后校验）
            hash_type: 哈希类型（md5/sha256）
            start_later: 是否稍后手动开始（任务直接以 paused 落库，不进队列调度）
        Returns:
            任务ID，失败返回None
        """
        request_context = normalize_context(url, request_context)
        # 生成任务ID
        task_id = str(uuid.uuid4())
        # 每次添加先清掉本线程上一次的失败原因，避免旧原因串到这次结果上
        self._set_add_error(None)

        # 解码后再次校验，不能把 URL 中编码的路径分隔符带入保存路径。
        # 非法文件名必须在发请求前就拒掉，不能为了一个坑链接白探一次。
        try:
            explicit_filename = bool(filename)
            filename = validate_filename(filename) if explicit_filename else get_filename_from_url(url)
        except ValueError as error:
            self._set_add_error('invalid_filename', str(error))
            if self.status_callback:
                self.status_callback(task_id, 'failed', str(error))
            return None

        metadata = {}
        support_range, total_size = self.check_url_support_range(url, request_context, metadata=metadata)

        # 未知大小/零字节（方案 A）：只对下载器自身入口（无浏览器上下文）且探测明确
        # 属于新成功类别的响应放行；浏览器右键入口保持原有拒绝行为。
        unknown_size_mode = bool(metadata.get('probe_ok')
                                 and metadata.get('length_kind') in ('unknown', 'empty')
                                 and request_context is None)

        # 文件名优先级：服务器 CD > 重定向后的真实地址 > 原始地址（显式指定的最优先）
        if not explicit_filename:
            candidate = metadata.get('filename')
            if not candidate and metadata.get('final_url'):
                try:
                    candidate = get_filename_from_url(metadata['final_url'])
                except ValueError:
                    candidate = None
            if candidate:
                filename = candidate
        # 目录决策必须在最终文件名之后：显式目录原样使用，未指定才按类型分类
        if save_path:
            target_dir = save_path
        else:
            target_dir = resolve_download_directory(
                self.config.download_dir, filename,
                bool(getattr(self.config, 'auto_classify_downloads', False)))
        save_path = os.path.join(target_dir, filename)

        if total_size == 0 and not unknown_size_mode:
            reason = metadata.get('reason') or 'no_length'
            detail = metadata.get('error') or ADD_ERROR_DETAILS.get(reason)
            self._set_add_error(reason, detail)
            if self.status_callback:
                self.status_callback(task_id, 'failed', detail)
            return None

        # 磁盘空间预检：同卷时分块和合并副本同时存在，峰值是两倍
        ok, space_detail = check_free_space(self._space_requirements(total_size, save_path))
        if not ok:
            self._set_add_error('disk_space', space_detail)
            logger.warning('创建任务前空间不足: %s', space_detail)
            if self.status_callback:
                self.status_callback(task_id, 'failed', space_detail)
            return None

        # 确定线程数
        thread_count = self.config.thread_count if support_range else 1
        if support_range:
            # 艹，小文件线程数绝不能比字节数大，不然分块区间会出现 end=-1 这种脏数据
            thread_count = max(1, min(thread_count, total_size))

        # 先构建分块数据，后面要和任务创建一起进事务，防止半拉子脏任务
        chunks = []
        if support_range and thread_count > 1:
            chunks = self._build_chunks(task_id, total_size, thread_count)

        # 艹，任务+分块必须放在同一个事务里，不然中途失败就是数据库烂尾
        success = self.db.create_task_with_chunks(
            task_id=task_id,
            url=url,
            filename=filename,
            save_path=save_path,
            total_size=total_size,
            support_range=support_range,
            thread_count=thread_count,
            chunks=chunks,
            expected_hash=expected_hash,
            hash_type=hash_type,
            browser_context=protect(request_context) if request_context else None,
            status='paused' if start_later else 'pending',
            resource_etag=metadata.get('etag'), resource_last_modified=metadata.get('last_modified'),
            reserve_path=True,
        )

        if not success:
            return None

        return task_id

    def _space_requirements(self, total_size: int, save_path: str) -> list:
        """创建任务前需要多少空间

        分块在临时目录、合并副本在目标目录，两边各需一份；
        同盘时由 check_free_space 自动合并需求（加起来就是两倍）。
        """
        target_dir = os.path.dirname(os.path.abspath(save_path))
        temp_dir = os.path.abspath(self.config.temp_dir)
        return [(temp_dir, total_size, '临时目录'), (target_dir, total_size, '下载目录')]

    def set_speed_limit(self, bytes_per_second: int):
        """设置全局限速（所有普通文件任务合计），运行中立即生效"""
        self.speed_limiter.set_limit(bytes_per_second)
        self.config.speed_limit = max(0, int(bytes_per_second or 0))

    def _set_add_error(self, reason: Optional[str], detail: Optional[str] = None):
        """记录本线程最近一次添加任务失败的原因"""
        self._add_state.last_error = reason
        self._add_state.last_detail = detail

    @property
    def last_add_error(self) -> Optional[str]:
        """本线程最近一次添加任务失败的原因代码（add_task 返回 None 时读）"""
        return getattr(self._add_state, 'last_error', None)

    @property
    def last_add_detail(self) -> Optional[str]:
        """本线程最近一次添加任务失败的具体说明（可能带状态码/缺口字节）"""
        return getattr(self._add_state, 'last_detail', None)

    def _build_chunks(self, task_id: str, total_size: int, thread_count: int) -> list[tuple[int, int, int, str]]:
        """构建分块区间（不写数据库）"""
        # 艹，这里再兜一层，任何入口传来的线程数都必须合法
        valid_thread_count = max(1, min(thread_count, total_size))
        chunk_size = total_size // valid_thread_count
        chunks = []

        for i in range(valid_thread_count):
            start_byte = i * chunk_size
            end_byte = (i + 1) * chunk_size - 1 if i < valid_thread_count - 1 else total_size - 1
            temp_file = os.path.join(self.config.temp_dir, f"{task_id}.part{i}")
            chunks.append((i, start_byte, end_byte, temp_file))

        return chunks

    def retarget_temp_dir(self, new_dir: str, exclude=()) -> int:
        """切换临时目录后，把没有断点数据的任务分块指向新目录。

        已有字节的分块不能就地换路径：文件还在旧目录，改了之后恢复会当成
        新任务重下，合并也找不到分块。返回实际同步的任务数。
        """
        new_dir = os.path.abspath(new_dir)
        exclude = set(exclude or ())
        updated = 0
        for task in self.db.get_all_tasks():
            task_id = task['task_id']
            if task_id in exclude or task_id in self.active_downloaders:
                continue
            if task['status'] in ('completed', 'verify_failed', 'cancelled'):
                continue
            chunks = self.db.get_chunks(task_id)
            if not chunks:
                continue
            targets = []
            blocked = False
            for chunk in chunks:
                old_path = chunk.get('temp_file')
                if not old_path:
                    blocked = True
                    break
                old_path = os.path.abspath(old_path)
                if os.path.normcase(os.path.dirname(old_path)) == os.path.normcase(new_dir):
                    continue
                try:
                    has_data = os.path.getsize(old_path) > 0
                except OSError:
                    has_data = False
                if int(chunk.get('downloaded_bytes') or 0) > 0 or has_data:
                    blocked = True
                    break
                targets.append((chunk['chunk_id'], os.path.join(new_dir, os.path.basename(old_path))))
            # 整个任务要么全迁要么不迁，一半新一半旧会在合并时找不到分块
            if blocked or not targets:
                continue
            if self.db.update_chunk_temp_files(targets):
                updated += 1
        return updated

    def _create_chunks(self, task_id: str, url: str, total_size: int, thread_count: int):
        """
        创建分块记录
        Args:
            task_id: 任务ID
            url: 下载链接
            total_size: 文件总大小
            thread_count: 线程数
        """
        # 艹，统一复用分块构建逻辑，避免两个地方算区间出现偏差
        chunks = self._build_chunks(task_id, total_size, thread_count)
        self.db.create_chunks(task_id, chunks)

    def create_youtube_task(self, url, filename, directory, height=720, kind='youtube', request_context=None):
        url = video_task_url(url, kind)
        context = video_request_context(url, kind, request_context) if kind in CONTEXT_KINDS else None
        require_tools()
        if height not in (480, 720, 1080):
            raise ValueError('不支持的清晰度')
        validate_filename(filename)
        if os.path.basename(filename) != filename or not filename.lower().endswith('.mp4'):
            raise ValueError('无效的视频文件名')
        task_id = str(uuid.uuid4())
        # directory=None 表示默认分类目录；已解析的显式目录原样使用，不二次分类
        if not directory:
            directory = resolve_download_directory(
                self.config.download_dir, filename,
                bool(getattr(self.config, 'auto_classify_downloads', False)))
        if self.db.create_task_with_chunks(task_id, url, filename, os.path.join(directory, filename),
                total_size=0, support_range=False, thread_count=1, download_type=kind, video_height=height,
                browser_context=protect(context) if context else None, reserve_path=True):
            return task_id
        return None

    def _start_youtube_download(self, task, run_id):
        task_id = task['task_id']
        current = lambda: self._is_current_task_run(task_id, run_id)

        def progress(done, size, speed):
            if current():
                self.db.update_task_size(task_id, size)
                self.db.update_task_progress(task_id, done, speed)
                if self.progress_callback:
                    self.progress_callback(task_id, done, size, speed)

        def complete(size):
            if current():
                self.db.update_task_size(task_id, size)
                self._verify_and_finish(task_id, task['save_path'], run_id=run_id)

        def fail(message):
            if current():
                self.db.update_task_status(task_id, 'failed', message)
                if self.status_callback:
                    self.status_callback(task_id, 'failed', message)

        worker = YoutubeDownloader(task, self.config, progress, complete, fail, current)
        self.cancelled_tasks.discard(task_id)
        self.active_downloaders[task_id] = [worker]
        pool = ThreadPoolExecutor(max_workers=1)
        self.thread_pools[task_id] = pool
        self.db.update_task_status(task_id, 'downloading')
        if self.status_callback:
            self.status_callback(task_id, 'downloading', '正在解析并下载视频')
        pool.submit(worker.run)
        return True

    def start_download(self, task_id: str, resume: bool = False) -> bool:
        """
        开始下载任务
        Args:
            task_id: 任务ID
            resume: 是否为断点续传
        Returns:
            True表示启动成功，False表示失败
        """
        with self._starting_download(task_id) as run_id:
            if run_id is None:
                return False
            return self._start_download(task_id, resume, run_id)

    def _start_download(self, task_id: str, resume: bool, run_id: int) -> bool:
        # 获取任务信息
        task = self.db.get_task(task_id)
        if not task:
            logger.error("任务不存在: %s", task_id)
            return False

        # 艹，启动前先清掉历史残留活跃线程，避免重复下载器互相踩状态
        if task_id in self.active_downloaders or task_id in self.thread_pools:
            self._stop_active_task_workers(task_id, invalidate_run=False, wait=True)

        # 旧任务或探测期间外部新建的同名文件也不能被覆盖。
        try:
            self.db.reserve_task_path(task_id)
            task = self.db.get_task(task_id)
            if task.get('download_type') in VIDEO_KINDS:
                with self._lifecycle:
                    if not self._is_current_task_run(task_id, run_id):
                        return False
                    return self._start_youtube_download(task, run_id)

            context = unprotect(task['browser_context']) if task.get('browser_context') else None
            task, resume = self._prepare_file_task(task, resume, context, run_id)
            task['_request_context'] = context
        except Exception as error:
            if self._is_current_task_run(task_id, run_id):
                self.db.update_task_status(task_id, 'failed', str(error))
                if self.status_callback:
                    self.status_callback(task_id, 'failed', str(error))
            return False
        # 最终检查与创建线程必须和关闭入口互斥，不能在检查之后被退出穿插。
        with self._lifecycle:
            if not self._is_current_task_run(task_id, run_id):
                return False
            self.cancelled_tasks.discard(task_id)
            self.db.update_task_status(task_id, 'downloading')
            if self.status_callback:
                self.status_callback(task_id, 'downloading', '开始下载')
            if task['support_range'] and task['thread_count'] > 1:
                return self._start_multithread_download(task, resume, run_id)
            return self._start_singlethread_download(task, resume, run_id)

    def _prepare_file_task(self, task, resume, context, run_id):
        """续传前核对资源身份；无验证器或资源变化时从零重下，不拼接不同版本。"""
        metadata = {}
        ranges, size = self.check_url_support_range(task['url'], context, metadata=metadata)
        if not self._is_current_task_run(task['task_id'], run_id):
            raise InterruptedError('资源探测期间任务已停止')
        # 未知大小（方案 A）：仅自身入口的新成功类别允许零哨兵；其余零/负大小仍失败。
        unknown_ok = (bool(metadata.get('probe_ok')
                           and metadata.get('length_kind') in ('unknown', 'empty')
                           and context is None
                           and task.get('download_type', 'file') == 'file'
                           and not task.get('browser_context')))
        if size <= 0 and not unknown_ok:
            raise ValueError('无法重新确认下载资源，请稍后重试')
        old_etag = task.get('resource_etag')
        old_modified = task.get('resource_last_modified')
        same_version = (old_etag == metadata.get('etag') if old_etag else
                        bool(old_modified and old_modified == metadata.get('last_modified')))
        # 无资源验证器时只发完整请求，不能把多次请求的内容拼在一起。
        support_range = bool(ranges and (metadata.get('etag') or metadata.get('last_modified')))
        # 未知大小/显式零长度重探测：非 Range 单线程；count 计算不能拿哨兵 0 去 min。
        count = max(1, min(task['thread_count'], size)) if support_range else 1
        reset = (not resume or not same_version or size != task['total_size']
                 or support_range != bool(task['support_range']) or count != task['thread_count'])
        chunks = None
        if reset:
            files = [chunk['temp_file'] for chunk in self.db.get_chunks(task['task_id'])]
            files += [os.path.join(self.config.temp_dir, task['task_id'] + suffix)
                      for suffix in ('.tmp', '.merged')]
            for path in files:
                if path and os.path.exists(path):
                    os.remove(path)
            chunks = self._build_chunks(task['task_id'], size, count) if count > 1 else []
            logger.info('重新确认资源，重置断点: %s', task['task_id'])
        self.db.update_task_resource(task['task_id'], size, support_range, count, metadata, chunks)
        return self.db.get_task(task['task_id']), bool(resume and not reset)

    def _start_multithread_download(self, task: dict, resume: bool, run_id: int) -> bool:
        """多线程分块下载；合并和校验也归线程池管理，暂停必须等整条链路退出。"""
        task_id = task['task_id']
        chunks = self.db.get_chunks(task_id)
        if not chunks:
            self._fail_task(task_id, '没有分块信息')
            return False

        # 进度基准以数据库现状为准，进入本轮下载后再由分块回调覆盖
        self.chunk_progress[task_id] = {
            chunk['chunk_id']: int(chunk.get('downloaded_bytes') or 0) for chunk in chunks}

        # 多留一个工作线程给调度任务本身，否则分块并行度会少一路
        thread_pool = ThreadPoolExecutor(max_workers=max(1, int(task['thread_count'])) + 1)
        self.thread_pools[task_id] = thread_pool
        # 同一 task run 的内外层共享重试窗口：第一次 transient 才启动，不按轮重置。
        retry_budget = RetryBudget(CHUNK_RETRY_DEADLINE)
        threading.Thread(target=self._monitor_progress,
                         args=(task_id, task['total_size'], time.time(), run_id), daemon=True).start()
        # 不能用不受管理的 daemon 合并线程，否则暂停返回后它仍在写输出。
        thread_pool.submit(self._run_chunk_rounds, task, chunks, resume, run_id, thread_pool,
                           retry_budget)
        return True

    def _build_chunk_downloaders(self, task: dict, chunk_rows: list,
                                 retry_budget: RetryBudget = None,
                                 not_before: dict = None) -> list:
        """按分块行构建下载器；所有分块共享引擎级限速器（总量限速）"""
        task_id = task['task_id']
        not_before = not_before or {}
        # 配置可能被设置页或外部直接改过，构建前对齐一次；值没变时 set_limit 是空操作
        self.speed_limiter.set_limit(self.config.speed_limit)
        downloaders = []
        for chunk in chunk_rows:
            downloader = ChunkDownloader(
                chunk_id=chunk['chunk_id'], task_id=task_id, url=task['url'],
                start_byte=chunk['start_byte'], end_byte=chunk['end_byte'], temp_file=chunk['temp_file'],
                timeout=self.config.timeout, retry_times=self.config.retry_times,
                user_agent=self.config.user_agent, limiter=self.speed_limiter,
                proxies=self.config.proxies, use_range=True, request_context=task.get('_request_context'),
                total_size=task['total_size'], etag=task.get('resource_etag'),
                last_modified=task.get('resource_last_modified'),
                retry_budget=retry_budget,
                retry_not_before=not_before.get(chunk['chunk_id'], 0.0),
            )
            downloader.set_progress_callback(
                lambda chunk_id, size, tid=task_id: self._record_chunk_progress(tid, chunk_id, size))
            downloaders.append(downloader)
        return downloaders

    def _run_chunk_rounds(self, task: dict, chunks: list, resume: bool, run_id: int, thread_pool,
                          retry_budget: RetryBudget = None):
        """
        跑完所有分块，只重试失败的那几块。

        失败优先级为 fatal > protocol > transient：
        - fatal：403/404/ETag 变化，立即收敛同轮其他分块并失败，保留已完成分块和字节
        - protocol：服务端不守 Range，降级单线程（fatal 同轮出现时不降级）
        - transient：超时/断流/5xx/429，在共享重试窗口内退避重试，尊重 Retry-After
        """
        task_id = task['task_id']
        rows = {chunk['chunk_id']: chunk for chunk in chunks}
        pending = list(chunks)
        downloaders = []
        not_before_map = {}
        max_not_before = 0.0

        try:
            for round_index in range(CHUNK_RETRY_ROUNDS):
                downloaders = self._build_chunk_downloaders(
                    task, pending, retry_budget, not_before_map)
                if not downloaders:
                    break
                self.active_downloaders[task_id] = downloaders
                # 首轮沿用调用方的续传意图；重试轮必须续传，否则会把已下字节抹掉
                round_resume = bool(resume) if round_index == 0 else True
                futures = {thread_pool.submit(d.download, round_resume): d for d in downloaders}

                failed = []
                fatal_message = ''
                for future in as_completed(futures):
                    if not self._can_finish(task_id, run_id):
                        return
                    downloader = futures[future]
                    try:
                        success = bool(future.result())
                    except Exception as error:
                        logger.warning('分块执行异常: chunk=%s, err=%s', downloader.chunk_id, error)
                        downloader.last_error = ChunkDownloadError(str(error), 'transient')
                        success = False
                    if not self._can_finish(task_id, run_id):
                        return
                    self.db.update_chunk_progress(downloader.chunk_id, downloader.downloaded_bytes,
                                                  'completed' if success else 'failed')
                    if downloader.retry_not_before:
                        previous = not_before_map.get(downloader.chunk_id, 0.0)
                        not_before_map[downloader.chunk_id] = max(previous, downloader.retry_not_before)
                        max_not_before = max(max_not_before, downloader.retry_not_before)
                    if not success:
                        failed.append(downloader)
                        if downloader.error_kind() == 'fatal' and not fatal_message:
                            fatal_message = downloader.error_message() or '下载失败'
                            # 只停同轮分块（复用 worker 停止标志），不把整条任务写成 cancelled；
                            # as_completed 继续收敛其他 futures，保留它们真实已下载字节。
                            for other in downloaders:
                                other.cancel()

                # fatal 优先：不补轮、不降级、不合并，已完成块和部分字节都保留
                if fatal_message:
                    self._fail_task(task_id, fatal_message)
                    return

                if not failed:
                    self._merge_and_finish(task_id, task['save_path'], chunks, run_id)
                    return

                kinds = {downloader.error_kind() for downloader in failed}
                if 'protocol' in kinds:
                    # 同 run 的降级也复用共享预算与仍有效的最大 not-before；
                    # 拒绝启动必须在删除/清零旧块之前判定。
                    blocked = self._retry_block_reason(retry_budget, max_not_before)
                    if blocked:
                        self._fail_task(task_id, blocked)
                        return
                    started = self._fallback_to_singlethread(
                        task, run_id, retry_budget, max_not_before)
                    if (not started and self._can_finish(task_id, run_id)
                            and self._get_task_status(task_id) != 'failed'):
                        self._fail_task(task_id, '服务器不支持稳定的分块下载，单线程重试也失败了')
                    return
                if 'transient' not in kinds:
                    # 没有可重试错误，再试也是浪费时间；已完成的分块和临时文件都保留
                    message = next((d.error_message() for d in failed if d.error_message()), '下载失败')
                    self._fail_task(task_id, message)
                    return
                if round_index + 1 >= CHUNK_RETRY_ROUNDS:
                    # 重试次数已用尽：说明自动重试结束，但不删除任何已有数据
                    self._fail_task(task_id, RETRY_BUDGET_MESSAGE)
                    return

                delay = CHUNK_RETRY_BACKOFF[min(round_index, len(CHUNK_RETRY_BACKOFF) - 1)]
                now = time.monotonic()
                # 外层退避与服务端 not-before 取最大值，不叠加；按绝对时间等待。
                target = max(now + delay, max_not_before)
                if retry_budget is not None:
                    deadline = retry_budget.deadline
                    if deadline is not None and (target >= deadline or retry_budget.expired(now)):
                        message = (RETRY_SERVER_WAIT_MESSAGE if max_not_before >= deadline
                                   else RETRY_BUDGET_MESSAGE)
                        self._fail_task(task_id, message)
                        return

                logger.info('重试失败分块: task=%s, 轮次=%s, 数量=%s',
                            task_id, round_index + 1, len(failed))
                if self.status_callback:
                    self.status_callback(task_id, 'downloading',
                                         f'正在重试 {len(failed)} 个分块（第 {round_index + 1} 轮）')
                if not self._sleep_until(task_id, run_id, target):
                    return
                # 等待跨过截止/暂停后的调度延迟：下一轮新请求前重新过门禁。
                blocked = self._retry_block_reason(retry_budget, 0.0)
                if blocked:
                    self._fail_task(task_id, blocked)
                    return
                # 只把失败的分块放回下一轮，已完成的一块都不重下
                pending = [rows[d.chunk_id] for d in failed if d.chunk_id in rows]
                if not pending:
                    return
        except Exception as error:
            if self._can_finish(task_id, run_id):
                self._fail_task(task_id, str(error))
        finally:
            if self.thread_pools.get(task_id) is thread_pool:
                del self.thread_pools[task_id]
            if self.active_downloaders.get(task_id) is downloaders:
                del self.active_downloaders[task_id]
            thread_pool.shutdown(wait=False)

    def _fail_task(self, task_id: str, message: str):
        """标记任务失败并通报 UI"""
        # 先结束内存进度汇总，避免监控线程对失败任务继续每秒写进度
        self.chunk_progress.pop(task_id, None)
        self.db.update_task_status(task_id, 'failed', message)
        if self.status_callback:
            self.status_callback(task_id, 'failed', message)

    def _retry_block_reason(self, retry_budget, retry_not_before: float = 0.0):
        """coordination 层门禁：返回自动重试不能启动的原因，None 表示可以继续。

        窗口由本 run 第一次 transient 开启；一旦过期，无论 not-before 是否还在未来、
        是否还有剩余轮次，都不能再发新的自动请求。无 transient（无预算）时不拦截。
        """
        if retry_budget is None:
            return None
        deadline = retry_budget.deadline
        if deadline is None:
            return None
        if retry_not_before and retry_not_before >= deadline:
            return RETRY_SERVER_WAIT_MESSAGE
        if retry_budget.expired():
            return RETRY_BUDGET_MESSAGE
        return None

    def _sleep_unless_stopped(self, task_id: str, run_id: int, delay: float) -> bool:
        """分段睡眠，期间一旦任务被暂停/取消就立刻返回 False"""
        return self._sleep_until(task_id, run_id, time.monotonic() + max(0.0, delay))

    def _sleep_until(self, task_id: str, run_id: int, target: float) -> bool:
        """按单调时钟绝对时间分段等待；暂停/取消/退出立即返回 False。"""
        while True:
            if not self._can_finish(task_id, run_id):
                return False
            remaining = target - time.monotonic()
            if remaining <= 0:
                return True
            time.sleep(min(0.2, remaining))

    def _fallback_to_singlethread(self, task: dict, run_id: int,
                                  retry_budget: RetryBudget = None,
                                  retry_not_before: float = 0.0) -> bool:
        """分块失败时自动降级到单线程重试"""
        task_id = task['task_id']

        if not self._is_current_task_run(task_id, run_id):
            return False

        # 拒绝启动的判定必须先于任何删除/清零：失败时磁盘和数据要保持原样。
        blocked = self._retry_block_reason(retry_budget, retry_not_before)
        if blocked:
            self._fail_task(task_id, blocked)
            return False

        try:
            logger.warning("检测到分块下载失败，自动降级单线程重试: %s", task_id)

            # 只先切换单线程标记（不删块、不清进度）：让后续暂停/恢复走
            # “复用现有下载器”的路径；真正的旧块清理推迟到整流启动的安全提交点。
            single_task = self.db.get_task(task_id)
            if not single_task:
                return False
            if not self.db.mark_task_singlethread(task_id):
                return False

            if not self._is_current_task_run(task_id, run_id):
                return False

            if self.status_callback:
                self.status_callback(task_id, 'downloading', '分块失败，已自动降级为单线程重试')

            return self._start_singlethread_download(
                single_task, resume=False, run_id=run_id,
                retry_budget=retry_budget, retry_not_before=retry_not_before,
                reset_on_start=True)
        except Exception as e:
            logger.error("自动降级单线程失败: %s", e)
            return False

    def _reset_chunks_for_singlethread(self, task_id: str):
        """降级单线程的安全提交点：此时已确定要发首个非 Range 请求。

        删除旧分块临时文件、重置分块与任务进度。拒绝启动时不得调用本方法。
        """
        chunks = self.db.get_chunks(task_id)
        for chunk in chunks:
            temp_file = chunk.get('temp_file')
            if temp_file and os.path.exists(temp_file):
                try:
                    os.remove(temp_file)
                except Exception as e:
                    logger.warning("删除分块临时文件失败: %s, %s", e)
            self.db.update_chunk_progress(chunk['chunk_id'], 0, 'pending')
        # 降级后本任务不再走分块汇总，旧监控线程靠这张表退出
        self.chunk_progress.pop(task_id, None)
        self.db.update_task_progress(task_id, 0, 0)

    def _start_singlethread_download(self, task: dict, resume: bool, run_id: int,
                                     retry_budget: RetryBudget = None,
                                     retry_not_before: float = 0.0,
                                     reset_on_start: bool = False) -> bool:
        """单线程下载（不支持分块的情况）

        reset_on_start：protocol 降级路径。旧块清理与进度重置不在启动前执行，
        而是在 worker 通过全部门禁、即将发首个整流请求时由安全提交钩子执行；
        所以启动延迟、等待中暂停跨截止而被拒绝时，旧块与原 tmp/进度都保留。
        """
        task_id = task['task_id']
        temp_file = os.path.join(self.config.temp_dir, f"{task_id}.tmp")

        # 创建下载器前先过同一共享窗口：拒绝启动时不刷新旧的临时进度。
        blocked = self._retry_block_reason(retry_budget, retry_not_before)
        if blocked:
            self._fail_task(task_id, blocked)
            return False

        # 未知大小模式：普通文件 + 无浏览器上下文 + 非 Range + 零哨兵才开启；
        # 其余单线程路径（含降级、有大小、浏览器任务）不进入新模式。
        unknown_size = (not reset_on_start
                        and task.get('download_type', 'file') == 'file'
                        and not task.get('browser_context') and not task.get('_request_context')
                        and not task.get('support_range')
                        and int(task.get('thread_count') or 1) == 1
                        and int(task.get('total_size') or 0) == 0)

        # 艹，非Range服务端根本不支持断点续传，恢复必须从0重下，不能继续append脏数据
        can_resume = bool(resume and task.get('support_range') and not reset_on_start)
        # 艹，这里不提前删临时文件，避免旧线程尚未释放句柄导致WinError 32；
        # 真正重置在ChunkDownloader内部按非Range规则以wb模式执行。

        def commit_singlethread_start():
            """安全提交点：已通过全部门禁，确定要发首个整流请求。"""
            if not self._is_current_task_run(task_id, run_id):
                raise ChunkDownloadError('下载会话已停止，已保留下载数据', 'transient')
            self._reset_chunks_for_singlethread(task_id)
            progress_state['last_downloaded'] = 0
            if self.progress_callback:
                self.progress_callback(task_id, 0, task['total_size'], 0)

        # 创建单线程下载器
        self.speed_limiter.set_limit(self.config.speed_limit)
        # 普通单线程使用自己的同类预算；降级路径传入同 run 的共享预算与 not-before。
        if retry_budget is None:
            retry_budget = RetryBudget(CHUNK_RETRY_DEADLINE)
        downloader = ChunkDownloader(
            chunk_id=0,
            task_id=task_id,
            url=task['url'],
            start_byte=0,
            end_byte=task['total_size'] - 1,
            temp_file=temp_file,
            timeout=self.config.timeout,
            retry_times=self.config.retry_times,
            user_agent=self.config.user_agent,
            limiter=self.speed_limiter,
            proxies=self.config.proxies,  # 代理支持
            use_range=(False if reset_on_start else bool(task.get('support_range'))),
            request_context=(task.get('_request_context') or
                             (unprotect(task['browser_context']) if task.get('browser_context') else None)),
            total_size=task['total_size'], etag=task.get('resource_etag'),
            last_modified=task.get('resource_last_modified'),
            unknown_size=unknown_size,
            retry_budget=retry_budget,
            retry_not_before=retry_not_before,
            pre_start_hook=commit_singlethread_start if reset_on_start else None,
        )

        # 艹，单线程也得走实时进度链路，不然UI速度和进度全是死的
        progress_state = {
            'last_time': time.time(),
            'last_downloaded': 0,
            'smoothed_speed': float(task.get('speed') or 0)
        }

        if can_resume and os.path.exists(temp_file):
            progress_state['last_downloaded'] = os.path.getsize(temp_file)

        # 先把当前已下载量推给UI，断点续传时进度不会从0假装重新开始。
        # 降级路径必须等安全提交点再重置，所以这里不提前写库。
        if not reset_on_start:
            self.db.update_task_progress(task_id, progress_state['last_downloaded'], 0)
            if self.progress_callback:
                self.progress_callback(task_id, progress_state['last_downloaded'],
                                       task['total_size'], 0)

        def on_single_progress(_chunk_id: int, downloaded_bytes: int):
            if not self._is_current_task_run(task_id, run_id):
                return
            now = time.time()
            elapsed = max(now - progress_state['last_time'], 1e-6)
            delta_bytes = max(0, downloaded_bytes - progress_state['last_downloaded'])
            instant_speed = max(0, delta_bytes / elapsed)

            # 艹，瞬时速度抖得像心电图，做平滑才不会把UI晃瞎
            prev_speed = max(0, float(progress_state.get('smoothed_speed', 0) or 0))
            alpha = 0.2
            speed = (prev_speed * (1 - alpha) + instant_speed * alpha) if prev_speed > 0 else instant_speed

            # 非分块暂停后恢复第一跳常出现毛刺，夹逼一下避免离谱峰值
            if task['total_size'] > 0:
                theoretical_max = task['total_size'] * 4
                speed = min(speed, theoretical_max)
            speed = max(0, speed)

            progress_state['last_time'] = now
            progress_state['last_downloaded'] = downloaded_bytes
            progress_state['smoothed_speed'] = speed

            self.db.update_task_progress(task_id, downloaded_bytes, speed)
            if self.progress_callback:
                self.progress_callback(task_id, downloaded_bytes, task['total_size'], speed)

        downloader.set_progress_callback(on_single_progress)
        downloaders_ref = [downloader]
        self.active_downloaders[task_id] = downloaders_ref
        thread_pool = ThreadPoolExecutor(max_workers=1)
        self.thread_pools[task_id] = thread_pool

        # 在后台线程下载
        def download_and_finish():
            try:
                success = downloader.download(can_resume)
                if (not self._is_current_task_run(task_id, run_id)
                        or self._is_task_cancelled(task_id) or self._is_task_paused(task_id)):
                    return
                if not success:
                    raise RuntimeError(downloader.error_message() or '下载失败')
                self._verify_and_finish(task_id, temp_file, run_id=run_id,
                                        eof_confirmed=unknown_size)
            except Exception as e:
                if (self._is_current_task_run(task_id, run_id)
                        and not self._is_task_cancelled(task_id) and not self._is_task_paused(task_id)):
                    # 降级在门禁处拒绝启动时旧分块仍在，但失败后旧监控必须退出，
                    # 否则它继续每秒写库/回调。复用公共失败收尾，不清除断点字节。
                    self._fail_task(task_id, str(e))
            finally:
                if self.active_downloaders.get(task_id) is downloaders_ref:
                    del self.active_downloaders[task_id]
                if self.thread_pools.get(task_id) is thread_pool:
                    del self.thread_pools[task_id]
                thread_pool.shutdown(wait=False)

        thread_pool.submit(download_and_finish)
        return True

    def sweep_orphan_temp(self, min_age_days: int = ORPHAN_TEMP_MIN_AGE_DAYS) -> tuple:
        """清理无主的分块临时文件（崩溃/强杀残留）。

        只有同时满足才删：顶层目录、严格 UUID 命名、库里没有任何任务引用、
        且 mtime 早于保留天数。视频子目录（temp/<kind>/<task_id>/）本轮不碰，
        避免把 yt-dlp 的中间文件误删。
        Returns:
            (删除个数, 回收字节)
        """
        temp_dir = os.path.abspath(self.config.temp_dir)
        if not os.path.isdir(temp_dir):
            return 0, 0
        try:
            task_ids = {task['task_id'] for task in self.db.get_all_tasks()}
            referenced = {os.path.normcase(os.path.abspath(path))
                          for path in self.db.get_referenced_temp_files() if path}
            entries = list(os.scandir(temp_dir))
        except Exception as error:
            logger.debug('临时目录不可用，跳过清扫: %s', error)
            return 0, 0

        deadline = time.time() - max(0, int(min_age_days)) * 86400
        removed = 0
        freed = 0
        for entry in entries:
            try:
                if not entry.is_file(follow_symlinks=False):
                    continue
                match = ORPHAN_TEMP_PATTERN.match(entry.name)
                if not match or match.group(1) in task_ids:
                    continue
                path = os.path.abspath(entry.path)
                if os.path.normcase(path) in referenced:
                    continue
                stat_result = entry.stat(follow_symlinks=False)
                if stat_result.st_mtime > deadline:
                    continue
                os.remove(path)
            except OSError as error:
                logger.debug('跳过无法清理的临时文件: %s', error)
                continue
            removed += 1
            freed += int(stat_result.st_size)

        if removed:
            # 日志不记路径，只记数量和回收量
            logger.info('临时文件清扫：删除 %s 个，回收 %s', removed, format_size(freed))
        return removed, freed

    def _merge_and_finish(self, task_id: str, save_path: str, chunks: list, run_id=None):
        """直接合并到目标目录临时文件，校验后同卷改名；暂停/失败保留原分块。"""
        run_id = self.task_run_ids.get(task_id, 0) if run_id is None else run_id
        current = lambda: self._can_finish(task_id, run_id)
        if not current():
            return
        directory = os.path.dirname(os.path.abspath(save_path))
        os.makedirs(directory, exist_ok=True)
        chunk_files = [chunk['temp_file'] for chunk in sorted(chunks, key=lambda x: x['chunk_index'])]

        # 合并要往目标目录先写一份完整副本，空间不够就停下保留分块（可恢复，不判死）
        task = self.db.get_task(task_id) or {}
        total_size = int(task.get('total_size') or 0)
        if total_size > 0:
            ok, detail = check_free_space([(directory, total_size, '下载目录')])
            if not ok:
                message = f'{detail}，已完成的分块已保留，腾出空间后可继续'
                logger.warning('合并前空间不足: task=%s, %s', task_id, detail)
                self.chunk_progress.pop(task_id, None)
                self.db.update_task_status(task_id, 'paused', message)
                if self.status_callback:
                    self.status_callback(task_id, 'paused', message)
                return

        # 随机且排他创建，不占用正式文件名，也不覆盖目录中已有的临时文件。
        with tempfile.NamedTemporaryFile(prefix=f'.daw-merge-{task_id}-', suffix='.tmp',
                                         dir=directory, delete=False) as output:
            staged = output.name
        try:
            if merge_chunks(chunk_files, staged, delete_chunks=False, is_current=current):
                self._verify_and_finish(task_id, staged, run_id=run_id)
                if current() and self._get_task_status(task_id) in ('completed', 'verify_failed'):
                    for path in chunk_files:
                        try:
                            os.remove(path)
                        except OSError as error:
                            logger.warning('已保存文件，但分块清理失败: %s', error)
            elif current():
                self.db.update_task_status(task_id, 'failed', '文件合并失败')
                if self.status_callback:
                    self.status_callback(task_id, 'failed', '合并失败')
        finally:
            # 未发布的合并副本可以重建，正常暂停/取消/失败后不占住额外空间。
            if os.path.exists(staged):
                try:
                    os.remove(staged)
                except OSError as error:
                    logger.warning('清理合并临时文件失败: %s', error)

    def _can_finish(self, task_id, run_id):
        task = self.db.get_task(task_id)
        return (self._is_current_task_run(task_id, run_id) and task is not None
                and task['status'] not in ('paused', 'cancelled') and task_id not in self.cancelled_tasks)

    def _verify_and_finish(self, task_id: str, save_path: str, run_id=None,
                           eof_confirmed: bool = False):
        """
        校验文件并完成任务
        老王说：校验这步很重要，下载了个假文件还不自知那才叫蠢！

        eof_confirmed：未知大小模式的 EOF 证明。仅普通 file、无浏览器上下文、
        非 Range、单线程且下载前为零哨兵的任务才允许跳过旧的大小比对，
        并把真实大小回填数据库；所有旧调用默认 False，保持原检查不变。
        """
        task = self.db.get_task(task_id)
        if not task:
            return

        run_id = self.task_run_ids.get(task_id, 0) if run_id is None else run_id
        if not self._can_finish(task_id, run_id):
            return

        if eof_confirmed:
            # 资格必须在共享校验函数里再次核对，不能只信调用方传来的布尔值。
            eligible = (task.get('download_type', 'file') == 'file'
                        and not task.get('browser_context')
                        and not task.get('support_range')
                        and int(task.get('thread_count') or 1) == 1
                        # 必须还是零哨兵：已知大小的任务拿这个标记也不能绕过原大小比对
                        and int(task.get('total_size') or 0) == 0)
            if eligible:
                try:
                    actual_size = os.path.getsize(save_path)
                except OSError as error:
                    self.db.update_task_status(task_id, 'failed', f'无法读取下载结果: {error}')
                    if self.status_callback:
                        self.status_callback(task_id, 'failed', '无法读取下载结果')
                    return
                # 回填真实大小；后续读取要拿新值，不能带旧字典里的零哨兵继续算。
                self.db.update_task_size(task_id, actual_size)
                task = self.db.get_task(task_id)
            else:
                # 不合格的 eof_confirmed 视为侵入，退回旧检查（旧分支对异常大小必失败）。
                eof_confirmed = False

        if not eof_confirmed and os.path.getsize(save_path) != task['total_size']:
            self.db.update_task_status(task_id, 'failed', '文件大小与预期不一致')
            if self.status_callback:
                self.status_callback(task_id, 'failed', '文件大小与预期不一致')
            return

        expected_hash = task.get('expected_hash')
        hash_type = task.get('expected_hash_type', 'md5') or 'md5'

        # 更新状态为verifying
        self.db.update_task_status(task_id, 'verifying')
        if self.status_callback:
            self.status_callback(task_id, 'verifying', '正在校验...')

        # 计算文件哈希
        logger.info("开始计算%s哈希...", hash_type.upper())
        actual_hash = calculate_file_hash(save_path, hash_type)
        if not self._can_finish(task_id, run_id):
            return

        # 普通文件在校验后才发布，排他创建防止探测后新出现的同名文件被覆盖。
        if os.path.normcase(os.path.abspath(save_path)) != os.path.normcase(os.path.abspath(task['save_path'])):
            publish_download(save_path, task['save_path'], lambda: self._can_finish(task_id, run_id))
            save_path = task['save_path']
        if not self._can_finish(task_id, run_id):
            return

        if actual_hash:
            logger.info("文件哈希: %s", actual_hash)
            # 有预期哈希值，进行对比
            if expected_hash:
                if actual_hash == expected_hash.lower():
                    # 校验通过
                    logger.info("校验通过")
                    self.db.update_task_hash(task_id, actual_hash, 1)
                    self._finish_task(task_id, save_path, 'completed', '下载完成，校验通过', run_id)
                else:
                    # 校验失败
                    logger.warning("校验失败: 期望 %s, 实际 %s", expected_hash, actual_hash)
                    self.db.update_task_hash(task_id, actual_hash, -1)
                    # 艹，verify_failed 也是终态，TaskManager会按终态释放并发槽位
                    self._finish_task(task_id, save_path, 'verify_failed',
                                      f'校验失败：期望{expected_hash[:8]}...，实际{actual_hash[:8]}...', run_id)
            else:
                # 没有预期哈希值，只记录实际哈希
                self.db.update_task_hash(task_id, actual_hash, 0)
                self._finish_task(task_id, save_path, 'completed', '下载完成', run_id)
        else:
            self.db.update_task_hash(task_id, '', -1 if expected_hash else 0)
            self._finish_task(task_id, save_path, 'verify_failed' if expected_hash else 'failed',
                              '文件哈希计算失败', run_id)

    def _finish_task(self, task_id: str, save_path: str, status: str, message: str, run_id=None):
        """完成任务的公共逻辑"""
        if run_id is not None and not self._can_finish(task_id, run_id):
            return
        if status != 'cancelled' and self._is_task_cancelled(task_id):
            # 艹，取消任务的状态是最高优先级，谁也别覆盖
            return

        task = self.db.get_task(task_id)
        if task and task.get('started_at'):
            try:
                started_at = datetime.fromisoformat(task['started_at']).replace(tzinfo=timezone.utc)
                elapsed_time = max(0, time.time() - started_at.timestamp())
                avg_speed = task['total_size'] / elapsed_time if elapsed_time > 0 else 0
                self.db.add_history(task_id, task['filename'], task['total_size'], elapsed_time, avg_speed)
            except Exception as e:
                logger.warning("添加历史记录失败: %s", e)

        if task and status in ('completed', 'verify_failed'):
            self.db.update_task_progress(task_id, task['total_size'], 0)
            if self.progress_callback:
                self.progress_callback(task_id, task['total_size'], task['total_size'], 0)
        # 终态任务不再需要内存进度汇总，监控线程靠这里退出
        self.chunk_progress.pop(task_id, None)
        self.db.update_task_status(task_id, status, message if status != 'completed' else None)
        if self.status_callback:
            self.status_callback(task_id, status, message)

    def _on_chunk_progress(self, chunk_id: int, downloaded_bytes: int):
        """分块进度回写数据库（调用方已按字节/时间节流）"""
        # 艹，这个回调只给真正存在分块记录的多线程任务用
        self.db.update_chunk_progress(chunk_id, downloaded_bytes)

    def _record_chunk_progress(self, task_id: str, chunk_id: int, downloaded_bytes: int):
        """先记内存再回写数据库：监控线程靠内存求和，不用每秒查分块表"""
        self.chunk_progress.setdefault(task_id, {})[chunk_id] = int(downloaded_bytes or 0)
        self._on_chunk_progress(chunk_id, downloaded_bytes)

    def _is_task_cancelled(self, task_id: str) -> bool:
        """判断任务是否已取消（并发状态保护）"""
        if task_id in self.cancelled_tasks:
            return True
        task = self.db.get_task(task_id)
        return bool(task and task.get('status') == 'cancelled')

    def _monitor_progress(self, task_id: str, total_size: int, start_time: float, run_id: int):
        """监控下载进度（后台线程）：只读内存聚合，不再每秒查一次分块表"""
        # 艹，恢复后的新会话不能继承旧监控器的last_downloaded，不然首秒速度会炸穿天花板
        last_downloaded = sum(self.chunk_progress.get(task_id, {}).values())

        while self._is_current_task_run(task_id, run_id):
            time.sleep(1)  # 每秒更新一次
            if not self._is_current_task_run(task_id, run_id):
                break

            # 任务已不在分块阶段（任务被删除/会话结束），安静退出
            sizes = self.chunk_progress.get(task_id)
            if sizes is None:
                break
            size_map = dict(sizes)
            downloaded_size = sum(size_map.values())

            # 计算速度
            speed = max(0, downloaded_size - last_downloaded)
            last_downloaded = downloaded_size

            # 更新数据库
            self.db.update_task_progress(task_id, downloaded_size, speed)

            # 调用进度回调
            if self.progress_callback:
                self.progress_callback(task_id, downloaded_size, total_size, speed)

            # 如果下载完成，退出
            if downloaded_size >= total_size:
                break

    def pause_download(self, task_id: str) -> bool:
        """暂停下载"""
        task = self.db.get_task(task_id)
        if not task:
            return False

        current_status = task.get('status')
        if current_status in ('completed', 'cancelled'):
            return False

        # 艹，分块任务暂停后走重建更稳；非分块任务保留活跃下载器，避免进度被重置到0
        if task.get('support_range') or task.get('download_type') in VIDEO_KINDS or current_status == 'verifying':
            # 艹，暂停时要等线程池收敛，避免紧接着恢复时旧线程还在抢写临时文件
            self._stop_active_task_workers(task_id, wait=True)
        elif task_id in self.active_downloaders:
            for downloader in self.active_downloaders[task_id]:
                downloader.pause()
        else:
            # 没有活跃下载器时仍允许标记为暂停（例如启动前切状态）
            self._next_task_run_id(task_id)

        self.db.update_task_status(task_id, 'paused')
        if self.status_callback:
            self.status_callback(task_id, 'paused', '已暂停')
        return True

    def resume_download(self, task_id: str) -> bool:
        """继续下载"""
        with self._starting_download(task_id, new_run=False) as run_id:
            if run_id is None:
                return False
            return self._resume_download(task_id, run_id)

    def _resume_download(self, task_id: str, run_id: int) -> bool:
        task = self.db.get_task(task_id)
        if not task:
            return False

        if task['status'] == 'paused':
            # 艹，非分块任务优先复用当前下载器继续，保证暂停后进度不归零
            if not task.get('support_range') and task_id in self.active_downloaders:
                with self._lifecycle:
                    if not self._is_current_task_run(task_id, run_id):
                        return False
                    self.db.update_task_status(task_id, 'downloading')
                    for downloader in self.active_downloaders[task_id]:
                        downloader.resume()
                    if self.status_callback:
                        self.status_callback(task_id, 'downloading', '继续下载')
                    return True

            # 分块任务或无活跃下载器，走重建续传
            self._stop_active_task_workers(task_id, wait=True)
            return self.start_download(task_id, resume=True)
        return False

    def cancel_download(self, task_id: str) -> bool:
        """取消下载"""
        task = self.db.get_task(task_id)
        if not task or task['status'] == 'completed':
            return False
        # 艹，先打取消标记并落库，后台汇总线程才能第一时间感知，防止回写failed
        self.cancelled_tasks.add(task_id)
        self.db.update_task_status(task_id, 'cancelled')
        if self.status_callback:
            self.status_callback(task_id, 'cancelled', '已取消')

        # 艹，取消后要确保线程真正停掉，不然后续删除文件会被句柄占用
        self._stop_active_task_workers(task_id, wait=True)
        return True

    def shutdown(self):
        """
        退出时清理资源

        老王说：不把线程池停干净，窗口关了进程还赖着不走，那真是祖宗十八代都要被骂！
        """
        self.begin_shutdown()
        with self._lifecycle:
            self._lifecycle.wait_for(lambda: not self._starting_tasks)
        # 艹，退出时必须等线程池收敛，确保不会把“旧线程回写”带到下次启动
        task_ids = set(self.active_downloaders.keys()) | set(self.thread_pools.keys())
        for task_id in list(task_ids):
            try:
                self._stop_active_task_workers(task_id, invalidate_run=True, wait=True)
            except Exception as e:
                logger.error("退出清理任务失败: task=%s, err=%s", task_id, e)

        self.active_downloaders.clear()
        self.thread_pools.clear()
        self.chunk_progress.clear()
