# 给新对话（Pi Agent）：实现小红书（XHS）视频笔记下载适配

日期：2026-02-09。任务由用户交给新对话的 Pi 执行；源码实现完成后交回 Codex 独立审查、补测和验收。不要拉其他代理代替 Codex 的审查与测试角色。

本文就是为了**避免新对话重复调研、重复提问、重复跑实测**：第 2 节是上一轮已经查实并写进设计的结论与精确落点，第 3 节是已确认的决策。新对话不要重新推导这些内容，只做第 1 节列出的最小核对，然后按第 4 节顺序实现。

## 可以直接复制给新对话的完整提示词

```text
你在项目 D:/vscode_workspace/downLoad_project 中工作，请实现「小红书（XHS）视频笔记下载适配」的源码。

设计已经定稿并由用户确认，逐字读这一份就够，不要重新做全仓调研：
D:/vscode_workspace/downLoad_project/docs/designs/xiaohongshu-adaptation.md

**目标**：www.xiaohongshu.com 的视频笔记详情页（/explore/<24位hex> 与 /discovery/item/<24位hex>，含 SPA 弹层）
在悬浮面板/工具栏里出现一条「小红书」站点条目，点一次进入桌面下载队列，走 kind="xhs" 的
「浏览器提供已签名 MP4 → 桌面端 info-json → yt-dlp --load-info-json → ffmpeg 合并 → ffprobe 校验音视频轨与时长」链路。
**硬约束**：不得改动 YouTube / X / B 站 / 抖音 / HLS / DASH / 右键普通链接下载 / 剪贴板 / 队列 / UI 的任何既有行为；
只允许「host 白名单守卫下的新增分支」和「常量末尾追加成员」。

### 1. 开工只做这三步核对（不要多做）

1. `git rev-parse HEAD` 与 `git status --porcelain`：设计基线是 768aa28e7f468c4d4985585ef0ed0068770ab983。
   工作区已有他人未提交改动（docs/context-sessions 下两个 .md）与未跟踪的 output/，保留，不要清理或覆盖。
2. 打开设计文档第 6 节（精确改动清单）、第 7 节（零外溢论证与回归锁定）、第 8 节（决策点）、第 11 节（风险）。
3. 只读下面这些你即将改的文件：
   chrome-extension/media.js、content.js、background.js、popup.js、link-download.js、manifest.json、douyin-page.js
   downloader/core/youtube_downloader.py、downloader/browser/bridge.py、downloader/core/task_manager.py、
   downloader/database/db_manager.py、scripts/package_release.py、tests/browser_extension.test.mjs、
   tests/test_douyin_download.py
   需要时再按需打开 tests/test_browser_integration.py、tests/test_stream_download.py、scripts/smoke_douyin.cjs。

**不要**做的事（上一轮已确认，重复做纯属浪费对话）：
- 不要重新 grep 仓库里有没有 xhs 代码，结论是 0 处（除打包进 vendor/dist 的 yt-dlp 二进制）。
- 不要重新跑 `vendor/video/yt-dlp.exe --simulate https://www.xiaohongshu.com/explore/...`，结论已实测：
  extractor 名为 XiaoHongShu、只匹配 (explore|discovery/item)/<hex>、缺登录 Cookie 时返回 "No video formats found"，
  上游还有 "Unable to extract initial state"/CAPTCHA 问题（issue #12719、#13578）。因此**不要**把小红书交给 yt-dlp 解析页面。
- 不要去收集小红书页面状态字段的来源资料，设计文档第 4.2/15 节已给出字段路径与出处。
- 不要重新列举「哪些文件硬编码了站点清单」，设计文档第 2、6 节已给全（含 file:line）。
- 不要读长期记忆库、不要 web_search、不要新建进度档。

### 2. 上一轮已核实的落点（基线 768aa28；行号会漂，请按符号/文本定位，不要硬信行号）

插件侧（chrome-extension/）
- media.js:1-58 站点函数只有 youtubeUrl/isXPage/xUrl/bilibiliUrl/isDouyinPage/douyinUrl → 需新增 isXhsPage/xhsUrl。
- media.js:61 classify() 与 media.js:83 classifyResponse() → **一行都不许改**（只有 requestType==="media" 才判 mp4，
  小红书 fetch 取址的请求会落成 stream；这是设计里刻意保留的现状，靠站点条目绕开，不放宽全局规则）。
