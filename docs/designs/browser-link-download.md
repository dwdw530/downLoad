# 浏览器右键发送普通链接到 daw 下载器：设计与验收约定

- 日期：2026-10-14（Asia/Shanghai）
- 项目：`D:/vscode_workspace/downLoad_project`
- 状态：Pi 已实现，Codex 已独立审查、修复并完成源码/隔离 EXE 验收；正式 `dist/` 尚未部署。结果见 [测试报告](../BROWSER_LINK_TEST_REPORT.md)。
- 分工：Pi Agent 实现源码；Codex 独立审查代码、补充必要回归用例并执行测试。用户负责把提示词交给 Pi，再把实现结果交回 Codex。
- 调研基线：Git HEAD `bdaa2951f2f05185daa3af75445b5433b5c834b0`。调研时已有功能分析文档修改和未跟踪的 `output/`；不得清理或覆盖这些内容。实施前重新核对实际工作区。

## 1. 用户已确认的需求

在 Chrome / Edge 的网页上，对一个可下载链接点击鼠标右键，出现 **“使用 daw 下载器下载此链接”**。只有用户选择该菜单时才向桌面下载器发送任务。

- 普通左键点击、浏览器“另存为”和浏览器自己的下载流程保持原行为。
- 不自动拦截、不取消浏览器下载、不扫描网页后自动提交任务。
- 菜单针对真实链接目标 `info.linkUrl`，不是当前页面地址、选中文字或播放器最近一次请求。
- 默认采用桌面端现有下载目录与下载设置，直接加入队列；不新增一层必须确认的添加对话框。
- 已启动时复用桌面实例；未启动时走现有 Native Messaging 冷启动机制。
- 扩展中的“识别视频”开关仍只控制视频识别。关闭视频识别后，手动右键下载仍可用；不改变该开关原语义。

## 2. 本次实施范围

支持 HTTP/HTTPS 的普通 GET 文件链接，包括带查询参数、没有扩展名但实际返回文件的链接。签名 URL 原样传递，不排序查询参数、不解码后重新拼装。实际可下载性由桌面探测确认，不能仅靠文件扩展名保证。

可沿用已有受限的 Cookie、Referer、User-Agent 转发，支持其覆盖范围内的 GET 下载；这不等于完整复制浏览器请求。

本轮不实现：通用 POST/表单重放、特殊 Authorization、分区 Cookie、blob/data/javascript/file/FTP 链接、未知大小普通文件、失效地址刷新、动态分段、自动分类、全局自动接管、定时队列和全局限速。继续保留现有未知大小/零字节普通文件的拒绝边界并明确报错，不顺带修改引擎。

右键普通链接入口不承担视频页面解析、HLS/DASH 合并或抖音播放器绑定。已知平台视频页、播放清单及抖音专用 CDN 资源应提示使用现有视频入口，不能用这个新入口绕过已建立的完整视频校验。

## 3. 已核对的现有实现与复用点

| 文件 / 符号 | 当前行为 | 本次处理 |
|---|---|---|
| `chrome-extension/background.js` / `native(message)` | 使用 `com.laowang.downloader`，有连接错误与 125 秒超时处理 | 直接复用，通过参数交给新增模块；不重写视频传输流程 |
| `background.js` / `download(tabId, id, senderFrame)` | 媒体列表、视频开关、站点复核、Cookie 与视频能力检查 | 保持原行为，不把普通链接塞进媒体列表 |
| `downloader/browser/native_host.py` / `forward` | 目前只允许 `ping`、`download`；显式下载可启动 GUI | 只增加新操作白名单与对应能力信息 |
| `downloader/browser/bridge.py` / `BridgeServer.dispatch` | 旧 `download` 先执行 `video_filename`，走视频专用校验与回执 | 增加独立的普通文件分支；旧视频分支保持原样 |
| `bridge.py` / `video_filename` | 只接受视频种类，并生成 MP4/WebM 文件名 | 普通文件不能调用它，不能把 PDF/ZIP 伪装成 MP4 |
| `downloader/browser/security.py` / `normalize_context` | 固定返回 `media: True` | 受控保留新的文件模式，旧数据默认仍为媒体模式 |
| `security.py` / `browser_get` | 响应 MIME 只允许 MP4/WebM/octet-stream | 仅对新增文件模式使用普通文件响应规则；原媒体规则不放宽 |
| `TaskManager.add_task` | 已有活动计数、UI 回调、队列调度和普通文件创建 | 直接调用，不复制下载调度逻辑 |
| `DownloadEngine.create_download_task` | 会再次调用 `normalize_context`，保存 DPAPI 加密上下文 | 新模式必须经二次规范化和重启恢复仍保持正确；原则上不用修改此方法 |
| `scripts/build_exe.py` | 会复制整个扩展目录到 `dist` | Pi 不执行构建；Codex 验收后处理正式交付 |
| `scripts/package_release.py` / `EXTENSION_FILES` | 发布文件白名单 | 新增扩展运行文件必须加入白名单 |

