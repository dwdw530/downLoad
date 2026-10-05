# -*- mode: python ; coding: utf-8 -*-
a = Analysis(['browser_host.py'], pathex=[], binaries=[], datas=[], hiddenimports=[],
             hookspath=[], hooksconfig={}, runtime_hooks=[], excludes=[], noarchive=False)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, a.binaries, a.datas, [], name='BrowserBridge',
          debug=False, bootloader_ignore_signals=False, strip=False, upx=True,
          console=True, disable_windowed_traceback=False)
