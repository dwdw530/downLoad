"""Install from a real DMG into a disposable account directory; exercise bundled media tools."""
import argparse
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import secrets
import sqlite3
import struct
import subprocess
import tempfile
import threading
import time


def run(args, *, timeout=30, **kwargs):
    return subprocess.run(args, check=True, timeout=timeout, **kwargs)


def wait_for(fn, limit=30):
    until = time.monotonic() + limit
    while time.monotonic() < until:
        if result := fn():
            return result
        time.sleep(.1)
    raise TimeoutError(fn.__name__)


class QuietHandler(SimpleHTTPRequestHandler):
    def log_message(self, *_):
        pass

    def copyfile(self, source, outputfile):
        try:
            super().copyfile(source, outputfile)
        except (BrokenPipeError, ConnectionResetError):
            pass  # Range probes may stop reading after headers.


def smoke(dmg):
    from AppKit import NSRunningApplication
    from Quartz import CGWindowListCopyWindowInfo, kCGWindowListOptionOnScreenOnly, kCGNullWindowID
    with tempfile.TemporaryDirectory(prefix='daw-dmg-test-') as tmp:
        home = Path(tmp).resolve()
        volume, applications = home / 'volume', home / 'Applications'
        volume.mkdir()
        applications.mkdir()
        app = applications / 'daw下载器.app'
        try:
            run(['/usr/bin/hdiutil', 'attach', '-verify', '-readonly', '-nobrowse', '-noautoopen',
                 '-mountpoint', volume, dmg], timeout=120, stdout=subprocess.DEVNULL)
            assert (volume / 'Applications').readlink() == Path('/Applications')
            run(['/usr/bin/ditto', volume / app.name, app], timeout=90)
        finally:
            if os.path.ismount(volume):
                run(['/usr/bin/hdiutil', 'detach', volume], timeout=60, stdout=subprocess.DEVNULL)
        run(['/usr/bin/codesign', '--verify', '--deep', '--strict', app])
        contents = app / 'Contents'
        desktop = contents / 'MacOS/daw下载器'
        tools, host = contents / 'Helpers', contents / 'Resources/BrowserBridge'
        for name in (desktop, *(tools / n for n in ('ffmpeg', 'ffprobe', 'yt-dlp', 'node'))):
            run(['/usr/bin/lipo', name, '-verify_arch', 'x86_64'])
        env = {**os.environ, 'HOME': str(home), 'CFFIXED_USER_HOME': str(home), 'PATH': '/usr/bin:/bin',
               'NO_PROXY': '127.0.0.1', 'no_proxy': '127.0.0.1'}
        support = home / 'Library/Application Support'
        key = home / 'Library/Keychains/login.keychain-db'
        key.parent.mkdir(parents=True)
        password = secrets.token_hex(24)
        run(['/usr/bin/security', 'create-keychain', '-p', password, key], env=env)
        run(['/usr/bin/security', 'unlock-keychain', '-p', password, key], env=env)
        selected = run(['/usr/bin/security', 'default-keychain', '-d', 'user'], env=env, capture_output=True, text=True)
        assert Path(selected.stdout.strip().strip('"')).resolve() == key.resolve()
        run([host, '--install'], env=env)
        for name in ('Google/Chrome', 'Microsoft Edge'):
            manifest = json.loads((support / name / 'NativeMessagingHosts/com.laowang.downloader.json').read_text())
            registered = Path(manifest['path'])
            assert registered.is_absolute() and registered.samefile(host) and manifest['allowed_origins'] == [
                'chrome-extension://kpknpdpemebgalcfiolkcpaafddelcjj/']
        def native(msg):
            data = json.dumps(msg).encode()
            raw = run([host], env=env, input=struct.pack('<I', len(data)) + data, capture_output=True).stdout
            assert len(raw) >= 4 and struct.unpack('<I', raw[:4])[0] == len(raw)-4
            return json.loads(raw[4:])
        ping = native({'action': 'ping'})
        assert not ping['ok'] and ping['installed'] and 'hls' in ping['capabilities']
        web = home / 'web'
        web.mkdir()
        run([tools/'ffmpeg', '-nostdin', '-v', 'error', '-f', 'lavfi', '-i', 'testsrc2=size=64x64:rate=25',
             '-f', 'lavfi', '-i', 'sine=frequency=440', '-t', '1', '-c:v', 'mpeg2video', '-c:a', 'aac',
             '-f', 'hls', '-hls_time', '1', '-hls_list_size', '0', web/'clip.m3u8'], env=env)
        original = bytes(range(256)) * 512
        (web/'sample.bin').write_bytes(original)
        database = support / 'daw下载器/data/downloads.db'
        endpoint = support / 'daw下载器/ipc/desktop.bridge'
        with ThreadingHTTPServer(('127.0.0.1', 0), partial(QuietHandler, directory=str(web))) as http:
            thread = threading.Thread(target=http.serve_forever, daemon=True)
            thread.start()
            base = f'http://127.0.0.1:{http.server_port}'
            proc = subprocess.Popen([desktop], env=env, stdin=subprocess.DEVNULL)
            try:
                def ready():
                    if proc.poll() is not None:
                        raise RuntimeError('The installed desktop exited before becoming ready')
                    return endpoint.is_file()
                wait_for(ready)
                def visible():
                    return next((w for w in CGWindowListCopyWindowInfo(kCGWindowListOptionOnScreenOnly, kCGNullWindowID)
                                 if w['kCGWindowOwnerPID'] == proc.pid and w['kCGWindowLayer'] == 0
                                 and w['kCGWindowBounds']['Width'] > 500), None)
                window = wait_for(visible)
                print('Installed Intel desktop visible:', dict(window['kCGWindowBounds']), flush=True)
                def download(message):
                    answer = native({**message, 'request_id': secrets.token_hex(16)})
                    assert answer.get('ok'), answer
                    def completed():
                        with sqlite3.connect(database) as cx:
                            row = cx.execute('SELECT status, save_path, error_message FROM download_tasks WHERE task_id=?',
                                             (answer['task_id'],)).fetchone()
                        if row and row[0] in ('failed', 'verify_failed'):
                            raise RuntimeError(row[2])
                        return Path(row[1]) if row and row[0] == 'completed' else None
                    path = wait_for(completed).resolve(strict=True)
                    assert path.is_relative_to(home / 'Downloads'), f'Output outside isolated Downloads: {path}'
                    return path
                assert download({'action':'download_file', 'url':base+'/sample.bin'}).read_bytes() == original
                for audio in (False, True):
                    message = {'action':'download', 'kind':'hls', 'url':base+'/clip.m3u8', 'filename':'音频' if audio else '视频'}
                    if audio:
                        message['media_options'] = {'mode':'audio', 'audio_format':'mp3'}
                    output = download(message)
                    probe = run([tools/'ffprobe', '-v', 'error', '-show_entries', 'stream=codec_type,codec_name:format=duration',
                                 '-of', 'json', output], env=env, capture_output=True)
                    info = json.loads(probe.stdout)
                    assert float(info['format']['duration']) > 0
                    streams = {s['codec_type']:s['codec_name'] for s in info['streams']}
                    assert streams == {'audio':'mp3'} if audio else set(streams) == {'audio','video'}
                    assert output.suffix == ('.mp3' if audio else '.mp4')
                    print('Desktop published:', output.suffix, streams, flush=True)
            finally:
                if proc.poll() is None:
                    application = NSRunningApplication.runningApplicationWithProcessIdentifier_(proc.pid)
                    if application is not None:
                        application.terminate()
                    try:
                        proc.wait(timeout=10)
                    except subprocess.TimeoutExpired:
                        # Only this Popen child lives in the disposable install; never signal by app name.
                        proc.kill()
                        proc.wait(timeout=5)
                http.shutdown()
                thread.join()
        print('DMG, installed signature, isolated browser registration, file integrity, HLS and MP3: OK')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('dmg', type=Path)
    smoke(parser.parse_args().dmg.resolve())