- media.js:111 publicItem() 的 kindLabel 三元链 → 追加 xhs → "小红书"。
- background.js:105/118/243 三处 kind 数组 → 按语义追加 "xhs"；注意 :118 是「纯页面分支 youtube/x/bilibili」，
  xhs 不进去（它走 scopedContext 分支）。
- background.js:231-238 list 分支（onX/onDouyin/biliUrl/youtubeUrl 硬编码）→ 末尾追加 onXhs 分支；命名「小红书-<noteId>」。
- background.js:129-140 scopedContext 判定与 capability 文案 → 追加 xhs，抖音/流媒体文案逐字保留。
- background.js 里 `item.kind === "douyin"` 共 5 处：:110（发给桌面端前的帧/URL 复核）、:136（发消息取抖音上下文）、:164（读取 cookies/设置前的二次复核）
  是三个独立复核块 → **并列**新增 `item.kind === "xhs"` 的对应复核，抖音块一字不改；xhs 取信息用 {action:"xhsContext", videoUrl}。
  另 :129/:132 是 scopedContext 判定与 capability 文案，按上一条追加 xhs。
- content.js:195 videoPostFor() → 追加小红书 host 分支（当前笔记 URL 命中 xhsUrl 即返回，否则在同一播放器容器找唯一
  /explore/、/discovery/item/ 链接；≥2 个失败关闭）；x.com/douyin 分支不动。
- content.js:280,285 与 popup.js:23-25 的 kind 数组 → 追加 "xhs"（reason 文案仍落在「音视频」分支）。
- content.js:42-57 是 MAIN world 事件握手的既有范式（nonce + JSON 字符串 + 2MB 长度上限 + try/finally 只监听一次），
  新脚本照抄这个模式。
- content.js:252 与 manifest.json:12-17 → MAIN world 目前只挂 douyin-page.js；需新增 xhs-page.js 条目，
  matches 只能是 *://www.xiaohongshu.com/*，不要动既有条目与 manifest key/minimum_chrome_version。
- link-download.js:35 videoOnlyLink() → 追加 || xhsUrl(value)（放在 douyinUrl(value) 之后）；
  **不要**在这一轮把 xhscdn.com 加进视频专用 CDN 名单（决策 E）。

桌面侧（downloader/）
- core/youtube_downloader.py:23 VIDEO_KINDS、:24-25 CONTEXT_KINDS → 末尾追加 'xhs'；
  :29 video_page_url 加 xhs 分支；新增 xhs_url()；
  video_request_context 末尾分派到新增 xhs_request_context()（抖音分支代码一字不改）；
  :275 prepare_video_info 的 kind 判断；:253-257 的 --load-info-json 分支；
  failure_message 新增 xhs 的「地址已过期/需重新打开笔记页」文案（既有分支判定顺序不变）。
  **不要**改 security.py 的任何函数（决策 E），不要改 download_engine/chunk_downloader/native_host/UI。
- browser/bridge.py:74-95 video_filename 与 :210 的 is_page → 新增模块级
  PAGE_KINDS = ('youtube','x','bilibili','douyin','xhs') 并替换这两处字面量；video_filename 增加
  elif kind == 'xhs': default_name = 'XHS-' + noteId。既有四站输出必须逐字不变。
  ping capability（bridge.py:192 与 native_host.py:57）来自 VIDEO_KINDS，自动跟随，不要手写数组。
- core/task_manager.py:63 与 database/db_manager.py:205 各有一份视频 kind 字面量 → 推荐引入中立常量模块
  downloader/utils/media_kinds.py，由 youtube_downloader 继续 re-export VIDEO_KINDS（tests 从那里导入，别断）；
  最小替代方案是两处末尾追加 'xhs'。这一步是消除「站点清单漂移」这一根因，行为必须逐字不变。
- scripts/package_release.py:12 EXTENSION_FILES → 追加 'xhs-page.js'（build_exe.py:82 整目录复制不用改）。

测试与文档
- tests/browser_extension.test.mjs：**只新增** xhs 用例（xhsUrl 正反例、publicItem kindLabel），既有断言一行不改。
- 新增 tests/browser_xhs_page.test.mjs：直接加载 chrome-extension/xhs-page.js 的纯解析函数（见设计 4.3/4.5）
  跑全部分支：PC/H5 两种结构、图文笔记、缺 duration、仅 av1、streamType 259、host 非 .xhscdn.com、
  noteId 不匹配、height 上限过滤后为空、候选数上限 6。
