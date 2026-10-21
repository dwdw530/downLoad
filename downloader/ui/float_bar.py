# -*- coding: utf-8 -*-
"""
桌面悬浮条（可拖动）

对标 IDM 的桌面悬浮窗：不抢焦点、不进任务栏、始终置顶，实时显示
总速度、当前任务进度与最近速度曲线。

渲染方式：整条卡片（含文字）用 Pillow 在 4 倍尺寸上绘制后降采样，
再通过 UpdateLayeredWindow 推成窗口像素。这样是真正的逐像素 alpha：
圆角边缘平滑，且没有色键透明那种「毛毛的黑边」。
老王说：小条子得做得让人愿意一直放着，别整成一块补丁！
"""
import ctypes
import logging
import os
import time
import tkinter as tk
from collections import deque
from ctypes import wintypes

import customtkinter as ctk
from PIL import Image, ImageDraw, ImageFont

from downloader.utils.file_utils import format_remaining, format_speed

logger = logging.getLogger(__name__)


# ==================== 尺寸与布局（逻辑像素，绘制时再乘 DPI 缩放） ====================

BAR_WIDTH = 280
BAR_HEIGHT = 118
# 迷你模式：只留速度、百分比和一条底部进度线，放在桌面角落不碍事
MINI_BAR_WIDTH = 158
MINI_BAR_HEIGHT = 34
CARD_RADIUS = 14
MINI_CARD_RADIUS = 10
PAD = 14
MINI_PAD = 9

# 拖动松手后的贴边距离
SNAP_DISTANCE = 16
# 判定为“拖动”而非“点击”的最小位移
DRAG_THRESHOLD = 3

# 采样窗口：每 1 秒一个点，60 点刚好一分钟
SAMPLE_LIMIT = 60

# 超采样倍数：4 倍降到 1 倍，圆角/文字边缘才够干净
SUPERSAMPLE = 4

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


# ==================== 颜色与字体 ====================

def _rgb(color, alpha=255):
    """#rrggbb -> PIL 用的 (r, g, b, a)"""
    text = str(color).lstrip('#')
    return (int(text[0:2], 16), int(text[2:4], 16), int(text[4:6], 16), int(alpha))


# 微软雅黑：中文显示最稳；拿不到就退回 PIL 内置字体，不让悬浮条整体报错
FONT_CANDIDATES = {
    False: ('msyh.ttc', 'msyh.ttf', 'segoeui.ttf'),
    True: ('msyhbd.ttc', 'msyhbd.ttf', 'segoeuib.ttf'),
}
_font_cache = {}


def _load_font(size_px, bold=False):
    """按像素加载字体（带缓存：每秒重绘不能反复读字体文件）"""
    size_px = max(6, int(size_px))
    key = (size_px, bool(bold))
    cached = _font_cache.get(key)
    if cached is not None:
        return cached
    font = None
    for name in FONT_CANDIDATES[bool(bold)]:
        path = os.path.join(os.environ.get('WINDIR', r'C:\Windows'), 'Fonts', name)
        try:
            font = ImageFont.truetype(path, size_px)
            break
        except OSError:
            continue
    if font is None:
        try:
            font = ImageFont.load_default(size=size_px)
        except TypeError:
            font = ImageFont.load_default()
    _font_cache[key] = font
    return font


def _text_width(font, text):
    return font.getlength(text or '') if hasattr(font, 'getlength') else font.getsize(text)[0]


def _ellipsize(font, text, max_width):
    """按实际像素宽度截断，中文/数字混排都不会溢出卡片"""
    if max_width <= 0 or not text:
        return ''
    if _text_width(font, text) <= max_width:
        return text
    ellipse = '…'
    low, high = 0, len(text)
    while low < high:
        middle = (low + high + 1) // 2
        if _text_width(font, text[:middle] + ellipse) <= max_width:
            low = middle
        else:
            high = middle - 1
    return text[:low] + ellipse if low else ellipse


# ==================== 绘制（纯函数，便于不建窗口就测） ====================

def bar_progress(state):
    """当前任务进度（0~1）"""
    total = state.get('total') or 0
    if total <= 0:
        return 0.0
    return min(1.0, max(0.0, (state.get('downloaded') or 0) / total))


