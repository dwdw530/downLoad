# 小红书（XHS）视频笔记下载适配：设计与验收约定

- 日期：2026-02-09（Asia/Shanghai）
- 项目：`D:/vscode_workspace/downLoad_project`
- 状态：**设计已实施**。2026-10-17 根据裸域笔记页失败反馈修复 MAIN world 注入范围，并补充 ID/清晰度及 smoke 回归；实现与当前验证范围见 `docs/context-sessions/browser-video/20261005-1455-YouTube下载修复与完整发布.md`。登录态实网验收仍需单独执行。
- 调研基线：Git HEAD `768aa28e7f468c4d4985585ef0ed0068770ab983`（`docs: record verified v0.7.0 publication`）。工作区已有他人未提交改动 `docs/context-sessions/20251214-1555-老王下载器功能分析.md`、`docs/context-sessions/installer/20261009-0013-Windows安装包与GitHub发布.md` 和未跟踪的 `output/`，实施时不得清理或覆盖。
- 分工沿用既有约定：Pi Agent 实现源码；Codex 独立审查代码、补充回归用例并执行验收；用户负责构建、发布与实网确认。

## 0. 一句话结论

小红书在不同链路上都是**绕开站点入口的裸媒体直链兜底**：能识别到 CDN 的 mp4/m3u8 时走"普通文件/通用流"路径，识别不到就只提示"尚未发现视频资源"或"媒体流不支持"；文件名、完整性校验、清晰度档位、站点分组全部缺失。本设计把小红书接入**与抖音同构**的站点链路（页面状态取已签名完整 MP4 → 桌面端 `--load-info-json` → ffprobe 校验音视频轨与时长），并把改动限制成"只在 `www.xiaohongshu.com` 生效的新增分支 + 常量追加"，用回归用例锁定其它站点与其它的功能行为逐字不变。

## 1. 用户已确认的需求与待确认项

### 1.1 已确认

1. 小红书视频笔记要能像 YouTube / X / B 站 / 抖音一样，在悬浮面板或工具栏里点一次就进桌面下载队列。
2. 适配过程**不得影响其它站点与其它功能**：既有四站、HLS/DASH、右键普通链接下载、剪贴板监视、任务队列/续传/历史、清晰度设置、安全模型都保持原行为。
3. 先出设计，实现另行安排。

### 1.2 待用户确认的决策点（第 8 节有完整权衡与推荐）

| 编号 | 决策点 | 推荐 |
|---|---|---|
| A | 水印口径：只用页面状态中官方播放器实际使用的水印流（`streamType` 258/301 等），还是额外拼接 `originVideoKey` 取原画无水印 | 只做官方流；`originVideoKey` 留到阶段 3 并单独评审 |
| B | 页面状态读不到时，是否把该 frame 的裸 mp4/m3u8 直链条目重新展示出来 | 阶段 1 不展示（与抖音一致，避免误下过期签名/分片）；实网覆盖率不足再按守卫分支补 |
| C | `duration` 缺失时是失败还是放宽完整性校验 | 失败（沿用"宁可失败也不发布不完整文件"的既有口径） |
| D | 清晰度上限语义：源只有高于上限的档位时 | 明确失败并提示改用更高档位（与抖音现状一致），不擅自超上限 |
| E | 是否把 `xhscdn.com` 加入"视频专用 CDN"名单，让右键普通链接下载不再能绕过视频入口 | 阶段 1 不动（零外溢优先），阶段 2 单独评审 + 回归 |

## 2. 只读核对结论（现状证据）

### 2.1 插件侧

| 位置 | 现状 | 影响 |
|---|---|---|
| `chrome-extension/media.js:1-58` | 站点函数只有 `youtubeUrl / isXPage / xUrl / bilibiliUrl / isDouyinPage / douyinUrl` | 没有 `isXhsPage / xhsUrl`，小红书页无法成为"站点条目" |
| `media.js:61 classify()` | 只按扩展名/MIME 认 `mp4/webm/hls/dash` | 小红书只能退化成裸媒体条目 |
| `media.js:83 classifyResponse()` | 只有 `requestType === "media"` 的请求才可能判成 `mp4`，其它一律 `stream` | 小红书"先调 API 拿地址再播放"的加载方式会落成 `stream`，按钮被禁用（"分片或媒体流"） |
| `background.js:231-238`（`list` 分支） | `onX = isXPage / onDouyin = isDouyinPage / biliUrl / youtubeUrl` 硬编码 | 小红书页 `pageUrl = null`，不注册站点条目、无站点命名 |
| `background.js:105,118,243`、`content.js:280,285`、`popup.js:23-25` | 共 7 处硬编码 kind 数组/文案判定 | 新 kind 必须逐处追加，否则按钮/发送会被拦 |
| `content.js:195 videoPostFor()` | 只有 douyin 与 x.com 两支 | 小红书无法把当前 `<video>` 关联到笔记 |
| `content.js:252` / `manifest.json:12-17` | MAIN world 脚本只挂 `douyin-page.js` | 页面世界数据（`window.__INITIAL_STATE__`）读不到 |
| `link-download.js:35 videoOnlyLink()` | 只拦 YouTube/X/B 站/抖音页面、`.m3u8/.mpd`、`douyinvod.com` | 小红书视频 CDN 可被右键当普通文件下载（策略不一致，非核心缺陷） |

### 2.2 桌面侧

