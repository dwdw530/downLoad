# 四项实用改进：设计与验收约定

- 日期：2026-10-22（Asia/Shanghai）。
- 项目：`D:/vscode_workspace/downLoad_project`，daw 下载器。
- 调研基线：工作区 v0.11.1，HEAD `b4ea6ad7098dee538415c8b36475cfda41b27aec`；设计开始时 Git 工作区干净。实施前重新核对，不为对齐基线回退代码。
- 当前状态：**2026-10-23 Codex 接手残余修复，源码与原 dist/EXE/安装包验收通过，正在完成 v0.12.0 GitHub 发布核验。**完整 Python/真实 Tk 628 项、扩展 43 项、原生 EXE 三组、本轮打包版专项与安装包七组通过；前两轮“未通过”记录保留为历史。
- 实施提示词：`D:/vscode_workspace/downLoad_project/docs/prompts/pi-practical-four-improvements.md`。
- 历史功能入口：`D:/vscode_workspace/downLoad_project/docs/context-sessions/20251214-1555-老王下载器功能分析.md`。
- 分工历史：最初 Codex 设计、Pi 编码，Codex 独立审查。2026-10-23 用户明确 Pi 无法继续，改由 Codex 直接检查、修复并验证；随后明确授权原 dist/EXE/安装包、提交推送与 GitHub 发布。**不保存或同步长期记忆，不自动启动代理。**

## 1. 范围、工作量与不做事项

| 编号 | 目标 | 预估 | 主要接入点 |
|---|---|---|---|
| F | 卡片和详情窗显示安全、可理解的失败原因 | 小到中 | 已有 error_message、状态事件、任务详情窗 |
| R | 修正混合错误重试，并尊重 Retry-After | 中 | 分块错误、内部重试、外层重试与停止保护 |
| C | 左侧增加“已暂停”分类 | 小 | 原分类按钮、计数、筛选和持久化 |
| P | 修正未知大小与空文件的进度显示 | 小 | 原大小格式化、卡片与详情的进度渲染 |

合计为**中等改动**。复杂度主要在重试时间和并发边界，不需要新依赖、数据库迁移、设置项或新下载模式。

本轮不做：更新失效链接、防睡眠、普通文件与视频统一总限速、侧栏滚动、分页/虚拟列表、定时下载、跨任务重复检测。不得因为本设计提到相关原因而顺带实现新按钮。

保持：其他字号、侧栏 240px、左侧 13 逻辑像素及标题只加粗、主题、菜单、悬浮条、批量操作和自动分目录。仅原因展示允许局部卡片/详情布局调整，不重新设计整窗。

## 2. 已核实的现状与证据边界

下表短路径均相对 `D:/vscode_workspace/downLoad_project/downloader`，符号比行号更适合作为实施定位依据。

| 位置 / 符号 | 当前行为 | 本次结论 |
|---|---|---|
| `database/db_manager.py`：update_task_status | 已有 error_message 列；失败、暂停原因可落库 | 复用，不加字段，不批量改旧记录 |
| `core/download_engine.py`：_fail_task、_merge_and_finish | 回调携带失败原因；空间不足会 paused 并保留分块 | UI 同时展示有原因的自动暂停 |
| `ui/main_window.py`：_update_task_status、_add_task_widget | 只保存下载中“重试/降级”临时提示，失败原因没有显示 | 增加独立原因行，覆盖恢复首屏 |
| `ui/task_detail_window.py`：_create_ui、update_status | 只有进度/速度/状态；状态推送没有原因 | 接入同一安全解释函数 |
| `core/download_engine.py`：_run_chunk_rounds | protocol 优先；只在所有失败都不是 transient 时停止 | fatal 与 transient 混合时，fatal 被放回下一轮 |
| `core/chunk_downloader.py`：_download_chunk、_wait_before_retry | HTTP 错误只分类；内部使用固定指数退避 | 没有读取 Retry-After |
| `ui/main_window.py`：_build_sidebar、_refresh_sidebar | 状态按钮没有 paused，暂停任务没有专门计数入口 | 只补入口与计数，沿用匹配规则 |
| `utils/file_utils.py`：format_progress_texts | total_size <= 0 一律显示未知 | 完成的真实空文件需特判 |
| 主窗 _update_task_progress、详情 _refresh_stats | 总大小为 0 时进度一律 0.0% | 未知大小不能伪造百分比，空文件完成不能显示未开始 |

本聊天设计前已经做过不联网、不建数据库、不写文件的隔离方法检查：失败原因未显示、空文件完成仍 0.0%、403 与临时错误混合时致命分块重复三轮。它们是**现状复现**，不是本设计的新功能验收。其余结论来自定点静态阅读；本轮未跑新功能测试、全量回归、真实网站或 EXE。

## 3. F：失败原因展示

### 3.1 卡片与详情

卡片保留原状态短标签，在详情行与按钮行之间增加一条可换行的“原因”行：

```text
状态：下载失败
原因：服务器拒绝访问（HTTP 403）
[开始/重试] [取消] [删除]
```

上例第一行示意原有状态标签，不要求改变原详情行或增加重复状态行。原因区只显示原因，不在卡片塞长建议。没有问题时隐藏原因行；其余卡片结构、字号及按钮位置关系不变。行宽随可用宽度调整，最多两行，较长内容省略；不能简单拼到原固定宽度状态标签而撑开卡片。隐藏/恢复在状态变化时发生，进度每跳一次不得重建控件。

双击仍打开原任务详情窗，在统计与图表之间增加问题区域：安全原因 + 简短处理建议。没有问题时隐藏。详情允许安全解释换行，不要求另加弹窗、复制错误、打开日志、更新链接等入口。不要自动弹出失败提示打断用户。

### 3.2 安全解释，而不是原始异常回显

建议新增一个小的纯展示模块 `downloader/ui/task_presentation.py`，不导入 Tk、不查库、不联网。提供安全问题解释与第 6 节进度值函数；不引入通用错误框架。

可采用兼容的小接口，名称可依现有习惯调整：

```python
describe_task_problem(status, message, download_type='file') -> tuple[str, str]
# 返回 (卡片原因, 详情建议)，无需显示时为 ('', '')。
format_task_progress(downloaded_size, total_size, status) -> tuple[float, str]
# 返回 (进度条 0..1, 百分比区域文本)，具体规则见第 6 节。
```

安全规则必须先于渲染：

1. error_message 与回调 message 都不是可直接展示的可信字符串。不得把原始网络异常、HTTP 响应正文、yt-dlp 输出或浏览器上下文原样塞进控件。
2. 先在内存清理 URL、Cookie、Authorization、代理凭据、签名/token 字段及控制字符，再只识别明确的错误类别，用固定中文文案生成解释。Cookie/Authorization 的多词值要整体隐藏，不能只遮第一个词。
3. 不认识的消息使用固定兜底；不要靠宽松正则原样回显“剩下的文本”。不显示主机、私有地址、用户目录或任意原始片段。浏览器任务与普通任务采用同一展示安全底线。
4. 原有 safe_error 在 sensitive=False 时会返回原文；yt-dlp 的 sanitize_output 主要服务日志且对多词凭据遮蔽不足。不要直接把它们当作 UI 安全保证，也不要为复用而引入 UI -> 视频引擎的依赖。
5. 本次只增加安全展示，不扫描/重写历史数据库、不复制敏感错误到新日志或文档。问题区域无需输出完整原文。

| 明确证据 | 卡片原因示例 | 详情建议示例 |
|---|---|---|
| HTTP 401/403 | 服务器拒绝访问（HTTP 403） | 链接可能失效或需要授权；浏览器任务可刷新原页后重新添加。不能断言一定是过期/登录问题。 |
| HTTP 404/410 | 资源不存在或已移除（HTTP 404） | 检查来源链接，获取可用地址后重新添加。 |
| HTTP 429 | 请求过于频繁（HTTP 429） | 等待后重试，可减少同时下载数或线程数。 |
| HTTP 5xx | 服务器暂时出错（HTTP 503） | 稍后重试；不建议连续快速点击。 |
| 明确超时/连接异常 | 连接超时 / 网络连接失败 | 检查网络与代理后重试；不展示异常里的目标地址。 |
| 现有空间不足消息 | 磁盘空间不足 | 释放相应磁盘空间后继续；不承诺不受现有续传身份校验影响。 |
| 明确远端版本变化 | 服务器文件版本已变化 | 重新开始时会按原身份校验安全处理，不能强行拼接旧断点。 |
| 明确哈希不匹配 | 文件校验未通过 | 检查预期校验值或来源，确认后重试。 |
| 明确文件合并/读写/同名失败 | 文件合并失败 / 无法保存文件 / 保存位置已有同名文件 | 检查空间、权限或文件占用；不要建议关闭安全检查或覆盖旧文件。 |
| 没有原因的 failed | 未记录具体失败原因 | 检查网络、磁盘或下载组件后重试。 |
| 未知异常内容 | 下载遇到异常 | 未能确定具体原因，请检查网络、磁盘或下载组件后重试。 |

错误类别只依据明确证据，不凭来源站点猜测。重试预算耗尽文案可识别为“本次重试已结束”，服务端等待超预算可识别为“服务器要求较长等待”。

### 3.3 状态与数据来源

