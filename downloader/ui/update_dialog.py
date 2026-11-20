"""Non-blocking update checks; upgrades remain an explicit browser action."""
from queue import Empty, SimpleQueue
import threading
import webbrowser

import customtkinter as ctk
import requests

from downloader.ui.ctk_helpers import SafeToplevelMixin
from downloader.utils.update_check import (
    RELEASES_URL, check_for_update, update_failure_message)
from downloader.version import APP_VERSION


class UpdateDialog(SafeToplevelMixin, ctk.CTkToplevel):
    def __init__(self, parent):
        super().__init__(parent)
        self.title('检查更新')
        self.geometry('640x200')
        self.minsize(540, 180)
        self.transient(parent)
        self._results = SimpleQueue()
        self._closed = False
        self._busy = False
        self._poll_id = None
        self._release_url = RELEASES_URL
        self._proxies = parent.task_manager.engine.config.proxies
        self.protocol('WM_DELETE_WINDOW', self.destroy)

        ctk.CTkLabel(self, text=f'当前程序版本：v{APP_VERSION}',
                     font=('', 16, 'bold')).pack(
                         anchor='w', padx=20, pady=(18, 8))
        self.status_label = ctk.CTkLabel(
            self, text='', anchor='w', wraplength=580)
        self.status_label.pack(fill='x', padx=20, pady=(0, 8))
        self.notes = ctk.CTkTextbox(self, wrap='word')
        self.notes.configure(state='disabled')
        self.upgrade_hint = ctk.CTkLabel(
            self, justify='left', anchor='w', wraplength=580,
            text='安装版：退出下载器及 BrowserBridge 后，沿用原目录安装。\n'
                 '便携版：完整更新发布文件，保留配置、下载文件和断点。\n'
                 '扩展有更新时，请重新加载并刷新视频页面。')
        self.button_frame = ctk.CTkFrame(self, fg_color='transparent')
        self.button_frame.pack(fill='x', padx=20, pady=(0, 18))
        self.check_button = ctk.CTkButton(
            self.button_frame, text='重新检查', width=110, command=self.check)
        self.check_button.pack(side='left')
        ctk.CTkButton(self.button_frame, text='查看发布页', width=150,
                      command=self.open_release).pack(side='left', padx=10)
        ctk.CTkButton(self.button_frame, text='关闭', width=80,
                      command=self.destroy).pack(side='right')
        self.check()

    def check(self):
        if self._busy or self._closed:
            return
        self._busy = True
        self.check_button.configure(state='disabled')
        self.status_label.configure(text='正在检查更新…')
        self.notes.pack_forget()
        self.upgrade_hint.pack_forget()
        self.geometry('640x200')
        self.notes.configure(state='normal')
        self.notes.delete('1.0', 'end')
        self.notes.configure(state='disabled')
        self._release_url = RELEASES_URL

        def fetch():
            try:
                result = check_for_update(self._proxies)
            except requests.RequestException as error:
                result = update_failure_message(error)
            except ValueError:
                result = '检查失败：GitHub 返回的发布信息无效，请稍后重试。'
            self._results.put(result)

        threading.Thread(target=fetch, daemon=True).start()
        self._poll_id = self.after(50, self._poll)

    def _poll(self):
        self._poll_id = None
        if self._closed:
            return
        try:
            result = self._results.get_nowait()
        except Empty:
            self._poll_id = self.after(50, self._poll)
            return
        self._busy = False
        self.check_button.configure(state='normal')
        if isinstance(result, str):
            self.status_label.configure(text=result)
            return
        if result.newer:
            text = f'发现新版本 {result.version}，可查看说明后前往官方下载。'
        elif result.version == f'v{APP_VERSION}':
            text = f'当前已是最新正式版本 {result.version}。'
        else:
            text = f'最新公开正式版为 {result.version}；当前程序版本更高，无需降级。'
        self.status_label.configure(text=text)
        self._release_url = result.url
        self.geometry('640x500')
        self.notes.pack(fill='both', expand=True, padx=20, pady=(0, 10),
                        before=self.button_frame)
        if result.newer:
            self.upgrade_hint.pack(fill='x', padx=20, pady=(0, 10),
                                   before=self.button_frame)
        self.notes.configure(state='normal')
        self.notes.insert('1.0', result.notes)
        self.notes.configure(state='disabled')

    def open_release(self):
        if not webbrowser.open(self._release_url):
            self.status_label.configure(
                text='无法启动浏览器，请访问 ' + RELEASES_URL)

    def destroy(self):
        self._closed = True
        if self._poll_id is not None:
            self.after_cancel(self._poll_id)
            self._poll_id = None
        super().destroy()