| 位置 | 现状 |
|---|---|
| `downloader/core/youtube_downloader.py:23-25` | `VIDEO_KINDS = ('youtube','x','bilibili','douyin','hls','dash')`；`STREAM_KINDS = ('hls','dash')`；`CONTEXT_KINDS = ('douyin','hls','dash')` |
| `youtube_downloader.py:29,100+` | `video_page_url()` 只认四站；`video_request_context()` 里抖音分支校验 `*.douyinvod.com`、codec ∈ h264/h265、width/height、duration |
| `youtube_downloader.py:246-257` | `CONTEXT_KINDS` 加 header/cookie；`kind == 'douyin'` 走 `--load-info-json`，并校验 ffprobe 时长（`expected_duration`） |
| `downloader/browser/bridge.py:74-95` | `video_filename()` 的站点分支与默认命名只认四站；`bridge.py:210` 的 `is_page` 同样 |
| `bridge.py:192` | `ping` 返回 `capabilities = list(VIDEO_KINDS)`，插件据此判断"模块尚未就绪" |
| `task_manager.py:63`、`db_manager.py:205` | 另有两处硬编码视频 kind 元组（启动纠偏、落库 `download_type`） |
| `downloader/browser/security.py:24,28-36` | `DOUYIN_CDN` + `reject_video_only_url()` 只保护抖音 CDN |
| `download_engine.py:150+` | 粘贴链接统一走普通文件任务；桌面 UI 无站点识别 |

### 2.3 yt-dlp 实测（本次实际执行，非推断）

`vendor/video/yt-dlp.exe` 版本 **2026.08.19**：

```
$ yt-dlp --list-extractors | grep -i xiaohongshu
XiaoHongShu
$ yt-dlp --simulate https://www.xiaohongshu.com/explore/66c3e1a3000000001e02b5b3
[XiaoHongShu] Extracting URL: ...        # 命中 extractor
ERROR: [XiaoHongShu] ...: No video formats found!
$ yt-dlp --simulate https://www.xiaohongshu.com/discovery/item/66c3...
[XiaoHongShu] Extracting URL: ...        # 同样命中，同样取不到格式
$ yt-dlp --simulate https://xhslink.com/a/abc123     # 走 generic → 404
$ yt-dlp --simulate https://www.xiaohongshu.com/user/profile/123abc  # 走 generic → 跳登录
```

结论：`XiaoHongShu` extractor 的 `_VALID_URL` 只覆盖 `(explore|discovery/item)/<hex>`，且需要登录 Cookie，上游长期存在 `Unable to extract initial state` / `No video formats found` / CAPTCHA 问题（yt-dlp issue #12719、#13578）。**因此不能采用"加个 kind 让 yt-dlp 解析页面"的方案**，必须像抖音一样由浏览器侧提供已签名媒体信息。

### 2.4 文档与测试口径

`README.md:75`、`docs/BROWSER_VIDEO.md:7-18`、`design.md:198-203` 的类型表都只列四站 + 直链/HLS/DASH；`docs/` 全文检索"小红书"为 **0 命中**；`tests/` 无 xhs 用例。

## 3. 目标与非目标

### 3.1 目标

1. `www.xiaohongshu.com` 的**视频笔记**详情页（含 SPA 弹层，URL 为 `/explore/<noteId>` 或 `/discovery/item/<noteId>`）在悬浮面板与工具栏中显示一条"小红书"站点条目。
2. 该条目走桌面端 `kind: "xhs"` 视频任务：yt-dlp `--load-info-json` + FFmpeg 合并为 MP4，ffprobe 校验**含音视频轨**且**时长与页面声明一致**后才发布。
3. 文件名 `小红书-<noteId>.mp4`，失败文案可解释、可排障（签名过期、清晰度上限、state 缺失、模块未就绪）。
4. 改动对非小红书站点与非视频功能**逐字零行为变化**（第 7 节给出论证与锁定用例）。

### 3.2 非目标（本轮不做，避免范围外溢）

- 图文笔记、图集原图、`LivePhoto` 关联视频、封面下载。
- 去水印原画（`originVideoKey` / `mediaV2` 的 screencast 流）、小红书 App 分享短链 `xhslink.com`（与 B 站短链口径一致：先在浏览器里跳转到笔记页）。
- 笔记首页流/搜索结果/用户主页的批量或自动识别（只在笔记详情页生效）。
- 收藏夹/合集批量、直播、字幕。
- 重写共享分类器、宽松化 `classifyResponse` 的 `requestType` 规则（见第 7 节）。
- 阶段 1 不收紧右键普通链接对 `xhscdn.com` 的策略（决策 E）。

## 4. 媒体获取方案

### 4.1 三层证据与"失败关闭"

| 层 | 来源 | 用途 | 失败处理 |
|---|---|---|---|
| L1 页面状态 | MAIN world 读 `window.__INITIAL_STATE__` | **唯一**用于生成站点条目的候选流与时长 | 该笔记在 state 中缺失 → 不生成站点条目，回落到现状（裸直链列表，决策 B） |
| L2 播放器绑定 | 该 frame 的 `<video>`（`duration`、`currentSrc`） | 交叉校验"state 里的这条笔记 = 用户正在看的这个故事" | 交叉校验不一致 → 拒绝下载并提示刷新 |
| L3 运行时直链 | 现有 `webRequest` + `content.js` 上报 | 保持现状兜底，不新增语义 | 沿用现状文案 |

