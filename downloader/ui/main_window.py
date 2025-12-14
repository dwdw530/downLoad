# -*- coding: utf-8 -*-
"""
GUI主窗口
老王说：界面简单够用就行，别搞那些花里胡哨的！
"""
import customtkinter as ctk
import os
import subprocess
import threading
from tkinter import messagebox, filedialog
from typing import Dict
from downloader.core.task_manager import TaskManager
from downloader.utils.file_utils import format_speed


class MainWindow(ctk.CTk):
    """主窗口"""

    def __init__(self, task_manager: TaskManager):
        super().__init__()

        self.task_manager = task_manager
        self.task_widgets = {}  # {task_id: widget}

        # 设置窗口
        self.title("老王下载器 v1.0")
        self.geometry("900x600")

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

        # 启动UI更新线程
        self._start_ui_update_thread()

        # 绑定窗口关闭事件
        self.protocol("WM_DELETE_WINDOW", self._on_window_close)

    def _minimize_app(self):
        """最小化窗口（继续后台下载）"""
        try:
            # 任务栏最小化，比“啥也不干”强一万倍
            self.iconify()
        except Exception as e:
            print(f"[错误] 最小化失败: {e}")

    def _exit_app(self):
        """退出程序（停止下载并释放资源）"""
        try:
            # 退出不清理线程？那就是找骂：ThreadPoolExecutor能把进程吊到天荒地老
            self.task_manager.shutdown()
        except Exception as e:
            print(f"[错误] 退出清理失败: {e}")
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

        # 任务列表区域（使用Scrollable Frame）
        self.task_list_frame = ctk.CTkScrollableFrame(self, label_text="下载任务列表")
        self.task_list_frame.pack(fill="both", expand=True, padx=10, pady=10)

        # 底部状态栏
        self.status_bar = ctk.CTkFrame(self, height=40)
        self.status_bar.pack(fill="x", padx=10, pady=10)

        self.status_label = ctk.CTkLabel(self.status_bar, text="总速度: 0 KB/s | 剩余任务: 0")
        self.status_label.pack(side="left", padx=10)

    def _on_add_task(self):
        """添加任务对话框"""
        dialog = ctk.CTkInputDialog(text="请输入下载链接:", title="添加下载任务")
        url = dialog.get_input()

        if url:
            # 询问保存位置
            save_dir = filedialog.askdirectory(title="选择保存位置")
            if not save_dir:
                save_dir = None  # 使用默认目录

            # 添加任务
            task_id = self.task_manager.add_task(url, save_path=save_dir)
            if task_id:
                messagebox.showinfo("成功", "任务添加成功！")
            else:
                messagebox.showerror("错误", "任务添加失败！")

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
        dialog = SettingsDialog(self, self.task_manager.engine.config)
        self.wait_window(dialog)

    def _load_existing_tasks(self):
        """加载现有任务"""
        tasks = self.task_manager.get_all_tasks()
        for task in tasks:
            self._add_task_widget(task)

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

        # 进度条
        progress_bar = ctk.CTkProgressBar(info_frame, width=400)
        progress_bar.pack(side="left", padx=5)
        progress_bar.set(0)

        # 进度百分比
        progress_label = ctk.CTkLabel(info_frame, text="0%", width=60)
        progress_label.pack(side="left", padx=5)

        # 速度
        speed_label = ctk.CTkLabel(info_frame, text="0 KB/s", width=100)
        speed_label.pack(side="left", padx=5)

        # 状态
        status_label = ctk.CTkLabel(info_frame, text=self._get_status_text(task['status']), width=80)
        status_label.pack(side="left", padx=5)

        # 按钮区域
        button_frame = ctk.CTkFrame(task_frame, fg_color="transparent")
        button_frame.pack(fill="x", padx=10, pady=5)

        # 开始/暂停按钮
        if task['status'] == 'downloading':
            action_btn = ctk.CTkButton(button_frame, text="⏸ 暂停", width=80,
                                      command=lambda: self._on_pause_task(task_id))
        elif task['status'] in ('paused', 'failed', 'pending'):
            action_btn = ctk.CTkButton(button_frame, text="▶ 开始", width=80,
                                      command=lambda: self._on_start_task(task_id))
        else:
            action_btn = ctk.CTkButton(button_frame, text="✓ 完成", width=80, state="disabled")

        action_btn.pack(side="left", padx=5)

        # 取消按钮
        cancel_btn = ctk.CTkButton(button_frame, text="✗ 取消", width=80,
                                   command=lambda: self._on_cancel_task(task_id))
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
            'speed_label': speed_label,
            'status_label': status_label,
            'action_btn': action_btn,
            'location_btn': location_btn,
        }

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
                try:
                    if os.path.exists(task['save_path']):
                        os.remove(task['save_path'])
                        print(f"[删除] 文件已删除: {task['save_path']}")

                    # 删除临时分块文件
                    if task['support_range']:
                        chunks = self.task_manager.db.get_chunks(task_id)
                        for chunk in chunks:
                            if chunk['temp_file'] and os.path.exists(chunk['temp_file']):
                                os.remove(chunk['temp_file'])
                except Exception as e:
                    messagebox.showerror("错误", f"删除文件失败: {e}")

            # 删除数据库记录
            self.task_manager.delete_task(task_id)

            # 移除UI组件
            if task_id in self.task_widgets:
                self.task_widgets[task_id]['frame'].destroy()
                del self.task_widgets[task_id]

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

    def _on_task_added(self, task_id: str):
        """任务添加回调"""
        task = self.task_manager.get_task(task_id)
        if task:
            self.after(0, lambda: self._add_task_widget(task))

    def _on_task_status_changed(self, task_id: str, status: str, message: str):
        """任务状态变更回调"""
        self.after(0, lambda: self._update_task_status(task_id, status))

    def _on_task_progress(self, task_id: str, downloaded_size: int, total_size: int, speed: float):
        """任务进度回调"""
        self.after(0, lambda: self._update_task_progress(task_id, downloaded_size, total_size, speed))

    def _update_task_status(self, task_id: str, status: str):
        """更新任务状态UI"""
        if task_id not in self.task_widgets:
            return

        widgets = self.task_widgets[task_id]
        widgets['status_label'].configure(text=self._get_status_text(status))

        # 更新按钮
        action_btn = widgets['action_btn']
        if status == 'downloading':
            action_btn.configure(text="⏸ 暂停", command=lambda: self._on_pause_task(task_id), state="normal")
        elif status in ('paused', 'failed'):
            action_btn.configure(text="▶ 继续", command=lambda: self._on_start_task(task_id), state="normal")
        elif status == 'pending':
            action_btn.configure(text="▶ 开始", command=lambda: self._on_start_task(task_id), state="normal")
        elif status in ('completed', 'cancelled'):
            action_btn.configure(text="✓ 完成", state="disabled")

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

    def _start_ui_update_thread(self):
        """启动UI更新线程（更新状态栏）"""
        def update_status_bar():
            while True:
                threading.Event().wait(1)  # 每秒更新一次

                # 获取统计信息
                stats = self.task_manager.get_statistics()
                downloading_tasks = self.task_manager.get_downloading_tasks()

                # 计算总速度
                total_speed = sum(task['speed'] for task in downloading_tasks)

                # 更新状态栏
                status_text = f"总速度: {format_speed(total_speed)} | 下载中: {stats['downloading']} | 等待: {stats['pending']}"
                self.after(0, lambda: self.status_label.configure(text=status_text))

        thread = threading.Thread(target=update_status_bar, daemon=True)
        thread.start()

    @staticmethod
    def _get_status_text(status: str) -> str:
        """获取状态文本"""
        status_map = {
            'pending': '等待中',
            'downloading': '下载中',
            'paused': '已暂停',
            'completed': '已完成',
            'failed': '失败',
            'cancelled': '已取消'
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
                print(f"[错误] 关闭确认弹窗异常: {e}")
                if messagebox.askyesno("退出确认", "关闭窗口失败了，是否直接退出程序？", parent=self):
                    self._exit_app()
        elif close_behavior == "exit":
            self._exit_app()
        elif close_behavior == "minimize":
            self._minimize_app()


class CloseConfirmDialog(ctk.CTkToplevel):
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

        # 这破弹窗别整得跟全屏似的：固定一个紧凑尺寸，直接变窄变好看
        self._apply_compact_geometry(parent)

        # 键盘快捷键：别让用户找不到“确定/取消”
        self.bind("<Escape>", lambda _e=None: self._on_cancel())
        self.bind("<Return>", lambda _e=None: self._on_confirm())
        self.focus_force()

    def _apply_compact_geometry(self, parent):
        """应用紧凑窗口尺寸（优先“变窄”，别搞一堆空白）"""
        self.update_idletasks()

        # 目标尺寸：你要“窄一点”，那就直接给个窄的；高DPI下会自动缩放
        width = 300
        height = 220

        # winfo_* 返回的是“实际像素”，geometry 需要“逻辑尺寸”，要按缩放换算，不然弹窗能飘到屏幕外去
        scale = ctk.ScalingTracker.get_window_scaling(parent)
        parent_x = int(parent.winfo_x() / scale)
        parent_y = int(parent.winfo_y() / scale)
        parent_w = int(parent.winfo_width() / scale)
        parent_h = int(parent.winfo_height() / scale)

        x = parent_x + (parent_w - width) // 2
        y = parent_y + (parent_h - height) // 2
        self.geometry(f"{width}x{height}+{max(0, x)}+{max(0, y)}")
        self.minsize(width, height)
        self.maxsize(width, height)

    def _create_ui(self):
        """创建UI"""
        # CTkFrame 默认会给个巨大的固定尺寸（高DPI下更离谱），不手动压成0会导致弹窗肥得像头猪
        main_frame = ctk.CTkFrame(self, width=0, height=0)
        main_frame.pack(fill="both", expand=True, padx=10, pady=10)

        # 提示信息
        msg_label = ctk.CTkLabel(main_frame, text="确定要关闭老王下载器吗？", font=("Arial", 12))
        msg_label.pack(pady=(4, 3))

        # 选项
        self.action_var = ctk.StringVar(value="minimize")

        minimize_radio = ctk.CTkRadioButton(
            main_frame,
            text="最小化（继续下载）",
            variable=self.action_var,
            value="minimize",
            font=("Arial", 10)
        )
        minimize_radio.pack(anchor="w", pady=1)

        exit_radio = ctk.CTkRadioButton(
            main_frame,
            text="退出（停止下载）",
            variable=self.action_var,
            value="exit",
            font=("Arial", 10)
        )
        exit_radio.pack(anchor="w", pady=1)

        # 记住选择
        self.remember_var = ctk.BooleanVar(value=False)
        remember_check = ctk.CTkCheckBox(
            main_frame,
            text="记住选择，下次不提示",
            variable=self.remember_var,
            font=("Arial", 9)
        )
        remember_check.pack(pady=(3, 0))

        # 按钮
        button_frame = ctk.CTkFrame(main_frame, fg_color="transparent", width=0, height=0)
        button_frame.pack(pady=(6, 0))

        self.confirm_btn = ctk.CTkButton(button_frame, text="确定", command=self._on_confirm, width=76, height=24)
        self.confirm_btn.pack(side="left", padx=6)

        cancel_btn = ctk.CTkButton(button_frame, text="取消", command=self._on_cancel, width=76, height=24)
        cancel_btn.pack(side="left", padx=6)

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
