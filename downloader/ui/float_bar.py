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
import sys
import time
import tkinter as tk
from collections import deque
from ctypes import wintypes

import customtkinter as ctk
from PIL import Image, ImageDraw, ImageFont, ImageTk

from downloader.utils.file_utils import format_remaining, parse_byte_count
from downloader.utils.config import normalize_opacity
from downloader.ui.task_presentation import format_task_progress

logger = logging.getLogger(__name__)


# ==================== 尺寸与布局（逻辑像素，绘制时再乘 DPI 缩放） ====================

BAR_WIDTH = 280
BAR_HEIGHT = 118
# 迷你模式：总速度为主，进度环与百分比表示当前任务
MINI_BAR_WIDTH = 158
MINI_BAR_HEIGHT = 34
CARD_RADIUS = 16
MINI_CARD_RADIUS = 16
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
        'card': '#1c2027',
        'border': '#343d4a',
        'border_hover': '#526175',
        'highlight': '#31363f',
        'track': '#343d4a',
        'text': '#f1f5f9',
        'muted': '#a6b1c1',
        'faint': '#4c525c',
        'accent': '#2fbf7f',
        'accent_dim': '#1c7d55',
        'idle_dot': '#5b6270',
        'error': '#ec9690',
    },
    'light': {
        'card': '#ffffff',
        'border': '#dce3ec',
        'border_hover': '#a1b0c4',
        'highlight': '#ffffff',
        'track': '#e6ebf2',
        'text': '#202938',
        'muted': '#56657a',
        'faint': '#a9b0ba',
        'accent': '#12855c',
        'accent_dim': '#8fcfb4',
        'idle_dot': '#b6bcc5',
        'error': '#b54747',
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


def _load_font(size_px, bold=False, *, numeric=False):
    """按像素加载字体（带缓存：每秒重绘不能反复读字体文件）"""
    size_px = max(6, int(size_px))
    key = (size_px, bool(bold), bool(numeric))
    cached = _font_cache.get(key)
    if cached is not None:
        return cached
    font = None
    candidates = FONT_CANDIDATES[bool(bold)]
    if numeric:
        candidates = (candidates[-1],) + candidates[:-1]
    if sys.platform == 'darwin':
        candidates = ('/System/Library/Fonts/PingFang.ttc', '/System/Library/Fonts/Helvetica.ttc')
    for name in candidates:
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
    return format_task_progress(state.get('downloaded'), state.get('total'),
                                state.get('status', 'idle'))[0]


_STATUS_LABELS = {
    'downloading': '下载中', 'pending': '等待中', 'paused': '已暂停',
    'verifying': '校验中', 'failed': '下载失败', 'verify_failed': '校验失败',
    'idle': '就绪',
}


def _speed_parts(speed):
    """数值和单位分开排版；高速时换单位，不靠缩字号挤进小窗。"""
    rate = max(0.0, float(speed or 0))
    unit = 'B/s'
    for next_unit in ('KB/s', 'MB/s', 'GB/s', 'TB/s'):
        if rate < 1024:
            break
        rate /= 1024
        unit = next_unit
    value = f'{rate:.0f}' if unit == 'B/s' else f'{rate:.2f}'
    if rate >= 1024:
        value = f'{rate:.2g}'
    return value, unit


def _remaining_text(state):
    """当前任务的剩余时间只能用当前任务速度，不能用合计速度。"""
    downloaded = parse_byte_count(state.get('downloaded'))
    total = parse_byte_count(state.get('total'))
    if (state.get('status') != 'downloading' or downloaded is None
            or total is None or total <= downloaded):
        return '--'
    return format_remaining(total - downloaded, state.get('current_speed') or 0)


def _hint_text(state, scale=1.0):
    value, unit = _speed_parts(state.get('speed'))
    _, percent = format_task_progress(state.get('downloaded'), state.get('total'),
                                     state.get('status', 'idle'))
    status = _STATUS_LABELS[state.get('status', 'idle')]
    filename = state.get('filename') or '暂无下载任务'
    filename = _ellipsize(_load_font(max(10, round(11 * scale))), filename, 250 * scale)
    return (f'总速度  {value} {unit}\n当前任务  {status} · {percent}\n{filename}\n'
            '双击显示主窗口 · 右键菜单\n拖动移动 · 松手贴边')


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


def render_bar(*, mode, theme, state, samples, hovered, scale=1.0, opacity=100):
    """在原窗口尺寸内绘制卡片；总速度和当前任务进度分层表达。

    state 的 speed 是合计速度，current_speed 是当前任务速度；total 可为 None。
    背景遵循 opacity，文字、状态图形和已填进度保持不透明。
    """
    mini = mode == 'mini'
    base_w = MINI_BAR_WIDTH if mini else BAR_WIDTH
    base_h = MINI_BAR_HEIGHT if mini else BAR_HEIGHT
    width = max(1, round(base_w * scale))
    height = max(1, round(base_h * scale))
    ss = SUPERSAMPLE
    background_alpha = round(normalize_opacity(opacity, 30) * 255 / 100)

    def u(value):
        return round(value * scale * ss)

    canvas_image = Image.new('RGBA', (width * ss, height * ss), (0, 0, 0, 0))
    draw = ImageDraw.Draw(canvas_image)

    def font(size, bold=False, numeric=False):
        return _load_font(max(6, round(size * scale)) * ss, bold, numeric=numeric)

    def text(position, content, fill, font_obj, anchor='ls'):
        draw.text((u(position[0]), u(position[1])), content, font=font_obj,
                  fill=_rgb(fill), anchor=anchor)

    status = state.get('status', 'idle')
    progress, percent_text = format_task_progress(state.get('downloaded'), state.get('total'), status)
    color = (theme['accent'] if status in ('downloading', 'verifying') else
             theme['error'] if status in ('failed', 'verify_failed') else theme['muted'])
    border = theme['border_hover'] if hovered else theme['border']
    radius = MINI_CARD_RADIUS if mini else CARD_RADIUS
    draw.rounded_rectangle([u(1), u(1), u(base_w - 1), u(base_h - 1)], radius=u(radius),
                           fill=_rgb(theme['card'], background_alpha),
                           outline=_rgb(border, background_alpha), width=max(1, u(1)))

    def status_icon(left, center_y, radius):
        center_x = left + radius
        box = [u(left), u(center_y - radius), u(left + radius * 2), u(center_y + radius)]
        draw.ellipse(box, outline=_rgb(theme['track'], background_alpha), width=max(1, u(1.5)))
        if status != 'idle' and progress > 0:
            draw.arc(box, -90, -90 + max(1, round(360 * progress)),
                     fill=_rgb(color), width=max(1, u(1.5)))
        stroke = max(1, u(1.3))
        if status == 'paused':
            for dx in (-2, 2):
                draw.line([(u(center_x + dx), u(center_y - 3)),
                           (u(center_x + dx), u(center_y + 3))], fill=_rgb(color), width=stroke)
        elif status in ('failed', 'verify_failed'):
            for direction in (-1, 1):
                draw.line([(u(center_x - 3), u(center_y - direction * 3)),
                           (u(center_x + 3), u(center_y + direction * 3))],
                          fill=_rgb(color), width=stroke)
        elif status in ('pending', 'verifying'):
            for dx in (-3, 0, 3):
                draw.ellipse([u(center_x + dx - 0.7), u(center_y - 0.7),
                              u(center_x + dx + 0.7), u(center_y + 0.7)], fill=_rgb(color))
        else:
            draw.line([(u(center_x), u(center_y - 4)), (u(center_x), u(center_y + 3)),
                       (u(center_x - 3), u(center_y)), (u(center_x), u(center_y + 3)),
                       (u(center_x + 3), u(center_y))], fill=_rgb(color), width=stroke, joint='curve')

    def speed_text(left, baseline, size, unit_size):
        value, unit = _speed_parts(state.get('speed'))
        number_font = font(size, bold=True, numeric=True)
        number_color = theme['muted'] if status == 'idle' else theme['text']
        text((left, baseline), value, number_color, number_font)
        unit_left = left + _text_width(number_font, value) / (scale * ss) + 4
        text((unit_left, baseline), unit, theme['muted'], font(unit_size, numeric=True))

    if mini:
        status_icon(MINI_PAD, 17, 9)
        speed_text(33, 22, 14, 9)
        if status == 'downloading':
            label = '--' if percent_text == '--' else f'{progress * 100:.0f}%'
        else:
            label = {'idle': '就绪', 'pending': '等待', 'paused': '暂停',
                     'verifying': '校验', 'failed': '失败', 'verify_failed': '失败'}[status]
        text((base_w - MINI_PAD, 21), label, color, font(11), anchor='rs')
        return canvas_image.resize((width, height), Image.LANCZOS)

    # 总速度独占主信息行，当前文件及百分比在下一行，不暗示两者属于同一任务。
    status_icon(PAD, 16, 7.5)
    meta_font = font(10)
    text((PAD + 22, 20), '总速度', theme['muted'], meta_font)
    if state.get('downloading'):
        counter = f"下载中 {state['downloading']}"
        if state.get('pending'):
            counter += f" · 等待 {state['pending']}"
    else:
        counter = _STATUS_LABELS[status]
        if state.get('status_count', 0) > 1:
            counter += f" {state['status_count']}"
    counter = _ellipsize(meta_font, counter, u(base_w - PAD * 2 - 60))
    text((base_w - PAD, 20), counter, color, meta_font, anchor='rs')
    speed_text(PAD, 48, 25, 12)

    label_font = font(11)
    name_text = '暂无下载任务' if status == 'idle' else '当前  ' + (state.get('filename') or '')
    percent_width = _text_width(label_font, percent_text) / (scale * ss)
    name_width = (base_w - PAD * 2) - percent_width - 10
    name_text = _ellipsize(label_font, name_text, name_width * scale * ss)
    text((PAD, 68), name_text, theme['muted'] if status == 'idle' else theme['text'], label_font)
    text((base_w - PAD, 68), percent_text, color, label_font, anchor='rs')

    bar_left, bar_right = PAD, base_w - PAD
    draw.rounded_rectangle([u(bar_left), u(76), u(bar_right), u(80)], radius=u(2),
                           fill=_rgb(theme['track'], background_alpha))
    if status != 'idle' and progress > 0:
        filled = bar_left + (bar_right - bar_left) * progress
        draw.rounded_rectangle([u(bar_left), u(76), u(filled), u(80)], radius=u(2), fill=_rgb(color))

    # 曲线和剩余时间各占一块，给真实曲线留出更多高度。
    curve_right, curve_bottom = base_w - 96, base_h - 9
    curve = _curve_points(samples, u(PAD), u(curve_right), u(87), u(curve_bottom))
    if curve is None:
        draw.line([(u(PAD), u(curve_bottom)), (u(curve_right), u(curve_bottom))],
                  fill=_rgb(theme['track'], background_alpha), width=max(1, u(1)))
    else:
        curve_color = theme['accent'] if status == 'downloading' else theme['muted']
        points = [(curve[i], curve[i + 1]) for i in range(0, len(curve), 2)]
        area = [(u(PAD), u(curve_bottom))] + points + [(u(curve_right), u(curve_bottom))]
        overlay = Image.new('RGBA', canvas_image.size, (0, 0, 0, 0))
        ImageDraw.Draw(overlay).polygon(area, fill=_rgb(curve_color, round(18 * background_alpha / 255)))
        canvas_image = Image.alpha_composite(canvas_image, overlay)
        draw = ImageDraw.Draw(canvas_image)
        draw.line(points, fill=_rgb(curve_color), width=max(1, u(1.2)), joint='curve')
    text((base_w - PAD, 93), '当前剩余', theme['muted'], font(9), anchor='rs')
    text((base_w - PAD, 109), _remaining_text(state), theme['text'], font(13, numeric=True), anchor='rs')
    return canvas_image.resize((width, height), Image.LANCZOS)


# ==================== Windows 分层窗口（真逐像素 alpha） ====================

GWL_EXSTYLE = -20
WS_EX_LAYERED = 0x00080000
WS_EX_TOOLWINDOW = 0x00000080
WS_EX_NOACTIVATE = 0x08000000
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


def premultiplied_bgra(image):
    """Windows AC_SRC_ALPHA 要求预乘；不能再转回普通 RGBA。"""
    return image.convert('RGBA').convert('RGBa').tobytes('raw', 'BGRa')


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
            # 64 位句柄不能用 ctypes 默认的 32 位 int 返回类型。
            self._user32.GetDC.restype = wintypes.HDC
            self._user32.GetDC.argtypes = [wintypes.HWND]
            self._user32.ReleaseDC.argtypes = [wintypes.HWND, wintypes.HDC]
            self._user32.GetAncestor.restype = wintypes.HWND
            self._user32.GetAncestor.argtypes = [wintypes.HWND, wintypes.UINT]
            self._user32.GetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int]
            self._user32.SetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int, wintypes.LONG]
            self._gdi32.CreateCompatibleDC.restype = wintypes.HDC
            self._gdi32.CreateCompatibleDC.argtypes = [wintypes.HDC]
            self._gdi32.CreateDIBSection.restype = wintypes.HBITMAP
            self._gdi32.CreateDIBSection.argtypes = [wintypes.HDC, ctypes.POINTER(_BitmapInfo),
                                                   wintypes.UINT, ctypes.POINTER(ctypes.c_void_p),
                                                   wintypes.HANDLE, wintypes.DWORD]
            self._gdi32.SelectObject.restype = wintypes.HANDLE
            self._gdi32.SelectObject.argtypes = [wintypes.HDC, wintypes.HANDLE]
            self._gdi32.DeleteObject.argtypes = [wintypes.HANDLE]
            self._gdi32.DeleteDC.argtypes = [wintypes.HDC]
            self._user32.UpdateLayeredWindow.argtypes = [wintypes.HWND, wintypes.HDC,
                ctypes.POINTER(wintypes.POINT), ctypes.POINTER(wintypes.SIZE), wintypes.HDC,
                ctypes.POINTER(wintypes.POINT), wintypes.COLORREF,
                ctypes.POINTER(_BlendFunction), wintypes.DWORD]
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
            raw = premultiplied_bgra(image)

            screen = self._user32.GetDC(None)
            if not screen:
                raise OSError('GetDC failed')
            mem = self._gdi32.CreateCompatibleDC(screen)
            try:
                if not mem:
                    raise OSError('CreateCompatibleDC failed')
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
                if not bitmap or not bits.value:
                    raise OSError('CreateDIBSection failed')
                previous = None
                try:
                    ctypes.memmove(bits, raw, len(raw))
                    previous = self._gdi32.SelectObject(mem, bitmap)
                    if not previous or previous == ctypes.c_void_p(-1).value:
                        previous = None
                        raise OSError('SelectObject failed')
                    blend = _BlendFunction(AC_SRC_OVER, 0, 255, AC_SRC_ALPHA)
                    size = wintypes.SIZE(width, height)
                    source = wintypes.POINT(0, 0)
                    success = bool(self._user32.UpdateLayeredWindow(
                        hwnd, screen, None, ctypes.byref(size), mem,
                        ctypes.byref(source), 0, ctypes.byref(blend), ULW_ALPHA))
                    if not success:
                        raise OSError('UpdateLayeredWindow failed')
                    return True
                finally:
                    if previous:
                        self._gdi32.SelectObject(mem, previous)
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
        self._hint_window: tk.Toplevel | None = None
        self._hint_label: tk.Label | None = None
        self._hint_job = None
        self._press = None
        self._dragging = False
        self._samples = deque()  # (单调秒, 总速度)
        self._image = None       # 当前帧（RGBA），调试与测试可读
        self._opacity = normalize_opacity(config.get('ui_float_bar_opacity', 100), 30)
        self._last_push_succeeded = False
        self._state = {
            'speed': 0.0, 'current_speed': 0.0, 'downloaded': 0, 'total': 0,
            'filename': '', 'downloading': 0, 'pending': 0,
            'status': 'idle', 'status_count': 0,
        }

        # 独立 Toplevel：CTk 会按 DPI 缩放自己的控件，这里要用物理像素自己算
        self.window = tk.Toplevel(master)
        self.window.withdraw()
        self.window.overrideredirect(True)
        self.window.attributes('-topmost', True)
        # 窗口自己的底色永远看不到：内容由 LayeredSurface 推上去
        self.window.configure(bg='#000000')

        self._surface = LayeredSurface()
        if sys.platform == 'darwin':
            self.window.attributes('-transparent', True)
            self.window.configure(bg='systemTransparent')
            self._canvas = tk.Canvas(self.window, bg='systemTransparent', highlightthickness=0, bd=0)
            self._canvas.pack(fill='both', expand=True)
            self._canvas_image = self._canvas.create_image(0, 0, anchor='nw')
        self._scale = self._detect_scale()
        self._mode = 'mini' if config.get('ui_float_bar_mode') == 'mini' else 'normal'
        self._apply_mode_metrics()

        self._theme = self._resolve_theme()
        self._menu_font = ('PingFang SC' if sys.platform == 'darwin' else 'Microsoft YaHei UI', -max(10, round(12 * self._scale)))
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
        self._hide_hint()
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
        if sys.platform == 'darwin':
            self.window.bind('<Button-2>', self._show_menu)
            self.window.bind('<Control-Button-1>', self._show_menu)
        self.window.bind('<Enter>', self._on_enter)
        self.window.bind('<Leave>', self._on_leave)

    def _on_enter(self, _event):
        self._hovered = True
        self._hide_hint()
        if not self._press:
            self.window.configure(cursor='hand2')
            self._hint_job = self.window.after(600, self._show_hint)
        self._draw()

    def _on_leave(self, _event):
        self._hovered = False
        self._hide_hint()
        if not self._dragging:
            self.window.configure(cursor='')
        self._draw()

    def _show_hint(self):
        """只说明数据口径和既有操作，不抢焦点，也不保存配置。"""
        self._hint_job = None
        if self._destroyed or not self._visible or not self._hovered or self._press:
            return
        hint = tk.Toplevel(self.window)
        hint.withdraw()
        hint.overrideredirect(True)
        hint.attributes('-topmost', True)
        hint.configure(bg=self._theme['border'])
        label = tk.Label(hint, text=_hint_text(self._state, self._scale),
                         font=('Microsoft YaHei UI', -max(10, round(11 * self._scale))),
                         fg=self._theme['text'], bg=self._theme['card'], justify='left',
                         padx=self._px(10), pady=self._px(8), wraplength=self._px(250))
        label.pack(padx=1, pady=1)
        hint.update_idletasks()
        width, height = self._px(274), hint.winfo_reqheight()
        bounds = self._bounds()
        x, y = self._pos[0], self._pos[1] - height - self._px(8)
        if y < bounds[1]:
            y = self._pos[1] + self._height + self._px(8)
        x, y = clamp_position(x, y, width, height, bounds)
        hint.geometry(f'{width}x{height}+{x}+{y}')
        if self._surface.available:
            user32 = ctypes.windll.user32
            hwnd = user32.GetAncestor(hint.winfo_id(), 2) or hint.winfo_id()
            exstyle = user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
            user32.SetWindowLongW(hwnd, GWL_EXSTYLE, exstyle | WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE)
        self._hint_window, self._hint_label = hint, label
        hint.deiconify()

    def _hide_hint(self):
        job, self._hint_job = self._hint_job, None
        if job is not None:
            try:
                self.window.after_cancel(job)
            except tk.TclError:
                pass
        hint, self._hint_window = self._hint_window, None
        self._hint_label = None
        if hint is not None:
            try:
                hint.destroy()
            except tk.TclError:
                pass

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
        self._hide_hint()
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
        self._hide_hint()
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
        self._hide_hint()
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
        self._hide_hint()
        self._theme = self._resolve_theme()
        self._menu.configure(font=self._menu_font)
        self._draw()

    def destroy(self):
        """销毁窗口并停止定时器"""
        if self._destroyed:
            return
        self._hide_hint()
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
            if self._hint_label is not None:
                self._hint_label.configure(text=_hint_text(self._state, self._scale))
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
        selected = downloading
        status = 'downloading' if downloading else 'idle'
        if not downloading:
            counts = self.task_manager.db.get_status_counts()
            for candidate in ('verifying', 'pending', 'paused', 'failed', 'verify_failed'):
                if candidate == 'pending':
                    tasks = pending
                elif counts.get(candidate):
                    tasks = self.task_manager.db.get_all_tasks(status=candidate)
                else:
                    continue
                if tasks:
                    selected, status = tasks, candidate
                    break
        current = selected[0] if selected else {}
        self._state = {
            'speed': total_speed,
            'current_speed': max(0.0, float(current.get('speed') or 0)) if downloading else 0.0,
            'downloaded': current.get('downloaded_size') or 0,
            'total': current.get('total_size'),
            'filename': current.get('filename') or '',
            'downloading': len(downloading),
            'pending': len(pending),
            'status': status,
            'status_count': len(selected),
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

    def set_opacity(self, value):
        """仅更新像素背景，预览不写配置、不改变显示开关。"""
        self._opacity = normalize_opacity(value, 30)
        self._draw()

    def _draw(self):
        """重画当前帧并推到窗口上"""
        if self._destroyed:
            return
        try:
            image = render_bar(mode=self._mode, theme=self._theme, state=self._state,
                               samples=list(self._samples), hovered=self._hovered,
                               scale=self._scale, opacity=self._opacity)
        except Exception as error:
            # 画不出来也不能让悬浮条把主程序拖崩
            logger.warning('悬浮条绘制失败: %s', error)
            return
        self._image = image
        if sys.platform == 'darwin':
            self._tk_image = ImageTk.PhotoImage(image, master=self.window)
            self._canvas.itemconfigure(self._canvas_image, image=self._tk_image)
            self._last_push_succeeded = True
            return
        hwnd = getattr(self, '_hwnd', None)
        if hwnd:
            self._last_push_succeeded = self._surface.push(hwnd, image)
