# -*- coding: utf-8 -*-
"""
EXE 打包脚本（给 build.bat 调用）

老王说：批处理里别塞中文，不然 cmd 一抽风就把你当场送走。
直接用 spec 文件打包，配置都在那里，别 TM 两边维护！
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


def main() -> int:
    project_root = Path(__file__).resolve().parents[1]
    spec_file = project_root / "老王下载器.spec"

    if not spec_file.exists():
        print(f"[ERROR] Spec file not found: {spec_file}")
        return 1

    dist_dir = project_root / "dist"

    # dist中可能已有用户配置、数据库和断点文件，不能整目录清理。
    # 由PyInstaller清理自己的构建缓存并更新同名EXE。

    # 直接用 spec 文件打包，所有配置都在那里
    cmd = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--noconfirm",
        "--clean",
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

    exe_path = dist_dir / "老王下载器.exe"
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
