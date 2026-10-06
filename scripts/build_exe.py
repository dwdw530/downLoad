# -*- coding: utf-8 -*-
"""
EXE 打包脚本（给 build.bat 调用）

老王说：批处理里别塞中文，不然 cmd 一抽风就把你当场送走。
直接用 spec 文件打包，配置都在那里，别 TM 两边维护！
"""

from __future__ import annotations

import os
import argparse
import subprocess
import sys
import shutil
from pathlib import Path


VIDEO_FILES = ('yt-dlp.exe', 'ffmpeg.exe', 'ffprobe.exe', 'node.exe', 'TOOLS.json',
               'yt-dlp-LICENSE', 'node-LICENSE', 'ffmpeg-LICENSE.txt', 'ffmpeg-GPL-3.0.txt')


def video_tool_files(project_root):
    source = project_root / 'vendor' / 'video'
    missing = [name for name in VIDEO_FILES if not (source / name).is_file() or (source / name).stat().st_size == 0]
    if missing:
        raise RuntimeError('Missing video tools: ' + ', '.join(missing) +
                           '; run scripts/setup_video_tools.py before building')
    return [source / name for name in VIDEO_FILES]


def copy_video_tools(files, dist_dir):
    destination = dist_dir / 'video-tools'
    destination.mkdir(parents=True, exist_ok=True)
    for source in files:
        shutil.copy2(source, destination / source.name)


def main() -> int:
    project_root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser()
    parser.add_argument('--dist-dir', type=Path, help='Build a separate release while the installed EXE is running')
    args = parser.parse_args()
    spec_file = project_root / "老王下载器.spec"

    if not spec_file.exists():
        print(f"[ERROR] Spec file not found: {spec_file}")
        return 1

    dist_dir = args.dist_dir.resolve() if args.dist_dir else project_root / "dist"
    # Fail before replacing either EXE if the YouTube release would be incomplete.
    video_files = video_tool_files(project_root)

    # dist中可能已有用户配置、数据库和断点文件，不能整目录清理。
    # 由PyInstaller清理自己的构建缓存并更新同名EXE。

    # 直接用 spec 文件打包，所有配置都在那里
    cmd = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--noconfirm",
        "--clean",
        "--distpath",
        str(dist_dir),
        str(spec_file),
    ]

    # 直接运行环境内的python时，PATH仍可能指向Anaconda base。
    # PyInstaller按PATH解析DLL，必须优先使用当前环境的Tcl/Tk等依赖。
    build_env = os.environ.copy()
    library_bin = Path(sys.prefix) / "Library" / "bin"
    if library_bin.is_dir():
        build_env["PATH"] = str(library_bin) + os.pathsep + build_env.get("PATH", "")

    print("[BUILD] Running:", " ".join(str(x) for x in cmd))
    subprocess.check_call(cmd, cwd=str(project_root), env=build_env)

    bridge_cmd = cmd[:-1] + [str(project_root / 'browser_bridge.spec')]
    subprocess.check_call(bridge_cmd, cwd=str(project_root), env=build_env)
    copy_video_tools(video_files, dist_dir)
    shutil.copytree(project_root / 'chrome-extension', dist_dir / 'chrome-extension', dirs_exist_ok=True)
    for name in ('install_browser_bridge.cmd', 'uninstall_browser_bridge.cmd'):
        shutil.copy2(project_root / 'scripts' / name, dist_dir / name)

    exe_path = dist_dir / "daw下载器.exe"
    if exe_path.exists():
        print(f"[OK] EXE: {exe_path}")
        return 0

    # 兜底：输出 dist 里有什么
    if dist_dir.exists():
        files = [p.name for p in dist_dir.iterdir()]
        print("[WARN] dist contains:", files)
    print("[ERROR] EXE not found in dist")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
