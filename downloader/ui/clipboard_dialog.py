# -*- coding: utf-8 -*-
"""
剪贴板链接提示浮窗
老王说：弹窗别抢键盘焦点，用户可能正在别的窗口打字呢！
"""
import customtkinter as ctk

MAX_URL_DISPLAY = 120


class ClipboardLinkDialog(ctk.CTkToplevel):
    """
    检测到剪贴板新链接时的小浮窗

    非模态、不 grab、不 focus_force，只置顶提醒；用户点「添加任务」才进入
    标准添加流程。同一浮窗实例可通过 update_url 原地换链接，不叠窗。
    """

    def __init__(self, master, url: str, on_add, on_ignore_site):
        """
        Args:
            master: 主窗口
            url: 检测到的链接
            on_add: 点击「添加任务」回调，参数为当前链接
            on_ignore_site: 点击「忽略此站点」回调，参数为当前链接
        """
        super().__init__(master)

        self.url = url
        self._on_add = on_add
        self._on_ignore_site = on_ignore_site

        self.title("检测到下载链接")
        self.geometry("470x190")
        self.resizable(False, False)
        self.attributes("-topmost", True)
        self.protocol("WM_DELETE_WINDOW", self.destroy)

        self._create_ui()
        self.lift()

    def _create_ui(self):
        """创建UI组件"""
        ctk.CTkLabel(
            self, text="检测到新的下载链接：", font=("Arial", 12, "bold"), anchor="w",
        ).pack(fill="x", padx=16, pady=(14, 4))

        self.url_label = ctk.CTkLabel(
            self, text=self._display_url(), font=("Arial", 11), text_color="gray70",
            wraplength=430, justify="left", anchor="w",
        )
        self.url_label.pack(fill="x", padx=16, pady=(0, 12))

        button_frame = ctk.CTkFrame(self, fg_color="transparent")
        button_frame.pack(fill="x", padx=16, pady=(0, 14))

        ctk.CTkButton(button_frame, text="添加任务", width=96, command=self._on_add_click).pack(side="left")

        ctk.CTkButton(
            button_frame, text="忽略此站点", width=96, command=self._on_ignore_click,
            fg_color=("gray85", "gray25"), hover_color=("gray80", "gray30"),
            text_color=("gray10", "gray90"),
        ).pack(side="left", padx=8)

        ctk.CTkButton(
            button_frame, text="关闭", width=72, command=self.destroy,
            fg_color=("gray85", "gray25"), hover_color=("gray80", "gray30"),
            text_color=("gray10", "gray90"),
        ).pack(side="left")

    def _display_url(self) -> str:
        """链接过长时截断显示，完整链接仍走添加流程"""
        if len(self.url) <= MAX_URL_DISPLAY:
            return self.url
        return self.url[:MAX_URL_DISPLAY] + "…"

    def update_url(self, url: str):
        """浮窗已开时又复制了新链接：原地更新内容，不叠窗"""
        self.url = url
        self.url_label.configure(text=self._display_url())
        self.lift()

    def _on_add_click(self):
        """添加任务：回调后自行关闭"""
        callback = self._on_add
        self.destroy()
        if callback:
            callback(self.url)

    def _on_ignore_click(self):
        """忽略此站点：回调后自行关闭"""
        callback = self._on_ignore_site
        self.destroy()
        if callback:
            callback(self.url)
