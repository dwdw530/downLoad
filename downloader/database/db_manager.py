# -*- coding: utf-8 -*-
"""
数据库管理模块
老王说：这玩意儿是整个项目的底层基础，千万别给我写出bug！
"""
import sqlite3
import os
from datetime import datetime
from typing import List, Dict, Optional, Tuple
import threading


class DatabaseManager:
    """SQLite数据库管理器"""

    def __init__(self, db_path: str = "data/downloads.db"):
        """
        初始化数据库管理器
        Args:
            db_path: 数据库文件路径
        """
        self.db_path = db_path
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
        conn.row_factory = sqlite3.Row  # 返回字典形式的查询结果
        return conn

    def _init_database(self):
        """初始化数据库表结构"""
        with self._lock:
            conn = self._get_connection()
            cursor = conn.cursor()

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
                    error_message TEXT
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
            try:
                conn = self._get_connection()
                cursor = conn.cursor()
                cursor.execute('''
                    INSERT INTO download_tasks
                    (task_id, url, filename, save_path, total_size, support_range, thread_count)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                ''', (task_id, url, filename, save_path, total_size, 1 if support_range else 0, thread_count))
                conn.commit()
                conn.close()
                return True
            except Exception as e:
                print(f"[错误] 创建任务失败: {e}")
                return False

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
        获取所有任务
        Args:
            status: 可选，筛选特定状态的任务
        """
        with self._lock:
            conn = self._get_connection()
            cursor = conn.cursor()
            if status:
                cursor.execute('SELECT * FROM download_tasks WHERE status = ? ORDER BY created_at DESC', (status,))
            else:
                cursor.execute('SELECT * FROM download_tasks ORDER BY created_at DESC')
            rows = cursor.fetchall()
            conn.close()
            return [dict(row) for row in rows]

    def update_task_status(self, task_id: str, status: str, error_message: Optional[str] = None) -> bool:
        """更新任务状态"""
        with self._lock:
            try:
                conn = self._get_connection()
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

                conn.commit()
                conn.close()
                return True
            except Exception as e:
                print(f"[错误] 更新任务状态失败: {e}")
                return False

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
                print(f"[错误] 更新任务进度失败: {e}")
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
                print(f"[错误] 删除任务失败: {e}")
                return False

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
                print(f"[错误] 创建分块失败: {e}")
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
                print(f"[错误] 更新分块进度失败: {e}")
                return False

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
                print(f"[错误] 更新重试次数失败: {e}")
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
                print(f"[错误] 添加历史记录失败: {e}")
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
