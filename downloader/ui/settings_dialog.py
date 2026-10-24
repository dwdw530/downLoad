# -*- coding: utf-8 -*-
"""
设置对话框
老王说：设置简单点就行，别搞太复杂！
"""
import os
import customtkinter as ctk
from tkinter import filedialog, messagebox
from typing import Callable, Optional
from downloader.ui.ctk_helpers import SafeToplevelMixin
from downloader.utils.config import ConfigManager, normalize_opacity


# 主题模式：配置值 <-> 界面标签（保证顺序：深色/浅色/跟随系统）
APPEARANCE_LABELS = {"dark": "深色", "light": "浅色", "system": "跟随系统"}
APPEARANCE_VALUE_BY_LABEL = {label: value for value, label in APPEARANCE_LABELS.items()}

# 悬浮条样式：配置值 <-> 界面标签
FLOAT_BAR_MODE_LABELS = {"normal": "完整", "mini": "迷你"}
FLOAT_BAR_MODE_VALUE_BY_LABEL = {label: value for value, label in FLOAT_BAR_MODE_LABELS.items()}
COMPLETION_ACTION_LABELS = {'none': '不操作', 'exit_app': '退出下载器', 'shutdown': '关闭电脑'}


class SettingsDialog(SafeToplevelMixin, ctk.CTkToplevel):
    """设置对话框"""

    def __init__(self, parent, config_manager: ConfigManager,
                 on_save_callback: Optional[Callable] = None, on_preview_callback=None):
        super().__init__(parent)

        self.config = config_manager
        self.on_save_callback = on_save_callback
        self._preview_callback = on_preview_callback or getattr(parent, '_preview_opacity', None)
        self._opacity_baseline = (self.config.ui_window_opacity, self.config.ui_float_bar_opacity)
        self.protocol('WM_DELETE_WINDOW', self._on_cancel)
        self.bind('<Escape>', lambda event: self._on_cancel())

        # 设置窗口
        self.title("设置")
        self.geometry("560x600")  # 下载页新增自动分目录说明，高度再放一档避免控件被切
        self.resizable(False, False)

        # 模态对话框
        self.transient(parent)
        self.grab_set()

        # 创建UI
        self._create_ui()

        # 加载当前配置
        self._load_config()

    def _create_ui(self):
        """创建UI组件：分标签页，避免单页太长"""
        self.tabview = ctk.CTkTabview(self, width=500, height=500)
        self.tabview.pack(fill="both", expand=True, padx=16, pady=(16, 0))

        self._build_download_tab(self.tabview.add("下载"))
        self._build_appearance_tab(self.tabview.add("外观"))
        self._build_other_tab(self.tabview.add("其他"))

        # 按钮区域
        button_frame = ctk.CTkFrame(self, fg_color="transparent")
        button_frame.pack(fill="x", padx=16, pady=12)

        save_btn = ctk.CTkButton(button_frame, text="保存", command=self._on_save, width=100)
        save_btn.pack(side="right", padx=5)

        cancel_btn = ctk.CTkButton(button_frame, text="取消", command=self._on_cancel, width=100)
        cancel_btn.pack(side="right", padx=5)

        reset_btn = ctk.CTkButton(button_frame, text="恢复默认", command=self._on_reset, width=100)
        reset_btn.pack(side="left", padx=5)

        # 恢复默认是就地生效，不能弹窗打断；状态直接写在按钮旁边，2 秒后自己消失
        self.status_label = ctk.CTkLabel(button_frame, text="", font=("Arial", 11),
                                        text_color=("#0f7b4f", "#4ade80"))
        self.status_label.pack(side="left", padx=10)
        self._status_job = None

    def _build_download_tab(self, tab):
        """下载相关设置：目录、线程、限速、超时和代理"""
        tab.columnconfigure(1, weight=1)

        # 下载目录设置
        dir_label = ctk.CTkLabel(tab, text="默认下载目录:", font=("Arial", 12))
        dir_label.grid(row=0, column=0, sticky="w", pady=8)

        dir_frame = ctk.CTkFrame(tab, fg_color="transparent")
        dir_frame.grid(row=0, column=1, sticky="ew", pady=8, padx=10)

        dir_row = ctk.CTkFrame(dir_frame, fg_color="transparent")
        dir_row.pack(fill="x")

        self.dir_entry = ctk.CTkEntry(dir_row, width=270)
        self.dir_entry.pack(side="left", padx=5)

        browse_btn = ctk.CTkButton(dir_row, text="浏览", command=self._browse_directory, width=80)
        browse_btn.pack(side="left")

        # 自动分目录：默认关，只影响之后的新任务；显式指定的保存位置原样使用
        self.auto_classify_var = ctk.BooleanVar(value=False)
        ctk.CTkCheckBox(
            dir_frame,
            text="按文件类型自动建立子目录",
            variable=self.auto_classify_var,
        ).pack(anchor="w", padx=5, pady=(6, 0))

        ctk.CTkLabel(
            dir_frame, text="仅影响新任务；手动指定保存位置时使用该位置",
            font=("Arial", 10), text_color="gray",
        ).pack(anchor="w", padx=5)

        # 临时文件目录：分块/合并/视频下载都先落这里，和最终下载目录分开配置
        temp_label = ctk.CTkLabel(tab, text="临时文件目录:", font=("Arial", 12))
        temp_label.grid(row=1, column=0, sticky="w", pady=8)

        temp_frame = ctk.CTkFrame(tab, fg_color="transparent")
        temp_frame.grid(row=1, column=1, sticky="ew", pady=8, padx=10)

        temp_row = ctk.CTkFrame(temp_frame, fg_color="transparent")
        temp_row.pack(fill="x")

        self.temp_entry = ctk.CTkEntry(temp_row, width=270)
        self.temp_entry.pack(side="left", padx=5)

        temp_browse_btn = ctk.CTkButton(temp_row, text="浏览", command=self._browse_temp_directory, width=80)
        temp_browse_btn.pack(side="left")

        ctk.CTkLabel(
            temp_frame, text="与下载目录不在同一磁盘时会多一次复制",
            font=("Arial", 10), text_color="gray",
        ).pack(anchor="w", padx=5, pady=(2, 0))

        # 线程数设置
        thread_label = ctk.CTkLabel(tab, text="下载线程数:", font=("Arial", 12))
        thread_label.grid(row=2, column=0, sticky="w", pady=8)

        thread_row = ctk.CTkFrame(tab, fg_color="transparent")
        thread_row.grid(row=2, column=1, sticky="w", pady=8, padx=10)

        self.thread_slider = ctk.CTkSlider(thread_row, from_=1, to=16, number_of_steps=15, width=220)
        self.thread_slider.pack(side="left")

        self.thread_value_label = ctk.CTkLabel(thread_row, text="8", font=("Arial", 12),
                                               width=28, anchor="w")
        self.thread_value_label.pack(side="left", padx=(10, 0))

        self.thread_slider.configure(command=self._on_thread_slider_change)

        # 并发任务数设置
        concurrent_label = ctk.CTkLabel(tab, text="同时下载任务数:", font=("Arial", 12))
        concurrent_label.grid(row=3, column=0, sticky="w", pady=8)

        concurrent_row = ctk.CTkFrame(tab, fg_color="transparent")
        concurrent_row.grid(row=3, column=1, sticky="w", pady=8, padx=10)

        self.concurrent_slider = ctk.CTkSlider(concurrent_row, from_=1, to=5, number_of_steps=4, width=220)
        self.concurrent_slider.pack(side="left")

        self.concurrent_value_label = ctk.CTkLabel(concurrent_row, text="3", font=("Arial", 12),
                                                   width=28, anchor="w")
        self.concurrent_value_label.pack(side="left", padx=(10, 0))

        self.concurrent_slider.configure(command=self._on_concurrent_slider_change)

        # 速度限制设置（全局限速：所有任务合计，不是每个任务各一份）
        speed_limit_label = ctk.CTkLabel(tab, text="总速度上限(KB/s):", font=("Arial", 12))
        speed_limit_label.grid(row=4, column=0, sticky="w", pady=8)

        speed_frame = ctk.CTkFrame(tab, fg_color="transparent")
        speed_frame.grid(row=4, column=1, sticky="ew", pady=8, padx=10)

        self.speed_limit_entry = ctk.CTkEntry(speed_frame, width=120, placeholder_text="0=不限速")
        self.speed_limit_entry.pack(side="left", padx=5)

        speed_hint_label = ctk.CTkLabel(speed_frame, text="(所有任务合计，0 表示不限速)",
                                        font=("Arial", 10), text_color="gray")
        speed_hint_label.pack(side="left")

        # 超时设置
        timeout_label = ctk.CTkLabel(tab, text="请求超时(秒):", font=("Arial", 12))
        timeout_label.grid(row=5, column=0, sticky="w", pady=8)

        self.timeout_entry = ctk.CTkEntry(tab, width=120)
        self.timeout_entry.grid(row=5, column=1, sticky="w", pady=8, padx=10)

        # ========== 代理设置 ==========
        proxy_label = ctk.CTkLabel(tab, text="代理设置:", font=("Arial", 12))
        proxy_label.grid(row=6, column=0, sticky="nw", pady=8)

        proxy_frame = ctk.CTkFrame(tab, fg_color="transparent")
        proxy_frame.grid(row=6, column=1, sticky="ew", pady=8, padx=10)

        # 代理开关
        self.proxy_enabled_var = ctk.BooleanVar(value=False)
        self.proxy_switch = ctk.CTkSwitch(
            proxy_frame,
            text="启用代理",
            variable=self.proxy_enabled_var,
            command=self._on_proxy_toggle
        )
        self.proxy_switch.pack(anchor="w")

        # HTTP代理输入
        http_proxy_frame = ctk.CTkFrame(proxy_frame, fg_color="transparent")
        http_proxy_frame.pack(fill="x", pady=(5, 0))
        ctk.CTkLabel(http_proxy_frame, text="HTTP:", width=60).pack(side="left")
        self.http_proxy_entry = ctk.CTkEntry(http_proxy_frame, width=250, placeholder_text="http://127.0.0.1:7890")
        self.http_proxy_entry.pack(side="left", padx=5)

        # HTTPS代理输入
        https_proxy_frame = ctk.CTkFrame(proxy_frame, fg_color="transparent")
        https_proxy_frame.pack(fill="x", pady=(5, 0))
        ctk.CTkLabel(https_proxy_frame, text="HTTPS:", width=60).pack(side="left")
        self.https_proxy_entry = ctk.CTkEntry(https_proxy_frame, width=250, placeholder_text="http://127.0.0.1:7890")
        self.https_proxy_entry.pack(side="left", padx=5)

    def _build_other_tab(self, tab):
        """完成后动作与剪贴板监视；外观集中到独立页。"""
        tab.columnconfigure(1, weight=1)

        # ========== 下载完成后动作 ==========
        after_label = ctk.CTkLabel(tab, text="下载完成后:", font=("Arial", 12))
        after_label.grid(row=0, column=0, sticky="nw", pady=10)

        after_frame = ctk.CTkFrame(tab, fg_color="transparent")
        after_frame.grid(row=0, column=1, sticky="ew", pady=10, padx=10)

        # 开关默认全关，避免替用户做多余动作
        self.after_open_file_var = ctk.BooleanVar(value=False)
        ctk.CTkCheckBox(after_frame, text="打开文件", variable=self.after_open_file_var).pack(anchor="w")

        self.after_open_folder_var = ctk.BooleanVar(value=False)
        ctk.CTkCheckBox(after_frame, text="打开所在文件夹", variable=self.after_open_folder_var).pack(anchor="w")

        ctk.CTkLabel(after_frame, text='全部完成后', font=('Arial', 12)).pack(anchor='w', pady=(12, 4))
        self.completion_action_segment = ctk.CTkSegmentedButton(
            after_frame, values=list(COMPLETION_ACTION_LABELS.values()))
        self.completion_action_segment.pack(anchor='w')
        ctk.CTkLabel(after_frame, text='退出前 10 秒、关机前 60 秒均可取消。\n本轮有失败或未完成任务时不自动退出。',
                    font=('Arial', 11), text_color=('gray35', 'gray65'),
                    justify='left', wraplength=320).pack(anchor='w', pady=(6, 0))

        # ========== 剪贴板监视 ==========
        clipboard_label = ctk.CTkLabel(tab, text="剪贴板监视:", font=("Arial", 12))
        clipboard_label.grid(row=1, column=0, sticky="nw", pady=10)

        clipboard_frame = ctk.CTkFrame(tab, fg_color="transparent")
        clipboard_frame.grid(row=1, column=1, sticky="ew", pady=10, padx=10)

        self.clipboard_monitor_var = ctk.BooleanVar(value=True)
        ctk.CTkCheckBox(
            clipboard_frame,
            text="复制链接时提示添加任务",
            variable=self.clipboard_monitor_var,
        ).pack(anchor="w")

        ctk.CTkLabel(
            clipboard_frame, text="忽略站点（每行一个域名，含其子域名）:",
            font=("Arial", 11), text_color="gray",
        ).pack(anchor="w", pady=(8, 2))

        self.ignore_hosts_textbox = ctk.CTkTextbox(clipboard_frame, width=320, height=100)
        self.ignore_hosts_textbox.pack(anchor="w")

    def _build_appearance_tab(self, tab):
        """外观集中到一页，保持既有窗口尺寸和字体。"""
        tab.columnconfigure(1, weight=1)
        appearance_label = ctk.CTkLabel(tab, text="主题:", font=("Arial", 12))
        appearance_label.grid(row=0, column=0, sticky="w", pady=10)

        appearance_frame = ctk.CTkFrame(tab, fg_color="transparent")
        appearance_frame.grid(row=0, column=1, sticky="ew", pady=10, padx=10)

        self.appearance_segment = ctk.CTkSegmentedButton(
            appearance_frame, values=list(APPEARANCE_LABELS.values()), width=300)
        self.appearance_segment.pack(anchor="w")
        self.appearance_segment.set(APPEARANCE_LABELS[self.config.ui_appearance_mode])

        self.window_opacity_slider, self.window_opacity_label = self._build_opacity_row(
            tab, 1, '主窗口不透明度', 60,
            '100% 为完全不透明；主窗口文字会一起变淡。\n设置与确认窗口保持不透明。', self._on_opacity_change)

        # ========== 桌面悬浮条 ==========
        float_label = ctk.CTkLabel(tab, text="桌面悬浮条:", font=("Arial", 12))
        float_label.grid(row=2, column=0, sticky="nw", pady=10)

        float_frame = ctk.CTkFrame(tab, fg_color="transparent")
        float_frame.grid(row=2, column=1, sticky="ew", pady=10, padx=10)

        self.float_bar_var = ctk.BooleanVar(value=False)
        ctk.CTkCheckBox(
            float_frame,
            text="显示可拖动的速度悬浮条",
            variable=self.float_bar_var,
        ).pack(anchor="w")

        ctk.CTkLabel(
            float_frame,
            text="双击显示主窗口；右键可隐藏、退出或切换模式。",
            font=("Arial", 11), text_color=('gray35', 'gray65'), wraplength=320, justify='left',
        ).pack(anchor="w", pady=(4, 0))

        mode_row = ctk.CTkFrame(float_frame, fg_color="transparent")
        mode_row.pack(anchor="w", pady=(8, 0))
        ctk.CTkLabel(mode_row, text="悬浮条样式:", font=("Arial", 12)).pack(side="left", padx=(0, 8))
        self.float_bar_mode_segment = ctk.CTkSegmentedButton(
            mode_row, values=list(FLOAT_BAR_MODE_LABELS.values()), width=200)
        self.float_bar_mode_segment.pack(side="left")
        self.float_bar_mode_segment.set(FLOAT_BAR_MODE_LABELS[self.config.ui_float_bar_mode])

        self.float_opacity_slider, self.float_opacity_label = self._build_opacity_row(
            tab, 3, '悬浮条背景不透明度', 30, '只调整背景，文字和进度保持清晰。', self._on_opacity_change)

    def _build_opacity_row(self, tab, row, title, minimum, hint, command):
        ctk.CTkLabel(tab, text=title, font=('Arial', 12)).grid(
            row=row, column=0, sticky='nw', pady=(12, 8))
        frame = ctk.CTkFrame(tab, fg_color='transparent')
        frame.grid(row=row, column=1, sticky='ew', padx=10, pady=(12, 8))
        frame.columnconfigure(0, weight=1)
        slider = ctk.CTkSlider(frame, from_=minimum, to=100, number_of_steps=100-minimum,
                              width=230, command=command)
        slider.grid(row=0, column=0, sticky='ew', padx=(0, 10))
        label = ctk.CTkLabel(frame, text='100%', width=44, anchor='e', font=('Arial', 12))
        label.grid(row=0, column=1)
        ctk.CTkLabel(frame, text=hint, font=('Arial', 11), justify='left',
                    text_color=('gray35', 'gray65'), wraplength=320).grid(
            row=1, column=0, columnspan=2, sticky='w', pady=(6, 0))
        return slider, label

    def _load_appearance_actions(self):
        actions = self.config.after_download
        action = 'exit_app' if actions['exit_app'] else 'shutdown' if actions['shutdown'] else 'none'
        self.completion_action_segment.set(COMPLETION_ACTION_LABELS[action])
        self.window_opacity_slider.set(self.config.ui_window_opacity)
        self.float_opacity_slider.set(self.config.ui_float_bar_opacity)
        self.window_opacity_label.configure(text=f'{self.config.ui_window_opacity}%')
        self.float_opacity_label.configure(text=f'{self.config.ui_float_bar_opacity}%')

    def _on_opacity_change(self, value=None):
        window = normalize_opacity(self.window_opacity_slider.get(), 60)
        floating = normalize_opacity(self.float_opacity_slider.get(), 30)
        self.window_opacity_label.configure(text=f'{window}%')
        self.float_opacity_label.configure(text=f'{floating}%')
        if self._preview_callback:
            self._preview_callback(window, floating)

    def _restore_preview(self):
        if self._preview_callback:
            self._preview_callback(*self._opacity_baseline)

    def _load_config(self):
        """加载当前配置"""
        self.dir_entry.insert(0, self.config.download_dir)
        self._initial_temp_dir = self.config.temp_dir
        self.temp_entry.insert(0, self._initial_temp_dir)
        self.auto_classify_var.set(self.config.auto_classify_downloads)
        self.thread_slider.set(self.config.thread_count)
        self.thread_value_label.configure(text=str(self.config.thread_count))
        self.concurrent_slider.set(self.config.max_concurrent_downloads)
        self.concurrent_value_label.configure(text=str(self.config.max_concurrent_downloads))
        self.timeout_entry.insert(0, str(self.config.timeout))
        # 速度限制：字节转KB显示
        speed_limit_kb = self.config.speed_limit // 1024 if self.config.speed_limit > 0 else 0
        self.speed_limit_entry.insert(0, str(speed_limit_kb))
        # 代理配置
        proxy_cfg = self.config.proxy
        self.proxy_enabled_var.set(proxy_cfg.get("enabled", False))
        self.http_proxy_entry.insert(0, proxy_cfg.get("http", ""))
        self.https_proxy_entry.insert(0, proxy_cfg.get("https", ""))
        self._on_proxy_toggle()  # 根据开关状态设置输入框状态
        # 下载完成后动作
        after_cfg = self.config.after_download
        self.after_open_file_var.set(after_cfg["open_file"])
        self.after_open_folder_var.set(after_cfg["open_folder"])
        self._load_appearance_actions()
        # 剪贴板监视
        self.clipboard_monitor_var.set(self.config.clipboard_monitor_enabled)
        self.ignore_hosts_textbox.delete("1.0", "end")
        self.ignore_hosts_textbox.insert("1.0", "\n".join(self.config.clipboard_ignore_hosts))
        # 外观主题
        self.appearance_segment.set(APPEARANCE_LABELS[self.config.ui_appearance_mode])
        # 桌面悬浮条
        self.float_bar_var.set(self.config.ui_float_bar_enabled)
        self.float_bar_mode_segment.set(FLOAT_BAR_MODE_LABELS[self.config.ui_float_bar_mode])

    def _browse_directory(self):
        """浏览目录"""
        directory = filedialog.askdirectory(title="选择下载目录")
        if directory:
            self.dir_entry.delete(0, "end")
            self.dir_entry.insert(0, directory)

    def _browse_temp_directory(self):
        """浏览临时文件目录"""
        directory = filedialog.askdirectory(title="选择临时文件目录")
        if directory:
            self.temp_entry.delete(0, "end")
            self.temp_entry.insert(0, directory)

    def _on_thread_slider_change(self, value):
        """线程数滑块变化"""
        self.thread_value_label.configure(text=str(int(value)))

    def _on_concurrent_slider_change(self, value):
        """并发数滑块变化"""
        self.concurrent_value_label.configure(text=str(int(value)))

    def _on_proxy_toggle(self):
        """代理开关切换"""
        enabled = self.proxy_enabled_var.get()
        state = "normal" if enabled else "disabled"
        self.http_proxy_entry.configure(state=state)
        self.https_proxy_entry.configure(state=state)

    def _on_save(self):
        """保存设置"""
        try:
            # 验证输入
            download_dir = self.dir_entry.get().strip()
            if not download_dir:
                messagebox.showerror("错误", "下载目录不能为空！", parent=self)
                return

            # 临时目录先规范化再落地：抽盘/只读目录在这里就拒绝，不要等下载到一半才报错
            temp_dir = self.temp_entry.get().strip()
            if not temp_dir:
                messagebox.showerror("错误", "临时文件目录不能为空！", parent=self)
                return
            temp_dir = os.path.abspath(os.path.expandvars(os.path.expanduser(temp_dir)))
            try:
                os.makedirs(temp_dir, exist_ok=True)
                probe = os.path.join(temp_dir, '.daw-write-check')
                with open(probe, 'w', encoding='utf-8'):
                    pass
                os.remove(probe)
            except OSError as error:
                messagebox.showerror("错误", f"临时目录不可写：{error}", parent=self)
                return

            # 未改动时保留原始写法（如相对路径 "temp"），避免把程序目录写死进配置
            temp_dir_value = temp_dir
            if os.path.normcase(temp_dir) == os.path.normcase(getattr(self, '_initial_temp_dir', '')):
                temp_dir_value = self.config.get('temp_dir') or 'temp'

            thread_count = int(self.thread_slider.get())
            concurrent_count = int(self.concurrent_slider.get())

            timeout_str = self.timeout_entry.get().strip()
            if not timeout_str.isdigit():
                messagebox.showerror("错误", "超时时间必须是数字！", parent=self)
                return
            timeout = int(timeout_str)
            if timeout <= 0:
                messagebox.showerror("错误", "超时时间必须大于0！", parent=self)
                return

            # 速度限制验证：KB转字节存储
            speed_limit_str = self.speed_limit_entry.get().strip()
            if speed_limit_str and not speed_limit_str.isdigit():
                messagebox.showerror("错误", "总速度上限必须是数字！", parent=self)
                return
            speed_limit_kb = int(speed_limit_str) if speed_limit_str else 0
            speed_limit_bytes = speed_limit_kb * 1024  # KB转字节

            # 保存配置
            previous_settings = self.config.get_all()
            self.config.download_dir = download_dir
            self.config.auto_classify_downloads = bool(self.auto_classify_var.get())
            self.config.temp_dir = temp_dir_value
            self.config.thread_count = thread_count
            self.config.max_concurrent_downloads = concurrent_count
            self.config.set('timeout', timeout)
            self.config.speed_limit = speed_limit_bytes
            # 保存代理配置
            proxy_enabled = self.proxy_enabled_var.get()
            http_proxy = self.http_proxy_entry.get().strip()
            https_proxy = self.https_proxy_entry.get().strip()
            self.config.set_proxy(proxy_enabled, http_proxy, https_proxy)
            # 下载完成后动作
            after_config = {
                'open_file': self.after_open_file_var.get(),
                'open_folder': self.after_open_folder_var.get(),
                'shutdown': self.completion_action_segment.get() == COMPLETION_ACTION_LABELS['shutdown'],
                'exit_app': self.completion_action_segment.get() == COMPLETION_ACTION_LABELS['exit_app'],
            }
            self.config.after_download = after_config
            # 剪贴板监视（忽略站点由 setter 归一化：小写、去www.、去重）
            self.config.clipboard_monitor_enabled = bool(self.clipboard_monitor_var.get())
            self.config.clipboard_ignore_hosts = self.ignore_hosts_textbox.get("1.0", "end").splitlines()
            # 外观主题（控件上是中文标签，落库用配置值）
            appearance_mode = APPEARANCE_VALUE_BY_LABEL.get(self.appearance_segment.get(), "dark")
            self.config.ui_appearance_mode = appearance_mode
            # 桌面悬浮条
            float_bar_enabled = bool(self.float_bar_var.get())
            self.config.ui_float_bar_enabled = float_bar_enabled
            float_bar_mode = FLOAT_BAR_MODE_VALUE_BY_LABEL.get(
                self.float_bar_mode_segment.get(), "normal")
            self.config.ui_float_bar_mode = float_bar_mode
            self.config.ui_window_opacity = self.window_opacity_slider.get()
            self.config.ui_float_bar_opacity = self.float_opacity_slider.get()
            if not self.config.save():
                for key, value in previous_settings.items():
                    self.config.set(key, value)
                self._restore_preview()
                messagebox.showerror("错误", "配置文件写入失败，请检查目录权限和可用空间。", parent=self)
                return

            self._opacity_baseline = (self.config.ui_window_opacity, self.config.ui_float_bar_opacity)
            self._restore_preview()
            # 保存后通知外部：配置文件落地只是第一步，运行时也得马上同步，不然就是假保存
            if self.on_save_callback:
                runtime_settings = {
                    'download_dir': download_dir,
                    'auto_classify_downloads': bool(self.auto_classify_var.get()),
                    'temp_dir': temp_dir_value,
                    'thread_count': thread_count,
                    'max_concurrent_downloads': concurrent_count,
                    'timeout': timeout,
                    'speed_limit': speed_limit_bytes,
                    'proxy': {
                        'enabled': proxy_enabled,
                        'http': http_proxy,
                        'https': https_proxy,
                    },
                    'after_download': after_config,
                    'appearance_mode': appearance_mode,
                    'float_bar_enabled': float_bar_enabled,
                    'float_bar_mode': float_bar_mode,
                    'ui_window_opacity': self.config.ui_window_opacity,
                    'ui_float_bar_opacity': self.config.ui_float_bar_opacity,
                }
                self.on_save_callback(runtime_settings)

            # 保存成功不弹窗：设置框直接关掉，主窗口状态栏闪一条“设置已保存”，
            # 既确认结果又不打断操作（弹窗还要再点一次“确定”）。
            self.destroy()

        except Exception as e:
            self._restore_preview()
            messagebox.showerror("错误", f"保存设置失败: {e}", parent=self)
        finally:
            # 输入校验的提前返回同样属于未保存；成功时基线已更新为新值。
            self._restore_preview()

    def _on_cancel(self):
        """取消"""
        self.destroy()

    def _flash_status(self, text: str, seconds: float = 2.0):
        """按钮区短暂提示：不弹窗、不拦操作，到点自己消失"""
        if self._status_job is not None:
            try:
                self.after_cancel(self._status_job)
            except Exception:
                pass
            self._status_job = None
        self.status_label.configure(text=text)

        def clear():
            self._status_job = None
            try:
                if self.winfo_exists():
                    self.status_label.configure(text="")
            except Exception:
                pass

        self._status_job = self.after(int(seconds * 1000), clear)

    def destroy(self):
        """销毁前撤掉提示定时器，避免窗口没了回调还在排队"""
        self._restore_preview()
        if getattr(self, '_status_job', None) is not None:
            try:
                self.after_cancel(self._status_job)
            except Exception:
                pass
            self._status_job = None
        super().destroy()

    def _on_reset(self):
        """恢复默认设置"""
        if messagebox.askyesno("确认", "确定要恢复默认设置吗？", parent=self):
            if not self.config.reset():
                self._restore_preview()
                messagebox.showerror("错误", "恢复默认设置失败，配置文件未能保存。", parent=self)
                return
            self._opacity_baseline = (self.config.ui_window_opacity, self.config.ui_float_bar_opacity)
            self._restore_preview()
            if self.on_save_callback:
                self.on_save_callback(self.config.get_all())
            # 重新加载配置
            self.dir_entry.delete(0, "end")
            self.dir_entry.insert(0, self.config.download_dir)
            self.temp_entry.delete(0, "end")
            self._initial_temp_dir = self.config.temp_dir
            self.temp_entry.insert(0, self._initial_temp_dir)
            self.auto_classify_var.set(self.config.auto_classify_downloads)
            self.thread_slider.set(self.config.thread_count)
            self.thread_value_label.configure(text=str(self.config.thread_count))
            self.concurrent_slider.set(self.config.max_concurrent_downloads)
            self.concurrent_value_label.configure(text=str(self.config.max_concurrent_downloads))
            self.timeout_entry.delete(0, "end")
            self.timeout_entry.insert(0, str(self.config.timeout))
            # 速度限制重置
            self.speed_limit_entry.delete(0, "end")
            self.speed_limit_entry.insert(0, "0")
            # 代理配置重置
            self.proxy_enabled_var.set(False)
            self.http_proxy_entry.configure(state='normal')
            self.https_proxy_entry.configure(state='normal')
            self.http_proxy_entry.delete(0, "end")
            self.https_proxy_entry.delete(0, "end")
            self._on_proxy_toggle()
            # 下载完成后动作重置
            after_default = self.config.after_download
            self.after_open_file_var.set(after_default["open_file"])
            self.after_open_folder_var.set(after_default["open_folder"])
            self._load_appearance_actions()
            # 剪贴板监视重置
            self.clipboard_monitor_var.set(self.config.clipboard_monitor_enabled)
            self.ignore_hosts_textbox.delete("1.0", "end")
            self.ignore_hosts_textbox.insert("1.0", "\n".join(self.config.clipboard_ignore_hosts))
            # 外观主题重置
            self.appearance_segment.set(APPEARANCE_LABELS[self.config.ui_appearance_mode])
            # 桌面悬浮条重置
            self.float_bar_var.set(self.config.ui_float_bar_enabled)
            self.float_bar_mode_segment.set(FLOAT_BAR_MODE_LABELS[self.config.ui_float_bar_mode])
            self._flash_status("已恢复默认设置")
