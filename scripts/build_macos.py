"""Assemble a local, self-contained Intel .app; never overwrite an installed app."""
import argparse
import hashlib
from importlib import metadata
import os
import platform
import plistlib
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from downloader.version import APP_VERSION
NAME = 'daw下载器'


def copy_python_notices(destination, extra):
    """Copy notices for the actual build environment, including vendored wheel libraries."""
    from packaging.requirements import Requirement
    from packaging.utils import canonicalize_name
    lines = (ROOT / 'requirements.txt').read_text(encoding='utf-8').splitlines()
    pending = [Requirement(s) for s in [*lines, 'PyInstaller', 'setuptools']
               if s.strip() and not s.lstrip().startswith('#')]
    seen = set()
    while pending:
        requirement = pending.pop()
        name = canonicalize_name(requirement.name)
        if name in seen or (requirement.marker and not requirement.marker.evaluate({'extra': ''})):
            continue
        seen.add(name)
        dist = metadata.distribution(name)
        target = destination / f'{name}-{dist.version}'
        found = False
        for member in dist.files or ():
            if member.is_absolute() or '..' in member.parts:
                continue
            if not (member.name.lower().startswith(('license', 'copying', 'notice', 'copyright'))
                    or '.dist-info/licenses/' in str(member).lower()):
                continue
            if member.suffix.lower() in ('.py', '.pyc') or not dist.locate_file(member).is_file():
                continue
            path = target / member
            path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(dist.locate_file(member), path)
            found = True
        if not found:
            # Some wheels (e.g. pyobjc-core) carry only an SPDX identifier.
            target.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(extra / (name + '-LICENSE.txt'), target / 'LICENSE.txt')
        pending.extend(Requirement(s) for s in dist.requires or ())


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--tools', type=Path, default=ROOT / 'vendor/macos-x86_64')
    parser.add_argument('--output', type=Path, default=ROOT / 'dist-macos')
    args = parser.parse_args()
    if sys.platform != 'darwin' or platform.machine() != 'x86_64':
        parser.error('Build using Intel Python on macOS')
    import tkinter
    tkinter.Tcl().eval('info library')
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    destination = output / (NAME + '.app')
    if destination.exists():
        parser.error(f'{destination} already exists; choose a new --output directory')
    tools = ('ffmpeg', 'ffprobe', 'yt-dlp', 'node')
    for name in tools:
        executable = args.tools / name
        if not os.access(executable, os.X_OK):
            parser.error(f'Missing executable: {executable}')
        subprocess.run(['/usr/bin/lipo', str(executable), '-verify_arch', 'x86_64'], check=True)
    with tempfile.TemporaryDirectory(dir=output, prefix='.build-') as build:
        work = Path(build)
        subprocess.run([sys.executable, '-m', 'PyInstaller', '--windowed', '--onedir',
                        '--target-arch', 'x86_64', '--name', NAME, '--paths', str(ROOT),
                        '--osx-bundle-identifier', 'com.laowang.downloader',
                        '--icon', str(ROOT / 'assets/icon.png'), '--collect-data', 'customtkinter',
                        '--add-data', f'{ROOT / "assets"}:assets',
                        '--workpath', str(work / 'work'), '--distpath', str(work / 'dist'),
                        '--specpath', build, str(ROOT / 'scripts/macos_entry.py')], cwd=ROOT, check=True)
        app = work / 'dist' / destination.name
        contents = app / 'Contents'
        media = contents / 'Helpers'
        media.mkdir(exist_ok=True)
        resources = contents / 'Resources'
        # Scripts are sealed bundle resources, not nested Mach-O code objects.
        bridge = resources / 'BrowserBridge'
        shutil.copyfile(ROOT / 'scripts/BrowserBridge', bridge)
        bridge.chmod(0o755)
        shutil.copytree(ROOT / 'chrome-extension', resources / 'chrome-extension')
        licenses = resources / 'Licenses'
        shutil.copytree(args.tools / 'notices/python-runtime', licenses / 'Python-runtime')
        copy_python_notices(licenses / 'Python-packages', args.tools / 'notices')
        shutil.copyfile(ROOT / 'LICENSE', licenses / 'daw-Apache-2.0.txt')
        shutil.copy2(ROOT / 'docs/MACOS_COMPONENTS.txt', licenses / 'SOURCES.txt')
        for name in ('yt-dlp-LICENSE', 'yt-dlp-THIRD_PARTY_LICENSES.txt', 'node-LICENSE',
                     'ffmpeg-GPLv3.txt', 'lame-COPYING.txt', 'lame-LICENSE.txt'):
            shutil.copyfile(args.tools / name, licenses / name)
        with (licenses / 'HELPER-SHA256SUMS.txt').open('w', encoding='ascii') as sums:
            for name in tools:
                with (args.tools / name).open('rb') as stream:
                    sums.write(f'{hashlib.file_digest(stream, "sha256").hexdigest()}  {name}\n')
        for name in tools:
            binary = media / name
            shutil.copy2(args.tools / name, binary)
            if subprocess.run(['/usr/bin/codesign', '--verify', str(binary)], capture_output=True).returncode:
                subprocess.run(['/usr/bin/codesign', '--force', '--sign', '-', str(binary)], check=True)
        plist = contents / 'Info.plist'
        with plist.open('rb') as stream:
            info = plistlib.load(stream)
        info.update(CFBundleShortVersionString=APP_VERSION, CFBundleVersion=APP_VERSION,
                    CFBundleDisplayName=NAME, LSMinimumSystemVersion='13.7',
                    NSAppleEventsUsageDescription='仅在启用且确认下载完成后关机时请求退出系统。')
        with plist.open('wb') as stream:
            plistlib.dump(info, stream)
        subprocess.run(['/usr/bin/codesign', '--force', '--sign', '-', str(app)], check=True)
        subprocess.run(['/usr/bin/codesign', '--verify', '--deep', '--strict', str(app)], check=True)
        app.rename(destination)
    print(destination)


if __name__ == '__main__':
    main()