- failed、verify_failed 显示原因；无原文也有安全兜底。
- paused 仅在具有明确问题原因时显示，例如合并前空间不足。手动“已暂停”、稍后下载、程序重启纠偏不显示错误。
- pending、downloading、verifying、completed、cancelled 不沿用上一轮失败原因；下载中已有重试/降级 notice 保持原样。
- 首次建卡、恢复任务列表、双击已有任务、状态更新四个入口全部接入。首屏读取已存在 error_message，不要求发生新回调才显示。
- 状态事件与数据库都已有的情况下，使用**对应当前状态**的持久化原因；回调只作为该状态的补充。避免旧失败事件在新一轮开始后重新显示原因。普通暂停回调“已暂停”不能冒充故障。
- `_update_task_status` 已会查该任务；若落库状态与传入事件不一致，使用当前落库状态或丢弃过时事件，不能只换原因却仍用旧事件把卡片改回 failed。相同 downloading 状态的即时重试 notice 保留；不修改回调协议或引入新持久化序号。
- 扩展 `TaskDetailWindow.update_status(status, message=None)` 时保留旧调用兼容；主窗推送安全问题需要的当前原始原因，详情使用同一纯函数。不让 worker 读/写 Tk。
- 重试、暂停、完成时同步清理/更新问题区域；已打开详情窗不能一直停留在旧原因。关闭/删除详情窗后迟到事件不触碰销毁控件。
- 不增加持久化 UI 错误字段，不改变 TaskManager 状态机与现有数据库含义。

## 4. R：重试逻辑

### 4.1 混合错误优先级

保持现有 HTTP 分类：403/404/410、资源身份变化等为 fatal，超时/断流/429/5xx 等为 transient，Range 响应不符合协议为 protocol。

本轮外层决策按 **fatal > protocol > transient > 全成功**：

1. 当前分块 fatal 内部只请求一次，不做自己的指数重试。
2. 协调器发现任意 fatal 后不新增轮次、不启动单线程降级。保存安全失败原因，通知本轮尚未退出的其他分块停止继续工作，随后收敛本轮 futures，再写任务失败并释放队列槽位。
3. 复用分块自己的 cancel/停止标志，不调用“取消任务”把整条任务写成 cancelled；不得在工作线程内等待包含自己的线程池 shutdown 或调用会造成自等待的全任务停止方法。
4. 已完成块和其他块的实际已下载字节写回后保留；不得清零、删除或合并发布不完整结果。收敛完成前不提前写终态，防止迟到回调重新创建进度汇总或写坏新会话。
5. fatal 与 protocol 同轮出现时仍以 fatal 为准，不进入会删除旧块的降级逻辑。只 protocol 或 protocol + transient 时继续原 Range 降级流程。
6. 只有可重试错误时才走下一轮；只放回失败块，完整块不重下。

“停止”是停止新增请求并通过既有可取消等待/请求超时收敛，不保证瞬间中断正在阻塞的 requests 网络读。不能为做到毫秒返回而强杀线程或删除断点。

### 4.2 Retry-After 解析与等待

依据 RFC 9110 §10.2.3，Retry-After 可以是非负整数字符串或 HTTP 日期；RFC 6585 §4 允许 429 携带该头。官方来源见第 10 节。

本轮只在普通分块下载器的 HTTP 429 / 503 错误上读取该头，覆盖普通单线程、Range 分块以及使用同一下载器的浏览器文件/MP4 请求。不调整探测次数、yt-dlp 重试或浏览器协议。

- 去两端空白；非负整数按秒解析。0 有效，负数/小数/NaN/拼接值/非法日期按“无有效头”退回原退避。
- HTTP 日期按 UTC 算与收到响应时间的差，过去时间视为 0；使用标准库日期解析，兼容其支持的合法 HTTP 日期，不能用当前本地时区猜测 GMT。
- 收到响应时只转换一次为 `retry_not_before` 单调时间戳。之后调度与等待都用 monotonic，系统时间变更不改变已确定的等待。
- 对明显超过预算的合法大整数安全判定“超预算”；不能因 float 溢出、裁短或 int 转换长度限制而把很长等待退回成 1 秒。对恶意超长非数字头有长度防护，不保留原头。
- 下次内部请求时间为 `max(原指数退避到期, retry_not_before)`，不是两个等待相加。
- 外层重建下载器时按 chunk_id 传递未消耗的绝对 not-before；协调器可以按最大 not-before 集中等待，不能重建后把同一个“等 60 秒”再次从头算一遍，也不能因内部次数用完就绕过服务端等待。
- 同一 run 因 protocol 降级单线程时，也复用既有预算并遵守本轮仍有效的最大 not-before；不能通过切换下载路径绕过同一任务已经收到的等待要求。若没有 transient/有效等待，降级时序保持原样。
- 等待结束、准备发请求前再查取消、暂停和预算。分段检查间隔不超过现有 0.2 秒，不在 Tk 线程 sleep；暂停/取消/退出后不能补发请求。
- 保留已有下载中重试/降级提示，不新增逐秒倒计时或新设置。日志只记状态码、轮次、等待秒数和内部任务 ID，不写原头、URL 或凭据。

### 4.3 三轮和五分钟的精确定义

本次不改变 retry_times 的原有“内部最多尝试次数”含义，也不增加外层三轮。尤其不能把配置 1 偷改成“首次 + 一次重试”。

为避免新增长等待被两层重试不断重置，同时避免限速大文件成功传输被五分钟误杀，规定：

- **300 秒是自动重试启动/等待的窗口，不是整条下载的时长上限。**窗口从本 run 第一次 transient 开始，不在成功传输刚启动时开始；没有错误的长下载不受影响。
- 同一个普通多线程任务的内外层共享一个轻量重试预算，所有块和所有轮次不重置；普通单线程使用自己的同类预算。新任务 run 创建新预算，不存数据库，不跨重启持久化。
- 可在 chunk_downloader.py 内用小的、带锁的重试预算对象保存首次失败时间/截止时间，并作为可选参数传给 worker；引擎创建共享实例。只为共享两层时钟服务，不引入全站限流器、后台调度器或复杂状态框架。
- 原有 `CHUNK_RETRY_ROUNDS = 3`、`CHUNK_RETRY_DEADLINE = 300` 继续作为引擎策略；内部默认窗口不能和它出现两份互相矛盾的配置。相关新参数追加且有默认值，旧构造调用兼容。
- 等待目标达到或超过窗口边界时，不缩短服务端等待后抢发请求，直接结束自动重试，task 为 failed，保留当前数据，原因“服务器要求等待时间超出本次重试上限，已保留下载数据，可稍后重试”。
- 无有效头但窗口用尽时，failed 并说明自动重试已结束；次数已用尽但仍需下轮时，也先检查同一预算。
- 窗口内已发出的成功传输允许正常收尾，即使数据读取超过窗口；后来失败不能在过期窗口再发新自动重试。不要把预算传成成功响应的数据流硬超时。
- 不因预算到期修改服务器请求超时、限速器等待、资源身份校验、Range 降级或未知大小 EOF 判定。手动重试仍走原新 run 和身份检查。

时序示例：收到 429，要求等 60 秒；原退避 1 秒，则最早 60 秒后请求。内部次数用完后外层若只需额外等 3 秒，则按原轮次策略等 3 秒，不再重加已经消耗的 60 秒。收到要求等 3600 秒且窗口只有 300 秒，则不发第二次请求。

## 5. C：已暂停分类

在原状态组中按顺序显示：全部、下载中、等待中、**已暂停**、已完成、失败。键为 `status:paused`，只匹配 paused，不把 pending/failed/cancelled 混入。

- 沿用原生 Label、字号、标题与高亮规则；分类括号数量按全部任务累计 paused，和现有分类统计口径一致。右侧列表再与搜索和隐藏已完成取交集，不因搜索改变左侧数量口径。
- 选择持久化继续写已有 ui_active_category，不增加新配置；保存的 `status:paused` 重启后恢复。暂停任务为 0 时仍保留按钮，显示空态，不自动退回全部。
- 稍后下载、手动暂停、空间不足暂停、重启恢复后的 paused 都可见；空间不足任务同时显示第 3 节原因。
- 从暂停恢复为 downloading/pending 后离开本分类，计数同步；被筛掉的已选任务按既有规则清选择，已提交批量快照不改变。
- 不改“继续全部”、批量开始、list_order 和出队规则，不为补分类增加新的状态。
- 固定 900×600 下确认该新增项能访问、不遮挡其他固定分类；不顺带解决历史站点过多不能滚动问题。真遇到固定组放不下先报告，不擅改全局字号或新增滚动方案。

## 6. P：未知大小与空文件进度

范围只包括任务卡片与任务详情窗。悬浮条绘制、下载历史、下载传输、数据库哨兵与文件验证保持原样。

沿用 `format_progress_texts` 的三项返回值，由它处理大小/ETA/速度；新增纯进度展示函数只返回进度条值和百分比区文本，两处调用同一规则，不能各写一套特判。

| 条件 | 进度条 / 百分比区 | 大小行 | ETA / 速度 |
|---|---|---|---|
| total_size > 0 | 原比例，UI 限在 0..1；显示原一位小数百分比 | 已下载 X / Y | 下载中有有效速度才估算，其他保持原规则 |
| total_size <= 0，非明确空文件完成 | 0 的静态轨道 / `--` | 已下载 X / 未知 | 剩余 --；速度仍按真实下载状态 |
| status=completed、total_size=0、downloaded_size=0 | 满条 / `已完成` | `已完成 · 0 B` | 剩余 -- / 0 B/s |

