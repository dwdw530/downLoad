# 浏览器视频集成测试记录

## 当前发布补充：daw 改名与图标

2026-10-05：用户将产品名称改为 `daw下载器`。当前正式入口为 `dist/daw下载器.exe`，下文旧名称 EXE 指纹仅为历史记录。

- 图标根因：旧 spec 仅为 EXE 设置 icon，没有将图标加入运行时 datas；托盘找不到资源时绘制纯蓝方块。现将 ICO/PNG 打入包，冻结环境按 `_MEIPASS` 查找，并配置窗口图标及 Windows AppUserModelID。备用托盘图标也具有下载箭头。
- 托盘、窗口、通知、关闭提示、扩展显示名、桥接启动文件名和安装检查统一更新；Native Host 名称和扩展 ID 保持兼容，既有用户下载目录不迁移。
- 74 项 Python 回归通过（含 3 项新图标测试）；正式 EXE 从无 assets 的独立目录启动并正常退出，大/小窗口图标句柄通过，截图 `C:/Users/lenovo/AppData/Local/Temp/downloader-exe-smoke-fqtlhqqa/main-window.png`。EXE 内 ICO/PNG 与源资源逐字节一致。
- 首次窗口断言只检查 WM_GETICON，未考虑 Tk 默认图标挂在窗口类上，导致假失败；补上 GetClassLongPtrW 检查后通过，实际截图已显示新图标。
- 正式发布扩展全套 Chromium 回归通过；新桥接冷启动 `daw下载器.exe` 下载用户 X 视频成功，720p H.264/AAC、全片解码、去重和正常退出通过，产物 `output/x-exe-43e4e915/`。
- 主 EXE SHA256：`b0693958feb16f0ee814736613243be0e9623c288ac36524acaaab660e92e70c`；桥接 SHA256：`b1376b238099259042336789480acb42f42235b6f5d2471c5a2f77a6b06d9c07`。
- 旧名称 EXE 未自动删除；用户应使用新名称文件，旧固定快捷方式需改指向新入口。未修改用户注册表、下载记录或断点文件；未声称已人工核对用户 Explorer 中的托盘缓存。

## 当前验收：X 帖子下载

日期：2026-10-05（Asia/Shanghai）。接续聊天 `01a10acb-8ce7-7a73-82e8-5a8a4792b703`。

### 问题与修复

- 用户在 Edge 的 X 首页看到大量 `video.twimg.com` 流式资源及 M3U8，按钮全部禁用。根因是仅有 YouTube 页面解析，X 仍走通用分段媒体禁用路径；同 frame 的多个帖子资源还会混在一起。
- 增加 X/Twitter 单帖子 URL 白名单与视频序号校验，复用现有 yt-dlp 工作线程、代理、限速、清晰度、队列、暂停/续传和持久化路径。不新增数据库列，不读取 X 账号 Cookie。
- 内容脚本从播放器附近定位帖子；后台复核发送 frame 属于 X 后创建页面下载项。未知或歧义归属不回退到全页媒体列表；重用播放器节点时点击会重新识别，不提交旧帖子。
- Native Host 和桌面端声明 `x` 能力，旧下载器会收到更新提示，不误发不支持的任务。重新打开面板清空旧回执。

### 验证结果

| 验证项 | 结果 |
| --- | --- |
| Python 全量 | 71 项通过，79.607 秒；覆盖 X URL、无声视频、桥接去重、暂停、重启与取消 |
| Node 逻辑 | 11 项通过；X/Twitter 规范化、视频序号、危险协议/主机/端口拒绝 |
| 实际发布扩展 | Playwright Chromium 独立配置加载 `output/release-x/chrome-extension`；桌面 1280x900、窄视口 390x844、真实 popup、两个帖子分流、清晰度、旧桌面提示、播放器复用/未知归属通过 |
| 既有浏览器路径 | 同一轮直链、HLS 禁用、iframe、blob 诊断、YouTube 清晰度/SPA/DRM 回归通过 |
| 源码 X 实网 | 用户链接已完成，`output/x-validation-61b1f411/` |
| 新包 X 实网 | 独立双 EXE 冷启动、重复消息只建一条任务、720p 下载、全片解码、正常退出通过 |
| 视频内容 | H.264 1280x720 + AAC；46.021950 秒，3983395 字节；FFmpeg 解码退出 0、stderr 为空 |

