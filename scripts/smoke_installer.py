"""Verify the real Windows wizard, installed EXE, upgrades and safe uninstallation.

Uses a disposable workspace installation. Refuses an existing installed edition or
shortcuts, and restores the original browser registration in a finally block.
"""
import argparse
import ctypes
from ctypes import wintypes
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import winreg

from package_release import collect_release_files, sha256
from smoke_exe import app_windows, capture, enum_children, wait_for


ROOT = Path(__file__).resolve().parents[1]
APP_KEY = r'Software\dawDownloader'
UNINSTALL_KEY = r'Software\Microsoft\Windows\CurrentVersion\Uninstall\dawDownloader'
BRIDGE_KEY = r'Software\Google\Chrome\NativeMessagingHosts\com.laowang.downloader'
user32 = ctypes.WinDLL('user32', use_last_error=True)
user32.GetDlgItem.argtypes = [wintypes.HWND, ctypes.c_int]
user32.GetDlgItem.restype = wintypes.HWND
user32.GetDlgCtrlID.argtypes = [wintypes.HWND]
user32.IsWindowVisible.argtypes = [wintypes.HWND]
user32.IsWindowEnabled.argtypes = [wintypes.HWND]
user32.SendMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
user32.SendMessageW.restype = wintypes.LPARAM


def read_registry(path):
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, path) as key:
            children, count, _ = winreg.QueryInfoKey(key)
            assert children == 0, f'Unexpected registry subkeys: {path}'
            return [winreg.EnumValue(key, index) for index in range(count)]
    except FileNotFoundError:
        return None


def registry_value(path, name=''):
    return next((value for key, value, _ in read_registry(path) or [] if key == name), None)


def restore_bridge(snapshot):
    try:
        winreg.DeleteKey(winreg.HKEY_CURRENT_USER, BRIDGE_KEY)
    except FileNotFoundError:
        pass
    if snapshot is not None:
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, BRIDGE_KEY) as key:
            for name, value, kind in snapshot:
                winreg.SetValueEx(key, name, 0, kind, value)


def shell_folder(identifier):
    value = ctypes.create_unicode_buffer(32768)
    result = ctypes.windll.shell32.SHGetFolderPathW(None, identifier, None, 0, value)
    if result:
        raise OSError(f'SHGetFolderPathW failed: {result}')
    return Path(value.value)


def click_next(window):
    button = user32.GetDlgItem(window['hwnd'], 1)
    assert button and user32.IsWindowEnabled(button), 'Wizard next button is unavailable'
    user32.SendMessageW(button, 0x00F5, 0, 0)


def install_with_wizard(installer, app_dir, workspace):
    process = subprocess.Popen([str(installer)])
    window = wait_for(lambda: next((entry for entry in app_windows(installer)
                                   if entry['class'] == '#32770' and user32.IsWindowVisible(entry['hwnd'])), None))
    try:
        directory_edit = None
        for _ in range(5):
            controls = [entry for entry in enum_children(window['hwnd'])
                        if user32.IsWindowVisible(entry['hwnd'])]
            directory_edit = next((entry for entry in controls if entry['class'] == 'Edit'), None)
            if directory_edit:
                break
            click_next(window)
            time.sleep(0.4)
        assert directory_edit, 'The installer did not present a directory selection page'
        value = ctypes.create_unicode_buffer(str(app_dir))
        user32.SendMessageW(directory_edit['hwnd'], 0x000C, 0, ctypes.cast(value, ctypes.c_void_p).value)
        time.sleep(0.3)
        capture(window['hwnd'], workspace / 'directory-selection.png')
        (workspace / 'directory-controls.json').write_text(
            json.dumps(enum_children(window['hwnd']), ensure_ascii=False, indent=2), encoding='utf-8')
        click_next(window)

        def finished():
            controls = enum_children(window['hwnd'])
            return any('完成' in item['title'] and user32.GetDlgCtrlID(item['hwnd']) == 1
                       and user32.IsWindowEnabled(item['hwnd']) for item in controls)

        wait_for(finished, timeout=180)
        capture(window['hwnd'], workspace / 'installation-finished.png')
        click_next(window)
        assert process.wait(15) == 0
    except Exception:
        capture(window['hwnd'], workspace / 'installer-failure.png')
        print('Installer windows:', app_windows(installer), flush=True)
        raise