不使用：不 patch `fetch`/`XMLHttpRequest`，不调用小红书 `feed`/`detail` 接口，不伪造 `x-s`/`x-t` 签名，不注入脚本。理由：与抖音 `douyin-page.js` 的既有做法一致（只读页面已注入的状态、只在用户点击时取一次），也与 `docs/BROWSER_VIDEO.md:62` 的"只请求播放器提供的同源接口"的克制口径一致。

### 4.2 页面状态字段与兼容路径（依第三方实现与上游记录，需实网复核）

`window.__INITIAL_STATE__` 是 Nuxt/Vue SSR 注水状态，PC 与 H5 结构不同：

- PC：`state.note.noteDetailMap[noteId].note`（首屏还可用 `state.note.firstNoteId` 兜底定位）。
- H5：`state.noteData.data.noteData`。
- 视频笔记：`note.type === "video"`，`note.video.media.stream.{h264,h265,h266,av1}[]`，每项含 `masterUrl`（带 `sign`+`t` 时效签名）、`backupUrls`、`width`、`height`、`videoCodec`、`duration`、`streamType`（第三方资料：258/301 为非小程序流，259/309 为带小程序水印流）。
- 时长：`note.video.capa.duration`（秒）。
- 注意：小红书近期在 state 中注入 `undefined` / `new Map()` / `new Set()` / `new Date()`，**不能对该对象做 `JSON.stringify` 往返**；只做属性读取与 `Array.isArray` 判断。
- 页面是否包含该字段**尚未在本机实网确认**（列为第 11 节 R1），因此设计为"取不到即失败关闭"，不做猜测式兜底。

参考：`ucmao/media-parser` 的 `docs/parsers/xiaohongshu.md`（字段路径、`undefined/new Map` 清洗、`xsec_token`/`xsec_source` 必须保留、短链 UA 差异）；`xpzouying/xiaohongshu-mcp` PR #719（`media.stream[].masterUrl` 带 `sign`+`t`、`mediaV2` 是 JSON 字符串）。

### 4.3 候选流选择规则（纯函数，可单测）

```
xhsNoteVideo(state, noteId, maxHeight) -> {id, duration, formats:[{url,width,height,vcodec}]} | null
```

1. 按 4.2 的两条路径定位 `note`；`noteId` 必须与当前页面 URL 中的 id 一致（不一致返回 `null`，不猜）。
2. `note.type !== "video"` 或 `note.video.media` 缺失 → `null`（图文/LivePhoto 一律不支持，避免把 LivePhoto 视频当笔记视频）。
3. `duration` 取 `note.video.capa.duration`（秒，`Number.isFinite` 且 `> 0`），缺失时回退 `stream[].duration`（若量级明显是毫秒则按与 `<video>.duration` 的容差归一），仍取不到 → `null`（决策 C）。
4. 编码只收 `h264` → `h265`（`av1`/`h266` 不收：FFmpeg 合并与播放兼容性；与 `BROWSER_VIDEO.md:79` 的 H.264 优先口径一致）。每编码 `masterUrl` + 最多 3 条 `backupUrls`，合计上限 6 条。
5. 每条都必须满足：`https:`、无用户名密码、端口为空或 `443`、hostname 以 `.xhscdn.com` 结尾、路径以 `.mp4` 结尾、`width/height` 为 `1..16384` 的整数。
6. 排除 `streamType` 为 `259`/`309` 的小程序水印流（若字段存在）；无法判断时保留原顺序（页面默认首选在最前）。
7. 只保留 `height <= maxHeight`（480/720/1080，来自扩展"视频清晰度"设置）；过滤后为空 → 返回 `null`，由上层给出"没有不高于 Np 的清晰度"提示（决策 D）。

### 4.4 播放器绑定校验（L2）

与 `douyin-page.js:8-31` 同构：

1. 仅接受页面上**可见且尺寸有效**的 `<video>`；存在多个可见视频时要求状态中的候选域与 `video.currentSrc` 同域且时长在容差内，否则拒绝（不按 DOM 顺序猜）。
2. 若 `<video>` 已有有效 `duration`（元数据已加载），要求与 state 时长满足 `max(1s, 2%)` 容差；未加载则跳过此项，但第 1 步的 noteId 匹配仍是硬条件。

### 4.5 握手方式（与抖音一致，不新增侵入面）

- 新文件 `chrome-extension/xhs-page.js`（MAIN world，`run_at: document_start`）。
- 自定义事件 `daw-xhs-read`（请求，`detail` 为 UUID nonce）/ `daw-xhs-data`（响应，`detail` 为 JSON 字符串，长度上限 2MB），由 `<video>` 元素派发与监听，`content.js` 侧用 `try/finally` 只监听一次（照抄 `content.js:42-57` 的既有模式）。
- 脚本本体不监听网络、不写 storage、不向页面挂任何全局变量（唯一例外：为可测性，在 `typeof chrome === "undefined"` 的非浏览器环境把纯函数挂到 `globalThis.__dawXhsParse`；浏览器环境不挂）。
- 结构上把"纯解析"与"浏览器接线"分开：解析部分不依赖 `document`/`chrome`，供 `tests/` 用 `node:vm`/`import` 直接单测。

## 5. 数据流与协议

### 5.1 端到端流程

```
小红书笔记页（MAIN world 状态）
  └─ xhs-page.js  ◄── daw-xhs-read ──  content.js（ISOLATED）  ──► background.js
        └─ daw-xhs-data ─────────────────┘                          │
                                                    站点条目 kind="xhs" url=/explore/<id>
                                                                     │ 用户点"下载"
                                             native({action:"download", kind:"xhs", video:{...}})
                                                                     ▼
                    BrowserBridge.exe ──► BridgeServer ──► video_filename("小红书-<id>.mp4")
                                                                     │
                                        xhs_request_context() 校验 → info-json → YoutubeDownloader
                                                                     │
                              yt-dlp --load-info-json + --format … → ffmpeg 合并 → ffprobe 校验 → 发布
```

