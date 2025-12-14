# -*- mode: python ; coding: utf-8 -*-
# 老王下载器打包配置
# tk 版本已统一为 8.6.12，无需手动指定路径

import os
from PyInstaller.utils.hooks import collect_all

# 收集 customtkinter 资源文件（关键！）
import customtkinter
ctk_path = os.path.dirname(customtkinter.__file__)

datas = [(ctk_path, 'customtkinter')]
binaries = []
hiddenimports = ['pystray', 'PIL', 'plyer', 'customtkinter']

tmp_ret = collect_all('pystray')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]
tmp_ret = collect_all('plyer')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]

a = Analysis(
    ['main.py'],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='老王下载器',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=['assets\\icon.ico'],
)
