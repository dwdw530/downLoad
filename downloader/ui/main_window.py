# -*- coding: utf-8 -*-
"""
GUI主窗口
老王说：界面简单够用就行，别搞那些花里胡哨的！
"""
import customtkinter as ctk
import logging
import os
import subprocess
import threading
import time
import tkinter
from collections import deque
from queue import Empty, SimpleQueue
from tkinter import messagebox, filedialog, PhotoImage, Menu
from typing import Dict
from downloader.core.task_manager import TaskManager
from downloader.utils.file_utils import (format_speed, format_progress_texts, guess_file_category,
                                        FILE_CATEGORY_LABELS)
from downloader.utils.url_utils import extract_clipboard_url, host_from_url, host_is_ignored
from downloader.ui.tray_manager import TrayManager
from downloader.ui.task_detail_window import TaskDetailWindow, get_status_text
from downloader.ui.clipboard_dialog import ClipboardLinkDialog
from downloader.ui.ctk_helpers import SafeToplevelMixin
from downloader.ui.typography import SIDEBAR_HEADING_FONT, SIDEBAR_ITEM_FONT


logger = logging.getLogger(__name__)

# 失败任务一次性重试前，超过这个数量先弹确认，避免误触打成一片
RETRY_FAILED_CONFIRM_THRESHOLD = 10

# 批量操作栏：动作 -> (按钮文案, 宽度)；删除记录只删记录，文件保留
BATCH_ACTIONS = (
    ('pause', '⏸ 暂停', 64),
    ('start', '▶ 开始/继续', 96),
    ('retry', '↻ 重试', 64),
    ('delete_records', '🗑 删除记录', 96),
)
BATCH_ACTION_LABELS = {
    'pause': '暂停', 'start': '开始/继续', 'retry': '重试', 'delete_records': '删除记录',
}


# 侧栏原生控件的配色（与 CustomTkinter 默认主题的 CTkFrame/CTkLabel 对齐）。
# 分类项数以十计且每个主窗口都会重建，用原生 Label 才能避开 CTk 控件的 canvas/字体开销。
SIDEBAR_PALETTE = {
    'light': {'bg': '#dbdbdb', 'fg': '#1a1a1a', 'muted': '#4a4a4a',
              'hover': '#c9c9c9', 'selected': '#b9b9b9'},
    'dark': {'bg': '#2b2b2b', 'fg': '#dce4ee', 'muted': '#a0a0a0',
             'hover': '#3a3a3a', 'selected': '#4f4f4f'},
}