### 5.2 站点识别与 URL 规范化

- 白名单 host：`xiaohongshu.com`、`www.xiaohongshu.com`（不支持 `m.`/`xhslink.com`/`user/profile`）。
- 路径：`/explore/<id>`、`/discovery/item/<id>`，`id` 为 `[0-9a-f]{24}`。
- 规范化输出：`https://www.xiaohongshu.com/explore/<id>`（`discovery/item` 归一到 `explore`）。
- **丢弃 `xsec_token`/`xsec_source`/`source`/`xhsshare` 等查询参数**：这些是页面渲染用的来源签名，仓库不把它们写进数据库、日志或任务 URL（与抖音只保留 `video/<id>` 的口径一致）。带 token 的页面 URL 在浏览器内正常加载，不受影响。
- 端口与凭证规则与 `media.js` 既有函数一致（仅 80/443、禁 user:pass）。

### 5.3 协议（不新增 action，与抖音同构）

请求（`action: "download"`）：

```json
{
  "action": "download",
  "request_id": "<item.id>-720",
  "kind": "xhs",
  "url": "https://www.xiaohongshu.com/explore/<noteId>",
  "filename": "小红书-<noteId>",
  "height": 720,
  "headers": {"User-Agent": "...", "Referer": "https://www.xiaohongshu.com/"},
  "cookies": [{"name":"...","value":"...","domain":"...","path":"/","secure":true,"hostOnly":false}],
  "video": {"id": "<noteId>", "duration": 12.34,
            "formats": [{"url":"https://sns-video-hw.xhscdn.com/...mp4?sign=..&t=..",
                         "width": 1080, "height": 1920, "vcodec": "h264"}]}
}
```

响应与既有视频任务一致：`{ok:true, task_id, filename}` 或 `{ok:false, error}`。`--load-info-json` 的 `title` 用 `XHS-<noteId>`。

桌面端校验（新增 `xhs_request_context()`，与抖音分支**并列**而非改写）：

1. `video.id` 必须等于 `xhs_url(url)` 的 noteId。
2. `duration`：数字、有限、`> 0`。
3. `formats`：长度 `1..64`；每项 `https` + 端口 443 + host 后缀 `.xhscdn.com` + 路径含 `.mp4` + 无 `range` 查询参数 + `width/height` 整数 `1..16384` + `vcodec ∈ {h264,h265}`；去重后按原顺序保留。
4. 产出 `result['video'] = {id, title: 'XHS-<id>', duration, is_live: False, formats: [...]}`，`acodec` 固定 `unknown`、`ext` 固定 `mp4`、`protocol` 固定 `https`（与抖音完全同构，确保 `--format` 选择器落在 `b[height<=N][ext=mp4]`）。

### 5.4 失败文案表

| 场景 | 文案（扩展侧） | 文案（桌面侧） |
|---|---|---|
| 桌面端无 `xhs` capability | 请更新并重启桌面下载器，小红书模块尚未就绪 | — |
| 页面 URL 不是笔记页 | 现状不变（只显示裸直链条目） | — |
| 笔记不是视频笔记 | 当前笔记不是视频笔记 | — |
| state 中无该笔记 / 无时长 | 未获取笔记视频信息，请刷新笔记页后重试 | 未获取当前小红书视频信息，请刷新笔记详情页后重试 |
| 无可用的 ≤ 上限清晰度 | 该笔记没有不高于 {N}p 的清晰度，请在扩展中调整清晰度上限 | 未找到不高于 {N}p 的完整 MP4 |
| 播放器与 state 不一致 | 小红书视频已切换，请刷新资源列表 | — |
| CDN 403 / 签名过期 | — | 小红书视频地址已过期，请重新打开笔记页后重试（`failure_message` 新增 xhs 分支） |
| 时长不符 | — | 下载结果时长与完整视频不符，未发布不完整文件（复用既有文案） |

## 6. 精确改动清单

> 命名约定：新增 kind 字符串统一为 `"xhs"`；站点函数 `isXhsPage` / `xhsUrl`；MAIN world 脚本 `xhs-page.js`；桌面校验函数 `xhs_request_context`。

### 6.1 新增文件

| 文件 | 内容 | 约束 |
|---|---|---|
| `chrome-extension/xhs-page.js` | MAIN world 握手 + `xhsNoteVideo()` 纯解析（4.3/4.4/4.5） | 仅 `xiaohongshu.com`；不 patch 网络；不挂浏览器全局 |
| `tests/fixtures/xhs/*.json` | 脱敏 state 样例：PC 正常、H5 正常、图文笔记、缺 `capa.duration`、仅 av1、`streamType` 259、host 非 `xhscdn.com`、noteId 不匹配 | 不含真实 token/签名 |
| `tests/test_xhs_download.py` | 桌面侧 `xhs_url` / `xhs_request_context` / `video_filename` / `failure_message` 用例（含全部反向用例） | 与 `test_douyin_download.py` 同风格 |
| `tests/browser_xhs_page.test.mjs` | 直接加载 `xhs-page.js` 的纯函数跑 4.3 全部分支 + `xhsUrl` 正反例 | 不放宽任何既有断言 |
| `scripts/smoke_xhs.cjs` | 实网验收脚本，照 `scripts/smoke_douyin.cjs` 模式（临时 profile、捕获 native 消息、隔离下载进程、不调用用户注册的下载器） | 只有用户提供笔记 URL 时才跑 |