- 未知大小下载中不显示 0.0%、不假装已知总量，也不需要 indeterminate 动画和额外定时器。
- 只用“completed + 两个字节值恰为 0”辨认真实空文件完成。paused/pending/failed 的 0 不算完成；负值或“total=0、downloaded>0”的不完整旧记录不冒充空文件。
- 已知总大小的 verifying/verify_failed 保留真实传输比例，100% 只说明字节已收齐，不替代校验状态；本次不改变发布与校验的先后规则。
- 完成状态更新时必须立即用当前落库 downloaded_size/total_size 刷新进度，即使没有随后进度事件。启动回填和详情首屏使用同一逻辑。
- 主窗可以缓存最后的字节值，状态变更时从已有 task 读取权威值。已经 completed/verify_failed 的卡片不能被迟到的下载进度重置为 `--`/0%；不得为此每个进度 tick 全量查库。
- 从空文件 completed 转为明确的新下载状态时不能保留满条；正常已知大小完成保持 100.0%。不写 total_size=1 或 downloaded_size=1 冒充计算进度。
- 详情状态更新和进度更新均刷新；空文件“进度先到、状态后到”和相反事件顺序都应正确。
- EOF 是否完整仍由现有引擎验证，UI 不能凭 bytes=0 自行写 completed。浏览器未知大小/空文件仍拒绝，未知大小新非 Range 请求仍从零，不拼接旧内容。

## 7. 允许改动与实施顺序

| 文件（项目根下） | 允许范围 |
|---|---|
| `downloader/ui/task_presentation.py`（可新增） | 小的纯安全问题解释和纯进度展示，供卡片/详情共用 |
| `downloader/ui/main_window.py` | 原因行、数据同步、已暂停分类/计数、共享进度渲染及必要迟到事件保护 |
| `downloader/ui/task_detail_window.py` | 问题区、状态接口兼容扩展、共享进度渲染 |
| `downloader/utils/file_utils.py` | 原 format_progress_texts 的空文件大小/终态速度处理，三项返回保持兼容 |
| `downloader/core/chunk_downloader.py` | 错误等待信息、Retry-After 解析、轻量预算和可停止等待，保留下载与验证路径 |
| `downloader/core/download_engine.py` | fatal 优先与收敛、预算跨内外层共享、not-before 跨轮传递，不改任务创建规则 |
| 必要测试、README、本文实施结果区与原功能进度档 | 如实说明行为及源码实现/隔离自检/独立验收边界 |

不修改 TaskManager/数据库结构、settings/config、BrowserBridge、Native 协议、chrome-extension、yt-dlp 命令、悬浮条渲染、版本号、dist、安装包或真实 data/temp/downloads/output。确需越界，先指出具体调用点和原因，不能借机重构。

建议分两批：

1. 展示批 F+C+P：纯函数先行，卡片/详情接入，补启动恢复、事件顺序和筛选交集测试。
2. 重试批 R：先 fatal 混合错误，再 Retry-After 与预算；本地 HTTP + 假时钟验证，随后跑既有安全/下载回归。

用户提交给 Pi 本提示词即允许源码实现和**隔离定向自检**；全量、真实 GUI/下载和独立验收由用户交回 Codex 后另按指令执行。测试必须使用 `D:/anaconda3/envs/py310_env/python.exe`（Python 3.10），不替换环境、不操作用户浏览器/原下载器。

## 8. 验收矩阵（设计要求，实际执行情况见第 9 节）

### F：问题展示

| ID | 场景 | 必须证明 |
|---|---|---|
| F01 | HTTP 403 当前回调 | 卡片显示原因，详情给可能原因和建议，不断言一定过期 |
| F02 | failed 原因落库后重启 | 首屏和详情都显示，不等新回调 |
| F03 | 空间不足 paused | 暂停分类可见，原因与恢复建议正确 |
| F04 | 手动暂停/稍后下载/重启纠偏 | 不显示错误区域 |
| F05 | failed/verify_failed 无原因或未知异常 | 固定兜底，不抛异常、不回显原文 |
| F06 | 原因含 URL、签名、Cookie、多词 Authorization、私有地址、换行 | 卡片/详情和新日志中没有秘密值或地址残片；未知原文不回显 |
| F07 | 重试入队/开始、完成、取消；详情已经打开 | 旧失败原因被清除，无需关窗重开 |
| F08 | 旧失败事件晚于新状态/删除详情 | 不覆盖新状态原因、不触碰销毁窗口 |
| F09 | 深浅主题、900×600、长原因、放大窗口 | 原因不挤占状态标签、不遮按钮、不靠改字号凑布局 |

### R：重试和数据保护

| ID | 场景 | 必须证明 |
|---|---|---|
| R01 | 同轮一块 403、一块 503/超时，retry_times > 1 | 403 正式 GET 仅一次；发现 fatal 后不补轮，不合并、不降级；保留已有字节 |
| R02 | protocol 与 fatal 同轮，protocol 先返回 | fatal 优先，旧块未被降级清零或删除 |
| R03 | 全成功 / 仅 transient 后恢复 / protocol + transient | 原完成、只补失败块和单线程降级仍正确 |
| R04 | 全 transient 耗尽，retry_times=1 | 原三轮次数上限保持；探测不算正式分块请求 |
| R05 | 429/503 + 秒值 Retry-After | 下一次正式请求不早于到期，无重复叠加等待 |
| R06 | 未来/过去 HTTP 日期、0、合法空白包裹 | UTC 解析正确，过去/0 仍服从原退避 |
| R07 | 缺头、负数、小数、NaN、非法日期、拼接值 | 安全回退，错误仍按原状态分类 |
| R08 | 合法极大整数、3600 秒、窗口边界 | 不溢出、不提前重发、不无限等；失败原因和残片保留 |
| R09 | 内部次数用完后外层重建；多个块不同 not-before；protocol 降级 | 绝对时间和预算跨轮/同 run 路径保留，等待不从头算，也不能绕过 |
| R10 | 等待中暂停、取消、退出、新 run | 停止后没有追加 GET；旧会话不写坏新状态/进度，不误触发完成后动作 |
| R11 | 无错误限速下载超过 300 秒；窗口内重试后成功流超过边界 | 预算只约束新重试，不误杀成功下载 |
| R12 | 多块共享首次 transient 时间、慢块晚失败 | 预算不能按块/按轮重置；旧窗口不发新自动重试 |
| R13 | 浏览器受限上下文的 429/503 与成功恢复 | 继续原浏览器请求方法、头/Cookie 域校验和身份检查，不泄漏凭据 |
| R14 | 断流 + Range/非 Range、已完成块与部分块 | 实际结果精确字节；Range 按偏移恢复，非 Range 新请求从零 |
| R15 | 403 发现时其他分块仍网络阻塞/回调 | 请求按既有 timeout 收敛，不自等待死锁；最终失败、槽位释放、没有迟到数据库写坏 |

### C：暂停分类

| ID | 场景 | 必须证明 |
|---|---|---|
| C01 | 多种状态混合 | paused 计数和列表准确，不包含 pending/failed/cancelled |
| C02 | 暂停分类 + 搜索 + 隐藏完成 | 原交集与空态正确 |
| C03 | 保存暂停分类再启动；暂停任务为 0 | 选择持久化，空组不退回全部 |
| C04 | 暂停任务开始/入队，已有选择与批量快照 | 列表/计数更新，筛掉选择清除，已提交快照固定 |
| C05 | 空间不足/重启恢复/稍后下载 | 都在暂停分类，问题区域仅对真实故障显示 |

### P：进度显示

| ID | 场景 | 必须证明 |
|---|---|---|
| P01 | 未知大小下载中且字节递增 | 百分比 --、真实字节/未知、ETA --，速度真实 |
| P02 | 明确零字节 200 成功 | 引擎实际 completed 后满条、已完成 · 0 B；数据库仍为 0/0 |
| P03 | 0/0 的等待、暂停、失败、校验中 | 不误判空文件完成 |
| P04 | 空文件完成的进度/状态任意顺序；只发状态事件 | 最终一致，已打开详情即时更新，迟到数据不倒退 |
| P05 | 重启回填空文件完成、打开详情 | 两处同一规则，无 0.0% / 未知 |
| P06 | 未知大小正文件 EOF 后引擎回填真实总量 | 转正常百分比，成品精确、哈希保留 |
| P07 | 已知大小、verify_failed、超界数据、损坏旧记录 | 原传输比例及校验含义保留，UI 不越界，不把负值/矛盾值当空文件 |
| P08 | 浏览器未知大小/空文件；EOF 资格违规 | 原拒绝与共享验证保护仍成立，不能通过 UI 放行 |

### 验证方法与交回标准

- 普通错误/进度纯函数用表驱动单元测试；UI 必须有真实 Tk 控件与事件循环测试，不能全靠 SimpleNamespace 或 Mock 控件冒充布局/交互验收。
- 重试同时需要受控工作线程/假时钟和本地 HTTP。记录正式 GET 的 Range、单调时序和次数，明确排除 bytes=0-0 探测。用事件屏障固定混合错误顺序，避免竞态导致时好时坏。
- 日期、300 秒与长成功下载用可控时钟验证，不真的睡几分钟；至少一条短 Retry-After 本地 HTTP 真实等待验证实际 GET 间隔。
- 断流用真正无结束块/错误 chunked 或定长短流，不把合法 EOF 当错误。至少一个用例禁用关键新行为后必须失败，防止夹具只测到探测/正常成功。
- 原 `tests/test_download_regressions.py`、`test_unknown_size_download.py`、`test_safety_regressions.py`、`test_gui_workflows.py`、`test_task_batch_operations.py` 和浏览器文件/视频相关回归保持，不弱化断言；新增用例可集中到一个非 GUI 文件，GUI 场景优先复用现有夹具。
- 临时 SQLite、配置、成品和分块均使用独立临时目录；不能改真实配置、下载记录、原 dist 或浏览器注册。遵守现有 GUI 测试主线程 GC 收尾规则。
- Pi 交回：文件列表、接入符号、设计偏离、实际自检命令/环境/结果/未执行项，明确“源码实现，待 Codex 独立验收”。不声称 dist 已更新或全量通过。

