# Chrome 视频下载助手

## 当前范围

本版提供网页视频识别、播放器悬浮下载按钮，以及普通文件链接的手动右键下载入口。

- 支持 HTTP/HTTPS 的 MP4、WebM 直链视频：从 HTML video/source 与浏览器媒体响应发现资源。
- 在播放器右上方显示“下载视频”；资源列表显示文件名、类型和可获取的大小。
- 点击列表中的“下载”，通过 Native Messaging 将任务发送到桌面下载器；未启动时自动启动，已启动时复用。
- 任务使用下载器设置中的默认目录，自动处理重名，复用多线程、暂停、续传、队列及历史记录。
- 按标签页、iframe 隔离资源；页面跳转、SPA 路由切换清理旧资源；提供总开关。
- YouTube 单视频页面、Shorts 和嵌入页使用 yt-dlp 下载，FFmpeg 合并为带声音的 MP4；扩展弹窗可选择最高 480p、720p、1080p，默认 720p，实际分辨率取决于源视频。
- YouTube 使用页面地址解析，不把 googlevideo 的零散片段当成完整文件。任务沿用桌面队列、暂停/续传和历史记录；不同清晰度可分别创建任务，同一请求重试不会重复创建。
- X/Twitter 公开视频帖子使用同一套 yt-dlp/FFmpeg 组件下载完整 MP4，支持 `/status/ID`、`/status/ID/video/1` 等单视频地址。保留视频序号与清晰度上限；有效的原生无声视频也允许完成。
- X 首页悬浮面板按播放器附近的帖子链接定位，只显示所属帖子，不混入整个页面的 `video.twimg.com` 片段。无法确认所属帖子时不提供下载，提示打开详情页；工具栏弹窗可列出已识别的帖子。
- B 站普通公开视频支持 `/video/BV...`、`/video/av...` 和 `?p=N` 分 P，复用 yt-dlp/FFmpeg 合并成带声音的 MP4。无 p 时只下载第一 P，切换分 P 后重新识别，不批量下载合集；文件名带 P 序号。支持主站与移动站，短链接需先在浏览器跳转到视频详情页。
- 抖音普通视频支持 `www.douyin.com/video/ID`，以及在任意路径上（首页、精选、个人页弹窗）用单个 `modal_id` 指代当前视频的链接。优先使用与当前播放器明确关联的视频信息；详情页仍可复用当前网页已发出的同源详情请求。桌面端验证视频 ID、CDN 地址、音视频轨和总时长后才报告完成。
- 抖音推荐页（`/?recommend=1` 等）通过页面播放器对象取得视频 ID 和清晰度地址，要求该对象绑定的 `<video>` 正是用户操作的可见播放器，支持旧版 `config.awemeInfo` 和新版播放器插件配置。推荐流不依赖详情请求，也不按时长、分辨率或请求顺序猜视频。下载时优先访问该视频提供的同源播放接口，取得实际签名 CDN 地址，避免直接使用备用地址导致 403。悬浮面板、工具栏和发送前都复核当前视频；拿不到明确关联时提示先播放或完整刷新页面。
- 小红书视频笔记详情页（`xiaohongshu.com` 或 `www.xiaohongshu.com` 下的 `/explore/<24位id>` 与 `/discovery/item/<id>`，含 SPA 弹层）通过页面状态读取官方播放器实际使用的签名 MP4（排除小程序水印流），桌面端校验笔记 ID、CDN 域名、编码、分辨率与总时长后经 `--load-info-json` 交给 yt-dlp 下载合并。图文笔记、LivePhoto、`xhslink.com` 短链、无水印原画与批量下载不在支持范围。签名地址过期时需重新打开笔记页。
- 弹窗的“视频清晰度”统一用于 YouTube、X、B 站、抖音、小红书和 HLS/DASH，最高 480p/720p/1080p 是上限，实际以源格式为准，不执行降分辨率转码。
- 通用 HLS/M3U8、DASH/MPD 播放列表已接入下载；未知媒体流、孤立分片仍禁用。通用流媒体的实网验收范围见测试记录。
- 检测到浏览器加密媒体事件或 mediaKeys 时，将该 frame 标记为受保护，不提供下载。不会绕过 DRM 或网站访问控制。
- 普通网页链接增加手动入口：对链接点击右键，选择“使用 daw 下载器下载此链接”后，才把该 HTTP/HTTPS 链接作为普通文件交给桌面下载器；普通点击、另存为和浏览器自带下载保持原样，不做全局自动接管。该入口独立于“识别视频”开关，普通文件不进入视频资源列表、不占用视频 badge，也不绕过已有的平台视频校验。
- **版本要求**：手动右键入口随桌面版 v0.7.0、扩展 0.6.0 交付，需要同时更新主程序、BrowserBridge 和扩展。旧版桥接缺少文件下载能力时明确提示升级，完整验证见[链接下载测试报告](BROWSER_LINK_TEST_REPORT.md)。