重要兼容点：`tests/test_douyin_download.py` 会验证冷启动与运行中的视频 `capabilities` 完全相同，并等于 `VIDEO_KINDS`。新功能不要修改这个数组，也不要把文件下载加入 `VIDEO_KINDS`。

## 4. 交互与反馈

### 4.1 菜单注册

- 新模块建议为 `chrome-extension/link-download.js`。
- 固定菜单 ID：`daw-download-link-v1`。
- `contexts: ["link"]`。
- `targetUrlPatterns` 与 `documentUrlPatterns` 均限定 `http://*/*`、`https://*/*`。
- Manifest V3 使用顶层注册的 `chrome.contextMenus.onClicked` 监听器，不使用菜单项的 `onclick` 回调。
- 安装、更新和浏览器启动时确保菜单存在；注册须幂等。Service Worker 休眠再唤醒不能出现重复菜单、重复监听或入口丢失。
- 只处理自己的固定 ID；不使用 `removeAll()` 清空其他菜单。
- 扩展 `manifest.json` 增加 `contextMenus`，反馈采用通知时增加 `notifications`；不增加 `downloads`、`webRequestBlocking`、`debugger` 或拦截规则权限。
- 兼容现有 `minimum_chrome_version: "120"`，按该最低版本支持的 API 形式处理回调/Promise 和 `runtime.lastError`，不为了省事提高最低版本。

`background.js` 只需导入新增模块，并在模块初始化时调用类似 `registerLinkDownloadMenu(native)` 的入口。监听注册必须同步发生；点击后的异步工作在回调内执行。不得先等待网络探测后才注册监听器。

菜单重复注册、通知不可用等可恢复错误必须在新模块内处理并给出脱敏反馈，不能让未捕获异常中断旧视频 Worker 的初始化或留下未处理的 Promise rejection。不得通过吞掉旧视频异常来伪装兼容。

### 4.2 反馈方案

- 用户选菜单后显示“正在发送到 daw 下载器…”。发送成功只表示“已加入下载队列”，不能说“下载完成”。
- 使用浏览器通知反馈发送状态、成功或失败；同一次请求更新同一条通知，不连续堆叠多条。
- 通知图标复用项目现有品牌资源，使用实际可渲染的本地图像或 PNG data URL，不请求远程图片。现有 `assets/icon.png` 可作为来源；若需要缩略图常量，通过 `apply_patch` 写入文本，不为此引入新图片依赖。SVG 通知图标兼容性不能仅凭文件存在判定。
- 在现有扩展 popup 中增加独立的“链接下载”状态区域，作为系统通知被关闭时的可查看反馈。使用现有样式、`textContent` 和 `role="status"`，不重做界面。
- 不占用现有视频 `#status`、媒体列表计数或 action badge；不改变视频按钮状态。普通文件状态不进入 `media:<tabId>`。
- 状态区使用独立且有界的 `chrome.storage.local` 键，例如 `fileDownloadStatus`，只保存最后一条状态及时间、请求 ID、脱敏文件名和固定错误文案。不得保存 URL、Cookie、Referer 或下载请求本体。
- 不能依赖 `storage.session` 保存此反馈：当前关闭视频识别会执行 `storage.session.clear()`。新状态区的初始化与监听也不能被旧 `refresh()` 的提前返回跳过。
- 成功沿用桌面 `show_window()`，任务卡显示实际保存名称。失败给出明确提示；超时提示“结果尚未确认，请先查看桌面任务列表”，不自动重发或虚构创建失败。

