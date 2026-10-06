"""Application icon resources must work both from source and a frozen bundle."""
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

from PIL import Image
from downloader.ui.tray_manager import TrayManager, icon_path


class BrandingTests(unittest.TestCase):
    def test_multi_size_icon_has_transparency_and_download_mark(self):
        with Image.open(icon_path()) as icon:
            self.assertTrue({(16, 16), (24, 24), (32, 32), (48, 48), (256, 256)} <= icon.ico.sizes())
            for size in ((16, 16), (32, 32), (256, 256)):
                image = icon.ico.getimage(size).convert('RGBA')
                self.assertEqual(image.getpixel((0, 0))[3], 0)
                self.assertGreater(len(set(image.getdata())), 3)

    def test_frozen_resource_path_does_not_follow_exe_or_working_directory(self):
        with patch.object(sys, '_MEIPASS', 'C:/isolated-bundle', create=True):
            self.assertEqual(Path(icon_path()), Path('C:/isolated-bundle/assets/icon.ico'))

    def test_tray_and_fallback_use_a_symbol_not_a_solid_square(self):
        tray = TrayManager()
        self.assertEqual(tray.app_name, 'daw下载器')
        self.assertTrue(Path(tray._icon_path).is_file())
        for path in (tray._icon_path, ''):
            tray._icon_path = path
            image = tray._create_icon_image()
            self.assertEqual(image.mode, 'RGBA')
            self.assertGreaterEqual(len(set(image.getdata())), 3)


if __name__ == '__main__':
    unittest.main()
