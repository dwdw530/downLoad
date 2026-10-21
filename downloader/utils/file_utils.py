# -*- coding: utf-8 -*-
"""
文件工具模块
老王说:  文件操作要稳，一个不小心就炸了！
"""
import errno
import logging
import re
import os
import shutil
import hashlib
import tempfile
import threading
from typing import List, Callable, Optional
from urllib.parse import urlparse, unquote


logger = logging.getLogger(__name__)


# 左侧分类树的文件类型分组：分类键 -> 中文标签
FILE_CATEGORY_LABELS = {
    'video': '视频',
    'audio': '音频',
    'document': '文档',
    'archive': '压缩包',
    'program': '程序',
    'other': '其他',
}

# 按扩展名归类；未知扩展名统一归 'other'
FILE_CATEGORY_SUFFIXES = {
    'video': {'.mp4', '.mkv', '.avi', '.mov', '.wmv', '.flv', '.webm', '.m4v', '.ts', '.mpg', '.mpeg', '.rmvb', '.3gp'},
    'audio': {'.mp3', '.flac', '.wav', '.aac', '.m4a', '.ogg', '.wma', '.ape'},
    'document': {'.pdf', '.doc', '.docx', '.xls', '.xlsx', '.ppt', '.pptx', '.txt', '.md', '.epub', '.mobi', '.csv', '.rtf'},
    'archive': {'.zip', '.rar', '.7z', '.tar', '.gz', '.bz2', '.xz', '.iso', '.tgz'},
    'program': {'.exe', '.msi', '.apk', '.dmg', '.deb', '.rpm', '.appx'},
}


def guess_file_category(filename: str) -> str:
    """
    按扩展名猜测文件分类（左侧分类树用）
    Args:
        filename: 文件名
    Returns:
        FILE_CATEGORY_LABELS 中的分类键，未知或没有扩展名返回 'other'
    老王说：分类只认扩展名，猜不出来就当“其他”，别硬塞个类型骗人！
    """
    suffix = os.path.splitext(filename or '')[1].lower()
    if not suffix:
        return 'other'
    for category, suffixes in FILE_CATEGORY_SUFFIXES.items():
        if suffix in suffixes:
            return category
    return 'other'


def resolve_download_directory(base_dir: str, filename: str, auto_classify: bool = False) -> str:
    """按文件类型决定最终保存目录（纯函数，不建目录、不发请求、不查库）

    老王说：分类目录只从固定表里取，别拿 URL 或文件名片段去拼路径！

    Args:
        base_dir: 默认下载根目录
        filename: 最终有效文件名（显式名/CD/重定向/原地址取名后的结果）
        auto_classify: 是否按类型建立子目录
    Returns:
        关闭时原样返回根目录；开启时返回 根目录/中文分类标签
    """
    base = base_dir or ''
    if not auto_classify:
        return base
    category = guess_file_category(filename)
    label = FILE_CATEGORY_LABELS.get(category) or FILE_CATEGORY_LABELS['other']
    if not base:
        return label
    return os.path.join(base, label)


# 空间预检余量：5% + 最多 64 MiB（小文件不会因为固定 64 MiB 余量被无故拦住）
FREE_SPACE_MARGIN_RATIO = 1.05
FREE_SPACE_MARGIN_MAX_BYTES = 64 * 1024 * 1024


def format_size(size_bytes: int) -> str:
    """
    格式化文件大小
    Args:
        size_bytes: 字节数
    Returns:
        格式化后的字符串，如 "1.23 MB"
    """
    if size_bytes < 1024:
        return f"{size_bytes} B"
    elif size_bytes < 1024 * 1024:
        return f"{size_bytes / 1024:.2f} KB"
    elif size_bytes < 1024 * 1024 * 1024:
        return f"{size_bytes / (1024 * 1024):.2f} MB"
    else:
        return f"{size_bytes / (1024 * 1024 * 1024):.2f} GB"


def format_speed(speed_bytes_per_sec: float) -> str:
    """
    格式化速度
    Args:
        speed_bytes_per_sec: 字节/秒
    Returns:
        格式化后的字符串，如 "1.23 MB/s"
    """
    if speed_bytes_per_sec < 1024:
        return f"{speed_bytes_per_sec:.0f} B/s"
    elif speed_bytes_per_sec < 1024 * 1024:
        return f"{speed_bytes_per_sec / 1024:.2f} KB/s"
    else:
        return f"{speed_bytes_per_sec / (1024 * 1024):.2f} MB/s"