## 5. 扩展中的点击处理

1. 校验菜单 ID、`tab.id` 和 `info.linkUrl`。HTTP/HTTPS、有效主机、长度限制、禁止 URL 用户名密码和控制字符；解析只用于校验，不改写签名 URL。
2. 取实际点击 frame 的上下文：优先 `info.frameUrl`，再按顶层场景使用 `info.pageUrl`；必要时用现有 `webNavigation.getFrame` 核对。不能把顶层页面地址当作所有 iframe 的来源。
3. 同一 tab/frame/完整链接在发送中只保留一份请求，避免快速重复点击创建重复任务；这份锁与旧视频 `sending` Map 分开。
4. 复用 `native({action: "ping"})` 检查新增 `features` 标记。冷启动返回 `ok: false` 但明确 `installed: true` 且支持该 feature 时可以继续发送，由原冷启动逻辑启动桌面。
5. 仅在用户点选菜单后读取当前标签页对应 cookie store 中、目标下载 URL 匹配的 Cookie。用 `getAllCookieStores()` 确认 store，不得猜默认 store、不遍历读取所有域名 Cookie；无法确认时明确失败。
6. 排除分区 Cookie 和空名称 Cookie，沿用路径优先顺序。仅构造允许的 User-Agent、Referer、Cookie。沿用现有同源/跨源 Referer 缩减策略，HTTPS 页面指向 HTTP 时不发送 Referer。
7. 异步取上下文期间若相关 frame 已关闭或导航，不能从新页面借用登录状态。发送前复核捕获的 frame/document 信息；无法确认就提示重新右键。
8. 使用固定的本次 `request_id` 调用新操作。不得将 `action: "download_file"` 开放成可由网页 content script 随意提交 URL 的 `runtime.onMessage` 通道；下载触发只来自自己的浏览器菜单监听器。
9. 保存脱敏结果并反馈。通知失败不应撤销已创建任务，也不能在错误处理里再次提交下载。

## 6. 独立协议与向后兼容

### 6.1 能力协商

保留协议 `version: 2`、原生主机名、扩展 key/ID、注册表路径、现有 `capabilities` 的含义与结果。在 ping 响应中增加：

```json
{"features": ["file_download_v1"]}
```

运行中的 `BridgeServer.dispatch(ping)` 与未启动时 `native_host.forward(ping)` 都报告文件 feature，且不依赖 `require_tools()` 成功。视频 `capabilities` 仍按原来的 FFmpeg/yt-dlp 检查生成。FFmpeg 缺失可以影响视频能力，不能使普通文件下载不可用。

旧桥接没有该标记时，扩展明确提示更新桌面程序和 BrowserBridge，不降级成视频请求。旧 BrowserBridge 搭配新桌面程序可能转发出新 ping 信息却拒绝新操作，这种响应也须提示升级桥接；不能把它当成链接失效。新版本与旧版本的混合安装只能明确失败，不能干扰原视频入口。

### 6.2 新操作

```json
{
  "action": "download_file",
  "request_id": "file-12d0edcb-3ac3-4c7b-8cd0-55de159a68d2",
  "url": "https://example.com/files/report.pdf?download=1",
  "headers": {
    "User-Agent": "Mozilla/5.0",
    "Referer": "https://example.com/"
  }
}
```

- 示例 ID 仅用于说明；实际使用每次逻辑提交生成的 UUID。重发同一逻辑请求时不得换 ID。
- 请求中没有 `kind`、`height`、`video` 或客户端可选择的 `media` 模式，也不接受浏览器指定任意本机保存路径。
- 普通文件名由现有 `get_filename_from_url` / `validate_filename` 处理。此次不新增 Content-Disposition 命名功能，不使用网页标题给文件强加后缀。
- `native_host.forward` 将 `download_file` 加入允许操作；继续复用消息长度检查、冷启动、超时与分帧机制。不修改已有 `download` 的路由。
- `BridgeServer.dispatch` 在旧视频分支前增加新操作分支，委托一个小的普通文件处理方法。旧 `video_filename`、站点路由和视频错误语义保持原样。
- 新分支复用现有请求 ID 校验、受限回执缓存与并发保护。文件回执使用单独命名空间，例如 `('file', request_id)`；不改变旧视频字符串 key。成功请求重复到达只返回原回执，不重复添加。
- 保留现有缓存容量限制；不承诺桌面重启后仍具有永久请求去重。连接超时不得后台自动再发一个新 ID。