def _curve_points(samples, left, right, top, bottom):
    """把采样换成折线坐标；数据不足或全 0 时返回 None（不画绿线）"""
    if len(samples) < 2:
        return None
    values = [value for _, value in samples]
    if max(values or [0]) <= 0:
        return None
    plot_width, plot_height = right - left, bottom - top
    if plot_width <= 2 or plot_height <= 2:
        return None
    ceiling = max(max(values) * 1.15, 1024.0)
    first, last = samples[0][0], samples[-1][0]
    span = max(1, last - first)
    points = []
    for stamp, value in samples:
        points.append(left + plot_width * (stamp - first) / span)
        points.append(bottom - plot_height * min(value / ceiling, 1.0))
    return points


def render_bar(*, mode, theme, state, samples, hovered, scale=1.0):
    """把整条悬浮卡片画成 RGBA 图（4 倍超采样后降采样）

    Args:
        mode: 'mini' 迷你 / 其它视为完整
        theme: THEMES 里的一个主题字典
        state: {speed, downloaded, total, filename, downloading, pending, status}
        samples: [(秒, 速度)] 速度曲线采样
        hovered: 鼠标是否悬停（描边变亮）
        scale: DPI 缩放
    Returns:
        PIL.Image（RGBA，物理像素尺寸）
    """
    mini = mode == 'mini'
    base_w = MINI_BAR_WIDTH if mini else BAR_WIDTH
    base_h = MINI_BAR_HEIGHT if mini else BAR_HEIGHT
    width = max(1, round(base_w * scale))
    height = max(1, round(base_h * scale))
    ss = SUPERSAMPLE

    def u(value):
        """逻辑像素 -> 超采样画布坐标"""
        return round(value * scale * ss)

    canvas_image = Image.new('RGBA', (width * ss, height * ss), (0, 0, 0, 0))
    draw = ImageDraw.Draw(canvas_image)

    def font(size, bold=False):
        return _load_font(max(6, round(size * scale)) * ss, bold)

    def text(position, content, fill, font_obj, anchor='lm'):
        draw.text((round(position[0] * scale * ss), round(position[1] * scale * ss)),
                  content, font=font_obj, fill=_rgb(fill), anchor=anchor)

    border_color = theme['border_hover'] if hovered else theme['border']
    status = state.get('status', 'idle')
    progress = bar_progress(state)
    dot_color = {'downloading': theme['accent'],
                 'pending': theme['muted']}.get(status, theme['idle_dot'])

    if mini:
        radius = MINI_CARD_RADIUS
        draw.rounded_rectangle([u(1), u(1), u(base_w - 1), u(base_h - 1)], radius=u(radius),
                               fill=_rgb(theme['card']), outline=_rgb(border_color),
                               width=max(1, u(1)))
        center_y = base_h / 2 - 2
        dot_r = 2.6
        draw.ellipse([u(MINI_PAD), u(center_y - dot_r), u(MINI_PAD + dot_r * 2), u(center_y + dot_r)],
                     fill=_rgb(dot_color))

        bar_left, bar_right = MINI_CARD_RADIUS, base_w - MINI_CARD_RADIUS
        bar_top, bar_bottom = base_h - 4, base_h - 2
        draw.rounded_rectangle([u(bar_left), u(bar_top), u(bar_right), u(bar_bottom)],
                               radius=u(1), fill=_rgb(theme['track']))
        if status != 'idle' and progress > 0:
            filled = min(bar_right, bar_left + max(4, (bar_right - bar_left) * progress))
            draw.rounded_rectangle([u(bar_left), u(bar_top), u(filled), u(bar_bottom)],
                                   radius=u(1), fill=_rgb(theme['accent']))

        if status == 'idle':
            speed_text, percent_text = '0 B/s', '--'
        else:
            speed_text = format_speed(state.get('speed') or 0)
            percent_text = f'{progress * 100:.0f}%'

        percent_font = font(11)
        text(position=(base_w - MINI_PAD, center_y), content=percent_text,
             fill=theme['accent'] if status != 'idle' else theme['faint'],
             font_obj=percent_font, anchor='rm')

        percent_width = _text_width(percent_font, percent_text) / (scale * ss)
        text_left = MINI_PAD + 8
        available = (base_w - MINI_PAD) - text_left - percent_width - 6
        speed_font = font(14, bold=True)
        if _text_width(speed_font, speed_text) / (scale * ss) > available:
            speed_font = font(12, bold=True)
        text(position=(text_left, center_y), content=speed_text,
             fill=theme['text'] if status != 'idle' else theme['faint'],
             font_obj=speed_font, anchor='lm')

        return canvas_image.resize((width, height), Image.LANCZOS)

    # ---------- 完整模式 ----------
    radius = CARD_RADIUS
    draw.rounded_rectangle([u(1), u(1), u(base_w - 1), u(base_h - 1)], radius=u(radius),
                           fill=_rgb(theme['card']), outline=_rgb(border_color),
                           width=max(1, u(1)))
    # 内圈细高光，给一点点玻璃质感
    draw.rounded_rectangle([u(3), u(3), u(base_w - 3), u(base_h - 3)], radius=u(radius - 2),
                           outline=_rgb(theme['highlight']), width=max(1, u(1)))

    # 顶部：状态点 + 标题 + 任务数
    center_y = 19
    dot_r = 3
    draw.ellipse([u(PAD), u(center_y - dot_r), u(PAD + dot_r * 2), u(center_y + dot_r)],
                 fill=_rgb(dot_color))
    meta_font = font(10)
    text(position=(PAD + 11, center_y), content='daw 下载器', fill=theme['muted'],
         font_obj=meta_font, anchor='lm')
    if state.get('downloading'):
        counter = f"下载中 {state['downloading']}"
    elif state.get('pending'):
        counter = f"等待 {state['pending']}"
    else:
        counter = '空闲'
    text(position=(base_w - PAD, center_y), content=counter, fill=theme['muted'],
         font_obj=meta_font, anchor='rm')

    # 速度大数字：放不下就降一档字号，绝不截断数字
    speed_text = '0 B/s' if status == 'idle' else format_speed(state.get('speed') or 0)
    percent_text = '--' if status == 'idle' else f'{progress * 100:.0f}%'
    percent_font = font(15, bold=True)
    percent_width = _text_width(percent_font, percent_text) / (scale * ss)
    speed_font = font(22, bold=True)
    available = (base_w - PAD * 2) - percent_width - 10
    if _text_width(speed_font, speed_text) / (scale * ss) > available:
        speed_font = font(17, bold=True)
    speed_color = theme['text'] if status != 'idle' else theme['faint']
    text(position=(PAD, 47), content=speed_text, fill=speed_color, font_obj=speed_font, anchor='lm')
    text(position=(base_w - PAD, 47), content=percent_text,
         fill=theme['accent'] if status != 'idle' else theme['faint'],
         font_obj=percent_font, anchor='rm')

    # 进度条：轨道 + 主色 + 顶部高光
    bar_left, bar_right = PAD, base_w - PAD
    bar_top, bar_bottom = 64, 70
    draw.rounded_rectangle([u(bar_left), u(bar_top), u(bar_right), u(bar_bottom)],
                           radius=u(3), fill=_rgb(theme['track']))
    if status != 'idle' and progress > 0:
        filled = min(bar_right, bar_left + max(7, (bar_right - bar_left) * progress))
        draw.rounded_rectangle([u(bar_left), u(bar_top), u(filled), u(bar_bottom)],
                               radius=u(3), fill=_rgb(theme['accent']))
        draw.rounded_rectangle([u(bar_left), u(bar_top), u(filled), u(bar_top + 2)],
                               radius=u(1), fill=_rgb(theme['accent_dim']))

    # 文件名 + 剩余时间
    eta_text = ''
    if status == 'downloading' and (state.get('total') or 0) > 0 and (state.get('speed') or 0) > 0:
        remaining = max(0, state['total'] - state['downloaded'])
        eta_text = '剩余 ' + format_remaining(remaining, state['speed'])
    eta_width = _text_width(meta_font, eta_text) / (scale * ss)
    name_max = (bar_right - bar_left) - eta_width - 12
    label_font = font(11, bold=True)
    name_text = _ellipsize(label_font, state.get('filename') or '',
                           max(0, name_max) * scale * ss)
    if status == 'idle':
        name_text = '暂无下载任务'
    text(position=(PAD, 85), content=name_text,
         fill=theme['text'] if status != 'idle' else theme['faint'],
         font_obj=label_font, anchor='lm')
    if eta_text:
        text(position=(base_w - PAD, 86), content=eta_text, fill=theme['muted'],
             font_obj=meta_font, anchor='rm')

    # 最近速度曲线：没有有效数据时只留一条中性基线，不能画成绿色实线
    curve = _curve_points(samples, u(PAD), u(base_w - PAD), u(95), u(base_h - 9))
    if curve is None:
        thickness = max(1, u(1))
        draw.rounded_rectangle([u(PAD), u(base_h - 9) - thickness, u(base_w - PAD), u(base_h - 9)],
                               radius=thickness / 2, fill=_rgb(theme['track']))
    else:
        left, right = u(PAD), u(base_w - PAD)
        bottom = u(base_h - 9)
        area = [left, bottom] + curve + [right, bottom]
        overlay = Image.new('RGBA', canvas_image.size, (0, 0, 0, 0))
        ImageDraw.Draw(overlay).polygon(
            [(area[i], area[i + 1]) for i in range(0, len(area), 2)],
            fill=_rgb(theme['accent'], 56))
        canvas_image = Image.alpha_composite(canvas_image, overlay)
        draw = ImageDraw.Draw(canvas_image)
        points = [(curve[i], curve[i + 1]) for i in range(0, len(curve), 2)]
        draw.line(points, fill=_rgb(theme['accent']), width=max(1, u(2)), joint='curve')
        end_radius = max(2, u(2.5))
        draw.ellipse([points[-1][0] - end_radius, points[-1][1] - end_radius,
                      points[-1][0] + end_radius, points[-1][1] + end_radius],
                     fill=_rgb(theme['accent']), outline=_rgb(theme['card']))

    return canvas_image.resize((width, height), Image.LANCZOS)


