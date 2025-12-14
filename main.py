# -*- coding: utf-8 -*-
"""
老王下载器 - 启动入口
IDM风格的多线程下载器，支持断点续传、队列管理

作者：老王
版本：v1.0
"""
import sys
import os

# 添加项目根目录到Python路径
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from downloader.database.db_manager import DatabaseManager
from downloader.utils.config import ConfigManager
from downloader.core.download_engine import DownloadEngine
from downloader.core.task_manager import TaskManager
from downloader.ui.main_window import MainWindow


def main():
    """主函数"""
    print("[启动] 老王下载器正在启动...")

    # 初始化配置管理器
    config_manager = ConfigManager()
    print(f"[配置] 下载目录: {config_manager.download_dir}")
    print(f"[配置] 默认线程数: {config_manager.thread_count}")

    # 初始化数据库
    db_manager = DatabaseManager()
    print("[数据库] 数据库初始化完成")

    # 初始化下载引擎
    download_engine = DownloadEngine(db_manager, config_manager)
    print("[引擎] 下载引擎初始化完成")

    # 初始化任务管理器
    task_manager = TaskManager(download_engine, db_manager, config_manager.max_concurrent_downloads)
    print("[管理器] 任务管理器初始化完成")

    # 创建并启动GUI
    print("[GUI] 启动图形界面...")
    app = MainWindow(task_manager)
    app.mainloop()

    print("[退出] 老王下载器已关闭")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n[中断] 用户手动中断程序")
    except Exception as e:
        print(f"\n[错误] 程序异常退出: {e}")
        import traceback
        traceback.print_exc()