### 6.3 创建普通任务

新分支内部强制构造文件模式上下文，调用：

```text
TaskManager.add_task(url, save_path=config.download_dir, request_context=file_context)
```

底层继续使用普通文件任务、现有 SQLite 结构、排队、暂停恢复、ETag/Last-Modified 校验、分块、哈希和非覆盖保存。请求探测发生在已有桥接请求线程，不在 Tk 主线程执行。

文件命名与预留交给现有引擎/数据库完成，不复制视频分支中的一套预分配文件名算法。成功回执必须从创建后的实际任务读取 `filename`，避免返回改名前的名称。

即使没有 Cookie，也要传非空的文件上下文，不能用 `request_context=None` 绕过响应类型检查和现有凭据保护。

失败返回文件专用、经过脱敏的错误文案；不得回传含 URL/请求头的原始异常。新增可选错误码可用于区分不支持、连接问题与未知结果，但不能为此改造全项目错误体系。

## 7. 安全上下文的最小扩展

当前 `normalize_context` 固定写入 `media: True`，引擎创建任务时还会再次规范化；只在桥接中设一个临时标记会被覆盖。这里必须做完整的小范围适配：

1. `normalize_context` 保留经过严格布尔类型校验的 `media` 值；缺省仍为 `True`，旧数据/旧调用行为不变。URL、头部白名单、长度、字符和编码校验不变。
2. 仅新增文件分支构造 `media: False`。旧视频消息即使夹带 `media: false`，也不能影响视频分支内部构造的媒体上下文。
3. `browser_get` 媒体模式保留原 MIME 白名单与错误行为；文件模式允许普通文件类型，并拒绝 HTML/XHTML 登录页、已知 HLS/DASH MIME。普通 TXT/JSON/PDF/ZIP 等不能因不在视频 MIME 白名单而被拒绝。缺失 MIME 但大小明确的普通响应不应仅因缺少 MIME 被当视频拒绝。
4. 对新文件模式检查显式 `.m3u8` / `.mpd` 和 `douyinvod.com` 及其子域名；包括重定向目标，均提示使用既有视频入口。不通过普通文件路线绕开抖音的 ID、分轨和完整时长校验。其余普通视频直链仍只是普通字节文件下载，不新增平台视频解析。
5. `browser_get` 的同源凭据规则、跨源不带 Cookie/Referer、HTTPS 降级拒绝、重定向次数限制保持原实现。不得改成无条件 `requests.get(..., allow_redirects=True)`。
6. 仍用现有 DPAPI 加密请求上下文。文件模式需经任务创建、落库、解密、重启及分块请求完整保留；不新增数据库字段或存明文 Cookie。

不要为新增文件模式重写 `security.py`，也不要把原视频 MIME 校验整体删除。这里属于必须由 Codex重点审查并执行旧视频回归的共享代码。

## 8. 允许变动的文件与保护范围

| 范围 | 允许的最小改动 |
|---|---|
| 新增 `chrome-extension/link-download.js` | 菜单、普通链接编排、独立反馈状态；注入已有 `native` 传输函数，便于独立测试 |
| `chrome-extension/manifest.json` | 必要权限；保留 key、身份和原有配置，发布版本号留到最终交付确定 |
| `chrome-extension/background.js` | 新模块导入和初始化；不抽取/重排原视频逻辑 |
| `chrome-extension/popup.html`、`popup.js` | 独立的链接状态区域和读取/监听；原有元素 ID、事件与显示保留 |
| `downloader/browser/bridge.py` | `features`、独立 `download_file` 路由、文件回执与错误处理 |
| `downloader/browser/native_host.py` | 新 action 白名单、冷启动 feature；原冷启动与分帧复用 |
| `downloader/browser/security.py` | 按第 7 节实现受控文件模式，保留旧默认行为 |
| `scripts/package_release.py` | 新扩展运行资源加入 `EXTENSION_FILES`，不改变已有载荷 |
| 本设计、功能进度档、README、BROWSER_VIDEO.md | 精确说明按需右键下载及当前部署状态；“不自动接管浏览器下载”仍然成立 |

