"""Package a verified Intel app for drag-in installation; do not upload or overwrite assets."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess
import tarfile
import tempfile

ROOT = Path(__file__).resolve().parents[1]


def sha256(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def main():
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument('--app', required=True, type=Path)
    cli.add_argument('--sources', required=True, type=Path)
    cli.add_argument('--output', required=True, type=Path)
    cli.add_argument('--tag', required=True)
    args = cli.parse_args()
    if not re.fullmatch(r'v\d+\.\d+\.\d+-macos-intel\.\d+', args.tag):
        cli.error('Expected vX.Y.Z-macos-intel.N')
    app, sources, output = (p.resolve() for p in (args.app, args.sources, args.output))
    prefix = 'daw-downloader-' + args.tag
    names = [prefix + '.dmg', prefix + '-third-party-sources.tar.gz', 'SHA256SUMS.txt']
    output.mkdir(parents=True, exist_ok=True)
    if any((output / n).exists() for n in names):
        cli.error('Use an empty output directory; release assets are immutable')
    if app.name != 'daw下载器.app' or not (app / 'Contents/Resources/Licenses/yt-dlp-THIRD_PARTY_LICENSES.txt').is_file():
        cli.error('Expected the distributable .app, not an earlier local build')
    subprocess.run(['/usr/bin/codesign', '--verify', '--deep', '--strict', str(app)], check=True)
    # Only source files named by verified inventories are published, never the build cache.
    files = {Path('ffmpeg-9.1.tar.xz'): '1a4d394ae63d190a526cd052eb4415c156b5c5e2df37efab41a7de0b8fde2433',
             Path('lame-4.0.tar.gz'): '3df5124d5ad3a98312ffd7ba6a9b36230e4f8a3e66d3ce0f425e336c32d216eb'}
    extras = [Path('metadata') / n for n in ('THIRD_PARTY_LICENSES.txt', 'macos.txt', 'macos-curl_cffi.txt',
              'build.yml', 'python-build-info.json', 'python-sources.json', 'native-sources.json')]
    for manifest, directory in [('native-sources.json', Path()), ('python-sources.json', Path('python'))]:
        for row in json.loads((sources / 'metadata' / manifest).read_text(encoding='utf-8')):
            name = row['archive']
            if Path(name).name != name or not re.fullmatch('[0-9a-f]{64}', row['sha256']):
                cli.error(f'Invalid entry in {manifest}: {name}')
            files[directory / name] = row['sha256']
    for path, expected in files.items():
        if (sources / path).is_symlink() or sha256(sources / path) != expected:
            cli.error(f'Invalid source archive: {path}')
    with tempfile.TemporaryDirectory(dir=output) as tmp:
        stage = Path(tmp)
        image_root = stage / 'image'
        image_root.mkdir()
        subprocess.run(['/usr/bin/ditto', str(app), str(image_root / app.name)], check=True)
        (image_root / 'Applications').symlink_to('/Applications')
        subprocess.run(['/usr/bin/hdiutil', 'create', '-quiet', '-fs', 'HFS+', '-format', 'UDZO',
                        '-volname', 'daw下载器 Intel Mac', '-srcfolder', str(image_root), str(stage / names[0])], check=True)
        with tarfile.open(stage / names[1], 'w:gz', compresslevel=1) as tar:
            for path in sorted([*extras, *files]):
                tar.add(sources / path, arcname=str(path), recursive=False)
            for path in ['scripts/build_macos_media.sh', 'scripts/build_macos.py', 'scripts/package_macos.py',
                         'scripts/smoke_macos_dmg.py', 'docs/MACOS_COMPONENTS.txt', 'docs/MACOS.md']:
                tar.add(ROOT / path, arcname=path, recursive=False)
            tar.add(app / 'Contents/Resources/Licenses', arcname='notices')
        sums = ''.join(f'{sha256(stage / n)}  {n}\n' for n in names[:2])
        (stage / names[2]).write_text(sums, encoding='ascii')
        for n in names:
            (stage / n).rename(output / n)
        print(sums, end='')


if __name__ == '__main__':
    main()