### 6.2 修改文件（全部为"追加"）

| 文件:位置 | 改动 | 为什么不影响其它站点/功能 |
|---|---|---|
| `chrome-extension/manifest.json:12-17` | 追加一条 `content_scripts`：`matches: ["*://www.xiaohongshu.com/*"]`，`js: ["xhs-page.js"]`，`world: "MAIN"`，`run_at: "document_start"` | 新条目只匹配该 host；既有条目与 `key`／`minimum_chrome_version` 不动（`tests/test_browser_integration.py:213` 的扩展 ID 只依赖 `key`） |
| `manifest.json:4` | `version` → `0.6.1` | 发布流程由用户/Codex 执行（`scripts/package_release.py` 会校验 dist 与源码一致） |
| `media.js`（追加 `isXhsPage` / `xhsUrl`；`publicItem` 的 `kindLabel` 三元链追加 `xhs → 小红书`） | 站点识别与显示名 | **不改** `classify` / `classifyResponse` / `upsert` / `displayName`；其它 kind 的三元链分支逐字不变 |
| `content.js:195 videoPostFor()` | 追加 `location.hostname` 为小红书时调用 `xhsNoteFor(video)`（当前笔记 URL 命中 `xhsUrl` 即返回；否则在同一播放器容器内找唯一 `/explore/`、`/discovery/item/` 链接；≥2 个时失败关闭） | 各 host 分支互斥早返回；x.com/douyin 分支代码不动 |
| `content.js:280,285` | 两处 kind 数组末尾追加 `"xhs"`（`reason` 文案保持"音视频"分支） | 只新增 kind；既有 kind 的布尔结果不变 |
| `popup.js:23-25` | 两处 kind 数组末尾追加 `"xhs"` | 同上 |
| `background.js:105,118,243` | 逐处按语义追加 `"xhs"`：105（可下载 kind 白名单）、118（**不**加，这是 YouTube/X/B 站的纯页面分支，xhs 走 `scopedContext` 分支）、243（同 frame 站点条目过期剔除） | 每处数组的既有元素与顺序不变 |
| `background.js:231-243`（`list`） | 新增 `const onXhs = isXhsPage(page.url)`；`pageKind`/`pageUrl` 链**在末尾**追加 `: onXhs ? xhsUrl(popup ? page.url : message.postUrl) : ...`；命名 `小红书-<noteId>`；`emptyMessage` 追加 xhs 分支 | 前三站的判定顺序与取值不变；xhs 只在自身 host 命中 |
| `background.js:129-140`（`scopedContext`） | `scopedContext = segmented || item.kind === "douyin" || item.kind === "xhs"`；capability 文案改为按 kind 映射（抖音/小红书/流媒体） | 抖音与流媒体文案逐字保留 |
| `background.js`（`download` 内 `item.kind === "douyin"` 的四处复核块） | 并列新增 `item.kind === "xhs"` 的复核：发送 `{action:"xhsContext", videoUrl}` 取候选流与时长；`frame` 当前 URL 与 `item.url` 一致；列表最新条目未变且未被标记 `protected`；设置仍开启 | 抖音块一字不改 |
| `background.js:136-140` 附近 | 新增 `case "xhs"` 取 `video`（与 douyin 的 `video` 字段同构） | 不新增 action、不改消息结构 |
| `link-download.js:35 videoOnlyLink()` | `|| xhsUrl(value)`（放在 `douyinUrl(value)` 之后） | 只新增一个严格 host 白名单判定；`xhscdn.com` 不加入（决策 E，阶段 2 再议） |
| `downloader/core/youtube_downloader.py:23` | `VIDEO_KINDS` 末尾追加 `'xhs'` | 只追加，既有 6 项顺序不变；`tests/test_douyin_download.py:102` 用 `list(VIDEO_KINDS)` 自动跟随 |
| 同上 `:24-25` | `CONTEXT_KINDS` 末尾追加 `'xhs'` | 3 处使用点都是 `in` 判断，只对 xhs 任务生效 |
| 同上（新增 `xhs_url()`；`video_page_url` 加 `if kind == 'xhs'` 分支） | URL 校验 | 既有分支不动 |
| 同上（`video_request_context` 末尾分派到新增 `xhs_request_context`） | 5.3 的校验 | 抖音分支代码一字不改 |
| 同上 `:275 prepare_video_info()` | `if self.kind not in ('douyin', 'xhs'): return` | douyin 行为不变 |
| 同上 `:253-257` | `if self.kind in ('douyin', 'xhs'): return command + ['--load-info-json', self.info_file]` | douyin 行为不变 |
| 同上 `failure_message()` | 新增 xhs 的 403/过期与登录文案分支 | 既有分支判定顺序不变（xhs 分支只在 `kind == 'xhs'` 时命中） |
| `downloader/browser/bridge.py:80,210` | 新增模块级 `PAGE_KINDS = ('youtube','x','bilibili','douyin','xhs')`，把 `kind in ('youtube',...)` 换成 `kind in PAGE_KINDS`；`video_filename` 增加 `elif kind == 'xhs': default_name = 'XHS-' + noteId` | 集合相等性由回归用例锁定（四站 result 逐字相同） |
| `downloader/core/task_manager.py:63`、`downloader/database/db_manager.py:205` | 推荐：把两处字面量改为引用 `VIDEO_KINDS`（新增中立模块 `downloader/utils/media_kinds.py` 存常量，`youtube_downloader` 继续 re-export 以保持既有导入路径）；最小替代方案：两处末尾追加 `'xhs'` | 行为逐字不变，由既有测试锁定；这一步是"消除漂移"，正是本次不适配的根因之一 |
| `scripts/package_release.py:12` | `EXTENSION_FILES` 追加 `'xhs-page.js'` | 只影响发布包文件清单；`build_exe.py:82` 整目录复制无需改 |

