"""XHS note pages: URL normalization, scoped context validation and complete output checks."""
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from downloader.browser.bridge import video_filename
from downloader.browser.security import protect
from downloader.core.youtube_downloader import YoutubeDownloader, failure_message, video_page_url, video_request_context, xhs_url
from scripts.smoke_youtube import page_filename, page_kind

URL = 'https://www.xiaohongshu.com/explore/66c3e1a3000000001e02b5b3'
MODULE = 'downloader.core.youtube_downloader'
COOKIE = dict(name='web_session', value='xhs-private-token', domain='.xiaohongshu.com', path='/',
              secure=True, hostOnly=False)
FORMAT = dict(url='http://sns-video-v3.xhscdn.com/stream/79/110/261/official.mp4?sign=format-sign&t=format-t',
              width=1080, height=1920, vcodec='h264')
FORMAT_720 = dict(url='http://sns-video-v3.xhscdn.com/stream/79/110/259/official-720.mp4?sign=format-sign&t=format-t',
                  width=720, height=1280, vcodec='h264')
SECURE = FORMAT['url'].replace('http://', 'https://')
VIDEO = dict(id='66c3e1a3000000001e02b5b3', duration=12.34, formats=[FORMAT, FORMAT_720])


class XhsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.context = {'headers': {'User-Agent': 'test-browser', 'Referer': URL + '?xsec_token=drop'},
                        'cookies': [COOKIE], 'video': VIDEO}

    def test_note_url_normalization(self):
        for value in (URL, URL + '?xsec_token=abc&xsec_source=pc_search', URL + '/',
                      URL.replace('/explore/', '/discovery/item/'),
                      URL.replace('www.', '')):
            self.assertEqual(xhs_url(value), URL)
        for value in ('https://www.xiaohongshu.com/user/profile/123',
                      'https://xhslink.com/a/abc123', URL.replace('66c3', '66c33'),
                      URL + 'a', URL.replace('b5b3', 'B5B3'), 'http://' + URL.split('//')[1],
                      URL.replace('www.', 'user:secret@www.'), URL.replace('.com', '.com:444'),
                      URL.replace('xiaohongshu.com', 'xiaohongshu.com.evil.test'), None):
            with self.subTest(url=value), self.assertRaises(ValueError):
                xhs_url(value)
        self.assertEqual(video_page_url(URL, 'xhs'), URL)
        self.assertEqual(video_filename({'kind': 'xhs', 'url': URL}), 'XHS-66c3e1a3000000001e02b5b3.mp4')

    def test_xhs_smoke_routes_both_hosts_through_browser_video_path(self):
        for value in (URL, URL.replace('www.', '')):
            self.assertEqual(page_kind(value), 'xhs')
            self.assertEqual(page_filename(value), 'XHS-66c3e1a3000000001e02b5b3.mp4')

    def test_context_rejects_unsafe_formats_and_accepts_the_real_ones(self):
        context = video_request_context(URL, 'xhs', self.context)
        self.assertEqual(context['video']['id'], VIDEO['id'])
        self.assertEqual(context['video']['title'], 'XHS-' + VIDEO['id'])
        self.assertEqual(context['video']['duration'], VIDEO['duration'])
        self.assertEqual(context['video']['formats'][0]['url'], SECURE)
        self.assertEqual(context['video']['formats'][0]['ext'], 'mp4')
        self.assertEqual(context['video']['formats'][0]['protocol'], 'https')
        self.assertEqual(context['headers']['Referer'], 'https://www.xiaohongshu.com/')
        invalid = [None, {**VIDEO, 'id': 'other'}, {**VIDEO, 'duration': 0}, {**VIDEO, 'duration': float('nan')},
                   {**VIDEO, 'duration': True}, {**VIDEO, 'duration': '12'}, {**VIDEO, 'formats': []},
                   {**VIDEO, 'formats': [FORMAT] * 65}, {**VIDEO, 'formats': [None]},
                   {**VIDEO, 'formats': [{**FORMAT, 'width': 1.5}]},
                   {**VIDEO, 'formats': [{**FORMAT, 'height': 0}]},
                   {**VIDEO, 'formats': [{**FORMAT, 'width': 16385}]},
                   {**VIDEO, 'formats': [{**FORMAT, 'vcodec': 'av1'}]}]
        for url in ('https://evil.test/stream/fake.mp4',
                    'http://evil.test/stream/fake.mp4',
                    'https://xhscdn.com/stream/no-sub.mp4',
                    'https://sns-video-hw.xhscdn.com/stream/movie.m3u8',
                    'https://sns-video-hw.xhscdn.com/stream/movie.m4s',
                    'ftp://sns-video-hw.xhscdn.com/stream/movie.mp4',
                    'https://user:secret@sns-video-hw.xhscdn.com/stream/movie.mp4',
                    'https://sns-video-hw.xhscdn.com:444/stream/movie.mp4',
                    FORMAT['url'] + '&range=0-99'):
            invalid.append({**VIDEO, 'formats': [{**FORMAT, 'url': url}]})
        for video in invalid:
            with self.subTest(video=video), self.assertRaises(ValueError):
                video_request_context(URL, 'xhs', {**self.context, 'video': video})
        with self.assertRaises(ValueError):
            video_request_context(URL, 'xhs', {'headers': {}})

    def test_portrait_tiers_use_the_short_edge_and_keep_http_upgrade(self):
        # 1080x1920 counts as 1080p: a 720 limit drops it and keeps the 720x1280 tier.
        formats = video_request_context(URL, 'xhs', self.context, 720)['video']['formats']
        self.assertEqual(len(formats), 1)
        self.assertEqual(min(formats[0]['width'], formats[0]['height']), 720)
        self.assertEqual(formats[0]['url'], FORMAT_720['url'].replace('http://', 'https://'))
        self.assertEqual(len(video_request_context(URL, 'xhs', self.context, 1080)['video']['formats']), 2)
        self.assertEqual(video_request_context(URL, 'xhs', self.context, 1080)['video']['formats'][0]['url'], SECURE)
        with self.assertRaises(ValueError) as caught:
            video_request_context(URL, 'xhs', self.context, 480)
        self.assertIn('清晰度', str(caught.exception))

    def test_worker_uses_info_json_with_headers_and_duration_gate(self):
        completed, failed = Mock(), Mock()
        task = dict(task_id='xhs-task', url=URL, download_type='xhs', video_height=720,
                    browser_context=protect(self.context), save_path=str(self.root / 'out.mp4'))
        with patch(MODULE + '.require_tools', return_value=self.root):
            worker = YoutubeDownloader(task, self.config(), Mock(), completed, failed, lambda: True)
        worker.folder.mkdir(parents=True)
        (worker.folder / 'video.mp4').write_bytes(b'video')
        infos, cookies = [], []

        def launch(command, **kwargs):
            self.assertEqual(command[-2], '--load-info-json')
            info = Path(command[-1])
            infos.append(info)
            metadata = json.loads(info.read_text())
            self.assertEqual(metadata['id'], VIDEO['id'])
            self.assertEqual(metadata['title'], 'XHS-' + VIDEO['id'])
            self.assertEqual(metadata['formats'][0]['url'], FORMAT_720['url'].replace('http://', 'https://'))
            self.assertNotIn(URL, command)
            self.assertNotIn('format-sign', ' '.join(command))
            self.assertIn('--no-playlist', command)
            # 竖屏下 yt-dlp 的 [height<=N] 选不到，必须固定到已校验的那一条
            self.assertEqual(command[command.index('--format') + 1], 'xhs-0')
            self.assertEqual(metadata['formats'][0]['format_id'], 'xhs-0')
            self.assertIn('User-Agent:test-browser', command)
            self.assertIn('Referer:https://www.xiaohongshu.com/', command)
            self.assertIn('--add-header', command)
            return Mock(stdout=io_empty(), wait=Mock(return_value=0))

        probe = SimpleNamespace(returncode=0, stdout=json.dumps(
            {'streams': [{'codec_type': 'video'}, {'codec_type': 'audio'}], 'format': {'duration': '12.34'}}))
        with patch(MODULE + '.subprocess.Popen', side_effect=launch), \
                patch(MODULE + '.subprocess.run', return_value=probe):
            worker.run()
        self.assertTrue(infos)
        self.assertTrue(all(not info.exists() for info in infos))
        completed.assert_called_once()
        failed.assert_not_called()

    def test_failure_messages_are_site_specific(self):
        self.assertIn('小红书', failure_message(['HTTP Error 403: Forbidden'], 'xhs'))
        self.assertIn('抖音', failure_message(['HTTP Error 403: Forbidden'], 'douyin'))
        self.assertIn('403', failure_message(['HTTP Error 403: Forbidden'], 'youtube'))

    def config(self):
        return SimpleNamespace(temp_dir=str(self.root / 'parts'), timeout=3, retry_times=1,
                               speed_limit=0, proxies={}, download_dir=str(self.root / 'out'))


def io_empty():
    import io
    return io.StringIO('')


if __name__ == '__main__':
    unittest.main()
