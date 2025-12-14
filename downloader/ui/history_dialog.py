# -*- coding: utf-8 -*-
"""
下载历史对话框
老王说：历史记录得有，不然用户找不到下过的文件要骂娘！
"""
import customtkinter as ctk
from typing import List, Dict
from datetime import datetime


def format_file_size(size_bytes: int) -> str:
    """格式化文件大小"""
    if size_bytes < 1024:
        return f"{size_bytes} B"
    elif size_bytes < 1024 * 1024:
        return f"{size_bytes / 1024:.1f} KB"
    elif size_bytes < 1024 * 1024 * 1024:
        return f"{size_bytes / (1024 * 1024):.1f} MB"
    else:
        return f"{size_bytes / (1024 * 1024 * 1024):.2f} GB"


def format_duration(seconds: float) -> str:
    """格式化下载时长"""
    if seconds < 60:
        return f"{seconds:.0f}秒"
    elif seconds < 3600:
        minutes = int(seconds // 60)
        secs = int(seconds % 60)
        return f"{minutes}分{secs}秒"
    else:
        hours = int(seconds // 3600)
        minutes = int((seconds % 3600) // 60)
        return f"{hours}小时{minutes}分"


def format_speed(speed: float) -> str:
    """格式化速度"""
    if speed < 1024:
        return f"{speed:.1f} B/s"
    elif speed < 1024 * 1024:
        return f"{speed / 1024:.1f} KB/s"
    else:
        return f"{speed / (1024 * 1024):.2f} MB/s"


class HistoryDialog(ctk.CTkToplevel):
    """下载历史对话框"""

    def __init__(self, parent, db_manager):
        super().__init__(parent)

        self.db_manager = db_manager

        # 设置窗口
        self.title("下载历史")
        self.geometry("700x500")
        self.resizable(True, True)

        # 模态对话框
        self.transient(parent)
        self.grab_set()

        # 创建UI
        self._create_ui()

        # 加载历史数据
        self._load_history()

        # 居中显示
        self.update_idletasks()
        x = parent.winfo_x() + (parent.winfo_width() - self.winfo_width()) // 2
        y = parent.winfo_y() + (parent.winfo_height() - self.winfo_height()) // 2
        self.geometry(f"+{x}+{y}")

    def _create_ui(self):
        """创建UI"""
        # 标题
        title_label = ctk.CTkLabel(
            self,
            text="下载历史记录",
            font=ctk.CTkFont(size=18, weight="bold")
        )
        title_label.pack(pady=(15, 10))

        # 历史列表区域（使用ScrollableFrame）
        self.history_frame = ctk.CTkScrollableFrame(
            self,
            label_text="",
            width=650,
            height=350
        )
        self.history_frame.pack(fill="both", expand=True, padx=20, pady=10)

        # 底部按钮区域
        button_frame = ctk.CTkFrame(self, fg_color="transparent")
        button_frame.pack(fill="x", padx=20, pady=15)

        # 刷新按钮
        refresh_btn = ctk.CTkButton(
            button_frame,
            text="刷新",
            command=self._load_history,
            width=100
        )
        refresh_btn.pack(side="left", padx=5)

        # 清空历史按钮
        clear_btn = ctk.CTkButton(
            button_frame,
            text="清空历史",
            command=self._on_clear_history,
            width=100,
            fg_color="red",
            hover_color="darkred"
        )
        clear_btn.pack(side="left", padx=5)

        # 关闭按钮
        close_btn = ctk.CTkButton(
            button_frame,
            text="关闭",
            command=self.destroy,
            width=100
        )
        close_btn.pack(side="right", padx=5)

    def _load_history(self):
        """加载历史数据"""
        # 清空现有内容
        for widget in self.history_frame.winfo_children():
            widget.destroy()

        # 获取历史记录
        history = self.db_manager.get_history(limit=100)

        if not history:
            # 无历史记录
            empty_label = ctk.CTkLabel(
                self.history_frame,
                text="暂无下载历史记录",
                font=ctk.CTkFont(size=14),
                text_color="gray"
            )
            empty_label.pack(pady=50)
            return

        # 创建历史列表
        for idx, record in enumerate(history):
            self._add_history_item(record, idx)

    def _add_history_item(self, record: Dict, index: int):
        """添加历史记录项"""
        # 每条记录一个卡片
        item_frame = ctk.CTkFrame(self.history_frame)
        item_frame.pack(fill="x", pady=5, padx=5)

        # 左侧信息区域
        info_frame = ctk.CTkFrame(item_frame, fg_color="transparent")
        info_frame.pack(fill="x", padx=10, pady=8)

        # 文件名（第一行）
        filename = record.get('filename', '未知文件')
        filename_label = ctk.CTkLabel(
            info_frame,
            text=filename,
            font=ctk.CTkFont(size=13, weight="bold"),
            anchor="w"
        )
        filename_label.pack(anchor="w")

        # 详细信息（第二行）
        file_size = format_file_size(record.get('file_size', 0))
        download_time = format_duration(record.get('download_time', 0))
        avg_speed = format_speed(record.get('avg_speed', 0))

        # 完成时间
        completed_at = record.get('completed_at', '')
        if completed_at:
            try:
                # 解析SQLite时间戳格式
                dt = datetime.fromisoformat(completed_at.replace('Z', '+00:00'))
                completed_str = dt.strftime('%Y-%m-%d %H:%M')
            except Exception:
                completed_str = completed_at[:16] if len(completed_at) >= 16 else completed_at
        else:
            completed_str = "未知时间"

        detail_text = f"大小: {file_size}  |  耗时: {download_time}  |  平均速度: {avg_speed}  |  完成于: {completed_str}"
        detail_label = ctk.CTkLabel(
            info_frame,
            text=detail_text,
            font=ctk.CTkFont(size=11),
            text_color="gray",
            anchor="w"
        )
        detail_label.pack(anchor="w", pady=(3, 0))

    def _on_clear_history(self):
        """清空历史记录"""
        from tkinter import messagebox

        if messagebox.askyesno("确认", "确定要清空所有下载历史记录吗？\n此操作不可恢复！", parent=self):
            try:
                self.db_manager.clear_history()
                self._load_history()
                messagebox.showinfo("成功", "历史记录已清空！", parent=self)
            except Exception as e:
                messagebox.showerror("错误", f"清空历史失败: {e}", parent=self)
