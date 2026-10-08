"""Package the verified dist using an explicit allowlist, without user runtime data."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import zipfile

from build_exe import VIDEO_FILES


EXTENSION_FILES = ('background.js', 'content.js', 'media.js', 'manifest.json', 'package.json',
                   'popup.js', 'popup.html', 'popup.css', 'icons/download.svg', 'icons/LICENSE')
PROGRAM_FILES = ('daw下载器.exe', 'BrowserBridge.exe', 'install_browser_bridge.cmd', 'uninstall_browser_bridge.cmd')


def sha256(path):
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def write_archive(destination, files):
    with zipfile.ZipFile(destination, 'w', compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        for source, name in files:
            archive.write(source, name)
    with zipfile.ZipFile(destination) as archive:
        assert archive.namelist() == [name for _, name in files]
        assert archive.testzip() is None


def collect_release_files(root, dist):
    """Share the explicit, verified payload between ZIP and Windows installers."""
    extension = [(dist / 'chrome-extension' / name, 'chrome-extension/' + name) for name in EXTENSION_FILES]
    program = [(dist / name, name) for name in PROGRAM_FILES]
    video_tools = [(dist / 'video-tools' / name, 'video-tools/' + name) for name in VIDEO_FILES]
    files = program + extension + video_tools
    for source, _ in files:
        if not source.is_file() or not source.stat().st_size or not source.resolve().is_relative_to(dist.resolve()):
            raise ValueError(f'Missing or unexpected release component: {source}')
    for name in EXTENSION_FILES:
        if (root / 'chrome-extension' / name).read_bytes() != (dist / 'chrome-extension' / name).read_bytes():
            raise ValueError(f'dist extension differs from source: {name}')
    return files


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('version', help='GitHub release tag, for example v0.3.0')
    args = parser.parse_args()
    if not re.fullmatch(r'v\d+\.\d+\.\d+', args.version):
        parser.error('Expected a vMAJOR.MINOR.PATCH release tag')
    root = Path(__file__).resolve().parents[1]
    dist = root / 'dist'
    notes = root / 'docs' / 'releases' / (args.version + '.md')
    files = collect_release_files(root, dist)
    extension = [(source, name) for source, name in files if name.startswith('chrome-extension/')]
    if not notes.is_file():
        raise ValueError(f'Missing release notes: {notes}')
    extension_version = json.loads((dist / 'chrome-extension/manifest.json').read_text(encoding='utf-8'))['version']
    output = root / 'output' / 'releases' / args.version
    output.mkdir(parents=True, exist_ok=True)
    desktop_zip = output / f'daw-downloader-{args.version}-windows-x64.zip'
    extension_zip = output / f'daw-browser-extension-v{extension_version}.zip'
    write_archive(desktop_zip, files + [(notes, 'README.md')])
    write_archive(extension_zip, extension)
    hashes = {artifact.name: sha256(artifact) for artifact in (desktop_zip, extension_zip)}
    (output / 'SHA256SUMS.txt').write_text(''.join(f'{digest}  {name}\n' for name, digest in hashes.items()), encoding='ascii')
    for artifact in (desktop_zip, extension_zip):
        print(json.dumps({'asset': artifact.name, 'bytes': artifact.stat().st_size, 'sha256': hashes[artifact.name]}), flush=True)
    print('Artifacts:', output, flush=True)


if __name__ == '__main__':
    main()