### 6.3 明确不动的文件

`downloader/browser/security.py`（阶段 1 不动：`DOUYIN_CDN`、`reject_video_only_url`、`normalize_context`、`normalize_stream_context`、`browser_get`）、`download_engine.py`、`chunk_downloader.py`、`download_file` 分支、`native_host.py`、`downloader/ui/**`（站点分类树按 host 自动分组，xhs 任务自动归入 `xiaohongshu.com`；类型分组按 `.mp4` 自动归入视频）。

## 7. 零外溢论证与回归锁定

### 7.1 论证

1. **站点隔离靠 host 白名单，不靠"共享逻辑放宽"**：插件侧新逻辑只在 `location.hostname ∈ {xiaohongshu.com, www.xiaohongshu.com}` 时进入，MAIN world 脚本只在 `manifest.matches` 命中的页面加载。
2. **共享分类器零改动**：`classify()`/`classifyResponse()` 不改 → 小红书页面上被误判为 `stream` 的条目与所有其它站点的分类结果逐字不变。这正是"决策 B"存在的理由：不为了让小红书好用而放宽全局规则。
3. **桌面端是"加法"**：`VIDEO_KINDS` / `CONTEXT_KINDS` / `PAGE_KINDS` 都只在末尾追加成员；所有判断点都是 `in`，因此既有 kind 的判定结果不变。`capabilities` 的新增项对插件是 `includes` 查询，旧插件不受影响。
4. **安全模型零放宽**：xhs 复用 `normalize_stream_context`（Cookie 域必须在媒体域内、Referer 裁到 origin、HTTPS-only），不新增 Cookie 透传面；小红书页面域 Cookie 不会跨域发到 `xhscdn.com`（由 Cookie 自身 domain 决定，yt-dlp/requests 只发给匹配 host）。
5. **协议零新增**：复用 `download` action 与 `video` 字段，`ping`/`download_file`/回执幂等逻辑不变，DNS/网络面不变。

### 7.2 必须保持不变的既有行为（回归锁定）

| 锁定项 | 现有断言位置 |
|---|---|
| `classify`/`classifyResponse` 全部既有用例 | `tests/browser_extension.test.mjs` |
| `upsert` 上限 60、30 分钟过期、frame 隔离 | 同上 |
| `video_filename` 对 hls/dash/mp4/NUL/非法类型的结果 | `tests/test_browser_integration.py:203` |
| 抖音 `video_request_context` 的全部分支（拒分轨、duration/pix 校验、缺字段报错） | `tests/test_douyin_download.py` |
| `capabilities` 冷启动 == 运行中 == `list(VIDEO_KINDS)`；无工具时为 `[]` | `tests/test_douyin_download.py:93`、`tests/test_browser_file_download.py:253` |
| HLS/DASH 任务的 capability 与 `--force-generic-extractor` 路径 | `tests/test_stream_download.py:56` |
| 右键普通链接分支（含拒绝 `.m3u8/.mpd`、`douyinvod.com`、HTML） | `tests/browser_link_download.test.mjs`、`tests/test_browser_file_download.py` |
| 扩展 ID 与 manifest `key` | `tests/test_browser_integration.py:213` |

新增用例只允许"新增 test 块/新增文件"，不允许修改既有断言（若某既有断言因设计缺陷必须改，视为设计缺陷，回到第 8 节重新决策）。

## 8. 决策点与推荐（需要用户确认）

- **A 水印口径**：推荐 v1 只用页面状态里官方播放器实际使用的流（排除 `streamType` 259/309 小程序水印流）。理由：`media.stream[].masterUrl` 就是页面播放器取用的地址，属"页面已声明的数据"；而 `originVideoKey` → `https://sns-video-bd.xhscdn.com/<key>` 是**平台未在页面播放的原画**，属推断式拼接，与 `docs/BROWSER_VIDEO.md:76-80` 的克制口径冲突，建议独立评审。
- **B 兜底可见性**：推荐 v1 与抖音一致（能定位笔记就只显示站点条目；定位不到就自动回到现状裸直链列表）。若实网发现 state 覆盖率低，阶段 1.5 增加"`onXhs && 站点条目无法解析` 时展示直链"的受控守卫分支，并补单测。
- **C 时长校验**：推荐保留为硬条件（`capa.duration` → `stream[].duration` → `<video>.duration` 三级回退），拿不到就明确失败。
- **D 清晰度上限**：推荐维持"上限即上限"，源没有 ≤ 上限的档位时明确报错并提示改设置（不改动该设置对其它站点的语义）。
- **E 右键策略收紧**：推荐阶段 2 单独评审（把 `xhscdn.com` 加入视频专用 CDN 拒绝名单会让"小红书裸直链"不再能通过右键下载，属跨站点策略变更，需要独立回归与用户确认）。

## 9. 测试与实网验收

### 9.1 单元/集成（不联网）