def format_remaining(remaining_bytes: int, speed: float) -> str:
    """
    格式化剩余时间
    Args:
        remaining_bytes: 剩余字节数
        speed: 当前速度（字节/秒）
    Returns:
        "mm:ss" / "h:mm:ss" / "超过 1 天"；未知或无法估算时返回 "--"
    老王说：速度没拿到就别硬编一个剩余时间坑用户！
    """
    try:
        remaining = int(remaining_bytes or 0)
        rate = float(speed or 0)
    except (TypeError, ValueError):
        return "--"

    if remaining <= 0 or rate <= 0:
        return "--"

    seconds = remaining / rate
    if seconds > 24 * 3600:
        return "超过 1 天"

    total_seconds = int(seconds + 0.5)
    hours, rest = divmod(total_seconds, 3600)
    minutes, secs = divmod(rest, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes:02d}:{secs:02d}"


def format_progress_texts(downloaded_size: int, total_size: int, speed: float, status: str) -> tuple:
    """
    生成任务卡片与详情窗共用的进度文案
    Args:
        downloaded_size: 已下载字节数
        total_size: 总字节数（<=0 视为未知）
        speed: 当前速度（字节/秒）
        status: 任务状态（仅 downloading 估算剩余时间）
    Returns:
        (大小文案, 剩余时间文案, 速度文案)
    """
    total_text = format_size(total_size) if total_size > 0 else "未知"
    size_text = f"已下载 {format_size(downloaded_size)} / {total_text}"
    if status == 'downloading':
        remaining = max(0, total_size - downloaded_size) if total_size > 0 else 0
        eta_text = f"剩余 {format_remaining(remaining, speed)}"
    else:
        eta_text = "剩余 --"
    return size_text, eta_text, format_speed(speed)


def get_filename_from_url(url: str) -> str:
    """
    从URL中提取文件名
    Args:
        url: 下载链接
    Returns:
        文件名
    """
    parsed = urlparse(url)
    filename = unquote(os.path.basename(parsed.path))
    if not filename or filename == '/':
        filename = 'download'
    return validate_filename(filename)


def parse_content_disposition(value: str) -> Optional[str]:
    """从 Content-Disposition 里取文件名，优先 RFC 5987 的 filename*。

    老王说：服务器说叫什么才叫什么，但路径和保留名一个都别想带进来！

    只返回单个路径段（已过 validate_filename）；拿不到可用名字返回 None。
    """
    if not value:
        return None
    header = str(value)

    extended = re.search(r"filename\*\s*=\s*([^;]+)", header, re.IGNORECASE)
    if extended:
        raw = extended.group(1).strip().strip('"')
        # 形如 UTF-8''%E4%B8%AD%E6%96%87.zip
        parts = raw.split("'", 2)
        if len(parts) == 3 and parts[2]:
            charset = parts[0] or 'utf-8'
            try:
                decoded = unquote(parts[2], encoding=charset, errors='strict')
            except (LookupError, UnicodeDecodeError):
                decoded = ''
            name = _single_path_segment(decoded)
            if name:
                return name

    plain = re.search(r'filename\s*=\s*(?:"([^"]*)"|([^;]+))', header, re.IGNORECASE)
    if plain:
        raw = plain.group(1) if plain.group(1) is not None else plain.group(2).strip()
        return _single_path_segment(raw)
    return None


def _single_path_segment(value: str) -> Optional[str]:
    """把服务器给的名字收敛成一个安全文件名；不合法返回 None"""
    if not value:
        return None
    # 反斜杠也是分隔符，先统一，再只留最后一段
    name = os.path.basename(str(value).replace('\\', '/')).strip().strip('"')
    if not name:
        return None
    if len(name) > 120:
        base, suffix = os.path.splitext(name)
        suffix = suffix[:16]
        name = (base[:max(1, 120 - len(suffix))] or 'download') + suffix
    try:
        return validate_filename(name)
    except ValueError:
        return None


def validate_filename(filename: str) -> str:
    """拒绝解码后的路径、Windows 设备名与 ADS，文件名只能是单个路径段。"""
    if (not filename or filename in ('.', '..') or filename != filename.rstrip(' .')
            or any(ord(c) < 32 or c in '<>:"/\\|?*' for c in filename)
            or re.fullmatch(r'(CON|PRN|AUX|NUL|COM[1-9¹²³]|LPT[1-9¹²³])',
                            filename.split('.')[0].rstrip(' '), re.IGNORECASE)):
        raise ValueError('文件名无效：不能包含路径、保留字符或 Windows 设备名')
    return filename


