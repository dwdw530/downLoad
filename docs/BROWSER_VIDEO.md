# Chrome 视频下载助手

## 当前范围

本版是“网页视频识别 + 播放器悬浮下载按钮”，不是 Chrome 普通文件下载自动接管。

- 支持 HTTP/HTTPS 的 MP4、WebM 直链视频：从 HTML video/source 与浏览器媒体响应发现资源。
- 在播放器右上方显示“下载视频”；资源列表显示文件名、类型和可获取的大小。
- 点击列表中的“下载”，通过 Native Messaging 将任务发送到桌面下载器；未启动时自动启动，已启动时复用。
- 任务使用下载器设置中的默认目录，自动处理重名，复用多线程、暂停、续传、队列及历史记录。
- 按标签页、iframe 隔离资源；页面跳转、SPA 路由切换清理旧资源；提供总开关。
- YouTube 单视频页面、Shorts 和嵌入页使用 yt-dlp 下载，FFmpeg 合并为带声音的 MP4；扩展弹窗可选择最高 480p、720p、1080p，默认 720p，实际分辨率取决于源视频。
- YouTube 使用页面地址解析，不把 googlevideo 的零散片段当成完整文件。任务沿用桌面队列、暂停/续传和历史记录；不同清晰度可分别创建任务，同一请求重试不会重复创建。
- X/Twitter 公开视频帖子使用同一套 yt-dlp/FFmpeg 组件下载完整 MP4，支持 `/status/ID`、`/status/ID/video/1` 等单视频地址。保留视频序号与清晰度上限；有效的原生无声视频也允许完成。
- X 首页悬浮面板按播放器附近的帖子链接定位，只显示所属帖子，不混入整个页面的 `video.twimg.com` 片段。无法确认所属帖子时不提供下载，提示打开详情页；工具栏弹窗可列出已识别的帖子。
- 其他网站的 HLS/M3U8、DASH/MPD 和流式片段仍只识别提示，不支持通用分段下载。
- 检测到浏览器加密媒体事件或 mediaKeys 时，将该 frame 标记为受保护，不提供下载。不会绕过 DRM 或网站访问控制。

## 安装（Windows + Chrome）

1. 保持 `dist/老王下载器.exe`、`dist/BrowserBridge.exe`、`dist/chrome-extension/`、`dist/video-tools/` 在同一目录。不能只复制主 EXE，否则 YouTube/X 功能不可用。
2. 双击 `dist/install_browser_bridge.cmd`。此操作只在当前用户的 `HKCU\Software\Google\Chrome\NativeMessagingHosts\com.laowang.downloader` 注册桥接，不需要修改系统 PATH。
3. 打开 Chrome 的 `chrome://extensions/`，开启“开发者模式”，点击“加载已解压的扩展程序”，选择 `dist/chrome-extension/`。
4. 刷新原来已经打开的视频页面，播放视频，再点击播放器上的“下载视频”。

扩展固定 ID：`kpknpdpemebgalcfiolkcpaafddelcjj`。manifest 内的公钥仅用于稳定开发版 ID，不是私钥或访问密码。

无需安装 Python。扩展尚未发布到 Chrome 商店，现阶段使用开发者模式加载。浏览器自身或组织策略可能禁止开发者扩展，此时需由设备管理员允许。

安装命令也可以在发布目录执行：`BrowserBridge.exe --install`。

移动发布目录后，需要重新运行安装脚本更新绝对路径；同一 Windows 用户只注册一份当前安装。更新扩展文件后在扩展管理页点击“重新加载”，并刷新视频页面。

已有安装升级：完全退出桌面下载器（包括托盘），更新完整发布包，然后在 `chrome://extensions/` 对本扩展点击“重新加载”，刷新视频页面。仅关闭窗口到托盘不会加载新版代码。原路径不变时不必重装桥接。

Edge 使用 `edge://extensions/` 重新加载同一扩展。2026-10-05 已在旧程序退出后完成原 `dist` 的 X 新版重建和实网验收，正式入口为 `dist/老王下载器.exe` 与 `dist/chrome-extension/`。配置、下载记录和断点文件保持不变；`output/release-x/` 仅为早先独立验收包，不再作为交付入口。重启下载器后仍需重新加载扩展并刷新视频页面。

卸载：在 Chrome 移除扩展，再双击 `dist/uninstall_browser_bridge.cmd`。卸载注册不会删除下载记录、下载文件或断点数据。

## 权限和数据