## 安装（Windows + Chrome）

1. 保持 `dist/daw下载器.exe`、`dist/BrowserBridge.exe`、`dist/chrome-extension/`、`dist/video-tools/` 在同一目录。不能只复制主 EXE，否则 YouTube/X/B 站功能不可用。
2. 双击 `dist/install_browser_bridge.cmd`。此操作只在当前用户的 `HKCU\Software\Google\Chrome\NativeMessagingHosts\com.laowang.downloader` 注册桥接，不需要修改系统 PATH。
3. 打开 Chrome 的 `chrome://extensions/`，开启“开发者模式”，点击“加载已解压的扩展程序”，选择 `dist/chrome-extension/`。
4. 刷新原来已经打开的视频页面，播放视频，再点击播放器上的“下载视频”。

扩展固定 ID：`kpknpdpemebgalcfiolkcpaafddelcjj`。manifest 内的公钥仅用于稳定开发版 ID，不是私钥或访问密码。

无需安装 Python。扩展尚未发布到 Chrome 商店，现阶段使用开发者模式加载。浏览器自身或组织策略可能禁止开发者扩展，此时需由设备管理员允许。

安装命令也可以在发布目录执行：`BrowserBridge.exe --install`。

移动发布目录后，需要重新运行安装脚本更新绝对路径；同一 Windows 用户只注册一份当前安装。更新扩展文件后在扩展管理页点击“重新加载”，并刷新视频页面。

已有安装升级：完全退出桌面下载器（包括托盘），更新完整发布包，然后在 `chrome://extensions/` 对本扩展点击“重新加载”，刷新视频页面。仅关闭窗口到托盘不会加载新版代码。原路径不变时不必重装桥接。

**刷新要覆盖所有已经打开的视频网站标签页。** 在各标签页按 F5 或 Ctrl+F5；X 首页内点击帖子、切换视频属于站内跳转，不能代替完整刷新。扩展更新后旧页面脚本可能失效，出现悬浮按钮消失，而帖子详情页的工具栏仍能列出视频的现象。2026-10-08 用户已确认 X 首页完整刷新后图标恢复；X 的首页下载能力保留，不要求逐条进入详情页。

2026-10-13 抖音推荐流更新：扩展升级到 **0.5.2**，源码和正式 `dist/chrome-extension/` 已同步。重新加载扩展后必须完整刷新推荐页，才能加载新增的抖音页面脚本。本次仅更新扩展，桌面程序和桥接协议未改，无需重打包 EXE。源码及正式扩展的 Chromium 回归通过；用户真实推荐页下载、播放成功，成品核对为 1920×1080 HEVC + AAC、246.410 秒。旧失败任务保存旧地址，应从插件重新添加，完整说明见 [插件更新日志](releases/extension-v0.5.2.md)。

