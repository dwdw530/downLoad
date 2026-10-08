"""Build an offline, per-user Windows installer from the verified dist payload."""
from __future__ import annotations

import argparse
import hashlib
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import urllib.request
import zipfile

from package_release import collect_release_files, sha256


ROOT = Path(__file__).resolve().parents[1]
NSIS_VERSION = '3.13'
NSIS_URL = f'https://downloads.sourceforge.net/project/nsis/NSIS%203/{NSIS_VERSION}/nsis-{NSIS_VERSION}.zip'
NSIS_SHA256 = 'ba63dffc4410ee89193e1cb5a41989991bd77c61068da17e3156d136b7b0b3d8'


def nsis_string(value):
    """Quote literal paths for NSIS without interpreting dollars or quotes."""
    return str(value).replace('$', '$$').replace('"', '$\\"')


def find_compiler(cache, explicit=None):
    if explicit:
        compiler = explicit.resolve()
        if not compiler.is_file():
            raise FileNotFoundError(f'NSIS compiler not found: {compiler}')
        return compiler
    installed = shutil.which('makensis')
    if installed:
        return Path(installed)
    compiler = cache / f'nsis-{NSIS_VERSION}' / 'Bin' / 'makensis.exe'
    if compiler.is_file():
        return compiler
    cache.mkdir(parents=True, exist_ok=True)
    archive = cache / f'nsis-{NSIS_VERSION}.zip'
    if not archive.is_file() or sha256(archive) != NSIS_SHA256:
        print(f'[TOOLS] Downloading portable NSIS {NSIS_VERSION} from SourceForge...', flush=True)
        request = urllib.request.Request(NSIS_URL, headers={'User-Agent': 'daw-downloader-build'})
        with urllib.request.urlopen(request, timeout=60) as response:
            payload = response.read()
        if hashlib.sha256(payload).hexdigest() != NSIS_SHA256:
            raise ValueError('NSIS archive checksum mismatch; compiler was not executed')
        archive.write_bytes(payload)
    with zipfile.ZipFile(archive) as bundle:
        for member in bundle.infolist():
            target = (cache / member.filename).resolve()
            if not target.is_relative_to(cache.resolve()):
                raise ValueError(f'Unsafe NSIS archive member: {member.filename}')
        bundle.extractall(cache)
    if not compiler.is_file():
        raise FileNotFoundError(f'NSIS archive has no compiler: {compiler}')
    return compiler


def write_payload_include(destination, files):
    """Generate exact install/delete instructions; never recursively delete a folder."""
    lines = ['!macro InstallPayload']
    directories = set()
    executables = []
    for source, relative in files:
        path = Path(relative)
        folder = '' if path.parent == Path('.') else '\\' + str(path.parent)
        lines.append(f'  SetOutPath "$INSTDIR{nsis_string(folder)}"')
        lines.append(f'  File /oname={nsis_string(path.name)} "{nsis_string(source)}"')
        directories.update(parent for parent in path.parents if parent != Path('.'))
        if path.suffix.lower() == '.exe':
            executables.append(str(path))
    lines += ['!macroend', '', '!macro RemovePayload']
    for _, relative in files:
        lines.append(f'  Delete "$INSTDIR\\{nsis_string(str(Path(relative)))}"')
    for directory in sorted(directories, key=lambda p: (-len(p.parts), str(p))):
        lines.append(f'  RMDir "$INSTDIR\\{nsis_string(directory)}"')
    lines += ['!macroend', '', '!macro CheckPayloadUnlocked']
    for relative in executables:
        lines.append(f'  !insertmacro CheckFileUnlocked "{nsis_string(relative)}"')
    lines += ['!macroend', '']
    destination.write_text('\n'.join(lines), encoding='utf-8-sig')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('version', nargs='?', default='v0.3.0', help='Release version, e.g. v0.3.0')
    parser.add_argument('--rebuild', action='store_true', help='Rebuild both EXEs in dist before packaging')
    parser.add_argument('--makensis', type=Path, help='Use an existing NSIS 3 Unicode compiler')
    args = parser.parse_args()
    if os.name != 'nt':
        parser.error('Build the Windows installer on Windows')
    if not re.fullmatch(r'v\d+\.\d+\.\d+', args.version):
        parser.error('Expected vMAJOR.MINOR.PATCH')
    if any(int(part) > 65535 for part in args.version[1:].split('.')):
        parser.error('Windows version fields must be at most 65535')
    work = ROOT / 'build' / 'installer'
    compiler = find_compiler(work / 'tools', args.makensis)
    if args.rebuild:
        subprocess.run([sys.executable, '-B', str(ROOT / 'scripts' / 'build_exe.py')], cwd=ROOT, check=True)
    dist = ROOT / 'dist'
    files = collect_release_files(ROOT, dist)
    files += [(ROOT / 'LICENSE', 'LICENSE'), (ROOT / 'installer' / '使用说明.txt', '使用说明.txt')]
    work.mkdir(parents=True, exist_ok=True)
    include = work / 'payload.nsh'
    write_payload_include(include, files)
    destination = dist / f'daw-downloader-{args.version}-windows-x64-setup.exe'
    command = [str(compiler), '/V3', '/INPUTCHARSET', 'UTF8',
               f'/DAPP_VERSION={args.version[1:]}', f'/DPROJECT_ROOT={nsis_string(ROOT)}',
               f'/DPAYLOAD_INCLUDE={nsis_string(include)}', f'/DOUTPUT_FILE={nsis_string(destination)}',
               f'/DINSTALL_SIZE_KB={sum(source.stat().st_size for source, _ in files) // 1024}',
               str(ROOT / 'installer' / 'daw-downloader.nsi')]
    print(f'[BUILD] Packaging {len(files)} allowlisted files from {dist}', flush=True)
    subprocess.run(command, cwd=ROOT, check=True)
    digest = sha256(destination)
    destination.with_suffix('.exe.sha256').write_text(f'{digest}  {destination.name}\n', encoding='ascii')
    print(f'[OK] Installer: {destination}', flush=True)
    print(f'[OK] Size: {destination.stat().st_size:,} bytes; SHA256: {digest}', flush=True)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
