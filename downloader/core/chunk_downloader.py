# -*- coding: utf-8 -*-
"""
分块下载器
老王说：这玩意儿是核心中的核心，写不好整个下载器都白搭！
"""
import os
import requests
import time
from typing import Callable, Optional


class ChunkDownloader:
    """单个分块下载器"""

    def __init__(self, chunk_id: int, task_id: str, url: str,
                 start_byte: int, end_byte: int, temp_file: str,
                 timeout: int = 30, retry_times: int = 3,
                 user_agent: str = "PyDownloader/1.0"):
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

        self.downloaded_bytes = 0  # 已下载字节数
        self.is_paused = False  # 暂停标志
        self.is_cancelled = False  # 取消标志

        # 进度回调函数
        self.progress_callback: Optional[Callable] = None

    def set_progress_callback(self, callback: Callable):
        """
        设置进度回调函数
        Args:
            callback: 回调函数，签名为 callback(chunk_id, downloaded_bytes)
        """
        self.progress_callback = callback

    def download(self, resume: bool = False) -> bool:
        """
        执行下载
        Args:
            resume: 是否为断点续传
        Returns:
            True表示成功，False表示失败
        """
        # 确保临时文件目录存在
        temp_dir = os.path.dirname(self.temp_file)
        if temp_dir and not os.path.exists(temp_dir):
            os.makedirs(temp_dir, exist_ok=True)

        # 如果是断点续传，获取已下载字节数
        if resume and os.path.exists(self.temp_file):
            self.downloaded_bytes = os.path.getsize(self.temp_file)
        else:
            self.downloaded_bytes = 0

        # 计算实际的起始位置
        actual_start = self.start_byte + self.downloaded_bytes

        # 如果已经下载完成，直接返回
        if actual_start >= self.end_byte:
            return True

        # 开始下载（带重试）
        for attempt in range(self.retry_times):
            try:
                if self._download_chunk(actual_start):
                    return True
            except Exception as e:
                print(f"[错误] 分块{self.chunk_id}下载失败（尝试{attempt + 1}/{self.retry_times}）: {e}")
                if attempt < self.retry_times - 1:
                    time.sleep(1)  # 重试前等待1秒
                else:
                    return False

        return False

    def _download_chunk(self, start: int) -> bool:
        """
        实际下载逻辑
        Args:
            start: 实际起始字节
        Returns:
            True表示成功，False表示失败
        """
        headers = {
            'Range': f'bytes={start}-{self.end_byte}',
            'User-Agent': self.user_agent
        }

        # 发起请求
        response = requests.get(self.url, headers=headers, stream=True, timeout=self.timeout)

        # 检查状态码（206是部分内容，200是完整内容）
        if response.status_code not in (200, 206):
            print(f"[错误] 分块{self.chunk_id}请求失败: HTTP {response.status_code}")
            return False

        # 打开临时文件（追加模式）
        mode = 'ab' if self.downloaded_bytes > 0 else 'wb'
        with open(self.temp_file, mode) as f:
            for data in response.iter_content(chunk_size=8192):
                # 检查取消标志
                if self.is_cancelled:
                    return False

                # 检查暂停标志
                while self.is_paused:
                    time.sleep(0.1)
                    if self.is_cancelled:
                        return False

                # 写入数据
                if data:
                    f.write(data)
                    self.downloaded_bytes += len(data)

                    # 调用进度回调
                    if self.progress_callback:
                        self.progress_callback(self.chunk_id, self.downloaded_bytes)

        return True

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
