"""侧栏字号按像素对齐 CTk，避免正数磅值把分类树反复放大。"""
from tkinter import font as tkfont
import os
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import Mock

import customtkinter as ctk

from downloader.ui.main_window import MainWindow
from downloader.ui.typography import SIDEBAR_HEADING_FONT, SIDEBAR_ITEM_FONT, scale_sidebar_font


class SidebarFontTests(unittest.TestCase):
    def test_constants_are_13_pixels_and_heading_only_adds_weight(self):
        self.assertEqual(SIDEBAR_ITEM_FONT[1], -13)
        self.assertEqual(SIDEBAR_HEADING_FONT[:2], SIDEBAR_ITEM_FONT)
        self.assertEqual(SIDEBAR_HEADING_FONT[2:], ('bold',))

    def test_only_existing_widget_scaling_is_applied_once(self):
        for scaling, pixels in ((0.8, 10), (1.0, 13), (1.25, 16), (1.5, 20), (2.0, 26)):
            with self.subTest(scaling=scaling):
                self.assertEqual(scale_sidebar_font(SIDEBAR_ITEM_FONT, scaling)[1], -pixels)
                self.assertEqual(scale_sidebar_font(SIDEBAR_HEADING_FONT, scaling)[1:],
                                 (-pixels, 'bold'))


class SidebarWidgetFontTests(unittest.TestCase):
    def set_up_widgets(self):
        self.root = ctk.CTk()
        self.root.withdraw()
        self.original_scaling = ctk.ScalingTracker.widget_scaling
        self.root.sidebar_frame = ctk.CTkFrame(self.root, width=240)
        self.root._sidebar_colors_cache = {'bg': '#2b2b2b', 'fg': '#eeeeee', 'muted': '#aaaaaa'}
        self.root._sidebar_heading_labels = []
        self.root._active_category = 'all'
        self.root.category_buttons = {}
        self.root.category_labels = {}
        self.root._set_category_selected = Mock()

    def tear_down_widgets(self):
        ctk.set_widget_scaling(self.original_scaling)
        for timer in self.root.tk.call('after', 'info'):
            self.root.after_cancel(timer)
        self.root.destroy()

    def check_fonts(self, scaling):
        ctk.set_widget_scaling(scaling)
        global_scaling = self.root.tk.call('tk', 'scaling')
        button = ctk.CTkButton(self.root, text='设置')
        reference = button._font.create_scaled_tuple(ctk.ScalingTracker.get_widget_scaling(self.root))
        heading = MainWindow._create_sidebar_heading(self.root, '分类')
        item = MainWindow._create_category_button(self.root, self.root.sidebar_frame, 'all', '全部 (0)')
        for label in (heading, item):
            actual = self.root.tk.splitlist(label.cget('font'))
            self.assertEqual(int(actual[1]), reference[1])
        self.assertEqual(heading.cget('padx'), 10)
        self.assertEqual(item.cget('padx'), 12)
        self.assertEqual(item.cget('pady'), 6)
        self.assertEqual(self.root.sidebar_frame.cget('width'), 240)
        self.assertEqual(self.root.tk.call('tk', 'scaling'), global_scaling)
        # 同一台机器同一 Tk 缩放下，新侧栏必须显著小于被用户指出过大的 16pt。
        # CTk 自动 DPI 与 Tk scaling 在当前 Windows 会话可能不同。
        # 旧 16pt 基线也乘同一 CTk 有效缩放，避免拿缩放后的新字体比未缩放旧字体。
        old_pixels = round(16 * float(global_scaling)
                           * ctk.ScalingTracker.get_widget_scaling(self.root))
        old = tkfont.Font(root=self.root, family=SIDEBAR_ITEM_FONT[0], size=-old_pixels)
        new = tkfont.Font(root=self.root, font=item.cget('font'))
        if scaling == 1.0:
            self.assertLess(new.measure('下载中 (0)'), old.measure('下载中 (0)') * 0.8)

    def check_in_subprocess(self, scaling):
        # 全量 GUI 用例可能在 CTk 全局缩放表留下已销毁弹窗的回调。
        # 缩放验证用全新的 Tcl/CTk 进程，不改产品逻辑，也不清理别的用例状态。
        root = Path(__file__).resolve().parents[1]
        code = ('from test_sidebar_typography import SidebarWidgetFontTests; '
                'import sys; case = SidebarWidgetFontTests(); case.set_up_widgets(); '
                '\ntry: case.check_fonts(float(sys.argv[1]))'
                '\nfinally: case.tear_down_widgets()')
        result = subprocess.run([sys.executable, '-B', '-c', code, str(scaling)],
                                cwd=root, capture_output=True, text=True, encoding='utf-8', timeout=30,
                                env=dict(os.environ, PYTHONIOENCODING='utf-8',
                                         PYTHONPATH=os.pathsep.join((str(root), str(root / 'tests')))))
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_real_widgets_match_toolbar_at_100_percent(self):
        self.check_in_subprocess(1.0)

    def test_real_widgets_match_toolbar_at_150_percent(self):
        self.check_in_subprocess(1.5)


if __name__ == '__main__':
    unittest.main()
