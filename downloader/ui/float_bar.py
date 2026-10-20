# -*- coding: utf-8 -*-
"""
桌面悬浮条（可拖动）

对标 IDM 的桌面悬浮窗：不抢焦点、不进任务栏、始终置顶，实时显示
总速度、当前任务进度与最近速度曲线。整条用单个 Canvas 自绘，避免
一堆 CTk 控件在 overrideredirect 窗口里各自处理主题。

老王说：这么个小条子，得做得让人愿意一直放着，别整成一块补丁！
"""
import logging
import math
import time
import tkinter as tk
from collections import deque
from tkinter import font as tkfont

import customtkinter as ctk

from downloader.utils.file_utils import format_remaining, format_speed

logger = logging.getLogger(__name__)


# ==================== 尺寸与布局（逻辑像素，绘制时再乘 DPI 缩放） ====================

BAR_WIDTH = 348
BAR_HEIGHT = 160
CARD_RADIUS = 16
PAD = 18

# 拖动松手后的贴边距离
SNAP_DISTANCE = 16
# 判定为“拖动”而非“点击”的最小位移
DRAG_THRESHOLD = 3

# 透明色键：窗口里露出的这个颜色在 Windows 上完全透明，用来做圆角卡片
TRANSPARENT_KEY = "#010203"

# 采样窗口：每 1 秒一个点，60 点刚好一分钟
SAMPLE_LIMIT = 60

THEMES = {
    'dark': {
        'card': '#17191d',
        'border': '#2b3038',
        'border_hover': '#3d4551',
        'highlight': '#31363f',
        'track': '#262a31',
        'text': '#eef1f5',
        'muted': '#8d95a1',
        'faint': '#4c525c',
        'accent': '#2fbf7f',
        'accent_dim': '#1c7d55',
        'idle_dot': '#5b6270',
    },
    'light': {
        'card': '#ffffff',
        'border': '#d8dde4',
        'border_hover': '#aab4c0',
        'highlight': '#ffffff',
        'track': '#eceff3',
        'text': '#1c1f24',
        'muted': '#6b7280',
        'faint': '#a9b0ba',
        'accent': '#12855c',
        'accent_dim': '#8fcfb4',
        'idle_dot': '#b6bcc5',
    },
}


def virtual_screen_bounds(fallback):
    """
    取整个虚拟桌面的物理像素范围（多显示器时包含负坐标的副屏）。

    Args:
        fallback: (width, height) 取不到系统信息时的主屏尺寸
    Returns:
        (left, top, right, bottom)
    """
    try:
        import ctypes
        user32 = ctypes.windll.user32
        left = user32.GetSystemMetrics(76)    # SM_XVIRTUALSCREEN
        top = user32.GetSystemMetrics(77)     # SM_YVIRTUALSCREEN
        width = user32.GetSystemMetrics(78)   # SM_CXVIRTUALSCREEN
        height = user32.GetSystemMetrics(79)  # SM_CYVIRTUALSCREEN
        if width > 0 and height > 0:
            return left, top, left + width, top + height
    except Exception:
        pass
    return 0, 0, fallback[0], fallback[1]


def clamp_position(x, y, width, height, bounds):
    """
    把悬浮条夹进可视桌面，保证至少露出完整一条。

    Args:
        x, y: 期望左上角物理像素坐标
        width, height: 悬浮条物理像素尺寸
        bounds: (left, top, right, bottom)
    Returns:
        (x, y)
    """
    left, top, right, bottom = bounds
    if right - left <= width:
        x = left
    else:
        x = max(left, min(x, right - width))
    if bottom - top <= height:
        y = top
    else:
        y = max(top, min(y, bottom - height))
    return int(x), int(y)


def snap_to_edges(x, y, width, height, bounds, distance=SNAP_DISTANCE):
    """靠近屏幕边缘时吸附，避免留下 1~2 像素的缝"""
    left, top, right, bottom = bounds
    if abs(x - left) <= distance:
        x = left
    elif abs(right - (x + width)) <= distance:
        x = right - width
    if abs(y - top) <= distance:
        y = top
    elif abs(bottom - (y + height)) <= distance:
        y = bottom - height
    return clamp_position(x, y, width, height, bounds)


