# -*- coding: utf-8 -*-
"""
任务管理器
老王说：队列管理得井井有条，不然乱套了！
"""
import threading
import os
from typing import List, Dict, Optional, Callable
from downloader.core.download_engine import DownloadEngine
from downloader.database.db_manager import DatabaseManager


class TaskManager:
    """任务队列管理器"""

    def __init__(self, engine: DownloadEngine, db_manager: DatabaseManager, max_concurrent: int = 3):
        """
        初始化任务管理器
        Args:
            engine: 下载引擎
            db_manager: 数据库管理器
            max_concurrent: 最大并发下载数
        """
        self.engine = engine
        self.db = db_manager
        self.max_concurrent = max_concurrent

        self._lock = threading.Lock()
        self._running_tasks = set()  # 正在下载的任务ID集合
        self._scheduling_paused = False

        # 回调函数
        self.task_added_callback: Optional[Callable] = None
        self.task_status_changed_callback: Optional[Callable] = None

        # 艹，程序重启后必须先把遗留downloading纠偏成paused，不然僵尸任务会把队列拖死
        self._recover_stale_downloading_tasks()
        # 艹，重启后必须按临时文件真实字节回填进度，不然UI会假装0%吓用户
        self._reconcile_incomplete_tasks_progress()

        # 设置引擎的状态回调
        self.engine.set_status_callback(self._on_engine_status_change)

    def _recover_stale_downloading_tasks(self):
        """启动时恢复遗留下载中任务"""
        stale_tasks = [task for task in self.db.get_all_tasks()
                       if task['status'] in ('downloading', 'verifying')]
        for task in stale_tasks:
            self.db.update_task_status(task['task_id'], 'paused', '程序重启自动纠偏：下载状态已重置为暂停')

    def _reconcile_incomplete_tasks_progress(self):
        """启动时根据临时文件修正未完成任务进度"""
        tasks = self.db.get_all_tasks()

        for task in tasks:
            if task.get('download_type') in ('youtube', 'x', 'bilibili', 'hls', 'dash'):
                continue
            status = task.get('status')
            # 终态不碰，避免误改历史结果
            if status in ('completed', 'verifying', 'verify_failed'):
                continue

            task_id = task['task_id']
            total_size = int(task.get('total_size') or 0)

            # 分块任务：以每个分块临时文件实际大小为准
            if task.get('support_range') and task.get('thread_count', 1) > 1:
                chunks = self.db.get_chunks(task_id)
                if not chunks:
                    continue

                total_downloaded = 0
                for chunk in chunks:
                    actual_bytes = 0
                    temp_file = chunk.get('temp_file')
                    if temp_file and os.path.exists(temp_file):
                        try:
                            actual_bytes = os.path.getsize(temp_file)
                        except Exception:
                            actual_bytes = 0

                    # 分块大小上限保护，防止脏数据污染总进度
                    chunk_max = max(0, int(chunk.get('end_byte', 0)) - int(chunk.get('start_byte', 0)) + 1)
                    if chunk_max > 0:
                        actual_bytes = min(actual_bytes, chunk_max)

                    total_downloaded += max(0, actual_bytes)

                    if int(chunk.get('downloaded_bytes') or 0) != actual_bytes:
                        self.db.update_chunk_progress(chunk['chunk_id'], actual_bytes)

                if total_size > 0:
                    total_downloaded = min(total_downloaded, total_size)

                if int(task.get('downloaded_size') or 0) != total_downloaded:
                    self.db.update_task_progress(task_id, total_downloaded, 0)

            else:
                # 单线程任务：按 .tmp 文件真实大小回填
                temp_file = os.path.join(self.engine.config.temp_dir, f"{task_id}.tmp")
                actual_bytes = 0
                if os.path.exists(temp_file):
                    try:
                        actual_bytes = os.path.getsize(temp_file)
                    except Exception:
                        actual_bytes = 0

                if total_size > 0:
                    actual_bytes = min(actual_bytes, total_size)

                if int(task.get('downloaded_size') or 0) != actual_bytes:
                    self.db.update_task_progress(task_id, actual_bytes, 0)

    def set_task_added_callback(self, callback: Callable):
        """设置任务添加回调"""
        self.task_added_callback = callback

    def set_task_status_changed_callback(self, callback: Callable):
        """设置任务状态变更回调"""
        self.task_status_changed_callback = callback

    def add_task(self, url: str, filename: Optional[str] = None,
                 save_path: Optional[str] = None,
                 expected_hash: Optional[str] = None,
                 hash_type: str = "md5", request_context=None) -> Optional[str]:
        """
        添加下载任务
        Args:
            url: 下载链接
            filename: 文件名（可选）
            save_path: 保存路径（可选）
            expected_hash: 预期哈希值（可选，用于下载后校验）
            hash_type: 哈希类型（md5/sha256）
        Returns:
            任务ID，失败返回None
        """
        # 创建任务
        kwargs = {'request_context': request_context} if request_context is not None else {}
        task_id = self.engine.create_download_task(url, filename, save_path, expected_hash, hash_type, **kwargs)
        if not task_id:
            return None

        # 调用任务添加回调
        if self.task_added_callback:
            self.task_added_callback(task_id)

        # 尝试启动任务
        self._try_start_next_task()

        return task_id

    def add_youtube_task(self, url, filename, save_path, height=720, kind='youtube', request_context=None):
        task_id = self.engine.create_youtube_task(url, filename, save_path, height, kind,
                                               request_context=request_context)
        if task_id:
            if self.task_added_callback:
                self.task_added_callback(task_id)
            self._try_start_next_task()
        return task_id

    def start_task(self, task_id: str) -> bool:
        """
        手动启动任务
        Args:
            task_id: 任务ID
        Returns:
            True表示成功，False表示失败
        """
        task = self.db.get_task(task_id)
        if not task:
            return False

        # 检查任务状态
        if task['status'] not in ('pending', 'paused', 'failed', 'verify_failed', 'cancelled'):
            return False

        # 校验失败重试要先把进度清零，不然UI会出现“没开始就100%”这种离谱显示
        if task['status'] == 'verify_failed':
            self.db.update_task_progress(task_id, 0, 0)
            if task.get('support_range') and task.get('thread_count', 1) > 1:
                chunks = self.db.get_chunks(task_id)
                for chunk in chunks:
                    self.db.update_chunk_progress(chunk['chunk_id'], 0, 'pending')

        # 检查并发限制
        with self._lock:
            if self._scheduling_paused or task_id in self._running_tasks:
                return False
            if len(self._running_tasks) >= self.max_concurrent:
                print(f"[提示] 已达到最大并发数，任务将等待: {task_id}")
                return False

            self._running_tasks.add(task_id)

        # 启动下载
        # 艹，pending 也统一按“可续传启动”处理：新任务没临时文件会从0开始，已下载任务可继续
        resume = task['status'] in ('pending', 'paused', 'failed', 'cancelled')
        try:
            success = self.engine.start_download(task_id, resume=resume)
        except Exception as e:
            self.db.update_task_status(task_id, 'failed', str(e))
            self._on_engine_status_change(task_id, 'failed', str(e))
            success = False

        if not success:
            with self._lock:
                self._running_tasks.discard(task_id)

        return success

    def pause_task(self, task_id: str) -> bool:
        """暂停任务"""
        success = self.engine.pause_download(task_id)
        if success:
            # 艹，暂停任务不能继续霸占并发槽位，不然队列永远起不来
            with self._lock:
                self._running_tasks.discard(task_id)
            self._try_start_next_task()
        return success

    def resume_task(self, task_id: str) -> bool:
        """继续任务"""
        task = self.db.get_task(task_id)
        if not task or task['status'] != 'paused':
            return False

        with self._lock:
            if self._scheduling_paused or task_id in self._running_tasks:
                return False
            # 艹，恢复任务也得走并发门禁，不能偷偷绕过最大并发
            if task_id not in self._running_tasks and len(self._running_tasks) >= self.max_concurrent:
                print(f"[提示] 已达到最大并发数，任务转入等待队列: {task_id}")
                self.db.update_task_status(task_id, 'pending', '等待可用下载槽位')
                if self.task_status_changed_callback:
                    self.task_status_changed_callback(task_id, 'pending', '等待可用下载槽位')
                return True
            self._running_tasks.add(task_id)

        success = self.engine.resume_download(task_id)
        if not success:
            with self._lock:
                self._running_tasks.discard(task_id)

        return success

    def cancel_task(self, task_id: str) -> bool:
        """取消任务"""
        success = self.engine.cancel_download(task_id)
        if success:
            with self._lock:
                self._running_tasks.discard(task_id)
            self._try_start_next_task()
        return success

    def delete_task(self, task_id: str) -> bool:
        """
        删除任务
        Args:
            task_id: 任务ID
        Returns:
            True表示成功，False表示失败
        """
        # 先取消下载
        self.cancel_task(task_id)

        # 删除数据库记录
        return self.db.delete_task(task_id)

    def get_task(self, task_id: str) -> Optional[Dict]:
        """获取任务详情"""
        return self.db.get_task(task_id)

    def get_all_tasks(self) -> List[Dict]:
        """获取所有任务"""
        return self.db.get_all_tasks()

    def get_downloading_tasks(self) -> List[Dict]:
        """获取正在下载的任务"""
        return self.db.get_all_tasks(status='downloading')

    def get_pending_tasks(self) -> List[Dict]:
        """获取等待中的任务"""
        return self.db.get_all_tasks(status='pending')

    def pause_all(self) -> int:
        """
        暂停下载中及等待中的任务，批量操作期间停止自动调度。
        Returns:
            暂停的任务数量
        """
        with self._lock:
            previous_scheduling_paused = self._scheduling_paused
            self._scheduling_paused = True
            runtime_task_ids = set(self._running_tasks)

        try:
            tasks = self.db.get_all_tasks()
            count = 0
            for task in tasks:
                if task['status'] in ('downloading', 'pending') or task['task_id'] in runtime_task_ids:
                    if self.pause_task(task['task_id']):
                        count += 1
            return count
        finally:
            with self._lock:
                self._scheduling_paused = previous_scheduling_paused

    def resume_all(self) -> int:
        """
        继续所有暂停的任务
        Returns:
            继续的任务数量
        """
        paused_tasks = self.db.get_all_tasks(status='paused')
        count = 0

        # 艹，多任务恢复要严格按FIFO来，避免新任务抢占老任务导致“看起来没恢复”
        paused_tasks = sorted(paused_tasks, key=lambda task: task.get('created_at') or '')

        for task in paused_tasks:
            if self.resume_task(task['task_id']):
                count += 1
        return count

    def _try_start_next_task(self):
        """尝试启动下一个等待中的任务"""
        while True:
            with self._lock:
                if self._scheduling_paused or len(self._running_tasks) >= self.max_concurrent:
                    return
            pending_tasks = self.get_pending_tasks()
            if not pending_tasks or not self.start_task(pending_tasks[0]['task_id']):
                return

    def _on_engine_status_change(self, task_id: str, status: str, message: str):
        """引擎状态变更回调"""
        # 如果任务完成或失败，从运行集合中移除
        if status in ('completed', 'failed', 'cancelled', 'verify_failed', 'paused'):
            # 艹，verify_failed和paused都算终态/非运行态，必须立刻释放槽位
            with self._lock:
                self._running_tasks.discard(task_id)

            # 尝试启动下一个任务
            self._try_start_next_task()

        # 调用外部回调
        if self.task_status_changed_callback:
            self.task_status_changed_callback(task_id, status, message)

    def set_max_concurrent(self, max_concurrent: int):
        """
        设置最大并发数
        Args:
            max_concurrent: 最大并发数（1-5）
        """
        self.max_concurrent = max(1, min(5, max_concurrent))
        # 尝试启动等待中的任务
        self._try_start_next_task()

    def get_statistics(self) -> Dict:
        """
        获取统计信息
        Returns:
            {
                'total': 总任务数,
                'downloading': 下载中,
                'pending': 等待中,
                'paused': 已暂停,
                'completed': 已完成,
                'failed': 失败
            }
        """
        all_tasks = self.get_all_tasks()
        stats = {
            'total': len(all_tasks),
            'downloading': 0,
            'pending': 0,
            'paused': 0,
            'completed': 0,
            'failed': 0,
            'cancelled': 0
        }

        for task in all_tasks:
            status = task['status']
            if status in stats:
                stats[status] += 1

        return stats

    def shutdown(self):
        """
        程序退出清理

        老王说：点了退出就得真退出，别让线程池把进程吊着不放，恶心！
        """
        with self._lock:
            self._scheduling_paused = True
        # 艹，退出时必须“自动暂停并保留进度”，不能把用户任务一刀切成cancelled
        downloading_tasks = self.db.get_all_tasks(status='downloading')
        for task in downloading_tasks:
            task_id = task['task_id']
            try:
                paused = self.engine.pause_download(task_id)
                if not paused:
                    # 兜底：即使下载器实例丢了，也要把状态拍平为paused，防止重启后僵尸状态
                    self.db.update_task_status(task_id, 'paused', '程序退出自动暂停')
            except Exception as e:
                print(f"[错误] 退出暂停任务失败: {task_id}, err={e}")
                self.db.update_task_status(task_id, 'paused', '程序退出自动暂停（异常兜底）')

        # 再兜底清理引擎资源
        try:
            self.engine.shutdown()
        finally:
            with self._lock:
                self._running_tasks.clear()
