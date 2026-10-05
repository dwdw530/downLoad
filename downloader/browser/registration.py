"""Explicit, current-user-only Chrome native host registration."""
import base64
import hashlib
import json
import os
from pathlib import Path
import sys

from downloader.utils.config import get_app_root

HOST_NAME = 'com.laowang.downloader'


def extension_id(manifest):
    digest = hashlib.sha256(base64.b64decode(manifest['key'], validate=True)).hexdigest()[:32]
    return ''.join(chr(ord('a') + int(character, 16)) for character in digest)


def register(uninstall=False):
    if os.name != 'nt':
        raise RuntimeError('Only Windows is supported')
    import winreg
    key = r'Software\Google\Chrome\NativeMessagingHosts' + '\\' + HOST_NAME
    if uninstall:
        try:
            winreg.DeleteKey(winreg.HKEY_CURRENT_USER, key)
        except FileNotFoundError:
            pass
        print('Chrome bridge registration removed. Download data was not changed.')
        return
    root = Path(get_app_root())
    if not getattr(sys, 'frozen', False):
        raise RuntimeError('Run BrowserBridge.exe --install from the release directory')
    extension = json.loads((root / 'chrome-extension' / 'manifest.json').read_text(encoding='utf-8'))
    if not (root / '老王下载器.exe').is_file():
        raise RuntimeError('Downloader EXE is missing')
    manifest = {'name': HOST_NAME, 'description': 'LaoWang video download bridge',
                'path': str(Path(sys.executable).resolve()), 'type': 'stdio',
                'allowed_origins': [f'chrome-extension://{extension_id(extension)}/']}
    path = root / 'browser-native-host.json'
    path.write_text(json.dumps(manifest, ensure_ascii=True, indent=2), encoding='utf-8')
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, key) as registry:
        winreg.SetValueEx(registry, '', 0, winreg.REG_SZ, str(path.resolve()))
    print('Chrome bridge registered for this Windows user.')
    print('Load unpacked extension from:', root / 'chrome-extension')

