"""隔离的真实 Tk/Win32 视觉验收，截图只裁剪自建窗口，不读取用户数据。"""
import argparse
import ctypes
from ctypes import wintypes
import json
import subprocess
import sys
import tempfile
import time
import tkinter as tk
import traceback
import types
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import customtkinter as ctk
from PIL import Image, ImageDraw, ImageGrab
from downloader.utils.config import ConfigManager
from downloader.database.db_manager import DatabaseManager
from downloader.core.download_engine import DownloadEngine
from downloader.core.task_manager import TaskManager
from downloader.ui.main_window import MainWindow
from downloader.ui.settings_dialog import SettingsDialog
import downloader.ui.float_bar as floating


def pump(app, duration=0.15):
    deadline = time.monotonic() + duration
    while time.monotonic() < deadline:
        app.update()
        time.sleep(0.01)


def native_rect(window):
    user32 = ctypes.windll.user32
    user32.GetAncestor.restype = wintypes.HWND
    user32.GetAncestor.argtypes = [wintypes.HWND, wintypes.UINT]
    user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
    hwnd = user32.GetAncestor(window.winfo_id(), 2) or window.winfo_id()
    rect = wintypes.RECT()
    if not user32.GetWindowRect(hwnd, ctypes.byref(rect)):
        raise RuntimeError('cannot read native window rectangle')
    return hwnd, (rect.left, rect.top, rect.right, rect.bottom)


