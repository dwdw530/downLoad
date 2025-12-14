# -*- coding: utf-8 -*-
"""
配置管理模块
老王说：配置文件就该简单明了，别搞那些花里胡哨的！
"""
import json
import os
import sys
from typing import Any, Dict


def get_app_root() -> str:
    """
    获取应用根目录（支持打包后运行）
    艹，打包后路径会变，得动态判断！
    """
    if getattr(sys, 'frozen', False):
        # 打包后的exe环境
        # sys.executable是exe的路径，取其目录作为根目录
        return os.path.dirname(sys.executable)
    else:
        # 开发环境，返回项目根目录（main.py所在目录）
        return os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class ConfigManager:
    """配置管理器"""

    DEFAULT_CONFIG = {
        "download_dir": os.path.join(os.path.expanduser("~"), "Downloads", "老王下载器"),  # 用户下载目录
        "temp_dir": "temp",  # 临时文件目录
        "thread_count": 8,  # 默认线程数
        "max_concurrent_downloads": 3,  # 同时下载任务数
        "retry_times": 3,  # 失败重试次数
        "chunk_size": 1024 * 1024,  # 分块大小（1MB）
        "timeout": 30,  # 请求超时（秒）
        "user_agent": "PyDownloader/1.0",  # User-Agent
        "proxy": {
            "enabled": False,
            "http": "",
            "https": ""
        },
        "close_behavior": "ask",  # 关闭行为：ask|minimize|exit
        "speed_limit": 0,  # 速度限制（字节/秒），0表示不限速
    }

    def __init__(self, config_path: str = None):
        """
        初始化配置管理器
        Args:
            config_path: 配置文件路径（如果为None，使用默认路径）
        """
        if config_path is None:
            # 使用应用根目录下的data/config.json
            config_path = os.path.join(get_app_root(), "data", "config.json")
        self.config_path = config_path
        self._config = {}
        self._ensure_config_dir()
        self._load_config()

    def _ensure_config_dir(self):
        """确保配置文件目录存在"""
        config_dir = os.path.dirname(self.config_path)
        if config_dir and not os.path.exists(config_dir):
            os.makedirs(config_dir, exist_ok=True)

    def _load_config(self):
        """加载配置文件"""
        if os.path.exists(self.config_path):
            try:
                with open(self.config_path, 'r', encoding='utf-8') as f:
                    self._config = json.load(f)
                # 合并默认配置（防止缺少字段）
                for key, value in self.DEFAULT_CONFIG.items():
                    if key not in self._config:
                        self._config[key] = value
            except Exception as e:
                print(f"[错误] 加载配置文件失败: {e}，使用默认配置")
                self._config = self.DEFAULT_CONFIG.copy()
        else:
            # 配置文件不存在，使用默认配置并保存
            self._config = self.DEFAULT_CONFIG.copy()
            self.save()

    def save(self):
        """保存配置到文件"""
        try:
            with open(self.config_path, 'w', encoding='utf-8') as f:
                json.dump(self._config, f, indent=4, ensure_ascii=False)
            return True
        except Exception as e:
            print(f"[错误] 保存配置文件失败: {e}")
            return False

    def get(self, key: str, default: Any = None) -> Any:
        """获取配置项"""
        return self._config.get(key, default)

    def set(self, key: str, value: Any):
        """设置配置项"""
        self._config[key] = value

    def get_all(self) -> Dict:
        """获取所有配置"""
        return self._config.copy()

    def reset(self):
        """重置为默认配置"""
        self._config = self.DEFAULT_CONFIG.copy()
        self.save()

    # ==================== 快捷访问方法 ====================

    @property
    def download_dir(self) -> str:
        """下载目录"""
        return self._config.get("download_dir", "downloads")

    @download_dir.setter
    def download_dir(self, value: str):
        self._config["download_dir"] = value

    @property
    def temp_dir(self) -> str:
        """临时文件目录（返回绝对路径）"""
        temp_dir = self._config.get("temp_dir", "temp")
        # 如果是相对路径，转换为应用根目录下的绝对路径
        if not os.path.isabs(temp_dir):
            temp_dir = os.path.join(get_app_root(), temp_dir)
        return temp_dir

    @property
    def thread_count(self) -> int:
        """默认线程数"""
        return self._config.get("thread_count", 8)

    @thread_count.setter
    def thread_count(self, value: int):
        self._config["thread_count"] = max(1, min(16, value))  # 限制1-16

    @property
    def max_concurrent_downloads(self) -> int:
        """同时下载任务数"""
        return self._config.get("max_concurrent_downloads", 3)

    @max_concurrent_downloads.setter
    def max_concurrent_downloads(self, value: int):
        self._config["max_concurrent_downloads"] = max(1, min(5, value))  # 限制1-5

    @property
    def retry_times(self) -> int:
        """重试次数"""
        return self._config.get("retry_times", 3)

    @property
    def timeout(self) -> int:
        """请求超时"""
        return self._config.get("timeout", 30)

    @property
    def user_agent(self) -> str:
        """User-Agent"""
        return self._config.get("user_agent", "PyDownloader/1.0")

    @property
    def speed_limit(self) -> int:
        """速度限制（字节/秒），0表示不限速"""
        return self._config.get("speed_limit", 0)

    @speed_limit.setter
    def speed_limit(self, value: int):
        """设置速度限制"""
        self._config["speed_limit"] = max(0, value)  # 不能为负数
