# -*- coding: utf-8 -*-
"""
下载引擎
老王说：这是整个下载器的大脑，得写得聪明点！
"""
import os
import re
import shutil
import uuid
import time
import requests
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from typing import Optional, Callable
from downloader.core.chunk_downloader import ChunkDownloader
from downloader.database.db_manager import DatabaseManager
from downloader.utils.config import ConfigManager
from downloader.utils.file_utils import merge_chunks, get_filename_from_url, ensure_dir, calculate_file_hash
from downloader.browser.security import browser_get, normalize_context, protect, unprotect
from downloader.core.youtube_downloader import YoutubeDownloader, video_page_url, require_tools


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

        # 回调函数
        self.progress_callback: Optional[Callable] = None
        self.status_callback: Optional[Callable] = None

    def _next_task_run_id(self, task_id: str) -> int:
        """生成任务新的下载会话ID"""
        next_run_id = int(self.task_run_ids.get(task_id, 0)) + 1
        self.task_run_ids[task_id] = next_run_id
        return next_run_id

    def _is_current_task_run(self, task_id: str, run_id: int) -> bool:
        """判断当前逻辑是否仍属于任务最新会话"""
        return int(self.task_run_ids.get(task_id, 0)) == int(run_id)

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
                print(f"[警告] 关闭线程池失败: task={task_id}, err={e}")

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

    def check_url_support_range(self, url: str, request_context=None) -> tuple[bool, int]:
        """
        检查URL是否支持Range请求（分块下载）
        Args:
            url: 下载链接
        Returns:
            (是否支持Range, 文件大小)
        """
        try:
            # 一次流式GET同时确定Range支持和真实大小，也兼容禁用HEAD的站点。
            headers = {'User-Agent': self.config.user_agent,
                       'Range': 'bytes=0-0', 'Accept-Encoding': 'identity'}
            get = requests.get if request_context is None else lambda url, **kw: browser_get(url, request_context, **kw)
            with get(url, headers=headers, stream=True,
                              timeout=self.config.timeout, allow_redirects=True,
                              proxies=self.config.proxies) as response:
                response.raise_for_status()
                if response.headers.get('Content-Encoding', 'identity').lower() != 'identity':
                    return False, 0
                if response.status_code == 206:
                    match = re.fullmatch(r'bytes 0-0/(\d+)', response.headers.get('Content-Range', ''))
                    if match and int(match[1]) > 0:
                        return True, int(match[1])
                    return False, 0
                if response.status_code == 200:
                    return False, max(0, int(response.headers.get('Content-Length', 0)))
                return False, 0
        except Exception as e:
            detail = type(e).__name__ if request_context else str(e)
            print(f"[错误] 检查URL失败: {detail}")
            return False, 0

    def create_download_task(self, url: str, filename: Optional[str] = None,
                            save_path: Optional[str] = None,
                            expected_hash: Optional[str] = None,
                            hash_type: str = "md5", request_context=None) -> Optional[str]:
        """
        创建下载任务
        Args:
            url: 下载链接
            filename: 文件名（可选，不提供则从URL提取）
            save_path: 保存路径（可选，不提供则使用默认下载目录）
            expected_hash: 预期哈希值（可选，用于下载后校验）
            hash_type: 哈希类型（md5/sha256）
        Returns:
            任务ID，失败返回None
        """
        request_context = normalize_context(url, request_context)
        # 生成任务ID
        task_id = str(uuid.uuid4())

        # 确定文件名
        if not filename:
            filename = get_filename_from_url(url)

        # 确定保存路径
        if not save_path:
            save_path = os.path.join(self.config.download_dir, filename)
        else:
            save_path = os.path.join(save_path, filename)

        # 检查URL支持情况
        support_range, total_size = (self.check_url_support_range(url, request_context)
                                    if request_context else self.check_url_support_range(url))

        if total_size == 0:
            if self.status_callback:
                self.status_callback(task_id, 'failed', '无法获取文件大小')
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
            browser_context=protect(request_context) if request_context else None
        )

        if not success:
            return None

        return task_id

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

    def create_youtube_task(self, url, filename, directory, height=720, kind='youtube'):
        url = video_page_url(url, kind)
        require_tools()
        if height not in (480, 720, 1080):
            raise ValueError('不支持的清晰度')
        if os.path.basename(filename) != filename or not filename.lower().endswith('.mp4'):
            raise ValueError('无效的视频文件名')
        task_id = str(uuid.uuid4())
        if self.db.create_task_with_chunks(task_id, url, filename, os.path.join(directory, filename),
                total_size=0, support_range=False, thread_count=1, download_type=kind, video_height=height):
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
        # 获取任务信息
        task = self.db.get_task(task_id)
        if not task:
            print(f"[错误] 任务不存在: {task_id}")
            return False

        # 艹，启动前先清掉历史残留活跃线程，避免重复下载器互相踩状态
        if task_id in self.active_downloaders or task_id in self.thread_pools:
            self._stop_active_task_workers(task_id, wait=True)

        # 每次启动都生成新的会话ID，后续所有回调/收尾都必须绑定它
        run_id = self._next_task_run_id(task_id)

        if task.get('download_type') in ('youtube', 'x'):
            return self._start_youtube_download(task, run_id)

        task['_request_context'] = unprotect(task['browser_context']) if task.get('browser_context') else None

        # 艹，下载启动前再做一次Range真探测，兜住历史任务和误判任务
        if task['support_range'] and task['thread_count'] > 1:
            context = task['_request_context']
            is_valid_range, _ = (self.check_url_support_range(task['url'], context)
                                 if context else self.check_url_support_range(task['url']))
            if not is_valid_range:
                self.db.mark_task_singlethread(task_id)
                task = self.db.get_task(task_id) or task
                task['_request_context'] = context

        # 更新任务状态为downloading
        self.cancelled_tasks.discard(task_id)
        self.db.update_task_status(task_id, 'downloading')
        if self.status_callback:
            self.status_callback(task_id, 'downloading', '开始下载')

        # 判断是否支持分块
        if task['support_range'] and task['thread_count'] > 1:
            return self._start_multithread_download(task, resume, run_id)
        else:
            return self._start_singlethread_download(task, resume, run_id)

    def _start_multithread_download(self, task: dict, resume: bool, run_id: int) -> bool:
        """多线程分块下载"""
        task_id = task['task_id']

        # 合并必须包含全部分块；下载器按临时文件大小跳过已完成的内容。
        chunks = self.db.get_chunks(task_id)

        if not chunks:
            print(f"[错误] 没有分块信息: {task_id}")
            self.db.update_task_status(task_id, 'failed', '没有分块信息')
            if self.status_callback:
                self.status_callback(task_id, 'failed', '没有分块信息')
            return False

        # 创建分块下载器
        downloaders = []
        # 设置中的限速针对整个任务，各分块共同分配额度。
        chunk_speed_limit = max(1, self.config.speed_limit // len(chunks)) if self.config.speed_limit else 0
        for chunk in chunks:
            downloader = ChunkDownloader(
                chunk_id=chunk['chunk_id'],
                task_id=task_id,
                url=task['url'],
                start_byte=chunk['start_byte'],
                end_byte=chunk['end_byte'],
                temp_file=chunk['temp_file'],
                timeout=self.config.timeout,
                retry_times=self.config.retry_times,
                user_agent=self.config.user_agent,
                speed_limit=chunk_speed_limit,
                proxies=self.config.proxies,  # 代理支持
                use_range=True,
                request_context=task.get('_request_context')
            )
            # 设置进度回调
            downloader.set_progress_callback(self._on_chunk_progress)
            downloaders.append(downloader)

        self.active_downloaders[task_id] = downloaders

        # 创建线程池
        thread_pool = ThreadPoolExecutor(max_workers=task['thread_count'])
        self.thread_pools[task_id] = thread_pool

        # 提交下载任务
        start_time = time.time()
        futures = {thread_pool.submit(d.download, resume): d for d in downloaders}

        # 启动进度监控线程
        import threading
        monitor_thread = threading.Thread(
            target=self._monitor_progress,
            args=(task_id, task['total_size'], start_time, run_id, downloaders),
            daemon=True
        )
        monitor_thread.start()

        # 等待所有分块完成（在后台线程中）
        def wait_and_merge():
            all_success = True
            try:
                for future in as_completed(futures):
                    # 艹，只处理当前会话；旧会话醒来后必须闭嘴，不准改新状态
                    if not self._is_current_task_run(task_id, run_id):
                        return
                    if self.active_downloaders.get(task_id) is not downloaders:
                        return

                    downloader = futures[future]
                    if self._is_task_cancelled(task_id) or self._is_task_paused(task_id):
                        # 艹，已经取消/暂停就别再碰状态了，后台线程老实收尾就行
                        continue
                    try:
                        success = future.result()
                        if self._is_task_cancelled(task_id) or self._is_task_paused(task_id):
                            continue
                        if success:
                            self.db.update_chunk_progress(downloader.chunk_id, downloader.downloaded_bytes, 'completed')
                        else:
                            all_success = False
                            self.db.update_chunk_progress(downloader.chunk_id, downloader.downloaded_bytes, 'failed')
                    except Exception as e:
                        print(f"[错误] 分块下载异常: {e}")
                        if not self._is_task_cancelled(task_id):
                            all_success = False
            finally:
                # 关闭线程池
                try:
                    thread_pool.shutdown(wait=False)
                except Exception:
                    pass

                # 艹，只能清理自己这次会话创建的对象，不能误删新会话资源
                if self.thread_pools.get(task_id) is thread_pool:
                    del self.thread_pools[task_id]
                if self.active_downloaders.get(task_id) is downloaders:
                    del self.active_downloaders[task_id]

            if not self._is_current_task_run(task_id, run_id):
                return

            if self._is_task_cancelled(task_id) or self._is_task_paused(task_id):
                return

            # 合并文件
            if all_success:
                self._merge_and_finish(task_id, task['save_path'], chunks)
            else:
                # 艹，分块模式翻车别急着判死刑，先自动降级单线程再拼一次
                if self._fallback_to_singlethread(task, run_id):
                    return
                self.db.update_task_status(task_id, 'failed', '部分分块下载失败')
                if self.status_callback:
                    self.status_callback(task_id, 'failed', '下载失败')

        # 在后台线程中等待
        threading.Thread(target=wait_and_merge, daemon=True).start()
        return True

    def _fallback_to_singlethread(self, task: dict, run_id: int) -> bool:
        """分块失败时自动降级到单线程重试"""
        task_id = task['task_id']

        if not self._is_current_task_run(task_id, run_id):
            return False

        try:
            print(f"[警告] 检测到分块下载失败，自动降级单线程重试: {task_id}")

            # 清理分块临时文件并重置分块状态，避免脏块影响后续重试
            chunks = self.db.get_chunks(task_id)
            for chunk in chunks:
                temp_file = chunk.get('temp_file')
                if temp_file and os.path.exists(temp_file):
                    try:
                        os.remove(temp_file)
                    except Exception as e:
                        print(f"[警告] 删除分块临时文件失败: {temp_file}, {e}")
                self.db.update_chunk_progress(chunk['chunk_id'], 0, 'pending')

            # 切换任务为单线程模式，并把任务进度归零后重新开始
            self.db.mark_task_singlethread(task_id)
            self.db.update_task_progress(task_id, 0, 0)

            single_task = self.db.get_task(task_id)
            if not single_task:
                return False

            if not self._is_current_task_run(task_id, run_id):
                return False

            if self.status_callback:
                self.status_callback(task_id, 'downloading', '分块失败，已自动降级为单线程重试')

            return self._start_singlethread_download(single_task, resume=False, run_id=run_id)
        except Exception as e:
            print(f"[错误] 自动降级单线程失败: {e}")
            return False

    def _start_singlethread_download(self, task: dict, resume: bool, run_id: int) -> bool:
        """单线程下载（不支持分块的情况）"""
        task_id = task['task_id']
        temp_file = os.path.join(self.config.temp_dir, f"{task_id}.tmp")

        # 艹，非Range服务端根本不支持断点续传，恢复必须从0重下，不能继续append脏数据
        can_resume = bool(resume and task.get('support_range'))
        # 艹，这里不提前删临时文件，避免旧线程尚未释放句柄导致WinError 32；
        # 真正重置在ChunkDownloader内部按非Range规则以wb模式执行。

        # 创建单线程下载器
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
            speed_limit=self.config.speed_limit,
            proxies=self.config.proxies,  # 代理支持
            use_range=bool(task.get('support_range')),
            request_context=(task.get('_request_context') or
                             (unprotect(task['browser_context']) if task.get('browser_context') else None))
        )

        # 艹，单线程也得走实时进度链路，不然UI速度和进度全是死的
        progress_state = {
            'last_time': time.time(),
            'last_downloaded': 0,
            'smoothed_speed': float(task.get('speed') or 0)
        }

        if can_resume and os.path.exists(temp_file):
            progress_state['last_downloaded'] = os.path.getsize(temp_file)

        # 先把当前已下载量推给UI，断点续传时进度不会从0假装重新开始
        self.db.update_task_progress(task_id, progress_state['last_downloaded'], 0)
        if self.progress_callback:
            self.progress_callback(task_id, progress_state['last_downloaded'], task['total_size'], 0)

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
                    raise RuntimeError('下载失败')
                save_dir = os.path.dirname(task['save_path'])
                if save_dir:
                    ensure_dir(save_dir)
                # 临时目录和下载目录可能分属不同磁盘。
                shutil.move(temp_file, task['save_path'])
                self._verify_and_finish(task_id, task['save_path'])
            except Exception as e:
                if (self._is_current_task_run(task_id, run_id)
                        and not self._is_task_cancelled(task_id) and not self._is_task_paused(task_id)):
                    self.db.update_task_status(task_id, 'failed', str(e))
                    if self.status_callback:
                        self.status_callback(task_id, 'failed', str(e))
            finally:
                if self.active_downloaders.get(task_id) is downloaders_ref:
                    del self.active_downloaders[task_id]
                if self.thread_pools.get(task_id) is thread_pool:
                    del self.thread_pools[task_id]
                thread_pool.shutdown(wait=False)

        thread_pool.submit(download_and_finish)
        return True

    def _merge_and_finish(self, task_id: str, save_path: str, chunks: list):
        """合并文件并完成任务"""
        if self._is_task_cancelled(task_id):
            # 艹，任务都取消了还合并个锤子，直接撤
            return

        print(f"[合并] 开始合并文件，目标路径: {save_path}")

        # 获取所有分块文件
        chunk_files = [chunk['temp_file'] for chunk in sorted(chunks, key=lambda x: x['chunk_index'])]
        print(f"[合并] 分块文件数: {len(chunk_files)}")

        # 合并文件
        if merge_chunks(chunk_files, save_path, delete_chunks=True):
            print(f"[成功] 文件已保存到: {save_path}")
            print(f"[检查] 文件是否存在: {os.path.exists(save_path)}")

            # 校验并完成任务
            self._verify_and_finish(task_id, save_path)
        else:
            print(f"[错误] 文件合并失败！")
            if self._is_task_cancelled(task_id):
                return
            self.db.update_task_status(task_id, 'failed', '文件合并失败')
            if self.status_callback:
                self.status_callback(task_id, 'failed', '合并失败')

    def _verify_and_finish(self, task_id: str, save_path: str, run_id=None):
        """
        校验文件并完成任务
        老王说：校验这步很重要，下载了个假文件还不自知那才叫蠢！
        """
        task = self.db.get_task(task_id)
        if not task:
            return

        if self._is_task_cancelled(task_id):
            # 艹，取消后禁止进入校验/终态覆盖流程
            return

        if os.path.getsize(save_path) != task['total_size']:
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
        print(f"[校验] 开始计算{hash_type.upper()}哈希...")
        actual_hash = calculate_file_hash(save_path, hash_type)
        if run_id is not None and not self._is_current_task_run(task_id, run_id):
            return

        if actual_hash:
            print(f"[校验] 文件哈希: {actual_hash}")
            # 有预期哈希值，进行对比
            if expected_hash:
                if actual_hash == expected_hash.lower():
                    # 校验通过
                    print(f"[校验] 校验通过！")
                    self.db.update_task_hash(task_id, actual_hash, 1)
                    self._finish_task(task_id, save_path, 'completed', '下载完成，校验通过')
                else:
                    # 校验失败
                    print(f"[校验] 校验失败！期望:{expected_hash}, 实际:{actual_hash}")
                    self.db.update_task_hash(task_id, actual_hash, -1)
                    # 艹，verify_failed 也是终态，TaskManager会按终态释放并发槽位
                    self._finish_task(task_id, save_path, 'verify_failed',
                                      f'校验失败：期望{expected_hash[:8]}...，实际{actual_hash[:8]}...')
            else:
                # 没有预期哈希值，只记录实际哈希
                self.db.update_task_hash(task_id, actual_hash, 0)
                self._finish_task(task_id, save_path, 'completed', '下载完成')
        else:
            self.db.update_task_hash(task_id, '', -1 if expected_hash else 0)
            self._finish_task(task_id, save_path, 'verify_failed' if expected_hash else 'failed',
                              '文件哈希计算失败')

    def _finish_task(self, task_id: str, save_path: str, status: str, message: str):
        """完成任务的公共逻辑"""
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
                print(f"[警告] 添加历史记录失败: {e}")

        if task and status in ('completed', 'verify_failed'):
            self.db.update_task_progress(task_id, task['total_size'], 0)
            if self.progress_callback:
                self.progress_callback(task_id, task['total_size'], task['total_size'], 0)
        self.db.update_task_status(task_id, status, message if status != 'completed' else None)
        if self.status_callback:
            self.status_callback(task_id, status, message)

    def _on_chunk_progress(self, chunk_id: int, downloaded_bytes: int):
        """分块进度回调"""
        # 艹，这个回调只给真正存在分块记录的多线程任务用
        self.db.update_chunk_progress(chunk_id, downloaded_bytes)

    def _is_task_cancelled(self, task_id: str) -> bool:
        """判断任务是否已取消（并发状态保护）"""
        if task_id in self.cancelled_tasks:
            return True
        task = self.db.get_task(task_id)
        return bool(task and task.get('status') == 'cancelled')

    def _monitor_progress(self, task_id: str, total_size: int, start_time: float, run_id: int, downloaders_ref: list):
        """监控下载进度（在后台线程中运行）"""
        # 艹，恢复后的新会话不能继承旧监控器的last_downloaded，不然首秒速度会炸穿天花板
        chunks = self.db.get_chunks(task_id)
        last_downloaded = sum(chunk['downloaded_bytes'] for chunk in chunks)

        while self._is_current_task_run(task_id, run_id) and self.active_downloaders.get(task_id) is downloaders_ref:
            time.sleep(1)  # 每秒更新一次
            if (not self._is_current_task_run(task_id, run_id)
                    or self.active_downloaders.get(task_id) is not downloaders_ref):
                break

            # 计算总下载量
            chunks = self.db.get_chunks(task_id)
            downloaded_size = sum(chunk['downloaded_bytes'] for chunk in chunks)

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
        if task.get('support_range') or task.get('download_type') in ('youtube', 'x'):
            # 艹，暂停时要等线程池收敛，避免紧接着恢复时旧线程还在抢写临时文件
            self._stop_active_task_workers(task_id, wait=True)
        elif task_id in self.active_downloaders:
            for downloader in self.active_downloaders[task_id]:
                downloader.pause()
        else:
            # 没有活跃下载器时仍允许标记为暂停（例如启动前切状态）
            pass

        self.db.update_task_status(task_id, 'paused')
        if self.status_callback:
            self.status_callback(task_id, 'paused', '已暂停')
        return True

    def resume_download(self, task_id: str) -> bool:
        """继续下载"""
        task = self.db.get_task(task_id)
        if not task:
            return False

        if task['status'] == 'paused':
            # 艹，非分块任务优先复用当前下载器继续，保证暂停后进度不归零
            if not task.get('support_range') and task_id in self.active_downloaders:
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
        # 艹，退出时必须等线程池收敛，确保不会把“旧线程回写”带到下次启动
        task_ids = set(self.active_downloaders.keys()) | set(self.thread_pools.keys())
        for task_id in list(task_ids):
            try:
                self._stop_active_task_workers(task_id, invalidate_run=True, wait=True)
            except Exception as e:
                print(f"[错误] 退出清理任务失败: task={task_id}, err={e}")

        self.active_downloaders.clear()
        self.thread_pools.clear()