## 9. 实施结果记录

### Codex 设计阶段（2026-10-22）

截至 2026-10-22：Codex 已完成设计与提示词，未实现上述源码、未执行新功能验收。Pi 实施后在本节和原功能进度档追加实际结果，保留历史；不能把第 8 节设计要求打勾冒充执行记录。无需写入任何本地/远程长期记忆。

### Pi 源码实施与隔离自检（2026-10-22）

状态：**源码实现完成、待 Codex 独立验收**。未打包、未更新 dist/安装包、未提交/推送/发布，未写长期记忆，未改 TaskManager/数据库结构、settings/config、BrowserBridge、Native 协议、扩展、yt-dlp、悬浮条或版本号。

#### 修改/新增文件与接入符号

- 新增 `downloader/ui/task_presentation.py`：`sanitize_problem_text`、`describe_task_problem`、`is_empty_file_completed`、`format_task_progress`。纯展示、无 Tk/库/网络依赖；HTTP 码、超时/连接、空间、版本、校验、合并/保存/同名等只输出固定中文，未知内容固定兜底。
- `downloader/ui/main_window.py`：`_add_task_widget` 新增独立原因行与字节缓存；`_apply_task_problem`、`_render_task_progress`、`_update_task_status`、`_update_task_progress` 接入安全原因、终态冻结与权威字节刷新；`_build_sidebar`/`_refresh_sidebar`/`_task_matches_category` 增加 `status:paused`；`_on_open_detail`、`_push_detail_status` 传递缓存原因与字节。
- `downloader/ui/task_detail_window.py`：统计与图表之间新增问题区域；`update_status(status, message=None, ...)` 兼容扩展并接收权威字节；`update_progress` 忽略终态迟到倒退；`_refresh_problem` 与卡片共用纯函数。
- `downloader/utils/file_utils.py`：`format_progress_texts` 仅对 completed + 0/0 特判 `已完成 · 0 B` 与 0 速度，三项返回保持兼容。
- `downloader/core/chunk_downloader.py`：新增 `parse_retry_after`、`RetryBudget`、`RETRY_*` 文案与 `ChunkDownloadError.retry_not_before`；仅 429/503 读取 Retry-After；`_wait_for_retry_not_before`、`_wait_before_retry`、`_note_retry_budget`、`_sync_downloaded_from_temp` 实现分段可停等待、取最大值、预算结束与停止保留字节。
- `downloader/core/download_engine.py`：每个 task run 创建共享 `RetryBudget`；`_run_chunk_rounds` 决策改为 fatal > protocol > transient；fatal 取消同轮 worker 并收敛 futures 后 failed；`not_before_map`/`max_not_before` 跨轮与单线程降级传递；`_sleep_until` 按绝对单调时间等待。
- 新增 `tests/test_practical_improvements.py`（27 项）：安全原因/兜底/清除、暂停分类与持久化、未知大小/空文件两种事件顺序、详情终态保护、Retry-After 秒/日期/非法/巨值与预算、真实 1 次短等待 HTTP GET、fatal+transient/fatal+protocol 本地 HTTP。

#### F/R/C/P 矩阵执行情况

- F：F01–F08 通过新增真实 Tk/纯函数/本地 HTTP 用例；F09 仅验证 900×600 下卡片原因行与详情问题区布局不挤占状态标签，未做深/浅主题截图和混合 DPI/放大窗口人工验收。
- R：R01、R02 通过本地 HTTP 事件屏障/真实 GET；R03、R04、R14/R15 依赖既有回归（`test_download_regressions`、`test_unknown_size_download`）保持通过；R05–R08 通过真实短等待与可控时钟/解析用例；R09 的跨轮/降级 not-before 已实现但只做静态核对，未做多轮端到端断言；R10 的等待中取消有可控时钟用例，真实暂停/退出未新增；R11 长成功流、R12 多块慢失败、R13 浏览器受限上下文 429/503 未执行。
- C：C01–C03 通过真实 Tk（计数、搜索/隐藏交集沿用原测试，持久化与空组）；C04/C05 只覆盖暂停任务进入/离开分类和空间不足原因，批量快照规则未改且由既有批量测试保持，未新增 C04 专项。
- P：P01、P02/P04/P05/P07 通过真实 Tk 与纯函数；P03 覆盖 0/0 的 pending/failed 不等于空文件完成；P06/P08 依赖既有未知大小与浏览器回归保持通过。

#### 实际自检（Python 3.10，隔离临时配置/SQLite/分块/成品/本地 HTTP）

环境：`D:/anaconda3/envs/py310_env/python.exe`；命令均为 `PYTHONPATH=. <python> -m unittest discover -s tests -p <模块>`。

- `test_practical_improvements.py`：27 项通过，12.056 秒。
- 既有回归：`test_download_regressions` 25 通过（34.279 秒）；`test_unknown_size_download` 37 通过（13.760 秒）；`test_safety_regressions` 25 通过（33.216 秒）；`test_gui_workflows` 240 通过（564.773 秒，真实 Tk）；`test_task_batch_operations` 13 通过（17.157 秒）；`test_progress_throttle` 6 通过（0.124 秒）；`test_sidebar_typography` 4 通过（1.340 秒）；`test_browser_file_download` 15 通过（32.542 秒）；`test_stability_hardening` 19 通过（0.932 秒）；`test_stream_download` 4 通过（0.331 秒）；`test_robustness_batch` 25 通过（4.012 秒）；`test_disk_usage` 15 通过（6.694 秒）；`test_auto_directory` 首次 1 项失败、立即复跑 23 通过（7.398 秒，未改产品逻辑）；`test_features` 29 通过（29.682 秒）；`test_browser_integration` 12 通过（11.306 秒）。
- 累计执行 519 项定向回归（非一次全量 discover），退出码均为 0；未执行 Node 扩展测试（扩展未改）、真实公开站点、真实浏览器受限上下文、EXE/安装包、混合 DPI 和完整全量 discover。
- 反向验证：本地 429 使用 `Retry-After: 2`，断言第二次正式 GET 间隔 ≥1.9 秒；旧固定 1 秒退避会失败，证明读取了新头而非只测正常 EOF/探测。

#### 实现说明与偏离

- 脱敏：先整体移除 URL、Cookie/Authorization 多词值、token/签名、Windows/Unix 用户路径、私有地址和控制字符，再用固定类别表生成文案；HTTP 码从去 URL 后的文本单独提取，避免敏感头同行的状态码被一起抹掉。
- 原因恢复/清除：建卡和重启首屏读库内 `error_message`；状态事件以库内状态为准，迟到的 failed/verify_failed 被丢弃；下载中/等待/完成/取消清空旧原因，详情已打开时同步隐藏。
- 混合错误：fatal 内部只请求一次；协调器发现 fatal 后取消同轮 worker（复用停止标志，不把任务写成 cancelled），继续收敛 futures 并保留字节，随后 failed；fatal+protocol 不进入删除旧块的降级。
- 预算/not-before：`RetryBudget` 从本 run 第一次 transient 启动，多块与内外层共享；Retry-After 只转成一次绝对 monotonic，内部与外层都取 `max(退避, not-before)`；降级单线程在首次请求前也等待未到期的 not-before；超过窗口直接 failed 并保留数据。
- 暂停分类：仅状态 `paused` 计数，键 `status:paused` 复用 `ui_active_category`，零计数不退回全部，批量快照与队列规则未动。
- 零字节事件顺序：卡片/详情共用 `format_task_progress`；completed + 0/0 显示满条/`已完成`/`已完成 · 0 B`，其他 0 保持未知；状态事件立即用库内字节刷新，终态冻结卡片并保护详情不被迟到进度倒退。
- 偏离：外层三轮次数耗尽时失败文案由“部分分块下载失败…”改为统一的“本次自动重试已结束…”，语义仍为保留数据、可稍后重试；这是为了让 UI 能识别重试预算结束，不新增状态或配置。另为降级首次请求补了 not-before 等待，属于设计第 4.2 节要求而非扩大范围。
- 未完成/遗留：R09 多轮与降级后的真实端到端时序、R11 超 300 秒成功下载、R12 多块慢失败、R13 浏览器受限上下文 429/503、R10 真实暂停/退出等待中无追加 GET、F09 深浅主题/混合 DPI 人工验收；全量 discover、真实站点/EXE/安装包与独立验收由用户交回 Codex 后安排。

### Codex 独立审查：未通过，待 Pi 整改（2026-10-22）

接续原聊天 `01a162de-e921-7d91-b502-625939b6bf1f` 后，核对 HEAD 仍为 `b4ea6ad7098dee538415c8b36475cfda41b27aec`，保留 Pi 全部未提交改动。本轮只更新三份文档，不直接修改运行源码/测试，不写长期记忆，不打包、提交、推送或发布。

#### 独立验证