def install_silently(installer, app_dir):
    # NSIS requires /D to be the final argument, without internal quotes.
    command = subprocess.list2cmdline([str(installer), '/S']) + f' /D={app_dir}'
    result = subprocess.run(command, timeout=180)
    assert result.returncode == 0, f'Install failed: {result.returncode}'


def assert_in_use_is_rejected(installer, app_dir):
    kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel32.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                                   ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p]
    kernel32.CreateFileW.restype = wintypes.HANDLE
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    handle = kernel32.CreateFileW(str(app_dir / 'daw下载器.exe'), 0x80000000, 1, None, 3, 0x80, None)
    assert handle != ctypes.c_void_p(-1).value
    try:
        upgrade = subprocess.list2cmdline([str(installer), '/S']) + f' /D={app_dir}'
        assert subprocess.run(upgrade, timeout=180).returncode == 2
        # _?= keeps the uninstaller in this process so its rejection code can be checked.
        removal = subprocess.list2cmdline([str(app_dir / 'Uninstall.exe'), '/S']) + f' _?={app_dir}'
        assert subprocess.run(removal, timeout=60).returncode == 2
    finally:
        kernel32.CloseHandle(handle)
    assert registry_value(APP_KEY, 'InstallDir') == str(app_dir)
    assert (app_dir / 'daw下载器.exe').is_file() and (app_dir / 'BrowserBridge.exe').is_file()


def uninstall(app_dir):
    uninstaller = app_dir / 'Uninstall.exe'
    result = subprocess.run([str(uninstaller), '/S'], cwd=app_dir.parent, timeout=60)
    assert result.returncode == 0
    # NSIS may hand off to its temporary uninstaller before the launcher exits.
    wait_for(lambda: not uninstaller.exists() and not (app_dir / 'daw下载器.exe').exists(), timeout=90)