class MainWindow(ctk.CTk):
    """主窗口"""

    def __init__(self, task_manager: TaskManager):
        super().__init__()

        self.task_manager = task_manager
        self.task_widgets = {}  # {task_id: widget}
        self._ui_events = SimpleQueue()
        self._shutdown_dialog_open = False  # 关机确认只能同时开一个，避免多任务同时完成时叠窗
        self._shutdown_pending = False
        self._shutdown_activity = None
        self._loading_tasks = False
        self._batch_add_pending = 0  # 后台批量添加中的链接数（只用于状态栏提示）
        self._selected_task_ids = set()  # 多选集合：仅内存，重启清空
        self._batch_busy = False  # 一批多选操作执行期间禁用再次提交、勾选与全局操作
        self._status_flash = None  # (文本, 截止时间) 状态栏临时提示：如“设置已保存”
        self._last_status_stats = ({'total': 0, 'downloading': 0, 'pending': 0, 'paused': 0,
                                    'completed': 0, 'failed': 0, 'cancelled': 0}, 0.0)
        self._speed_history = {}  # {task_id: deque(maxlen=180)} 速度曲线采样，仅内存不落盘
        self._detail_windows = {}  # {task_id: TaskDetailWindow} 打开中的详情窗
        self._clipboard_dialog = None  # 剪贴板提示浮窗（同时只开一个）
        self._clipboard_last_seen = None  # 上一次见到的剪贴板内容，同一次内容只弹一次

        # 设置窗口
        self.title("daw下载器 v1.0")
        from downloader.ui.tray_manager import icon_path
        window_icon = icon_path()
        self.iconbitmap(default=window_icon)
        # Keep the full-resolution image; Tk's ICO loader blurs it at high DPI.
        self._window_icon_photo = PhotoImage(master=self, file=os.path.splitext(window_icon)[0] + '.png')
        self.iconphoto(True, self._window_icon_photo)

        # 主窗口按屏幕居中：尺寸仍是 900x600（CustomTkinter 会按 DPI 缩放放大），
        # 而 +x+y 是按物理像素原样生效，所以偏移要按缩放换算到物理像素；
        # 尺寸和位置必须写在同一次 geometry 调用里，分开调用会被二次偏移。
        window_width, window_height = 900, 600
        self.update_idletasks()
        scaling = ctk.ScalingTracker.get_window_scaling(self) or 1
        win_w, win_h = round(window_width * scaling), round(window_height * scaling)
        screen_w = int(self.winfo_screenwidth() * scaling)
        screen_h = int(self.winfo_screenheight() * scaling)
        x = max(0, (screen_w - win_w) // 2)
        y = max(0, (screen_h - win_h) // 2)
        self.geometry(f"{window_width}x{window_height}+{x}+{y}")

        # 设置主题（深色/浅色/跟随系统，来自配置）
        ctk.set_appearance_mode(self.task_manager.engine.config.ui_appearance_mode)
        ctk.set_default_color_theme("blue")

        # 左侧分类树当前选中项（all / status:xxx / type:xxx / host:xxx，持久化）
        self._active_category = self.task_manager.engine.config.ui_active_category

        # 创建UI
        self._create_ui()

        # 设置任务管理器回调
        self.task_manager.set_task_added_callback(self._on_task_added)
        self.task_manager.set_task_status_changed_callback(self._on_task_status_changed)
        self.task_manager.engine.set_progress_callback(self._on_task_progress)

        # 加载现有任务
        self._load_existing_tasks()

        # 崩溃/强杀残留的分块临时文件：启动后清一次，不阻塞窗口显示
        self.after(0, self._sweep_orphan_temp)

        # 由主线程处理下载回调，避免暂停时与工作线程互相等待。
        self.after(50, self._drain_ui_events)
        self._start_ui_update_thread()

        # 绑定窗口关闭事件
        self.protocol("WM_DELETE_WINDOW", self._on_window_close)

        # 初始化系统托盘
        self._init_tray()

        # 桌面悬浮条（默认关闭，随配置自动恢复）
        self.float_bar = None
        self.after(200, self._restore_float_bar)

        # 剪贴板监视：启动时先记下现有内容，避免冷启动就弹窗
        try:
            self._clipboard_last_seen = self.clipboard_get()
        except Exception:
            self._clipboard_last_seen = None
        self.after(1000, self._poll_clipboard)

        # 侧栏用原生控件，不会跟着 CTk 自动重绘；这里兜底监听外观模式变化，
        # 不管主题是哪条路径改的，最多 0.5 秒后侧栏颜色都会跟上。
        self._last_appearance_mode = ctk.get_appearance_mode()
        self.after(500, self._watch_appearance_mode)

    def _watch_appearance_mode(self):
        """外观模式变化兜底：确保侧栏原生控件始终跟随当前主题"""
        try:
            if not self.winfo_exists():
                return
            current = ctk.get_appearance_mode()
            if current != self._last_appearance_mode:
                self._last_appearance_mode = current
                self._retheme_sidebar()
            self.after(500, self._watch_appearance_mode)
        except Exception:
            return  # 窗口已销毁，停表

    def _init_tray(self):
        """初始化系统托盘"""
        self.tray_manager = TrayManager("daw下载器")
        self.tray_manager.set_show_window_callback(self._show_from_tray)
        self.tray_manager.set_exit_callback(lambda: self._ui_events.put((self._exit_app, ())))
        self.tray_manager.start()

    def _restore_float_bar(self):
        """按配置恢复悬浮条（启动时延后一点，等主窗口布局稳定再定位）"""
        if self.task_manager.engine.config.ui_float_bar_enabled:
            self._set_float_bar_visible(True)

    def _set_float_bar_visible(self, visible: bool):
        """显示或隐藏桌面悬浮条"""
        config = self.task_manager.engine.config
        if visible:
            if self.float_bar is None:
                from downloader.ui.float_bar import FloatBar
                self.float_bar = FloatBar(
                    self, self.task_manager, config,
                    on_show_window=lambda: self._ui_events.put((self._restore_window, ())),
                    on_pause_all=self._on_pause_all,
                    on_resume_all=self._on_resume_all,
                    on_settings=self._on_settings,
                    on_exit=lambda: self._ui_events.put((self._exit_app, ())),
                    on_hidden=self._on_float_bar_hidden,
                )
            else:
                self.float_bar.show()
        elif self.float_bar is not None:
            self.float_bar.hide()

    def _on_float_bar_hidden(self):
        """悬浮条自己隐藏时同步配置，设置页下次打开不会又勾上"""
        self.task_manager.engine.config.ui_float_bar_enabled = False

    def _show_from_tray(self):
        """从托盘恢复窗口"""
        # 在主线程中执行UI操作
        self._ui_events.put((self._restore_window, ()))

    def _restore_window(self):
        """恢复窗口显示"""
        self.deiconify()  # 取消最小化
        self.lift()  # 置顶
        self.focus_force()  # 获取焦点

    def _minimize_app(self):
        """最小化到托盘（继续后台下载）"""
        try:
            # 如果托盘可用，隐藏到托盘；否则普通最小化
            if hasattr(self, 'tray_manager') and self.tray_manager.available:
                self.withdraw()
            else:
                self.iconify()
        except Exception as e:
            logger.error("最小化失败: %s", e)
            self.iconify()

    def _exit_app(self):
        """退出程序（停止下载并释放资源）"""
        self._exiting = True
        try:
            if hasattr(self, 'browser_bridge'):
                self.browser_bridge.close()
            # 停止托盘
            if hasattr(self, 'tray_manager'):
                self.tray_manager.stop()
            # 悬浮条是独立 Toplevel，不手动销毁会跟着进程残留
            if getattr(self, 'float_bar', None) is not None:
                self.float_bar.destroy()
                self.float_bar = None
            # 退出不清理线程？那就是找骂：ThreadPoolExecutor能把进程吊到天荒地老
            self.task_manager.shutdown()
        except Exception as e:
            logger.error("退出清理失败: %s", e)
        finally:
            # destroy 比 quit 更干脆：关窗口 + 结束mainloop
            try:
                self.destroy()
            except Exception:
                # 已经销毁就算了
                pass

    def _create_ui(self):
        """创建UI组件"""
        # 顶部工具栏
        toolbar = ctk.CTkFrame(self)
        toolbar.pack(fill="x", padx=10, pady=10)

        # 添加任务按钮
        add_btn = ctk.CTkButton(toolbar, text="➕ 添加任务", command=self._on_add_task, width=100)
        add_btn.pack(side="left", padx=5)

        # 暂停全部按钮
        pause_all_btn = ctk.CTkButton(toolbar, text="⏸ 暂停全部", command=self._on_pause_all, width=100)
        pause_all_btn.pack(side="left", padx=5)

        # 继续全部按钮
        resume_all_btn = ctk.CTkButton(toolbar, text="▶ 继续全部", command=self._on_resume_all, width=100)
        resume_all_btn.pack(side="left", padx=5)

        # 重试失败按钮：断网/平台限流后批量恢复，不用一个个点
        retry_failed_btn = ctk.CTkButton(toolbar, text="↻ 重试失败", command=self._on_retry_failed, width=100)
        retry_failed_btn.pack(side="left", padx=5)

        # 批量操作期间这些全局操作也属于冲突入口，一起禁用/恢复
        self._toolbar_global_buttons = [pause_all_btn, resume_all_btn, retry_failed_btn]

        # 设置按钮
        settings_btn = ctk.CTkButton(toolbar, text="⚙ 设置", command=self._on_settings, width=100)
        settings_btn.pack(side="right", padx=5)

        # 历史按钮
        history_btn = ctk.CTkButton(toolbar, text="📜 历史", command=self._on_history, width=100)
        history_btn.pack(side="right", padx=5)

        # 主体：左侧分类树 + 右侧任务区
        content = ctk.CTkFrame(self, fg_color="transparent")
        content.pack(fill="both", expand=True, padx=10, pady=(0, 10))

        # 分类项数量有限（状态+类型+站点），用普通 Frame 即可；
        # 侧栏不用 CTkScrollableFrame：它会在每次创建/销毁窗口时反复注册全局鼠标滚轮绑定，
        # 大量测试窗口累积后会拖慢整个 Tk 事件循环（实测让 GUI 测试从 120s 劣化到 530s）。
        self.sidebar_frame = ctk.CTkFrame(content, width=240)
        self.sidebar_frame.pack(side="left", fill="y", padx=(0, 8))
        self.sidebar_frame.pack_propagate(False)

        right_frame = ctk.CTkFrame(content, fg_color="transparent")
        right_frame.pack(side="left", fill="both", expand=True)

        # 左侧分类：状态 / 类型 / 站点
        self._build_sidebar()

        # 过滤行：搜索 + 隐藏已完成
        filter_frame = ctk.CTkFrame(right_frame, fg_color="transparent")
        filter_frame.pack(fill="x", pady=(0, 5))

        self.search_entry = ctk.CTkEntry(filter_frame, width=240, placeholder_text="搜索文件名或链接")
        self.search_entry.pack(side="left")
        self.search_entry.bind("<KeyRelease>", lambda _event: self._apply_task_filter())

        config = self.task_manager.engine.config
        self.hide_completed_var = ctk.BooleanVar(value=config.ui_hide_completed)
        hide_completed_check = ctk.CTkCheckBox(
            filter_frame,
            text="隐藏已完成",
            variable=self.hide_completed_var,
            command=self._on_hide_completed_toggle,
        )
        hide_completed_check.pack(side="left", padx=10)

        self.filter_hint_label = ctk.CTkLabel(filter_frame, text="", font=("Arial", 11), text_color="gray")
        self.filter_hint_label.pack(side="right", padx=5)

        # 多选批量操作栏：固定单行紧凑布局，不随选中数量变化跳动
        self.batch_bar = ctk.CTkFrame(right_frame, fg_color="transparent")
        self.batch_bar.pack(fill="x", pady=(0, 5))

        self.batch_select_all_btn = ctk.CTkButton(
            self.batch_bar, text="全选当前列表", width=96, command=self._on_batch_select_all)
        self.batch_select_all_btn.pack(side="left", padx=(0, 6))

        self.selection_label = ctk.CTkLabel(self.batch_bar, text="已选 0", width=64)
        self.selection_label.pack(side="left", padx=(0, 6))

        self.batch_action_buttons = {}
        for action, text, width in BATCH_ACTIONS:
            button = ctk.CTkButton(self.batch_bar, text=text, width=width,
                                   command=lambda a=action: self._on_batch_action(a))
            button.pack(side="left", padx=(0, 4))
            self.batch_action_buttons[action] = button

        self.batch_clear_btn = ctk.CTkButton(
            self.batch_bar, text="清空", width=56, command=self._on_batch_clear_selection)
        self.batch_clear_btn.pack(side="left", padx=(0, 4))

        self._refresh_selection_ui()

        # 任务列表区域（使用Scrollable Frame）
        self.task_list_frame = ctk.CTkScrollableFrame(right_frame, label_text="下载任务列表")
        self.task_list_frame.pack(fill="both", expand=True)

        # 空态提示：由 _apply_task_filter 统一控制显示
        self.empty_label = ctk.CTkLabel(self.task_list_frame, text="暂无任务", text_color="gray")
        self.empty_label.pack(pady=20)

        # 底部状态栏
        self.status_bar = ctk.CTkFrame(self, height=40)
        self.status_bar.pack(fill="x", padx=10, pady=10)

        self.status_label = ctk.CTkLabel(self.status_bar, text="总速度: 0 KB/s | 剩余任务: 0")
        self.status_label.pack(side="left", padx=10)

    # ==================== 左侧分类树 ====================

    def _build_sidebar(self):
        """分类树：状态 / 类型 / 站点（站点分组随任务动态重建）

        分类项用原生 tkinter.Label：侧栏项多，而每个主窗口都会重建它们。
        CTk 控件自带的 canvas / 字体 / hover 动画在反复创建上千次后会拖慢整个
        Tk 事件循环（实测把 GUI 测试从 120s 劣化到 530s），原生 Label 没这个问题。
        """
        self.category_buttons = {}
        self.category_labels = {}
        self._host_buttons = {}
        self._known_hosts = []
        self._sidebar_heading_labels = []
        self._sidebar_colors_cache = self._sidebar_palette()

        self._create_sidebar_heading("分类")
        self._create_sidebar_heading("状态")
        for key, text in (("all", "全部"), ("status:downloading", "下载中"),
                          ("status:pending", "等待中"), ("status:completed", "已完成"),
                          ("status:failed", "失败")):
            self._create_category_button(self.sidebar_frame, key, text)

        self._create_sidebar_heading("类型")
        for category, label in FILE_CATEGORY_LABELS.items():
            self._create_category_button(self.sidebar_frame, f"type:{category}", label)

        self._create_sidebar_heading("站点")
        self.host_section_frame = tkinter.Frame(self.sidebar_frame, bd=0, highlightthickness=0,
                                                bg=self._sidebar_colors_cache['bg'])
        self.host_section_frame.pack(fill="x")

    @staticmethod
    def _sidebar_palette() -> dict:
        """取当前主题对应的侧栏配色（原生控件不吃 CTk 的 (light, dark) 元组）"""
        dark = ctk.get_appearance_mode() == 'Dark'
        return dict(SIDEBAR_PALETTE['dark' if dark else 'light'])

    def _create_sidebar_heading(self, text: str):
        """侧栏分组标题（原生 Label 用磅值，字号与分类项同档）"""
        colors = self._sidebar_colors_cache
        label = tkinter.Label(self.sidebar_frame, text=text, anchor="w", padx=10, pady=3,
                              bg=colors['bg'], fg=colors['muted'], font=SIDEBAR_HEADING_FONT)
        label.pack(fill="x", pady=(10, 1))
        self._sidebar_heading_labels.append(label)
        return label

    def _create_category_button(self, parent, key: str, text: str):
        """创建一个分类项（原生 Label，点击过滤，hover/选中换底色）"""
        colors = self._sidebar_colors_cache
        item = tkinter.Label(parent, text=text, anchor="w", padx=12, pady=6,
                             bg=colors['bg'], fg=colors['fg'], cursor="hand2",
                             font=SIDEBAR_ITEM_FONT)
        item.pack(fill="x")
        item.bind("<Button-1>", lambda _event, k=key: self._on_category_select(k))
        item.bind("<Enter>", lambda _event, widget=item: self._on_category_hover(widget, True))
        item.bind("<Leave>", lambda _event, widget=item: self._on_category_hover(widget, False))
        self.category_buttons[key] = item
        self.category_labels[key] = text
        self._set_category_selected(key, key == self._active_category)
        return item

    def _on_category_hover(self, widget, entering: bool):
        """hover 只影响未选中项，免得选中态被鼠标扫过就丢了"""
        key = next((key for key, item in self.category_buttons.items() if item is widget), None)
        if key is None or key == self._active_category:
            return
        widget.configure(bg=self._sidebar_colors_cache['hover' if entering else 'bg'])

    def _set_category_selected(self, key: str, selected: bool):
        """分类项选中态：选中项用实底色，其余透明"""
        item = self.category_buttons.get(key)
        if item is None or not item.winfo_exists():
            return
        item.configure(bg=self._sidebar_colors_cache['selected' if selected else 'bg'])

    def _retheme_sidebar(self):
        """主题切换后刷新侧栏颜色（CTk 控件会自己重绘，原生控件不会）"""
        self._sidebar_colors_cache = self._sidebar_palette()
        self._last_appearance_mode = ctk.get_appearance_mode()
        colors = self._sidebar_colors_cache
        for label in self._sidebar_heading_labels:
            if label.winfo_exists():
                label.configure(bg=colors['bg'], fg=colors['muted'])
        if getattr(self, 'host_section_frame', None) is not None and self.host_section_frame.winfo_exists():
            self.host_section_frame.configure(bg=colors['bg'])
        for key, item in self.category_buttons.items():
            if item.winfo_exists():
                item.configure(bg=colors['bg'], fg=colors['fg'])
                self._set_category_selected(key, key == self._active_category)

    def _on_category_select(self, key: str):
        """切换分类：持久化选中项并即时过滤"""
        if key == self._active_category:
            return
        self._active_category = key
        config = self.task_manager.engine.config
        config.ui_active_category = key
        config.save()
        for existing_key in list(self.category_buttons):
            self._set_category_selected(existing_key, existing_key == key)
        self._apply_task_filter()

    def _refresh_sidebar(self):
        """同步分类树的计数与站点分组（任务增删、状态变更后调用）"""
        self._refresh_host_section()

        counts = {'all': len(self.task_widgets)}
        for widgets in self.task_widgets.values():
            status = widgets.get('status')
            status_key = None
            if status == 'downloading':
                status_key = 'status:downloading'
            elif status == 'pending':
                status_key = 'status:pending'
            elif status == 'completed':
                status_key = 'status:completed'
            elif status in ('failed', 'verify_failed', 'cancelled'):
                status_key = 'status:failed'
            if status_key:
                counts[status_key] = counts.get(status_key, 0) + 1

            type_key = f"type:{widgets.get('category') or 'other'}"
            counts[type_key] = counts.get(type_key, 0) + 1
            if widgets.get('host'):
                host_key = f"host:{widgets['host']}"
                counts[host_key] = counts.get(host_key, 0) + 1

        for key, button in self.category_buttons.items():
            label = self.category_labels.get(key)
            if label is None or not button.winfo_exists():
                continue
            button.configure(text=f"{label} ({counts.get(key, 0)})")

    def _refresh_host_section(self):
        """站点分组按当前任务动态重建；选中的站点没任务了则回退到「全部」"""
        hosts = sorted({widgets['host'] for widgets in self.task_widgets.values() if widgets.get('host')})
        if hosts == self._known_hosts:
            return
        self._known_hosts = hosts

        for button in self._host_buttons.values():
            button.destroy()
        self._host_buttons = {}
        for key in [key for key in self.category_buttons if key.startswith('host:')]:
            del self.category_buttons[key]
            self.category_labels.pop(key, None)

        for host in hosts:
            self._host_buttons[host] = self._create_category_button(
                self.host_section_frame, f"host:{host}", host)

        if not self._loading_tasks and self._active_category.startswith('host:') and self._active_category not in self.category_buttons:
            # 选中的站点已经没任务了，回退到「全部」，别让列表一直空着
            self._on_category_select('all')

    def _on_add_task(self):
        """添加任务对话框（支持多行批量）"""
        dialog = AddTaskDialog(self)
        self.wait_window(dialog)
        self._process_add_dialog(dialog)

    def _process_add_dialog(self, dialog):
        """添加框确认后的统一处理（工具栏入口与剪贴板浮窗共用）"""
        if not dialog.confirmed:
            return

        urls, skipped = self._dedupe_urls(dialog.urls)
        if not urls:
            return

        # 单链接也走后台：每条链接都要发探测请求，同步跑会把界面卡死
        self._add_tasks_in_background(urls, dialog, skipped)

    @staticmethod
    def _dedupe_urls(urls) -> tuple:
        """同批内按顺序去重，返回 (去重后列表, 跳过条数)"""
        unique = []
        seen = set()
        skipped = 0
        for url in urls or ():
            if url in seen:
                skipped += 1
                continue
            seen.add(url)
            unique.append(url)
        return unique, skipped

    def _add_tasks_in_background(self, urls, dialog, skipped: int = 0):
        """批量添加走后台：每条链接都要发探测请求，同步会把界面卡死"""
        save_dir = dialog.save_dir
        expected_hash = dialog.expected_hash
        hash_type = dialog.hash_type
        start_later = dialog.start_later
        self._batch_add_pending += len(urls)

        def worker():
            success = 0
            failures = []
            try:
                for url in urls:
                    reason = ""
                    try:
                        task_id = self.task_manager.add_task(
                            url=url,
                            save_path=save_dir,
                            expected_hash=expected_hash,
                            hash_type=hash_type,
                            start_later=start_later,
                        )
                    except Exception as e:
                        task_id = None
                        reason = str(e)
                    if task_id:
                        success += 1
                    else:
                        # 引擎会把这次失败的分类原因写进线程本地槽位，比兜底文案具体得多
                        reason = reason or (self.task_manager.last_add_detail
                                            or self.task_manager.last_add_error
                                            or "无法获取文件大小或链接不可用")
                        failures.append(f"{url}（{reason}）")
            finally:
                self._batch_add_pending -= len(urls)
                # 日志不记链接，只记数量，避免把 URL 写进日志
                logger.info("批量添加完成: 成功=%d 失败=%d 跳过=%d", success, len(failures), skipped)
                self._ui_events.put((self._show_add_summary, (success, failures, skipped)))

        threading.Thread(target=worker, daemon=True).start()

    def _show_add_summary(self, success: int, failures, skipped: int):
        """批量添加结果汇总；单条成功只在状态栏提示，不再弹框打断"""
        if success == 1 and not failures and not skipped:
            self._flash_status("任务添加成功")
            if self._shutdown_pending and self.task_manager.engine.config.after_download['shutdown']:
                self._schedule_shutdown_if_idle()
            return

        lines = [f"成功添加 {success} 个任务"]
        if skipped:
            lines.append(f"已跳过 {skipped} 条重复链接")
        if failures:
            lines.append(f"失败 {len(failures)} 个：")
            lines.extend(f"  • {item}" for item in failures[:3])
            if len(failures) > 3:
                lines.append(f"  … 其余 {len(failures) - 3} 个未列出")
        text = "\n".join(lines)
        if success:
            messagebox.showinfo("添加结果", text)
        else:
            messagebox.showerror("添加结果", text)
        if self._shutdown_pending and self.task_manager.engine.config.after_download['shutdown']:
            self._schedule_shutdown_if_idle()

    def _on_pause_all(self):
        """暂停全部任务"""
        if self._task_actions_blocked():
            return
        count = self.task_manager.pause_all()
        messagebox.showinfo("提示", f"已暂停 {count} 个任务")

    def _on_resume_all(self):
        """继续全部任务"""
        if self._task_actions_blocked():
            return
        count = self.task_manager.resume_all()
        messagebox.showinfo("提示", f"已继续 {count} 个任务")

    def _on_retry_failed(self):
        """重试所有失败任务；数量多时先确认，避免误触"""
        if self._task_actions_blocked():
            return
        failed_tasks = self.task_manager.db.get_all_tasks(status='failed')
        if not failed_tasks:
            self._flash_status("没有失败的任务")
            return
        if len(failed_tasks) >= RETRY_FAILED_CONFIRM_THRESHOLD:
            if not messagebox.askyesno("确认", f"将重试 {len(failed_tasks)} 个失败任务，继续？"):
                return

        def worker():
            # 每条重试都要重新探测资源，同步跑会把界面卡死
            try:
                started, skipped = self.task_manager.retry_failed()
            except Exception as error:
                logger.warning("批量重试失败任务出错: %s", error)
                started, skipped = 0, len(failed_tasks)
            self._ui_events.put((self._show_retry_failed_result, (started, skipped)))

        threading.Thread(target=worker, daemon=True).start()

    def _show_retry_failed_result(self, started: int, skipped: int):
        """批量重试结果：状态栏提示，不弹框打断"""
        message = f"已重试 {started} 个任务"
        if skipped:
            message += f"，{skipped} 个未能启动"
        self._flash_status(message)

    def _on_settings(self):
        """打开设置对话框"""
        from downloader.ui.settings_dialog import SettingsDialog
        dialog = SettingsDialog(
            self,
            self.task_manager.engine.config,
            on_save_callback=self._on_settings_saved,
        )
        self.wait_window(dialog)

    def _on_settings_saved(self, settings: Dict):
        """设置保存回调（让运行时配置立即生效）"""

        def apply_runtime_settings():
            # 回调在 settings_dialog destroy 之前触发，如果这里直接碰主窗口控件，
            # 会撞上 CTk 延迟标题栏回调的时序问题；兜一层 after，
            # 等设置框完全销毁、事件队列排空后再同步运行时状态
            self.after(30, self._apply_saved_settings, settings)

        # 兜一层after，保证控件操作和队列调度都在主线程触发
        self.after(0, apply_runtime_settings)

    def _apply_saved_settings(self, settings: Dict):
        """应用设置保存后的运行时同步（在设置框销毁后执行）"""
        config = self.task_manager.engine.config

        # 配置对象这里再显式刷一次，避免后续维护把保存顺序改崩了
        if 'download_dir' in settings:
            config.download_dir = settings['download_dir']
        if 'auto_classify_downloads' in settings:
            config.auto_classify_downloads = settings['auto_classify_downloads']
        if 'temp_dir' in settings:
            old_temp_dir = config.temp_dir
            config.temp_dir = settings['temp_dir']
            if os.path.normcase(old_temp_dir) != os.path.normcase(config.temp_dir):
                # 新任务直接用新目录；没有断点数据的排队任务同步改路径，
                # 已有字节的任务保留原目录，避免移动大文件或破坏断点
                moved = self.task_manager.engine.retarget_temp_dir(config.temp_dir)
                logger.info("临时目录已切换: %s（同步排队任务 %s 个）", config.temp_dir, moved)
        if 'thread_count' in settings:
            config.thread_count = settings['thread_count']
        if 'timeout' in settings:
            config.set('timeout', settings['timeout'])
        if 'speed_limit' in settings:
            # 全局限速：所有普通文件任务共用一个令牌桶，运行中立即生效
            self.task_manager.engine.set_speed_limit(settings['speed_limit'])

        proxy_cfg = settings.get('proxy')
        if isinstance(proxy_cfg, dict):
            config.set_proxy(
                proxy_cfg.get('enabled', False),
                proxy_cfg.get('http', ''),
                proxy_cfg.get('https', ''),
            )

        # 并发数不马上同步，队列就会装死给你看，这里必须立刻刷新
        max_concurrent = settings.get('max_concurrent_downloads')
        if max_concurrent is not None:
            self.task_manager.set_max_concurrent(max_concurrent)

        # 恢复默认设置可能把隐藏已完成改回关，勾选框得跟着配置走
        self.hide_completed_var.set(config.ui_hide_completed)
        # 主题与分类选中项同理：恢复默认后界面要跟配置一致
        ctk.set_appearance_mode(config.ui_appearance_mode)
        self._retheme_sidebar()
        if getattr(self, 'float_bar', None) is not None:
            self.float_bar.refresh_theme()
        # 悬浮条开关立即生效
        self._set_float_bar_visible(config.ui_float_bar_enabled)
        # 悬浮条样式（完整/迷你）也随设置即时切换
        if getattr(self, 'float_bar', None) is not None:
            self.float_bar.set_mode(config.ui_float_bar_mode, persist=False)
        self._flash_status("设置已保存")
        if self._active_category != config.ui_active_category:
            self._active_category = config.ui_active_category
            for key in list(self.category_buttons):
                self._set_category_selected(key, key == self._active_category)
        self._apply_task_filter()

    def _on_history(self):
        """打开下载历史对话框"""
        from downloader.ui.history_dialog import HistoryDialog
        dialog = HistoryDialog(self, self.task_manager.db)
        self.wait_window(dialog)

    def _sweep_orphan_temp(self):
        """启动时清理崩溃/强杀残留的无主分块临时文件"""
        try:
            removed, _freed = self.task_manager.engine.sweep_orphan_temp()
        except Exception as error:
            # 清扫是锦上添花，出错不能影响程序启动
            logger.warning("临时文件清扫失败: %s", error)
            return
        if removed:
            self._flash_status(f"已清理 {removed} 个残留临时文件")

    def _load_existing_tasks(self):
        """加载现有任务"""
        # 加载期间不能用半份列表判断保存的站点是否存在。
        self._loading_tasks = True
        tasks = self.task_manager.get_all_tasks()
        for task in tasks:
            task_for_ui = dict(task)

            # 启动时数据库里残留的downloading其实没在跑，UI别装蒜，直接按已暂停展示
            if task_for_ui.get('status') == 'downloading':
                # 顺手把后端状态也拍平为paused，避免按钮显示能点、点了却没反应
                self.task_manager.db.update_task_status(task_for_ui['task_id'], 'paused')
                task_for_ui['status'] = 'paused'

            self._add_task_widget(task_for_ui)

            # 艹，重启后必须立刻回填真实进度，不然UI全是0看起来像任务废了
            self._update_task_progress(
                task_for_ui['task_id'],
                int(task_for_ui.get('downloaded_size') or 0),
                int(task_for_ui.get('total_size') or 0),
                float(task_for_ui.get('speed') or 0),
            )

        self._loading_tasks = False
        self._refresh_sidebar()
        if self._active_category.startswith('host:') and self._active_category not in self.category_buttons:
            self._on_category_select('all')
        self._apply_task_filter()

    def _add_task_widget(self, task: Dict):
        """添加任务UI组件"""
        task_id = task['task_id']

        # 创建任务卡片
        task_frame = ctk.CTkFrame(self.task_list_frame)
        task_frame.pack(fill="x", pady=5)

        # 文件名行：左侧勾选框只切选择，不触发双击详情/下载
        filename_row = ctk.CTkFrame(task_frame, fg_color="transparent")
        filename_row.pack(fill="x", padx=10, pady=5)

        select_var = ctk.BooleanVar(value=False)
        select_box = ctk.CTkCheckBox(
            filename_row, text="", width=24, variable=select_var,
            command=lambda: self._on_task_select_toggle(task_id))
        select_box.pack(side="left", padx=(0, 6))

        filename_label = ctk.CTkLabel(filename_row, text=task['filename'], font=("Arial", 14, "bold"))
        filename_label.pack(side="left", anchor="w")

        # 进度条和信息行
        info_frame = ctk.CTkFrame(task_frame, fg_color="transparent")
        info_frame.pack(fill="x", padx=10, pady=5)

        # 文件位置按钮（一直显示，别搞“下载中没有入口”这种反人类设计）
        location_btn = ctk.CTkButton(
            info_frame,
            text="文件位置",
            width=110,
            command=lambda: self._on_open_location(task_id),
        )
        location_btn.pack(side="right", padx=5)

        # 进度条（拉满剩余宽度，窗口缩放不露出空档）
        progress_bar = ctk.CTkProgressBar(info_frame, width=300)
        progress_bar.pack(side="left", fill="x", expand=True, padx=5)
        progress_bar.set(0)

        # 进度百分比
        progress_label = ctk.CTkLabel(info_frame, text="0%", width=60)
        progress_label.pack(side="left", padx=5)

        # 详情行：已下载/总大小、剩余时间、速度、状态
        detail_frame = ctk.CTkFrame(task_frame, fg_color="transparent")
        detail_frame.pack(fill="x", padx=10, pady=(0, 5))

        # 宽度写死，不然数字一跳整行都在抖
        size_label = ctk.CTkLabel(detail_frame, text="已下载 0 B / 未知", width=220, anchor="w")
        size_label.pack(side="left", padx=5)

        eta_label = ctk.CTkLabel(detail_frame, text="剩余 --", width=140, anchor="w")
        eta_label.pack(side="left", padx=5)

        speed_label = ctk.CTkLabel(detail_frame, text="0 KB/s", width=110, anchor="w")
        speed_label.pack(side="left", padx=5)

        status_label = ctk.CTkLabel(detail_frame, text=self._get_status_text(task['status']), width=80, anchor="w")
        status_label.pack(side="left", padx=5)

        # 按钮区域
        button_frame = ctk.CTkFrame(task_frame, fg_color="transparent")
        button_frame.pack(fill="x", padx=10, pady=5)

        # 操作按钮（统一走状态配置，避免初始化和回调逻辑打架）
        action_btn = ctk.CTkButton(button_frame, text="▶ 开始", width=80)
        self._configure_action_button(task_id, task['status'], action_btn)

        action_btn.pack(side="left", padx=5)

        # 取消按钮
        cancel_btn = ctk.CTkButton(button_frame, text="✗ 取消", width=80,
                                   command=lambda: self._on_cancel_task(task_id))
        cancel_btn.configure(state='disabled' if task['status'] == 'completed' else 'normal')
        cancel_btn.pack(side="left", padx=5)

        # 删除按钮
        delete_btn = ctk.CTkButton(button_frame, text="🗑 删除", width=80,
                                   command=lambda: self._on_delete_task(task_id))
        delete_btn.pack(side="left", padx=5)

        # 双击卡片空白处打开详情窗（按钮区不绑，避免和单击命令打架）
        self._bind_task_double_click(task_frame, task_id)
        # 右键菜单：开始/暂停、打开、复制链接、上移/置顶、删除
        self._bind_task_right_click(task_frame, task_id)

        # 保存widget引用
        self.task_widgets[task_id] = {
            'frame': task_frame,
            'progress_bar': progress_bar,
            'progress_label': progress_label,
            'size_label': size_label,
            'eta_label': eta_label,
            'speed_label': speed_label,
            'status_label': status_label,
            'action_btn': action_btn,
            'cancel_btn': cancel_btn,
            'delete_btn': delete_btn,
            'location_btn': location_btn,
            'select_box': select_box,
            'select_var': select_var,
            # 过滤用的缓存，避免每次输入都查库
            'filename': task.get('filename') or '',
            'filename_label': filename_label,
            'url': task.get('url') or '',
            'status': task.get('status') or 'pending',
            # 分类树用：文件类型与站点；visible 由 _apply_task_filter 维护
            'category': guess_file_category(task.get('filename') or ''),
            'host': host_from_url(task.get('url') or ''),
            'notice': '',
            'visible': True,
        }

        self._refresh_task_controls(task_id)
        self._apply_task_filter()
        self._refresh_sidebar()

    def _bind_task_double_click(self, widget, task_id: str):
        """递归绑定双击打开详情窗；按钮/勾选框跳过，避免和单击命令打架"""
        if isinstance(widget, (ctk.CTkButton, ctk.CTkCheckBox)):
            return
        widget.bind("<Double-Button-1>", lambda _event, tid=task_id: self._on_open_detail(tid))
        for child in widget.winfo_children():
            self._bind_task_double_click(child, task_id)

    def _on_open_detail(self, task_id: str):
        """双击卡片打开任务详情窗（速度曲线），已开则置前"""
        window = self._detail_windows.get(task_id)
        if window is not None:
            if window.winfo_exists():
                window.lift()
                window.focus()
                return
            del self._detail_windows[task_id]

        widgets = self.task_widgets.get(task_id)
        if not widgets:
            return
        task = self.task_manager.get_task(task_id)
        if not task:
            return
        # 卡片缓存的状态比库里新鲜（状态回调先改缓存），详情窗首屏用它
        task['status'] = widgets.get('status') or task.get('status')
        history = self._speed_history.get(task_id)
        if history is None:
            history = deque(maxlen=180)
            self._speed_history[task_id] = history
        self._detail_windows[task_id] = TaskDetailWindow(self, task, history)

    def _push_detail_update(self, task_id: str, downloaded_size: int, total_size: int, speed: float):
        """把进度推给已打开的详情窗（窗口已关则顺手清理引用）"""
        window = self._detail_windows.get(task_id)
        if window is None:
            return
        if window.winfo_exists():
            window.update_progress(downloaded_size, total_size, speed)
        else:
            del self._detail_windows[task_id]

    def _push_detail_status(self, task_id: str, status: str):
        """把状态变更推给已打开的详情窗"""
        window = self._detail_windows.get(task_id)
        if window is None:
            return
        if window.winfo_exists():
            window.update_status(status)
        else:
            del self._detail_windows[task_id]

    # ==================== 剪贴板监视 ====================

    def _poll_clipboard(self):
        """剪贴板监视：每秒轮询一次，发现新链接弹浮窗（异常全吞，别影响下载）"""
        try:
            if not self.winfo_exists():
                return
            self.after(1000, self._poll_clipboard)
        except Exception:
            return  # 窗口已销毁，停表
        try:
            self._check_clipboard_once()
        except Exception as e:
            logger.debug("剪贴板检查失败: %s", e)

    def _check_clipboard_once(self):
        """检查一次剪贴板：新链接且不在抑制/忽略场景才弹浮窗"""
        config = self.task_manager.engine.config
        if not config.clipboard_monitor_enabled:
            return
        try:
            text = self.clipboard_get()
        except Exception:
            return  # 剪贴板里是图片/文件等非文本内容，静默跳过
        url = extract_clipboard_url(text)
        if not url:
            return
        if text == self._clipboard_last_seen:
            return  # 同一次内容只弹一次
        # 无论弹不弹都记为已见：托盘隐藏/对话框打开期间复制的链接不补弹
        self._clipboard_last_seen = text
        if self._clipboard_suppressed():
            return
        if host_is_ignored(url, config.clipboard_ignore_hosts):
            return
        self._show_clipboard_popup(url)

    def _clipboard_suppressed(self) -> bool:
        """主窗口隐藏（托盘）或有模态对话框持 grab 时不弹，避免打扰"""
        if not self.winfo_viewable():
            return True
        if self.grab_current() is not None:
            return True
        return False

    def _show_clipboard_popup(self, url: str):
        """弹出剪贴板链接提示浮窗（已开则原地换链接）"""
        if self._clipboard_dialog is not None and self._clipboard_dialog.winfo_exists():
            self._clipboard_dialog.update_url(url)
            return
        self._clipboard_dialog = ClipboardLinkDialog(
            self, url,
            on_add=self._on_clipboard_add,
            on_ignore_site=self._on_clipboard_ignore_site,
        )

    def _on_clipboard_add(self, url: str):
        """浮窗「添加任务」：打开标准添加框并预填链接，走既有确认流程"""
        dialog = AddTaskDialog(self, initial_url=url)
        self.wait_window(dialog)
        self._process_add_dialog(dialog)

    def _on_clipboard_ignore_site(self, url: str):
        """浮窗「忽略此站点」：域名写入忽略列表并持久化"""
        host = host_from_url(url)
        if not host:
            return
        config = self.task_manager.engine.config
        hosts = list(config.clipboard_ignore_hosts)
        if host in hosts:
            return
        hosts.append(host)
        config.clipboard_ignore_hosts = hosts
        if config.save():
            logger.info("剪贴板监视已忽略站点: %s", host)
        else:
            logger.error("忽略站点写入配置失败: %s", host)

    # ==================== 多选与批量操作 ====================

    def _on_task_select_toggle(self, task_id: str):
        """勾选框只改选择集合，不联网、不改任务状态"""
        widgets = self.task_widgets.get(task_id)
        if widgets is None:
            return
        if self._batch_busy:
            # 忙碌时勾选框已禁用，兜底把视觉状态还原成真实选择
            widgets['select_var'].set(task_id in self._selected_task_ids)
            return
        if widgets['select_var'].get():
            self._selected_task_ids.add(task_id)
        else:
            self._selected_task_ids.discard(task_id)
        self._refresh_selection_ui()

    def _on_batch_select_all(self):
        """全选当前分类/搜索/隐藏已完成交集，包含滚动视口外的匹配卡片"""
        if self._batch_busy:
            return
        for task_id, widgets in self.task_widgets.items():
            if widgets.get('visible', True):
                self._selected_task_ids.add(task_id)
                widgets['select_var'].set(True)
        self._refresh_selection_ui()

    def _on_batch_clear_selection(self):
        """清空选择，不动任务状态"""
        if self._batch_busy:
            return
        for task_id in list(self._selected_task_ids):
            widgets = self.task_widgets.get(task_id)
            if widgets is not None:
                widgets['select_var'].set(False)
        self._selected_task_ids.clear()
        self._refresh_selection_ui()

    def _prune_selection(self):
        """筛掉或已删除的任务不能保留不可见选择；切回筛选也不恢复"""
        stale = self._selected_task_ids - {
            task_id for task_id, widgets in self.task_widgets.items()
            if widgets.get('visible', True)
        }
        if not stale:
            return
        for task_id in stale:
            self._selected_task_ids.discard(task_id)
            widgets = self.task_widgets.get(task_id)
            if widgets is not None:
                widgets['select_var'].set(False)

    def _refresh_selection_ui(self):
        """同步选中计数与按钮可用性（无选择时动作禁用，选择入口仍可用）"""
        if getattr(self, 'selection_label', None) is None:
            return
        count = len(self._selected_task_ids)
        self.selection_label.configure(text=f"已选 {count}")
        actions_state = 'disabled' if (not count or self._batch_busy) else 'normal'
        for button in self.batch_action_buttons.values():
            button.configure(state=actions_state)
        self.batch_select_all_btn.configure(state='disabled' if self._batch_busy else 'normal')
        self.batch_clear_btn.configure(
            state='disabled' if (not count or self._batch_busy) else 'normal')

    def _task_actions_blocked(self):
        """同时保护按钮、右键旧菜单命令和其他直接调用入口。"""
        if getattr(self, '_exiting', False):
            return True
        if self._batch_busy:
            self._flash_status('已有批量操作正在执行')
            return True
        return False

    def _refresh_task_controls(self, task_id):
        widgets = self.task_widgets[task_id]
        busy = self._batch_busy
        self._configure_action_button(task_id, widgets.get('status'), widgets['action_btn'])
        if busy:
            widgets['action_btn'].configure(state='disabled')
        widgets['cancel_btn'].configure(
            state='disabled' if busy or widgets.get('status') == 'completed' else 'normal')
        widgets['delete_btn'].configure(state='disabled' if busy else 'normal')
        widgets['select_box'].configure(state='disabled' if busy else 'normal')

    def _set_batch_busy(self, busy: bool):
        """批量执行期间禁用再次提交、勾选与冲突的单任务/全局操作"""
        self._batch_busy = bool(busy)
        if not self.winfo_exists():
            return
        state = 'disabled' if busy else 'normal'
        for button in self._toolbar_global_buttons:
            try:
                button.configure(state=state)
            except Exception:
                pass
        for task_id in self.task_widgets:
            self._refresh_task_controls(task_id)
        self._refresh_selection_ui()

    def _on_batch_action(self, action: str):
        """按当前列表顺序复制 ID 快照，后台执行；提交时范围不再随筛选/新增变化"""
        if self._task_actions_blocked() or action not in BATCH_ACTION_LABELS:
            return
        # task_widgets 的顺序就是列表顺序，快照只在 Tk 主线程读一次
        task_ids = [task_id for task_id in self.task_widgets if task_id in self._selected_task_ids]
        if not task_ids:
            return

        if action == 'delete_records':
            names = [self.task_widgets[task_id].get('filename') or '' for task_id in task_ids[:3]]
            lines = [f"确定删除选中的 {len(task_ids)} 条下载记录吗？"]
            lines.extend(f"  • {name}" for name in names)
            if len(task_ids) > len(names):
                lines.append(f"  … 其余 {len(task_ids) - len(names)} 条未列出")
            lines.append("下载好的文件会保留，不做删除。")
            if not messagebox.askyesno("删除记录", "\n".join(lines)):
                return

        # UI 点击立即占忙碌名额，worker 启动前连点也不会重复提交
        if not self.task_manager.begin_batch_operation(action):
            self._flash_status("已有批量操作正在执行")
            return
        self._set_batch_busy(True)

        def worker():
            try:
                result = self.task_manager.batch_operate(task_ids, action, reserved=True)
            except Exception:
                logger.warning("批量操作失败: %s", action)
                result = {'action': action, 'total': len(task_ids), 'succeeded': [],
                          'skipped': [], 'failed': [{'task_id': task_id, 'reason': 'error'}
                                                    for task_id in task_ids]}
            self._ui_events.put((self._on_batch_result, (result,)))

        try:
            threading.Thread(target=worker, daemon=True).start()
        except Exception:
            self.task_manager.end_batch_operation()
            self._on_batch_result({'action': action, 'succeeded': [], 'skipped': [],
                                   'failed': [{'task_id': task_id, 'reason': 'worker'}
                                              for task_id in task_ids]})

    def _on_batch_result(self, result: Dict):
        """批量结果回 Tk：汇总成功/跳过/失败，单项失败不阻断其余"""
        if getattr(self, '_exiting', False):
            return
        action = result.get('action')
        succeeded = result.get('succeeded') or []
        if action == 'delete_records':
            for task_id in succeeded:
                self._remove_task_ui(task_id)
        self._set_batch_busy(False)
        self._apply_task_filter()
        self._refresh_sidebar()
        self._flash_status(
            f"{BATCH_ACTION_LABELS.get(action, action)}：成功 {len(succeeded)}，"
            f"跳过 {len(result.get('skipped') or [])}，失败 {len(result.get('failed') or [])}")
        if self._shutdown_pending and self.task_manager.engine.config.after_download['shutdown']:
            self._schedule_shutdown_if_idle()

    def _remove_task_ui(self, task_id: str):
        """成功删记录后清理卡片、详情窗、速度历史与选择（不动实际文件）"""
        window = self._detail_windows.pop(task_id, None)
        if window is not None and window.winfo_exists():
            window.destroy()
        self._speed_history.pop(task_id, None)
        self._selected_task_ids.discard(task_id)
        widgets = self.task_widgets.pop(task_id, None)
        if widgets is not None:
            widgets['frame'].destroy()

    def _configure_action_button(self, task_id: str, status: str, action_btn: ctk.CTkButton):
        """根据任务状态配置操作按钮"""
        if status == 'downloading':
            action_btn.configure(text="⏸ 暂停", command=lambda: self._on_pause_task(task_id), state="normal")
        elif status == 'paused':
            action_btn.configure(text="▶ 继续", command=lambda: self._on_start_task(task_id), state="normal")
        elif status == 'pending':
            action_btn.configure(text="▶ 开始", command=lambda: self._on_start_task(task_id), state="normal")
        elif status in ('failed', 'verify_failed', 'cancelled'):
            action_btn.configure(text="▶ 重试", command=lambda: self._on_start_task(task_id), state="normal")
        elif status == 'verifying':
            action_btn.configure(text="⏳ 校验中", state="disabled")
        elif status in ('completed', 'cancelled'):
            action_btn.configure(text="✓ 完成", state="disabled")
        else:
            # 未知状态保守处理成可重试，至少别让用户点了半天没反应
            action_btn.configure(text="▶ 重试", command=lambda: self._on_start_task(task_id), state="normal")

    def _on_start_task(self, task_id: str):
        """开始/继续任务"""
        if self._task_actions_blocked():
            return
        task = self.task_manager.get_task(task_id)
        if not task:
            return
        if task['status'] == 'paused':
            accepted = self.task_manager.resume_task(task_id)
        else:
            accepted = self.task_manager.start_task(task_id)
        if not accepted:
            messagebox.showerror('无法启动', '任务未能启动，请检查任务状态或日志后重试。')

    def _on_pause_task(self, task_id: str):
        """暂停任务"""
        if self._task_actions_blocked():
            return
        self.task_manager.pause_task(task_id)

    def _on_cancel_task(self, task_id: str):
        """取消任务"""
        if self._task_actions_blocked():
            return
        if messagebox.askyesno("确认", "确定要取消该任务吗？"):
            self.task_manager.cancel_task(task_id)

    def _on_delete_task(self, task_id: str):
        """删除任务"""
        if self._task_actions_blocked():
            return
        # 获取任务信息
        task = self.task_manager.get_task(task_id)
        if not task:
            return

        # 创建自定义对话框
        dialog = DeleteTaskDialog(self, task)
        self.wait_window(dialog)

        # 获取用户选择
        if dialog.confirmed:
            delete_file = dialog.delete_file

            # 删除文件（如果用户选择了）
            if delete_file and task['save_path']:
                # 等待工作线程释放文件句柄后再删除，避免Windows文件占用。
                self.task_manager.cancel_task(task_id)
                try:
                    if os.path.exists(task['save_path']):
                        os.remove(task['save_path'])
                        logger.info("文件已删除: %s", task['save_path'])

                    temporary_files = [chunk['temp_file'] for chunk in self.task_manager.db.get_chunks(task_id)]
                    temporary_files.extend(os.path.join(self.task_manager.engine.config.temp_dir, f'{task_id}{suffix}')
                                           for suffix in ('.tmp', '.merged'))
                    for temporary_file in temporary_files:
                        if temporary_file and os.path.exists(temporary_file):
                            os.remove(temporary_file)
                except Exception as e:
                    messagebox.showerror("错误", f"删除文件失败: {e}")
                    return

            # 删除数据库记录
            delete_ok = self.task_manager.delete_task(task_id)
            if not delete_ok:
                messagebox.showerror("错误", "删除任务失败，可能仍有后台线程占用文件。请稍后重试。")
                return

            # 关掉详情窗、清速度历史、移除卡片与选择（单任务删文件流程不变）
            self._remove_task_ui(task_id)
            self._apply_task_filter()
            self._refresh_sidebar()
            self._refresh_selection_ui()

    def _on_open_location(self, task_id: str):
        """打开文件位置（Windows资源管理器定位文件）"""
        task = self.task_manager.get_task(task_id)
        if not task:
            messagebox.showerror("错误", "任务不存在！")
            return

        file_path = task.get("save_path")
        if not file_path:
            messagebox.showerror("错误", "任务没有保存路径！")
            return

        try:
            if os.path.exists(file_path):
                # 定位并选中文件
                subprocess.Popen(["explorer", "/select,", file_path])
                return

            folder = os.path.dirname(file_path)
            if folder and os.path.exists(folder):
                os.startfile(folder)
            else:
                messagebox.showerror("错误", "保存目录不存在！")
        except Exception as e:
            messagebox.showerror("错误", f"打开文件位置失败: {e}")

    # ==================== 任务右键菜单与手动排序 ====================

    def _bind_task_right_click(self, widget, task_id: str):
        """递归绑定右键菜单；CTk 控件的 bind 会转发到内部画布，所以不再深入其子控件"""
        if isinstance(widget, (ctk.CTkButton, ctk.CTkCheckBox)):
            return
        widget.bind("<Button-3>", lambda event, tid=task_id: self._show_task_menu(event, tid), add="+")
        if isinstance(widget, ctk.CTkFrame):
            for child in widget.winfo_children():
                self._bind_task_right_click(child, task_id)

    def _copy_task_url(self, url: str):
        """复制链接到剪贴板；先记为已见，免得自己触发的监视又弹一次提示"""
        if not url:
            return
        self._clipboard_last_seen = url
        self.clipboard_clear()
        self.clipboard_append(url)

    def _open_task_file(self, save_path: str):
        """打开已下载的文件"""
        try:
            if save_path and os.path.exists(save_path):
                os.startfile(save_path)
        except Exception as e:
            messagebox.showerror("错误", f"打开文件失败: {e}")

    def _build_task_menu(self, task_id: str):
        """构建任务右键菜单（按状态与文件是否存在启用菜单项）"""
        menu = Menu(self, tearoff=0)
        widgets = self.task_widgets.get(task_id)
        if widgets is None:
            return menu

        status = widgets.get('status')
        if status == 'downloading':
            menu.add_command(label="⏸ 暂停", command=lambda: self._on_pause_task(task_id))
        else:
            startable = status in ('pending', 'paused', 'failed', 'verify_failed', 'cancelled')
            menu.add_command(label="▶ 开始下载", command=lambda: self._on_start_task(task_id),
                             state="normal" if startable else "disabled")

        task = self.task_manager.get_task(task_id) or {}
        save_path = task.get('save_path') or ''
        menu.add_command(label="📂 打开文件", command=lambda: self._open_task_file(save_path),
                         state="normal" if save_path and os.path.exists(save_path) else "disabled")
        menu.add_command(label="📁 打开所在文件夹", command=lambda: self._on_open_location(task_id),
                         state="normal" if save_path else "disabled")
        menu.add_separator()
        menu.add_command(label="🔗 复制下载链接", command=lambda: self._copy_task_url(widgets.get('url') or ''))
        menu.add_separator()
        index = list(self.task_widgets).index(task_id)
        menu.add_command(label="⬆ 上移", command=lambda: self._on_move_task(task_id, 'up'),
                         state="normal" if index > 0 else "disabled")
        menu.add_command(label="⏫ 置顶", command=lambda: self._on_move_task(task_id, 'top'),
                         state="normal" if index > 0 else "disabled")
        menu.add_separator()
        menu.add_command(label="🗑 删除任务", command=lambda: self._on_delete_task(task_id))
        if self._batch_busy:
            for index in (0, 6, 7, 9):
                menu.entryconfigure(index, state='disabled')
        return menu

    def _show_task_menu(self, event, task_id: str):
        """在鼠标位置弹出任务菜单"""
        menu = self._build_task_menu(task_id)
        try:
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            menu.grab_release()

    def _on_move_task(self, task_id: str, direction: str):
        """上移/置顶：先写库，再按新顺序重排卡片"""
        if self._task_actions_blocked():
            return
        if direction == 'top':
            moved = self.task_manager.move_task_to_top(task_id)
        else:
            moved = self.task_manager.move_task_up(task_id)
        if not moved:
            return
        self._sync_task_order()

    def _sync_task_order(self):
        """从数据库读回顺序并重排 Tk 卡片（task_widgets 的顺序即显示顺序）"""
        ordered_ids = [task['task_id'] for task in self.task_manager.get_all_tasks()]
        ordered = {tid: self.task_widgets[tid] for tid in ordered_ids if tid in self.task_widgets}
        for tid, widgets in self.task_widgets.items():
            ordered.setdefault(tid, widgets)
        self.task_widgets = ordered
        self._relayout_task_widgets()
        self._apply_task_filter()

    def _handle_download_completed(self, task_id: str):
        """下载完成后动作：打开文件/所在文件夹、全部完成后关机（默认全关）"""
        actions = self.task_manager.engine.config.after_download
        if not any(actions.values()):
            return

        task = self.task_manager.get_task(task_id)
        if not task:
            return

        save_path = task.get('save_path') or ''

        if actions['open_file'] and save_path and os.path.exists(save_path):
            try:
                os.startfile(save_path)
            except Exception as e:
                logger.warning("完成后打开文件失败: %s", e)

        if actions['open_folder'] and save_path:
            self._on_open_location(task_id)

        if actions['shutdown']:
            self._shutdown_pending = True
            self._schedule_shutdown_if_idle()

    def _has_active_tasks(self) -> bool:
        """是否还有在跑的任务（下载中/等待/校验中）"""
        return (self._batch_busy or self._batch_add_pending > 0 or self.task_manager.get_activity_state()[1] or any(
            task.get('status') in ('downloading', 'pending', 'verifying')
            for task in self.task_manager.get_all_tasks()
        ))

    def _schedule_shutdown_if_idle(self):
        """队列里还有任务就先不关机，等最后一个完成时再弹确认"""
        if self._shutdown_dialog_open or self._has_active_tasks():
            return

        self._shutdown_dialog_open = True
        self._shutdown_pending = False
        self._shutdown_activity = self.task_manager.get_activity_state()[0]
        try:
            dialog = ShutdownConfirmDialog(self, on_shutdown=self._perform_shutdown,
                                           can_shutdown=self._shutdown_still_valid)
            self.wait_window(dialog)
        finally:
            self._shutdown_dialog_open = False
            self._shutdown_activity = None

    def _shutdown_still_valid(self):
        """新增、重试任务或关闭设置都使旧倒计时失效，即使新任务已经很快完成。"""
        return (self.task_manager.engine.config.after_download['shutdown']
                and self._shutdown_activity == self.task_manager.get_activity_state()[0]
                and not self._has_active_tasks())

    def _perform_shutdown(self):
        """等倒计时确认走完才真关机，取消就是什么都不做"""
        if not self._shutdown_still_valid():
            logger.info('下载任务已变化，取消本次关机')
            return
        logger.info("全部下载完成，执行关机命令")
        try:
            subprocess.Popen(
                ["shutdown", "/s", "/t", "0"],
                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0),
            )
        except Exception as e:
            logger.error("执行关机命令失败: %s", e)
            messagebox.showerror("错误", f"关机失败: {e}")

    def _on_task_added(self, task_id: str):
        """任务添加回调"""
        task = self.task_manager.get_task(task_id)
        if task:
            self._ui_events.put((self._add_task_widget, (task,)))

    def _on_task_status_changed(self, task_id: str, status: str, message: str):
        """任务状态变更回调"""
        self._ui_events.put((self._update_task_status, (task_id, status, message)))

        # 下载完成时发送托盘通知
        if status == 'completed':
            task = self.task_manager.get_task(task_id)
            if task and hasattr(self, 'tray_manager'):
                self.tray_manager.notify_download_complete(task['filename'])
            # 完成后动作必须走UI事件队列，不能在引擎回调线程里直接开窗口/关机
            self._ui_events.put((self._handle_download_completed, (task_id,)))

    def _on_task_progress(self, task_id: str, downloaded_size: int, total_size: int, speed: float):
        """任务进度回调"""
        self._ui_events.put((self._update_task_progress, (task_id, downloaded_size, total_size, speed)))

    def _drain_ui_events(self):
        """只在Tk主线程更新控件，工作线程只负责入队。"""
        try:
            while True:
                callback, args = self._ui_events.get_nowait()
                callback(*args)
        except Empty:
            pass
        finally:
            self.after(50, self._drain_ui_events)

    def _update_task_status(self, task_id: str, status: str, message: str = None):
        """更新任务状态UI"""
        if task_id not in self.task_widgets:
            return

        widgets = self.task_widgets[task_id]
        # 重试时可能重新分配了文件名，卡片与搜索缓存必须同步。
        task = self.task_manager.get_task(task_id)
        if task and task.get('filename') != widgets.get('filename'):
            widgets['filename'] = task['filename']
            widgets['category'] = guess_file_category(task['filename'])
            widgets['filename_label'].configure(text=task['filename'])
        widgets['status'] = status
        # 重试/降级属于临时提示：显示到状态标签上，一有真实速度就自动收回去
        notice = ''
        if status == 'downloading' and message and ('重试' in message or '降级' in message):
            notice = message
        widgets['notice'] = notice
        widgets['status_label'].configure(
            text=self._get_status_text(status) + (f" · {notice}" if notice else ""))
        if status != 'downloading':
            widgets['speed_label'].configure(text=format_speed(0))
            widgets['eta_label'].configure(text="剩余 --")
        # 状态回调和批量期间的新卡片同样遵守忙碌状态，不能重新启用冲突操作。
        self._refresh_task_controls(task_id)

        # 同步详情窗（暂停/完成后曲线自然冻结）
        self._push_detail_status(task_id, status)

        self._apply_task_filter()
        self._refresh_sidebar()

    def _update_task_progress(self, task_id: str, downloaded_size: int, total_size: int, speed: float):
        """更新任务进度UI"""
        if task_id not in self.task_widgets:
            return

        widgets = self.task_widgets[task_id]

        # 数据又开始流动了，把“正在重试”这种临时提示收回成正常状态
        if widgets.get('notice') and float(speed or 0) > 0:
            widgets['notice'] = ''
            widgets['status_label'].configure(text=self._get_status_text(widgets.get('status')))

        # 速度曲线采样：只在下载中记录，启动回填的0速度样本不进曲线
        if widgets.get('status') == 'downloading':
            history = self._speed_history.get(task_id)
            if history is None:
                history = deque(maxlen=180)
                self._speed_history[task_id] = history
            second = int(time.monotonic())
            sample = (second, max(0.0, float(speed or 0)))
            if history and history[-1][0] == second:
                history[-1] = sample
            else:
                history.append(sample)
            while history and history[0][0] < second - 180:
                history.popleft()

        # 更新进度条
        progress = downloaded_size / total_size if total_size > 0 else 0
        widgets['progress_bar'].set(progress)

        # 更新百分比
        widgets['progress_label'].configure(text=f"{progress * 100:.1f}%")

        # 已下载/总大小 + 剩余时间 + 速度：文案生成与详情窗共用一套逻辑
        size_text, eta_text, speed_text = format_progress_texts(
            downloaded_size, total_size, speed, widgets.get('status'))
        widgets['speed_label'].configure(text=speed_text)
        widgets['size_label'].configure(text=size_text)
        widgets['eta_label'].configure(text=eta_text)

        # 同步详情窗
        self._push_detail_update(task_id, downloaded_size, total_size, speed)

    def _flash_status(self, text: str, seconds: float = 3.0):
        """状态栏闪一条临时提示，不弹窗：到点自动恢复常规统计"""
        self._status_flash = (text, time.monotonic() + seconds)
        try:
            self.status_label.configure(text=text)
        except Exception:
            pass

    def _start_ui_update_thread(self):
        """使用Tk定时器更新状态栏，窗口销毁后不再保留后台线程。"""
        def update_status_bar():
            flash = self._status_flash
            if flash and time.monotonic() < flash[1]:
                self.status_label.configure(text=flash[0])
                self.after(1000, update_status_bar)
                return
            self._status_flash = None
            try:
                stats = self.task_manager.get_statistics()
                total_speed = self.task_manager.get_total_speed()
            except Exception as error:
                # 磁盘忙/库被短暂锁住时别每秒抛异常，沿上一次数字显示
                logger.warning("读取任务统计失败: %s", error)
                stats, total_speed = self._last_status_stats
            else:
                self._last_status_stats = (stats, total_speed)
            status_text = f"总速度: {format_speed(total_speed)} | 下载中: {stats['downloading']} | 等待: {stats['pending']}"
            if self._batch_add_pending:
                status_text += f" | 正在添加 {self._batch_add_pending} 个任务…"
            self.status_label.configure(text=status_text)
            self.after(1000, update_status_bar)

        self.after(1000, update_status_bar)

    def _on_hide_completed_toggle(self):
        """隐藏已完成：立即生效并持久化"""
        config = self.task_manager.engine.config
        config.ui_hide_completed = bool(self.hide_completed_var.get())
        config.save()
        self._apply_task_filter()

    def _apply_task_filter(self):
        """按分类、搜索词与“隐藏已完成”统一控制任务行显示"""
        keyword = self.search_entry.get().strip().lower()
        hide_completed = bool(self.hide_completed_var.get())
        visible = 0
        changed = False

        for widgets in self.task_widgets.values():
            matched = self._task_matches_filter(widgets, keyword, hide_completed)
            if matched:
                visible += 1
            if matched != widgets.get('visible', True):
                changed = True
            widgets['visible'] = matched

        # 筛选后不再可见的选择立即清除；切回原筛选也不恢复
        self._prune_selection()

        # 只在显隐变化时重排，避免每敲一个搜索字符都把整个列表拆了重装
        if changed:
            self._relayout_task_widgets()

        if visible == 0:
            has_condition = bool(keyword) or hide_completed or (self._active_category or 'all') != 'all'
            self.empty_label.configure(text="没有匹配的任务" if has_condition else "暂无任务")
            self.empty_label.pack(pady=20)
        elif self.empty_label.winfo_manager():
            self.empty_label.pack_forget()

        self.filter_hint_label.configure(text=f"显示 {visible} / {len(self.task_widgets)}")
        self._refresh_selection_ui()

    def _task_matches_filter(self, widgets: Dict, keyword: str, hide_completed: bool) -> bool:
        """单条任务的过滤判定：分类 + 搜索 + 隐藏已完成"""
        if hide_completed and widgets.get('status') == 'completed':
            return False
        if keyword and keyword not in widgets.get('filename', '').lower() \
                and keyword not in widgets.get('url', '').lower():
            return False
        return self._task_matches_category(widgets)

    def _task_matches_category(self, widgets: Dict) -> bool:
        """分类树命中判定：'all' / 'status:xxx' / 'type:xxx' / 'host:xxx'"""
        key = self._active_category or 'all'
        if key == 'all':
            return True
        kind, _, value = key.partition(':')
        if kind == 'status':
            if value == 'failed':
                return widgets.get('status') in ('failed', 'verify_failed', 'cancelled')
            return widgets.get('status') == value
        if kind == 'type':
            return (widgets.get('category') or 'other') == value
        if kind == 'host':
            return widgets.get('host') == value
        return True

    def _relayout_task_widgets(self):
        """按 self.task_widgets 的顺序重排卡片（pack 顺序就是显示顺序）"""
        if self.empty_label.winfo_manager():
            self.empty_label.pack_forget()
        for widgets in self.task_widgets.values():
            frame = widgets['frame']
            if frame.winfo_manager():
                frame.pack_forget()
        for widgets in self.task_widgets.values():
            if widgets.get('visible', True):
                widgets['frame'].pack(fill="x", pady=5)
        if not any(widgets.get('visible', True) for widgets in self.task_widgets.values()):
            self.empty_label.pack(pady=20)

    @staticmethod
    def _get_status_text(status: str) -> str:
        """获取状态文本（映射表在 task_detail_window，卡片与详情窗共用）"""
        return get_status_text(status)

    def _on_window_close(self):
        """窗口关闭事件"""
        config = self.task_manager.engine.config
        close_behavior = config.get("close_behavior", "ask")

        if close_behavior == "ask":
            try:
                # 弹出关闭确认对话框
                dialog = CloseConfirmDialog(self, config)
                self.wait_window(dialog)

                if dialog.confirmed:
                    if dialog.remember:
                        # 保存用户选择
                        config.set("close_behavior", dialog.action)
                        config.save()

                    if dialog.action == "exit":
                        self._exit_app()
                    else:
                        self._minimize_app()
            except Exception as e:
                # 兜底：对话框出幺蛾子也不能把用户卡死在“关不掉”的地狱里
                logger.error("关闭确认弹窗异常: %s", e)
                if messagebox.askyesno("退出确认", "关闭窗口失败了，是否直接退出程序？", parent=self):
                    self._exit_app()
        elif close_behavior == "exit":
            self._exit_app()
        elif close_behavior == "minimize":
            self._minimize_app()


class CloseConfirmDialog(SafeToplevelMixin, ctk.CTkToplevel):
    """关闭确认对话框"""

    def __init__(self, parent, config):
        super().__init__(parent)

        self.config = config
        self.confirmed = False
        self.action = "minimize"  # minimize 或 exit
        self.remember = False

        # 设置窗口
        self.title("退出确认")
        self.resizable(False, False)
        self.protocol("WM_DELETE_WINDOW", self._on_cancel)

        # 模态对话框
        self.transient(parent)
        self.grab_set()

        # 创建UI
        self._create_ui()

        # 这破弹窗别整得跟全屏似的：固定一个紧凑尺寸 + 居中，别再飘到角落里丢人
        self._apply_compact_geometry(parent)

        # 键盘快捷键：别让用户找不到“确定/取消”
        self.bind("<Escape>", lambda _e=None: self._on_cancel())
        self.bind("<Return>", lambda _e=None: self._on_confirm())
        self.focus_force()

    def _apply_compact_geometry(self, parent):
        """应用紧凑窗口尺寸并居中到父窗口"""
        # 固定尺寸：内容别忽胖忽瘦，用户一眼就烦
        width = 360
        height = 240
        self.minsize(width, height)
        self.maxsize(width, height)

        # 先让Tk把尺寸算明白，不然 winfo_* 可能全是 1，居中就会跑偏
        parent.update_idletasks()
        self.update_idletasks()

        parent_x = parent.winfo_rootx()
        parent_y = parent.winfo_rooty()
        parent_w = parent.winfo_width()
        parent_h = parent.winfo_height()

        x = parent_x + (parent_w - width) // 2
        y = parent_y + (parent_h - height) // 2

        screen_w = self.winfo_screenwidth()
        screen_h = self.winfo_screenheight()
        x = max(0, min(x, screen_w - width))
        y = max(0, min(y, screen_h - height))

        self.geometry(f"{width}x{height}+{x}+{y}")

    def _create_ui(self):
        """创建UI"""
        title_font = ctk.CTkFont(size=16, weight="bold")
        body_font = ctk.CTkFont(size=13)
        option_font = ctk.CTkFont(size=12)
        note_font = ctk.CTkFont(size=11)

        main_frame = ctk.CTkFrame(
            self,
            corner_radius=12,
            border_width=1,
            border_color=("gray70", "gray25"),
        )
        main_frame.pack(fill="both", expand=True, padx=14, pady=14)

        title_label = ctk.CTkLabel(main_frame, text="退出确认", font=title_font)
        title_label.pack(anchor="w", pady=(2, 10))

        # 提示信息
        msg_label = ctk.CTkLabel(
            main_frame,
            text="确定要关闭daw下载器吗？",
            font=body_font,
            justify="left",
            wraplength=320,
        )
        msg_label.pack(anchor="w")

        # 选项
        self.action_var = ctk.StringVar(value="minimize")

        options_frame = ctk.CTkFrame(main_frame, fg_color="transparent")
        options_frame.pack(fill="x", pady=(14, 0))

        minimize_radio = ctk.CTkRadioButton(
            options_frame,
            text="最小化到任务栏（继续下载）",
            variable=self.action_var,
            value="minimize",
            font=option_font,
        )
        minimize_radio.pack(anchor="w", pady=(0, 6))

        exit_radio = ctk.CTkRadioButton(
            options_frame,
            text="退出程序（停止所有下载）",
            variable=self.action_var,
            value="exit",
            font=option_font,
        )
        exit_radio.pack(anchor="w")

        # 记住选择
        self.remember_var = ctk.BooleanVar(value=False)
        remember_check = ctk.CTkCheckBox(
            main_frame,
            text="记住我的选择，下次不提示",
            variable=self.remember_var,
            font=note_font,
        )
        remember_check.pack(anchor="w", pady=(12, 0))

        # 按钮
        button_frame = ctk.CTkFrame(main_frame, fg_color="transparent", width=0, height=0)
        button_frame.pack(fill="x", pady=(16, 0))

        cancel_btn = ctk.CTkButton(
            button_frame,
            text="取消",
            command=self._on_cancel,
            width=96,
            height=28,
            text_color=("gray10", "gray90"),
            fg_color=("gray85", "gray25"),
            hover_color=("gray80", "gray30"),
        )
        cancel_btn.pack(side="right")

        self.confirm_btn = ctk.CTkButton(
            button_frame,
            text="确定",
            command=self._on_confirm,
            width=96,
            height=28,
        )
        self.confirm_btn.pack(side="right", padx=(0, 10))

        # 默认把焦点给“确定”，键盘一回车就能走
        self.after(0, lambda: self.confirm_btn.focus_set())

    def _on_confirm(self):
        """确定"""
        self.confirmed = True
        self.action = self.action_var.get()
        self.remember = self.remember_var.get()
        self.destroy()

    def _on_cancel(self):
        """取消"""
        self.confirmed = False
        self.destroy()


class ShutdownConfirmDialog(SafeToplevelMixin, ctk.CTkToplevel):
    """全部下载完成后的关机确认（倒计时结束才真关机，关窗=取消）"""

    DEFAULT_COUNTDOWN = 60

    def __init__(self, parent, on_shutdown=None, countdown: int = DEFAULT_COUNTDOWN, can_shutdown=None):
        super().__init__(parent)

        self._on_shutdown = on_shutdown
        self._can_shutdown = can_shutdown or (lambda: True)
        self._remaining = max(0, int(countdown))
        self._tick_id = None

        self.title("下载完成")
        self.geometry("360x150")
        self.resizable(False, False)
        self.transient(parent)
        self.grab_set()

        ctk.CTkLabel(self, text="全部下载已完成", font=("Arial", 14, "bold")).pack(pady=(20, 5))

        self.count_label = ctk.CTkLabel(self, text=self._countdown_text())
        self.count_label.pack(pady=5)

        button_frame = ctk.CTkFrame(self, fg_color="transparent")
        button_frame.pack(pady=10)

        ctk.CTkButton(button_frame, text="立即关机", width=100, command=self._shutdown_now).pack(side="left", padx=5)
        ctk.CTkButton(button_frame, text="取消", width=100, command=self.destroy).pack(side="left", padx=5)

        self._center(parent)
        self._tick()

    def _countdown_text(self) -> str:
        return f"{self._remaining} 秒后自动关机"

    def _center(self, parent):
        try:
            parent.update_idletasks()
            x = parent.winfo_rootx() + (parent.winfo_width() - 360) // 2
            y = parent.winfo_rooty() + (parent.winfo_height() - 150) // 2
            self.geometry(f"360x150+{max(x, 0)}+{max(y, 0)}")
        except Exception:
            pass

    def _tick(self):
        if not self.winfo_exists():
            return
        if not self._can_shutdown():
            self.destroy()
            return
        if self._remaining <= 0:
            self._shutdown_now()
            return
        self.count_label.configure(text=self._countdown_text())
        self._remaining -= 1
        self._tick_id = self.after(1000, self._tick)

    def destroy(self):
        # 定时器必须先撤，不然窗口没了回调还会炸一遍
        if self._tick_id is not None:
            try:
                self.after_cancel(self._tick_id)
            except Exception:
                pass
            self._tick_id = None
        super().destroy()

    def _shutdown_now(self):
        allowed = self._can_shutdown()
        self.destroy()
        if allowed and self._on_shutdown:
            self._on_shutdown()


class DeleteTaskDialog(SafeToplevelMixin, ctk.CTkToplevel):
    """删除任务对话框"""

    def __init__(self, parent, task):
        super().__init__(parent)

        self.task = task
        self.confirmed = False
        self.delete_file = False

        # 设置窗口
        self.title("删除确认")
        self.geometry("400x200")
        self.resizable(False, False)

        # 模态对话框
        self.transient(parent)
        self.grab_set()

        # 创建UI
        self._create_ui()

        # 居中显示
        self.update_idletasks()
        x = parent.winfo_x() + (parent.winfo_width() - self.winfo_width()) // 2
        y = parent.winfo_y() + (parent.winfo_height() - self.winfo_height()) // 2
        self.geometry(f"+{x}+{y}")

    def _create_ui(self):
        """创建UI"""
        # 主容器
        main_frame = ctk.CTkFrame(self)
        main_frame.pack(fill="both", expand=True, padx=20, pady=20)

        # 提示信息
        file_exists = os.path.exists(self.task['save_path']) if self.task['save_path'] else False

        if file_exists:
            msg = f"确定要删除任务吗？\n\n文件名: {self.task['filename']}"
        else:
            msg = f"确定要删除任务吗？\n\n文件名: {self.task['filename']}\n（文件不存在或未下载完成）"

        msg_label = ctk.CTkLabel(main_frame, text=msg, font=("Arial", 12), justify="left")
        msg_label.pack(pady=10)

        # 删除文件选项（只有文件存在时才显示）
        if file_exists:
            self.delete_file_var = ctk.BooleanVar(value=False)
            delete_file_check = ctk.CTkCheckBox(
                main_frame,
                text="同时删除已下载的文件",
                variable=self.delete_file_var,
                font=("Arial", 11)
            )
            delete_file_check.pack(pady=10)
        else:
            self.delete_file_var = None

        # 按钮区域
        button_frame = ctk.CTkFrame(main_frame, fg_color="transparent")
        button_frame.pack(pady=20)

        confirm_btn = ctk.CTkButton(button_frame, text="确定", command=self._on_confirm, width=100)
        confirm_btn.pack(side="left", padx=10)

        cancel_btn = ctk.CTkButton(button_frame, text="取消", command=self._on_cancel, width=100)
        cancel_btn.pack(side="left", padx=10)

    def _on_confirm(self):
        """确定按钮"""
        self.confirmed = True
        if self.delete_file_var:
            self.delete_file = self.delete_file_var.get()
        self.destroy()

    def _on_cancel(self):
        """取消按钮"""
        self.confirmed = False
        self.destroy()


class AddTaskDialog(SafeToplevelMixin, ctk.CTkToplevel):
    """添加任务对话框 - 支持多行批量与哈希校验"""

    def __init__(self, parent, initial_url: str = None):
        super().__init__(parent)

        self.confirmed = False
        self.urls = []           # 解析后的链接列表（批量按行）
        self.url = ""            # 兼容调用方：首条链接
        self.save_dir = ""
        self.expected_hash = ""
        self.hash_type = "md5"
        self.start_later = False

        # 设置窗口
        self.title("添加下载任务")
        self.resizable(False, False)
        self.protocol("WM_DELETE_WINDOW", self._on_cancel)

        # 模态对话框
        self.transient(parent)
        self.grab_set()

        # 创建UI（剪贴板浮窗入口会预填链接）
        self._create_ui(initial_url=initial_url)

        # 居中显示
        self._center_window(parent, 520, 430)

        # 快捷键：多行输入框里回车是换行，提交用 Ctrl+Enter
        self.bind("<Escape>", lambda _: self._on_cancel())
        self.bind("<Return>", self._on_return)
        self.bind("<Control-Return>", lambda _: self._on_confirm())
        self.focus_force()

    def _center_window(self, parent, width, height):
        """居中显示"""
        self.update_idletasks()
        x = parent.winfo_x() + (parent.winfo_width() - width) // 2
        y = parent.winfo_y() + (parent.winfo_height() - height) // 2
        x = max(0, x)
        y = max(0, y)
        self.geometry(f"{width}x{height}+{x}+{y}")

    def _create_ui(self, initial_url: str = None):
        """创建UI"""
        main_frame = ctk.CTkFrame(self)
        main_frame.pack(fill="both", expand=True, padx=20, pady=20)

        # URL输入（多行=批量，一行一个）
        url_label = ctk.CTkLabel(main_frame, text="下载链接（每行一个，支持批量）:", font=("Arial", 12))
        url_label.grid(row=0, column=0, sticky="w", pady=(0, 5))

        self.url_text = ctk.CTkTextbox(main_frame, width=400, height=90)
        self.url_text.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(0, 15))
        self.url_text.bind("<KeyRelease>", self._on_url_changed)
        self.url_text.bind("<<Paste>>", lambda _event: self.after(50, self._on_url_changed))
        if initial_url:
            self.url_text.insert("1.0", initial_url)
        self.url_text.focus_set()

        # 保存位置
        save_label = ctk.CTkLabel(main_frame, text="保存位置:", font=("Arial", 12))
        save_label.grid(row=2, column=0, sticky="w", pady=(0, 5))

        save_frame = ctk.CTkFrame(main_frame, fg_color="transparent")
        save_frame.grid(row=3, column=0, columnspan=2, sticky="ew", pady=(0, 15))

        self.save_entry = ctk.CTkEntry(save_frame, width=330, placeholder_text="留空使用默认下载目录")
        self.save_entry.pack(side="left")

        browse_btn = ctk.CTkButton(save_frame, text="浏览", width=60, command=self._on_browse)
        browse_btn.pack(side="left", padx=(10, 0))

        # 哈希校验（可选）
        hash_label = ctk.CTkLabel(main_frame, text="文件校验 (可选):", font=("Arial", 12))
        hash_label.grid(row=4, column=0, sticky="w", pady=(0, 5))

        hash_frame = ctk.CTkFrame(main_frame, fg_color="transparent")
        hash_frame.grid(row=5, column=0, columnspan=2, sticky="ew", pady=(0, 15))

        # 哈希类型选择
        self.hash_type_var = ctk.StringVar(value="md5")
        self.hash_type_menu = ctk.CTkOptionMenu(
            hash_frame,
            values=["md5", "sha256"],
            variable=self.hash_type_var,
            width=80
        )
        self.hash_type_menu.pack(side="left")

        # 哈希值输入
        self.hash_entry = ctk.CTkEntry(hash_frame, width=310, placeholder_text="预期哈希值（留空跳过校验）")
        self.hash_entry.pack(side="left", padx=(10, 0))

        # 批量提示：多行时忽略校验字段
        self.batch_hint_label = ctk.CTkLabel(main_frame, text="", font=("Arial", 11), text_color="gray")
        self.batch_hint_label.grid(row=6, column=0, columnspan=2, sticky="w", pady=(0, 5))

        # 按钮区域：开始下载 / 稍后下载 / 取消
        button_frame = ctk.CTkFrame(main_frame, fg_color="transparent")
        button_frame.grid(row=7, column=0, columnspan=2, pady=(10, 0))

        start_btn = ctk.CTkButton(button_frame, text="开始下载", command=lambda: self._on_confirm(False), width=100)
        start_btn.pack(side="left", padx=10)

        later_btn = ctk.CTkButton(
            button_frame, text="稍后下载", command=lambda: self._on_confirm(True), width=100,
            fg_color=("gray85", "gray25"), hover_color=("gray80", "gray30"),
            text_color=("gray10", "gray90")
        )
        later_btn.pack(side="left", padx=10)

        cancel_btn = ctk.CTkButton(
            button_frame, text="取消", command=self._on_cancel, width=100,
            fg_color=("gray85", "gray25"), hover_color=("gray80", "gray30"),
            text_color=("gray10", "gray90")
        )
        cancel_btn.pack(side="left", padx=10)

        self._on_url_changed()

    def parse_urls(self) -> list:
        """按行解析链接：去空行、去首尾空格"""
        try:
            raw = self.url_text.get("1.0", "end")
        except Exception:
            return []
        return [line.strip() for line in raw.splitlines() if line.strip()]

    def _on_url_changed(self, _event=None):
        """链接行数变化时切换校验字段可用性和批量提示"""
        multiple = len(self.parse_urls()) > 1
        state = "disabled" if multiple else "normal"
        self.hash_entry.configure(state=state)
        self.hash_type_menu.configure(state=state)
        self.batch_hint_label.configure(
            text="多行批量添加时忽略文件校验" if multiple else "")
        return None

    def _on_return(self, event=None):
        """多行输入框里回车只换行，别的控件回车才提交"""
        widget = getattr(event, "widget", None)
        if widget is not None and widget.winfo_class() == "Text":
            return None
        self._on_confirm(False)
        return "break"

    def _on_browse(self):
        """浏览保存位置"""
        from tkinter import filedialog
        directory = filedialog.askdirectory(title="选择保存位置")
        if directory:
            self.save_entry.delete(0, "end")
            self.save_entry.insert(0, directory)

    def _on_confirm(self, start_later: bool = False):
        """确定：单链接带哈希校验，多链接只批量加任务"""
        urls = self.parse_urls()
        if not urls:
            from tkinter import messagebox
            messagebox.showerror("错误", "请输入下载链接！", parent=self)
            return

        self.confirmed = True
        self.urls = urls
        self.url = urls[0]
        self.start_later = bool(start_later)
        self.save_dir = self.save_entry.get().strip() or None
        if len(urls) == 1:
            self.expected_hash = self.hash_entry.get().strip() or None
            self.hash_type = self.hash_type_var.get()
        else:
            # 批量没有单一哈希概念，直接忽略校验字段
            self.expected_hash = None
            self.hash_type = "md5"
        self.destroy()

    def _on_cancel(self):
        """取消"""
        self.confirmed = False
        self.destroy()