用户验收链接：`https://x.com/kyliaspeijcken/status/2106950444442595371/video/1`。
实测文件：`output/x-exe-719c24b2/downloads/X-2106950444442595371-video-1.mp4`。
SHA256：`7b842f0e8f85f1d1889a19026d4f04255b2f04024685a6c910b86427c4fbf870`。
截图：`output/playwright/x-desktop.png`、`x-mobile.png`、`x-popup.png`；`output/x-exe-719c24b2/x-completed.png`。

### 发布状态与边界

- 新完整包：`output/release-x/`。主 EXE SHA256 `19aaa091431834e4748ed725956eabe2ca4e04a2df899f8b8a426491a980d75a`；桥接 EXE SHA256 `9a800db8238a19ca8f76d563f454f14cf722e3d5403673ba40fc059d61d45090`。
- 首次因原 `dist` 的用户程序仍运行，直接构建返回 WinError 5，只完成独立包。用户反馈原目录仍不可用后，核对哈希确认其仍为旧版；在程序已退出的情况下，已成功重建原 `dist`，没有强杀用户程序或修改注册表。
- 当前正式入口为 `dist`：主 EXE SHA256 `7feac93482f1704fd0bcf0e832af585b4eed7ad95c9d32dd48751effefd4c9e4`；桥接 EXE SHA256 `d82694ed7e12bcb42bd4f25c93798f6a1e30f88d25414f1233e73cd5bf363b62`。
- 已从本次 `dist` 复制双 EXE 和组件到隔离目录，真实冷启动、下载同一 X 视频、重复请求去重、音视频检查、全片解码和正常退出全部通过。产物：`output/x-exe-7643aa78/downloads/X-2106950444442595371-video-1.mp4`，1280x720 H.264/AAC、46.021950 秒、3983395 字节。
- 使用 `--dist` 加载正式扩展的完整浏览器回归通过，扩展与源码逐文件哈希一致。构建和测试前后 `dist/data/`、`dist/temp/` 全部现有文件哈希不变，包括配置、数据库及 YouTube 断点。用户仍需在 Edge 重新加载扩展并刷新视频页面。
- 初次浏览器调用的 Playwright 与缓存浏览器不匹配，改用本机已安装的匹配版本。新增测试首轮误用隐藏关闭按钮的序号导致超时，修正测试选择器后源码及发布扩展均通过。
- GUI 测试仍出现既有 CustomTkinter `bad window path name` 打印，测试结果通过；未扩展修改第三方库。
- 浏览器验收使用构造的 X 多帖子页面与模拟 Native Messaging；实际用户链接由真实 Native Host/EXE 下载验收。未声称已自动操作用户 Edge 登录会话或验证 Edge 注册表到本地桥接的完整链路。
- 本轮未重新进行 YouTube 长视频实网和普通 EXE 全工作流验收；它们的历史结果见下文。非公开、登录受限、直播、DRM 与通用 HLS/DASH 下载仍不支持。

## 历史：YouTube 下载与完整发布包

日期：2026-10-05（Asia/Shanghai）。接续原会话 `01a10997-3059-7060-b8de-a61f502048e8`。

### 问题与修复