2026-10-08 抖音更新：原 `dist` 已重新构建双 EXE 并同步扩展 0.5.0，配置、下载记录及已有断点文件保留。正式包实际完成用户提供的抖音视频下载，982×720 H.264 + AAC、580.868934 秒，整片解码及正常退出通过。更新后重新加载扩展并刷新抖音详情页，点击下载可自动启动下载器。

2026-10-06 B 站更新：原 `dist` 已完成完整重建和验收，扩展版本为 0.3.0；正式包实际下载第一 P 1080p、音视频合并及全片解码通过，用户配置、数据库和原桥接注册路径保留。`output/release-bilibili/` 只是早期独立验收目录，不再作为交付入口。最新指纹和验证边界见测试记录顶部。

注意：Chrome 连接的是注册项指定的 `BrowserBridge.exe` 及其同目录下载器，不会自动寻找任意目录中已经运行的 EXE。只启动 `output` 下的测试包而不更新正式目录，不能修复浏览器仍连接旧 `dist` 的问题；页面刷新也不会改变桥接注册路径。

Edge 使用 `edge://extensions/` 重新加载同一扩展。2026-10-05 已在旧程序退出后完成原 `dist` 的 X 新版重建和实网验收，正式入口为 `dist/daw下载器.exe` 与 `dist/chrome-extension/`。配置、下载记录和断点文件保持不变；`output/release-x/` 仅为早先独立验收包，不再作为交付入口。重启下载器后仍需重新加载扩展并刷新视频页面。

卸载：在 Chrome 移除扩展，再双击 `dist/uninstall_browser_bridge.cmd`。卸载注册不会删除下载记录、下载文件或断点数据。

## 权限和数据

- `webRequest`、HTTP/HTTPS 站点权限：识别当前页面的媒体响应，不拦截或取消浏览器请求。
- `webNavigation`：按页面与 frame 清理资源。
- `cookies`：仅在用户点击下载时读取目标视频或文件 URL 对应 Cookie；不读取所有站点的 Cookie。分区 Cookie 暂不转发。
- 抖音仅使用当前标签页所在 Cookie store 的授权信息，过滤分区及空名称 Cookie；无法确认当前 store 时拒绝提交。抖音专用 MAIN 脚本只向隔离内容脚本提供绑定播放器的 ID、时长和格式地址，不调用下载器、不读取 Cookie、不修改网站的 fetch/XHR。详情请求仍按同源规则执行；读取授权信息后、发送任务前再次复核当前视频和保护状态。
- 小红书专用 MAIN 脚本只读取页面已注入的 `__INITIAL_STATE__`（不做 JSON 往返、不 patch fetch/XHR、不发网络请求、不写 storage），把当前可见播放器对应笔记的官方签名 MP4 候选交给内容脚本；授权信息与抖音同样按当前 Cookie store 范围读取并在发送前复核。
- 播放地址解析仅请求播放器提供的同源 `/aweme/v1/play/`，携带当前页面的同源授权；`Range: bytes=0-0` 请求在收到响应头后取消响应体。只接受最终 HTTPS 抖音 CDN 的完整 MP4 地址，跨域不转发站点 Cookie，视频整体仍由桌面端下载和校验。
- YouTube/X/B 站页面路径不读取或导出浏览器账号 Cookie；需要登录、年龄验证、订阅或机器人验证的视频会失败并提示，不尝试绕过访问控制。
- `storage`：本地保存开关，浏览器会话内保存有数量与时间上限的媒体候选。列表 URL 可能含临时签名，不发送到任何第三方服务。
- `contextMenus`、`notifications`：注册唯一的链接右键菜单（固定 ID `daw-download-link-v1`）并反馈发送结果。只有用户选择菜单后才读取目标链接所在标签页与 frame 的授权信息；弹窗状态与通知只保留脱敏结果（成功/失败、脱敏文件名、请求 ID、时间），不写 URL、Cookie、Referer 或请求头，也不随关闭“识别视频”时的会话清理消失。
- `nativeMessaging`：只连接注册的本机桥接。桥接清单只允许上述固定扩展 ID。
- Cookie、Referer、User-Agent 以 Windows 当前用户 DPAPI 加密后写入任务数据库，支持重启续传；不能把这份凭据直接迁移给其他 Windows 用户。
- 抖音的签名视频地址同样保存在加密请求上下文中。运行下载子进程时才生成受控字段的临时格式 JSON 和 Cookie 文件，完成、失败及取消后清理；不会把账号信息或签名地址写入错误提示。
- 桌面端使用仅监听 `127.0.0.1` 的随机端口和随机令牌；端点信息也使用 DPAPI 加密，拒绝带网页 Origin 或错误令牌的调用。
- 浏览器请求凭据仅发送给原始同源地址，跨域重定向不带 Cookie 和 Referer；HTTPS 降级被拒绝。
- 仅接受 HTTP/HTTPS 视频或普通文件任务，不执行扩展传来的命令、不自动打开下载文件。

