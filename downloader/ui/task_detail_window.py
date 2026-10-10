# -*- coding: utf-8 -*-
"""
任务详情窗（双击任务卡片打开，显示实时速度曲线）
老王说：曲线一秒一画足够了，别把CPU当显卡使！
"""
from collections import deque

import customtkinter as ctk
import tkinter as tk

from downloader.utils.file_utils import format_progress_texts, format_speed

# 状态文案映射（任务卡片与详情窗共用，改这里两处一起变）
STATUS_TEXT_MAP = {
    'pending': '等待中',
    'downloading': '下载中',
    'paused': '已暂停',
    'completed': '已完成',
    'failed': '失败',
    'cancelled': '已取消',
    'verifying': '校验中',
    'verify_failed': '校验失败',
}


def get_status_text(status: str) -> str:
    """获取状态文本"""
    return STATUS_TEXT_MAP.get(status, status)


# 深色主题下的曲线配色（主窗口固定 dark 模式，写死即可）
CANVAS_BG = "#2b2b2b"
LINE_COLOR = "#1F6AA5"
GRID_COLOR = "#4a4a4a"
TEXT_COLOR = "#9c9c9c"
HIGHLIGHT_COLOR = "#c8c8c8"


class TaskDetailWindow(ctk.CTkToplevel):
    """
    任务详情窗：进度统计 + 实时速度曲线（非模态，不抢主窗口操作）

    速度历史由 MainWindow 持有（每秒一个采样点），这里只读同一份 deque，
    避免两处各记一份导致曲线和卡片对不上。
    """

    def __init__(self, master, task: dict, history: deque):
        super().__init__(master)

        self.task_id = task['task_id']
        self._status = task.get('status') or 'pending'
        self._downloaded = int(task.get('downloaded_size') or 0)
        self._total = int(task.get('total_size') or 0)
        self._speed = float(task.get('speed') or 0)
        # 直接引用主窗口的采样队列（只读），窗口关闭不影响主窗口继续记录
        self._history = history if history is not None else deque(maxlen=180)
        self._closed = False

        self.title(f"任务详情 - {task.get('filename') or ''}")
        self.geometry("560x420")
        self.minsize(480, 360)
        self.transient(master)
        self.protocol("WM_DELETE_WINDOW", self._on_close)

        self._create_ui(task)
        # 首次布局时 canvas 宽度还是 1px，等一轮布局再画；拉伸窗口靠 Configure 重绘
        self.canvas.bind("<Configure>", lambda _event: self._redraw())
        self.after(60, self._redraw)
        self._refresh_stats()

    def _create_ui(self, task: dict):
        """创建UI组件"""
        self.filename_label = ctk.CTkLabel(
            self, text=task.get('filename') or '', font=("Arial", 15, "bold"),
            wraplength=520, anchor="w", justify="left",
        )
        self.filename_label.pack(fill="x", padx=14, pady=(14, 2))

        self.stats_label = ctk.CTkLabel(self, text="", font=("Arial", 12), anchor="w", justify="left")
        self.stats_label.pack(fill="x", padx=14, pady=(0, 4))

        progress_row = ctk.CTkFrame(self, fg_color="transparent")
        progress_row.pack(fill="x", padx=14, pady=(0, 8))

        self.progress_bar = ctk.CTkProgressBar(progress_row)
        self.progress_bar.pack(side="left", fill="x", expand=True, padx=(0, 8))
        self.progress_bar.set(0)

        self.percent_label = ctk.CTkLabel(progress_row, text="0%", width=60)
        self.percent_label.pack(side="left")

        self.canvas = tk.Canvas(self, bg=CANVAS_BG, highlightthickness=0, height=260)
        self.canvas.pack(fill="both", expand=True, padx=14, pady=(0, 12))

    def update_progress(self, downloaded_size: int, total_size: int, speed: float):
        """主窗口进度事件推送（主线程调用）"""
        if self._closed or not self.winfo_exists():
            return
        self._downloaded = int(downloaded_size or 0)
        self._total = int(total_size or 0)
        self._speed = float(speed or 0)
        self._refresh_stats()
        self._redraw()

    def update_status(self, status: str):
        """主窗口状态事件推送（暂停/完成后曲线自然冻结）"""
        if self._closed or not self.winfo_exists():
            return
        self._status = status
        if status != 'downloading':
            self._speed = 0.0
        self._refresh_stats()

    def _refresh_stats(self):
        """刷新统计文案与进度条"""
        size_text, eta_text, speed_text = format_progress_texts(
            self._downloaded, self._total, self._speed, self._status)
        self.stats_label.configure(
            text=f"状态 {get_status_text(self._status)}    {size_text}    {eta_text}    {speed_text}")
        progress = self._downloaded / self._total if self._total > 0 else 0
        self.progress_bar.set(progress)
        self.percent_label.configure(text=f"{progress * 100:.1f}%")

    def _redraw(self):
        """重绘速度曲线（采样点约1秒1个，全量重画开销可忽略）"""
        if self._closed or not self.winfo_exists():
            return
        canvas = self.canvas
        canvas.delete("all")
        width = canvas.winfo_width()
        height = canvas.winfo_height()
        if width <= 10 or height <= 10:
            return  # 还没完成布局，等 Configure 事件再画

        samples = list(self._history)
        if not samples:
            canvas.create_text(
                width // 2, height // 2,
                text="暂无速度数据（下载开始后记录）",
                fill=TEXT_COLOR, font=("Arial", 12))
            return

        pad_l, pad_r, pad_t, pad_b = 64, 12, 14, 24
        plot_w = width - pad_l - pad_r
        plot_h = height - pad_t - pad_b
        if plot_w <= 10 or plot_h <= 10:
            return

        peak = max(samples)
        # 纵轴上限：峰值×1.1，下限 1KB/s，避免全零时除零
        top = max(peak * 1.1, 1024.0)

        # 横向网格线：0 / 50% / 100%，左侧标注对应速度
        for frac in (0.0, 0.5, 1.0):
            y = pad_t + plot_h * (1 - frac)
            canvas.create_line(pad_l, y, width - pad_r, y, fill=GRID_COLOR)
            canvas.create_text(
                pad_l - 6, y, text=format_speed(top * frac),
                fill=TEXT_COLOR, font=("Arial", 10), anchor="e")

        n = len(samples)
        step = plot_w / max(n - 1, 1)
        points = []
        for i, value in enumerate(samples):
            x = pad_l + step * i
            y = pad_t + plot_h * (1 - min(value / top, 1.0))
            points.extend((x, y))

        if n == 1:
            canvas.create_oval(
                points[0] - 2, points[1] - 2, points[0] + 2, points[1] + 2,
                fill=LINE_COLOR, outline="")
        else:
            # 折线下方半透明填充 + 折线本体
            canvas.create_polygon(
                points[0], pad_t + plot_h, *points, points[-2], pad_t + plot_h,
                fill=LINE_COLOR, outline="", stipple="gray25")
            canvas.create_line(*points, fill=LINE_COLOR, width=2)

        avg = sum(samples) / n
        canvas.create_text(
            width - pad_r, pad_t + 2,
            text=f"当前 {format_speed(samples[-1])}   峰值 {format_speed(peak)}   平均 {format_speed(avg)}",
            fill=HIGHLIGHT_COLOR, font=("Arial", 10), anchor="ne")
        canvas.create_text(
            width - pad_r, height - pad_b + 6,
            text=f"最近 {n} 秒", fill=TEXT_COLOR, font=("Arial", 10), anchor="se")

    def _on_close(self):
        """关闭窗口"""
        self._closed = True
        self.destroy()