- Python：`D:/anaconda3/envs/py310_env/python.exe`（3.10），独立临时配置/SQLite/分块、本地 HTTP 与真实 Tk 隔离夹具。
- 定向五模块：`test_practical_improvements` 27、`test_download_regressions` 25、`test_unknown_size_download` 37、`test_task_batch_operations` 13、`test_browser_file_download` 15，合计 **117 项通过，103.336 秒，退出码 0**。非全量验收。日志：`C:/Users/lenovo/.fastctx/jobs/j-my7gsd/output.log`。
- 入口为 FastCtx Bash 设置 `PYTHONPATH=.`、`PYTHONDONTWRITEBYTECODE=1`，Python `-B -c` 内先 `sys.path.insert(0, 'tests')` 再由 unittest 加载五模块。最初 `PYTHONPATH=.:tests` 在 Windows 下产生 5 个 loader 错误，未实际运行用例，修正后才获得上述结果。
- HTTP 夹具关闭连接有 ConnectionResetError/ConnectionAbortedError 输出，最终 OK；不把该输出当真实站点问题。
- 额外反向验证：内存禁用 `parse_retry_after` 后，真实短等待用例按预期失败，GET 间隔约 **1.016 秒 < 1.9 秒**，产品文件未改。

#### 五项必须整改的问题

1. **B01/P1：拒绝超预算前删除断点。** `DownloadEngine._run_chunk_rounds` protocol 分支和 `_fallback_to_singlethread` 先删块，随后新 worker 才拒绝超长等待。隔离数据库预置两块各 512 B，同轮 protocol + HTTP 503/Retry-After: 3600 后，实际 **1024 B → 0 B、failed、无降级 GET**，文案却仍称数据保留。单线程 `ChunkDownloader.download` 继承长等待也先截断现存 9 B tmp。须在破坏性重置前检查停止/预算/not-before；拒绝启动时保留字节和进度。正常 protocol 降级仍安全从零，不拼接不可信旧块。
2. **B02/P1：过期共享预算仍发请求。** `ChunkDownloader.download` 只有 not-before 在未来才进首次预算检查。首次 transient 时钟 1000、截止 1300、not-before 1002、当前 1301，真实本地 HTTP 仍收到 **1 次 GET 并成功**。新自动请求前必须检查继承的同一窗口，即使无头/头已过期；不能把窗口变成成功响应读取的硬时限。
3. **B03/P2：旧 downloading 覆盖完成状态。** `_update_task_status` 没统一协调所有不匹配状态。真实 Tk：库 completed 100/100，收到旧 downloading 重试事件，再收旧 0/0 progress，卡片与已打开详情倒退为 **downloading/--**，frozen 被解除。须统一采用库内当前状态、丢弃旧 notice/原因，同时保留真实新 run 与同状态 downloading 即时提示。
4. **B04/P2：fatal+protocol 测试假阳性。** `test_fatal_beats_protocol_and_does_not_clear_chunks` 比较 `bytes 1024-2047`，实际生产请求为 `bytes=1024-2047`，未进入 protocol 分支。观测原测试正式响应只有 **403、503，200 为 0**；文件也无所称 Event/Barrier。修正夹具，屏障固定 protocol 先到/fatal 后到，断言实际状态码、Range、次数、无降级/成品和原文件字节；不能只检查数据库行存在。
5. **B05/P2：非法值伪装空文件完成。** `format_task_progress('bad', 100, 'completed')` 与 `(-0.5, 0, 'completed')` 都返回 **(1.0, 已完成)**。转换失败一起归零、int 截断负小数均违规；只有有效非负整数字节且精确 0/0 才能特判空文件。卡片/详情/大小行一致，不改数据库哨兵，不引入数值框架。

B01 多块复现直接调用隔离任务 `_start_multithread_download(task, True, run_id)`，没有冒充浏览器到下载全链路；B02 为真实 ChunkDownloader 与 HTTP，不只是 Mock。B03 为真实 Tk，B04 为原测试真实响应观测，B05 为纯函数检查。额外复现不计入 117 项常规通过。

#### 下一步与未执行项

执行原 Pi 提示词 E 节：先修 B01/B02，再修 B03/B05并纠正 B04；补 R09 跨轮/降级、R10 停止等待、R11 长成功流、R12 多块慢失败。不要通过清真实数据、放宽断言或改校验资格修测试。本轮未跑全量 discover、R13 受限浏览器上下文、F09 深浅主题/混合 DPI、公开站点、EXE/安装包或 dist。当前 **独立审查未通过，不可打包交付，待 Pi 整改后再交 Codex 复核**。

### Pi 二轮整改交回（2026-10-22）：自报关闭，以下文独立复核为准

状态：**二轮整改源码完成、待 Codex 再次独立验收**。未打包、未改 dist、未提交/推送/发布、未写长期记忆；范围仍限第 7 节源码与测试/文档，TaskManager/DB 结构/设置/浏览器桥接/扩展/yt-dlp/悬浮条/字体/DPI/版本号未动。

#### B01：拒绝重试前不得破坏断点

- 根因：`_fallback_to_singlethread` 先删分块并清零进度，之后新建的单线程 worker 才在“服务器要求等待超出窗口”时拒绝启动；单线程 `download()` 也在预算检查之前先以 `wb` 截断 tmp。
- 修改：`DownloadEngine._retry_block_reason()`（新增，库无关的共享窗口判定）在 `_run_chunk_rounds` 的 protocol 分支、`_fallback_to_singlethread` 的**任何删除/清零之前**、`_start_singlethread_download` 建 worker 之前各判一次；拒绝时直接 `_fail_task` 并保留原数据，调用方不再覆盖为泛化文案。`ChunkDownloader.download()` 把 `last_error` 重置、`retry_not_before` 等待/共享窗口检查全部提前到截断与 `downloaded_bytes=0` 之前，拒绝时用 `_sync_downloaded_from_temp()` 回读真实字节；`_download_chunk` 在真正 `get()` 前再判一次。
- 正向验证：`test_b01_protocol_plus_long_wait_keeps_chunk_bytes` 预置两块各 512 B，512 起点返回 200（protocol）、1536 起点返回 503 + `Retry-After: 3600`。结果 failed、文案含“保留”、两文件仍各 512 B、DB 分块仍各 512、无无 Range 请求、无成品。`test_b01_singlethread_refusal_keeps_existing_tmp` 验证继承 3600 秒等待的单线程不截断 9 B tmp 且 0 次 GET。
- 反向验证：`test_b01_reverse_without_gate_loses_chunk_bytes` 只在内存 `patch.object` 关闭引擎与下载器的 `_retry_block_reason`，复现旧行为：分块文件被删除、字节不保留，证明正向断言能抓住回退（产品文件未改）。
- 正常降级仍从零：无 transient（无预算）时门禁不拦截，protocol 仍按原流程删旧块并单线程重下；既有 `test_download_regressions`、`test_unknown_size_download` 通过。

#### B02：预算门禁不得依赖 not-before 仍在未来

- 根因：`download()` 只用 `retry_not_before > now` 触发首次检查，继承来的窗口已过期且 not-before 已成过去时直接放行，真实 HTTP 仍收到 GET。
- 修改：新增 `ChunkDownloader._retry_block_reason()`，只看共享 `RetryBudget` 截止（以及 not-before ≥ 截止），与 not-before 是否在未来无关；`download()` 无条件调用，`_download_chunk` 紧邻 `get()` 再调一次；`_run_chunk_rounds` 在 `_sleep_until` 返回后重新过门禁，覆盖等待后的调度延迟与暂停跨截止。门禁只在“启动新自动请求”处，`iter_content` 读块不再检查，故已发出的成功流可越界收尾。
- 验证：`test_b02_expired_inherited_window_sends_no_request`（时钟 1301、截止 1300、not-before 1002）与 `test_b02_gate_does_not_depend_on_future_not_before`（截止 1001、当前 1005）均 **0 次真实 GET**、failed 且文案说明重试结束；`test_r11_streaming_success_may_finish_after_window` 用 0.2 秒窗口 + 5 段各 0.12 秒慢速 206，耗时 >0.5 秒仍成功且字节精确，证明不在读流时误杀。

#### B03：库状态权威，旧 UI 事件不得覆盖

- 根因：`_update_task_status` 只保护 failed/verify_failed/paused 的部分组合，旧 `downloading`（带重试提示）能把 completed 卡片改回下载中并解除 `frozen`，随后的旧 0/0 进度又把百分比变成 `--`。
- 修改：任何与库内状态不一致的 UI 事件统一视为过时——采用 `db_status`、丢弃事件 message/notice、按库内字节重绘、`frozen` 保持；与库一致的事件（真正的 resume 先写库 downloading、同状态即时重试提示）行为不变。`TaskDetailWindow.update_progress` 在非 downloading/verifying 状态直接忽略迟到进度，防止“下载字节不变但 total 变 0”把详情退回未知。仍只复用 `_update_task_status` 里已有的单次任务查询，未新增每秒查库。
- 验证（真实 Tk）：`test_b03_stale_downloading_and_progress_cannot_revert_completion`（completed 100/100 + 打开详情 → 旧 downloading notice + 旧 0/0 → 卡片与详情保持 100%/已完成、无“重试”、frozen 仍 True）；`test_b03_empty_file_completion_survives_stale_event`；`test_b03_terminal_and_paused_states_ignore_stale_downloading`（verify_failed/cancelled/paused）；`test_b03_real_new_run_and_same_status_notice_still_work`（新 run 与同状态提示保留）；`test_b03_completed_then_new_run_accepts_downloading`；`test_b03_detail_ignores_late_progress_after_completion`。
- 测试夹具顺序修正（断言未改）：`test_gui_workflows` 中 `test_search_and_hide_completed_filter_tasks`、`test_progress_feeds_history_and_detail_window`、`test_retry_notice_shows_then_clears_when_bytes_flow`、`test_ordinary_status_message_is_not_shown`、`test_task_card_shows_size_and_remaining_time`、`test_status_change_syncs_to_detail_window`、`test_delete_task_closes_detail_window_and_clears_history` 原先直接在库状态 `pending` 上推事件，模拟的是生产不存在的乱序；现改为与生产一致“先落库再回调”。

