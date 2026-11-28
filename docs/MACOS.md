# Intel Mac 下载与安装

1. 在 [Intel Mac 发布页](https://github.com/dwdw530/downLoad/releases/tag/v0.16.0-macos-intel.2) 下载 **`daw-downloader-v0.16.0-macos-intel.2.dmg`**。普通用户只需这一个文件，无需下载源码、安装 Python 或 Homebrew。
2. 双击 DMG，把 `daw下载器.app` 拖到旁边的 `Applications`，推出镜像，再从“应用程序”启动。不要直接在只读镜像中注册浏览器。
3. 本版针对 Intel `x86_64`，实机环境为 Ventura 13.7.6，最低目标系统为 13.7；不是 Apple Silicon 原生包。

**首次打开：**此预览版使用本地 ad-hoc 签名，未经过 Apple Developer ID 签名和公证。若系统无法验证开发者，请先核对下载来源和同页 `SHA256SUMS.txt`，再参考 [Apple 官方说明](https://support.apple.com/zh-cn/102445)。请勿关闭 Gatekeeper；若设备的组织策略不允许打开，联系管理员。已安装早期版本的用户先正常退出下载器，再替换应用，不要删除数据目录。

- 普通下载：添加任务 → 粘贴 HTTP(S) 直链；下载进行中可以暂停，退出后重新打开，选择该任务继续。支持续传仍取决于服务器是否支持 Range 以及资源是否改变。
- 默认成品：`~/Downloads/daw下载器/`。配置、任务库和默认分块：`~/Library/Application Support/daw下载器/`；日志：`~/Library/Logs/daw下载器/app.log`。升级时只换 `.app`。若设置成外置卷，恢复时先接回同一设备。
- 浏览器是可选项：在 **macOS 屏幕顶部菜单栏 → 浏览器 → 连接 Chrome / Edge**。按弹窗说明加载扩展并刷新需要使用的页面。移动 `.app` 后重新连接、重新选择扩展目录；“断开浏览器连接”仅移除本用户宿主清单。不会自动修改浏览器扩展或读取浏览器配置库。
- 菜单栏下载图标可恢复主窗口或退出。悬浮条默认关闭；剪贴板监视在 Mac 首次启动默认关闭。带凭据的任务使用本用户登录钥匙串中的独立密钥；解锁或授权被拒绝时不降级成明文。

## 本地构建

在 Intel macOS 上使用带 Tk 8.6 的隔离 Python 3.11 环境，安装 `requirements.txt`、`customtkinter==5.2.2` 与 `PyInstaller==6.22.3`。本次构建输入列于 [组件清单](MACOS_COMPONENTS.txt)。从同页第三方源码附件解包后，可重建 FFmpeg、ffprobe 和 MP3 编码器；媒体构建需要 Apple 命令行开发工具、make 与 pkg-config。

```bash
# SOURCES 是解包后的第三方源码目录，TOOLS 是新的工具目录。
scripts/build_macos_media.sh "$SOURCES" "$TOOLS"
# 按下文准备其余固定工具与许可文本后：
python scripts/build_macos.py --tools "$TOOLS" --output dist-macos
python scripts/package_macos.py --app dist-macos/daw下载器.app --sources "$SOURCES" \
  --output dist-macos/release --tag v0.16.0-macos-intel.2
```

媒体脚本产生 `ffmpeg`、`ffprobe` 及相应许可证。将清单中对应的 `yt-dlp_macos`（命名为 `yt-dlp`）、Intel `node`、`yt-dlp-LICENSE`、上游完整 `yt-dlp-THIRD_PARTY_LICENSES.txt` 与 `node-LICENSE` 放进 `TOOLS`。在 `TOOLS/notices/python-runtime/` 放置与所用 Python 构建对应的运行时许可（本版取自 python-build-standalone 完整构建的 `python/licenses/`）；`TOOLS/notices/pyobjc-core-LICENSE.txt` 来自对应 PyObjC 源码的 `License.txt`。打包器另外收集实际 Python 依赖的 wheel 授权声明。
也可直接从配套源码附件的 `notices/` 取本版实际使用的许可文件（运行时在 `Python-runtime/`，PyObjC 在 `Python-packages/pyobjc-core-13.1/`）。

制作 DMG 时同时生成源码附件和摘要文件，不覆盖已有发行件。Windows 构建入口保持 `build.bat`。

发布者在可交互 Intel Mac 上运行 `python scripts/smoke_macos_dmg.py <生成的.dmg>`；验收使用临时 HOME 和独立钥匙串，不覆盖现有安装或下载记录。
