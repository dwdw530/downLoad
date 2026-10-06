# 浏览器视频集成测试记录

## 当前状态：通用 HLS/DASH 待完整验收

2026-10-06：新增通用流媒体源码与扩展 0.4.0，用户本轮自行打包。存档时最新已有提交 `98f559d` 包含双 EXE 和 dist 扩展更新，不能继续称 dist 未更新；本轮未运行新 EXE 或完成真实流媒体下载验收。

- 新增流媒体 Python 单测 4 项通过（模拟下载进程）；JavaScript 13 项通过。
- 先前全量 Python 81 项中 2 项失败：旧 HLS 拒绝断言后来已修改但未重跑；GUI 暂停速度测试建任务时发生 HTTP 超时，仍待调查。不能将新增单测通过等同于全量回归通过。
- Playwright 启动失败：缺少其期望的 chromium-1234；仅发现本机 chromium-1212。本轮浏览器回归、无痕模式、HLS/DASH 真实下载合并、暂停恢复与正式 EXE 均未验收。
- 详细改动、尚未修正的浏览器文案断言及下一步见 `docs/context-sessions/browser-video/20261005-1455-YouTube下载修复与完整发布.md`。下列 B站/YouTube/X 结果为历史验证，不覆盖本轮新格式。

## 历史验收：B 站普通视频与分 P

日期：2026-10-06（Asia/Shanghai）。源码、独立包及原 `dist` 完整升级均已完成；正式包实网第一 P 1080p 下载、注册路径桥接能力及浏览器回归已通过。

### 问题与修复

- 用户在 B 站看到“检测到浏览器内视频源（blob），尚未获取可下载直链”。原扩展和桌面只对 YouTube/X 提供页面解析，B 站分轨流进入通用禁用路径。
- 增加 B 站主站/移动站 BV、av 普通视频地址校验，去除跟踪参数并显式保留 `p`，缺省规范化为 `p=1`；拒绝错误分 P、伪造主机、凭据、异常端口、直播和番剧地址。
- 复用既有 yt-dlp/FFmpeg、清晰度、代理、限速、队列、暂停/续传和数据库字段；桥接冷启动与运行中能力均新增 `bilibili`。不新增数据库结构，不读取账号 Cookie。
- 扩展新增 B 站资源项和分 P 文件名；站内切换清理旧列表，发送前复核当前 frame 的分 P；旧桌面缺能力时提示更新。清晰度标签改为通用“视频清晰度”，扩展版本 0.3.0。
- 后续用户启动 `output/release-bilibili/daw下载器.exe` 仍提示视频模块未就绪：进程与接口核实该新版已运行并支持 B 站，但 Chrome 注册项仍指向 `dist/browser-native-host.json`，其旧桥接仅返回 `youtube/x`。实例按安装目录隔离，手动启动另一目录 EXE 不会切换已注册桥接。原 `dist` 进程退出后直接重建正式目录，不改注册表，不强杀独立目录用户程序。

### 验证结果

| 验证项 | 结果 |
| --- | --- |
| Python 全量 | 77 项通过，82.444 秒；新增 B 站地址、命令、缺音频拒绝、桥接去重、暂停、重启进度保留及取消 |
| Node 逻辑 | 12 项通过；包含 B 站地址规范化、分 P 和危险/不支持地址拒绝 |
| 真实扩展 | 源码及 `output/release-bilibili/chrome-extension` 均通过 Chromium 测试，1280x900 与 390x844；覆盖 blob、P1/P2 切换、popup 清晰度、不同请求 ID、旧桌面能力和 DRM guard |
| 既有浏览器路径 | 同轮直链、iframe、HLS/流片段禁用、YouTube 清晰度/SPA/DRM、X 多播放器回归通过 |
| 源码实网 P1 | 用户视频第一 P 下载完成；1280x720 H.264 + AAC，324.151202 秒，16492537 字节；独立 ffprobe 和全片解码通过 |
| 新 EXE 实网 P2 | 实际 BrowserBridge 冷启动新 EXE；重复消息只创建一条任务，下载当前第二 P，1280x720 H.264 + AAC，165.651474 秒，7766652 字节；全片解码和正常退出通过 |
| 发布一致性 | 独立包扩展所有文件与源码逐字节一致；完整包含双 EXE、扩展、视频组件、许可证及安装脚本 |
| 正式 dist EXE | 默认发布目录复制出的真实双 EXE 冷启动、去重、第一 P 1080p 下载、全片解码、正常退出通过；1920x1080 H.264 + AAC，324.151202 秒，29001472 字节 |
| 正式 dist 扩展 | `smoke_browser.cjs --dist` 全套通过；原目录扩展与源码、全部 9 个视频工具/许可文件与 vendor 逐字节一致 |
| 已注册桥接 | 按 HKCU 注册项解析真实 host 路径并发送 Native Messaging ping，返回 `installed=true` 且能力包含 `youtube/x/bilibili`；正式 GUI 未启动时返回此安装能力属于预期 |
| 用户数据保留 | 部署及隔离测试前后原 `dist/data/config.json` 与 `downloads.db` SHA256 均不变；未修改注册表，未关闭用户独立目录的程序 |