1. `xhsUrl`：正例（`/explore/<id>`、`/discovery/item/<id>`、带 `xsec_token`/`xsec_source`、裸域与 `www`）、反例（`xhslink.com`、`/user/profile/...`、大写 hex、25 位 id、`http://`、非 443 端口、`xiaohongshu.com.evil.test`、带 user:pass、超长串）。
2. `xhsNoteVideo`：PC/H5 两条结构；图文笔记 → `null`；缺 duration → `null`；仅 av1 → `null`；`streamType` 259 → 排除；host 非 `.xhscdn.com` → 剔除；noteId 不匹配 → `null`；`height` 上限过滤与"全超限 → `null`"；候选数上限 6。
3. 桌面侧 `xhs_request_context`：非 `xhscdn.com` 拒绝、`http://` 拒绝、`range` 参数拒绝、`vcodec=av1` 拒绝、`duration<=0` 拒绝、`video.id != noteId` 拒绝、正常样例通过并产出 yt-dlp 可用的 info-json 结构。
4. `video_filename({'kind':'xhs',...})` → `小红书-<id>.mp4`（或桌面端默认 `XHS-<id>.mp4`），并保持四站既有输出不变。
5. `youtube_downloader.command()`：xhs 任务含 `--load-info-json`、`--add-header`、`--no-playlist`，且 `--format` 选择器与抖音一致（用 mock 检查 argv，不启动子进程）。
6. 插件纯函数回归：既有 `node --test tests/*.test.mjs` 全绿。

### 9.2 实网验收（用户提供笔记 URL，需登录态）

1. 用 `scripts/smoke_xhs.cjs`（Playwright + 临时 profile + 未打包扩展）打开用户指定的笔记页，验证：面板出现"小红书 · 最高 Np"条目、`kind === "xhs"`、`video.id === noteId`、formats 全部 `*.xhscdn.com`、duration > 0。
2. 用同一份 `video` 信息在隔离进程里跑下载（照 `smoke_douyin.cjs` 的做法，不调用用户注册的下载器），验证：产物为 MP4、ffprobe 含视频与音频轨、时长与页面一致、正常退出。
3. 负面实网：未登录/已删除笔记 → 明确失败文案；签名过期（等待或换一次性链接）→ "地址已过期"文案。
4. **前置实测项（决定设计是否成立）**：在第 11 节 R1-R4 中至少确认 R1（state 字段存在）与 R4（CDN 是否需要 Referer/Cookie），否则阶段 1 的方案需回退到阶段 1.5/2。

### 9.3 回归

`python -m pytest tests/ -q` 与 `node --test tests/*.test.mjs` 全绿，且新增用例之外无既有断言被修改。桌面端发布前按既有流程做 EXE 隔离 smoke（用户/Codex 执行）。

## 10. 兼容、灰度和回滚

| 组合 | 行为 |
|---|---|
| 新插件 + 旧桌面 | `ping.capabilities` 无 `xhs` → 面板显示"小红书模块尚未就绪"，不发送任务；其它站点不受影响 |
| 旧插件 + 新桌面 | 插件不认识 xhs，不会生成站点条目；桌面端多出的 capability 被忽略 |
| 新插件 + 新桌面 | 完整功能 |

- 发布顺序：**先发桌面端**（完整包，capabilities 含 `xhs`），再发扩展（`extension-v0.6.1`）。桌面端先发不会改变任何现有行为（纯追加）。
- 回滚：插件回滚到上一版即恢复现状；桌面端回滚只需旧包。注意一处**已核实的回滚副作用**：旧版本 `task_manager.py:63` 的视频 kind 字面量不含 `xhs`，启动纠偏 `_reconcile_incomplete_tasks_progress` 会把 `download_type='xhs'` 的未完成任务当成单线程普通文件，去找 `temp_dir/<task_id>.tmp`（视频任务实际用 `temp_dir/xhs/<task_id>/video.mp4`），因此把该任务的显示进度回填为 0；任务本身与续传不受影响（yt-dlp `--continue` 重新计算进度）。这只影响回滚期的进度显示，实施时用测试确认，不必为它额外改旧版本。
- `dist/` 构建与发布不由实现阶段执行（沿用 `docs/designs/browser-link-download.md:223` 的边界约定）。

## 11. 风险与未知（必须实网确认）

| 编号 | 未知 | 影响 | 处置 |
|---|---|---|---|
| R1 | `window.__INITIAL_STATE__` 是否仍含 `note.noteDetailMap[noteId].note.video.media.stream` | 主路径可行性 | 实网确认；不足则启用阶段 1.5（`<meta property="og:video">` 作为页面已声明的第二证据源）或阶段 2（`mediaV2`） |
| R2 | SPA 内切换笔记时 state 是否刷新 | 可能取到上一条笔记 | 已设计为"必须与当前 URL 的 noteId 一致，否则失败关闭"；实网验证覆盖率 |
| R3 | 第三方资料的 `streamType` 数值（258/301 无水印、259/309 小程序水印）是否仍准确 | 水印口径（决策 A） | 实网对比下载产物人工确认；不确定时保留原顺序 |
| R4 | `xhscdn.com` 直链是否需要 `Referer` 或小红书域 Cookie | 可能 403 | 实网确认。仅需 Referer 时零改动；若需要 `xiaohongshu.com` 的 `web_session`，**不得**放宽 `normalize_stream_context`（跨域 Cookie 泄漏），需回评审 |
| R5 | 签名有效期很短（`t` 时间戳） | 队列延迟后 403 | 已有"地址已过期"文案；必要时提示用户重新打开笔记页 |
| R6 | 小红书对高频/自动化访问的风控（CAPTCHA、`/login` 重定向） | 实网脚本可能不稳定 | smoke 脚本只在用户提供 URL 且手动确认时运行；不引入自动重试 |
| R7 | 笔记视频可能是竖屏 1080x1920 等非常规分辨率 | `--format` 的 `height<=N` 语义按高度过滤，竖屏下"720p"实际指宽度？ | 现有抖音同样问题；v1 沿用同一语义，不做宽度换算，文档写清 |

