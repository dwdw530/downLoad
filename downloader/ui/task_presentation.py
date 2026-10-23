# -*- coding: utf-8 -*-
"""任务展示纯函数：安全失败原因与卡片/详情共用的进度值。

老王说：原始异常里啥都有（URL、Cookie、签名、路径），界面只能显示认得的
固定中文，认不出来就给兜底，绝不能把原文直接怼到控件里！

本模块不导入 Tk、不查库、不联网，只做文本识别与数值换算。
"""
import re
from typing import Tuple

from downloader.utils.file_utils import parse_byte_count


# 明显敏感片段：先整体抹掉，再做类别识别。识别结果本身是固定中文，
# 不从原文截取任何片段，因此即使正则漏掉某个值也不会回显到界面。
_URL_PATTERN = re.compile(r'[a-zA-Z][a-zA-Z0-9+.-]*://[^\s<>"\']+')
_SENSITIVE_HEADER_PATTERN = re.compile(
    r'(?i)\b(?:set-cookie|cookie|proxy-authorization|authorization|'
    r'access[_-]?token|refresh[_-]?token|token|signature|sig|x-signature|'
    r'proxy-connection)\b\s*[:=]\s*[^\r\n]*')
_CREDENTIAL_PATTERN = re.compile(
    r'(?i)\b(?:bearer|basic|digest|token|secret|password|passwd|pwd)\b\s+\S+')
_WINDOWS_PATH_PATTERN = re.compile(r'(?i)\b[a-z]:[\\/][^\s<>"\']*')
_POSIX_PATH_PATTERN = re.compile(r'(?<![\w.])/(?:home|users|root|tmp|var|mnt|opt)/[^\s<>"\']*')
_PRIVATE_ADDRESS_PATTERN = re.compile(
    r'\b(?:localhost|127(?:\.\d{1,3}){3}|10(?:\.\d{1,3}){3}|'
    r'192\.168(?:\.\d{1,3}){2}|172\.(?:1[6-9]|2\d|3[01])(?:\.\d{1,3}){2}|'
    r'\[?::1\]?)\b', re.IGNORECASE)
_CONTROL_PATTERN = re.compile(r'[\x00-\x1f\x7f]+')

_HTTP_STATUS_PATTERN = re.compile(r'HTTP\s*(\d{3})(?!\d)', re.IGNORECASE)


def sanitize_problem_text(message: str) -> str:
    """把错误文案里的地址、凭据和路径整体抹掉（只用于识别，不回显原文）。"""
    text = str(message or '')
    text = _CONTROL_PATTERN.sub(' ', text)
    text = _URL_PATTERN.sub(' ', text)
    text = _SENSITIVE_HEADER_PATTERN.sub(' ', text)
    text = _CREDENTIAL_PATTERN.sub(' ', text)
    text = _WINDOWS_PATH_PATTERN.sub(' ', text)
    text = _POSIX_PATH_PATTERN.sub(' ', text)
    text = _PRIVATE_ADDRESS_PATTERN.sub(' ', text)
    return text


def _http_status(text: str):
    match = _HTTP_STATUS_PATTERN.search(text)
    if not match:
        return None
    try:
        return int(match.group(1))
    except ValueError:
        return None


def _http_problem(status_code: int):
    if status_code in (401, 403):
        return ('服务器拒绝访问（HTTP %d）' % status_code,
                '链接可能失效或需要授权；浏览器任务可刷新原页后重新添加。')
    if status_code in (404, 410):
        return ('资源不存在或已移除（HTTP %d）' % status_code,
                '检查来源链接，获取可用地址后重新添加。')
    if status_code == 429:
        return ('请求过于频繁（HTTP 429）',
                '等待后重试，可减少同时下载数或线程数。')
    if status_code >= 500:
        return ('服务器暂时出错（HTTP %d）' % status_code,
                '稍后重试；不建议连续快速点击。')
    if status_code >= 400:
        return ('服务器拒绝了请求（HTTP %d）' % status_code,
                '检查来源链接是否需要授权，或稍后重试。')
    return None


