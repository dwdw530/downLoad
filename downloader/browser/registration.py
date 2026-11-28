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
    if sys.platform == 'darwin':
        if not getattr(sys, 'frozen', False):
            raise RuntimeError('请从已安装的 daw下载器.app 连接浏览器')
        bundle = Path(sys.executable).resolve().parent.parent
        host = bundle / 'Resources/BrowserBridge'
        extension_dir = bundle / 'Resources/chrome-extension'
        extension = json.loads((extension_dir / 'manifest.json').read_text(encoding='utf-8'))
        manifest = {'name': HOST_NAME, 'description': 'daw 下载器', 'path': str(host),
                    'type': 'stdio', 'allowed_origins': [f'chrome-extension://{extension_id(extension)}/']}
        for browser in ('Google/Chrome', 'Microsoft Edge'):
            path = Path.home() / 'Library/Application Support' / browser / 'NativeMessagingHosts' / (HOST_NAME + '.json')
            if uninstall:
                path.unlink(missing_ok=True)
            else:
                import tempfile
                path.parent.mkdir(parents=True, exist_ok=True)
                with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=path.parent, delete=False) as stream:
                    json.dump(manifest, stream, ensure_ascii=True)
                os.replace(stream.name, path)
        return
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
    if not (root / 'daw下载器.exe').is_file():
        raise RuntimeError('Downloader EXE is missing')
    manifest = {'name': HOST_NAME, 'description': 'daw video download bridge',
                'path': str(Path(sys.executable).resolve()), 'type': 'stdio',
                'allowed_origins': [f'chrome-extension://{extension_id(extension)}/']}
    path = root / 'browser-native-host.json'
    path.write_text(json.dumps(manifest, ensure_ascii=True, indent=2), encoding='utf-8')
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, key) as registry:
        winreg.SetValueEx(registry, '', 0, winreg.REG_SZ, str(path.resolve()))
    print('Chrome bridge registered for this Windows user.')
    print('Load unpacked extension from:', root / 'chrome-extension')

