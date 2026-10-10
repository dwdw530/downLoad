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
from queue import Empty, SimpleQueue
from tkinter import messagebox, filedialog, PhotoImage
from typing import Dict
from downloader.core.task_manager import TaskManager
from downloader.utils.file_utils import format_speed, format_size, format_remaining
from downloader.ui.tray_manager import TrayManager


logger = logging.getLogger(__name__)


class MainWindow(ctk.CTk):
    """主窗口"""

    def __init__(self, task_manager: TaskManager):
        super().__init__()

        self.task_manager = task_manager
        self.task_widgets = {}  # {task_id: widget}
        self._ui_events = SimpleQueue()
        self._shutdown_dialog_open = False  # 关机确认只能同时开一个，避免多任务同时完成时叠窗
        self._batch_add_pending = 0  # 后台批量添加中的链接数（只用于状态栏提示）

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

        # 设置主题
        ctk.set_appearance_mode("dark")
        ctk.set_default_color_theme("blue")

        # 创建UI
        self._create_ui()

        # 设置任务管理器回调
        self.task_manager.set_task_added_callback(self._on_task_added)
        self.task_manager.set_task_status_changed_callback(self._on_task_status_changed)
        self.task_manager.engine.set_progress_callback(self._on_task_progress)

        # 加载现有任务
        self._load_existing_tasks()

        # 由主线程处理下载回调，避免暂停时与工作线程互相等待。
        self.after(50, self._drain_ui_events)
        self._start_ui_update_thread()

        # 绑定窗口关闭事件
        self.protocol("WM_DELETE_WINDOW", self._on_window_close)

        # 初始化系统托盘
        self._init_tray()

    def _init_tray(self):
        """初始化系统托盘"""
        self.tray_manager = TrayManager("daw下载器")
        self.tray_manager.set_show_window_callback(self._show_from_tray)
        self.tray_manager.set_exit_callback(lambda: self._ui_events.put((self._exit_app, ())))
        self.tray_manager.start()

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
        try:
            if hasattr(self, 'browser_bridge'):
                self.browser_bridge.close()
            # 停止托盘
            if hasattr(self, 'tray_manager'):
                self.tray_manager.stop()
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

        # 设置按钮
        settings_btn = ctk.CTkButton(toolbar, text="⚙ 设置", command=self._on_settings, width=100)
        settings_btn.pack(side="right", padx=5)

        # 历史按钮
        history_btn = ctk.CTkButton(toolbar, text="📜 历史", command=self._on_history, width=100)
        history_btn.pack(side="right", padx=5)

        # 过滤行：搜索 + 隐藏已完成
        filter_frame = ctk.CTkFrame(self, fg_color="transparent")
        filter_frame.pack(fill="x", padx=10, pady=(0, 5))

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

        # 任务列表区域（使用Scrollable Frame）
        self.task_list_frame = ctk.CTkScrollableFrame(self, label_text="下载任务列表")
        self.task_list_frame.pack(fill="both", expand=True, padx=10, pady=10)

        # 空态提示：由 _apply_task_filter 统一控制显示
        self.empty_label = ctk.CTkLabel(self.task_list_frame, text="暂无任务", text_color="gray")
        self.empty_label.pack(pady=20)

        # 底部状态栏
        self.status_bar = ctk.CTkFrame(self, height=40)
        self.status_bar.pack(fill="x", padx=10, pady=10)

        self.status_label = ctk.CTkLabel(self.status_bar, text="总速度: 0 KB/s | 剩余任务: 0")
        self.status_label.pack(side="left", padx=10)

    def _on_add_task(self):
        """添加任务对话框（支持多行批量）"""
        dialog = AddTaskDialog(self)
        self.wait_window(dialog)

        if not dialog.confirmed:
            return

        urls, skipped = self._dedupe_urls(dialog.urls)
        if not urls:
            return

        if len(urls) == 1:
            # 单链接保持原同步流程，行为和原来完全一致
            task_id = self.task_manager.add_task(
                url=urls[0],
                save_path=dialog.save_dir,
                expected_hash=dialog.expected_hash,
                hash_type=dialog.hash_type,
                start_later=dialog.start_later,
            )
            if task_id:
                message = "任务添加成功！"
                if skipped:
                    message += f"\n已跳过 {skipped} 条重复链接"
                messagebox.showinfo("成功", message)
            else:
                messagebox.showerror("错误", "任务添加失败！")
            return

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
                    reason = "无法获取文件大小或链接不可用"
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
                        failures.append(f"{url}（{reason}）")
            finally:
                self._batch_add_pending -= len(urls)
                # 日志不记链接，只记数量，避免把 URL 写进日志
                logger.info("批量添加完成: 成功=%d 失败=%d 跳过=%d", success, len(failures), skipped)
                self._ui_events.put((self._show_add_summary, (success, failures, skipped)))

        threading.Thread(target=worker, daemon=True).start()

    def _show_add_summary(self, success: int, failures, skipped: int):
        """批量添加结果汇总（成功失败都只弹一个框）"""
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

    def _on_pause_all(self):
        """暂停全部任务"""
        count = self.task_manager.pause_all()
        messagebox.showinfo("提示", f"已暂停 {count} 个任务")

    def _on_resume_all(self):
        """继续全部任务"""
        count = self.task_manager.resume_all()
        messagebox.showinfo("提示", f"已继续 {count} 个任务")

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
            config = self.task_manager.engine.config

            # 配置对象这里再显式刷一次，避免后续维护把保存顺序改崩了
            if 'download_dir' in settings:
                config.download_dir = settings['download_dir']
            if 'thread_count' in settings:
                config.thread_count = settings['thread_count']
            if 'timeout' in settings:
                config.set('timeout', settings['timeout'])
            if 'speed_limit' in settings:
                config.speed_limit = settings['speed_limit']
                for downloaders in list(self.task_manager.engine.active_downloaders.values()):
                    limit = max(1, config.speed_limit // len(downloaders)) if config.speed_limit and downloaders else 0
                    for downloader in downloaders:
                        downloader.speed_limiter.set_limit(limit)

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
            self._apply_task_filter()

        # 兜一层after，保证控件操作和队列调度都在主线程触发
        self.after(0, apply_runtime_settings)

    def _on_history(self):
        """打开下载历史对话框"""
        from downloader.ui.history_dialog import HistoryDialog
        dialog = HistoryDialog(self, self.task_manager.db)
        self.wait_window(dialog)

    def _load_existing_tasks(self):
        """加载现有任务"""
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

        # 没有任务时上面的回调一个都不会跑，这里补一次，把空态和“显示 N / M”初始化
        self._apply_task_filter()

    def _add_task_widget(self, task: Dict):
        """添加任务UI组件"""
        task_id = task['task_id']

        # 创建任务卡片
        task_frame = ctk.CTkFrame(self.task_list_frame)
        task_frame.pack(fill="x", pady=5)

        # 文件名标签
        filename_label = ctk.CTkLabel(task_frame, text=task['filename'], font=("Arial", 14, "bold"))
        filename_label.pack(anchor="w", padx=10, pady=5)

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
            'location_btn': location_btn,
            # 过滤用的缓存，避免每次输入都查库
            'filename': task.get('filename') or '',
            'url': task.get('url') or '',
            'status': task.get('status') or 'pending',
        }

        self._apply_task_filter()

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
        task = self.task_manager.get_task(task_id)
        if task['status'] == 'paused':
            self.task_manager.resume_task(task_id)
        else:
            self.task_manager.start_task(task_id)

    def _on_pause_task(self, task_id: str):
        """暂停任务"""
        self.task_manager.pause_task(task_id)

    def _on_cancel_task(self, task_id: str):
        """取消任务"""
        if messagebox.askyesno("确认", "确定要取消该任务吗？"):
            self.task_manager.cancel_task(task_id)

    def _on_delete_task(self, task_id: str):
        """删除任务"""
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
                    temporary_files.append(os.path.join(self.task_manager.engine.config.temp_dir, f'{task_id}.tmp'))
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

            # 移除UI组件
            if task_id in self.task_widgets:
                self.task_widgets[task_id]['frame'].destroy()
                del self.task_widgets[task_id]
                self._apply_task_filter()

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
            self._schedule_shutdown_if_idle()

    def _has_active_tasks(self) -> bool:
        """是否还有在跑的任务（下载中/等待/校验中）"""
        return any(
            task.get('status') in ('downloading', 'pending', 'verifying')
            for task in self.task_manager.get_all_tasks()
        )

    def _schedule_shutdown_if_idle(self):
        """队列里还有任务就先不关机，等最后一个完成时再弹确认"""
        if self._shutdown_dialog_open or self._has_active_tasks():
            return

        self._shutdown_dialog_open = True
        try:
            dialog = ShutdownConfirmDialog(self, on_shutdown=self._perform_shutdown)
            self.wait_window(dialog)
        finally:
            self._shutdown_dialog_open = False

    def _perform_shutdown(self):
        """等倒计时确认走完才真关机，取消就是什么都不做"""
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
        self._ui_events.put((self._update_task_status, (task_id, status)))

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

    def _update_task_status(self, task_id: str, status: str):
        """更新任务状态UI"""
        if task_id not in self.task_widgets:
            return

        widgets = self.task_widgets[task_id]
        widgets['status'] = status
        widgets['status_label'].configure(text=self._get_status_text(status))
        if status != 'downloading':
            widgets['speed_label'].configure(text=format_speed(0))
            widgets['eta_label'].configure(text="剩余 --")
        if 'cancel_btn' in widgets:
            widgets['cancel_btn'].configure(state='disabled' if status == 'completed' else 'normal')

        # 更新按钮
        action_btn = widgets['action_btn']
        self._configure_action_button(task_id, status, action_btn)

        self._apply_task_filter()

    def _update_task_progress(self, task_id: str, downloaded_size: int, total_size: int, speed: float):
        """更新任务进度UI"""
        if task_id not in self.task_widgets:
            return

        widgets = self.task_widgets[task_id]

        # 更新进度条
        progress = downloaded_size / total_size if total_size > 0 else 0
        widgets['progress_bar'].set(progress)

        # 更新百分比
        widgets['progress_label'].configure(text=f"{progress * 100:.1f}%")

        # 更新速度
        widgets['speed_label'].configure(text=format_speed(speed))

        # 已下载/总大小 + 剩余时间：总大小未知时别编数字
        total_text = format_size(total_size) if total_size > 0 else "未知"
        widgets['size_label'].configure(text=f"已下载 {format_size(downloaded_size)} / {total_text}")
        if widgets.get('status') == 'downloading':
            remaining = max(0, total_size - downloaded_size) if total_size > 0 else 0
            widgets['eta_label'].configure(text=f"剩余 {format_remaining(remaining, speed)}")
        else:
            widgets['eta_label'].configure(text="剩余 --")

    def _start_ui_update_thread(self):
        """使用Tk定时器更新状态栏，窗口销毁后不再保留后台线程。"""
        def update_status_bar():
            stats = self.task_manager.get_statistics()
            downloading_tasks = self.task_manager.get_downloading_tasks()
            total_speed = sum(task['speed'] for task in downloading_tasks)
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
        """按搜索词与“隐藏已完成”统一控制任务行显示"""
        keyword = self.search_entry.get().strip().lower()
        hide_completed = bool(self.hide_completed_var.get())
        visible = 0

        for widgets in self.task_widgets.values():
            matched = not keyword or keyword in widgets['filename'].lower() or keyword in widgets['url'].lower()
            if hide_completed and widgets['status'] == 'completed':
                matched = False

            frame = widgets['frame']
            if matched:
                if not frame.winfo_manager():
                    frame.pack(fill="x", pady=5)
                visible += 1
            elif frame.winfo_manager():
                frame.pack_forget()

        if visible == 0:
            self.empty_label.configure(text="没有匹配的任务" if (keyword or hide_completed) else "暂无任务")
            self.empty_label.pack(pady=20)
        elif self.empty_label.winfo_manager():
            self.empty_label.pack_forget()

        self.filter_hint_label.configure(text=f"显示 {visible} / {len(self.task_widgets)}")

    @staticmethod
    def _get_status_text(status: str) -> str:
        """获取状态文本"""
        status_map = {
            'pending': '等待中',
            'downloading': '下载中',
            'paused': '已暂停',
            'completed': '已完成',
            'failed': '失败',
            'cancelled': '已取消',
            'verifying': '校验中',
            'verify_failed': '校验失败',
        }
        return status_map.get(status, status)

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


