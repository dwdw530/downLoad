# 给 Pi：四项实用改进的完整实施提示词

创建日期：2026-10-22（Asia/Shanghai）；最后更新：2026-10-23。原 Pi 提示词留作历史；用户现已改由 Codex 直接完成残余检查和修复，没有启动代理、打包或写长期记忆。

**当前交接状态：2026-10-23 Codex 已接手并完成源码独立验收；用户随后明确授权完整正式交付，v0.12.0 原 dist/EXE/安装包已构建验收，GitHub 发布核验进行中。**不再交 Pi 执行 A–F，历史临时“不打包”要求不适用于后续明确授权；实际证据见原设计第 9 节与原功能进度档。不写长期记忆。

## 可直接复制给 Pi 的提示词

请在 `D:/vscode_workspace/downLoad_project` 按已确认的四项范围进行源码实现，先读设计，不重新扩展需求：

1. 任务卡片与详情窗显示具体、安全的失败原因和简短建议。
2. 修正 fatal/transient/protocol 混合错误重试，并尊重 429/503 的 Retry-After。
3. 左侧状态分类增加“已暂停”，复用计数、筛选、持久化与批量选择规则。
4. 未知大小百分比显示 --；真实空文件完成显示满条、已完成 · 0 B，卡片和详情一致。

### 必读与基线

- 完整设计：`D:/vscode_workspace/downLoad_project/docs/designs/practical-four-improvements.md`，特别是第 3～6 节固定行为、第 7 节文件范围、第 8 节 F/R/C/P 验收矩阵。
- 历史边界：`D:/vscode_workspace/downLoad_project/docs/context-sessions/20251214-1555-老王下载器功能分析.md` 的 v0.11.1、未知大小和多选批量章节；历史旧结论不能替代当前代码。
- 当前生效的 AGENTS.md 与项目工程规则。先定点查代码/调用方/测试，不整仓倾倒，不把历史候选当实施授权。

设计基线为 v0.11.1，HEAD `b4ea6ad7098dee538415c8b36475cfda41b27aec`。实施前核对实际 HEAD 与 Git 状态，保留后续用户改动；**不 reset、不切/建分支、不覆盖他人内容**。设计阶段只新增本设计与本提示词，它们应原样保留再追加结果。

用户明确不需要将本次分工或设计写入本地记忆：不要创建、更新或同步 Codex/Claude/Pi 的长期记忆，也不用远程记忆。只在项目设计结果区与原功能进度档记录实际实现和自检。

### A. 展示、筛选与进度的固定行为

1. 卡片增加独立、最多两行的原因区域，正常隐藏；不把长文本拼进原固定宽度状态标签。详情复用原双击窗口，在统计与图表之间显示安全原因与短建议，不新增弹窗/日志入口/链接更新按钮。
2. 复用已有 error_message。失败、校验失败显示安全原因，空间不足等有明确故障原因的 paused 也显示；手动暂停、稍后下载、重启纠偏不显示故障。
3. 原始异常不是可信 UI 文案。用一个小纯展示函数识别明确错误类别并输出固定中文；未识别用兜底，不回显 URL、Cookie、多词 Authorization、签名/token、私有主机/地址或用户路径。不要把现有 safe_error/sanitize_output 直接当完整 UI 脱敏方案。
4. 首屏建卡、重启回填、状态事件、已打开详情全部接入；新一轮 pending/downloading、完成/取消清掉旧原因，迟到错误不能污染新状态，worker 不操作 Tk。
5. 状态组增加 `status:paused`，位于等待中之后，只匹配 paused。左侧按全部任务累计 paused，右侧按分类/搜索/隐藏完成取交集；ui_active_category 持久化、零计数空态与原筛选选择清除规则沿用。开始/入队后离开暂停组；不改批量快照或队列规则。
6. 复用 format_progress_texts 的三项返回值；卡片和详情共用纯百分比/进度值函数。未知总量保持静态空轨道和 --，真实字节/未知、ETA --，不加动画。
7. 仅 completed 且 total_size=0 且 downloaded_size=0 特判满条/已完成、大小行“已完成 · 0 B”，速度 0。其他 0 不代表完成，不篡改数据库哨兵为 1；负值/矛盾旧记录不冒充空文件。
8. 完成状态事件立刻同步权威字节值，不依赖下一次进度事件；重启/详情首屏相同。迟到下载进度不能把终态倒退；不要每个 tick 全量查库。
9. 已知大小的原百分比和校验状态保持，UI 百分比限在合法范围；verify_failed 的 100% 是传输完成不是校验成功。未知大小 EOF、非 Range 重试从零和浏览器无大小/空文件拒绝不动。

