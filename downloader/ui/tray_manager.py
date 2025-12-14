# -*- coding: utf-8 -*-
"""
系统托盘管理
老王说：托盘这玩意儿得整好，用户习惯最小化到托盘！
"""
import os
import threading
from typing import Callable, Optional

# 托盘依赖检测，打包后可能缺失这些库
HAS_TRAY_SUPPORT = False
try:
    from PIL import Image
    import pystray
    from pystray import MenuItem, Menu
    HAS_TRAY_SUPPORT = True
except ImportError as e:
    print(f"[警告] 托盘依赖未安装({e})，托盘功能不可用")
    Image = None
    pystray = None

# 通知模块，Windows用plyer
HAS_NOTIFICATION = False
try:
    from plyer import notification
    HAS_NOTIFICATION = True
except ImportError:
    print("[警告] plyer未安装，下载完成通知功能不可用")
    notification = None


class TrayManager:
    """系统托盘管理器"""

    def __init__(self, app_name: str = "老王下载器"):
        self.app_name = app_name
        self.icon: Optional[object] = None
        self._running = False
        self._thread: Optional[threading.Thread] = None

        # 回调函数
        self._on_show_window: Optional[Callable] = None
        self._on_exit: Optional[Callable] = None

        # 图标路径
        self._icon_path = self._find_icon()

        # 托盘是否可用
        self.available = HAS_TRAY_SUPPORT

    def _find_icon(self) -> str:
        """查找图标文件"""
        # 艹，图标路径得动态查找，打包后和开发环境不一样
        possible_paths = [
            os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "assets", "icon.ico"),
            os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "assets", "icon.png"),
            os.path.join(os.path.dirname(__file__), "assets", "icon.ico"),
        ]
        for path in possible_paths:
            if os.path.exists(path):
                return path
        return ""

    def _create_icon_image(self):
        """创建托盘图标"""
        if not HAS_TRAY_SUPPORT or Image is None:
            return None

        if self._icon_path and os.path.exists(self._icon_path):
            try:
                return Image.open(self._icon_path)
            except Exception as e:
                print(f"[警告] 加载图标失败: {e}")

        # 没有图标就创建一个简单的蓝色方块
        img = Image.new('RGB', (64, 64), color=(30, 144, 255))
        return img

    def _create_menu(self):
        """创建右键菜单"""
        if not HAS_TRAY_SUPPORT:
            return None
        from pystray import MenuItem, Menu
        return Menu(
            MenuItem("显示主窗口", self._on_show_click, default=True),
            MenuItem("退出", self._on_exit_click),
        )

    def _on_show_click(self, icon, item):
        """点击'显示主窗口'"""
        if self._on_show_window:
            self._on_show_window()

    def _on_exit_click(self, icon, item):
        """点击'退出'"""
        self.stop()
        if self._on_exit:
            self._on_exit()

    def set_show_window_callback(self, callback: Callable):
        """设置显示窗口回调"""
        self._on_show_window = callback

    def set_exit_callback(self, callback: Callable):
        """设置退出回调"""
        self._on_exit = callback

    def start(self):
        """启动托盘图标（在单独线程中运行）"""
        if not HAS_TRAY_SUPPORT:
            print("[信息] 托盘功能不可用，跳过启动")
            return

        if self._running:
            return

        self._running = True

        def run_tray():
            try:
                self.icon = pystray.Icon(
                    self.app_name,
                    self._create_icon_image(),
                    self.app_name,
                    self._create_menu()
                )
                self.icon.run()
            except Exception as e:
                print(f"[错误] 托盘运行失败: {e}")
                self._running = False

        self._thread = threading.Thread(target=run_tray, daemon=True)
        self._thread.start()

    def stop(self):
        """停止托盘图标"""
        self._running = False
        if self.icon:
            try:
                self.icon.stop()
            except Exception:
                pass
            self.icon = None

    def show_notification(self, title: str, message: str, timeout: int = 5):
        """
        显示系统通知
        Args:
            title: 通知标题
            message: 通知内容
            timeout: 显示时长（秒）
        """
        if not HAS_NOTIFICATION:
            print(f"[通知] {title}: {message}")
            return

        try:
            # plyer的notification在Windows上使用win10toast
            notification.notify(
                title=title,
                message=message,
                app_name=self.app_name,
                timeout=timeout,
                app_icon=self._icon_path if self._icon_path.endswith('.ico') else None
            )
        except Exception as e:
            print(f"[警告] 发送通知失败: {e}")
            print(f"[通知] {title}: {message}")

    def notify_download_complete(self, filename: str):
        """下载完成通知"""
        self.show_notification(
            "下载完成",
            f"文件 {filename} 已下载完成！",
            timeout=5
        )