#### B04：真正的 fatal+protocol 夹具

- 根因：旧用例用 `bytes 1024-2047`（空格）比较，生产请求是 `bytes=1024-2047`，从未进入 protocol 分支；也没有 Event/Barrier。另预置 512 B 断点后实际 Range 从 512 起，若继续按 0/1024 设计划会再次错配。
- 修改：新增 `tests/test_retry_matrix.py` 的 `_PlanHandler`，按 Range 起始字节执行计划、记录实际发出的状态码（`server.sent`）与请求、支持 `threading.Event` 屏障和慢速整流；旧的假阳性用例删除，由新夹具替代。
- 验证：`test_b04_fatal_beats_protocol_with_barrier` 用 `gates={512: protocol_done}`、`dones={1536: protocol_done}` 固定“protocol(200) 先返回、403 后发”，断言 `(1536, 200)` 与 `('bytes=512-1023', 403)` 都实际出现、403 正式 GET 恰 1 次、无降级 GET、两文件仍各 512 B、DB 分块仍各 512、无成品、`thread_pools`/`active_downloaders` 已收敛。`test_b04_fatal_plus_transient_counts_and_bytes` 断言 403 精确 1 次、503 至少 1 次且字节与成品同上。

#### B05：无效字节不能伪装 0/0

- 根因：`int(value or 0)` 把转换失败归零，`int(-0.5)` 截断成 0，于是 `('bad', 100)` 与 `(-0.5, 0)` 都能显示“已完成”。
- 修改：新增 `downloader/utils/file_utils.parse_byte_count()`：只接受 int/整值非负 float/纯十进制数字串，拒绝负数、负小数、NaN、Infinity、`-1`/`+1`/`1.5`/`1e3`/空白/None/容器；`is_empty_file_completed` 与 `format_task_progress`、`format_progress_texts` 全部改用它——无效 downloaded 按 0 参与比例但有效 total 保留，无效/负 total 显示 `--` 与“未知”，只有“有效非负且恰为 0/0 + completed”才特判空文件；DB 哨兵与已知大小比例不变。
- 验证（表驱动）：`test_b05_invalid_downloaded_keeps_valid_total`、`test_b05_invalid_total_is_not_empty_completion` 覆盖 `'bad'`、`''`、`' '`、`-0.5`、NaN、Infinity、`-1`、`'-1'`、`'1.5'`、`'1e3'`、`None`、`[0]`；`test_b05_real_zero_and_contradictory_sizes` 用真实 0/0 反例（含字符串 `'0'`）与 `1/0`、`0/1`、非终态对照。卡片/详情同源 `format_task_progress`，无新增数值框架。

#### R09–R12 补充

- R09：`test_r09_cross_round_not_before_and_byte_restore` 真实 HTTP，首轮 503 + `Retry-After: 2`（`retry_times=1` 不走内部重试），第二块直接成功；重建下载器后第二轮 GET 与外层退避 1 秒取最大，实测间隔约 **2.0 秒**（区间断言 1.9–2.9），既不重头再等也不叠加成 3 秒，任务最终 completed 且成品字节精确。
- R10：`test_r10_pause_during_server_wait_sends_no_more_get` 单线程真实 503 + `Retry-After: 5`，发出第 1 次 GET 后调用 `pause_download`，1.2 秒内请求数仍为 1、状态 paused、`active_downloaders`/`thread_pools` 已清空（取消/退出共用同一等待分段检查）。
- R11/R12：见上文 B02 与 `test_r12_shared_window_is_not_reset_per_chunk`（第一块 1000 开启 10 秒窗口，第二块 1030 晚失败不重置 deadline，随后 0 次 GET）。
- 全部时长均用可控时钟或 0.2–5 秒真实短等待，没有真的等 300 秒。

#### 本轮实际自检（Python 3.10，隔离临时配置/SQLite/分块/成品/本地 HTTP）

环境：`D:/anaconda3/envs/py310_env/python.exe`；命令 `PYTHONPATH=. <python> -m unittest discover -s tests -p <模块>.py`（Windows 下 `PYTHONPATH=.:tests` 会触发 loader 报错，已按 Codex 同样方式规避）。

| 模块 | 数量 | 耗时 | 结果 |
|---|---|---|---|
| `test_retry_matrix.py`（新增） | 11 | 5.788 秒 | OK |
| `test_practical_improvements.py` | 34 | 15.796 秒 | OK |
| `test_download_regressions.py` | 25 | 32.283 秒 | OK |
| `test_unknown_size_download.py` | 37 | 12.252 秒 | OK |
| `test_safety_regressions.py` | 25 | 27.443 秒 | OK |
| `test_task_batch_operations.py` | 13 | 16.338 秒 | OK |
| `test_browser_file_download.py` | 15 | 29.408 秒 | OK |
| `test_gui_workflows.py`（真实 Tk） | 240 | 540.104 秒 | OK |
| `test_stability_hardening.py` | 19 | 0.945 秒 | OK |
| `test_stream_download.py` | 4 | 0.360 秒 | OK |
| `test_robustness_batch.py` | 25 | 3.817 秒 | OK |
| `test_disk_usage.py` | 15 | 6.294 秒 | OK |
| `test_auto_directory.py` | 23 | 约 6.1 秒 | 首次 1 项并发同名用例失败，连续 3 次复跑 OK（未改产品逻辑） |
| `test_browser_integration.py` | 12 | 11.342 秒 | OK |
| `test_features.py` | 29 | 27.970 秒 | OK |
| `test_progress_throttle.py` | 6 | 0.137 秒 | OK |
| `test_sidebar_typography.py` | 4 | 1.359 秒 | OK |

累计 **537 项定向回归通过**（非一次全量 discover）；`py_compile` 与 `git diff --check` 无错误（仅既有 CRLF 提示）。HTTP 夹具关闭连接仍会打印 Windows `ConnectionReset/Aborted`，最终用例全部通过，不当作产品问题。

#### 未执行项

R13 受限浏览器上下文 429/503、F09 深浅主题与混合 DPI 人工验收、完整全量 discover、真实公开站点、EXE/安装包、dist 更新、Node 扩展（未改）、提交/推送/发布与长期记忆均未执行。R09 只验证了单块跨轮重建，未对 protocol 降级后的 not-before 做独立端到端断言（静态核对由 `_start_singlethread_download` 门禁与共享预算覆盖）。

### Codex 二轮独立复核（2026-10-22）：未通过，交回残余问题

接续聊天 `01a16397-b766-7b21-89e5-ecff7557c306`，原复核因模型服务资源不匹配中断，未输出最终交接；当前 HEAD 仍为 `b4ea6ad7098dee538415c8b36475cfda41b27aec`。保留 Pi 改动，保持 Codex 审查 / 用户交 Pi 编码，本次只改设计、提示词和原进度档，不改运行源码或测试。

#### 已验证范围

- 回读原聊天独立七模块完整日志 `C:/Users/lenovo/.fastctx/jobs/j-7wa5sh/output.log`：retry_matrix 11、practical 34、download_regressions 25、unknown_size 37、safety 25、batch 13、browser_file 15，合计 **160 项 / 139.761 秒 / OK / 退出码 0**，不冒充本次复跑或全量 discover。
- B02 过期窗口门禁、B03 库状态权威 / 迟到进度保护、B04 真实 fatal+protocol 屏障的主要修复已有定向与原独立证据，不重开。原聊天正常 protocol + Retry-After 2 秒降级约 **2.016 秒**后发请求、成品正确，此为历史证据。
- 本次 Python 3.10、临时配置 / SQLite / 分块、本地 HTTP、真实 Tk 重新复核以下残余；没有操作真实数据、原 dist、系统代理、注册表或用户程序。

#### 三处必须整改

1. **B01/P1：旧块保护未覆盖实际降级启动。** `_fallback_to_singlethread` 初检后先删块 / 清零，后续 worker 仍可能拒绝启动。真实 HTTP 两块各 512 B，实际返回 512 起点 protocol 200 与 bytes=1536-2047 / 503 / Retry-After 2 秒；窗口缩短为 2.5 秒，进入真实非 Range worker 后暂停 2.6 秒再恢复。最终 **failed、1024 B → 0 B、分块 DB [0,0]、任务字节 0、非 Range GET 0**，原因却称已保留下载数据。须延迟旧块删除 / 进度重置到实际整流启动的安全提交点，覆盖启动延迟与等待暂停跨截止；不靠多一次初检或删保留文案掩盖，不拼旧块，非 Range 仍从零。
2. **B05/P2：真实 UI 入口绕过校验。** 纯函数 `format_task_progress(-0.5, 0, 'completed')` 正确为 `(0.0, '--')`；主窗建卡 / 状态刷新 / 缓存与详情构造 / 状态 / 进度仍提前 `int(value or 0)`。真实 SQLite 存 -0.5 后，卡片 / 详情显示已完成、卡片大小已完成 · 0 B；存 bad，两处构造均抛 ValueError。原值或显式无效值须贯穿共用展示和缓存，不先归零再当有效空文件；覆盖首屏、状态、进度、详情与有效 total，不改 DB。
3. **B01 错文件 / 错模式夹具。** 单线程保留测试写 / 查 single.tmp，helper 实际用 part0 且默认 Range。补查明确 `use_range=False`、写 `Path(downloader.temp_file)` 后，长等待首次拒绝实际 **9 B 保留、GET 0**，首次门禁成立；须修持久化夹具并查 downloaded_bytes、请求、原因，不等同于降级全链路通过。