class FloatBar:
    """
    桌面悬浮条

    只读任务数据绘制，不参与下载调度；所有回调都由主窗口注入，
    暂停/继续/设置/退出走的仍是主窗口原有链路。
    """

    def __init__(self, master, task_manager, config,
                 on_show_window=None, on_pause_all=None, on_resume_all=None,
                 on_settings=None, on_exit=None, on_hidden=None):
        self.master = master
        self.task_manager = task_manager
        self.config = config
        self._on_show_window = on_show_window
        self._on_pause_all = on_pause_all
        self._on_resume_all = on_resume_all
        self._on_settings = on_settings
        self._on_exit = on_exit
        self._on_hidden = on_hidden

        self._visible = False
        self._tick_job = None
        self._destroyed = False
        self._hovered = False
        self._press = None
        self._dragging = False
        self._samples = deque()  # (单调秒, 总速度)
        self._state = {
            'speed': 0.0, 'downloaded': 0, 'total': 0,
            'filename': '', 'downloading': 0, 'pending': 0,
            'status': 'idle',
        }

        # 独立 Toplevel：CTk 会按 DPI 缩放自己的控件，这里要用物理像素自己算
        self.window = tk.Toplevel(master)
        self.window.withdraw()
        self.window.overrideredirect(True)
        self.window.attributes('-topmost', True)
        self.window.configure(bg=TRANSPARENT_KEY)
        try:
            # Windows 专属：色键透明，圆角外侧才是真透明而不是一块方底板
            self.window.attributes('-transparentcolor', TRANSPARENT_KEY)
        except tk.TclError:
            pass

        self._scale = self._detect_scale()
        self._width = max(1, round(BAR_WIDTH * self._scale))
        self._height = max(1, round(BAR_HEIGHT * self._scale))

        self.canvas = tk.Canvas(
            self.window, bg=TRANSPARENT_KEY, highlightthickness=0, bd=0,
            width=self._width, height=self._height)
        self.canvas.pack(fill='both', expand=True)

        self._build_fonts()
        self._theme = self._resolve_theme()
        self._build_menu()
        self._bind_events()

        self._restore_position()
        self._draw()
        self.show()

    # ==================== 基础工具 ====================

    def _detect_scale(self):
        """DPI 缩放：100% 返回 1.0，150% 返回 1.5"""
        try:
            scaling = ctk.ScalingTracker.get_window_scaling(self.master)
            if scaling and scaling > 0:
                return float(scaling)
        except Exception:
            pass
        try:
            return max(1.0, self.master.winfo_fpixels('1i') / 96.0)
        except Exception:
            return 1.0

    def _build_fonts(self):
        """字体尺寸按物理像素取负值，避免 Tk 自己再做一次磅→像素换算"""
        size = self._scale
        family = 'Microsoft YaHei UI'
        # 字号整体上调一档：悬浮条是小窗，字小了看着就是一块毛玻璃
        self._font_label = tkfont.Font(family=family, size=-max(10, round(12 * size)))
        self._font_label_bold = tkfont.Font(family=family, size=-max(10, round(12 * size)), weight='bold')
        self._font_speed = tkfont.Font(family=family, size=-max(16, round(26 * size)), weight='bold')
        self._font_speed_small = tkfont.Font(family=family, size=-max(13, round(20 * size)), weight='bold')
        self._font_percent = tkfont.Font(family=family, size=-max(12, round(18 * size)), weight='bold')
        self._font_meta = tkfont.Font(family=family, size=-max(9, round(11 * size)))
        self._font_menu = tkfont.Font(family=family, size=-max(9, round(11 * size)))

    def _resolve_theme(self):
        """深色/浅色跟随主窗口当前外观模式"""
        try:
            mode = ctk.get_appearance_mode()
        except Exception:
            mode = 'Dark'
        return THEMES['light' if str(mode).lower() == 'light' else 'dark']

    def _px(self, value):
        return round(value * self._scale)

    def _measure_width(self):
        return self.window.winfo_width() if self.window.winfo_width() > 1 else self._width

    # ==================== 窗口位置与拖动 ====================

    def _bounds(self):
        return virtual_screen_bounds((self.master.winfo_screenwidth(), self.master.winfo_screenheight()))

    def _restore_position(self):
        """恢复上次位置；首次出现放在右下角，且始终保证在屏幕内"""
        bounds = self._bounds()
        saved = self.config.get('ui_float_bar_pos') or []
        if (isinstance(saved, (list, tuple)) and len(saved) == 2
                and all(isinstance(item, (int, float)) for item in saved)):
            x, y = int(saved[0]), int(saved[1])
        else:
            x = bounds[2] - self._width - self._px(24)
            y = bounds[3] - self._height - self._px(72)
        x, y = clamp_position(x, y, self._width, self._height, bounds)
        self._pos = (x, y)
        self.window.geometry(f'{self._width}x{self._height}+{x}+{y}')

    def _persist_position(self):
        """位置落盘：拖动结束时调用，键位存物理像素"""
        try:
            x, y = self._pos
            self.config.set('ui_float_bar_pos', [int(x), int(y)])
            self.config.save()
        except Exception as error:
            logger.warning('保存悬浮条位置失败: %s', error)

    def _on_press(self, event):
        self._press = (event.x_root, event.y_root, self._pos[0], self._pos[1])
        self._dragging = False

    def _on_motion(self, event):
        if not self._press:
            return
        press_x, press_y, win_x, win_y = self._press
        delta_x, delta_y = event.x_root - press_x, event.y_root - press_y
        if not self._dragging and abs(delta_x) + abs(delta_y) >= DRAG_THRESHOLD:
            self._dragging = True
            self.canvas.configure(cursor='fleur')
        if self._dragging:
            # 全程用相对位移，避免 DPI 缩放下 x_root 与 geometry 坐标系不一致；
            # 位置自己记，不从 winfo_x 回读（WM 应用 geometry 是异步的）
            self._pos = (win_x + delta_x, win_y + delta_y)
            self.window.geometry(f'+{self._pos[0]}+{self._pos[1]}')

    def _on_release(self, _event):
        if self._dragging:
            bounds = self._bounds()
            x, y = snap_to_edges(self._pos[0], self._pos[1],
                                 self._width, self._height, bounds)
            self._pos = (x, y)
            self.window.geometry(f'+{x}+{y}')
            self.canvas.configure(cursor='')
            self._persist_position()
        self._press = None
        self._dragging = False

    def _on_double_click(self, _event):
        if self._on_show_window:
            self._on_show_window()

    def _bind_events(self):
        self.canvas.bind('<ButtonPress-1>', self._on_press)
        self.canvas.bind('<B1-Motion>', self._on_motion)
        self.canvas.bind('<ButtonRelease-1>', self._on_release)
        self.canvas.bind('<Double-Button-1>', self._on_double_click)
        self.canvas.bind('<Button-3>', self._show_menu)
        self.canvas.bind('<Enter>', self._on_enter)
        self.canvas.bind('<Leave>', self._on_leave)

    def _on_enter(self, _event):
        self._hovered = True
        self._draw()

    def _on_leave(self, _event):
        self._hovered = False
        self._draw()

    # ==================== 右键菜单 ====================

    def _build_menu(self):
        self._menu = tk.Menu(self.window, tearoff=0, font=self._font_menu)
        self._menu.add_command(label='显示主窗口', command=self._emit(self._on_show_window))
        self._menu.add_separator()
        self._menu.add_command(label='暂停全部', command=self._emit(self._on_pause_all))
        self._menu.add_command(label='继续全部', command=self._emit(self._on_resume_all))
        self._menu.add_separator()
        self._menu.add_command(label='设置…', command=self._emit(self._on_settings))
        self._menu.add_command(label='隐藏悬浮条', command=self.hide_by_user)
        self._menu.add_separator()
        self._menu.add_command(label='退出程序', command=self._emit(self._on_exit))

    @staticmethod
    def _emit(callback):
        """菜单回调空值兜底，避免菜单项点了没反应还抛异常"""
        def run():
            if callback:
                callback()
        return run

    def _show_menu(self, event):
        state = self._state
        has_active = state['downloading'] > 0 or state['pending'] > 0
        self._menu.entryconfigure('暂停全部', state='normal' if has_active else 'disabled')
        self._menu.entryconfigure('继续全部', state='normal' if state['downloading'] or state['pending'] else 'normal')
        try:
            self._menu.tk_popup(event.x_root, event.y_root)
        finally:
            self._menu.grab_release()

    # ==================== 显示/隐藏 ====================

    def show(self):
        """显示并开始刷新"""
        if self._destroyed:
            return
        self._visible = True
        self._restore_position()
        self.window.deiconify()
        self.window.attributes('-topmost', True)
        self._draw()
        self._schedule_tick()

    def hide(self):
        """仅隐藏（保留窗口对象供再次显示）"""
        self._visible = False
        self._cancel_tick()
        try:
            self.window.withdraw()
        except tk.TclError:
            pass

    def hide_by_user(self):
        """用户主动隐藏：记住开关状态，下次启动不再自动弹出"""
        try:
            self.config.ui_float_bar_enabled = False
            self.config.save()
        except Exception as error:
            logger.warning('保存悬浮条开关失败: %s', error)
        self.hide()
        if self._on_hidden:
            self._on_hidden()

    def refresh_theme(self):
        """主窗口切换主题后重绘"""
        if self._destroyed:
            return
        self._theme = self._resolve_theme()
        self._menu.configure(font=self._font_menu)
        self._draw()

    def destroy(self):
        """销毁窗口并停止定时器"""
        if self._destroyed:
            return
        self._destroyed = True
        self._visible = False
        self._cancel_tick()
        try:
            self.window.destroy()
        except tk.TclError:
            pass

    # ==================== 数据刷新 ====================

    def _schedule_tick(self):
        if self._destroyed:
            return
        self._cancel_tick()
        try:
            self._tick_job = self.window.after(1000, self._tick)
        except tk.TclError:
            # 主窗口直接 destroy 时子窗口已失效，没必要再报错
            self._tick_job = None

    def _cancel_tick(self):
        if self._tick_job is not None:
            try:
                self.window.after_cancel(self._tick_job)
            except Exception:
                pass
            self._tick_job = None

    def _tick(self):
        """每秒读一次任务状态：与主窗口状态栏同一节奏，不做高频轮询"""
        self._tick_job = None
        if self._destroyed or not self._visible:
            return
        try:
            self._refresh_state()
            self._draw()
        except tk.TclError:
            # 窗口已销毁，安静退出
            self._destroyed = True
            return
        except Exception as error:
            # 悬浮条出错绝不能影响下载主流程
            logger.warning('悬浮条刷新失败: %s', error)
        finally:
            self._schedule_tick()

    def _refresh_state(self):
        downloading = self.task_manager.get_downloading_tasks()
        pending = self.task_manager.get_pending_tasks()

        total_speed = sum(max(0.0, float(task.get('speed') or 0)) for task in downloading)
        current = downloading[0] if downloading else None

        if current:
            status = 'downloading'
            filename = current.get('filename') or ''
            downloaded = int(current.get('downloaded_size') or 0)
            total = int(current.get('total_size') or 0)
        elif pending:
            status = 'pending'
            filename = pending[0].get('filename') or ''
            downloaded = int(pending[0].get('downloaded_size') or 0)
            total = int(pending[0].get('total_size') or 0)
        else:
            status = 'idle'
            filename, downloaded, total = '', 0, 0

        self._state = {
            'speed': total_speed,
            'downloaded': downloaded,
            'total': total,
            'filename': filename,
            'downloading': len(downloading),
            'pending': len(pending),
            'status': status,
        }

        # 采样：同一秒只保留最新值，和主窗口速度曲线口径一致
        second = int(time.monotonic())
        sample = (second, total_speed)
        if self._samples and self._samples[-1][0] == second:
            self._samples[-1] = sample
        else:
            self._samples.append(sample)
        while self._samples and self._samples[0][0] < second - SAMPLE_LIMIT:
            self._samples.popleft()

    # ==================== 绘制 ====================

    def _round_rect(self, x1, y1, x2, y2, radius, **kwargs):
        """用密集采样点画精确圆角矩形（Canvas 的 smooth 会把圆角拉成椭圆）"""
        steps = 7
        points = []
        corners = ((x2 - radius, y1 + radius, -90),
                   (x2 - radius, y2 - radius, 0),
                   (x1 + radius, y2 - radius, 90),
                   (x1 + radius, y1 + radius, 180))
        for center_x, center_y, start in corners:
            for index in range(steps + 1):
                angle = math.radians(start + index * 90 / steps)
                points.extend((center_x + radius * math.cos(angle),
                               center_y + radius * math.sin(angle)))
        return self.canvas.create_polygon(points, **kwargs)

    def _ellipsize(self, text, font, max_width):
        """按实际像素宽度截断，中文/数字混排都不会溢出卡片"""
        if max_width <= 0 or not text:
            return ''
        if font.measure(text) <= max_width:
            return text
        ellipse = '…'
        low, high = 0, len(text)
        while low < high:
            middle = (low + high + 1) // 2
            if font.measure(text[:middle] + ellipse) <= max_width:
                low = middle
            else:
                high = middle - 1
        return text[:low] + ellipse if low else ellipse

    def _draw(self):
        if self._destroyed:
            return
        canvas = self.canvas
        canvas.delete('all')

        scale = self._scale
        theme = self._theme
        width = BAR_WIDTH
        height = BAR_HEIGHT
        state = self._state

        def px(value):
            return round(value * scale)

        line = max(1, px(1))
        border_color = theme['border_hover'] if self._hovered else theme['border']

        # 卡片 + 顶部内高光，模拟一点点玻璃质感
        self._round_rect(px(1), px(1), px(width - 1), px(height - 1), px(CARD_RADIUS),
                         fill=theme['card'], outline=border_color, width=line)
        self._round_rect(px(3), px(3), px(width - 3), px(height - 3), px(CARD_RADIUS - 2),
                         fill='', outline=theme['highlight'], width=1)

        # 顶部行：状态点 + 标题，右侧任务数
        dot_color = {'downloading': theme['accent'],
                     'pending': theme['muted']}.get(state['status'], theme['idle_dot'])
        center_y = px(26)
        dot_r = px(3.5)
        canvas.create_oval(px(PAD), center_y - dot_r, px(PAD) + dot_r * 2, center_y + dot_r,
                           fill=dot_color, outline='')
        canvas.create_text(px(PAD + 13), center_y, text='daw 下载器', anchor='w',
                           fill=theme['muted'], font=self._font_meta)

        if state['downloading']:
            counter = f"下载中 {state['downloading']}"
        elif state['pending']:
            counter = f"等待 {state['pending']}"
        else:
            counter = '空闲'
        canvas.create_text(px(width - PAD), center_y, text=counter, anchor='e',
                           fill=theme['muted'], font=self._font_meta)

        # 速度大数字：超宽时降一档字号，绝不截断数字
        if state['status'] == 'idle':
            speed_text = '0 B/s'
        else:
            speed_text = format_speed(state['speed'])
        speed_font = self._font_speed
        percent_text = '--' if state['status'] == 'idle' else f'{self._progress() * 100:.0f}%'
        percent_width = self._font_percent.measure(percent_text)
        available = px(width - PAD * 2) - percent_width - px(12)
        if speed_font.measure(speed_text) > available:
            speed_font = self._font_speed_small
        speed_color = theme['text'] if state['status'] != 'idle' else theme['faint']
        canvas.create_text(px(PAD), px(68), text=speed_text, anchor='w',
                           fill=speed_color, font=speed_font)
        canvas.create_text(px(width - PAD), px(68), text=percent_text, anchor='e',
                           fill=theme['accent'] if state['status'] != 'idle' else theme['faint'],
                           font=self._font_percent)

        # 进度条：底色轨道 + 主色（分段画，避免引入图像依赖）
        bar_left, bar_right = px(PAD), px(width - PAD)
        bar_top, bar_bottom = px(92), px(99)
        radius = px(3.5)
        self._round_rect(bar_left, bar_top, bar_right, bar_bottom, radius,
                         fill=theme['track'], outline='')

        progress = self._progress()
        if state['status'] != 'idle' and progress > 0:
            filled_right = bar_left + max(px(7), (bar_right - bar_left) * progress)
            filled_right = min(bar_right, filled_right)
            self._round_rect(bar_left, bar_top, filled_right, bar_bottom, radius,
                             fill=theme['accent'], outline='')
            # 顶部高光让条子看起来是凸起的
            self._round_rect(bar_left, bar_top, filled_right, bar_top + px(2),
                             px(1), fill=theme['accent_dim'], outline='')

        # 文件名 + 剩余时间
        if state['status'] == 'downloading' and state['total'] > 0 and state['speed'] > 0:
            remaining = max(0, state['total'] - state['downloaded'])
            eta_text = '剩余 ' + format_remaining(remaining, state['speed'])
        else:
            eta_text = ''
        eta_width = self._font_meta.measure(eta_text)
        name_max = (bar_right - bar_left) - eta_width - px(14)
        name_text = self._ellipsize(state['filename'], self._font_label_bold, name_max)
        if state['status'] == 'idle':
            name_text = '暂无下载任务'
        canvas.create_text(px(PAD), px(116), text=name_text, anchor='w',
                           fill=theme['text'] if state['status'] != 'idle' else theme['faint'],
                           font=self._font_label_bold)
        if eta_text:
            canvas.create_text(px(width - PAD), px(117), text=eta_text, anchor='e',
                               fill=theme['muted'], font=self._font_meta)

        # 最近速度曲线
        self._draw_curve(theme, px(PAD), px(width - PAD), px(128), px(height - 12))

    def _progress(self):
        """当前任务进度（0~1）"""
        state = self._state
        if state['total'] <= 0:
            return 0.0
        return min(1.0, max(0.0, state['downloaded'] / state['total']))

    def _draw_curve(self, theme, left, right, top, bottom):
        """一分钟速度曲线：面积 + 折线 + 末端点"""
        canvas = self.canvas
        plot_width = right - left
        plot_height = bottom - top
        if plot_width <= 2 or plot_height <= 2:
            return

        samples = list(self._samples)
        if len(samples) < 2:
            canvas.create_line(left, bottom, right, bottom,
                               fill=theme['track'], width=max(1, round(self._scale)))
            return

        values = [value for _, value in samples]
        peak = max(values)
        ceiling = max(peak * 1.15, 1024.0)

        first, last = samples[0][0], samples[-1][0]
        span = max(1, last - first)
        points = []
        for stamp, value in samples:
            x = left + plot_width * (stamp - first) / span
            y = bottom - plot_height * min(value / ceiling, 1.0)
            points.extend((x, y))

        # 面积填充：stipple 在 Canvas 上最接近半透明效果
        canvas.create_polygon(points[0], bottom, *points, points[-2], bottom,
                              fill=theme['accent'], outline='', stipple='gray25')
        canvas.create_line(*points, fill=theme['accent'], width=max(1, round(2 * self._scale)),
                           smooth=True)

        # 末端亮点
        end_radius = max(2, round(2.5 * self._scale))
        canvas.create_oval(points[-2] - end_radius, points[-1] - end_radius,
                           points[-2] + end_radius, points[-1] + end_radius,
                           fill=theme['accent'], outline=theme['card'])
