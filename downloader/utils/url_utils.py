# -*- coding: utf-8 -*-
"""
URL工具模块（剪贴板监视用）
老王说：链接判断要保守，拿不准就不弹，别骚扰用户！
"""
from urllib.parse import urlsplit


def normalize_host(host: str) -> str:
    """
    归一化域名：去空白、去scheme/路径/端口、转小写、去开头www.、去首尾点
    Args:
        host: 域名或完整URL（容错处理）
    Returns:
        归一化后的域名；无效返回空字符串
    """
    if not host:
        return ""
    text = str(host).strip().lower()
    if not text:
        return ""
    # 容错：用户可能把完整URL粘进忽略站点输入框
    if "://" in text:
        text = text.split("://", 1)[1]
    # 去掉路径、端口、查询串
    text = text.split("/", 1)[0].split("?", 1)[0].split(":", 1)[0]
    text = text.strip(".")
    if text.startswith("www."):
        text = text[4:]
    return text


def extract_clipboard_url(text: str) -> str:
    """
    从剪贴板文本提取可下载链接：剥去首尾空白后必须是单行 http(s):// 链接
    Args:
        text: 剪贴板原始文本
    Returns:
        去首尾空白后的链接；不满足条件返回空字符串
    """
    if not text:
        return ""
    stripped = str(text).strip()
    if not stripped or "\n" in stripped or "\r" in stripped:
        return ""
    lowered = stripped.lower()
    if not (lowered.startswith("http://") or lowered.startswith("https://")):
        return ""
    try:
        parts = urlsplit(stripped)
    except ValueError:
        return ""
    if not parts.netloc:
        return ""
    return stripped


def host_from_url(url: str) -> str:
    """
    从URL提取归一化域名（去掉www.等首段，返回可匹配忽略列表的主域部分之外的真实主机名）
    Returns:
        归一化域名；解析失败返回空字符串
    """
    if not url:
        return ""
    try:
        netloc = urlsplit(url).netloc
    except ValueError:
        return ""
    if not netloc:
        return ""
    text = netloc.lower()
    # 去掉端口和用户信息（user:pass@host:port）
    if "@" in text:
        text = text.rsplit("@", 1)[1]
    text = text.split(":", 1)[0]
    # 首段不是 www 就保留完整主机名（dl.example.com 不能归成 example.com，
    # 否则忽略站点会把无关子域也一起忽略）；匹配时用后缀规则覆盖子域名
    if text.startswith("www."):
        text = text[4:]
    return text.strip(".")


def host_is_ignored(url: str, ignore_hosts) -> bool:
    """
    判断链接域名是否命中忽略列表（精确匹配或子域名匹配）
    忽略 example.com 时，m.example.com、www.example.com 一并忽略
    """
    hosts = [normalize_host(item) for item in (ignore_hosts or [])]
    hosts = [item for item in hosts if item]
    if not hosts:
        return False
    host = host_from_url(url)
    if not host:
        return False
    return any(host == item or host.endswith("." + item) for item in hosts)
