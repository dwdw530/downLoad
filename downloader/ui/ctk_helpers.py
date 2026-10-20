# -*- coding: utf-8 -*-
"""
CustomTkinter 顶层窗口的小补丁

CTk 的 Toplevel 在建窗后会往事件队列里排几个延迟回调：
- `_windows_set_titlebar_color`（resizable() / 外观模式变化后 10ms）
- `_revert_withdraw_after_windows_set_titlebar_color`
- 恢复原来的焦点控件

对话框常常“点确定就 destroy”，这些回调随后就撞上已经销毁的窗口，抛
`bad window path name`。CTk 自己在其中一个分支里还会直接 print，控制台会刷
一堆无意义报错，GUI 测试也会因此报主线程回调异常。这里统一挡掉。
"""


class SafeToplevelMixin:
    """让 CTk 的延迟回调在窗口已销毁时安静退出"""

    def _window_alive(self) -> bool:
        try:
            return bool(self.winfo_exists())
        except Exception:
            return False

    def _windows_set_titlebar_color(self, *args, **kwargs):
        if self._window_alive():
            super()._windows_set_titlebar_color(*args, **kwargs)

    def _revert_withdraw_after_windows_set_titlebar_color(self):
        if self._window_alive():
            super()._revert_withdraw_after_windows_set_titlebar_color()

    def focus_set(self):
        if self._window_alive():
            super().focus_set()

    # tkinter 里 focus 是 focus_set 的独立别名，CTk 排的回调可能拿的是任一名字
    focus = focus_set
