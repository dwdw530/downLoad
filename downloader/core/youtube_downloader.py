"""Supported video pages downloaded through the upstream yt-dlp/FFmpeg toolchain."""
import json
import math
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import threading
import tempfile
from urllib.parse import parse_qs, urlsplit

from downloader.utils.config import get_app_root
from downloader.browser.security import http_url, normalize_stream_context, unprotect

VIDEO_KINDS = ('youtube', 'x', 'bilibili', 'douyin', 'hls', 'dash')
STREAM_KINDS = ('hls', 'dash')
CONTEXT_KINDS = ('douyin', *STREAM_KINDS)


def video_task_url(value, kind):
    return http_url(value) if kind in STREAM_KINDS else video_page_url(value, kind)


def youtube_url(value):
    if not isinstance(value, str) or len(value) > 2048:
        raise ValueError('无效的 YouTube 地址')
    parsed = urlsplit(value)
    if parsed.scheme not in ('http', 'https') or parsed.username or parsed.password or parsed.port not in (None, 80, 443):
        raise ValueError('无效的 YouTube 地址')
    if parsed.hostname in ('youtube.com', 'www.youtube.com', 'm.youtube.com', 'www.youtube-nocookie.com'):
        if parsed.path == '/watch':
            video_id = parse_qs(parsed.query).get('v', [''])[0]
        elif parsed.path.startswith(('/shorts/', '/embed/')):
            video_id = parsed.path.split('/')[2]
        else:
            video_id = ''
    elif parsed.hostname == 'youtu.be':
        video_id = parsed.path.removeprefix('/')
    else:
        video_id = ''
    if not re.fullmatch(r'[A-Za-z0-9_-]{11}', video_id):
        raise ValueError('仅接受单个 YouTube 视频地址')
    return 'https://www.youtube.com/watch?v=' + video_id


def x_url(value):
    if not isinstance(value, str) or len(value) > 2048:
        raise ValueError('无效的 X 帖子地址')
    parsed = urlsplit(value)
    if (parsed.scheme not in ('http', 'https') or parsed.username or parsed.password
            or parsed.port not in (None, 80, 443)
            or parsed.hostname not in ('x.com', 'www.x.com', 'twitter.com', 'www.twitter.com', 'mobile.twitter.com')):
        raise ValueError('无效的 X 帖子地址')
    match = re.fullmatch(r'/([A-Za-z0-9_]{1,15})/status/([0-9]{1,25})(?:/video/([1-4]))?/?', parsed.path)
    if not match:
        raise ValueError('仅接受单个 X 帖子视频地址')
    user, post, index = match.groups()
    return f'https://x.com/{user}/status/{post}/video/{index or "1"}'


def bilibili_url(value):
    if not isinstance(value, str) or len(value) > 2048 or any(ord(c) < 32 for c in value):
        raise ValueError('无效的 B 站视频地址')
    parsed = urlsplit(value)
    if (parsed.scheme not in ('http', 'https') or parsed.username or parsed.password
            or parsed.port not in (None, 80, 443)
            or parsed.hostname not in ('bilibili.com', 'www.bilibili.com', 'm.bilibili.com')):
        raise ValueError('无效的 B 站视频地址')
    match = re.fullmatch(r'/video/(BV[A-Za-z0-9]{10}|av[1-9][0-9]{0,19})/?', parsed.path)
    parts = parse_qs(parsed.query, keep_blank_values=True).get('p', ['1'])
    if not match or len(parts) != 1 or not re.fullmatch(r'[1-9][0-9]{0,4}', parts[0]):
        raise ValueError('仅接受 B 站普通视频及有效的分 P 地址')
    # Explicit p=1 prevents a multi-part video from becoming a playlist.
    return f'https://www.bilibili.com/video/{match[1]}/?p={parts[0]}'


def douyin_url(value):
    if not isinstance(value, str) or len(value) > 2048 or any(ord(c) < 32 for c in value):
        raise ValueError('无效的抖音视频地址')
    parsed = urlsplit(value)
    if (parsed.scheme not in ('http', 'https') or parsed.username or parsed.password
            or parsed.port not in (None, 80, 443)
            or parsed.hostname not in ('douyin.com', 'www.douyin.com')):
        raise ValueError('无效的抖音视频地址')
    match = re.fullmatch(r'/video/([1-9][0-9]{0,24})/?', parsed.path)
    ids = parse_qs(parsed.query, keep_blank_values=True).get('modal_id', [])
    video_id = match[1] if match else ids[0] if parsed.path in ('', '/') and len(ids) == 1 else ''
    if not re.fullmatch(r'[1-9][0-9]{0,24}', video_id):
        raise ValueError('仅接受单个抖音视频地址，请打开视频详情页')
    return f'https://www.douyin.com/video/{video_id}'


