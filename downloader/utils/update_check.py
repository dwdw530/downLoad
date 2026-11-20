"""Check official releases without downloading or executing an update."""
from dataclasses import dataclass
import re

import requests

from downloader.version import APP_VERSION

RELEASES_URL = 'https://github.com/dwdw530/downLoad/releases'
LATEST_RELEASE_URL = (
    'https://api.github.com/repos/dwdw530/downLoad/releases/latest')


def version_tuple(value):
    if (not isinstance(value, str)
            or not re.fullmatch(r'v?\d+\.\d+\.\d+', value)):
        raise ValueError('无效的正式版本号')
    return tuple(int(part) for part in value.removeprefix('v').split('.'))


@dataclass(frozen=True)
class ReleaseInfo:
    version: str
    notes: str
    url: str
    newer: bool


def check_for_update(proxies=None):
    with requests.get(LATEST_RELEASE_URL, timeout=(5, 10), proxies=proxies,
                      headers={'Accept': 'application/vnd.github+json',
                               'User-Agent': f'dawDownloader/{APP_VERSION}'},
                      allow_redirects=False) as response:
        response.raise_for_status()
        if response.status_code != 200:
            raise ValueError('发布接口没有返回正式版本信息')
        data = response.json()
    if (not isinstance(data, dict) or data.get('draft')
            or data.get('prerelease')):
        raise ValueError('发布接口没有返回正式版本信息')
    version = data.get('tag_name')
    remote = version_tuple(version)
    notes = data.get('body') or '此版本未提供更新说明。'
    if not isinstance(notes, str):
        raise ValueError('更新说明格式无效')
    version = 'v' + '.'.join(str(part) for part in remote)
    return ReleaseInfo(version, notes, RELEASES_URL + '/tag/' + version,
                       remote > version_tuple(APP_VERSION))


def update_failure_message(error):
    """已收到 HTTP 响应时说明服务端原因，不误报为代理不通。"""
    if isinstance(error, requests.HTTPError) and error.response is not None:
        response = error.response
        limited = (response.status_code == 429 or (
            response.status_code == 403
            and response.headers.get('X-RateLimit-Remaining') == '0'))
        if limited:
            return '自动检查暂时受限，可在发布页查看最新版本。'
        return (f'检查失败：GitHub 返回 HTTP {response.status_code}，'
                '暂未获取版本信息。可打开官方下载页面查看。')
    return '检查失败：无法访问 GitHub，请检查网络或下载器的代理设置后重试。'