class CloseConfirmDialog(ctk.CTkToplevel):
    """关闭确认对话框"""

    def focus_set(self):
        if self.winfo_exists():
            super().focus_set()

    focus = focus_set

    def _revert_withdraw_after_windows_set_titlebar_color(self):
        # CustomTkinter 5.2会延迟恢复标题栏，快速关闭时窗口可能已销毁。
        if self.winfo_exists():
            super()._revert_withdraw_after_windows_set_titlebar_color()

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


class ShutdownConfirmDialog(ctk.CTkToplevel):
    """全部下载完成后的关机确认（倒计时结束才真关机，关窗=取消）"""

    DEFAULT_COUNTDOWN = 60

    def __init__(self, parent, on_shutdown=None, countdown: int = DEFAULT_COUNTDOWN):
        super().__init__(parent)

        self._on_shutdown = on_shutdown
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
        self.destroy()
        if self._on_shutdown:
            self._on_shutdown()


class DeleteTaskDialog(ctk.CTkToplevel):
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


class AddTaskDialog(ctk.CTkToplevel):
    """添加任务对话框 - 支持多行批量与哈希校验"""

    def _revert_withdraw_after_windows_set_titlebar_color(self):
        # 与退出对话框相同：标题栏延迟回调可能晚于窗口销毁。
        if self.winfo_exists():
            super()._revert_withdraw_after_windows_set_titlebar_color()

    def focus_set(self):
        if self.winfo_exists():
            super().focus_set()

    focus = focus_set

    def __init__(self, parent):
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

        # 创建UI
        self._create_ui()

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

    def _create_ui(self):
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