- 验收链接：`https://www.bilibili.com/video/BV1VbYk6FE6c/`，分别选择 `?p=1` 和 `?p=2`。
- P1：`output/bilibili-validation-3d2b4069/downloads/Bilibili-BV1VbYk6FE6c-P1.mp4`。
- P2：`output/bilibili-exe-ce2f25e3/downloads/Bilibili-BV1VbYk6FE6c-P2.mp4`；EXE 截图 `output/bilibili-exe-ce2f25e3/bilibili-completed.png`。
- 正式包 P1 1080p：`output/bilibili-exe-60032184/downloads/Bilibili-BV1VbYk6FE6c-P1.mp4`；截图 `output/bilibili-exe-60032184/bilibili-completed.png`。命令：`python -B scripts/smoke_youtube.py "https://www.bilibili.com/video/BV1VbYk6FE6c/?p=1" --exe --height 1080`，默认发布目录为原 `dist`。
- 浏览器截图：`output/playwright/bilibili-desktop.png`、`bilibili-mobile.png`、`bilibili-popup.png`，已目视核对无溢出。
- 首次浏览器测试调用了与本机 Chromium 缓存不匹配的 Playwright（缺 chromium-1247）；改用已有 `C:/Users/lenovo/AppData/Local/npm-cache/_npx/84539a01c0e4c364/node_modules/playwright` 后通过，未安装或修改用户浏览器。

### 发布状态与边界

- 正式交付目录已经更新为原 `dist/`，扩展 0.3.0；不再要求用户改用 `output/release-bilibili/`。Chrome 继续使用原注册路径，点击下载可冷启动正式新版。
- 正式主 EXE SHA256（含清空历史及高清窗口图标修复）：`e5ffd838b386a8afc06fe22242e027d8b274a6f9565b20734d7eacd792f0b3c0`。
- 正式桥接 EXE SHA256：`d602a759e9521e07c9d9bbf2c2c8a24133dd26dc886736efd5f687b13b025a99`。
- 高清窗口图标版已通过真实 EXE 全工作流、任务栏实拍和注册桥接能力检查；本轮未重做 B 站实网，下一条实网结果属于清空历史补丁版本，详细版本边界见 `TEST_REPORT_2026-09-11.md` 顶部。
- 清空交互补丁后同一正式包再次完成 B 站 P1 1080p 实网与全片解码，产物 `output/bilibili-exe-365cff77/`，1920x1080 H.264/AAC、324.151202 秒、29001472 字节。代理正常退出原应用后打包、验收并重启，原路径桥接运行中 ping 返回 `ok=true` 和完整能力；清空交互 EXE 验收详见 `TEST_REPORT_2026-09-11.md` 顶部。
- `output/release-bilibili/` 仅为早期独立验收包；其主 EXE SHA256 `47367c001a11aa995b7743dc7978d9d9d240b8fe9359cb584a1339051a509d24`，桥接 SHA256 `91e5c229a0ebbd9df9b30ceab09a3c23b4c5474c0a37601095a5711fd843429c`，不等同于 Chrome 当前注册的正式实例。
- 原 `dist/data/config.json` 基线 SHA256：`5b792996463345cc3cde1b83b1f619aebf454b08ee3fb631b2ee0b776af2f451`；`downloads.db`：`58c7313d48a13e00c66dcfc02e28f4ecfb968f885539a1312b9596a44e2e8f96`；当前 `dist/temp/` 未发现文件。部署必须保留这些用户数据。
- 浏览器使用独立配置和构造页面，Native Messaging 在浏览器段模拟；用户视频另由实际桥接/EXE 完整下载，另已核对真实注册路径的 ping 能力。未自动点击用户登录 Chrome 完成整条链路，仍待用户重试；未重新实网验收 YouTube/X。
- 已实测上述普通公开视频 P1/P2 的 720p 及 P1 的 1080p；不扩展到番剧、付费/登录受限、直播、DRM、所有分 P 或整合集下载，不保证其他视频匿名可取 1080p。

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
