# -*- coding: utf-8 -*-
"""
文件工具模块
老王说:  文件操作要稳，一个不小心就炸了！
"""
import os
import hashlib
import threading
from typing import List, Callable, Optional
from urllib.parse import urlparse, unquote


def format_size(size_bytes: int) -> str:
    """
    格式化文件大小
    Args:
        size_bytes: 字节数
    Returns:
        格式化后的字符串，如 "1.23 MB"
    """
    if size_bytes < 1024:
        return f"{size_bytes} B"
    elif size_bytes < 1024 * 1024:
        return f"{size_bytes / 1024:.2f} KB"
    elif size_bytes < 1024 * 1024 * 1024:
        return f"{size_bytes / (1024 * 1024):.2f} MB"
    else:
        return f"{size_bytes / (1024 * 1024 * 1024):.2f} GB"


def format_speed(speed_bytes_per_sec: float) -> str:
    """
    格式化速度
    Args:
        speed_bytes_per_sec: 字节/秒
    Returns:
        格式化后的字符串，如 "1.23 MB/s"
    """
    if speed_bytes_per_sec < 1024:
        return f"{speed_bytes_per_sec:.0f} B/s"
    elif speed_bytes_per_sec < 1024 * 1024:
        return f"{speed_bytes_per_sec / 1024:.2f} KB/s"
    else:
        return f"{speed_bytes_per_sec / (1024 * 1024):.2f} MB/s"


def get_filename_from_url(url: str) -> str:
    """
    从URL中提取文件名
    Args:
        url: 下载链接
    Returns:
        文件名
    """
    parsed = urlparse(url)
    filename = unquote(os.path.basename(parsed.path))
    if not filename or filename == '/':
        filename = 'download'
    return filename


def ensure_dir(directory: str):
    """确保目录存在"""
    if not os.path.exists(directory):
        os.makedirs(directory, exist_ok=True)


def merge_chunks(chunk_files: List[str], output_file: str, delete_chunks: bool = True) -> bool:
    """
    合并分块文件
    Args:
        chunk_files: 分块文件路径列表（按顺序）
        output_file: 输出文件路径
        delete_chunks: 是否删除临时分块文件
    Returns:
        True表示成功，False表示失败
    """
    try:
        # 确保输出目录存在
        output_dir = os.path.dirname(output_file)
        if output_dir:
            ensure_dir(output_dir)

        # 合并文件
        with open(output_file, 'wb') as outfile:
            for chunk_file in chunk_files:
                if not os.path.exists(chunk_file):
                    print(f"[错误] 分块文件不存在: {chunk_file}")
                    return False
                with open(chunk_file, 'rb') as infile:
                    while True:
                        data = infile.read(1024 * 1024)  # 每次读1MB
                        if not data:
                            break
                        outfile.write(data)

        # 删除临时文件
        if delete_chunks:
            for chunk_file in chunk_files:
                try:
                    os.remove(chunk_file)
                except Exception as e:
                    print(f"[警告] 删除临时文件失败: {chunk_file}, {e}")

        return True
    except Exception as e:
        print(f"[错误] 合并文件失败: {e}")
        return False


def delete_file(file_path: str) -> bool:
    """
    安全删除文件
    Args:
        file_path: 文件路径
    Returns:
        True表示成功，False表示失败
    """
    try:
        if os.path.exists(file_path):
            os.remove(file_path)
        return True
    except Exception as e:
        print(f"[错误] 删除文件失败: {file_path}, {e}")
        return False


def get_file_size(file_path: str) -> int:
    """
    获取文件大小
    Args:
        file_path: 文件路径
    Returns:
        文件大小（字节），文件不存在返回0
    """
    try:
        if os.path.exists(file_path):
            return os.path.getsize(file_path)
        return 0
    except Exception:
        return 0


def calculate_file_hash(file_path: str,
                        hash_type: str = "md5",
                        progress_callback: Optional[Callable[[int, int], None]] = None) -> str:
    """
    计算文件哈希值
    Args:
        file_path: 文件路径
        hash_type: 哈希类型，支持 md5/sha256
        progress_callback: 进度回调 callback(processed_bytes, total_bytes)
    Returns:
        哈希值字符串（小写16进制），失败返回空字符串

    老王说：大文件哈希计算得分块读，不然内存会爆炸！
    """
    hash_type = hash_type.lower()
    if hash_type == "md5":
        hasher = hashlib.md5()
    elif hash_type == "sha256":
        hasher = hashlib.sha256()
    else:
        print(f"[错误] 不支持的哈希类型: {hash_type}")
        return ""

    try:
        total_size = os.path.getsize(file_path)
        processed = 0
        chunk_size = 8 * 1024 * 1024  # 8MB一块，平衡速度和内存

        with open(file_path, 'rb') as f:
            while True:
                data = f.read(chunk_size)
                if not data:
                    break
                hasher.update(data)
                processed += len(data)
                if progress_callback:
                    progress_callback(processed, total_size)

        return hasher.hexdigest().lower()
    except Exception as e:
        print(f"[错误] 计算哈希失败: {e}")
        return ""


def verify_file_hash(file_path: str, expected_hash: str, hash_type: str = "md5") -> bool:
    """
    验证文件哈希
    Args:
        file_path: 文件路径
        expected_hash: 预期哈希值
        hash_type: 哈希类型
    Returns:
        True表示匹配，False表示不匹配
    """
    actual = calculate_file_hash(file_path, hash_type)
    if not actual:
        return False
    return actual == expected_hash.lower()


class FileLock:
    """文件锁（用于多线程安全写入）"""

    def __init__(self):
        self._lock = threading.Lock()

    def __enter__(self):
        self._lock.acquire()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self._lock.release()
