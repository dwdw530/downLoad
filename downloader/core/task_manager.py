# -*- coding: utf-8 -*-
"""
任务管理器
老王说：队列管理得井井有条，不然乱套了！
"""
import logging
import threading
import os
from contextlib import contextmanager
from typing import List, Dict, Optional, Callable
from downloader.core.download_engine import DownloadEngine
from downloader.core.youtube_downloader import VIDEO_KINDS
from downloader.database.db_manager import DatabaseManager


logger = logging.getLogger(__name__)


# 批量操作允许处理的源状态；None 表示删除记录（除 verifying 外都可删）
BATCH_ACTION_STATUSES = {
    'pause': ('downloading', 'pending'),
    'start': ('pending', 'paused'),
    'retry': ('failed', 'verify_failed', 'cancelled'),
    'delete_records': None,
}


class BatchBusyError(RuntimeError):
    """已有一批批量操作在执行；调用方应提示忙碌而不是重复提交"""


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
        self._activity_condition = threading.Condition(self._lock)
        self._running_tasks = set()  # 正在下载的任务ID集合
        self._scheduling_paused = False
        self._adding_tasks = 0
        self._batch_operations = 0  # 正在执行的批量操作数（同一时间只允许一批）
        self._batch_hold = 0  # 暂停/删除从 UI 预留名额起就挡住启动，避免 worker 启动间隙补位
        self._batch_action = None
        self._batch_executing = False
        self._activity_generation = 0
        self._shutdown_started = False

        # 回调函数
        self.task_added_callback: Optional[Callable] = None
        self.task_status_changed_callback: Optional[Callable] = None
        self.task_started_callback: Optional[Callable] = None

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
            if task.get('download_type') in VIDEO_KINDS:
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

    def set_task_started_callback(self, callback: Callable):
        """通知实际接纳的启动/排队；即使瞬间完成也不会漏掉本轮参与者。"""
        self.task_started_callback = callback

    @contextmanager
    def _adding_task(self):
        """后台探测也属于未完成工作，关机确认不能只看已落库的任务。"""
        with self._lock:
            accepted = not self._shutdown_started
            if accepted:
                self._adding_tasks += 1
                self._activity_generation += 1
        try:
            yield accepted
        finally:
            if accepted:
                with self._activity_condition:
                    self._adding_tasks -= 1
                    self._activity_condition.notify_all()

    def get_activity_state(self):
        """返回任务操作代数及是否仍有探测/启动/运行工作。"""
        with self._lock:
            return self._activity_generation, bool(
                self._adding_tasks or self._running_tasks or self._batch_operations)

    @property
    def last_add_error(self) -> Optional[str]:
        """当前线程最近一次添加任务失败的原因代码"""
        return self.engine.last_add_error

    @property
    def last_add_detail(self) -> Optional[str]:
        """当前线程最近一次添加任务失败的具体说明"""
        return self.engine.last_add_detail

    def add_task(self, url: str, filename: Optional[str] = None,
                 save_path: Optional[str] = None,
                 expected_hash: Optional[str] = None,
                 hash_type: str = "md5", request_context=None,
                 start_later: bool = False) -> Optional[str]:
        """
        添加下载任务
        Args:
            url: 下载链接
            filename: 文件名（可选）
            save_path: 保存路径（可选）
            expected_hash: 预期哈希值（可选，用于下载后校验）
            hash_type: 哈希类型（md5/sha256）
            start_later: 是否稍后手动开始（不自动进入下载队列）
        Returns:
            任务ID，失败返回None
        """
        with self._adding_task() as accepted:
            if not accepted:
                return None
            kwargs = {'request_context': request_context} if request_context is not None else {}
            task_id = self.engine.create_download_task(url, filename, save_path, expected_hash, hash_type,
                                                      start_later=start_later, **kwargs)
            if not task_id:
                return None
            if self._shutdown_started:
                self.db.update_task_status(task_id, 'paused', '程序退出自动暂停')
                return task_id
            if self.task_added_callback:
                self.task_added_callback(task_id)
            if not start_later:
                self._try_start_next_task()
            return task_id

    def add_youtube_task(self, url, filename, save_path, height=720, kind='youtube', request_context=None):
        with self._adding_task() as accepted:
            if not accepted:
                return None
            task_id = self.engine.create_youtube_task(url, filename, save_path, height, kind,
                                                   request_context=request_context)
            if task_id:
                if self._shutdown_started:
                    self.db.update_task_status(task_id, 'paused', '程序退出自动暂停')
                    return task_id
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
            if (self._scheduling_paused or self._shutdown_started or self._batch_hold
                    or task_id in self._running_tasks):
                return False
            self._activity_generation += 1
            generation = self._activity_generation
            if len(self._running_tasks) >= self.max_concurrent:
                queued = self.db.update_task_status(task_id, 'pending', '等待可用下载槽位')
            else:
                queued = None
                self._running_tasks.add(task_id)

        if self.task_started_callback:
            self.task_started_callback(task_id, generation)
        if queued is not None:
            if queued and self.task_status_changed_callback:
                self.task_status_changed_callback(task_id, 'pending', '等待可用下载槽位')
            return bool(queued)

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
            if (self._scheduling_paused or self._shutdown_started or self._batch_hold
                    or task_id in self._running_tasks):
                return False
            self._activity_generation += 1
            # 艹，恢复任务也得走并发门禁，不能偷偷绕过最大并发
            if task_id not in self._running_tasks and len(self._running_tasks) >= self.max_concurrent:
                logger.info("已达到最大并发数，任务转入等待队列: %s", task_id)
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

    def move_task_up(self, task_id: str) -> bool:
        """
        把任务在列表中前移一位（影响尚未出队的 pending 顺序）
        Returns:
            True表示顺序已改变
        """
        return self._move_task(task_id, offset=-1)

    def move_task_to_top(self, task_id: str) -> bool:
        """把任务移到列表最前；已在最前时不改动"""
        return self._move_task(task_id, offset=None)

    def _move_task(self, task_id: str, offset: Optional[int]) -> bool:
        """
        按当前列表顺序重排，然后整体写回 list_order
        Args:
            offset: -1 表示前移一位；None 表示置顶
        """
        tasks = self.db.get_all_tasks()
        ordered = [task['task_id'] for task in tasks]
        if task_id not in ordered:
            return False

        index = ordered.index(task_id)
        if offset is None:
            if index == 0:
                return False
            ordered.pop(index)
            ordered.insert(0, task_id)
        else:
            target = index + offset
            if target < 0 or target >= len(ordered):
                return False
            ordered[index], ordered[target] = ordered[target], ordered[index]

        return self.db.update_task_orders(ordered)

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
                self._scheduling_paused = previous_scheduling_paused or self._shutdown_started

    def resume_all(self) -> int:
        """
        继续所有暂停的任务
        Returns:
            继续的任务数量
        """
        paused_tasks = self.db.get_all_tasks(status='paused')
        count = 0

        # 数据库已按 list_order 返回，不能再按创建时间覆盖用户的手动顺序。

        for task in paused_tasks:
            if self.resume_task(task['task_id']):
                count += 1
        return count

    def retry_failed(self) -> tuple:
        """重试所有失败任务（不碰暂停/等待中的任务）

        老王说：断网一晚上攒几十个失败，不能让人一个个点！
        Returns:
            (成功启动数, 未能启动数)；并发门禁由 start_task 自己处理
        """
        failed_tasks = self.db.get_all_tasks(status='failed')
        started = 0
        skipped = 0
        for task in failed_tasks:
            if self.start_task(task['task_id']):
                started += 1
            else:
                skipped += 1
        return started, skipped

    # ==================== 多选批量操作 ====================

    def begin_batch_operation(self, action=None) -> bool:
        """占用唯一的批量操作名额（UI 点击时先抢，防止 worker 启动前重复点击）

        Returns:
            True 表示抢到名额；False 表示已有一批在执行或程序正在退出
        """
        if action is not None and action not in BATCH_ACTION_STATUSES:
            raise ValueError('不支持的批量操作')
        with self._lock:
            if self._batch_operations or self._shutdown_started:
                return False
            self._batch_operations = 1
            self._batch_action = action
            self._batch_executing = False
            if action in ('pause', 'delete_records'):
                self._batch_hold += 1
            self._activity_generation += 1
            return True

    def end_batch_operation(self, schedule=True):
        """释放批量名额；没有暂缓/退出时按正常队列补位一次"""
        with self._lock:
            if not self._batch_operations:
                return
            if self._batch_action in ('pause', 'delete_records'):
                self._batch_hold = max(0, self._batch_hold - 1)
            self._batch_operations = 0
            self._batch_action = None
            self._batch_executing = False
            should_schedule = (schedule and not self._batch_hold and not self._scheduling_paused
                               and not self._shutdown_started)
        if should_schedule:
            try:
                self._try_start_next_task()
            except Exception:
                # 批量结果已经确定，恢复补位失败不能把已成功的项整体报成失败。
                logger.warning('批量操作后的队列补位失败')

    @staticmethod
    def _unique_batch_ids(task_ids) -> List[str]:
        """保序去重，不把重复 ID 算成多次操作"""
        unique = []
        seen = set()
        for raw in task_ids or ():
            task_id = str(raw or '')
            if not task_id or task_id in seen:
                continue
            seen.add(task_id)
            unique.append(task_id)
        return unique

    def _ordered_batch_ids(self, task_ids) -> List[str]:
        """按当前 list_order 排序；缺失 ID 保序排在最后，仍会计入结果"""
        unique = self._unique_batch_ids(task_ids)
        positions = {task['task_id']: index for index, task in enumerate(self.db.get_all_tasks())}
        fallback = len(positions)
        indexed = [(positions.get(task_id, fallback), index, task_id)
                   for index, task_id in enumerate(unique)]
        indexed.sort(key=lambda item: (item[0], item[1]))
        return [task_id for _position, _index, task_id in indexed]

    def batch_operate(self, task_ids, action: str, reserved: bool = False) -> Dict:
        """对选中任务执行一批相同动作，复用单任务接口

        Args:
            task_ids: 选中任务 ID（乱序允许，内部去重并按 list_order 重排）
            action: pause / start / retry / delete_records
            reserved: UI 已通过 begin_batch_operation 抢到名额时为 True
        Returns:
            {action, total, succeeded, skipped, failed}；每个去重 ID 恰好落入一类
        Raises:
            ValueError: 未知动作
            BatchBusyError: 已有另一批批量操作在执行
        """
        if action not in BATCH_ACTION_STATUSES:
            raise ValueError(f'不支持的批量操作: {action}')
        if not reserved and not self.begin_batch_operation(action):
            raise BatchBusyError('已有批量操作正在执行或下载器正在退出')

        with self._lock:
            if (not self._batch_operations or self._batch_executing
                    or self._batch_action not in (None, action)):
                raise BatchBusyError('没有可用的批量预留名额')
            if self._batch_action is None:
                self._batch_action = action
                if action in ('pause', 'delete_records'):
                    self._batch_hold += 1
            self._batch_executing = True
        try:
            ordered = self._ordered_batch_ids(task_ids)
            result = {'action': action, 'total': len(ordered),
                      'succeeded': [], 'skipped': [], 'failed': []}
            allowed = BATCH_ACTION_STATUSES[action]

            for index, task_id in enumerate(ordered):
                if self._shutdown_started:
                    # 退出后所有动作都停止后续逐项提交，不与退出清理竞争。
                    for rest in ordered[index:]:
                        result['skipped'].append({'task_id': rest, 'reason': 'shutdown'})
                    break

                task = self.db.get_task(task_id)
                if not task:
                    result['skipped'].append({'task_id': task_id, 'reason': 'missing'})
                    continue
                status = task.get('status')

                if action == 'delete_records':
                    if status == 'verifying':
                        result['skipped'].append({'task_id': task_id, 'reason': 'verifying'})
                        continue
                    try:
                        ok = self.delete_task(task_id)
                    except Exception:
                        logger.warning('批量删除记录失败: %s', task_id)
                        result['failed'].append({'task_id': task_id, 'reason': 'error'})
                        continue
                else:
                    if status not in allowed:
                        result['skipped'].append({'task_id': task_id, 'reason': 'status'})
                        continue
                    try:
                        if action == 'pause':
                            ok = self.pause_task(task_id)
                        else:
                            # 开始/重试必须走 start_task 的并发门禁与校验失败重置逻辑
                            ok = self.start_task(task_id)
                    except Exception:
                        logger.warning('批量%s任务失败: %s', action, task_id)
                        result['failed'].append({'task_id': task_id, 'reason': 'error'})
                        continue

                if ok:
                    result['succeeded'].append(task_id)
                else:
                    result['failed'].append({'task_id': task_id, 'reason': 'error'})

            return result
        finally:
            # 无论成功、异常还是退出，都必须释放忙碌并恢复正常调度判定
            self.end_batch_operation()

    def _try_start_next_task(self):
        """尝试启动下一个等待中的任务"""
        while True:
            with self._lock:
                # 只有暂停/删除持有 hold；开始/重试不会被自己的门禁挡住。
                if (self._scheduling_paused or self._shutdown_started or self._batch_hold
                        or len(self._running_tasks) >= self.max_concurrent):
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
        获取统计信息（SQL 聚合，不再把全部任务拉进内存）
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
        counts = self.db.get_status_counts()
        stats = {'total': sum(counts.values())}
        for status in ('downloading', 'pending', 'paused', 'completed', 'failed', 'cancelled'):
            stats[status] = counts.get(status, 0)
        return stats

    def get_total_speed(self) -> float:
        """下载中任务的速度合计（状态栏总速度）"""
        return self.db.get_total_speed()

    def begin_idle_shutdown(self, expected_generation, completed_ids):
        """原子完成最后复核并关闭入口；不持锁运行 Tk 或清理线程。"""
        with self._lock:
            if (self._shutdown_started or self._activity_generation != expected_generation
                    or self._adding_tasks or self._running_tasks or self._batch_operations):
                return False
            try:
                tasks = {task['task_id']: task for task in self.db.get_all_tasks()}
                if (not completed_ids or any(tasks.get(task_id, {}).get('status') != 'completed'
                                            for task_id in completed_ids)
                        or any(task.get('status') in ('pending', 'downloading', 'verifying')
                               for task in tasks.values())):
                    return False
            except Exception as error:
                logger.warning('自动退出最终复核失败，保留程序运行: %s', error)
                return False
            self._shutdown_started = True
            self._scheduling_paused = True
        return True

    def shutdown(self):
        """
        程序退出清理

        老王说：点了退出就得真退出，别让线程池把进程吊着不放，恶心！
        """
        with self._lock:
            self._scheduling_paused = True
            # 退出后批量循环不再提交后续项，也不恢复调度
            self._shutdown_started = True
            # 关闭入口前先固定在途任务；探测恰好返回时会释放运行名额，
            # 不能等网络收敛之后再拍快照，否则漏掉仍为 pending 的任务。
            runtime_task_ids = set(self._running_tasks)
        self.engine.begin_shutdown()
        # 后台添加的探测也要收敛，避免退出后才落库或触碰已销毁的窗口。
        with self._activity_condition:
            self._activity_condition.wait_for(lambda: not self._adding_tasks)
            runtime_task_ids.update(self._running_tasks)
        # 艹，退出时必须“自动暂停并保留进度”，不能把用户任务一刀切成cancelled
        downloading_tasks = [task for task in self.db.get_all_tasks()
                             if task['status'] in ('downloading', 'verifying')
                             or task['task_id'] in runtime_task_ids]
        for task in downloading_tasks:
            task_id = task['task_id']
            try:
                paused = self.engine.pause_download(task_id)
                if not paused:
                    # 兜底：即使下载器实例丢了，也要把状态拍平为paused，防止重启后僵尸状态
                    current = self.db.get_task(task_id)
                    if current and current['status'] in ('pending', 'downloading', 'verifying'):
                        self.db.update_task_status(task_id, 'paused', '程序退出自动暂停')
            except Exception as e:
                logger.error("退出暂停任务失败: %s, err=%s", task_id, e)
                self.db.update_task_status(task_id, 'paused', '程序退出自动暂停（异常兜底）')

        # 再兜底清理引擎资源
        try:
            self.engine.shutdown()
        finally:
            with self._lock:
                self._running_tasks.clear()