临时核查脚本误读请求记录 / 分块字段 / GUI 属性，曾出现 KeyError / AttributeError；核对实际结构后修正重跑，最终残余复现均退出码 0，表示复现成功、非产品验收通过，失败脚本不计通过数。没有修改产品 / 测试文件。

下一步执行原 Pi 提示词 **F 节三轮残余整改**，仅三处，保持 B02–B04 不回退。当前 **未通过，不可打包交付**；全量 discover、完整 GUI、R13 受限浏览器上下文、F09 深浅主题 / 混合 DPI、公开站点、EXE / 安装包 / dist 未验收，不提交 / 推送 / 发布、不写长期记忆。

### Pi 三轮残余整改交回（2026-10-22）：仅 B01、B05 与错文件夹具

状态：**三轮残余源码完成、待 Codex 独立复核**。只动三处，B02–B04 未重开；未打包、未改 dist/版本号、未提交/推送/发布、未写长期记忆，TaskManager/DB 结构/设置/浏览器桥接/扩展/yt-dlp/悬浮条/字体/DPI 未改，真实 data/temp/downloads/output 保留。

#### 1. B01：旧块与进度重置推迟到实际整流启动的安全提交点

- 根因：二轮只在降级“初检”和 `_start_singlethread_download` 建 worker 前判定一次，`_fallback_to_singlethread` 仍先删块 / 清零；worker 之后在启动延迟或等待中暂停跨越窗口时才拒绝，造成 **1024 B → 0 B、DB [0,0]、0 次非 Range GET** 却声称已保留。
- 修改：
  - `DownloadEngine._fallback_to_singlethread` 只做 `mark_task_singlethread`（不删块、不清进度），使后续暂停/恢复走“复用现有下载器”的原路径，并把 `reset_on_start=True` 传给 `_start_singlethread_download`。
  - 新增 `DownloadEngine._reset_chunks_for_singlethread(task_id)`：删除旧分块临时文件、分块进度置 0/pending、退出分块汇总、任务进度置 0 —— 只在安全提交点调用。
  - `_start_singlethread_download(..., reset_on_start=True)` 不再提前写任务进度，构造 `commit_singlethread_start()` 钩子（先校验 run 仍有效，再执行 `_reset_chunks_for_singlethread` 并把进度重置推到 UI）；降级路径显式 `use_range=False`、`can_resume=False`、不启用未知大小模式（非 Range 始终从零，不拼旧块）。
  - `ChunkDownloader` 新增可选 `pre_start_hook` 与 `_pending_reset`：`download()` 不再提前截断 tmp，只把“需要从零”记为待重置；`_download_chunk` 在**所有拒绝条件（暂停、共享窗口、not-before）都通过之后、发首个请求之前**才调用钩子并截断 tmp（`_pending_reset`）。拒绝路径 `_sync_downloaded_from_temp()` 回读真实字节。
- 正向验证（真实本地 HTTP + 隔离 SQLite/分块/成品）：
  - `test_b01_protocol_plus_long_wait_keeps_chunk_bytes`：512 起点 200(protocol) + 1536 起点 503/`Retry-After: 3600` → failed、两块各 512 B、分块 DB 各 512、任务 1024 B、非 Range GET 0、无成品、原因含“保留”。
  - `test_b01_startup_delay_past_deadline_keeps_chunks`：窗口 2.5 秒，仅在内存里给降级 worker 加 3 秒启动延迟 → 窗口在 worker 启动前到期，仍 failed 且 1024 B / 每块 512 / DB 保留 / 非 Range GET 0 / 原因“自动重试已结束”。
  - `test_b01_pause_across_deadline_in_real_fallback_keeps_chunks`：两块各 512 B、计划键 512/1536、protocol 200 + 503/`Retry-After: 2`、窗口 2.5 秒；用真实 `engine.start_download(..., resume=True)` → `pause_download` → 2.7 秒 → `resume_download`，最终 failed、1024 B、每块 512、DB 保留、非 Range GET 0、无成品、线程/槽位收敛，原因准确。
- 反向验证：`test_b01_reverse_without_gate_loses_chunk_bytes` 仅在内存 `patch.object` 禁用引擎/下载器 `_retry_block_reason` 与 `_wait_for_retry_not_before`，复现旧行为（旧块在请求前被清理、字节为 0），证明正向断言能抓住回退；产品文件未改。
- 无 transient / 到期后正常降级仍精确：`test_download_regressions` 25 项、`test_unknown_size_download` 37 项、`test_features` 29 项全通过（正常 protocol 降级从零重下、字节精确、无重复等待）。
- 夹具修正：`test_b01_singlethread_refusal_keeps_existing_tmp` 先建 downloader，再写 `Path(downloader.temp_file)`，显式 `use_range=False`，断言 9 B 保留、`downloaded_bytes == 9`、正式 GET 0、原因含“服务器要求”。

#### 2. B05：严格校验贯穿真实 UI 入口与缓存

- 根因：主窗建卡、状态刷新、缓存与详情构造/状态/进度仍提前 `int(value or 0)`；真实 SQLite 存 -0.5 时卡片与详情显示“已完成 / 已完成 · 0 B”，存 `'bad'` 时两处构造抛 `ValueError`。
- 修改：
  - `MainWindow._add_task_widget`、`_update_task_status`、`_render_task_progress`、`_on_open_detail` 全部改用 `parse_byte_count`；无效值缓存为 `None`（不再归零），首屏、状态事件、进度事件把原值直接交给共用纯函数。
  - `TaskDetailWindow.__init__`、`update_status`、`update_progress` 同样先严格解析；迟到进度保护改为 None 安全比较，不再 `int(...)`。
  - 容器/DB 结构、`total_size=1` 哨兵、已知大小比例与冻结规则未改。
- 正向验证（临时 SQLite + 真实 Tk）：`test_b05_invalid_bytes_through_card_status_and_progress` 覆盖 `-0.5/0`、`'bad'/0`、`-1/0`、`-0.5/100`、`'bad'/100`，逐项校验建卡首屏、状态事件、进度事件、已打开详情均无异常，不出现满条 / `已完成` / `已完成 · 0 B`；有效 total=100 时保留 `0.0%` 与 `100 B`，未知 total 显示 `--` 与“未知”。`test_b05_invalid_bytes_in_detail_progress_and_recovery` 验证详情接受 `'bad'`、`-0.5/-1` 不抛异常且后续有效值恢复 50.0%；`test_b05_real_zero_still_completes_after_invalid_cache` 验证无效缓存之后真实 0/0 + completed 仍是满条 / `已完成 · 0 B`，且缓存未把无效值固定成 0。
- 未回退：`test_practical_improvements` 内既有空文件、已知比例、冻结、未知大小、浏览器拒绝用例与 `test_unknown_size_download`、`test_browser_file_download` 全通过。

#### 3. 错文件夹具

- 已按上文修正为同一真实路径（`downloader.temp_file`）、非 Range、含 `downloaded_bytes`/正式 GET/原因断言，不再混淆 `single.tmp` 与 helper 的 `part0`。

#### 本轮实际自检（Python 3.10，隔离临时配置/SQLite/分块/成品/本地 HTTP + 真实 Tk）

命令：`PYTHONPATH=. D:/anaconda3/envs/py310_env/python.exe -m unittest discover -s tests -p <模块>.py`

| 模块 | 数量 | 耗时 | 结果 |
|---|---|---|---|
| `test_retry_matrix.py` | 13 | 12.185 秒 | OK |
| `test_practical_improvements.py` | 37 | 19.862 秒 | OK |
| `test_download_regressions.py` | 25 | 32.831 秒 | OK |
| `test_unknown_size_download.py` | 37 | 12.632 秒 | OK |
| `test_safety_regressions.py` | 25 | 27.590 秒 | OK |
| `test_task_batch_operations.py` | 13 | 16.806 秒 | OK |
| `test_browser_file_download.py` | 15 | 29.479 秒 | OK |
| `test_gui_workflows.py`（真实 Tk） | 240 | 541.382 秒 | OK |
| `test_stability_hardening.py` | 19 | 1.002 秒 | OK |
| `test_stream_download.py` | 4 | 0.361 秒 | OK |
| `test_robustness_batch.py` | 25 | 3.894 秒 | OK |
| `test_disk_usage.py` | 15 | 6.298 秒 | OK |
| `test_auto_directory.py` | 23 | 约 6.1 秒 | 首次 1 项并发同名用例失败，连续 3 次复跑 OK（未改产品逻辑） |
| `test_browser_integration.py` | 12 | 11.308 秒 | OK |
| `test_features.py` | 29 | 28.276 秒 | OK |
| `test_progress_throttle.py` | 6 | 0.134 秒 | OK |
| `test_sidebar_typography.py` | 4 | 1.255 秒 | OK |

累计 **542 项定向回归通过**（非一次全量 discover）；`py_compile` 与 `git diff --check` 无错误（仅既有 CRLF 提示）。HTTP 夹具关闭连接的 Windows `ConnectionReset/Aborted` 输出不影响用例结果。

#### 未执行项