## 12. 分阶段实施顺序

1. **阶段 1（主体，本设计范围）**：`xhs-page.js` + `media.js` 站点函数 + `content.js`/`background.js`/`popup.js` 追加分支 + 桌面 `xhs_url`/`xhs_request_context`/info-json 分支 + `PAGE_KINDS` + 发布白名单 + 单测 + 文档。上线前必须完成 R1/R4 实网确认。
2. **阶段 1.5（可选兜底）**：若 state 覆盖率不足，追加受守卫的"裸直链可见"分支与 `og:video` 第二证据源（决策 B）。
3. **阶段 2（策略收紧，独立评审）**：`xhscdn.com` 纳入视频专用 CDN 拒绝名单（决策 E）；`mediaV2`/screencast 流作为补充证据源。
4. **阶段 3（可选能力）**：`originVideoKey` 无水印原画（决策 A）、图文笔记图集、LivePhoto —— 每一项独立评审与设计。

## 13. 文档与交付物同步清单

| 文件 | 改动 |
|---|---|
| `docs/BROWSER_VIDEO.md` | 支持范围新增小红书条目；限制章节补"不支持图文/LivePhoto/短链/无水印原画"；能力表补 xhs 行 |
| `README.md:75,83` | 支持站点列表与"刷新页面"提示补小红书 |
| `design.md:198-203` | 类型表新增 `xhs` 行（浏览器已取得签名 formats → `--load-info-json`） |
| `docs/releases/extension-v0.6.1.md` | 新增扩展更新日志（照 v0.5.2 的结构） |
| `PROJECT_SUMMARY.md` / `BUILD_EXE.md` | 如涉及功能清单则同步 |
| 本文件 | 实施后把"状态"改为实际结果，并补实网验收结论 |

## 14. Codex 独立审查清单

1. `grep -rn "xhs" chrome-extension downloader` 的每一处改动是否全部落在"新增分支/末尾追加"上；是否存在任何共享表达式的语义变化。
2. `classify` / `classifyResponse` / `upsert` / `displayName` / `browser_get` / `normalize_context` / `normalize_stream_context` 是否**零 diff**。
3. `VIDEO_KINDS` / `CONTEXT_KINDS` / `PAGE_KINDS` 的既有成员与顺序是否未变；`task_manager.py:63`、`db_manager.py:205` 是否已消除字面量漂移或已同步追加。
4. `xhs_page` 脚本是否真的不 patch `fetch`/`XMLHttpRequest`、不发网络请求、不写 storage、不在浏览器环境挂全局；事件握手的 nonce 与长度上限是否与抖音一致。
5. `xhs_request_context` 的拒绝用例是否覆盖：非 `xhscdn.com`、http、range、av1、duration<=0、id 不匹配、formats 空/超 64、宽高非整数。
6. 旧插件 + 新桌面、新插件 + 旧桌面两种组合的提示与不回退路径是否如第 10 节所写；`capabilities` 是否只在 `ping` 追加。
7. 回滚风险：旧版本桌面端遇到 `download_type='xhs'` 的历史任务，是否会被 `_reconcile_incomplete_tasks_progress` 误改进度（第 10 节的注意事项要有测试或明确说明）。
8. 发布白名单 `scripts/package_release.py:EXTENSION_FILES` 是否包含 `xhs-page.js`；`dist` 与源码一致性校验是否通过。
9. 文档与测试口径是否与实现一致（尤其"支持/不支持"边界与决策 A/B/D 的最终选择）。

## 15. 参考资料

- 本仓库：`chrome-extension/douyin-page.js`（MAIN world 握手与播放器绑定范式）、`background.js:105-180`（发送前复核范式）、`downloader/core/youtube_downloader.py`（`--load-info-json` 与 ffprobe 时长校验）、`download_file`/`link-download.js`（策略边界）、`docs/BROWSER_VIDEO.md`（对外承诺的边界口径）。
- yt-dlp：`ie/XiaoHongShu`（`_VALID_URL = https?://www\.xiaohongshu\.com/(?:explore|discovery/item)/(?P<id>[\da-f]+)`）、issue #12719（No video formats found，需 Cookie）、issue #13578（Unable to extract initial state / CAPTCHA）。
- 字段与反爬：`ucmao/media-parser` `docs/parsers/xiaohongshu.md`（`__INITIAL_STATE__` 双结构、`undefined/new Map` 清洗、`xsec_token`/`xsec_source` 必须保留、短链 UA 差异、`consumer.originVideoKey` 与 `mediaV2`）；`xpzouying/xiaohongshu-mcp` PR #719（`media.stream[].masterUrl` 带 `sign`+`t`、`backupUrls`、`mediaV2` 为 JSON 字符串、字幕位置）；`NanmiCoder/MediaCrawler` `media_platform/xhs/extractor.py`（`noteDetailMap` 提取范式）；`JoeanAmier/XHS-Downloader` discussion #94（`originVideoKey` 只在部分结构中出现）。