def available_download_path(path: str, occupied=()) -> str:
    """在选定目录中分配未占用名称；调用方负责串行化任务落库。"""
    directory = os.path.realpath(os.path.dirname(os.path.abspath(path)))
    filename = validate_filename(os.path.basename(path))
    base, suffix = os.path.splitext(filename)
    occupied = {os.path.normcase(os.path.realpath(item)) for item in occupied}
    for index in range(10000):
        name = filename if index == 0 else f'{base} ({index}){suffix}'
        target = os.path.join(directory, name)
        if os.path.normcase(os.path.realpath(target)) not in occupied and not os.path.lexists(target):
            return target
    raise ValueError('同名文件过多，无法分配保存路径')


def publish_download(source: str, destination: str, is_current=lambda: True) -> bool:
    """Windows 下同卷改名、跨卷才复制；最终改名不覆盖已有目标。

    跨卷先写目标目录的独立临时文件，写完后才发布正式名称。
    暂停/取消时保留源文件；这里只清理本次创建的临时文件。
    """
    def rename_if_current(src, dst):
        if not is_current():
            raise InterruptedError('下载会话已停止')
        # Windows 的 rename 在目标已存在时失败，不使用会覆盖的 replace/move。
        os.rename(src, dst)
        if not is_current():
            # 停止恰好发生在改名期间，退回原临时路径供下次续传。
            os.rename(dst, src)
            raise InterruptedError('下载会话已停止')

    if not is_current():
        raise InterruptedError('下载会话已停止')
    directory = os.path.dirname(os.path.abspath(destination))
    ensure_dir(directory)
    try:
        rename_if_current(source, destination)
        return True
    except OSError as error:
        # 仅跨卷错误允许复制；权限不足、同名冲突等直接交给调用方处理。
        if error.errno != errno.EXDEV and getattr(error, 'winerror', None) != 17:
            raise

    staged = None
    try:
        with tempfile.NamedTemporaryFile(mode='wb', prefix='.daw-publish-', suffix='.tmp',
                                         dir=directory, delete=False) as outfile:
            staged = outfile.name
            with open(source, 'rb') as infile:
                while True:
                    if not is_current():
                        raise InterruptedError('下载会话已停止')
                    data = infile.read(1024 * 1024)
                    if not data:
                        break
                    outfile.write(data)
        rename_if_current(staged, destination)
        try:
            os.remove(source)
        except OSError as error:
            # 目标已经完整发布，源文件清理失败不应把成功任务重新标成失败。
            logger.warning('已保存文件，但源临时文件清理失败: %s', error)
        return True
    finally:
        if staged and os.path.exists(staged):
            os.remove(staged)


def _existing_ancestor(path: str) -> str:
    """找到最近一个已存在的上级目录（新建任务时目标目录可能还不存在）"""
    current = os.path.abspath(path or '.')
    while not os.path.exists(current):
        parent = os.path.dirname(current)
        if parent == current:
            break
        current = parent
    return current


def check_free_space(requirements) -> tuple:
    """检查多个路径所在卷的剩余空间。

    老王说：大文件下到 90% 才发现盘满了，那叫耍流氓！

    Args:
        requirements: [(目录, 需求字节, 标签), ...]
    Returns:
        (True, None) 或 (False, 给用户看的说明)
    同一盘符的需求会合并（分块和合并副本同盘时叠加）；
    拿不到容量的路径（网络盘、权限不足、盘符消失）按“未知”放行，不阻断下载。
    """
    needed = {}
    labels = {}
    probes = {}
    for path, need_bytes, label in requirements or ():
        try:
            need_bytes = int(need_bytes or 0)
        except (TypeError, ValueError):
            continue
        if need_bytes <= 0 or not path:
            continue
        absolute = _existing_ancestor(path)
        key = os.path.splitdrive(absolute)[0].lower() or absolute
        needed[key] = needed.get(key, 0) + need_bytes
        probes.setdefault(key, absolute)
        names = labels.setdefault(key, [])
        if label and label not in names:
            names.append(label)

    for key, need_bytes in needed.items():
        label = '与'.join(labels.get(key) or ['下载'])
        try:
            free = shutil.disk_usage(probes[key]).free
        except OSError as error:
            # 网络盘、权限、盘符变化都读不到容量，不能因为新检查把下载拦死
            logger.debug('无法读取剩余空间，跳过检查: %s, err=%s', label, error)
            continue
        margin = min(FREE_SPACE_MARGIN_MAX_BYTES, max(1024, int(need_bytes * 0.05)))
        required = int(need_bytes * FREE_SPACE_MARGIN_RATIO) + margin
        if free < required:
            return False, (f'{label}空间不足：需要 {format_size(required)}，'
                           f'剩余 {format_size(free)}，还差 {format_size(required - free)}')
    return True, None


