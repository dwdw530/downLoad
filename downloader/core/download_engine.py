# -*- coding: utf-8 -*-
"""
下载引擎
老王说：这是整个下载器的大脑，得写得聪明点！
"""
import os
import uuid
import time
import requests
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Optional, Callable
from downloader.core.chunk_downloader import ChunkDownloader
from downloader.database.db_manager import DatabaseManager
from downloader.utils.config import ConfigManager
from downloader.utils.file_utils import merge_chunks, get_filename_from_url, ensure_dir


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

        # 回调函数
        self.progress_callback: Optional[Callable] = None
        self.status_callback: Optional[Callable] = None

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

    def check_url_support_range(self, url: str) -> tuple[bool, int]:
        """
        检查URL是否支持Range请求（分块下载）
        Args:
            url: 下载链接
        Returns:
            (是否支持Range, 文件大小)
        """
        try:
            headers = {'User-Agent': self.config.user_agent}
            response = requests.head(url, headers=headers, timeout=self.config.timeout, allow_redirects=True)

            # 获取文件大小
            total_size = int(response.headers.get('Content-Length', 0))

            # 检查是否支持Range
            accept_ranges = response.headers.get('Accept-Ranges', '')
            support_range = accept_ranges == 'bytes'

            return support_range, total_size
        except Exception as e:
            print(f"[错误] 检查URL失败: {e}")
            return False, 0

    def create_download_task(self, url: str, filename: Optional[str] = None,
                            save_path: Optional[str] = None) -> Optional[str]:
        """
        创建下载任务
        Args:
            url: 下载链接
            filename: 文件名（可选，不提供则从URL提取）
            save_path: 保存路径（可选，不提供则使用默认下载目录）
        Returns:
            任务ID，失败返回None
        """
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
        support_range, total_size = self.check_url_support_range(url)

        if total_size == 0:
            if self.status_callback:
                self.status_callback(task_id, 'failed', '无法获取文件大小')
            return None

        # 确定线程数
        thread_count = self.config.thread_count if support_range else 1

        # 创建任务记录
        success = self.db.create_task(
            task_id=task_id,
            url=url,
            filename=filename,
            save_path=save_path,
            total_size=total_size,
            support_range=support_range,
            thread_count=thread_count
        )

        if not success:
            return None

        # 如果支持分块，创建分块记录
        if support_range and thread_count > 1:
            self._create_chunks(task_id, url, total_size, thread_count)

        return task_id

    def _create_chunks(self, task_id: str, url: str, total_size: int, thread_count: int):
        """
        创建分块记录
        Args:
            task_id: 任务ID
            url: 下载链接
            total_size: 文件总大小
            thread_count: 线程数
        """
        chunk_size = total_size // thread_count
        chunks = []

        for i in range(thread_count):
            start_byte = i * chunk_size
            end_byte = (i + 1) * chunk_size - 1 if i < thread_count - 1 else total_size - 1
            temp_file = os.path.join(self.config.temp_dir, f"{task_id}.part{i}")
            chunks.append((i, start_byte, end_byte, temp_file))

        self.db.create_chunks(task_id, chunks)

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

        # 更新任务状态为downloading
        self.db.update_task_status(task_id, 'downloading')
        if self.status_callback:
            self.status_callback(task_id, 'downloading', '开始下载')

        # 判断是否支持分块
        if task['support_range'] and task['thread_count'] > 1:
            return self._start_multithread_download(task, resume)
        else:
            return self._start_singlethread_download(task, resume)

    def _start_multithread_download(self, task: dict, resume: bool) -> bool:
        """多线程分块下载"""
        task_id = task['task_id']

        # 获取分块信息
        if resume:
            chunks = self.db.get_incomplete_chunks(task_id)
        else:
            chunks = self.db.get_chunks(task_id)

        if not chunks:
            print(f"[错误] 没有分块信息: {task_id}")
            return False

        # 创建分块下载器
        downloaders = []
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
                user_agent=self.config.user_agent
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
            args=(task_id, task['total_size'], start_time),
            daemon=True
        )
        monitor_thread.start()

        # 等待所有分块完成（在后台线程中）
        def wait_and_merge():
            all_success = True
            for future in as_completed(futures):
                downloader = futures[future]
                try:
                    success = future.result()
                    if success:
                        self.db.update_chunk_progress(downloader.chunk_id, downloader.downloaded_bytes, 'completed')
                    else:
                        all_success = False
                        self.db.update_chunk_progress(downloader.chunk_id, downloader.downloaded_bytes, 'failed')
                except Exception as e:
                    print(f"[错误] 分块下载异常: {e}")
                    all_success = False

            # 关闭线程池
            thread_pool.shutdown(wait=False)
            if task_id in self.thread_pools:
                del self.thread_pools[task_id]
            if task_id in self.active_downloaders:
                del self.active_downloaders[task_id]

            # 合并文件
            if all_success:
                self._merge_and_finish(task_id, task['save_path'], chunks)
            else:
                self.db.update_task_status(task_id, 'failed', '部分分块下载失败')
                if self.status_callback:
                    self.status_callback(task_id, 'failed', '下载失败')

        # 在后台线程中等待
        threading.Thread(target=wait_and_merge, daemon=True).start()
        return True

    def _start_singlethread_download(self, task: dict, resume: bool) -> bool:
        """单线程下载（不支持分块的情况）"""
        task_id = task['task_id']
        temp_file = os.path.join(self.config.temp_dir, f"{task_id}.tmp")

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
            user_agent=self.config.user_agent
        )
        downloader.set_progress_callback(self._on_chunk_progress)
        self.active_downloaders[task_id] = [downloader]

        # 在后台线程下载
        def download_and_finish():
            start_time = time.time()
            success = downloader.download(resume)

            if task_id in self.active_downloaders:
                del self.active_downloaders[task_id]

            if success:
                # 移动文件到目标位置
                ensure_dir(os.path.dirname(task['save_path']))
                os.replace(temp_file, task['save_path'])

                # 更新任务状态
                elapsed_time = time.time() - start_time
                avg_speed = task['total_size'] / elapsed_time if elapsed_time > 0 else 0
                self.db.update_task_status(task_id, 'completed')
                self.db.add_history(task_id, task['filename'], task['total_size'], elapsed_time, avg_speed)

                if self.status_callback:
                    self.status_callback(task_id, 'completed', '下载完成')
            else:
                self.db.update_task_status(task_id, 'failed', '下载失败')
                if self.status_callback:
                    self.status_callback(task_id, 'failed', '下载失败')

        import threading
        threading.Thread(target=download_and_finish, daemon=True).start()
        return True

    def _merge_and_finish(self, task_id: str, save_path: str, chunks: list):
        """合并文件并完成任务"""
        print(f"[合并] 开始合并文件，目标路径: {save_path}")

        # 确保目标目录存在
        from downloader.utils.file_utils import ensure_dir
        save_dir = os.path.dirname(save_path)
        if save_dir:
            ensure_dir(save_dir)
            print(f"[合并] 目标目录: {save_dir}")

        # 获取所有分块文件
        chunk_files = [chunk['temp_file'] for chunk in sorted(chunks, key=lambda x: x['chunk_index'])]
        print(f"[合并] 分块文件数: {len(chunk_files)}")

        # 合并文件
        if merge_chunks(chunk_files, save_path, delete_chunks=True):
            print(f"[成功] 文件已保存到: {save_path}")
            print(f"[检查] 文件是否存在: {os.path.exists(save_path)}")

            # 获取任务信息计算统计数据
            task = self.db.get_task(task_id)
            if task:
                elapsed_time = time.time() - time.mktime(time.strptime(task['started_at'], '%Y-%m-%d %H:%M:%S'))
                avg_speed = task['total_size'] / elapsed_time if elapsed_time > 0 else 0
                self.db.add_history(task_id, task['filename'], task['total_size'], elapsed_time, avg_speed)

            self.db.update_task_status(task_id, 'completed')
            if self.status_callback:
                self.status_callback(task_id, 'completed', '下载完成')
        else:
            print(f"[错误] 文件合并失败！")
            self.db.update_task_status(task_id, 'failed', '文件合并失败')
            if self.status_callback:
                self.status_callback(task_id, 'failed', '合并失败')

    def _on_chunk_progress(self, chunk_id: int, downloaded_bytes: int):
        """分块进度回调"""
        self.db.update_chunk_progress(chunk_id, downloaded_bytes)

    def _monitor_progress(self, task_id: str, total_size: int, start_time: float):
        """监控下载进度（在后台线程中运行）"""
        last_downloaded = 0
        while task_id in self.active_downloaders:
            time.sleep(1)  # 每秒更新一次

            # 计算总下载量
            chunks = self.db.get_chunks(task_id)
            downloaded_size = sum(chunk['downloaded_bytes'] for chunk in chunks)

            # 计算速度
            speed = downloaded_size - last_downloaded
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
        if task_id in self.active_downloaders:
            for downloader in self.active_downloaders[task_id]:
                downloader.pause()
            self.db.update_task_status(task_id, 'paused')
            if self.status_callback:
                self.status_callback(task_id, 'paused', '已暂停')
            return True
        return False

    def resume_download(self, task_id: str) -> bool:
        """继续下载"""
        task = self.db.get_task(task_id)
        if not task:
            return False

        if task['status'] == 'paused':
            # 如果正在暂停中，恢复
            if task_id in self.active_downloaders:
                for downloader in self.active_downloaders[task_id]:
                    downloader.resume()
                self.db.update_task_status(task_id, 'downloading')
                if self.status_callback:
                    self.status_callback(task_id, 'downloading', '继续下载')
                return True
            else:
                # 重新启动（断点续传）
                return self.start_download(task_id, resume=True)
        return False

    def cancel_download(self, task_id: str) -> bool:
        """取消下载"""
        if task_id in self.active_downloaders:
            for downloader in self.active_downloaders[task_id]:
                downloader.cancel()

            # 关闭线程池
            if task_id in self.thread_pools:
                self.thread_pools[task_id].shutdown(wait=False)
                del self.thread_pools[task_id]

            del self.active_downloaders[task_id]

        self.db.update_task_status(task_id, 'cancelled')
        if self.status_callback:
            self.status_callback(task_id, 'cancelled', '已取消')
        return True

    def shutdown(self):
        """
        退出时清理资源

        老王说：不把线程池停干净，窗口关了进程还赖着不走，那真是祖宗十八代都要被骂！
        """
        # 先把所有下载器都打上取消标志，尽量让下载线程自己滚蛋
        for task_id, downloaders in list(self.active_downloaders.items()):
            for downloader in downloaders:
                try:
                    downloader.cancel()
                except Exception as e:
                    print(f"[错误] 取消分块失败: task={task_id}, err={e}")

        # 再把线程池关掉（cancel_futures=True 能把还没开始的任务直接掐掉）
        for task_id, thread_pool in list(self.thread_pools.items()):
            try:
                thread_pool.shutdown(wait=False, cancel_futures=True)
            except TypeError:
                # 兼容老版本Python（没有cancel_futures参数）
                thread_pool.shutdown(wait=False)
            except Exception as e:
                print(f"[错误] 关闭线程池失败: task={task_id}, err={e}")

        self.active_downloaders.clear()
        self.thread_pools.clear()