def _classify(text: str, raw_status_code=None):
    """按明确证据识别失败类别，返回 (卡片原因, 详情建议)。"""
    if ('服务器要求等待时间超出本次重试上限' in text
            or '服务器要求较长等待' in text):
        return ('服务器要求较长等待',
                '本次自动重试已结束，下载数据已保留；请稍后手动重试。')
    if ('自动重试已结束' in text or '本次重试已结束' in text
            or '重试次数已用尽' in text):
        return ('自动重试已结束',
                '已下载的数据已保留，可稍后手动重试。')

    status_code = raw_status_code or _http_status(text)
    http_problem = _http_problem(status_code) if status_code else None
    if http_problem:
        return http_problem

    lowered = text.lower()
    if '版本已变化' in text or '修改时间已变化' in text:
        return ('服务器文件版本已变化',
                '重新开始时会按原身份校验安全处理，不能强行拼接旧断点。')
    if '校验失败' in text or '校验未通过' in text or '哈希' in text:
        return ('文件校验未通过',
                '检查预期校验值或来源，确认后重试。')
    if '空间不足' in text or '磁盘空间' in text:
        return ('磁盘空间不足',
                '释放相应磁盘空间后继续；现有续传身份校验仍然生效。')
    if '文件大小与预期不一致' in text:
        return ('文件大小与预期不一致',
                '删除不完整的临时数据后重新添加任务。')
    if '合并失败' in text:
        return ('文件合并失败',
                '检查磁盘空间、权限或文件占用后重试。')
    if '同名' in text or '已存在' in text:
        return ('保存位置已有同名文件',
                '检查保存位置，换一个文件名或目录后重试。')
    if ('权限' in text or 'permission denied' in lowered or 'access is denied' in lowered
            or '无法保存' in text or '拒绝写入' in text):
        return ('无法保存文件',
                '检查保存位置的权限或文件占用情况后重试。')
    if '超时' in text or 'timeout' in lowered or 'timed out' in lowered:
        return ('连接超时',
                '检查网络与代理后重试；目标地址不会在界面显示。')
    if ('连接中断' in text or '连接失败' in text or '网络' in text
            or 'connection' in lowered or 'network' in lowered
            or 'connect' in lowered or '断流' in text):
        return ('网络连接失败',
                '检查网络与代理后重试。')
    if 'yt-dlp' in lowered or '下载组件' in text:
        return ('下载组件出错',
                '检查下载组件与网络后重试。')
    return None


def describe_task_problem(status: str, message: str, download_type: str = 'file') -> Tuple[str, str]:
    """返回 (卡片原因, 详情建议)；无需显示时返回 ('', '')。

    failed / verify_failed 必有安全说明；paused 只有明确故障原因才显示，
    手动暂停、稍后下载和重启纠偏都不会出现错误区域。
    """
    status = status or 'pending'
    if status in ('pending', 'downloading', 'verifying', 'completed', 'cancelled'):
        return ('', '')

    text = sanitize_problem_text(message)
    # HTTP 状态码是明确证据；即使它和敏感头写在同一行，也只用数字生成固定文案。
    raw_status_code = _http_status(
        _URL_PATTERN.sub(' ', _CONTROL_PATTERN.sub(' ', str(message or ''))))

    if status == 'verify_failed':
        advice = '检查预期校验值或来源，确认后重试。'
        if '哈希' in text or '计算' in text:
            advice = '文件哈希计算失败，检查文件是否被占用后重试。'
        return ('文件校验未通过', advice)

    problem = _classify(text, raw_status_code)
    if status == 'paused':
        # 只有明确故障原因才显示；手动暂停/稍后下载/重启纠偏没有错误区域。
        if problem is None or (not text.strip()):
            return ('', '')
        return problem

    if status == 'failed':
        if problem is not None:
            return problem
        if not text.strip():
            return ('未记录具体失败原因',
                    '检查网络、磁盘或下载组件后重试。')
        return ('下载遇到异常',
                '未能确定具体原因，请检查网络、磁盘或下载组件后重试。')

    # 其他未知状态保守给兜底，不显示原始文本。
    if problem is not None:
        return problem
    return ('下载遇到异常',
            '未能确定具体原因，请检查网络、磁盘或下载组件后重试。')


def is_empty_file_completed(downloaded_size, total_size, status: str) -> bool:
    """只有 completed 且两个字节值都是有效非负整数并恰为 0 才算真实空文件完成。"""
    if status != 'completed':
        return False
    return (parse_byte_count(downloaded_size) == 0
            and parse_byte_count(total_size) == 0)


def format_task_progress(downloaded_size, total_size, status: str) -> Tuple[float, str]:
    """返回 (进度条 0..1, 百分比区域文本)，卡片与详情共用同一规则。

    非法值不得被 int() 截断成 0：下载值为非法时按 0 计比例但不伪造空文件；
    有效 total 不因 downloaded 转换失败而一起归零。
    """
    downloaded = parse_byte_count(downloaded_size)
    total = parse_byte_count(total_size)

    if is_empty_file_completed(downloaded, total, status):
        return 1.0, '已完成'
    if total is None or total <= 0:
        # 未知总量/非法总量不伪造百分比，也不做 indeterminate 动画。
        return 0.0, '--'

    ratio_bytes = downloaded if downloaded is not None else 0
    progress = ratio_bytes / total
    progress = min(1.0, max(0.0, progress))
    return progress, f'{progress * 100:.1f}%'
