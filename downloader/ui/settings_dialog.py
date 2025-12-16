# -*- coding: utf-8 -*-
"""
设置对话框
老王说：设置简单点就行，别搞太复杂！
"""
import customtkinter as ctk
from tkinter import filedialog, messagebox
from downloader.utils.config import ConfigManager


class SettingsDialog(ctk.CTkToplevel):
    """设置对话框"""

    def __init__(self, parent, config_manager: ConfigManager):
        super().__init__(parent)

        self.config = config_manager

        # 设置窗口
        self.title("设置")
        self.geometry("500x520")  # 加高给代理设置腾位置
        self.resizable(False, False)

        # 模态对话框
        self.transient(parent)
        self.grab_set()

        # 创建UI
        self._create_ui()

        # 加载当前配置
        self._load_config()

    def _create_ui(self):
        """创建UI组件"""
        # 主容器
        main_frame = ctk.CTkFrame(self)
        main_frame.pack(fill="both", expand=True, padx=20, pady=20)

        # 下载目录设置
        dir_label = ctk.CTkLabel(main_frame, text="默认下载目录:", font=("Arial", 12))
        dir_label.grid(row=0, column=0, sticky="w", pady=10)

        dir_frame = ctk.CTkFrame(main_frame, fg_color="transparent")
        dir_frame.grid(row=0, column=1, sticky="ew", pady=10, padx=10)

        self.dir_entry = ctk.CTkEntry(dir_frame, width=250)
        self.dir_entry.pack(side="left", padx=5)

        browse_btn = ctk.CTkButton(dir_frame, text="浏览", command=self._browse_directory, width=80)
        browse_btn.pack(side="left")

        # 线程数设置
        thread_label = ctk.CTkLabel(main_frame, text="下载线程数:", font=("Arial", 12))
        thread_label.grid(row=1, column=0, sticky="w", pady=10)

        self.thread_slider = ctk.CTkSlider(main_frame, from_=1, to=16, number_of_steps=15, width=200)
        self.thread_slider.grid(row=1, column=1, sticky="w", pady=10, padx=10)

        self.thread_value_label = ctk.CTkLabel(main_frame, text="8", font=("Arial", 12))
        self.thread_value_label.grid(row=1, column=2, sticky="w", pady=10)

        self.thread_slider.configure(command=self._on_thread_slider_change)

        # 并发任务数设置
        concurrent_label = ctk.CTkLabel(main_frame, text="同时下载任务数:", font=("Arial", 12))
        concurrent_label.grid(row=2, column=0, sticky="w", pady=10)

        self.concurrent_slider = ctk.CTkSlider(main_frame, from_=1, to=5, number_of_steps=4, width=200)
        self.concurrent_slider.grid(row=2, column=1, sticky="w", pady=10, padx=10)

        self.concurrent_value_label = ctk.CTkLabel(main_frame, text="3", font=("Arial", 12))
        self.concurrent_value_label.grid(row=2, column=2, sticky="w", pady=10)

        self.concurrent_slider.configure(command=self._on_concurrent_slider_change)

        # 超时设置
        timeout_label = ctk.CTkLabel(main_frame, text="请求超时(秒):", font=("Arial", 12))
        timeout_label.grid(row=3, column=0, sticky="w", pady=10)

        self.timeout_entry = ctk.CTkEntry(main_frame, width=200)
        self.timeout_entry.grid(row=3, column=1, sticky="w", pady=10, padx=10)

        # ========== 代理设置 ==========
        proxy_label = ctk.CTkLabel(main_frame, text="代理设置:", font=("Arial", 12))
        proxy_label.grid(row=4, column=0, sticky="nw", pady=10)

        proxy_frame = ctk.CTkFrame(main_frame, fg_color="transparent")
        proxy_frame.grid(row=4, column=1, columnspan=2, sticky="ew", pady=10, padx=10)

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

        # 速度限制设置（row改为5）
        speed_limit_label = ctk.CTkLabel(main_frame, text="速度限制(KB/s):", font=("Arial", 12))
        speed_limit_label.grid(row=5, column=0, sticky="w", pady=10)

        speed_frame = ctk.CTkFrame(main_frame, fg_color="transparent")
        speed_frame.grid(row=5, column=1, sticky="ew", pady=10, padx=10)

        self.speed_limit_entry = ctk.CTkEntry(speed_frame, width=120, placeholder_text="0=不限速")
        self.speed_limit_entry.pack(side="left", padx=5)

        speed_hint_label = ctk.CTkLabel(speed_frame, text="(0表示不限速)", font=("Arial", 10), text_color="gray")
        speed_hint_label.pack(side="left")

        # 按钮区域
        button_frame = ctk.CTkFrame(self, fg_color="transparent")
        button_frame.pack(fill="x", padx=20, pady=10)

        save_btn = ctk.CTkButton(button_frame, text="保存", command=self._on_save, width=100)
        save_btn.pack(side="right", padx=5)

        cancel_btn = ctk.CTkButton(button_frame, text="取消", command=self._on_cancel, width=100)
        cancel_btn.pack(side="right", padx=5)

        reset_btn = ctk.CTkButton(button_frame, text="恢复默认", command=self._on_reset, width=100)
        reset_btn.pack(side="left", padx=5)

        # 配置grid权重
        main_frame.columnconfigure(1, weight=1)

    def _load_config(self):
        """加载当前配置"""
        self.dir_entry.insert(0, self.config.download_dir)
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

    def _browse_directory(self):
        """浏览目录"""
        directory = filedialog.askdirectory(title="选择下载目录")
        if directory:
            self.dir_entry.delete(0, "end")
            self.dir_entry.insert(0, directory)

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

            thread_count = int(self.thread_slider.get())
            concurrent_count = int(self.concurrent_slider.get())

            timeout_str = self.timeout_entry.get().strip()
            if not timeout_str.isdigit():
                messagebox.showerror("错误", "超时时间必须是数字！", parent=self)
                return
            timeout = int(timeout_str)

            # 速度限制验证：KB转字节存储
            speed_limit_str = self.speed_limit_entry.get().strip()
            if speed_limit_str and not speed_limit_str.isdigit():
                messagebox.showerror("错误", "速度限制必须是数字！", parent=self)
                return
            speed_limit_kb = int(speed_limit_str) if speed_limit_str else 0
            speed_limit_bytes = speed_limit_kb * 1024  # KB转字节

            # 保存配置
            self.config.download_dir = download_dir
            self.config.thread_count = thread_count
            self.config.max_concurrent_downloads = concurrent_count
            self.config.set('timeout', timeout)
            self.config.speed_limit = speed_limit_bytes
            # 保存代理配置
            proxy_enabled = self.proxy_enabled_var.get()
            http_proxy = self.http_proxy_entry.get().strip()
            https_proxy = self.https_proxy_entry.get().strip()
            self.config.set_proxy(proxy_enabled, http_proxy, https_proxy)
            self.config.save()

            messagebox.showinfo("成功", "设置已保存！", parent=self)
            self.destroy()

        except Exception as e:
            messagebox.showerror("错误", f"保存设置失败: {e}", parent=self)

    def _on_cancel(self):
        """取消"""
        self.destroy()

    def _on_reset(self):
        """恢复默认设置"""
        if messagebox.askyesno("确认", "确定要恢复默认设置吗？", parent=self):
            self.config.reset()
            # 重新加载配置
            self.dir_entry.delete(0, "end")
            self.dir_entry.insert(0, self.config.download_dir)
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
            self.http_proxy_entry.delete(0, "end")
            self.https_proxy_entry.delete(0, "end")
            self._on_proxy_toggle()
            messagebox.showinfo("成功", "已恢复默认设置！", parent=self)