## 已知边界

- `blob:` / MediaSource 本身不是可交给桌面下载器的文件 URL。YouTube/X/B 站/抖音使用页面路径；其余网站需要发现 MP4/WebM 直链或 HLS/DASH 播放列表。
- 不处理通用 POST 下载、Authorization 特殊鉴权、分区 Cookie、直播录制；音视频分轨合并目前仅支持 YouTube/X/B 站页面路径（抖音与小红书由浏览器直接提供已签名完整 MP4，无需合并分轨）。
- B 站番剧、课程、付费/登录受限视频、直播与整合集批量下载不在本次范围内，不保证匿名可取得 1080p。
- 普通直链下载要求服务器提供文件总大小。未提供大小、过期链接、登录失效、返回 HTML 登录页时，会拒绝创建直链视频任务。YouTube/X/B 站的大小在解析下载过程中更新。
- YouTube 默认优先 H.264 + AAC；没有兼容格式时会失败，不会把缺音频的文件报告为下载完成。网络、地区和站点策略仍可能限制下载。
- 不把 XHR 返回的零散 MP4 片段作为完整视频自动收录；普通站点多播放器无法精确关联时显示该 frame 的候选列表，由用户选择。X 页面无法定位所属帖子时不回退到混杂资源列表。
- 抖音目前要求存在完整 MP4 条目和有效的音视频轨；不处理只有分轨资源、图集、持续直播、受保护视频或批量作品。签名地址可能过期，届时需刷新当前视频页重新提交。已通过的视频不代表所有抖音作品均可下载。
- 小红书目前要求笔记状态里存在官方播放器签名 MP4 候选和有效时长；不处理图文笔记、LivePhoto、小程序水印流、短链、合集、直播与无水印原画。`xsec_token` 等来源签名只留在浏览器内，不写入任务 URL、数据库或日志。
- HTML video 位于封闭 Shadow DOM、浏览器原生 video 全屏或特殊播放器中时，悬浮按钮可能不可用；扩展工具栏仍可查看已经识别的资源。
- 只读取用户有权访问的资源；不是所有网站都可下载，也不承诺突破服务器限速。
- 手动右键发送仅支持 HTTP/HTTPS 的普通 GET 文件，复用现有受限的 Cookie/Referer/User-Agent 转发；不支持 POST、特殊 Authorization、分区 Cookie 及 `blob:`/`data:`/`javascript:`/`file:`/FTP 链接。明确拒绝 HTML/XHTML 登录页、未知或零字节大小、HLS/DASH 清单（含 `.m3u8`/`.mpd` 与对应 MIME）及抖音专用 CDN（含重定向目标），这些路径应改用既有视频入口。签名地址可能过期，发送超时属于结果未知，不自动重发，需以桌面任务列表为准。
- 关闭 Chrome 不会终止已被桌面端接收的下载。桌面端已经接收但浏览器回执丢失时，应先查看桌面任务列表。

## 构建和验证