# ==================== Windows 分层窗口（真逐像素 alpha） ====================

GWL_EXSTYLE = -20
WS_EX_LAYERED = 0x00080000
WS_EX_TOOLWINDOW = 0x00000080
ULW_ALPHA = 0x00000002
AC_SRC_OVER = 0x00
AC_SRC_ALPHA = 0x01
BI_RGB = 0


class _BlendFunction(ctypes.Structure):
    _fields_ = [('BlendOp', ctypes.c_ubyte), ('BlendFlags', ctypes.c_ubyte),
                ('SourceConstantAlpha', ctypes.c_ubyte), ('AlphaFormat', ctypes.c_ubyte)]


class _BitmapInfoHeader(ctypes.Structure):
    _fields_ = [('biSize', ctypes.c_uint32), ('biWidth', ctypes.c_int32),
                ('biHeight', ctypes.c_int32), ('biPlanes', ctypes.c_uint16),
                ('biBitCount', ctypes.c_uint16), ('biCompression', ctypes.c_uint32),
                ('biSizeImage', ctypes.c_uint32), ('biXPelsPerMeter', ctypes.c_int32),
                ('biYPelsPerMeter', ctypes.c_int32), ('biClrUsed', ctypes.c_uint32),
                ('biClrImportant', ctypes.c_uint32)]


