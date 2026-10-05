"""YouTube page downloads through the upstream yt-dlp/FFmpeg toolchain."""
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import threading
from urllib.parse import parse_qs, urlsplit

from downloader.utils.config import get_app_root


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


def tools_directory():
    root = Path(get_app_root())
    return root / 'video-tools' if getattr(sys, 'frozen', False) else root / 'vendor' / 'video'


def require_tools(directory=None):
    directory = Path(directory or tools_directory())
    if not all((directory / name).is_file() and (directory / name).stat().st_size > 0
               for name in ('yt-dlp.exe', 'ffmpeg.exe', 'ffprobe.exe', 'node.exe')):
        raise ValueError('YouTube 下载组件缺失，请安装包含 video-tools 的完整更新包')
    return directory


def failure_message(lines):
    text = '\n'.join(lines).lower()
    if 'sign in' in text or 'not a bot' in text:
        return 'YouTube 要求登录或验证，此版本不会读取账号 Cookie'
    if 'private video' in text or 'video unavailable' in text:
        return '视频不可用、私有或当前地区无法访问'
    if '403' in text:
        return 'YouTube 拒绝下载（403），请检查网络或更新视频组件'
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
        self.folder = Path(config.temp_dir) / 'youtube' / task['task_id']

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
        return command + ['--', youtube_url(self.task['url'])]

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
                raise ValueError(failure_message(errors))
            output = self.folder / 'video.mp4'
            if not output.is_file() or output.stat().st_size == 0:
                raise ValueError('视频引擎未生成完整的 MP4 文件')
            probe = subprocess.run([str(self.tools / 'ffprobe.exe'), '-v', 'error', '-show_entries',
                'stream=codec_type:format=duration', '-of', 'json', str(output)], capture_output=True,
                text=True, encoding='utf-8', timeout=30,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
            metadata = json.loads(probe.stdout)
            types = {stream.get('codec_type') for stream in metadata.get('streams', [])}
            if probe.returncode or not {'video', 'audio'} <= types or float(metadata.get('format', {}).get('duration', 0)) <= 0:
                raise ValueError('合并结果缺少视频、音频或有效时长')
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