```powershell
D:/anaconda3/envs/py310_env/python.exe -B scripts/setup_video_tools.py
D:/anaconda3/envs/py310_env/python.exe -B scripts/build_exe.py
D:/anaconda3/envs/py310_env/python.exe -B -m unittest discover -s tests -v
node --test tests/browser_extension.test.mjs
```

抖音实网验收：`node scripts/smoke_douyin.cjs <已安装的playwright包绝对路径> <python.exe绝对路径>`；追加 `--exe` 验证原 `dist` 构建出的双 EXE。浏览器使用临时配置，真实打开用户指定的验收视频并通过扩展取得消息，随后把相同视频信息通过标准输入传给隔离下载进程；不调用用户注册的下载器执行测试任务，不修改其历史记录。验证音视频、时长、整片解码及正常退出。

小红书实网验收：`XHS_NOTE_URL=<笔记详情页> node scripts/smoke_xhs.cjs <已安装的playwright包绝对路径> <python.exe绝对路径>`，结构与抖音 smoke 相同：临时 profile、未打包扩展、捕获 native 消息、隔离进程下载、不调用用户注册的下载器、不写用户历史；需登录态，未登录或已删除笔记应得到明确失败文案。

组件准备脚本从上游获取 yt-dlp、FFmpeg/ffprobe 和许可证，复制本机 Node 22+，记录在 `vendor/video/TOOLS.json`；下载组件只需准备一次。构建脚本先检查组件齐全，再生成两个 EXE，复制扩展、视频组件、许可证及安装/卸载脚本；不清空 `dist/data/` 或 `dist/temp/`。

旧 EXE 正在运行时应先完全退出。需独立构建可使用 `scripts/build_exe.py --dist-dir output/release-youtube`，不会替换正在使用的 `dist`。

真实 Chromium 扩展测试：`node scripts/smoke_browser.cjs <已安装的playwright包绝对路径>`。使用 Playwright 的 Chromium，在临时用户配置中加载真实扩展，本地生成可播放 WebM；截图和视频保存在 `output/playwright/`。

本次打包：`python -B scripts/package_release.py v0.7.0`，在 `output/releases/v0.7.0/` 生成完整便携版 ZIP、独立的 `daw-browser-extension-v0.6.0.zip` 和 `SHA256SUMS.txt`。脚本核对源码与 `dist` 扩展逐文件一致，ZIP 版本取自 manifest；本次新功能还需同步升级桌面程序与桥接。

随后运行 `python -B scripts/smoke_browser_bridge.py`，在隔离目录测试打包后的 Native Messaging 协议、冷启动、登录视频字节校验、重复请求及单实例。此测试不写注册表。

普通下载 EXE 测试仍使用 `python -B scripts/smoke_exe.py`。

YouTube 实网验收：`python -B scripts/smoke_youtube.py "https://www.youtube.com/watch?v=ayl6TcSsre8&t=1s" --exe`。复制发布包到独立目录，真实运行桥接和桌面 EXE，下载、检查音视频流、全片解码并检查正常退出。测试不修改注册表、不读取用户 Chrome 配置；产物在 `output/youtube-exe-*/`。

X 实网验收复用该脚本：`python -B scripts/smoke_youtube.py "https://x.com/kyliaspeijcken/status/2106950444442595371/video/1" --exe --release output/release-x`。产物在 `output/x-exe-*/`。浏览器验收可追加 `--release output/release-x` 加载独立发布包中的真实扩展。

B 站正式包实网验收：`python -B scripts/smoke_youtube.py "https://www.bilibili.com/video/BV1VbYk6FE6c/?p=1" --exe --height 1080`，产物在 `output/bilibili-exe-*/`。`--height` 可选 480/720/1080，默认 720；省略 `--exe` 可验证源码任务链路，默认 `--release` 为原 `dist`。

详见 [本轮测试记录](BROWSER_VIDEO_TEST_REPORT.md)。
