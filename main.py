# -*- coding: utf-8 -*-
"""
daw下载器 - 启动入口
IDM风格的多线程下载器，支持断点续传、队列管理

作者：老王
版本：v1.0
"""
import sys
import os

# 添加项目根目录到Python路径
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from downloader.database.db_manager import DatabaseManager
from downloader.utils.app_log import get_logger, setup_logging
from downloader.utils.config import ConfigManager
from downloader.core.download_engine import DownloadEngine
from downloader.core.task_manager import TaskManager
from downloader.ui.main_window import MainWindow


def main():
    """主函数"""
    setup_logging()
    logger = get_logger('main')

    if os.name == 'nt':
        import ctypes
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID('daw.Downloader')
    from downloader.browser.bridge import BridgeServer, InstanceGuard, call_desktop
    guard = InstanceGuard()
    if not guard.primary:
        try:
            call_desktop({'action': 'activate'}, timeout=2)
        except (OSError, ValueError):
            pass
        finally:
            guard.close()
        return
    logger.info("daw下载器正在启动...")

    # 初始化配置管理器
    config_manager = ConfigManager()
    logger.info("下载目录: %s", config_manager.download_dir)
    logger.info("默认线程数: %s", config_manager.thread_count)

    # 初始化数据库
    db_manager = DatabaseManager()
    logger.info("数据库初始化完成")

    # 初始化下载引擎
    download_engine = DownloadEngine(db_manager, config_manager)
    logger.info("下载引擎初始化完成")

    # 初始化任务管理器
    task_manager = TaskManager(download_engine, db_manager, config_manager.max_concurrent_downloads)
    logger.info("任务管理器初始化完成")

    # 创建并启动GUI
    logger.info("启动图形界面...")
    app = MainWindow(task_manager)
    bridge = BridgeServer(task_manager, app._show_from_tray) if os.name == 'nt' else None
    try:
        try:
            if bridge:
                bridge.start()
                app.browser_bridge = bridge
        except OSError:
            from tkinter import messagebox
            messagebox.showwarning('浏览器集成不可用', '无法创建本机通信端点，普通下载仍可使用。', parent=app)
        app.mainloop()
    finally:
        if bridge:
            bridge.close()
        task_manager.shutdown()
        guard.close()

    logger.info("daw下载器已关闭")


if __name__ == "__main__":
    logger = get_logger('main')
    try:
        main()
    except KeyboardInterrupt:
        setup_logging()
        logger.info("用户手动中断程序")
    except Exception as error:
        setup_logging()
        # 只记类型名，避免异常里的链接或凭据落盘；详细堆栈用 DAW_LOG_LEVEL=DEBUG
        logger.error("程序异常退出: %s", type(error).__name__)
        logger.debug("异常堆栈", exc_info=True)