class _BitmapInfo(ctypes.Structure):
    _fields_ = [('bmiHeader', _BitmapInfoHeader), ('bmiColors', ctypes.c_uint32 * 3)]


class LayeredSurface:
    """把 RGBA 图推成窗口像素。

    Tk 只负责窗口与鼠标事件，像素完全由这里给，因此能做真正的半透明边缘。
    不可用时（非 Windows）自动降级：悬浮条仍可显示，只是边缘不做柔化。
    """

    def __init__(self):
        self.available = False
        self._user32 = None
        self._gdi32 = None
        try:
            self._user32 = ctypes.windll.user32
            self._gdi32 = ctypes.windll.gdi32
            self.available = os.name == 'nt'
        except Exception:  # pragma: no cover - 非 Windows 环境
            self.available = False

    def attach(self, hwnd):
        """给窗口加上分层样式，之后内容由 push() 决定"""
        if not self.available:
            return False
        try:
            exstyle = self._user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
            self._user32.SetWindowLongW(hwnd, GWL_EXSTYLE,
                                        exstyle | WS_EX_LAYERED | WS_EX_TOOLWINDOW)
            return True
        except Exception as error:  # pragma: no cover
            logger.warning('开启分层窗口失败: %s', error)
            self.available = False
            return False

    def push(self, hwnd, image):
        """用预乘 BGRA 位图更新窗口内容（尺寸跟着图走）"""
        if not self.available:
            return False
        try:
            if image.mode != 'RGBA':
                image = image.convert('RGBA')
            width, height = image.size
            premultiplied = image.convert('RGBa').convert('RGBA')
            raw = premultiplied.tobytes('raw', 'BGRA')

            screen = self._user32.GetDC(None)
            mem = self._gdi32.CreateCompatibleDC(screen)
            try:
                info = _BitmapInfo()
                info.bmiHeader.biSize = ctypes.sizeof(_BitmapInfoHeader)
                info.bmiHeader.biWidth = width
                info.bmiHeader.biHeight = -height
                info.bmiHeader.biPlanes = 1
                info.bmiHeader.biBitCount = 32
                info.bmiHeader.biCompression = BI_RGB
                bits = ctypes.c_void_p()
                bitmap = self._gdi32.CreateDIBSection(mem, ctypes.byref(info), 0,
                                                      ctypes.byref(bits), None, 0)
                if not bitmap:
                    return False
                try:
                    ctypes.memmove(bits, raw, len(raw))
                    self._gdi32.SelectObject(mem, bitmap)
                    blend = _BlendFunction(AC_SRC_OVER, 0, 255, AC_SRC_ALPHA)
                    size = wintypes.SIZE(width, height)
                    source = wintypes.POINT(0, 0)
                    return bool(self._user32.UpdateLayeredWindow(
                        hwnd, screen, None, ctypes.byref(size), mem,
                        ctypes.byref(source), 0, ctypes.byref(blend), ULW_ALPHA))
                finally:
                    self._gdi32.DeleteObject(bitmap)
            finally:
                self._gdi32.DeleteDC(mem)
                self._user32.ReleaseDC(None, screen)
        except Exception as error:  # pragma: no cover
            logger.warning('更新悬浮条像素失败: %s', error)
            return False