- 原先只有 MP4/WebM 直链下载，识别到 YouTube 流式片段不等于能下载完整视频。原会话后来已接入 yt-dlp/FFmpeg，后台源码下载成功，但未完成发布包和浏览器交互验收。
- 本轮完成视频组件打包：构建前检查四个可执行组件及许可证，复制到 `dist/video-tools/`；缺件或空文件则在覆盖 EXE 前失败，防止发布不可用的半套更新。
- 浏览器实测发现 SPA 切换后旧面板未收起，`MessageSender.url` 还可能保留旧页面地址。改为从 `webNavigation.getFrame` 获取当前页面，过滤旧视频候选并清理旧面板；异步旧结果不能回写新页面。
- 同视频不同清晰度使用不同幂等请求 ID，避免 480p 请求被之前 720p 的回执吞掉；相同请求重试仍去重。
- 首次原目录构建因旧 EXE 正在运行出现 WinError 5。先构建独立目录并验收；旧进程退出后已重新成功构建原 `dist`，没有强制终止用户程序。

### 验证结果

| 验证项 | 结果 |
| --- | --- |
| Python 全量回归 | 68 项通过，81.552 秒，包含新增 10 项 YouTube/发布包测试 |
| 扩展逻辑 | Node 原生测试 10 项通过 |
| 发布目录扩展实测 | Playwright 加载实际 `dist/chrome-extension`，桌面 1280x900 与窄视口 390x844 通过；YouTube 页面消息、480p/720p 设置、同页不同清晰度、SPA 视频 ID、受保护媒体禁用通过 |
| 正式 EXE 实网下载 | 从 `dist` 复制双 EXE 和 video-tools 到独立目录，原生消息冷启动 GUI，真实下载用户提供视频、重复请求去重、完成后正常退出 |
| 下载内容 | `ayl6TcSsre8`，1280x720 H.264 + AAC，1370.023764 秒，182709980 字节；FFmpeg 全片解码退出 0，stderr 为空 |
| 普通下载 EXE 回归 | 重跑通过：真实按钮、暂停/继续、重启恢复、队列、Range/非 Range、SHA256、历史记录、正常退出；stderr 为空 |
| 原直链桥接回归 | 正式双 EXE 冷启动、Cookie 视频字节校验、请求去重、单实例、退出通过 |
| 发布一致性 | 双 EXE 中 8 个关键模块与当前源码编译结果一致；扩展及全部视频组件与源文件逐字节一致 |
| 用户数据 | 原 `dist/data/config.json`、`downloads.db` 构建与测试前后 SHA256 相同；未清理断点数据 |

正式 EXE 实测视频：`output/youtube-exe-bcdfc178/downloads/YouTube-ayl6TcSsre8.mp4`。
SHA256：`74eda3061be7972842da23de433abb9db8f9d93dc40fc8a3f052b1118975e744`。
截图：`output/youtube-exe-bcdfc178/youtube-completed.png`、`output/playwright/youtube-desktop.png`、`youtube-mobile.png`、`youtube-popup.png`。

原始日志：Python `C:/Users/lenovo/.fastctx/jobs/j-q8bcxg/output.log`；发布扩展 `j-21izvv/output.log`；正式 EXE YouTube `j-vtsoj6/output.log`（后二者同在 `.fastctx/jobs/`）。

普通 EXE 重跑产物：`C:/Users/lenovo/AppData/Local/Temp/downloader-exe-smoke-3hjeru7g/`，日志 `C:/Users/lenovo/.fastctx/jobs/j-zx0hil/output.log`；直链桥接产物：`C:/Users/lenovo/AppData/Local/Temp/browser-bridge-exe-vv95x48u/`。

普通 EXE 首轮出现窗口交互超时：重启后自动点击预期的“继续全部”却观察到“下载历史”窗口，等待提示框超时。应用 stderr 为空；不改代码重跑通过。未确认是指针/焦点竞争还是测试定位时序，不能据此声称产品存在下载失败，也不能把首轮算成通过。保留失败日志 `C:/Users/lenovo/.fastctx/jobs/j-7xfmpm/output.log`。

### 当前发布文件