### B. 重试的固定行为

1. 外层决策 fatal > protocol > transient > 成功。任意 fatal 内部不重试，协调器发现后不补轮、不降级；复用 worker 停止标志收敛其他 futures，再失败并释放槽位。保留已完成块和部分字节，不清零/删除，不发布不完整文件。
2. 停 worker 不等于取消任务，最终应 failed 而非 cancelled；不能从池内等待自己的池或让终态后旧回调再写坏状态/进度。正在阻塞的网络请求仍按原 timeout 收敛，不强杀线程。
3. 无 fatal 的 protocol 继续原安全降级；纯 transient 只补失败块；retry_times 的尝试次数语义、外层三轮上限保持。
4. 仅 HTTP 429/503 读取 Retry-After，覆盖同一 ChunkDownloader 的普通单线程/Range/受限浏览器请求，不改探测或 yt-dlp。支持非负整数和合法 HTTP 日期；非法头安全退回原指数退避。
5. UTC 日期只在收响应时转换一次到 monotonic 的绝对 not-before。内部等待取原退避与 not-before 的最大值，不相加；外层重建按块传绝对时间，不绕过也不从头重等。同 run 的 protocol 降级也保留预算与尚有效的最大 not-before，无 transient 时仍按原降级时序。
6. 300 秒是本 run 从第一次 transient 起共享的自动重试启动/等待窗口，不是整条成功下载的硬时限。轻量预算由所有块和内外层共享，不按轮重置；新 run 新建，不落库，不引入全站限流调度框架。
7. 服务端要求等到预算边界或以后就结束自动重试，failed 并保留数据，明确较长等待原因；不能把合法巨大数字当解析失败后 1 秒重发，也不能提前截短服务器等待。
8. 等待分段可停，发请求前再查暂停/取消/退出和预算。窗口内已发出的成功流允许超过窗口收尾；未失败的长下载不受 300 秒限制。原限速/timeout/身份与 Range 校验不变。
9. 保留既有重试/降级临时提示，不新增倒计时或设置；新日志不含原响应头、真实 URL、Cookie 或异常敏感文本。

### C. 文件边界与自检授权

允许：设计第 7 节的五个既有源码文件（main_window.py、task_detail_window.py、file_utils.py、chunk_downloader.py、download_engine.py），以及可新增的小纯模块 `downloader/ui/task_presentation.py`、必要测试、README、设计结果区和原功能进度档。

不改 TaskManager/数据库结构、settings/config、BrowserBridge、Native 协议、扩展、yt-dlp 命令、悬浮条、版本号；不扩展链接更新、防睡眠、统一视频限速、侧栏滚动、分页或定时下载。不打包/部署/改 dist，不创建分支、提交、推送或发布。需要越界先说明具体理由，不暗自扩大。

保留真实 data、temp、downloads、output 和原 dist；测试只用独立临时配置/SQLite/分块/成品，不能清理用户目录、注册浏览器或操作用户的运行程序。项目文件修改遵守 apply_patch 及机械替换预览规则。

建议先 F+C+P，再 R。新增真正能证明边界的用例：