# ==================== 位置计算 ====================

def virtual_screen_bounds(fallback):
    """
    取整个虚拟桌面的物理像素范围（多显示器时包含负坐标的副屏）。

    Args:
        fallback: (width, height) 取不到系统信息时的主屏尺寸
    Returns:
        (left, top, right, bottom)
    """
    try:
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
    """松手后离边缘够近就吸附上去"""
    left, top, right, bottom = bounds
    if abs(x - left) <= distance:
        x = left
    elif abs((right - width) - x) <= distance:
        x = right - width
    if abs(y - top) <= distance:
        y = top
    elif abs((bottom - height) - y) <= distance:
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
        self._image = None       # 当前帧（RGBA），调试与测试可读
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
        # 窗口自己的底色永远看不到：内容由 LayeredSurface 推上去
        self.window.configure(bg='#000000')

        self._surface = LayeredSurface()
        self._scale = self._detect_scale()
        self._mode = 'mini' if config.get('ui_float_bar_mode') == 'mini' else 'normal'
        self._apply_mode_metrics()

        self._theme = self._resolve_theme()
        self._menu_font = ('Microsoft YaHei UI', -max(10, round(12 * self._scale)))
        self._build_menu()
        self._bind_events()

        self._restore_position()
        self._attach_surface()
        self._draw()
        self.show()

    # ==================== 基础工具 ====================

    def _attach_surface(self):
        """拿到真正的顶层窗口句柄并开启分层样式"""
        if not self._surface.available:
            return
        try:
            self.window.update_idletasks()
            hwnd = ctypes.windll.user32.GetAncestor(self.window.winfo_id(), 2)
            self._hwnd = hwnd or self.window.winfo_id()
            self._surface.attach(self._hwnd)
        except Exception as error:  # pragma: no cover
            logger.warning('悬浮条句柄获取失败: %s', error)
            self._hwnd = None

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

    def _apply_mode_metrics(self):
        """按当前样式重新算窗口逻辑尺寸与物理尺寸（DPI 缩放一起乘）"""
        if self._mode == 'mini':
            self._base_width, self._base_height = MINI_BAR_WIDTH, MINI_BAR_HEIGHT
        else:
            self._base_width, self._base_height = BAR_WIDTH, BAR_HEIGHT
        self._width = max(1, round(self._base_width * self._scale))
        self._height = max(1, round(self._base_height * self._scale))

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
            self.window.configure(cursor='fleur')
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
            self.window.configure(cursor='')
            self._persist_position()
            # 位置变了以后重推一次，确保像素与窗口尺寸一致
            self._draw()
        self._press = None
        self._dragging = False

    def _on_double_click(self, _event):
        if self._on_show_window:
            self._on_show_window()

    def _bind_events(self):
        # 没有子控件，事件直接绑在窗口上
        self.window.bind('<ButtonPress-1>', self._on_press)
        self.window.bind('<B1-Motion>', self._on_motion)
        self.window.bind('<ButtonRelease-1>', self._on_release)
        self.window.bind('<Double-Button-1>', self._on_double_click)
        self.window.bind('<Button-3>', self._show_menu)
        self.window.bind('<Enter>', self._on_enter)
        self.window.bind('<Leave>', self._on_leave)

    def _on_enter(self, _event):
        self._hovered = True
        self._draw()

    def _on_leave(self, _event):
        self._hovered = False
        self._draw()

    # ==================== 右键菜单 ====================

    def _build_menu(self):
        self._mini_var = tk.BooleanVar(master=self.window, value=self._mode == 'mini')
        self._menu = tk.Menu(self.window, tearoff=0, font=self._menu_font)
        self._menu.add_command(label='显示主窗口', command=self._emit(self._on_show_window))
        self._menu.add_separator()
        self._menu.add_checkbutton(label='迷你模式', variable=self._mini_var,
                                   command=self._on_toggle_mini)
        self._menu.add_separator()
        self._menu.add_command(label='暂停全部', command=self._emit(self._on_pause_all))
        self._menu.add_command(label='继续全部', command=self._emit(self._on_resume_all))
        self._menu.add_separator()
        self._menu.add_command(label='设置…', command=self._emit(self._on_settings))
        self._menu.add_command(label='隐藏悬浮条', command=self.hide_by_user)
        self._menu.add_separator()
        self._menu.add_command(label='退出程序', command=self._emit(self._on_exit))

    def _on_toggle_mini(self):
        """右键切换完整/迷你：立即生效并记住选择"""
        self.set_mode('mini' if self._mini_var.get() else 'normal')

    def set_mode(self, mode, persist: bool = True):
        """切换悬浮条样式（完整/迷你），位置仍夹在当前桌面内"""
        if self._destroyed:
            return
        mode = 'mini' if mode == 'mini' else 'normal'
        if mode == self._mode:
            return
        self._mode = mode
        self._mini_var.set(mode == 'mini')
        self._apply_mode_metrics()
        # 尺寸变了：保留左上角，但重新夹进可视桌面，避免小屏上越界
        x, y = self._pos
        x, y = clamp_position(x, y, self._width, self._height, self._bounds())
        self._pos = (x, y)
        self.window.geometry(f'{self._width}x{self._height}+{x}+{y}')
        if persist:
            try:
                self.config.ui_float_bar_mode = mode
                self.config.save()
            except Exception as error:
                logger.warning('保存悬浮条样式失败: %s', error)
        self._draw()

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
        if not getattr(self, '_hwnd', None):
            self._attach_surface()
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
        self._menu.configure(font=self._menu_font)
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
            self._tick_job = None

    def _cancel_tick(self):
        job, self._tick_job = self._tick_job, None
        if job is not None:
            try:
                self.window.after_cancel(job)
            except Exception:
                pass

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

    def _progress(self):
        """当前任务进度（0~1）"""
        return bar_progress(self._state)

    def _draw(self):
        """重画当前帧并推到窗口上"""
        if self._destroyed:
            return
        try:
            image = render_bar(mode=self._mode, theme=self._theme, state=self._state,
                               samples=list(self._samples), hovered=self._hovered,
                               scale=self._scale)
        except Exception as error:
            # 画不出来也不能让悬浮条把主程序拖崩
            logger.warning('悬浮条绘制失败: %s', error)
            return
        self._image = image
        hwnd = getattr(self, '_hwnd', None)
        if hwnd:
            self._surface.push(hwnd, image)