- `dist/老王下载器.exe`，20551269 字节，SHA256 `fdf5a70908cd939a38a2638a0e96c2203b6eb9079dbabc2a3768d4d259818560`。
- `dist/BrowserBridge.exe`，9965892 字节，SHA256 `033c9d18b5a8064c1573382fbaeadbed85dffa587e7b2881006a45e44c803ebe`。
- `dist/chrome-extension/`、`dist/video-tools/` 及安装/卸载脚本必须随包保留。
- 实际工具版本：yt-dlp `2026.08.19`；FFmpeg `N-127203-ga35c879992-20261004`；Node `v22.23.2`。本轮未声称它们为最新版本。
- 本轮保留的用户配置 SHA256：`5ba43dbf67c6b67da71bb7e7920fc81e112c1e61f2583a4a0d31691eb8f925ad`；数据库：`840d7a753dcc4f5da1529d33722eaedb42f43c75102f910ac8c55bde00944846`。

### 验证边界

- 实际用户 Chrome 配置未被自动修改或重新加载；用户需要重启下载器、在扩展管理页重新加载原扩展并刷新视频页。已有桥接注册路径未改变。
- 浏览器 UI 测试在独立 Chromium 配置中拦截 Native Messaging，断言发送内容；EXE 测试直接使用原生消息协议测试真实桥接和下载。没有把这两段测试冒充用户 Chrome 注册表启动的完整实测。
- 用户指定视频已实网完成 720p 下载。480p/1080p 的参数与清晰度选择已测，不代表所有站点、所有视频和清晰度均已实网验收。
- 不支持 DRM、直播录制、需要账号验证的视频及其他网站通用 HLS/DASH 合并；不读取 YouTube 账号 Cookie。

## 历史：首版直链视频验收

以下为同日早先 MP4/WebM 首版的记录；产物指纹和“不支持音视频合并”等边界以以上当前验收为准。

### 首版验证

| 验证项 | 结果 |
| --- | --- |
| Python 全量回归 | 58 项通过，81.365 秒；包含 12 项浏览器集成测试及新增 GUI 生命周期测试 |
| 扩展纯逻辑 | Node 原生测试 5 项通过：类型识别、危险协议/片段排除、去重、frame 隔离、数量/时间上限 |
| 真实 Chromium 扩展 | 通过：加载实际 Manifest V3 扩展、播放生成的 WebM、网络识别、播放器资源关联、缺少 Native Host 时显示可重试错误 |
| 扩展视图与状态 | 通过：1280x900 与 390x844、悬浮面板边界、启停开关、真实 popup 页面、HLS 不可下载状态、SPA 清理、iframe 播放器；无页面异常 |
| 打包后视频链路 | 通过：直接向 BrowserBridge.exe 写入 Chrome 格式的原生消息，冷启动 GUI、携带 Cookie 下载真实 WebM、逐字节比对、重复消息去重、单实例、正常关闭 |
| 原有 EXE 工作流 | 通过：实际按钮、暂停/继续、关闭重启、队列、Range/非 Range、SHA256、历史记录、隔离数据路径和正常退出 |
| 打包一致性 | 主程序相关模块、桥接模块与当前源码编译代码一致；发布目录扩展与源码文件逐字节一致 |
| 数据保护 | 构建、测试前后原 dist/data/config.json 与 downloads.db 的 SHA256 不变 |
| 补丁检查 | git diff --check 通过；仅有仓库 LF/CRLF 提示 |

浏览器使用 Playwright Chromium 146.0.7680.0 独立测试配置，不使用用户的 Chrome 配置。Python 使用既有 py310_env（3.10.14），PyInstaller 6.17.0。测试和安装脚本没有新增运行时 Python 依赖。

截图：`output/playwright/video-desktop.png`、`video-mobile.png`、`video-popup.png`。测试 WebM：`output/playwright/sample.webm`。

最后一次双 EXE 视频测试产物：`C:/Users/lenovo/AppData/Local/Temp/browser-bridge-exe-floffrrq/`。