原则上不修改 `content.js`、`douyin-page.js`、`media.js`、`youtube_downloader.py`、下载引擎、任务状态机、数据库结构、主窗口业务、托盘、设置、安装脚本和注册表逻辑。不新增另一个本地 HTTP 服务。

不修改既有测试断言来迁就新实现，不删用例、不跳过失败、不顺带格式化全仓。若代码现状使设计无法落实，应记录具体阻碍和最小调整建议交 Codex，不擅自扩大到重构或新增其他下载功能。

## 9. Codex 的独立代码审查清单

- 先比较 Pi 实施前后的差异，排除他人原有改动；逐一检查是否超出第 8 节。
- 菜单仅能由用户主动触发；普通点击/浏览器下载没有新增拦截监听或取消操作。
- 新链接不能经网页消息伪造提交，不能污染视频列表、视频 badge、识别开关与站点状态。
- 核对协议兼容、FFmpeg 独立性、混合版本报错与冷启动行为。
- 核对 `media: False` 在二次规范化与恢复时保留，旧视频不能被降级到文件模式。
- 核对 URL 原串、iframe 上下文、cookie store、重定向、错误脱敏及 DPAPI。
- 核对同名不覆盖、实际文件名回执、并发重复点击、成功回执与超时后的不确定结果。
- 核对新资源发布白名单和生产目录状态，不能把“源码已实现”写成“正式 EXE 已支持”。

## 10. Codex 执行的验收矩阵

| 编号 | 场景 | 必须得到的结果 |
|---|---|---|
| C01 | 远程网页右键 HTTP/HTTPS 链接；空白区域、选中文字、非法协议 | 链接上出现唯一菜单；其他上下文不提供这个下载入口；非法协议即便伪造回调也被拒绝 |
| C02 | 普通左键、浏览器另存为、只右键后取消 | 浏览器行为与基线相同，不发下载消息、不创建桌面任务 |
| C03 | PDF、ZIP、DOCX、EXE、TXT；有大小但无扩展名/无 MIME 的 GET | 入队正确，保存字节/哈希与本地服务样本一致，不被命名成 MP4 |
| C04 | 查询签名、中文编码、`+` 与 `%2B` | 服务端收到的目标参数不被重写；界面和日志不泄露签名 |
| C05 | 同源 Cookie 文件、跨源重定向、iframe、不同 cookie store | 正确选择当前授权环境；跨源不转发凭据；不从错误 tab/frame 取登录信息 |
| C06 | HTML 登录页、未知大小、零字节、POST、blob、HLS/DASH、抖音 CDN | 按本轮边界明确拒绝或指向旧视频入口，不生成假成功，不改写现有引擎边界 |
| C07 | URL 用户信息、CRLF 头注入、非法文件名、路径越界、HTTPS 降级 | 保留既有安全拒绝；错误文案不带原始凭据 |
| C08 | 桌面已开/未开、FFmpeg 不可用、桥接未装、旧桌面/旧桥接混用 | 复用实例或按需启动；普通文件不受视频组件缺失影响；不兼容明确报错，无静默降级 |
| C09 | 同一次请求重复到达、快速重复点击、两个不同链接、同名文件 | 单个在途点击不重复建任务；不同链接独立；同名不覆盖，回执为实际文件名 |
| C10 | 连接超时、通知不可用、Worker 休眠重启、重复菜单注册 | 不自动重复下载；popup 可查看脱敏结果；菜单与监听唯一且可用 |
| C11 | 关闭/打开视频识别；发送普通文件时查看视频 popup | 两个功能互不改状态；视频数量、清晰度、按钮和原提示继续正确 |
| C12 | 普通文件暂停、继续、重启、远端版本变化、Range 异常、跨盘、校验失败 | 通过现有引擎回归；不覆盖文件，不拼接不同版本数据，不误报完成 |
| C13 | 视频直链 MP4/WebM、HLS/DASH、YouTube、X 首页、B 站分 P、抖音推荐/详情/modal_id | 原有测试和对应浏览器夹具继续通过；新文件模式不绕过或改变旧视频保护 |
| C14 | 批量添加、稍后下载、队列顺序、搜索分类、主题、托盘、历史、完成后动作 | 既有完整 Python/GUI 回归通过；新增后台探测仍计入活动任务，不误触发关机 |
| C15 | 打包后的主 EXE、BrowserBridge、原扩展与新增资源 | 正式交付前分别验收；源文件与打包载荷一致，用户数据与断点保留 |

