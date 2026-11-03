# -*- coding: utf-8 -*-
"""
CustomTkinter 顶层窗口的显示稳定性与回调安全

CTk 的 Toplevel 在建窗后会往事件队列里排几个延迟回调：
- `_windows_set_titlebar_color`（resizable() / 外观模式变化后 10ms）
- `_revert_withdraw_after_windows_set_titlebar_color`
- 恢复原来的焦点控件

对话框常常“点确定就 destroy”，这些回调随后就撞上已经销毁的窗口，抛
`bad window path name`。CTk 自己在其中一个分支里还会直接 print，控制台会刷
一堆无意义报错，GUI 测试也会因此报主线程回调异常。这里统一挡掉。

已显示的窗口直接更新 DWM 标题栏属性，不再 withdraw/update/deiconify，避免
resizable() 的延迟回调让功能窗口闪一下。首次显示仍沿用 CTk 的初始化时序。
"""
import ctypes
from ctypes import wintypes
import logging
import sys


logger = logging.getLogger(__name__)
if sys.platform.startswith("win"):
    _get_window_parent = ctypes.windll.user32.GetParent
    _get_window_parent.argtypes = [wintypes.HWND]
    _get_window_parent.restype = wintypes.HWND
    _set_dwm_attribute = ctypes.windll.dwmapi.DwmSetWindowAttribute
    _set_dwm_attribute.argtypes = [wintypes.HWND, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD]
    _set_dwm_attribute.restype = ctypes.c_long


class SafeToplevelMixin:
    """让 CTk 的延迟回调在窗口已销毁时安静退出"""

    def _window_alive(self) -> bool:
        try:
            return bool(self.winfo_exists())
        except Exception:
            return False

    def _windows_set_titlebar_color(self, color_mode: str):
        if not self._window_alive():
            return
        if not self.winfo_ismapped():
            super()._windows_set_titlebar_color(color_mode)
            return
        if not sys.platform.startswith("win") or self._deactivate_windows_window_header_manipulation:
            return
        mode = color_mode.lower()
        if mode not in ("dark", "light"):
            return
        value = ctypes.c_int(mode == "dark")
        hwnd = _get_window_parent(self.winfo_id())
        result = _set_dwm_attribute(hwnd, 20, ctypes.byref(value), ctypes.sizeof(value))
        if result != 0:
            result = _set_dwm_attribute(hwnd, 19, ctypes.byref(value), ctypes.sizeof(value))
        if result != 0:
            logger.warning("窗口标题栏主题更新失败 (HRESULT=%s)", result)

    def _revert_withdraw_after_windows_set_titlebar_color(self):
        if self._window_alive():
            super()._revert_withdraw_after_windows_set_titlebar_color()

    def focus_set(self):
        if self._window_alive():
            super().focus_set()

    # tkinter 里 focus 是 focus_set 的独立别名，CTk 排的回调可能拿的是任一名字
    focus = focus_set
