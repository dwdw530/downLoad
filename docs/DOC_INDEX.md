# 下载器项目索引

- `main.py`：共享桌面入口；`downloader/core/`：HTTP 分块、续传、队列和媒体；`downloader/database/`：SQLite 数据。
- `downloader/ui/`：Tk 桌面、通知、悬浮条；`macos_status.py`：Mac 原生菜单栏。
- `downloader/browser/` 与 `chrome-extension/`：受限本机桥接、宿主注册、浏览器扩展。
- `downloader/utils/macos.py`：排他改名、卷可用性和登录钥匙串；`config.py`：资源目录与用户状态目录。
- `scripts/build_macos.py`、`scripts/macos_entry.py`、`scripts/BrowserBridge`：[Intel Mac 本地构建与使用](MACOS.md)。
- `build.bat` 与 `installer/`：[Windows 打包](../BUILD_EXE.md)；`tests/`：下载及系统集成回归。