- 混合错误用本地 HTTP/事件屏障固定顺序，403 正式 GET 一次，fatal+protocol 不降级，退出无迟到写入。
- Retry-After 的秒/日期/巨值/跨轮用可控时钟与真实 GET 计数，至少一次短等待本地 HTTP 实测。日期/300 秒/长下载不能真的等几分钟。
- 排除 bytes=0-0 探测，断流要真实，至少一个禁用新行为后的反向验证必须失败，不能只测到正常 EOF 或探测。
- 真实 Tk 验证原因区、详情、暂停分类及零字节两种事件顺序，复用现有 GUI 夹具和主线程 GC 收尾规则，不能全靠 Mock 当 UI 验收。
- 不弱化既有续传、Range、未知大小、非覆盖、限速、批量和浏览器安全回归。

你获准使用 `D:/anaconda3/envs/py310_env/python.exe`（Python 3.10）执行隔离定向自检并记录命令/结果。全量回归、真实 GUI/下载及独立验收由用户交回 Codex 后再安排；不要使用默认 base Python，更不要拿历史通过数字冒充本次。

### D. 交回要求

完成后更新设计第 9 节，并在原功能进度档新增“2026-10-22 四项实用改进”实施记录，保留原档创建时间和历史，不另建同功能进度档。最终回复列出：

1. 修改/新增文件及四项功能关键接入符号。
2. 对应 F/R/C/P 矩阵哪些做了、哪些未执行；实际自检命令、Python 环境、结果与耗时。
3. 脱敏/兜底、原因恢复与清除、混合错误收敛、共享预算/not-before、暂停筛选、零字节事件顺序的实现说明。
4. 设计偏离和原因、确实未完成项；没有遗留就明确说明，不留 TODO/占位功能。
5. 明确状态为“源码实现完成、待 Codex 独立验收”或实际未完成，不声称打包/发布/全量验收已完成。

不要自动启动其他代理、写长期记忆、打包或做 Git/发布动作。用户会把你的实现结果交回 Codex。

### E. 独立审查后的二轮整改提示词（2026-10-22）

请在 `D:/vscode_workspace/downLoad_project` 继续已有四项实用改进，**只修 Codex 已独立复现的 B01–B05 与相关回归缺口**，不重做原功能、不扩展范围。

先完整读取原设计 `D:/vscode_workspace/downLoad_project/docs/designs/practical-four-improvements.md`，尤其第 4/6 节安全语义、第 8 节矩阵、第 9 节 Codex 独立审查；本提示词 A–D 的边界仍有效。再查原功能进度档与当前工作区。基线 HEAD 为 `b4ea6ad7098dee538415c8b36475cfda41b27aec`，保留所有现有改动，不 reset、切/建分支或覆盖其他会话文件。

#### 必须关闭的问题