def ensure_dir(directory: str):
    """确保目录存在"""
    if not os.path.exists(directory):
        os.makedirs(directory, exist_ok=True)


def merge_chunks(chunk_files: List[str], output_file: str, delete_chunks: bool = True,
                 is_current=lambda: True) -> bool:
    """
    合并分块文件
    Args:
        chunk_files: 分块文件路径列表（按顺序）
        output_file: 输出文件路径
        delete_chunks: 是否删除临时分块文件
    Returns:
        True表示成功，False表示失败
    """
    try:
        # 确保输出目录存在
        output_dir = os.path.dirname(output_file)
        if output_dir:
            ensure_dir(output_dir)

        # 合并文件
        with open(output_file, 'wb') as outfile:
            for chunk_file in chunk_files:
                if not os.path.exists(chunk_file):
                    logger.error("分块文件不存在: %s", chunk_file)
                    return False
                with open(chunk_file, 'rb') as infile:
                    while True:
                        if not is_current():
                            return False
                        data = infile.read(1024 * 1024)  # 每次读1MB
                        if not data:
                            break
                        outfile.write(data)

        # 删除临时文件
        if not is_current():
            return False
        if delete_chunks:
            for chunk_file in chunk_files:
                try:
                    os.remove(chunk_file)
                except Exception as e:
                    logger.warning("删除临时文件失败: %s, %s", chunk_file, e)

        return True
    except Exception as e:
        logger.error("合并文件失败: %s", e)
        return False


def delete_file(file_path: str) -> bool:
    """
    安全删除文件
    Args:
        file_path: 文件路径
    Returns:
        True表示成功，False表示失败
    """
    try:
        if os.path.exists(file_path):
            os.remove(file_path)
        return True
    except Exception as e:
        logger.error("删除文件失败: %s, %s", file_path, e)
        return False


def get_file_size(file_path: str) -> int:
    """
    获取文件大小
    Args:
        file_path: 文件路径
    Returns:
        文件大小（字节），文件不存在返回0
    """
    try:
        if os.path.exists(file_path):
            return os.path.getsize(file_path)
        return 0
    except Exception:
        return 0


def calculate_file_hash(file_path: str,
                        hash_type: str = "md5",
                        progress_callback: Optional[Callable[[int, int], None]] = None) -> str:
    """
    计算文件哈希值
    Args:
        file_path: 文件路径
        hash_type: 哈希类型，支持 md5/sha256
        progress_callback: 进度回调 callback(processed_bytes, total_bytes)
    Returns:
        哈希值字符串（小写16进制），失败返回空字符串

    老王说：大文件哈希计算得分块读，不然内存会爆炸！
    """
    hash_type = hash_type.lower()
    if hash_type == "md5":
        hasher = hashlib.md5()
    elif hash_type == "sha256":
        hasher = hashlib.sha256()
    else:
        logger.error("不支持的哈希类型: %s", hash_type)
        return ""

    try:
        total_size = os.path.getsize(file_path)
        processed = 0
        chunk_size = 8 * 1024 * 1024  # 8MB一块，平衡速度和内存

        with open(file_path, 'rb') as f:
            while True:
                data = f.read(chunk_size)
                if not data:
                    break
                hasher.update(data)
                processed += len(data)
                if progress_callback:
                    progress_callback(processed, total_size)

        return hasher.hexdigest().lower()
    except Exception as e:
        logger.error("计算哈希失败: %s", e)
        return ""


def verify_file_hash(file_path: str, expected_hash: str, hash_type: str = "md5") -> bool:
    """
    验证文件哈希
    Args:
        file_path: 文件路径
        expected_hash: 预期哈希值
        hash_type: 哈希类型
    Returns:
        True表示匹配，False表示不匹配
    """
    actual = calculate_file_hash(file_path, hash_type)
    if not actual:
        return False
    return actual == expected_hash.lower()


class FileLock:
    """文件锁（用于多线程安全写入）"""

    def __init__(self):
        self._lock = threading.Lock()

    def __enter__(self):
        self._lock.acquire()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self._lock.release()
