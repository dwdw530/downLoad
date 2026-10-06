"""Chrome Native Messaging host; stdout is reserved for framed JSON."""
import json
import os
from pathlib import Path
import struct
import subprocess
import sys
import time

from downloader.browser.bridge import MAX_MESSAGE, call_desktop
from downloader.utils.config import get_app_root


def read_exact(stream, size):
    data = bytearray()
    while len(data) < size:
        part = stream.read(size - len(data))
        if not part:
            raise EOFError('Incomplete native message')
        data.extend(part)
    return bytes(data)


def read_message(stream):
    prefix = stream.read(1)
    if not prefix:
        return None
    size = struct.unpack('<I', prefix + read_exact(stream, 3))[0]
    if not 0 < size <= MAX_MESSAGE:
        raise ValueError('Native message too large')
    value = json.loads(read_exact(stream, size))
    if not isinstance(value, dict):
        raise ValueError('Expected a JSON object')
    return value


def write_message(stream, message):
    data = json.dumps(message, ensure_ascii=True).encode('utf-8')
    if len(data) > MAX_MESSAGE:
        raise ValueError('Native response too large')
    stream.write(struct.pack('<I', len(data)) + data)
    stream.flush()


def forward(message):
    action = message.get('action')
    if action not in ('ping', 'download'):
        return {'ok': False, 'error': '不支持的操作'}
    try:
        # Only an explicit video download may start the GUI.
        call_desktop({'action': 'ping'}, timeout=2)
    except (OSError, ValueError, ConnectionError):
        if action == 'ping':
            from downloader.core.youtube_downloader import require_tools
            try:
                require_tools()
                capabilities = ['youtube', 'x']
            except ValueError:
                capabilities = []
            return {'ok': False, 'error': '下载器未启动', 'installed': True, 'capabilities': capabilities}
        root = Path(get_app_root())
        command = ([str(root / 'daw下载器.exe')] if getattr(sys, 'frozen', False)
                   else [sys.executable, str(root / 'main.py')])
        subprocess.Popen(command, cwd=str(root), stdin=subprocess.DEVNULL,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                         creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
        for _ in range(120):
            time.sleep(0.25)
            try:
                call_desktop({'action': 'ping'}, timeout=1)
                break
            except (OSError, ValueError, ConnectionError):
                continue
        else:
            return {'ok': False, 'error': '下载器启动超时，请手动打开后重试'}
    return call_desktop(message)


def main():
    if '--install' in sys.argv or '--uninstall' in sys.argv:
        from downloader.browser.registration import register
        register(uninstall='--uninstall' in sys.argv)
        return
    if os.name == 'nt':
        import msvcrt
        msvcrt.setmode(sys.stdin.fileno(), os.O_BINARY)
        msvcrt.setmode(sys.stdout.fileno(), os.O_BINARY)
    try:
        while True:
            message = read_message(sys.stdin.buffer)
            if message is None:
                return
            try:
                result = forward(message)
            except Exception:
                result = {'ok': False, 'error': '无法连接下载器，请检查本地桥接安装'}
            write_message(sys.stdout.buffer, result)
    except (EOFError, ValueError, OSError):
        return