1. **B01：拒绝重试前的数据保护。** `_fallback_to_singlethread` 不能先删块再让 worker 判定 Retry-After 超预算；单线程 `download()` 也不能先截断 tmp 再拒绝等待。先检查当前 run/停止/共享窗口与有效 not-before；不启动时不删除、截断或清零，错误说明须与磁盘事实一致。正常 protocol 降级仍安全从零，不将无资格旧块拼到新下载。真实本地 HTTP 预置断点，制造 protocol + 503/429 长等待；断言每块字节、数据库进度、无降级 GET、无成品。单线程继承长等待的原 tmp 也须保留。
2. **B02：预算门禁不得依赖 not-before 仍在未来。** 继承旧窗口且无头/头已到期，或等待后的调度延迟、暂停跨过截止，均不能继续自动发请求。门禁须紧邻实际新请求，覆盖内外层/降级，所有块复用同一窗口，不按轮重建预算。预算只约束自动重试启动/等待：无错误长下载和窗口内已发出的成功流允许超过边界，不在 iter_content 读块时误杀。用可控时钟与真实 GET 计数证明 R09/R11/R12，不真的等 300 秒。
3. **B03：统一库状态与过时 UI 事件。** 数据库已完成/校验失败/取消/暂停/等待时，迟到 downloading/verifying/旧终态事件不得覆盖当前状态、解除冻结、嫁接旧 notice/原因。复用现有单任务查询/队列，不改回调协议/DB，不每个 tick 查库。真实 Tk 覆盖 completed 100/100 → 旧 downloading notice → 旧 0/0 progress，卡片与已打开详情仍完成/100%；另覆盖空文件、校验失败、取消、暂停、真正新 run 和同状态 downloading 即时 notice。详情自身也要防下载字节不变但 total 变成 0 的迟到数据。
4. **B04：真正的 fatal+protocol 夹具。** 原条件 `bytes 1024-2047` 写错，实际请求是 `bytes=1024-2047`。先断言夹具确实分别返回 200 protocol 与 403 fatal，再用 Event/Barrier 固定 protocol 先完成、fatal 后完成，不凭 sleep 猜顺序。断言 403 正式 GET 一次、未 fallback、旧文件字节保留、无成品、线程和槽位收敛。fatal+transient 也补实际响应计数/已有字节断言。至少一个禁用关键修复后必须失败的反向验证；只在内存替换行为，不改产品文件实现反向验证。
5. **B05：无效字节不能伪装精确 0/0。** 转换失败不得把有效 total 一并归零；负小数/NaN/Infinity/负整数/非法字符串/矛盾大小不能被 int 截断成“空文件完成”。小的纯函数确认有效非负整数字节后才认精确 0/0。保留已知大小比例、三项大小返回值和 DB 原值；卡片/详情一致，不造数值框架。添加这些值的表驱动用例和真实 0/0 反例。

#### 验证、范围与交回

- 只允许原第 7 节源码及必要测试/README/原设计/原进度档。TaskManager/数据库结构/设置/浏览器桥接/扩展/yt-dlp/悬浮条/字体/DPI/版本号不改。真实 data/temp/downloads/output/dist 保留；不打包、提交、推送、发布或写长期记忆，不自动启动代理。
- 环境为 `D:/anaconda3/envs/py310_env/python.exe`；自检用临时配置/SQLite/分块/成品和本地 HTTP，真实 Tk 沿用隔离夹具及主线程 GC 收尾。不改系统代理/注册表、不操作用户运行程序。
- 补 R10 真实暂停/取消/退出等待中无追加 GET；R09/R11/R12 验证实际边界，不能只测私有 wait 方法。R13 受限浏览器上下文及 F09 深浅主题/混合 DPI 若未做继续明列，不拿别处通过数字代替。
- 新增/修正用例通过后，定向重跑 `test_practical_improvements`、`test_download_regressions`、`test_unknown_size_download`、`test_safety_regressions`、`test_task_batch_operations`、`test_browser_file_download` 及涉及的 GUI 回归。不弱化旧断言；全量 discover、公开站点、EXE/安装包和独立验收仍交回 Codex 后安排。
- 更新原设计第 9 节与原功能进度档，不另建同功能档。逐 B01–B05 写明根因、修改符号、正向/反向验证、命令/耗时/结果与未执行项，首轮记录留作历史。
- 最终状态只写“二轮整改源码完成、待 Codex 再次独立验收”，或如实列出未完成；不能自行将独立审查结论改成通过。

### F. 二轮独立复核后的三轮残余整改（2026-10-22）

在 `D:/vscode_workspace/downLoad_project` 继续既有功能。先完整读原设计第 9 节最新二轮独立复核、原进度档及本文件 A–E。只修三处，不重开 B02–B04，不从头实现；HEAD b4ea6ad7098dee538415c8b36475cfda41b27aec 仅为核实基线，不据此 reset 或覆盖改动。