最后一次原有 EXE 工作流产物：`C:/Users/lenovo/AppData/Local/Temp/downloader-exe-smoke-2nyah2hh/`。

最终全量回归原始日志：`C:/Users/lenovo/.fastctx/jobs/j-uwdpgv/output.log`。

## 本轮发现并处理的问题

### 悬浮按钮偏出视口

现象：真实浏览器定位到按钮，但按钮位于视口外，无法点击。

原因：Shadow DOM 宿主上的 `all: initial !important` 重置了外部设置的位置；宿主隐藏状态也需要显式样式。

处理：移除全属性重置的重要标记，保留必要属性隔离；增加宿主 hidden 规则、窄视口面板边界和打开时的层级处理。

验证：桌面与窄视口实际点击、截图和边界断言通过。

### 视频列表 popup 的标签页选择

现象：独立打开扩展 popup 页面时，列表错误地使用 popup 自己的 tab ID。

处理：仅对本扩展固定 popup URL 信任其传入的目标 tab ID；网页内容脚本继续强制使用 sender.tab/frame。

验证：真实 popup 页面可显示目标视频及 HLS 不可下载状态。

### 桥接唤起后 GUI 退出

现象：首轮打包联调中，桥接等待 GUI 超时；GUI 窗口创建后退出。

根因：当前机器在本地用户目录发布端点文件时，`os.replace` 返回 WinError 17，即使源和目标路径位于同一目录。

处理：仅对跨设备错误增加同目录直接写入、flush/fsync 的兼容分支；写完后才启动监听，启动中的读取由客户端重试。普通文件下载不再因端点创建的 OSError 而直接退出。

验证：加入模拟跨设备错误测试，重新打包后实际冷启动、视频下载和退出通过。

### 对话框销毁后的延迟回调

现象：全量 GUI 测试间歇出现 AddTaskDialog 标题栏恢复、CloseConfirmDialog 焦点恢复访问已销毁窗口的 Python 回调异常。

处理：沿用现有窗口存活检查，覆盖标题栏恢复和焦点恢复；Tk 的 `focus` 是 `focus_set` 的别名，两者都需要指向受保护的方法。

验证：增加销毁后直接执行延迟回调的测试，最终 58 项回归通过。全量测试输出仍偶有 CustomTkinter 自行打印的 `bad window path name` 文本，但已无被测试捕获的 Python 回调异常；真实 EXE 工作流 stderr 为空。未扩大到第三方库内部修订。

## 发布文件

- `dist/老王下载器.exe`
  - SHA256：`613a3a593b3cc885b8593b4c6164951e0a53a9ae0fbbb1f41150a66307add46f`
- `dist/BrowserBridge.exe`
  - SHA256：`0d3fafe4628227fdb1c406995c8bfd29eb50801959b9d7303285995337f65e83`
- `dist/chrome-extension/`
- `dist/install_browser_bridge.cmd`
- `dist/uninstall_browser_bridge.cmd`

受保护的原用户文件 SHA256：

- config.json：`9e9b0308eb04247285339145c7c50c5f4fdf208a080f215838057ba76c54d900`
- downloads.db：`972d9822be472227021cb501ae54b2492058bfdc45577ccddd3512d55cc23c93`

## 未验证与不支持

- 尚未向用户 Chrome 安装扩展，也没有执行注册表注册。因此“实际 Chrome 根据注册表拉起 Native Host”需要安装后验证；本轮分别验证了真实扩展和实际 Native Host 到桌面 EXE 的链路。
- 尚未验证用户实际网站、特定站点反盗链策略及长期使用。
- 不支持 HLS/DASH 分段下载与合并、直播、DRM、分区 Cookie 和特殊 Authorization 鉴权。
- 已实现的是第一版视频方向，不是完整 IDM，也没有自动接管普通文件下载。

安装与权限说明见 [BROWSER_VIDEO.md](BROWSER_VIDEO.md)。
