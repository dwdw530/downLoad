# -*- coding: utf-8 -*-
"""
数据库管理模块
老王说：这玩意儿是整个项目的底层基础，千万别给我写出bug！
"""
import logging
import sqlite3
import os
from datetime import datetime
from typing import List, Dict, Optional, Tuple
import threading
from downloader.utils.config import get_app_root
from downloader.core.youtube_downloader import VIDEO_KINDS
from downloader.utils.file_utils import available_download_path


logger = logging.getLogger(__name__)


class DatabaseManager:
    """SQLite数据库管理器"""

    def __init__(self, db_path: Optional[str] = None):
        """
        初始化数据库管理器
        Args:
            db_path: 数据库文件路径
        """
        self.db_path = db_path if db_path is not None else os.path.join(get_app_root(), 'data', 'downloads.db')
        self._lock = threading.Lock()  # 艹，多线程访问必须加锁
        self._ensure_db_dir()
        self._init_database()

    def _ensure_db_dir(self):
        """确保数据库目录存在"""
        db_dir = os.path.dirname(self.db_path)
        if db_dir and not os.path.exists(db_dir):
            os.makedirs(db_dir, exist_ok=True)

    def _get_connection(self) -> sqlite3.Connection:
        """获取数据库连接"""
        conn = sqlite3.connect(self.db_path, check_same_thread=False)
        # 艹，SQLite默认外键是关的，不显式打开级联删除就是摆设
        conn.execute('PRAGMA foreign_keys = ON')
        conn.row_factory = sqlite3.Row  # 返回字典形式的查询结果
        return conn

    def _init_database(self):
        """初始化数据库表结构"""
        with self._lock:
            conn = self._get_connection()
            cursor = conn.cursor()

            # 进度写库较频，WAL 让写入不必等读锁；网络盘等不支持时退回默认日志模式
            try:
                cursor.execute('PRAGMA journal_mode=WAL')
            except sqlite3.DatabaseError as e:
                logger.warning("启用 WAL 失败，继续使用默认日志模式: %s", e)

            # 任务表
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS download_tasks (
                    task_id TEXT PRIMARY KEY,
                    url TEXT NOT NULL,
                    filename TEXT NOT NULL,
                    save_path TEXT NOT NULL,
                    total_size INTEGER DEFAULT 0,
                    downloaded_size INTEGER DEFAULT 0,
                    status TEXT DEFAULT 'pending',
                    support_range INTEGER DEFAULT 1,
                    thread_count INTEGER DEFAULT 8,
                    speed REAL DEFAULT 0.0,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    started_at TIMESTAMP,
                    completed_at TIMESTAMP,
                    error_message TEXT,
                    list_order REAL DEFAULT 0
                )
            ''')

            # 分块表
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS download_chunks (
                    chunk_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    task_id TEXT NOT NULL,
                    chunk_index INTEGER NOT NULL,
                    start_byte INTEGER NOT NULL,
                    end_byte INTEGER NOT NULL,
                    downloaded_bytes INTEGER DEFAULT 0,
                    status TEXT DEFAULT 'pending',
                    temp_file TEXT,
                    retry_count INTEGER DEFAULT 0,
                    FOREIGN KEY (task_id) REFERENCES download_tasks(task_id) ON DELETE CASCADE
                )
            ''')

            # 下载历史表
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS download_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    task_id TEXT NOT NULL,
                    filename TEXT NOT NULL,
                    file_size INTEGER,
                    download_time REAL,
                    avg_speed REAL,
                    completed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            ''')

            # 创建索引
            cursor.execute('CREATE INDEX IF NOT EXISTS idx_task_status ON download_tasks(status)')
            cursor.execute('CREATE INDEX IF NOT EXISTS idx_chunk_task ON download_chunks(task_id)')
            cursor.execute('CREATE INDEX IF NOT EXISTS idx_chunk_status ON download_chunks(task_id, status)')

            # 兼容老版本数据库：检查并添加校验相关字段
            # 老王说：不做兼容处理，老用户更新后数据库就炸了！
            try:
                cursor.execute("SELECT expected_hash FROM download_tasks LIMIT 1")
            except sqlite3.OperationalError:
                # 字段不存在，添加它们
                cursor.execute("ALTER TABLE download_tasks ADD COLUMN expected_hash TEXT")
                cursor.execute("ALTER TABLE download_tasks ADD COLUMN expected_hash_type TEXT")
                cursor.execute("ALTER TABLE download_tasks ADD COLUMN actual_hash TEXT")
                cursor.execute("ALTER TABLE download_tasks ADD COLUMN hash_verified INTEGER DEFAULT 0")

            columns = {row[1] for row in cursor.execute('PRAGMA table_info(download_tasks)')}
            if 'browser_context' not in columns:
                cursor.execute('ALTER TABLE download_tasks ADD COLUMN browser_context TEXT')
            if 'download_type' not in columns:
                cursor.execute("ALTER TABLE download_tasks ADD COLUMN download_type TEXT DEFAULT 'file'")
            if 'video_height' not in columns:
                cursor.execute('ALTER TABLE download_tasks ADD COLUMN video_height INTEGER DEFAULT 720')
            for column in ('resource_etag', 'resource_last_modified'):
                if column not in columns:
                    cursor.execute(f'ALTER TABLE download_tasks ADD COLUMN {column} TEXT')
            if 'list_order' not in columns:
                # 手动排序用的列表顺序：老库按原顺序（created_at 升序）回填，保证升级后列表不变
                cursor.execute('ALTER TABLE download_tasks ADD COLUMN list_order REAL DEFAULT 0')
                cursor.execute('UPDATE download_tasks SET list_order = rowid WHERE list_order IS NULL OR list_order = 0')

            conn.commit()
            conn.close()

    # ==================== 任务表操作 ====================

    def create_task(self, task_id: str, url: str, filename: str, save_path: str,
                    total_size: int = 0, support_range: bool = True, thread_count: int = 8) -> bool:
        """
        创建下载任务
        Returns:
            True表示创建成功，False表示失败
        """
        with self._lock:
            conn = None
            try:
                conn = self._get_connection()
                with conn:
                    cursor = conn.cursor()
                    cursor.execute('''
                        INSERT INTO download_tasks
                        (task_id, url, filename, save_path, total_size, support_range, thread_count, list_order)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    ''', (task_id, url, filename, save_path, total_size, 1 if support_range else 0,
                          thread_count, self._next_list_order(cursor)))
                return True
            except Exception as e:
                logger.error("创建任务失败: %s", e)
                return False
            finally:
                if conn:
                    conn.close()

    def create_task_with_chunks(self, task_id: str, url: str, filename: str, save_path: str,
                                total_size: int = 0, support_range: bool = True, thread_count: int = 8,
                                chunks: Optional[List[Tuple[int, int, int, str]]] = None,
                                expected_hash: Optional[str] = None, hash_type: str = "md5",
                                browser_context: Optional[str] = None, download_type: str = 'file',
                                video_height: int = 720, status: str = 'pending',
                                resource_etag=None, resource_last_modified=None,
                                reserve_path: bool = False) -> bool:
        """
        原子创建任务与分块（可选带预期哈希）
        老王说：任务和分块必须一锅端，要么全成要么全回滚，别搞半截子烂账。
        status 直接进 INSERT：稍后下载的任务一出生就是 paused，
        不然建完再改状态会被队列调度抢先捡起来。
        """
        with self._lock:
            conn = None
            try:
                conn = self._get_connection()
                with conn:
                    cursor = conn.cursor()
                    if reserve_path:
                        cursor.execute('BEGIN IMMEDIATE')
                        occupied = [row[0] for row in cursor.execute('SELECT save_path FROM download_tasks')]
                        save_path = available_download_path(save_path, occupied)
                        filename = os.path.basename(save_path)
                    cursor.execute('''
                        INSERT INTO download_tasks
                        (task_id, url, filename, save_path, total_size, support_range, thread_count, status, list_order,
                         resource_etag, resource_last_modified)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ''', (task_id, url, filename, save_path, total_size, 1 if support_range else 0,
                          thread_count, status, self._next_list_order(cursor), resource_etag, resource_last_modified))

                    if download_type in VIDEO_KINDS:
                        cursor.execute('UPDATE download_tasks SET download_type = ?, video_height = ? WHERE task_id = ?',
                                       (download_type, video_height, task_id))
                    if browser_context:
                        cursor.execute('UPDATE download_tasks SET browser_context = ? WHERE task_id = ?',
                                       (browser_context, task_id))

                    if expected_hash:
                        # 艹，预期哈希也跟任务一起落库，避免事务外更新导致脏状态
                        cursor.execute('''
                            UPDATE download_tasks
                            SET expected_hash = ?, expected_hash_type = ?
                            WHERE task_id = ?
                        ''', (
                            expected_hash.lower() if expected_hash else None,
                            hash_type.lower() if hash_type else None,
                            task_id
                        ))

                    if chunks:
                        cursor.executemany('''
                            INSERT INTO download_chunks
                            (task_id, chunk_index, start_byte, end_byte, temp_file)
                            VALUES (?, ?, ?, ?, ?)
                        ''', [(task_id, chunk_index, start_byte, end_byte, temp_file)
                              for chunk_index, start_byte, end_byte, temp_file in chunks])
                return True
            except Exception as e:
                logger.error("原子创建任务失败: %s", e)
                return False
            finally:
                if conn:
                    conn.close()

    def reserve_task_path(self, task_id: str):
        """启动前重查外部新增文件和旧任务重名；重试也不覆盖上次的输出。"""
        with self._lock:
            conn = self._get_connection()
            try:
                with conn:
                    conn.execute('BEGIN IMMEDIATE')
                    task = conn.execute('SELECT save_path FROM download_tasks WHERE task_id = ?', (task_id,)).fetchone()
                    if task is None:
                        raise ValueError('任务不存在')
                    occupied = [row[0] for row in conn.execute(
                        'SELECT save_path FROM download_tasks WHERE task_id != ?', (task_id,))]
                    path = available_download_path(task[0], occupied)
                    conn.execute('UPDATE download_tasks SET filename = ?, save_path = ? WHERE task_id = ?',
                                 (os.path.basename(path), path, task_id))
            finally:
                conn.close()

    def update_task_resource(self, task_id, size, support_range, count, metadata, chunks=None):
        """保存续传验证信息；资源变化时原子替换旧分块和进度。"""
        with self._lock:
            conn = self._get_connection()
            try:
                with conn:
                    conn.execute('''UPDATE download_tasks SET total_size = ?, support_range = ?, thread_count = ?,
                                    resource_etag = ?, resource_last_modified = ? WHERE task_id = ?''',
                                 (size, int(support_range), count, metadata.get('etag'),
                                  metadata.get('last_modified'), task_id))
                    if chunks is not None:
                        conn.execute('DELETE FROM download_chunks WHERE task_id = ?', (task_id,))
                        conn.executemany('''INSERT INTO download_chunks
                            (task_id, chunk_index, start_byte, end_byte, temp_file) VALUES (?, ?, ?, ?, ?)''',
                                         [(task_id, *chunk) for chunk in chunks])
                        conn.execute('UPDATE download_tasks SET downloaded_size = 0, speed = 0 WHERE task_id = ?',
                                     (task_id,))
            finally:
                conn.close()

    def get_task(self, task_id: str) -> Optional[Dict]:
        """获取任务详情"""
        with self._lock:
            conn = self._get_connection()
            cursor = conn.cursor()
            cursor.execute('SELECT * FROM download_tasks WHERE task_id = ?', (task_id,))
            row = cursor.fetchone()
            conn.close()
            return dict(row) if row else None

    def get_all_tasks(self, status: Optional[str] = None) -> List[Dict]:
        """
        获取所有任务（按手动排序顺序，未调整时等于创建时间升序）
        Args:
            status: 可选，筛选特定状态的任务
        """
        with self._lock:
            conn = None
            try:
                conn = self._get_connection()
                cursor = conn.cursor()
                if status:
                    # 艹，队列必须FIFO，按列表顺序取任务，不然越新的越先跑
                    cursor.execute('SELECT * FROM download_tasks WHERE status = ? '
                                   'ORDER BY list_order ASC, created_at ASC', (status,))
                else:
                    cursor.execute('SELECT * FROM download_tasks ORDER BY list_order ASC, created_at ASC')
                rows = cursor.fetchall()
                return [dict(row) for row in rows]
            finally:
                # 艹，这里必须finally关连接，异常路径不关库连接迟早把句柄耗死
                if conn:
                    conn.close()

    def update_task_status(self, task_id: str, status: str, error_message: Optional[str] = None) -> bool:
        """更新任务状态"""
        with self._lock:
            conn = None
            try:
                conn = self._get_connection()
                with conn:
                    cursor = conn.cursor()

                    # 根据状态更新时间戳
                    if status == 'downloading':
                        cursor.execute('''
                            UPDATE download_tasks
                            SET status = ?, started_at = CURRENT_TIMESTAMP, error_message = ?
                            WHERE task_id = ?
                        ''', (status, error_message, task_id))
                    elif status == 'completed':
                        cursor.execute('''
                            UPDATE download_tasks
                            SET status = ?, completed_at = CURRENT_TIMESTAMP, error_message = ?
                            WHERE task_id = ?
                        ''', (status, error_message, task_id))
                    else:
                        cursor.execute('''
                            UPDATE download_tasks
                            SET status = ?, error_message = ?
                            WHERE task_id = ?
                        ''', (status, error_message, task_id))
                return True
            except Exception as e:
                logger.error("更新任务状态失败: %s", e)
                return False
            finally:
                if conn:
                    conn.close()

    def update_task_size(self, task_id: str, total_size: int):
        with self._lock:
            conn = self._get_connection()
            try:
                with conn:
                    conn.execute('UPDATE download_tasks SET total_size = ? WHERE task_id = ?', (total_size, task_id))
            finally:
                conn.close()

    def update_task_progress(self, task_id: str, downloaded_size: int, speed: float) -> bool:
        """更新任务进度"""
        with self._lock:
            try:
                conn = self._get_connection()
                cursor = conn.cursor()
                cursor.execute('''
                    UPDATE download_tasks
                    SET downloaded_size = ?, speed = ?
                    WHERE task_id = ?
                ''', (downloaded_size, speed, task_id))
                conn.commit()
                conn.close()
                return True
            except Exception as e:
                logger.error("更新任务进度失败: %s", e)
                return False

    def mark_task_singlethread(self, task_id: str) -> bool:
        """
        将任务标记为单线程下载模式
        老王说：服务端Range不靠谱时，必须立刻降级，别让分块把任务拖死。
        """
        with self._lock:
            conn = None
            try:
                conn = self._get_connection()
                with conn:
                    cursor = conn.cursor()
                    cursor.execute('''
                        UPDATE download_tasks
                        SET support_range = 0, thread_count = 1
                        WHERE task_id = ?
                    ''', (task_id,))
                return True
            except Exception as e:
                logger.error("标记单线程模式失败: %s", e)
                return False
            finally:
                if conn:
                    conn.close()

    def update_task_hash(self, task_id: str, actual_hash: str, hash_verified: int) -> bool:
        """
        更新任务哈希校验结果
        Args:
            task_id: 任务ID
            actual_hash: 计算出的哈希值
            hash_verified: 校验结果 (0=未校验, 1=匹配, -1=不匹配)
        老王说：校验结果得存下来，不然用户想看都看不到！
        """
        with self._lock:
            try:
                conn = self._get_connection()
                cursor = conn.cursor()
                cursor.execute('''
                    UPDATE download_tasks
                    SET actual_hash = ?, hash_verified = ?
                    WHERE task_id = ?
                ''', (actual_hash, hash_verified, task_id))
                conn.commit()
                conn.close()
                return True
            except Exception as e:
                logger.error("更新哈希失败: %s", e)
                return False

    def set_expected_hash(self, task_id: str, expected_hash: str, hash_type: str) -> bool:
        """
        设置预期哈希值
        Args:
            task_id: 任务ID
            expected_hash: 预期哈希值
            hash_type: 哈希类型 (md5/sha256)
        """
        with self._lock:
            try:
                conn = self._get_connection()
                cursor = conn.cursor()
                cursor.execute('''
                    UPDATE download_tasks
                    SET expected_hash = ?, expected_hash_type = ?
                    WHERE task_id = ?
                ''', (expected_hash.lower() if expected_hash else None, hash_type.lower() if hash_type else None, task_id))
                conn.commit()
                conn.close()
                return True
            except Exception as e:
                logger.error("设置预期哈希失败: %s", e)
                return False

    def delete_task(self, task_id: str) -> bool:
        """删除任务（级联删除分块信息）"""
        with self._lock:
            try:
                conn = self._get_connection()
                cursor = conn.cursor()
                cursor.execute('DELETE FROM download_tasks WHERE task_id = ?', (task_id,))
                conn.commit()
                conn.close()
                return True
            except Exception as e:
                logger.error("删除任务失败: %s", e)
                return False

    @staticmethod
    def _next_list_order(cursor) -> float:
        """下一个列表顺序：追加到末尾，不动已有任务"""
        row = cursor.execute('SELECT COALESCE(MAX(list_order), 0) + 1 AS next_order FROM download_tasks').fetchone()
        return float(row['next_order']) if row else 1.0

    def update_task_orders(self, ordered_task_ids: List[str]) -> bool:
        """
        按给定顺序批量重写 list_order（1..N，单事务提交）
        Args:
            ordered_task_ids: 目标顺序的任务ID列表
        Returns:
            True表示保存成功
        """
        if not ordered_task_ids:
            return True
        with self._lock:
            conn = None
            try:
                conn = self._get_connection()
                with conn:
                    conn.cursor().executemany(
                        'UPDATE download_tasks SET list_order = ? WHERE task_id = ?',
                        [(index + 1, task_id) for index, task_id in enumerate(ordered_task_ids)],
                    )
                return True
            except Exception as e:
                logger.error("保存任务排序失败: %s", e)
                return False
            finally:
                if conn:
                    conn.close()

    # ==================== 分块表操作 ====================

    def create_chunks(self, task_id: str, chunks: List[Tuple[int, int, int, str]]) -> bool:
        """
        批量创建分块记录
        Args:
            task_id: 任务ID
            chunks: [(chunk_index, start_byte, end_byte, temp_file), ...]
        """
        with self._lock:
            try:
                conn = self._get_connection()
                cursor = conn.cursor()
                for chunk_index, start_byte, end_byte, temp_file in chunks:
                    cursor.execute('''
                        INSERT INTO download_chunks
                        (task_id, chunk_index, start_byte, end_byte, temp_file)
                        VALUES (?, ?, ?, ?, ?)
                    ''', (task_id, chunk_index, start_byte, end_byte, temp_file))
                conn.commit()
                conn.close()
                return True
            except Exception as e:
                logger.error("创建分块失败: %s", e)
                return False

    def get_chunks(self, task_id: str) -> List[Dict]:
        """获取任务的所有分块"""
        with self._lock:
            conn = self._get_connection()
            cursor = conn.cursor()
            cursor.execute('SELECT * FROM download_chunks WHERE task_id = ? ORDER BY chunk_index', (task_id,))
            rows = cursor.fetchall()
            conn.close()
            return [dict(row) for row in rows]

    def get_incomplete_chunks(self, task_id: str) -> List[Dict]:
        """获取未完成的分块"""
        with self._lock:
            conn = self._get_connection()
            cursor = conn.cursor()
            cursor.execute('''
                SELECT * FROM download_chunks
                WHERE task_id = ? AND status != 'completed'
                ORDER BY chunk_index
            ''', (task_id,))
            rows = cursor.fetchall()
            conn.close()
            return [dict(row) for row in rows]

    def update_chunk_progress(self, chunk_id: int, downloaded_bytes: int, status: Optional[str] = None) -> bool:
        """更新分块进度"""
        with self._lock:
            try:
                conn = self._get_connection()
                cursor = conn.cursor()
                if status:
                    cursor.execute('''
                        UPDATE download_chunks
                        SET downloaded_bytes = ?, status = ?
                        WHERE chunk_id = ?
                    ''', (downloaded_bytes, status, chunk_id))
                else:
                    cursor.execute('''
                        UPDATE download_chunks
                        SET downloaded_bytes = ?
                        WHERE chunk_id = ?
                    ''', (downloaded_bytes, chunk_id))
                conn.commit()
                conn.close()
                return True
            except Exception as e:
                logger.error("更新分块进度失败: %s", e)
                return False

    def update_chunk_temp_files(self, items: List[Tuple[int, str]]) -> bool:
        """批量更新分块临时文件路径（切换临时目录时同步未开始的任务）"""
        if not items:
            return True
        with self._lock:
            conn = None
            try:
                conn = self._get_connection()
                with conn:
                    conn.executemany(
                        'UPDATE download_chunks SET temp_file = ? WHERE chunk_id = ?',
                        [(path, chunk_id) for chunk_id, path in items],
                    )
                return True
            except Exception as e:
                logger.error("更新分块临时路径失败: %s", e)
                return False
            finally:
                if conn:
                    conn.close()

    def increment_chunk_retry(self, chunk_id: int) -> bool:
        """增加分块重试次数"""
        with self._lock:
            try:
                conn = self._get_connection()
                cursor = conn.cursor()
                cursor.execute('UPDATE download_chunks SET retry_count = retry_count + 1 WHERE chunk_id = ?', (chunk_id,))
                conn.commit()
                conn.close()
                return True
            except Exception as e:
                logger.error("更新重试次数失败: %s", e)
                return False

    # ==================== 历史记录操作 ====================

    def add_history(self, task_id: str, filename: str, file_size: int,
                    download_time: float, avg_speed: float) -> bool:
        """添加下载历史记录"""
        with self._lock:
            try:
                conn = self._get_connection()
                cursor = conn.cursor()
                cursor.execute('''
                    INSERT INTO download_history
                    (task_id, filename, file_size, download_time, avg_speed)
                    VALUES (?, ?, ?, ?, ?)
                ''', (task_id, filename, file_size, download_time, avg_speed))
                conn.commit()
                conn.close()
                return True
            except Exception as e:
                logger.error("添加历史记录失败: %s", e)
                return False

    def get_history(self, limit: int = 100) -> List[Dict]:
        """获取下载历史"""
        with self._lock:
            conn = self._get_connection()
            cursor = conn.cursor()
            cursor.execute('SELECT * FROM download_history ORDER BY completed_at DESC LIMIT ?', (limit,))
            rows = cursor.fetchall()
            conn.close()
            return [dict(row) for row in rows]

    def clear_history(self) -> bool:
        """清空下载历史记录"""
        with self._lock:
            try:
                conn = self._get_connection()
                cursor = conn.cursor()
                cursor.execute('DELETE FROM download_history')
                conn.commit()
                conn.close()
                return True
            except Exception as e:
                logger.error("清空历史记录失败: %s", e)
                return False