def screenshot(window, path, backdrop=None):
    hwnd, box = native_rect(window)
    if backdrop is None:
        # PrintWindow 只截取目标窗口，避免 DPI 虚拟坐标误裁到用户其它窗口。
        image = ImageGrab.grab(window=hwnd)
    else:
        _, background = native_rect(backdrop)
        if not (background[0] <= box[0] < box[2] <= background[2] and
                background[1] <= box[1] < box[3] <= background[3]):
            raise RuntimeError('floating window is not inside owned test backdrop')
        user32 = ctypes.windll.user32
        user32.WindowFromPoint.restype = wintypes.HWND
        user32.WindowFromPoint.argtypes = [wintypes.POINT]
        point = wintypes.POINT((box[0]+box[2])//2, (box[1]+box[3])//2)
        visible = user32.WindowFromPoint(point)
        visible = user32.GetAncestor(visible, 2) or visible
        if visible != hwnd:
            raise RuntimeError('another window occludes the isolated test surface; no screenshot captured')
        image = ImageGrab.grab(bbox=box, all_screens=True, include_layered_windows=True)
    image.save(path)


def assert_layout(dialog, tab):
    errors = []
    x0, y0 = tab.winfo_rootx(), tab.winfo_rooty()
    right, bottom = x0 + tab.winfo_width(), y0 + tab.winfo_height()
    def visit(widget):
        if isinstance(widget, (ctk.CTkLabel, ctk.CTkCheckBox, ctk.CTkSlider,
                               ctk.CTkSegmentedButton, ctk.CTkEntry, ctk.CTkTextbox)):
            x, y = widget.winfo_rootx(), widget.winfo_rooty()
            if widget.winfo_ismapped() and (x < x0-2 or y < y0-2 or
                    x + widget.winfo_width() > right+2 or y + widget.winfo_height() > bottom+2):
                errors.append((type(widget).__name__, x-x0, y-y0,
                               widget.winfo_width(), widget.winfo_height()))
            return
        for child in widget.winfo_children():
            visit(child)
    visit(tab)
    if errors:
        raise AssertionError(f'tab content clipped in {tab.winfo_width()}x{tab.winfo_height()}, '
                             f'dialog={dialog.winfo_width()}x{dialog.winfo_height()}, '
                             f'screen={dialog.winfo_screenwidth()}x{dialog.winfo_screenheight()}: {errors}')


def case(scale, theme, output):
    # 仅本隔离子进程：先消除系统的坐标虚拟化，再模拟 CTk 100/150/200%。
    user32 = ctypes.windll.user32
    user32.SetThreadDpiAwarenessContext.restype = ctypes.c_void_p
    user32.SetThreadDpiAwarenessContext.argtypes = [ctypes.c_void_p]
    if not user32.SetThreadDpiAwarenessContext(ctypes.c_void_p(-4)):
        raise RuntimeError('cannot establish isolated per-monitor DPI coordinates')
    ctk.deactivate_automatic_dpi_awareness()
    ctk.set_widget_scaling(scale)
    ctk.set_window_scaling(scale)
    output.mkdir(parents=True, exist_ok=True)
    old_source = subprocess.run(['git', 'show', 'HEAD:downloader/ui/float_bar.py'],
        cwd=ROOT, check=True, capture_output=True, encoding='utf-8').stdout
    old = types.ModuleType('appearance_baseline')
    exec(compile(old_source, 'HEAD:downloader/ui/float_bar.py', 'exec'), old.__dict__)
    errors, pushes = [], []
    with tempfile.TemporaryDirectory(prefix='appearance-visual-') as directory, \
            patch('downloader.ui.main_window.TrayManager.start'), \
            patch('downloader.ui.main_window.TrayManager.stop'):
        config = ConfigManager(str(Path(directory) / 'config.json'))
        config.temp_dir = str(Path(directory) / 'parts')
        config.download_dir = str(Path(directory) / 'files')
        config.clipboard_monitor_enabled = False
        config.ui_appearance_mode = theme
        db = DatabaseManager(str(Path(directory) / 'test.db'))
        manager = TaskManager(DownloadEngine(db, config), db)
        app = MainWindow(manager)
        app.report_callback_exception = lambda *args: errors.append(''.join(traceback.format_exception(*args)))
        backdrop = None
        try:
            app.withdraw()
            dialog = SettingsDialog(app, config, on_save_callback=app._on_settings_saved)
            dialog.geometry('560x600+80+80')
            dialog.attributes('-topmost', True)
            dialog.deiconify()
            for name in ('下载', '外观', '其他'):
                dialog.tabview.set(name)
                pump(app, 0.25)
                screenshot(dialog, output / f'settings-{name}.png')
                assert_layout(dialog, dialog.tabview._tab_dict[name])
            self_width, self_height = dialog.winfo_width(), dialog.winfo_height()
            if abs(self_width - round(560*scale)) > 2 or abs(self_height - round(600*scale)) > 2:
                raise AssertionError(f'settings dimensions changed: {self_width}x{self_height}')
            dialog.destroy()
            backdrop = tk.Toplevel(app)
            backdrop.overrideredirect(True)
            backdrop.geometry('800x500+100+100')
            backdrop.attributes('-topmost', True)
            app._set_float_bar_visible(True)
            bar = app.float_bar
            bar._scale = scale
            state = dict(status='downloading', speed=123456789, downloaded=70, total=100,
                         filename='很长的中文文件名：会议录像与学习资料.mp4', downloading=2, pending=1)
            samples = [(1, 20), (2, 12), (3, 31), (4, 22), (5, 26)]
            for background, color in (('dark', '#181a20'), ('light', '#f0f2f5'), ('color', '#36577b')):
                backdrop.configure(bg=color)
                backdrop.lift()
                for mode in ('normal', 'mini'):
                    bar._mode = mode
                    bar._apply_mode_metrics()
                    bar.window.geometry(f'{bar._width}x{bar._height}+150+150')
                    bar.window.deiconify()
                    bar.window.lift()
                    bar._cancel_tick()
                    pump(app, 0.05)
                    before = old.render_bar(mode=mode, theme=old.THEMES[theme], state=state,
                                            samples=samples, hovered=False, scale=scale)
                    before.save(output / f'pure-before-{mode}.png')
                    with patch.object(floating, 'premultiplied_bgra',
                        lambda im: im.convert('RGBa').convert('RGBA').tobytes('raw', 'BGRA')):
                        if not bar._surface.push(bar._hwnd, before):
                            raise AssertionError('native baseline push failed')
                    pump(app, 0.05)
                    screenshot(bar.window, output / f'actual-{background}-{mode}-before.png', backdrop)
                    for opacity in (30, 100):
                        image = floating.render_bar(mode=mode, theme=floating.THEMES[theme], state=state,
                            samples=samples, hovered=False, scale=scale, opacity=opacity)
                        image.save(output / f'pure-after-{mode}-{opacity}.png')
                        pushed = bar._surface.push(bar._hwnd, image)
                        pushes.append(pushed)
                        if not pushed:
                            raise AssertionError('native updated push failed')
                        pump(app, 0.05)
                        screenshot(bar.window, output / f'actual-{background}-{mode}-{opacity}.png', backdrop)
            if errors:
                raise AssertionError(errors)
        finally:
            if backdrop is not None:
                backdrop.destroy()
            app._exit_app()
        result = dict(scale=scale, theme=theme, settings_size=[self_width, self_height],
                      native_pushes=len(pushes), native_all_success=all(pushes), callback_errors=errors,
                      physical_mixed_dpi_tested=False, exe_tested=False)
        (output / 'result.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
        print(json.dumps(result, ensure_ascii=False))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--case', action='store_true')
    parser.add_argument('--scale', type=float, default=1)
    parser.add_argument('--theme', choices=['dark', 'light'], default='dark')
    parser.add_argument('--output', type=Path, default=ROOT / 'output' / 'appearance-acceptance')
    args = parser.parse_args()
    if args.case:
        case(args.scale, args.theme, args.output)
        return
    results = []
    for theme in ('dark', 'light'):
        for scale in (1, 1.5, 2):
            output = args.output / f'{theme}-{scale:g}'
            completed = subprocess.run([sys.executable, '-B', str(Path(__file__).resolve()), '--case',
                '--scale', str(scale), '--theme', theme, '--output', str(output)],
                cwd=ROOT, capture_output=True, encoding='utf-8', errors='replace', timeout=90)
            output.mkdir(parents=True, exist_ok=True)
            (output / 'process.log').write_text(completed.stdout + completed.stderr, encoding='utf-8')
            if completed.returncode:
                raise RuntimeError(f'visual case failed: {output}\n{completed.stdout}{completed.stderr}')
            results.append(json.loads((output / 'result.json').read_text(encoding='utf-8')))
    sheet = Image.new('RGB', (1340, 720), '#101217')
    draw = ImageDraw.Draw(sheet)
    font = floating._load_font(20)
    draw.text((20, 14), '实际 Win32 窗口截图 · 同主题/同缩放 · 左：原效果，中：新 100%，右：新 30%', font=font, fill='white')
    y = 60
    for theme in ('dark', 'light'):
        folder = args.output / f'{theme}-1.5'
        for mode in ('normal', 'mini'):
            draw.text((20, y), f'{theme} / {mode}', font=font, fill='#a8b2c0')
            for index, suffix in enumerate(('before', '100', '30')):
                image = Image.open(folder / f'actual-color-{mode}-{suffix}.png').convert('RGB')
                sheet.paste(image, (20 + index*440, y+28))
            y += 220 if mode == 'normal' else 80
    sheet.save(args.output / 'actual-contact-sheet.png')
    (args.output / 'result.json').write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding='utf-8')
    print(f'PASS: {len(results)} isolated real Tk/Win32 visual cases; {args.output}')


if __name__ == '__main__':
    main()
