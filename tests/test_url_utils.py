# -*- coding: utf-8 -*-
"""剪贴板链接提取、域名忽略与共用进度文案的纯函数测试（不依赖Tk）"""
import os
import tempfile
import unittest

from downloader.utils.config import ConfigManager
from downloader.utils.file_utils import format_progress_texts
from downloader.utils.url_utils import (
    extract_clipboard_url,
    host_from_url,
    host_is_ignored,
    normalize_host,
)


class NormalizeHostTests(unittest.TestCase):
    def test_lowercases_and_strips_www(self):
        self.assertEqual(normalize_host("WWW.Example.com"), "example.com")

    def test_accepts_full_url_input(self):
        self.assertEqual(normalize_host("https://www.example.com/path?q=1"), "example.com")

    def test_strips_port_path_and_dots(self):
        self.assertEqual(normalize_host("example.com:8080/x"), "example.com")
        self.assertEqual(normalize_host(".a.com."), "a.com")

    def test_blank_returns_empty(self):
        self.assertEqual(normalize_host("   "), "")
        self.assertEqual(normalize_host(None), "")


class ExtractClipboardUrlTests(unittest.TestCase):
    def test_plain_http_and_https(self):
        self.assertEqual(extract_clipboard_url("http://a.com/f.zip"), "http://a.com/f.zip")
        self.assertEqual(extract_clipboard_url("https://a.com/f.zip"), "https://a.com/f.zip")

    def test_surrounding_whitespace_is_trimmed(self):
        self.assertEqual(extract_clipboard_url("  https://a.com/f.zip \n"), "https://a.com/f.zip")

    def test_multiline_rejected(self):
        self.assertEqual(extract_clipboard_url("http://a.com\nhttp://b.com"), "")
        self.assertEqual(extract_clipboard_url("http://a.com\r\nhttp://b.com"), "")

    def test_non_http_schemes_rejected(self):
        for text in ("ftp://a.com/f", "magnet:?xt=urn:btih:xyz", "www.example.com/f.zip"):
            self.assertEqual(extract_clipboard_url(text), "")

    def test_missing_host_rejected(self):
        self.assertEqual(extract_clipboard_url("http://"), "")

    def test_empty_rejected(self):
        self.assertEqual(extract_clipboard_url(""), "")
        self.assertEqual(extract_clipboard_url(None), "")
        self.assertEqual(extract_clipboard_url("   "), "")


class HostFromUrlTests(unittest.TestCase):
    def test_normalizes_host_with_port(self):
        self.assertEqual(host_from_url("https://WWW.Example.com:8080/x"), "example.com")

    def test_keeps_non_www_subdomain(self):
        # dl.example.com 不能归一成 example.com，否则忽略站点会误伤无关子域
        self.assertEqual(host_from_url("https://dl.example.com/f.zip"), "dl.example.com")
        self.assertEqual(host_from_url("https://m.example.com/f.zip"), "m.example.com")

    def test_strips_userinfo(self):
        self.assertEqual(host_from_url("https://user:pass@example.com/f.zip"), "example.com")

    def test_invalid_url_returns_empty(self):
        self.assertEqual(host_from_url("http://"), "")
        self.assertEqual(host_from_url(""), "")


class HostIsIgnoredTests(unittest.TestCase):
    def test_exact_match(self):
        self.assertTrue(host_is_ignored("https://example.com/a.zip", ["example.com"]))

    def test_subdomain_matches(self):
        self.assertTrue(host_is_ignored("https://m.example.com/a.zip", ["example.com"]))
        self.assertTrue(host_is_ignored("https://www.example.com/a.zip", ["example.com"]))

    def test_different_site_not_ignored(self):
        self.assertFalse(host_is_ignored("https://other.com/a.zip", ["example.com"]))
        # 前缀相似但不是子域名也不能误伤
        self.assertFalse(host_is_ignored("https://notexample.com/a.zip", ["example.com"]))

    def test_empty_list_never_ignores(self):
        self.assertFalse(host_is_ignored("https://example.com/a.zip", []))
        self.assertFalse(host_is_ignored("https://example.com/a.zip", None))

    def test_invalid_url_not_ignored(self):
        self.assertFalse(host_is_ignored("http://", ["example.com"]))


class ClipboardConfigTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="downloader-url-utils-")
        self.path = os.path.join(self.temp.name, "config.json")

    def tearDown(self):
        self.temp.cleanup()

    def test_defaults(self):
        config = ConfigManager(self.path)
        self.assertTrue(config.clipboard_monitor_enabled)
        self.assertEqual(config.clipboard_ignore_hosts, [])

    def test_ignore_hosts_normalized_deduped_and_persisted(self):
        config = ConfigManager(self.path)
        config.clipboard_ignore_hosts = [
            "WWW.Example.com", "example.com", " https://Foo.org/x ", "", "bar.cn",
        ]
        self.assertEqual(config.clipboard_ignore_hosts, ["example.com", "foo.org", "bar.cn"])
        config.save()
        reloaded = ConfigManager(self.path)
        self.assertEqual(reloaded.clipboard_ignore_hosts, ["example.com", "foo.org", "bar.cn"])
        self.assertTrue(reloaded.clipboard_monitor_enabled)

    def test_toggle_persisted(self):
        config = ConfigManager(self.path)
        config.clipboard_monitor_enabled = False
        config.save()
        self.assertFalse(ConfigManager(self.path).clipboard_monitor_enabled)


class FormatProgressTextsTests(unittest.TestCase):
    def test_downloading_shows_eta(self):
        size_text, eta_text, speed_text = format_progress_texts(8192, 100000, 4096, 'downloading')
        self.assertEqual(size_text, "已下载 8.00 KB / 97.66 KB")
        self.assertEqual(eta_text, "剩余 00:22")
        self.assertEqual(speed_text, "4.00 KB/s")

    def test_paused_hides_eta(self):
        size_text, eta_text, _ = format_progress_texts(8192, 100000, 4096, 'paused')
        self.assertEqual(size_text, "已下载 8.00 KB / 97.66 KB")
        self.assertEqual(eta_text, "剩余 --")

    def test_unknown_total(self):
        size_text, eta_text, _ = format_progress_texts(0, 0, 0, 'pending')
        self.assertEqual(size_text, "已下载 0 B / 未知")
        self.assertEqual(eta_text, "剩余 --")

    def test_completed_hides_eta_even_with_speed(self):
        _size, eta_text, _speed = format_progress_texts(1000, 1000, 500, 'completed')
        self.assertEqual(eta_text, "剩余 --")


if __name__ == '__main__':
    unittest.main()