1. **B01/P1：保留旧块直到实际非 Range 请求获准。** 初检后立即删块，worker 后续启动 / 等待 / 暂停跨截止拒绝，仍造成无 GET 却丢数据。以最小改动复用 run、停止、预算和等待，延迟旧块清理 / 进度重置到实际整流启动的安全提交点；拒绝启动保留旧块 / 原 tmp / DB 真实进度。不能只多一次初检、删保留文案、拼接旧块或新增备份系统 / 持久化框架。补初检通过到 worker 启动前跨截止，以及真实降级等待暂停跨截止两种用例；后者沿用 RetryMatrixTests，两块各 512 B、计划键 512/1536、protocol 200 + 503/429 Retry-After 2 秒、窗口 2.5 秒、暂停 2.6 秒，下载 / 暂停 / 恢复用真实生产方法。断言 1024 B / 每块 / DB 保留、非 Range GET 0、无成品、原因准确、线程槽位收敛；保留无 transient 与到期后正常降级的精确字节 / 无重复等待回归。非 Range 始终从零。
2. **B05/P2：严格校验贯穿真实入口和缓存。** 核查 MainWindow 的 _on_task_added / _add_task_widget / _update_task_status / _render_task_progress / _update_task_progress，以及详情构造 / update_status / update_progress。禁止严格校验前 int(value or 0)，不缓存无效值为 0 后冒充有效空文件，复用纯函数、不造框架、不改 DB。临时 SQLite + 真实 Tk 覆盖 -0.5、bad、负整数和真实 0/0，首屏 / 状态 / 进度 / 已打开详情均无异常；无效值不能满条或已完成 · 0 B，有效 total 保留。正常空文件、已知比例、冻结、未知大小和浏览器拒绝不回退。
3. **修正 RetryGateTests.test_b01_singlethread_refusal_keeps_existing_tmp。** 创建 downloader 后向 Path(downloader.temp_file) 写 9 B，断言同一真实路径，明确 use_range=False；原 single.tmp 与 helper part0 不同且默认 Range。补 downloaded_bytes、正式 GET 0、拒绝原因断言。至少一项仅在内存禁用关键门禁 / 保护后须失败的反向验证，不能改产品文件做反向实现。

使用 `D:/anaconda3/envs/py310_env/python.exe`，独立临时配置 / SQLite / 分块 / 成品、本地 HTTP、真实 Tk，沿用主线程 GC 收尾。先新 / 修正用例，再定向 retry_matrix、practical_improvements、download_regressions、unknown_size_download、safety_regressions、task_batch_operations、browser_file_download 与受影响 GUI；不弱化原断言，数量注明定向或全量，R13 / F09 未执行明列。

遵守原允许文件范围，保留真实数据与已有改动；**不打包、不改 dist / 版本号、不提交 / 推送 / 发布、不写长期记忆、不自动启动代理**。更新原设计第 9 节与原进度档，保留前两轮历史；交回根因、符号、正反向验证、实际命令 / 数量 / 耗时 / 未执行项，最终只报三轮残余源码完成、待 Codex 独立复核，不自行改成审查通过。

### G. Codex 已接手完成（2026-10-23，当前状态）

用户要求接续原聊天并直接完成，不再延续“只审查、交回 Pi 编码”。Codex 复核 Pi 改动后，补修最终门禁前超长 tmp 重置、拒绝启动钩子/进度监控、详情显式 NULL、浏览器 HTTPError 的状态/Retry-After 与超长前导零；纠正非 Range 响应夹具和并发分目录起跑竞态，保留原断言。

最终完整 discover 628 项 / 831.203 秒通过，Node 扩展 43 项通过；源码独立验收已完成。F09 深浅主题与 100%/150% 缩放有真实 Tk 布局测试，物理混合 DPI 未人工验收；未打包、未改原 dist/版本号、未提交/推送/发布、未写长期记忆。后续正式交付须按用户新的明确授权执行，不重复实施 A–F 或从头设计。
