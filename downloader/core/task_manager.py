# -*- coding: utf-8 -*-
"""
任务管理器
老王说：队列管理得井井有条，不然乱套了！
"""
import threading
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

        # 回调函数
        self.task_added_callback: Optional[Callable] = None
        self.task_status_changed_callback: Optional[Callable] = None

        # 设置引擎的状态回调
        self.engine.set_status_callback(self._on_engine_status_change)

    def set_task_added_callback(self, callback: Callable):
        """设置任务添加回调"""
        self.task_added_callback = callback

    def set_task_status_changed_callback(self, callback: Callable):
        """设置任务状态变更回调"""
        self.task_status_changed_callback = callback

    def add_task(self, url: str, filename: Optional[str] = None,
                 save_path: Optional[str] = None,
                 expected_hash: Optional[str] = None,
                 hash_type: str = "md5") -> Optional[str]:
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
        task_id = self.engine.create_download_task(url, filename, save_path, expected_hash, hash_type)
        if not task_id:
            return None

        # 调用任务添加回调
        if self.task_added_callback:
            self.task_added_callback(task_id)

        # 尝试启动任务
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
        if task['status'] not in ('pending', 'paused', 'failed'):
            return False

        # 检查并发限制
        with self._lock:
            if len(self._running_tasks) >= self.max_concurrent:
                print(f"[提示] 已达到最大并发数，任务将等待: {task_id}")
                return False

            self._running_tasks.add(task_id)

        # 启动下载
        resume = task['status'] in ('paused', 'failed')
        success = self.engine.start_download(task_id, resume=resume)

        if not success:
            with self._lock:
                self._running_tasks.discard(task_id)

        return success

    def pause_task(self, task_id: str) -> bool:
        """暂停任务"""
        return self.engine.pause_download(task_id)

    def resume_task(self, task_id: str) -> bool:
        """继续任务"""
        return self.engine.resume_download(task_id)

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
        暂停所有下载中的任务
        Returns:
            暂停的任务数量
        """
        downloading_tasks = self.get_downloading_tasks()
        count = 0
        for task in downloading_tasks:
            if self.pause_task(task['task_id']):
                count += 1
        return count

    def resume_all(self) -> int:
        """
        继续所有暂停的任务
        Returns:
            继续的任务数量
        """
        paused_tasks = self.db.get_all_tasks(status='paused')
        count = 0
        for task in paused_tasks:
            if self.resume_task(task['task_id']):
                count += 1
        return count

    def _try_start_next_task(self):
        """尝试启动下一个等待中的任务"""
        with self._lock:
            if len(self._running_tasks) >= self.max_concurrent:
                return

        # 获取等待中的任务
        pending_tasks = self.get_pending_tasks()
        if pending_tasks:
            task = pending_tasks[0]
            self.start_task(task['task_id'])

    def _on_engine_status_change(self, task_id: str, status: str, message: str):
        """引擎状态变更回调"""
        # 如果任务完成或失败，从运行集合中移除
        if status in ('completed', 'failed', 'cancelled'):
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
        # 先取消所有正在活跃的下载（避免ThreadPoolExecutor线程阻塞进程退出）
        active_task_ids = list(self.engine.active_downloaders.keys())
        for task_id in active_task_ids:
            try:
                self.engine.cancel_download(task_id)
            except Exception as e:
                print(f"[错误] 取消任务失败: {task_id}, err={e}")

        # 再兜底清理引擎资源
        try:
            self.engine.shutdown()
        finally:
            with self._lock:
                self._running_tasks.clear()
