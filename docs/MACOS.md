# Intel Mac 本机版

将 `daw下载器.app` 放在固定位置，双击运行即可。此包面向 Intel macOS Ventura 13.7，不依赖 Homebrew、系统 Python 或终端 PATH。本地 ad-hoc 签名，不是 Developer ID 公证分发包。不要关闭 Gatekeeper。

- 普通下载：添加任务 → 粘贴 HTTP(S) 直链；下载进行中可以暂停，退出后重新打开，选择该任务继续。支持续传仍取决于服务器是否支持 Range 以及资源是否改变。
- 默认成品：`~/Downloads/daw下载器/`。配置、任务库和默认分块：`~/Library/Application Support/daw下载器/`；日志：`~/Library/Logs/daw下载器/app.log`。升级时只换 `.app`。若设置成外置卷，恢复时先接回同一设备。
- 浏览器是可选项：在 **macOS 屏幕顶部菜单栏 → 浏览器 → 连接 Chrome / Edge**。按弹窗说明加载扩展并刷新需要使用的页面。移动 `.app` 后重新连接、重新选择扩展目录；“断开浏览器连接”仅移除本用户宿主清单。不会自动修改浏览器扩展或读取浏览器配置库。
- 菜单栏下载图标可恢复主窗口或退出。悬浮条默认关闭；剪贴板监视在 Mac 首次启动默认关闭。带凭据的任务使用本用户登录钥匙串中的独立密钥；解锁或授权被拒绝时不降级成明文。

## 本地构建

准备带 Tk 8.6 的 Intel Python 3.11。建议使用独立虚拟环境，安装 `requirements.txt`、`customtkinter==5.2.2` 和 `PyInstaller==6.22.3`；使用预编译轮包可避免在旧系统上从头编译扩展。将下列可执行文件和授权文本放入同一个工具目录，然后执行：

```bash
python scripts/build_macos.py --tools /绝对路径/媒体工具 --output dist-macos
```

工具目录包含 `yt-dlp`、`ffmpeg`、`ffprobe`、`node` 及 `yt-dlp-LICENSE`、`ffmpeg-GPLv3.txt`、`node-LICENSE`。均需包含 x86_64 且可在目标系统运行，不可用 Windows `.exe` 替代。实际采用的来源与原始二进制 SHA-256 见 [组件清单](MACOS_COMPONENTS.txt)。脚本拒绝覆盖任何已存在的应用。Windows 的构建入口保持 `build.bat`。