- `webRequest`、HTTP/HTTPS 站点权限：识别当前页面的媒体响应，不拦截或取消浏览器请求。
- `webNavigation`：按页面与 frame 清理资源。
- `cookies`：仅在用户点击下载时读取目标视频 URL 对应 Cookie；不读取所有站点的 Cookie。分区 Cookie 暂不转发。
- YouTube/X 帖子路径不读取或导出浏览器账号 Cookie；需要登录、年龄验证、订阅或机器人验证的视频会失败并提示，不尝试绕过访问控制。
- `storage`：本地保存开关，浏览器会话内保存有数量与时间上限的媒体候选。列表 URL 可能含临时签名，不发送到任何第三方服务。
- `nativeMessaging`：只连接注册的本机桥接。桥接清单只允许上述固定扩展 ID。
- Cookie、Referer、User-Agent 以 Windows 当前用户 DPAPI 加密后写入任务数据库，支持重启续传；不能把这份凭据直接迁移给其他 Windows 用户。
- 桌面端使用仅监听 `127.0.0.1` 的随机端口和随机令牌；端点信息也使用 DPAPI 加密，拒绝带网页 Origin 或错误令牌的调用。
- 浏览器请求凭据仅发送给原始同源地址，跨域重定向不带 Cookie 和 Referer；HTTPS 降级被拒绝。
- 仅接受 HTTP/HTTPS 媒体任务，不执行扩展传来的命令、不自动打开下载文件。

## 已知边界

- `blob:` / MediaSource 本身不是可交给桌面下载器的文件 URL。只能列出同时发现的底层资源；分段或加密媒体不支持下载。
- 不处理通用 POST 下载、Authorization 特殊鉴权、分区 Cookie、直播录制；音视频分轨合并目前仅支持 YouTube/X 帖子路径。
- 普通直链下载要求服务器提供文件总大小。未提供大小、过期链接、登录失效、返回 HTML 登录页时，会拒绝创建直链视频任务。YouTube/X 的大小在解析下载过程中更新。
- YouTube 默认优先 H.264 + AAC；没有兼容格式时会失败，不会把缺音频的文件报告为下载完成。网络、地区和站点策略仍可能限制下载。
- 不把 XHR 返回的零散 MP4 片段作为完整视频自动收录；普通站点多播放器无法精确关联时显示该 frame 的候选列表，由用户选择。X 页面无法定位所属帖子时不回退到混杂资源列表。
- HTML video 位于封闭 Shadow DOM、浏览器原生 video 全屏或特殊播放器中时，悬浮按钮可能不可用；扩展工具栏仍可查看已经识别的资源。
- 只读取用户有权访问的资源；不是所有网站都可下载，也不承诺突破服务器限速。
- 关闭 Chrome 不会终止已被桌面端接收的下载。桌面端已经接收但浏览器回执丢失时，应先查看桌面任务列表。

## 构建和验证

```powershell
D:/anaconda3/envs/py310_env/python.exe -B scripts/setup_video_tools.py
D:/anaconda3/envs/py310_env/python.exe -B scripts/build_exe.py
D:/anaconda3/envs/py310_env/python.exe -B -m unittest discover -s tests -v
node --test tests/browser_extension.test.mjs
```

组件准备脚本从上游获取 yt-dlp、FFmpeg/ffprobe 和许可证，复制本机 Node 22+，记录在 `vendor/video/TOOLS.json`；下载组件只需准备一次。构建脚本先检查组件齐全，再生成两个 EXE，复制扩展、视频组件、许可证及安装/卸载脚本；不清空 `dist/data/` 或 `dist/temp/`。

旧 EXE 正在运行时应先完全退出。需独立构建可使用 `scripts/build_exe.py --dist-dir output/release-youtube`，不会替换正在使用的 `dist`。

真实 Chromium 扩展测试：`node scripts/smoke_browser.cjs <已安装的playwright包绝对路径>`。使用 Playwright 的 Chromium，在临时用户配置中加载真实扩展，本地生成可播放 WebM；截图和视频保存在 `output/playwright/`。

随后运行 `python -B scripts/smoke_browser_bridge.py`，在隔离目录测试打包后的 Native Messaging 协议、冷启动、登录视频字节校验、重复请求及单实例。此测试不写注册表。

普通下载 EXE 测试仍使用 `python -B scripts/smoke_exe.py`。

YouTube 实网验收：`python -B scripts/smoke_youtube.py "https://www.youtube.com/watch?v=ayl6TcSsre8&t=1s" --exe`。复制发布包到独立目录，真实运行桥接和桌面 EXE，下载、检查音视频流、全片解码并检查正常退出。测试不修改注册表、不读取用户 Chrome 配置；产物在 `output/youtube-exe-*/`。

X 实网验收复用该脚本：`python -B scripts/smoke_youtube.py "https://x.com/kyliaspeijcken/status/2106950444442595371/video/1" --exe --release output/release-x`。产物在 `output/x-exe-*/`。浏览器验收可追加 `--release output/release-x` 加载独立发布包中的真实扩展。

详见 [本轮测试记录](BROWSER_VIDEO_TEST_REPORT.md)。