- 新增 tests/fixtures/xhs/*.json：脱敏状态样例，不含真实 token/签名。
- 新增 tests/test_xhs_download.py：xhs_url / xhs_request_context / video_filename / failure_message / command() 用例，
  反向用例必须覆盖：非 .xhscdn.com、http、range 参数、vcodec=av1、duration<=0、video.id != noteId、
  formats 空或超 64、宽高非整数。
- 新增 scripts/smoke_xhs.cjs：照 scripts/smoke_douyin.cjs 的结构（临时 profile、未打包扩展、捕获 native 消息、
  用隔离进程下载、不调用用户注册的下载器、不写用户历史）。本轮**只写脚本，不跑实网**。
- 文档同步按设计文档第 13 节（BROWSER_VIDEO.md / README.md:75,83 / design.md:198-203 /
  docs/releases/extension-v0.6.1.md），并同步 manifest.json:4 版本号到 0.6.1。

### 3. 用户已确认的决策（不要再问，直接按此实现）

- A 水印：只用页面状态里官方播放流的候选（排除 streamType 259/309 小程序水印流）；
  **不**拼接 originVideoKey 无水印原画（阶段 3 另评审）。
- B 兜底：能定位笔记就只显示站点条目；定位不到就自动回落到现状裸直链列表（不改 classify/classifyResponse）。
- C 时长：duration 为硬条件，三级回退（note.video.capa.duration → stream[].duration → <video>.duration），
  取不到就明确失败，不发布未校验完整性的文件。
- D 清晰度：上限即上限，源没有 ≤ 上限的档位就明确报错并提示改设置，不擅自超上限、不改该设置对其它站点的语义。
- E 右键策略：本轮不动 security.py 的 DOUYIN_CDN / reject_video_only_url。

### 4. 实现顺序（每步做完先自检，再进下一步）

1. media.js 站点函数 + publicItem 标签；跑 node --test tests/browser_extension.test.mjs（既有用例必须全绿）。
2. 新增 chrome-extension/xhs-page.js（纯解析与浏览器接线分离；浏览器环境不挂全局，仅
   `typeof chrome === "undefined"` 时把纯函数挂到 globalThis.__dawXhsParse 以便单测）+ manifest 追加 content_scripts 条目。
3. content.js 分支 + background.js 的 list/scopedContext/download 复核分支 + popup.js 与两处 kind 数组。
4. 新增 tests/browser_xhs_page.test.mjs + tests/fixtures/xhs/*.json，跑 node --test tests/*.test.mjs。
5. 桌面：xhs_url / xhs_request_context / kind 常量与 PAGE_KINDS / video_filename / info-json 分支 /
   task_manager 与 db_manager 的常量来源 / package_release 白名单。
6. 新增 tests/test_xhs_download.py，跑 python -m pytest tests/ -q（全绿，且既有断言零修改）。
7. 新增 scripts/smoke_xhs.cjs（不运行），同步文档与版本号。

### 5. 保护边界（违反任何一条都算实现失败）

- classify / classifyResponse / upsert / displayName / normalize_context / normalize_stream_context / browser_get
  **零 diff**；security.py 其它函数也不动。
- 不改 download_engine.py、chunk_downloader.py、native_host.py、downloader/ui/**、数据库结构、队列状态机、
  文件名安全策略、不覆盖已有文件策略。
- 不删除、不 skip、不弱化任何现有测试；不用改断言掩盖问题。
- 不运行构建/发布脚本，不动 dist/、不动用户配置/数据库/断点，不注册/卸载浏览器桥接，不启动或强杀用户程序。
- 不提交、不建分支、不推送、不 reset；所有文本编辑用补丁方式，不整仓格式化、不用 shell 覆盖写文件。
- 不做范围外功能：图文笔记、LivePhoto、图集原画、xhslink 短链、florilegium/合集、直播、字幕、mediaV2、
  无水印原画、以及设计文档第 3.2 节列出的全部非目标。

### 6. 遇到未知时的处理（这是「不要反复来问」的关键）

- R1（state 里是否真还有 note.video.media.stream）与 R4（xhscdn 直链是否需要 Referer/Cookie）**属于实网验收项，
  不是实现阻塞项**：按设计实现，把结论写进交接记录即可，不要因此停下反复提问。
- 只有当出现下面情况才停下来问用户，并且一次把问题、证据、最小备选方案一起给出：
  (a) 必须修改第 5 节保护边界里的任何文件或函数；
  (b) 设计文档第 6 节的落点与实际代码结构性不一致（例如某处数组又多了一站、函数被重命名到别的模块），
      且存在两种以上合理适配方式；
  (c) 需要新增 action、改变现有消息结构或放宽安全校验；
  (d) 发现设计本身在安全或完整性上不成立（例如必须把 xiaohongshu.com 的 Cookie 发到 xhscdn.com 才能下载）。
  其它情况一律自行按设计做最小适配，并在交接记录里写明「与设计的不同处理」。

### 7. 自检与交接

允许并应当执行：`node --test tests/*.test.mjs`、`python -m pytest tests/ -q`、
`python -c "import downloader.browser.bridge"` 之类的导入检查、`git diff --check`、`git status --porcelain`。
不允许用静态检查冒充实网验收：真实笔记页识别、真实下载、EXE 验收留给 Codex/用户。

完成后更新同功能进度档（追加「小红书适配」小节，不要新建重复档）：
D:/vscode_workspace/downLoad_project/docs/context-sessions/browser-video/20261005-1455-YouTube下载修复与完整发布.md
若该档已不存在或用户另行指定，按其指示，不要自行造新档。

交接记录必须写清：
1. 实际新增/修改的文件清单，每个文件的目的与关键落点（含符号名，不要只给行号）。
2. 新增协议字段（kind="xhs"、video.id/duration/formats）、桌面校验位置、命名规则。
3. 自检命令与**真实输出**；未运行的测试明确列出（尤其实网 smoke）。
4. 决策 A/B/C/D/E 的实际落地方式，以及任何与设计不同的处理。
5. 未解决项与风险（R1-R7 中仍未确认的），dist 未更新，等待 Codex 独立审查与测试。

最终回复给出简洁的文件清单与交接说明，最后一行明确写：

「小红书适配源码已交接，待 Codex 独立代码审查、补测与实网验收；正式 dist 尚未更新。」
```

## 用户把实现结果交回 Codex 时可用的提示词

```text
上一个对话已完成小红书（XHS）视频笔记下载适配的源码实现，请先独立审查实际变更，再补测并执行回归与实网验收。

设计与改动范围、零外溢论证、回归锁定清单、决策点、审查清单都在：
D:/vscode_workspace/downLoad_project/docs/designs/xiaohongshu-adaptation.md
请重点执行该文档第 14 节的 Codex 独立审查清单，以及第 9 节的测试与实网验收（含第 11 节 R1/R4 的前置实测）。

要求：
1. 先确认改动范围与交接记录，保留工作区原有改动与用户数据，不要回退基线。
2. 核对 classify/classifyResponse/upsert/displayName/normalize_context/normalize_stream_context/browser_get 是否零 diff；
   核对 VIDEO_KINDS/CONTEXT_KINDS/PAGE_KINDS 既有成员与顺序未变；核对旧插件+新桌面、新插件+旧桌面两种组合的提示路径。
3. 关键负向用例必须亲自跑：非 .xhscdn.com 域名、http、range 参数、vcodec=av1、duration 缺失或为 0、
   video.id 与 noteId 不一致、清晰度上限过滤后为空、图文笔记、SPA 切换笔记后的 noteId 不匹配。
4. 实网验收要用 smoke_xhs.cjs（Playwright + 临时 profile），验证面板条目、formats 全在 *.xhscdn.com、时长一致、
   产物含音视频轨；未登录/已删除笔记要有明确失败文案。
5. R1 与 R4 若确认不成立，按设计文档第 12 节的阶段 1.5/2/3 回退，并先向用户说明后再改设计，不要自行扩大范围。
6. 不能用单元测试通过代替实网验收，不能用源码通过代替 EXE 验收；报告实际结果与仍未验证的部分。

未经用户明确要求，不要打包发布、不要部署到 dist、不要提交或推送。
```

这段后续提示词不包含 Git 发布授权；打包与正式目录部署按用户届时要求推进。