def assert_preserved(files):
    for path, digest in files.items():
        assert path.is_file() and sha256(path) == digest, f'User data changed: {path}'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--installer', type=Path, default=ROOT / 'dist' / 'daw-downloader-v0.3.0-windows-x64-setup.exe')
    args = parser.parse_args()
    installer = args.installer.resolve()
    desktop = shell_folder(0x10) / 'daw下载器.lnk'
    menu = shell_folder(0x02) / 'daw下载器'
    if any(read_registry(key) is not None for key in (APP_KEY, UNINSTALL_KEY)) or desktop.exists() or menu.exists():
        parser.error('An installed edition or its shortcuts already exist; use a clean Windows test user')
    browser_before = read_registry(BRIDGE_KEY)
    original_data = {path: sha256(path) for path in (ROOT / 'dist' / 'data').glob('*') if path.is_file()}
    output = ROOT / 'output' / 'installer-smoke'
    output.mkdir(parents=True, exist_ok=True)
    workspace = Path(tempfile.mkdtemp(prefix='run-', dir=output)).resolve()
    app_dir = workspace / '中文安装目录 with spaces' / 'daw下载器'
    expected = collect_release_files(ROOT, ROOT / 'dist')
    expected += [(ROOT / 'LICENSE', 'LICENSE'), (ROOT / 'installer' / '使用说明.txt', '使用说明.txt')]
    checks = []
    print(f'Artifacts: {workspace}', flush=True)
    try:
        install_with_wizard(installer, app_dir, workspace)
        assert registry_value(APP_KEY, 'InstallDir') == str(app_dir)
        assert registry_value(UNINSTALL_KEY, 'InstallLocation') == str(app_dir)
        assert registry_value(UNINSTALL_KEY, 'UninstallString') == f'"{app_dir / "Uninstall.exe"}"'
        assert desktop.is_file() and (menu / 'daw下载器.lnk').is_file() and (menu / '卸载 daw下载器.lnk').is_file()
        manifest = json.loads((app_dir / 'browser-native-host.json').read_text(encoding='utf-8'))
        assert manifest['path'] == str(app_dir / 'BrowserBridge.exe')
        assert registry_value(BRIDGE_KEY) == str(app_dir / 'browser-native-host.json')
        actual = {str(path.relative_to(app_dir)).replace('\\', '/') for path in app_dir.rglob('*') if path.is_file()}
        assert actual == {name for _, name in expected} | {'Uninstall.exe', 'browser-native-host.json'}, actual
        for source, relative in expected:
            assert sha256(source) == sha256(app_dir / relative), relative
        checks.append('Chinese wizard, custom path with spaces, complete allowlisted payload, shortcuts, uninstall entry and browser registration')
        print('PASS:', checks[-1], flush=True)

        with (workspace / 'installed-app-smoke.log').open('w', encoding='utf-8') as log:
            result = subprocess.run([sys.executable, '-B', str(ROOT / 'scripts' / 'smoke_browser_bridge.py'),
                                     '--app-dir', str(app_dir)],
                                    cwd=ROOT, stdout=log, stderr=subprocess.STDOUT, timeout=180)
        assert result.returncode == 0, f'Installed application smoke failed; see {workspace / "installed-app-smoke.log"}'
        checks.append('Installed EXE cold startup, authenticated video download, byte equality, deduplication, single instance and clean exit')
        print('PASS:', checks[-1], flush=True)

        for relative in ('temp/断点.part', 'downloads/保留的文件.bin', '用户自己的文件.txt'):
            path = app_dir / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b'installer-smoke-user-data\x00\x01\x02')
        preserved = {path: sha256(path) for path in app_dir.rglob('*') if path.is_file()
                     and str(path.relative_to(app_dir)).replace('\\', '/') not in actual}
        assert_in_use_is_rejected(installer, app_dir)
        assert_preserved(preserved)
        checks.append('In-use executables prevent upgrade and uninstall before files or registry entries are removed')
        print('PASS:', checks[-1], flush=True)
        install_silently(installer, app_dir)
        assert_preserved(preserved)
        checks.append('Upgrade in the same directory preserves configuration, database, downloads and partial files')
        print('PASS:', checks[-1], flush=True)

        other_manifest = str(workspace / 'another-installation' / 'browser-native-host.json')
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, BRIDGE_KEY) as key:
            winreg.SetValueEx(key, '', 0, winreg.REG_SZ, other_manifest)
        uninstall(app_dir)
        assert registry_value(BRIDGE_KEY) == other_manifest, 'Uninstall removed another installation browser registration'
        assert read_registry(APP_KEY) is None and read_registry(UNINSTALL_KEY) is None
        assert not desktop.exists() and not menu.exists()
        assert_preserved(preserved)
        assert {path for path in app_dir.rglob('*') if path.is_file()} == set(preserved)
        checks.append('Uninstall removes only program files and preserves another installation browser registration')
        print('PASS:', checks[-1], flush=True)

        install_silently(installer, app_dir)
        assert_preserved(preserved)
        assert registry_value(BRIDGE_KEY) == str(app_dir / 'browser-native-host.json')
        uninstall(app_dir)
        assert read_registry(BRIDGE_KEY) is None
        assert read_registry(APP_KEY) is None and read_registry(UNINSTALL_KEY) is None
        assert not desktop.exists() and not menu.exists()
        assert_preserved(preserved)
        assert_preserved(original_data)
        checks.append('Reinstall preserves data; normal uninstall removes its own browser registration')
        print('PASS:', checks[-1], flush=True)
        report = {'installer': str(installer), 'sha256': sha256(installer), 'install_directory': str(app_dir), 'checks': checks}
        (workspace / 'result.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    finally:
        try:
            if registry_value(APP_KEY, 'InstallDir') == str(app_dir) and (app_dir / 'Uninstall.exe').is_file():
                uninstall(app_dir)
        finally:
            restore_bridge(browser_before)
    assert read_registry(BRIDGE_KEY) == browser_before
    print('PASS: original browser registration restored; original dist data unchanged', flush=True)


if __name__ == '__main__':
    main()