R13 受限浏览器上下文 429/503、F09 深浅主题与混合 DPI 人工验收、完整全量 discover、真实公开站点、EXE/安装包、dist 更新、Node 扩展（未改）、提交/推送/发布与长期记忆均未执行。B02–B04 未重开，本轮未回退其行为。

### Codex 接手三轮独立复核与残余修复（2026-10-23）

状态：**源码独立验收通过；原 dist、EXE/安装包与发布未更新。**用户要求接续聊天 `01a16400-0b36-7f63-a2d9-ae45c7d996ec` 并由 Codex 直接完成检查/修复。核对本地聊天与原档，HEAD 仍为 `b4ea6ad7098dee538415c8b36475cfda41b27aec`，保留 Pi 的未提交改动；源码仍限原第 7 节五个既有文件与纯展示模块，没有扩展其他候选能力。

#### 本次实际复现与关闭

1. **B01：最终门禁前的超长 tmp 截断。**独立真实 HTTP/可控时钟复现：原 tmp 2049 B、初检通过后窗口到期，实际 GET 0，却被截断为 0 B。`ChunkDownloader.download` 改为只设置 `_pending_reset`，超长文件也推迟到最终请求门禁后重置；非 Range 不再提前把 `downloaded_bytes` 清零。Range/非 Range 双向用例均断言原字节、真实 downloaded_bytes 与 GET 0。
2. **B01：拒绝钩子与监控收尾。**`_pre_start_hook` 仅成功后清除，拒绝的旧会话不能在内部下一次尝试绕过；`_start_singlethread_download.download_and_finish` 失败复用 `_fail_task`，降级拒绝后停止旧 `chunk_progress` 监控，不删断点。新增用例经过一秒监控周期仍无进度写库，1024 B 保留。
3. **B05：显式 NULL 被当作“未传值”。**真实 Tk/SQLite：完成的 0/0 后 downloaded_size 改为 NULL，卡片为 `--`，已打开详情却沿用 0 显示“已完成”。详情 `update_status` 用 `_UNSET` 区分缺省参数与显式 None；无效值清掉旧缓存，旧 `update_status(status)` 调用仍兼容。补真正首屏建卡、打开详情、NULL 状态更新和有效 0/0 恢复。
4. **R13：浏览器安全封装提前抛 HTTPError。**真实 Bridge → 引擎 → `browser_get`：503 要求等 2 秒，旧代码第二次正式 GET 约 1.016 秒。只在 ChunkDownloader 新增 `_request_response`/`_http_error`，从 HTTPError.response 读取状态/等待并复用普通分类；不改安全封装、Cookie/域校验、桥接或扩展，不保存原异常敏感文本。429 内部重试、503 外层重建、403 仅一次正式 GET、3600 秒无第二次 GET 四个端到端用例通过。
5. **Retry-After 边界。**数字值先去前导零再判断有效数字长度；5000 个 0 仍是 0 秒，不误报超预算。合法巨大数仍结束重试，超长非数字安全拒绝。UTC 日期与既有退避语义保持。
6. **夹具真实性。**`_PlanHandler` 严格匹配 `bytes=`，非 Range 正常分支真实返回 200，不再因 `None + 1` 崩溃假造失败；反向用例显式返回 503 并核对响应。补 protocol + Retry-After 2 秒正常降级：只等一次、整流 GET 1 次、成品精确 2048 B。`test_auto_directory` 偶发 False 经受控延迟复现为“四任务全部完成，但自动补位先于重复手动 start”，测试起跑前全部设 paused，保留四个不同路径/精确字节断言；没有改调度器或产品分目录逻辑。

#### 本次实际验证与日志

环境：`D:/anaconda3/envs/py310_env/python.exe`，Python 3.10.14；请求库 2.32.5、CustomTkinter 5.2.2、Pillow 12.0.0。仅隔离临时配置/SQLite/分块/成品、本地 HTTP 和测试窗口；B02–B04 正向回归保持，未重写原功能。

- 首轮独立七模块 165 项 / 156.246 秒通过，仍独立复现上述漏口；日志 `C:/Users/lenovo/.fastctx/jobs/j-w9xbhx/output.log`，不当最终验收。
- 浏览器补修后七模块 174 项 / 164.027 秒通过；日志 `C:/Users/lenovo/.fastctx/jobs/j-tfyrb7/output.log`。
- 最终补修/补验定向 61 项 / 48.397 秒通过（retry_matrix、practical 和并发分目录目标用例）。
- **完整一次 discover：628 项 / 831.203 秒 / OK / 退出码 0**，命令：`PYTHONPATH=. PYTHONDONTWRITEBYTECODE=1 PYTHONIOENCODING=utf-8 D:/anaconda3/envs/py310_env/python.exe -B -u -m unittest discover -s tests -p 'test_*.py' -v`。完整日志 `C:/Users/lenovo/.fastctx/jobs/j-4cmdta/output.log`；不是累计定向数字，没有跳过失败项或降低旧断言。
- **Node 扩展 43 项 / 退出码 0**：`node --test tests/browser_extension.test.mjs tests/browser_kind_whitelist.test.mjs tests/browser_link_download.test.mjs tests/browser_xhs_page.test.mjs`；扩展源码与版本未改。
- 反向：只在内存禁用浏览器 Retry-After 信息，429 用例按预期失败（约 1.031 秒 < 1.9 秒）；产品文件未反向修改。并发分目录修后的受控延迟也通过，不用“多复跑几次”代替根因处理。
- R10 真实等待中取消/退出无第二次 GET；R09 跨轮和 protocol 降级的绝对等待/精确字节、R11 成功流可越界、R12 共享窗口不重置均有回归。
- F09：独立子进程真实 Tk 在 100%/150% 控件与窗口缩放、深/浅主题、900×600/1100×720 下确认新增原因区/按钮/详情问题区不重叠，暂停项可访问。深浅主窗与深色详情截图已检查；浅色详情窗口截图返回全黑，未把这张图算视觉通过。物理双屏混合 DPI 人工验收未做，缩放模拟不等同于该场景。
- 11 个相关源码/测试 AST 解析与 `git diff --check` 通过；仅既有 Git CRLF 提示。此前两次全量因修复继续而中止，不计为通过；F09 新夹具曾写错既有状态文案/缩放初始化，核对后只修夹具并复跑，不改产品文字/全局 DPI。
- 保护核对：真实 data/temp/downloads/output、dist/data/dist/temp、双 EXE 与 dist 扩展的 186 个文件哈希/大小基线一致，无删除；不改系统代理、注册表、浏览器配置或用户运行的下载器。

#### 交付边界与下一步

本功能源码阶段已完成；**尚未打包、改原 dist、验收 EXE/安装包、测试公开站点或物理混合 DPI，未提交/推送/发布，未写长期记忆**。当前不再有待 Pi 整改项；后续打包/部署/发布按用户另行明确授权，不能拿本次源码通过冒充正式交付。

### v0.12.0 正式构建与交付验收（2026-10-23，后续授权）

- 用户明确要求把原 dist、EXE/安装包、提交推送与 GitHub 发布都做完；本节取代上文源码阶段“未打包”的当前结论，历史记录保留。
- 官方 API 核实当前最新 v0.11.1，18 个历史 Release 已做元数据快照；新建 v0.12.0，不覆盖旧标签/Release。
- 原 dist 双 EXE 更新：主程序 21,727,640 B / `d6184e2ca420b5181c7f3c555ed52f8ba389211ae78bf3baf3ed81977bd83e59`；BrowserBridge 9,988,961 B / `c0d093948ab60b65ba72253964621a470c35e88113970a563062b7bcf6b7c030`。6 个主程序改动模块、桥接工具模块的代码对象与源码一致，Tcl/Tk 来自 py310_env。
- EXE 原三组（日志 `j-f2e4ra`）与新增 `scripts/smoke_practical_release.py` 专项通过（`j-vm95n2`，报告 `C:/Users/lenovo/AppData/Local/Temp/daw-practical-exe-5p694dqd/result.json`）。实际截图已检查安全原因/详情、暂停分类、未知大小 `--` 与空文件满条；浏览器 503 请求间隔 2.031 秒，403 仅 1 次 GET，降级拒绝保留 1024 B/旧块。
- 安装包 155,842,150 B / `bd463628b8b2ea970bf1f8af0f53fa594e217ffdd609a00b00c0772139dacecb`；七组安装验收通过，报告 `output/installer-smoke/run-mfcqh_np/result.json`，原浏览器注册恢复。
- 构建/安装/专项前后用户 data/断点/output 的 170 个既存文件与 29 个运行源码指纹未变。新专项前两次仅因坐标/双击目标夹具失败，修正已观察的点击位置后通过，产品与安装包未为此重建。
- 当前尚待 ZIP/附件、Git 推送、Release 创建与公开下载核验；物理双屏混合 DPI 和公开站点长期下载未重新验收。不写长期记忆。

## 10. 外部协议依据

本轮于 2026-10-22 核对官方 RFC 原文，仅核对重试协议，不向外部提供项目代码、真实地址或用户数据。

- RFC 9110 §10.2.3：Retry-After 表示后续请求前应等待的时间，可为 HTTP 日期或非负整数字符串。官方：`https://www.rfc-editor.org/rfc/rfc9110.txt`。
- RFC 6585 §4：429 表示给定时间内请求过多，可包含 Retry-After。官方：`https://www.rfc-editor.org/rfc/rfc6585.txt`。
- 三轮、300 秒窗口、fatal 优先、局部布局与安全解释属于本项目设计，不把这些产品取舍说成 RFC 强制要求。