测试分层：

1. Codex 为新增菜单编排补独立 JS 用例，建议文件 `tests/browser_link_download.test.mjs`；覆盖真实回调路由和失败状态，不只比对实现字符串。
2. Codex 复用现有隔离 HTTP 服务、临时数据库与桥接测试方式，补 `tests/test_browser_file_download.py` 等必要用例；不把本地真实用户任务用作夹具。
3. 运行现有扩展测试与全部 Python 测试。Python/GUI 使用 `py310_env`；历史“308 项通过”等数字不能作为本轮结果。
4. 使用隔离浏览器配置测试源码扩展。现有 `scripts/smoke_browser.cjs` 会禁用真实 Native 传输，覆盖视频 UI；它通过不代表右键到 EXE 整链路通过。
5. 浏览器原生右键菜单不属于网页 DOM；Codex 需要用 Windows 桌面自动化实际点菜单，不能用 Playwright 的页面文字定位或直接调用函数冒充真实右键验收。
6. 真实 Native/EXE 联调使用隔离程序目录、配置和下载目录。隔离浏览器仍能看到 HKCU 注册的真实 host，不能把测试消息误发到用户正在用的实例；如需临时调整注册，先备份并确保恢复。用户程序不得强杀。
7. 涉及共享安全模块，必须回归既有平台夹具；真实站点验证单独记录。登录不可用、外网限制等如实标记，不用夹具通过代替真实站点结果。

预期命令由 Codex 在对应文件存在后执行：

```text
node --test tests/browser_extension.test.mjs tests/browser_link_download.test.mjs
python -B -m unittest discover -s tests -v
node scripts/smoke_browser.cjs <已核实的 Playwright 包路径>
```

最后一条的包路径由 Codex 实测确认，不猜测安装位置。构建与 `smoke_exe.py`、`smoke_browser_bridge.py` 等打包验收属于审查通过后的交付阶段；新增右键到 EXE 的真实检查仍需独立执行。

## 11. Pi 的交接与正式部署边界

- Pi 完成实现和静态自检后停在源码交接，不负责最终审查或验收测试。
- Pi 不构建或改写 `dist/`，不覆盖扩展生产副本，不启动/退出用户下载器，不修改注册表，不创建发布包，不提交/建分支/推送 Git。
- Pi 在原功能进度档的“右键普通链接下载专项交接”中记录实际变更、静态检查结果、未解决问题及“待 Codex 审查测试”；不得写“全部测试通过”或“正式版已可用”。
- 交接至少给出：改动文件清单、关键接口对应、旧视频保持兼容的说明、执行过的命令及实际结果、还未运行的验证、是否存在设计偏离。
- Codex 收到实现后先独立审查，再补测试并运行矩阵；发现问题修复并验证相关回归后，才进入正式交付。
- 本功能修改 Python 桥接/安全代码，因此仅复制扩展不能完成正式升级。后续交付须核对主 EXE 与 BrowserBridge 的重建、扩展载荷同步及原 `dist/` 验收，保留 `dist/data/`、`dist/temp/` 和下载文件；发布 GitHub 仍以用户当时授权为准。

## 12. 参考与关联材料

- [交给 Pi 的完整提示词](../prompts/pi-browser-link-download.md)。
- [原功能分析与专项进度](../context-sessions/20251214-1555-老王下载器功能分析.md)。
- [现有浏览器视频边界](../BROWSER_VIDEO.md)。
- Chrome 官方 [contextMenus](https://developer.chrome.com/docs/extensions/reference/api/contextMenus)：菜单权限、链接上下文与 MV3 的 onClicked 用法。
- Chrome 官方 [cookies](https://developer.chrome.com/docs/extensions/reference/api/cookies)：cookie store、URL 范围与分区 Cookie。
- Chrome 官方 [notifications](https://developer.chrome.com/docs/extensions/reference/api/notifications)：通知权限与 iconUrl。

以上 API 文档在 2026-10-14 核对；本文件是设计依据，不是已完成实现或测试报告。