def video_page_url(value, kind='youtube'):
    if kind == 'youtube':
        return youtube_url(value)
    if kind == 'x':
        return x_url(value)
    if kind == 'bilibili':
        return bilibili_url(value)
    if kind == 'douyin':
        return douyin_url(value)
    raise ValueError('不支持的视频站点')


def video_request_context(url, kind, context):
    result = normalize_stream_context(url, context)
    if kind != 'douyin':
        return result
    video_id = douyin_url(url).rsplit('/', 1)[-1]
    video = (context or {}).get('video')
    if not isinstance(video, dict) or video.get('id') != video_id:
        raise ValueError('未获取当前抖音视频信息，请刷新视频详情页后重试')
    duration = video.get('duration')
    if isinstance(duration, bool) or not isinstance(duration, (int, float)) or not math.isfinite(duration) or duration <= 0:
        raise ValueError('抖音视频时长无效，无法确认完整视频')
    formats = video.get('formats')
    if not isinstance(formats, list) or not 1 <= len(formats) <= 64:
        raise ValueError('未找到抖音完整 MP4，请刷新视频详情页后重试')
    normalized = []
    seen = set()
    for item in formats:
        if not isinstance(item, dict):
            raise ValueError('无效的抖音视频格式')
        media_url = http_url(item.get('url'))
        parsed = urlsplit(media_url)
        if (parsed.scheme != 'https' or parsed.port not in (None, 443)
                or not parsed.hostname.endswith('.douyinvod.com')
                or re.search(r'/media-(?:video|audio)-', parsed.path)
                or 'range' in parse_qs(parsed.query, keep_blank_values=True)):
            raise ValueError('拒绝将抖音分轨或未知地址作为完整视频')
        if any(type(item.get(key)) is not int or not 0 < item[key] <= 16384 for key in ('width', 'height')):
            raise ValueError('无效的抖音视频分辨率')
        if item.get('vcodec') not in ('h264', 'h265'):
            raise ValueError('无效的抖音视频编码')
        if media_url in seen:
            continue
        seen.add(media_url)
        normalized.append({'format_id': f'douyin-{len(normalized)}', 'url': media_url,
                           'ext': 'mp4', 'protocol': 'https', 'vcodec': item['vcodec'], 'acodec': 'unknown',
                           'width': item['width'], 'height': item['height']})
    result['video'] = {'id': video_id, 'title': 'Douyin-' + video_id, 'duration': duration,
                       'is_live': False, 'formats': normalized}
    return result


def tools_directory():
    root = Path(get_app_root())
    return root / 'video-tools' if getattr(sys, 'frozen', False) else root / 'vendor' / 'video'


def require_tools(directory=None):
    directory = Path(directory or tools_directory())
    if not all((directory / name).is_file() and (directory / name).stat().st_size > 0
               for name in ('yt-dlp.exe', 'ffmpeg.exe', 'ffprobe.exe', 'node.exe')):
        raise ValueError('视频下载组件缺失，请安装包含 video-tools 的完整更新包')
    return directory


def failure_message(lines, kind=None):
    text = '\n'.join(lines).lower()
    if any(word in text for word in ('drm', 'sample-aes', 'widevine', 'playready')):
        return '受 DRM 保护的视频不支持下载'
    if 'live' in text and ('filter' in text or 'match' in text):
        return '当前仅支持点播视频，不支持持续直播录制'
    if 'fresh cookies' in text or (kind == 'douyin' and any(
            word in text for word in ('sign in', 'not a bot', 'login', 'log in', 'authentication'))):
        return '抖音需要新的访问凭据，请刷新视频页面后重新下载'
    if any(word in text for word in ('sign in', 'not a bot', 'login', 'log in', 'authentication')):
        return '网站要求登录或验证，此版本不会读取账号 Cookie'
    if 'private video' in text or 'video unavailable' in text:
        return '视频不可用、私有或当前地区无法访问'
    if '403' in text:
        if kind == 'douyin':
            return '抖音视频地址已过期或被拒绝，请刷新视频页面后重新下载'
        return '网站拒绝下载（403），请检查网络或更新视频组件'
    if 'timed out' in text or 'unable to download' in text:
        return '视频下载网络失败，请检查代理或稍后重试'
    return '视频解析或合并失败，请检查网络、磁盘空间及视频组件'


