"""Fetch pinned yt-dlp and checksum-verified FFmpeg into this project only."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import zipfile

import requests

ROOT = Path(__file__).resolve().parents[1]
YT_VERSION = '2026.08.19'
YT_HASH = '66674953fe251b89f4d08c5f0e35e0728679bd67ab3d7d05c0562af101dd3e7a'


def download(url, target, expected=None):
    if target.exists() and expected and hashlib.sha256(target.read_bytes()).hexdigest() == expected:
        return expected
    temporary = target.with_suffix(target.suffix + '.part')
    digest = hashlib.sha256()
    with requests.get(url, stream=True, timeout=(20, 60)) as response:
        response.raise_for_status()
        with temporary.open('wb') as stream:
            for chunk in response.iter_content(1024 * 1024):
                stream.write(chunk)
                digest.update(chunk)
    actual = digest.hexdigest()
    if expected and actual != expected:
        raise ValueError(f'Checksum mismatch: {target.name}')
    shutil.move(str(temporary), str(target))
    print('Verified' if expected else 'Downloaded', target.name, actual, flush=True)
    return actual


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--yt-only', action='store_true')
    args = parser.parse_args()
    destination = ROOT / 'vendor' / 'video'
    destination.mkdir(parents=True, exist_ok=True)
    yt_url = f'https://github.com/yt-dlp/yt-dlp/releases/download/{YT_VERSION}/yt-dlp.exe'
    download(yt_url, destination / 'yt-dlp.exe', YT_HASH)
    if args.yt_only:
        return
    base = 'https://github.com/yt-dlp/FFmpeg-Builds/releases/download/latest/'
    archive_name = 'ffmpeg-master-latest-win64-gpl.zip'
    response = requests.get(base + 'checksums.sha256', timeout=30)
    response.raise_for_status()
    expected = next(line.split()[0] for line in response.text.splitlines() if line.split()[-1] == archive_name)
    archive = destination / archive_name
    download(base + archive_name, archive, expected)
    with zipfile.ZipFile(archive) as zipped:
        for executable in ('ffmpeg.exe', 'ffprobe.exe'):
            member = next(info for info in zipped.infolist() if info.filename.endswith('/bin/' + executable))
            with zipped.open(member) as source, (destination / executable).open('wb') as output:
                shutil.copyfileobj(source, output)
        for member in zipped.infolist():
            name = Path(member.filename).name
            if name.lower().startswith(('license', 'copying')) and not member.is_dir():
                (destination / ('ffmpeg-' + name)).write_bytes(zipped.read(member))
    node = Path(subprocess.check_output(['node', '-p', 'process.execPath'], text=True).strip())
    node_version = subprocess.check_output([str(node), '--version'], text=True).strip()
    if int(node_version.split('.')[0][1:]) < 22:
        raise RuntimeError('Node 22+ is required')
    shutil.copy2(node, destination / 'node.exe')
    download(f'https://raw.githubusercontent.com/nodejs/node/{node_version}/LICENSE', destination / 'node-LICENSE')
    download(f'https://raw.githubusercontent.com/yt-dlp/yt-dlp/{YT_VERSION}/LICENSE', destination / 'yt-dlp-LICENSE')
    download('https://raw.githubusercontent.com/FFmpeg/FFmpeg/master/COPYING.GPLv3', destination / 'ffmpeg-GPL-3.0.txt')
    manifest = {'yt_dlp': {'version': YT_VERSION, 'url': yt_url, 'sha256': YT_HASH},
                'ffmpeg': {'url': base + archive_name, 'sha256': expected,
                           'source': 'https://github.com/yt-dlp/FFmpeg-Builds'},
                'node': {'version': node_version, 'source': 'https://nodejs.org/'}}
    (destination / 'TOOLS.json').write_text(json.dumps(manifest, indent=2), encoding='utf-8')


if __name__ == '__main__':
    main()
