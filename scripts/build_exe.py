# -*- coding: utf-8 -*-
"""
EXE 打包脚本（给 build.bat 调用）

老王说：批处理里别塞中文，不然 cmd 一抽风就把你当场送走。
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path


def _safe_rmtree(path: Path):
    if path.exists() and path.is_dir():
        shutil.rmtree(path, ignore_errors=True)


def main() -> int:
    parser = argparse.ArgumentParser(description="Build 老王下载器 EXE via PyInstaller")
    parser.add_argument(
        "--mode",
        choices=("onefile", "onedir"),
        default="onefile",
        help="Packaging mode: onefile (single exe) or onedir (folder).",
    )
    args = parser.parse_args()

    project_root = Path(__file__).resolve().parents[1]

    build_dir = project_root / "build"
    dist_dir = project_root / "dist"

    # 清理旧产物
    _safe_rmtree(build_dir)
    _safe_rmtree(dist_dir)
    for spec in project_root.glob("*.spec"):
        try:
            spec.unlink(missing_ok=True)
        except Exception:
            # 不影响打包流程，最多留个旧spec
            pass

    icon_path = project_root / "assets" / "icon.ico"
    if not icon_path.exists():
        print(f"[ERROR] Icon not found: {icon_path}")
        return 1

    # 艹，找到customtkinter的安装路径，把资源文件打包进去！
    try:
        import customtkinter
        ctk_path = os.path.dirname(customtkinter.__file__)
        print(f"[INFO] CustomTkinter path: {ctk_path}")
    except ImportError:
        print("[ERROR] customtkinter not installed!")
        return 1

    cmd = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--noconfirm",
        "--clean",
        "--windowed",
        f"--icon={icon_path}",
        "--name=老王下载器",
        "--hidden-import=customtkinter",
        "--hidden-import=PIL",
        # 把customtkinter的资源文件打包进去（关键！）
        f"--add-data={ctk_path}{os.pathsep}customtkinter",
        "main.py",
    ]

    if args.mode == "onefile":
        cmd.insert(5, "--onefile")
        # onefile 会解压到临时目录；某些环境（沙箱/企业策略）写不了 %TEMP% 会直接“打不开”。
        # 放到当前目录（exe 所在目录）能规避大部分权限问题；如果你把 exe 放到只读目录运行，照样会炸。
        cmd.insert(7, "--runtime-tmpdir=.")
    else:
        cmd.insert(5, "--onedir")

    print("[BUILD] Running:", " ".join(str(x) for x in cmd))
    subprocess.check_call(cmd, cwd=str(project_root))

    exe_path = dist_dir / "老王下载器.exe"
    if args.mode == "onedir":
        exe_path = dist_dir / "老王下载器" / "老王下载器.exe"

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