class YoutubeDownloader:
    def __init__(self, task, config, progress, completed, failed, is_current, directory=None):
        self.task = task
        self.config = config
        self.progress = progress
        self.completed = completed
        self.failed = failed
        self.is_current = is_current
        self.tools = require_tools(directory)
        self.stop = threading.Event()
        self.process = None
        self.lock = threading.Lock()
        self.tracks = {}
        self.kind = task.get('download_type', 'youtube')
        self.folder = Path(config.temp_dir) / self.kind / task['task_id']
        self.cookie_file = None
        self.info_file = None
        self.expected_duration = None

    def command(self):
        height = int(self.task.get('video_height') or 720)
        if height not in (480, 720, 1080):
            raise ValueError('不支持的清晰度')
        command = [str(self.tools / 'yt-dlp.exe'), '--ignore-config', '--no-playlist', '--no-cache-dir',
                   '--no-update', '--no-colors', '--newline', '--progress', '--progress-delta', '0.5',
                   '--match-filters', '!is_live',
                   '--continue', '--no-overwrites', '--socket-timeout', str(self.config.timeout),
                   '--retries', str(self.config.retry_times), '--fragment-retries', str(self.config.retry_times),
                   '--js-runtimes', 'node:' + str(self.tools / 'node.exe'),
                   '--ffmpeg-location', str(self.tools), '--merge-output-format', 'mp4',
                   '--format', f'bv[height<={height}][vcodec^=avc1]+ba[ext=m4a]/b[height<={height}][ext=mp4]',
                   '--output', str(self.folder / 'video.%(ext)s'),
                   '--progress-template', 'download:__LW_PROGRESS__{"downloaded":%(progress.downloaded_bytes|0)j,"total":%(progress.total_bytes,progress.total_bytes_estimate|0)j,"speed":%(progress.speed|0)j,"file":%(progress.filename)j}']
        if self.config.speed_limit:
            command += ['--limit-rate', str(self.config.speed_limit)]
        proxies = self.config.proxies or {}
        if proxies.get('https') or proxies.get('http'):
            command += ['--proxy', proxies.get('https') or proxies['http']]
        if self.kind in STREAM_KINDS:
            # Native fragment download preserves resume and scoped cookies; never skip lost fragments.
            command[command.index('--format') + 1] = (
                f'bv[height<=?{height}]+ba/b[height<=?{height}]/bv[height<=?{height}]')
            command += ['--force-generic-extractor', '--downloader', 'm3u8:native',
                        '--downloader', 'dash:native', '--abort-on-unavailable-fragments',
                        '--remux-video', 'mp4']
        if self.kind in CONTEXT_KINDS:
            context = video_request_context(self.task['url'], self.kind,
                unprotect(self.task['browser_context']) if self.task.get('browser_context') else None)
            for key, value in context['headers'].items():
                command += ['--add-header', f'{key}:{value}']
            if self.cookie_file:
                command += ['--cookies', self.cookie_file]
        if self.kind == 'douyin':
            if not self.info_file:
                raise ValueError('抖音视频信息尚未准备好')
            # The browser already obtained signed formats. Do not repeat an unsigned page API request.
            return command + ['--load-info-json', self.info_file]
        return command + ['--', video_task_url(self.task['url'], self.kind)]

    def prepare_cookies(self):
        if self.kind not in CONTEXT_KINDS or not self.task.get('browser_context'):
            return
        context = video_request_context(self.task['url'], self.kind, unprotect(self.task['browser_context']))
        if not context['cookies']:
            return
        # The database stays DPAPI-encrypted; only the running child gets a temporary cookie jar.
        stream = tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', suffix='.cookies', delete=False)
        self.cookie_file = stream.name
        with stream:
            stream.write('# Netscape HTTP Cookie File\n')
            for cookie in context['cookies']:
                stream.write('\t'.join((cookie['domain'], 'FALSE' if cookie['hostOnly'] else 'TRUE',
                    cookie['path'], 'TRUE' if cookie['secure'] else 'FALSE', '0', cookie['name'], cookie['value'])) + '\n')

    def prepare_video_info(self):
        if self.kind != 'douyin':
            return
        context = video_request_context(self.task['url'], self.kind,
            unprotect(self.task['browser_context']) if self.task.get('browser_context') else None)
        self.expected_duration = context['video']['duration']
        stream = tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', suffix='.info.json', delete=False)
        self.info_file = stream.name
        with stream:
            json.dump(context['video'], stream)

    def active(self):
        return not self.stop.is_set() and self.is_current()

    def consume_progress(self, line):
        prefix = '__LW_PROGRESS__'
        if not line.startswith(prefix) or not self.active():
            return
        data = json.loads(line[len(prefix):])
        downloaded = max(0, int(data.get('downloaded') or 0))
        total = max(downloaded, int(data.get('total') or 0))
        self.tracks[str(data.get('file'))] = (downloaded, total)
        done = sum(item[0] for item in self.tracks.values())
        size = max(done + 1, sum(item[1] for item in self.tracks.values()))
        self.progress(done, size, max(0, float(data.get('speed') or 0)))

    def run(self):
        errors = []
        try:
            self.folder.mkdir(parents=True, exist_ok=True)
            with self.lock:
                if not self.active():
                    return
                self.prepare_cookies()
                self.prepare_video_info()
                self.process = subprocess.Popen(self.command(), stdin=subprocess.DEVNULL,
                    stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding='utf-8', errors='replace',
                    creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
            for line in self.process.stdout:
                if line.startswith('__LW_PROGRESS__'):
                    try:
                        self.consume_progress(line)
                    except (ValueError, TypeError):
                        pass
                else:
                    errors.append(line[:2000])
                    errors = errors[-12:]
            code = self.process.wait()
            if not self.active():
                return
            if code:
                raise ValueError(failure_message(errors, self.kind))
            output = self.folder / 'video.mp4'
            if not output.is_file() or output.stat().st_size == 0:
                raise ValueError('视频引擎未生成完整的 MP4 文件')
            probe = subprocess.run([str(self.tools / 'ffprobe.exe'), '-v', 'error', '-show_entries',
                'stream=codec_type:format=duration', '-of', 'json', str(output)], capture_output=True,
                text=True, encoding='utf-8', timeout=30,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
            metadata = json.loads(probe.stdout)
            types = {stream.get('codec_type') for stream in metadata.get('streams', [])}
            required = {'video'} if self.kind in ('x', *STREAM_KINDS) else {'video', 'audio'}
            duration = float(metadata.get('format', {}).get('duration', 0))
            if probe.returncode or not required <= types or not math.isfinite(duration) or duration <= 0:
                raise ValueError('合并结果缺少视频、音频或有效时长')
            if self.expected_duration and abs(duration - self.expected_duration) > max(2, self.expected_duration * 0.02):
                raise ValueError('下载结果时长与完整视频不符，未发布不完整文件')
            if not self.active():
                return
            destination = Path(self.task['save_path'])
            destination.parent.mkdir(parents=True, exist_ok=True)
            if destination.exists():
                raise ValueError('保存位置已有同名文件，未覆盖原文件')
            shutil.move(str(output), str(destination))
            if self.active():
                self.completed(destination.stat().st_size)
        except Exception as error:
            if self.active():
                message = str(error) if isinstance(error, ValueError) else '视频下载失败，请检查网络、磁盘或下载组件'
                self.failed(message)
        finally:
            if self.process and self.process.stdout:
                self.process.stdout.close()
            for attribute in ('cookie_file', 'info_file'):
                filename = getattr(self, attribute)
                if filename:
                    Path(filename).unlink(missing_ok=True)
                    setattr(self, attribute, None)

    def cancel(self):
        self.stop.set()
        with self.lock:
            process = self.process
            if process and process.poll() is None:
                if os.name == 'nt':
                    subprocess.run([os.path.join(os.environ['SystemRoot'], 'System32', 'taskkill.exe'),
                                    '/PID', str(process.pid), '/T', '/F'], stdout=subprocess.DEVNULL,
                                   stderr=subprocess.DEVNULL, timeout=15, creationflags=subprocess.CREATE_NO_WINDOW)
                else:
                    process.terminate()

