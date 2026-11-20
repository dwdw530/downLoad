"""Validated media choices shared by task creation and the video worker."""
import json

VIDEO_HEIGHTS = (0, 480, 720, 1080, 1440, 2160)
MEDIA_SUFFIXES = {'.mp4', '.mkv', '.webm', '.m4a', '.mp3', '.opus', '.aac',
                  '.ogg', '.flac', '.wav', '.alac'}
SUBTITLE_LANGUAGES = {
    'none': '',
    'zh': 'zh.*,cmn.*,yue.*',
    'en': 'en.*',
    'zh,en': 'zh.*,cmn.*,yue.*,en.*',
    'all': 'all,-live_chat',
}
DEFAULT_MEDIA_OPTIONS = {
    'mode': 'video', 'audio_format': 'best',
    'profile': 'compatible', 'subtitles': 'none',
}


def normalize_media_options(value=None):
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (ValueError, TypeError):
            raise ValueError('媒体下载选项无效') from None
    if value is None:
        value = {}
    if not isinstance(value, dict) or set(value) - set(DEFAULT_MEDIA_OPTIONS):
        raise ValueError('媒体下载选项无效')
    options = {**DEFAULT_MEDIA_OPTIONS, **value}
    allowed = {
        'mode': ('video', 'audio'), 'audio_format': ('best', 'mp3'),
        'profile': ('compatible', 'source'),
        'subtitles': tuple(SUBTITLE_LANGUAGES),
    }
    if any(not isinstance(options[key], str) or options[key] not in values
           for key, values in allowed.items()):
        raise ValueError('不支持的媒体下载选项')
    return options


def media_extension(options):
    if options['mode'] == 'audio':
        return '.mp3' if options['audio_format'] == 'mp3' else '.m4a'
    return '.mkv' if options['profile'] == 'source' else '.mp4'


def describe_media_result(value):
    if not value:
        return ''
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError:
            return ''
    if not isinstance(value, dict):
        return ''
    parts = []
    width, height = value.get('width'), value.get('height')
    if type(width) is int and type(height) is int and width > 0 and height > 0:
        parts.append(f'实际 {width}×{height}')
    for key in ('video_codec', 'audio_codec'):
        codec = value.get(key)
        if isinstance(codec, str) and codec.isascii() and codec.isalnum():
            parts.append(codec)
    subtitles = value.get('subtitles')
    if isinstance(subtitles, list) and subtitles:
        parts.append(f'字幕 {len(subtitles)} 份，保存在媒体文件旁')
    if value.get('subtitle_missing') is True:
        parts.append('未获取到全部所选字幕；媒体文件已保存')
    return ' · '.join(parts)
