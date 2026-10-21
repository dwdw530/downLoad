# 下载器健壮性与限速优化：设计与验收约定

- 日期：2026-10-21（Asia/Shanghai）
- 项目：`D:/vscode_workspace/downLoad_project`
- 状态：**设计待评审，尚未编码**。本文只描述方案、边界与验收口径，不代表任何代码已修改。
- 设计基线：本地 `master` = `13a77b6`（`v0.8.1` 之后的一份进度档提交），工作区干净，仅 `output/` 未跟踪。
- 用户已选定范围：编号 **2、3、4、5、6、7、8**（见下表）。**编号 1（跨任务重复链接检测）本次不做。**
- 估算的代码行数均为按现有结构推断的**相对量级**，不是实测工时。

| 编号 | 项目 | 类型 |
|---|---|---|
| 2 | 单链接后台添加 + 失败原因回传 | 交互 + 可诊断性 |
| 3 | Content-Disposition 文件名识别 | 正确性 |
| 4 | 磁盘空间预检 | 稳定性 |
| 5 | 全局总限速（语义从"每任务"改为"总量"） | 行为变更 |
| 6 | 重试全部失败 | 批量操作 |
| 7 | 状态栏统计改 SQL 聚合 | 性能 |
| 8 | 孤儿临时文件清扫 | 磁盘卫生 |

## 1. 覆盖范围与非目标

**本轮实现**

- 上述 7 项，均在普通文件下载链路内（探测 → 建任务 → 分块 → 合并 → 列表/状态栏）。
- 不改浏览器扩展、不改 Native Messaging 协议、不改视频平台适配。

**明确不做**

- 跨任务重复链接检测（编号 1，用户本轮排除）。
- 未知大小 / 零字节文件下载（中等改动，需单独一批；本轮仍维持拒绝并给出具体原因，见 2.4）。
- 动态分段、每任务独立限速、多选批量、定时下载、自动按类型分目录、列表导入导出、失效链接换源。
- 新增第三方依赖。
- 数据库结构迁移（本轮新增能力全部可在现有表与配置上实现；见第 6 节）。

## 2. 公共约束（所有分项共同遵守）

1. **不覆盖已有文件**：仍由 `file_utils.available_download_path` 分配名称（`file_utils.py:169`），任何新文件名来源都必须过这一步。
2. **断点安全**：任何新逻辑都不得使"资源身份未确认的分块"被复用。失败/中止一律保留分块与临时文件（`download_engine.py:709` 现有语义）。
3. **不破坏视频链路**：`youtube_downloader.py`、`downloader/browser/*` 的既有分支不参与本轮改造；全局限速**不覆盖**视频任务（见 5.6）。
4. **用户数据不动**：构建与验收前后 `dist/data`、`dist/temp` 哈希保持一致，`config.json` / `downloads.db` 不得被测试写入。
5. **日志脱敏**：只记数量、状态码、字节数；不记 URL、Cookie、含用户名的绝对路径（沿用现有口径）。
6. **可降级**：任何新增检查（空间、文件名、清理）在"拿不到信息"时必须**放行**，不能让探测或下载因新逻辑而失败。
7. **兼容现有测试**：`check_url_support_range` 的返回签名保持 `(bool, int)`；`TaskManager.get_statistics()` / `add_task()` 的签名与返回值保持不变（`tests/test_download_regressions.py:224`、`tests/test_safety_regressions.py:308` 依赖）。

## 3. 分项设计

### 3.1 编号 2：单链接后台添加 + 失败原因回传

#### 现状与证据

- 单链接仍走同步流程：`main_window.py:473-489` 直接在主线程调 `task_manager.add_task`，而 `add_task` 内部会发起一次完整的 Range 探测（`download_engine.py:175`）。慢站点或大 RTT 时窗口无响应，且没有取消入口。
- 失败原因在 `check_url_support_range` 里被统一吞掉：异常只写日志并返回 `(False, 0)`（`download_engine.py:130-142`）；`create_download_task` 再把它变成 `failed` 回调 `'无法获取文件大小'`（`download_engine.py:177-180`）。任务没有落库，UI 拿不到这条消息。
- 批量路径已有后台线程与汇总框（`main_window.py:507-556`），但 `reason` 只有兜底字符串 `"无法获取文件大小或链接不可用"`（`main_window.py:519`），因为 `add_task` 返回 `None` 时不带原因。

#### 设计

**A. 统一走后台**

- 删除 `_process_add_dialog` 中的单链接同步分支，改为与批量完全相同的 `_add_tasks_in_background(urls, dialog, skipped)`。
- 单链接的差异只体现在**结果展示**上（见 C），不再有第二条执行路径。
- 后台线程生命周期沿用现有写法：`daemon=True`，`self._batch_add_pending` 只做状态栏计数，结果经 `self._ui_events` 回到主线程。

**B. 原因回传（不改函数签名）**

- `check_url_support_range` 在 `metadata` 中写入失败原因，返回签名不变：
  - `metadata['reason']`：枚举字符串，取值 `network`、`timeout`、`dns`、`http_4xx`、`http_5xx`、`html_page`、`no_length`、`encoding_not_supported`、`invalid_url`、`range_protocol`、`unknown`；
  - `metadata['status']`：HTTP 状态码（有则填）。
- 判定顺序：`requests` 异常类型（`Timeout` / `ConnectionError` / 其他）→ `response.status_code` → `Content-Type` 含 `text/html`（说明拿到的是登录页/错误页）→ `Content-Encoding != identity`（现状返回 0，`download_engine.py:129`）→ 无 `Content-Length`（`no_length`）。
- `create_download_task` 在返回 `None` 前，把原因写入**线程本地**槽位：`engine._add_state.local.last_error`（`threading.local`）。这样 UI 后台线程与 BrowserBridge 线程各自读自己的值，不会互相污染，也不需要改 `add_task` 的返回值。
- `TaskManager.add_task` 增加只读属性 `last_add_error`（转发引擎的线程本地值），其余逻辑不动。

**C. 展示**

- 单链接成功：**不再弹"任务添加成功！"**，改为状态栏闪烁提示（复用 `_flash_status`，与 v0.8.1 设置保存口径一致）。
- 单链接失败：弹错误框，正文为 `原因：<具体说明>`，例如：
  - `HTTP 403（可能需要在浏览器中登录后重新发送）`
  - `服务器返回的是网页而不是文件（可能需要登录）`
  - `服务器未提供文件大小（当前版本暂不支持未知大小下载）`
  - `连接超时，请检查网络或代理设置`
- 批量汇总框保持现有结构，`failures` 里换成具体原因（`main_window.py:545-556` 的排版不改）。
- 文案只使用固定映射表，不把原始异常字符串拼进界面（避免把 URL 或内部栈信息暴露出去）。

#### 边界与失败处理

- 同一 URL 重复提交仍由 `_dedupe_urls` 处理（同批内）。
- 后台线程内异常：捕获后记为 `unknown`，不能让线程静默死掉导致 `_batch_add_pending` 不归零（现有 `finally` 已保证，保留）。
- 添加过程中关闭窗口：沿用现有 `_shutdown_pending` 与事件队列逻辑，不新增竞态。
- 取消添加入口本轮不做（现有批量也没有），列为后续。

#### 验收

- 单链接添加时，主窗口在探测期间可拖动、可响应按钮（用慢响应夹具模拟 3 秒延迟）。
- 403 / 404 / 返回 HTML 登录页 / 无 Content-Length / 连接超时 五类夹具各返回对应文案，且**不创建任务**。
- 单链接成功不再弹框，状态栏提示 3 秒后恢复。
- 批量添加的汇总框行为与现状一致，失败项带具体原因。
- 并发 3 个后台添加（UI + Bridge 模拟）时，`last_add_error` 不串台。

#### 改动文件与量级

| 文件 | 改动 |
|---|---|
| `downloader/ui/main_window.py` | 删同步分支；单链接改后台；失败文案映射；成功改状态栏提示（约 40 行） |
| `downloader/core/download_engine.py` | `check_url_support_range` 填 `metadata['reason']`；线程本地错误槽（约 30 行） |
| `downloader/core/task_manager.py` | `last_add_error` 只读属性（约 5 行） |
| `tests/test_download_regressions.py`、`tests/test_gui_workflows.py` | 新增夹具与用例（约 60 行） |

---

### 3.2 编号 3：Content-Disposition 文件名识别

#### 现状与证据

- 文件名在**探测之前**就由 URL 决定：`download_engine.py:166-173` 先 `get_filename_from_url(url)`，`file_utils.py:144-156` 只取 `urlparse(url).path` 的末段。
- 因此 `https://host/file.php?id=123` 会得到文件名 **`file.php`**，`https://host/download` 会得到 `download`（无扩展名），`https://host/` 会得到 `download`。
- 全局没有任何 `Content-Disposition` 解析（仅测试夹具 `scripts/browser_link_test_fixture.py:149` 会发这个头）。

#### 设计

**A. 优先级（从高到低）**

1. 用户在添加对话框里显式填写的文件名（保持现有优先级，不被服务器覆盖）；
2. `Content-Disposition` 的 `filename*`（RFC 5987，UTF-8）；
3. `Content-Disposition` 的 `filename`；
4. 重定向后最终 URL（`response.url`）的路径末段；
5. 原始 URL 的路径末段（现有行为）；
6. 兜底 `download`。

**B. 实现要点**

- 在 `check_url_support_range` 中，把 `response.headers.get('Content-Disposition')` 与 `response.url` 写入 `metadata`（该参数已存在，用于 etag/last-modified）。
- `create_download_task` 调整为**先探测、后定名**：`metadata = {}` → 探测 → 若调用方未显式给 `filename`，按 A 的优先级从 `metadata` 取 → 统一 `validate_filename`。
- 解析函数放在 `downloader/utils/file_utils.py`：`parse_content_disposition(value) -> Optional[str]`。
  - 支持 `filename*=UTF-8''%E4%B8%AD%E6%96%87.zip`（含 `iso-8859-1'lang'...` 形式）与 `filename="a b.zip"`；
  - 引号内支持转义 `\"`；
  - 两种同时存在时 `filename*` 优先；
  - 解码失败退回 `filename`，都失败返回 `None`；
  - 结果只保留**单个路径段**（`os.path.basename` 之后再过 `validate_filename`），`../`、`C:\`、ADS、设备名一律拒绝并回退到下一优先级。
- 仅在响应不是 `text/html` 时采用服务器文件名（避免把登录页的 `attachment; filename="login.html"` 当真）。
- 长度处理：超过 120 字符时保留扩展名并截断主体；截断后再 `validate_filename`。

#### 边界与失败处理

- 服务器给的名字与现有文件重名：仍由 `available_download_path` 生成 `x (1).zip`，不覆盖。
- 未知大小/被拒绝的链接：不产生文件名，行为不变。
- 扩展名改写风险：不根据 `Content-Type` 强行替换扩展名（例如把 `.zip` 改成 `.bin`），本轮只认服务器/URL 给的名字，避免引入"文件关联错乱"的新问题。
- `metadata['filename']` 只在内存中传递，不落库、不写日志。

#### 验收

| 场景 | 期望 |
|---|---|
| 无 Content-Disposition，URL 以 `report.pdf` 结尾 | `report.pdf`（现状不变） |
| `file.php?id=123` + `filename=report.pdf` | `report.pdf` |
| `filename*=UTF-8''%E4%B8%AD%E6%96%87.zip` | `中文.zip` |
| `filename="../../evil.exe"` | 拒绝，回退到 URL 末段 |
| `filename="CON"` | 拒绝，回退 |
| 响应 `text/html` + `filename="login.html"` | 忽略，回退到 URL 末段 |
| 对话框显式填 `自定义名.zip` | 保持 `自定义名.zip`，不被服务器覆盖 |
| HTTP/HTTPS 重定向到 `/files/real-name.zip` | `real-name.zip` |

#### 改动文件与量级

| 文件 | 改动 |
|---|---|
| `downloader/utils/file_utils.py` | 新增 `parse_content_disposition`，约 45 行 |
| `downloader/core/download_engine.py` | `metadata` 多记两项；文件名来源顺序调整（约 20 行） |
| `tests/test_features.py`（或新增 `tests/test_filename_resolution.py`） | 解析与优先级用例（约 60 行） |

---

### 3.3 编号 4：磁盘空间预检

#### 现状与证据

- 全仓库没有剩余空间查询（`shutil.disk_usage` 零引用）；`tests/test_disk_usage.py` 是"磁盘发布/跨卷提交"测试，与剩余空间无关。
- 写入路径为"分块落 `temp_dir` → 合并为**目标目录中的完整副本** → 校验 → 同卷改名"（`download_engine.py:709-742`）。峰值占用因此是：

| 场景 | 临时盘峰值 | 目标盘峰值 |
|---|---|---|
| `temp_dir` 与目标**同卷** | `total_size`（分块）+ `total_size`（合并副本）= **2×** | 同一卷，合计 2× |
| `temp_dir` 与目标**跨卷** | `total_size` | `total_size`（合并副本） |

#### 设计

**A. 统一入口**

`file_utils.check_free_space(requirements) -> (ok: bool, detail: str | None)`：
- `requirements` 为 `[(目录, 需求字节, 标签)]` 列表；
- 同卷路径先合并需求再检查（用 `os.stat(dir).st_dev` 判定；Windows 下同盘符 `st_dev` 相同，UNC 路径归为独立卷）；
- 余量按 `需求 × 1.05 + min(64 MiB, 需求 × 5%)` 计算：大文件多留 64 MiB，小文件不会被固定 64 MiB 余量无故拦住；
- 任一路径 `statvfs`/`disk_usage` 抛 `OSError`（网络盘、权限、盘符消失）→ 该条记为"未知"，**整体放行**，只写日志；
- 返回 `detail` 形如 `目标盘需要 12.3 GB，剩余 8.1 GB，还差 4.2 GB`。

**B. 创建任务时（`create_download_task`，探测拿到 `total_size` 之后）**

- 同卷：单条需求 `(temp_dir, 2 × total_size)`；
- 跨卷：两条 `(temp_dir, total_size)`、`(目标目录, total_size)`；
- 不满足 → 不建任务，走编号 2 的错误通道，`reason = disk_space`，文案带缺口数字；
- 已知有未完成断点的任务不重复预检（续传不需要新增全量空间）。

**C. 合并前（`_merge_and_finish` 入口）**

- 只需检查目标卷 `total_size`（合并副本是全量写）。
- 不满足 → 任务置为 **`paused`**（不是 `failed`），`error_message = '磁盘空间不足，清理后可继续'`，分块与临时文件全部保留；UI 状态行显示该原因。
- 理由：这里是可恢复状态，用户腾出空间后点"继续"即可；置 `failed` 会让用户误以为任务报废，且与"保留断点"的既有约定不一致。
- 恢复时重新走 C 的检查，仍然不足则再次 `paused`（不消耗重试次数）。

**D. 不做**

- 不做跨任务的"总预留"（避免 A 任务占着额度导致 B 任务加不进来，误报成本高）。
- 不做配额/软限制提醒。
- 不改变 `available_download_path` 与合并原子性。

#### 边界与失败处理

- `total_size` 为 0 或未知：不检查（本轮仍拒绝这类任务）。
- 目标目录在 U 盘/网络盘且读不到容量：放行，不阻断。
- 空间检查与真实写入之间仍有竞态（其他程序占盘）：写入失败路径保持现状，不依赖预检。
- 悬浮条/状态栏不做空间提示（范围外）。

#### 验收

- 剩余空间充足：任务正常创建与完成，行为与现状一致。
- 同卷空间小于 2×：拒绝添加并给出"还差 X"；不产生任务记录、不产生临时文件。
- 跨卷且目标盘不足：拒绝添加，提示指向目标盘。
- 合并前人为把目标盘填满：任务进入 `paused`，分块仍在，腾空后"继续"能完成，文件哈希正确。
- 网络盘 `disk_usage` 抛异常：日志有一条 warn，任务照常创建。
- `dist/temp` 原有文件在测试后哈希不变。

#### 改动文件与量级

| 文件 | 改动 |
|---|---|
| `downloader/utils/file_utils.py` | `check_free_space` + 余量/同卷判定，约 60 行 |
| `downloader/core/download_engine.py` | 创建任务处、`_merge_and_finish` 入口各一处检查（约 35 行） |
| `tests/test_disk_usage.py`（扩展）或新增 `tests/test_free_space.py` | 同卷/跨卷/未知/不足用例（约 70 行） |

---

### 3.4 编号 5：全局总限速（语义务必看清）

#### 现状与证据

- `SpeedLimiter` 是**每分块一个实例**：`chunk_downloader.py:149` 在 `ChunkDownloader.__init__` 里新建。
- 引擎按**任务内分块数**均分配置值：`download_engine.py:443-444`（`config.speed_limit // len(chunk_rows)`）。
- 因此 `speed_limit = 1 MB/s` 的实际含义是**每个任务各 1 MB/s**；并发 5 就是 5 MB/s。
- 运行中改设置时，UI 再按活跃 downloader 重新分摊（`main_window.py:613-618`），语义仍然是"每任务"。

#### 设计（语义改为"所有普通文件任务合计上限"）

**A. 共享限速桶**

- `DownloadEngine` 持有一个实例：`self.speed_limiter = SpeedLimiter(config.speed_limit)`。
- 所有 `ChunkDownloader` 通过新增可选参数 `limiter=` 复用同一实例；`speed_limit=` 参数保留（不传 `limiter` 时行为与现在完全一致），避免破坏独立测试与视频路径。
- 删除 `download_engine.py:443-444` 的按任务均分逻辑（共 2 行 + 一个参数传递）。

**B. 线程安全（必须做，否则共享即出错）**

现有 `SpeedLimiter.acquire` 无锁（`chunk_downloader.py:75-99`），多线程共享会竞争 `_tokens` / `_last_time`。改造为：

1. `threading.Lock` 保护令牌计算与扣减；
2. **sleep 在锁外**执行：锁内只计算"需要等待多久"，释放锁后 sleep，醒后再进入下一轮；
3. 每次 `acquire` 的预扣量上限取 `1/8 秒额度`（`bytes_per_second / 8`），避免一次预扣过久导致其他分块长时间等锁；
4. `set_limit` 加锁；从 0 改为非 0 时重置 `_last_time`，避免用旧时间戳补出一大笔令牌；
5. 保持 `bytes_per_second <= 0` 时零开销直接返回（不取锁）。

**C. 运行时生效**

- 新增 `engine.set_speed_limit(bytes_per_second)`：更新 limiter 与 `config.speed_limit`（配置由调用方 `save()`）。
- `main_window._on_settings_saved` 中，把 `main_window.py:613-618` 的分摊逻辑替换为一次 `engine.set_speed_limit(...)`。
- 已运行的任务无需重启，下一次 `acquire` 即用新值。

**D. 设置页文案**

- `settings_dialog.py:146` 的标签由「速度限制(KB/s)」改为「**总速度上限(KB/s)**」，提示改为「所有任务合计，0 表示不限速」。
- 不新增配置键：继续用 `speed_limit`，但 README 与设置页明示语义变更，并在 `docs/releases/` 的下一版说明里写清楚"老用户注意：原先是每任务上限"。

**E. 悬浮条与状态栏**

- 总速度显示逻辑不变（仍是各任务 speed 求和）。

#### 边界与失败处理

- 视频任务（`youtube_downloader.py:306-307` 用 `--limit-rate`）**不受**共享桶约束：它与分块下载不共享进程内令牌，本轮不改造。文档与设置页提示需写明"视频任务按单任务限速"。
- 共享桶在低限速 + 高并发时会出现"唤醒放大"（多个线程同时醒来抢令牌）。上述第 3 条（1/8 秒额度预扣）已缓解；不接受更复杂的公平队列方案（收益低）。
- 限速值下限：`< 1024 B/s` 按 1024 处理，避免界面误填 1（KB/s）导致几乎停摆。

#### 验收

- 单任务 8 分块、限速 512 KB/s、下载 20 MB 夹具：总时长 ≈ 40 秒（±15%），且**不是** 8 × 512 KB/s。
- 3 任务并发、总量限速 512 KB/s：三者速度之和 ≤ 512 KB/s × 1.15。
- 运行中从 0 改为 512 KB/s：10 秒内总速度收敛到上限以内。
- 运行中改为 0：立刻恢复到无 sleep（等待中的线程在下一次 `acquire` 直接返回）。
- 限速 0 时 CPU 无额外开销（与现状对比不明显劣化）。
- 暂停一个任务后，其余任务总速度仍不超过上限（共享桶天然满足）。
- `tests/test_features.py:256` 的既有 `SpeedLimiter(1024)` 行为用例保持通过（独立实例语义不变）。

#### 改动文件与量级

| 文件 | 改动 |
|---|---|
| `downloader/core/chunk_downloader.py` | `SpeedLimiter` 加锁与锁外 sleep；`ChunkDownloader` 增 `limiter=` 参数，约 45 行 |
| `downloader/core/download_engine.py` | 引擎级 limiter、去掉按任务均分、`set_speed_limit`，约 25 行 |
| `downloader/ui/main_window.py` | 替换分摊逻辑（约 15 行） |
| `downloader/ui/settings_dialog.py` | 文案（约 5 行） |
| `tests/test_features.py` | 共享桶与并发限速用例（约 60 行） |

---

### 3.5 编号 6：重试全部失败

#### 现状与证据

- `resume_all()` 只捞 `paused`（`task_manager.py:383`），失败任务只能逐个点卡片上的「▶ 重试」（`main_window.py:931`）。
- `start_task` 已支持 `failed` / `verify_failed` / `cancelled`（`task_manager.py:199-204`），并且对 `verify_failed` 会先清零进度（`task_manager.py:203-209`）——恢复能力是现成的，只缺批量入口。

#### 设计

- `TaskManager.retry_failed() -> (started, skipped)`：
  - 取 `status='failed'` 的任务，按 `list_order` 顺序逐个 `start_task(task_id)`；
  - 受 `max_concurrent` 门禁约束（超出自动转 `pending`，现有 `start_task` 已处理）；
  - 不区分致命/临时错误（不做 `error_message` 文本匹配，避免脆弱的字符串协议）。原因：可控风险是"快速再失败一次"，而不是错误地放过临时错误。
  - `verify_failed` 本轮不纳入（哈希不匹配通常是源文件变了，重下意义有限），列在后续。
- UI：工具栏在「继续全部」右侧新增「↻ 重试失败」按钮（`main_window.py:231-244`，宽度与现有按钮一致 100）。
  - 布局风险：左侧现有 3 个 100 宽按钮 + 4 个按钮、右侧 2 个；主窗口最小宽度需实测，若 1024 宽下换行，则把左侧 4 个按钮收进一个 `CTkOptionMenu`「批量操作 ▾」，默认选项为「暂停全部」。**优先方案是直接加按钮**，只在实测溢出时才改下拉。
- 二次确认：失败任务数 ≥ 10 时弹 `askyesno`「将重试 N 个失败任务，继续？」；< 10 直接执行。
- 结果提示：状态栏闪「已重试 N 个任务」（与 v0.8.1 口径一致），不弹框；`skipped`（启动失败/被门禁拒绝）计入文案。

#### 边界与失败处理

- 没有失败任务：状态栏闪「没有失败的任务」，不弹框。
- 正在下载的任务不受影响；`_running_tasks` 状态由 `start_task` 自行维护。
- 用户连点两次：第二次时第一批已被转为 `downloading`/`pending`，不会重复启动（`start_task` 对 `downloading` 返回 False）。
- 重试失败不影响"全部完成后关机"的倒计时判定（现有 `_schedule_shutdown_if_idle` 依赖活动计数，`start_task` 已自增 `_activity_generation`）。

#### 验收

- 5 个失败任务 → 点一次按钮，1 个进入下载、4 个转 `pending`（不超过并发上限），状态栏提示。
- 10 个及以上 → 出现确认框；取消则无任何变化。
- 0 个失败任务 → 状态栏提示，不弹框。
- 失败任务重试成功后进度从断点继续（不是从 0），临时文件与分块复用。
- 工具栏在 1024 / 1280 宽度下不换行、按钮文字不截断。

#### 改动文件与量级

| 文件 | 改动 |
|---|---|
| `downloader/core/task_manager.py` | `retry_failed()`，约 25 行 |
| `downloader/ui/main_window.py` | 按钮、确认、提示，约 30 行 |
| `tests/test_gui_workflows.py`、`tests/test_features.py` | 用例约 35 行 |

---

### 3.6 编号 7：状态栏统计改 SQL 聚合

#### 现状与证据

- `update_status_bar` 每秒执行两次全量查询：`get_statistics()`（`task_manager.py:428-457` → `get_all_tasks()` → `SELECT *`，`db_manager.py:288`）与 `get_downloading_tasks()`（`task_manager.py:309-311`，又一次 `SELECT *`）。见 `main_window.py:1330-1331`。
- `download_tasks` 已有 `idx_task_status`（`db_manager.py:111`）与 `speed` 字段（`db_manager.py:72`）。

#### 设计

- `db_manager.py` 新增两个只读方法：
  - `get_status_counts() -> dict`：`SELECT status, COUNT(*) FROM download_tasks GROUP BY status`；
  - `get_total_speed() -> float`：`SELECT COALESCE(SUM(speed), 0) FROM download_tasks WHERE status = 'downloading'`（走 `idx_task_status`）。
- `TaskManager.get_statistics()` 改为基于 `get_status_counts()` 组装，**返回结构完全不变**：`total`、`downloading`、`pending`、`paused`、`completed`、`failed`、`cancelled`，缺失状态补 0。
- `main_window.update_status_bar` 改为：
  - `stats = task_manager.get_statistics()`；
  - `total_speed = task_manager.get_total_speed()`（新增转发）；
  - 不再调用 `get_downloading_tasks()`。
- 保留 `get_downloading_tasks()` 供其他调用点（不删除，避免影响未知依赖）。

#### 边界与失败处理

- 空库返回全 0，状态栏显示「总速度: 0 B/s | 下载中: 0 | 等待: 0」，与现状一致。
- 数据库 `OperationalError`：捕获并沿用上一次统计值，状态栏不闪断（现有代码没有这层保护，顺手加，避免磁盘忙时每秒报错）。
- 不做缓存层、不改刷新频率（仍是 1 秒）。

#### 验收

- 功能等价：插入 N 条各状态任务，新 `get_statistics()` 与旧实现结果逐字段一致（用旧逻辑复制一份做对照断言）。
- 性能记录：5000 条任务下测单次耗时，新旧对比写入验收报告（不设硬性阈值，只记录）。
- GUI 状态栏在下载/暂停/完成后 1 秒内更新正确。

#### 改动文件与量级

| 文件 | 改动 |
|---|---|
| `downloader/database/db_manager.py` | 两个聚合方法，约 25 行 |
| `downloader/core/task_manager.py` | `get_statistics` 重写 + `get_total_speed` 转发，约 20 行 |
| `downloader/ui/main_window.py` | 状态栏两行（约 5 行） |
| `tests/test_features.py` | 等价性与空库用例（约 40 行） |

---

### 3.7 编号 8：孤儿临时文件清扫

#### 现状与证据

- 删除任务时会删分块文件（`download_engine.py:573-581`）；合并副本在 `finally` 中删除（`download_engine.py:735-741`）。
- 但崩溃、强杀、断电后，`temp_dir` 顶层的 `<task_id>.partN` / `<task_id>.tmp` 与目标目录的 `.daw-merge-*.tmp` 会永久残留，没有任何启动清扫。
- 视频任务使用 `temp_dir/<kind>/<task_id>/`（`youtube_downloader.py:286`），结构不同。

#### 设计

- 新增 `DownloadEngine.sweep_orphan_temp() -> (count, bytes)`，在 `MainWindow` 初始化完成、数据库就绪后调用一次（放在 `_load_existing_tasks()` 之后，`after(0)` 调度，不阻塞启动）。
- 清理规则（**必须同时满足**才删除）：
  1. 仅扫描 `temp_dir` **顶层**，文件名严格匹配 `^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\.(part\d+|tmp)$`；
  2. 该 UUID 不在 `download_tasks` 中（既不作为 `task_id`，也不被任何 `download_chunks.temp_file` 引用）；
  3. `mtime` 早于 **7 天**（与"正在创建任务"的竞态隔离）。
- 不递归、不动 `temp_dir/<kind>/` 子目录（视频中间文件容易被误伤，本轮不处理，列为后续）。
- 不清理目标下载目录里的 `.daw-merge-*.tmp`（那是 `_merge_and_finish` 的职责；强杀残留留待后续单独设计）。
- 结果只写一行日志：`临时文件清扫：删除 N 个，回收 X MB`；不记路径。删除失败逐个捕获并计数。
- 不新增配置开关（默认执行，风险已由三条规则约束）。

#### 边界与失败处理

- `temp_dir` 不存在 / 不可读 / 是网络盘：直接返回 `(0, 0)`，只记 debug。
- 断点任务：其分块路径被 `download_chunks.temp_file` 引用，规则 2 保证不删。
- 用户手工放入 `temp_dir` 的其它文件：名字不匹配 UUID 规则，不动。
- 时间判定用 `mtime`（不用 `ctime`），避免复制/移动后误判。

#### 验收

- 构造 5 类文件：无主 UUID `.part0`（8 天前）、无主 UUID `.tmp`（1 天前）、有主任务的分块（8 天前）、非 UUID 名（8 天前）、UUID 形式的目录（8 天前）→ 只有第一条被删。
- 真实场景：下载中强杀进程 → 重新启动 → 断点任务的分块**全部保留**，任务可继续完成且哈希正确。
- 清理耗时在 1 万个文件的 temp 目录下 < 1 秒（只做 `scandir` + `getmtime`，不读文件内容）。
- `dist/temp` 在验收后无新增/删除（用户实际目录不动）。

#### 改动文件与量级

| 文件 | 改动 |
|---|---|
| `downloader/core/download_engine.py` | `sweep_orphan_temp()`，约 55 行 |
| `downloader/ui/main_window.py` | 启动后触发一次，约 5 行 |
| `tests/test_safety_regressions.py` 或新增 `tests/test_temp_sweep.py` | 用例约 50 行 |

---

## 4. 界面与配置变更汇总

| 位置 | 变更 | 分项 |
|---|---|---|
| 工具栏（左侧） | 新增「↻ 重试失败」按钮；实测溢出则改为「批量操作 ▾」下拉 | 6 |
| 添加任务结果 | 单链接成功改状态栏闪烁提示；失败弹框带具体原因 | 2 |
| 设置页「下载」标签 | 「速度限制(KB/s)」→「总速度上限(KB/s)」+ 提示「所有任务合计」 | 5 |
| 状态栏 | 数据来源改聚合查询；新增一次性提示「已重试 N 个任务」「临时文件清扫…」 | 6、7、8 |
| 任务卡片状态行 | 合并前空间不足时显示「磁盘空间不足，清理后可继续」 | 4 |
| 添加对话框 | 无变更（显式文件名仍最优先） | 3 |
| 配置键 | **不新增**；`speed_limit` 语义变更 | 5 |
| 数据库 | **不新增表/列** | 全部 |

## 5. 接口与内部契约变更

| 接口 | 变更 | 兼容策略 |
|---|---|---|
| `check_url_support_range` | 仍返回 `(bool, int)`；向 `metadata` 写入 `reason` / `status` / `filename` / `final_url` | 现有测试与调用点不破 |
| `create_download_task` | 内部顺序改为"先探测后定名" | 返回值、异常语义不变 |
| `TaskManager.add_task` | 新增只读 `last_add_error` | 签名不变，Bridge 无需改动 |
| `TaskManager.get_statistics` | 实现改聚合，返回结构不变 | 逐字段等价 |
| `TaskManager` | 新增 `retry_failed()`、`get_total_speed()` | 纯新增 |
| `SpeedLimiter` | 加锁；`ChunkDownloader` 新增可选 `limiter=` | 不传时行为不变 |
| `DownloadEngine` | 新增 `set_speed_limit()`、`sweep_orphan_temp()`、线程本地 `last_add_error` | 纯新增 |

## 6. 实施批次建议

| 批次 | 包含 | 理由 |
|---|---|---|
| A | 2 + 3 | 同在"探测 → 建任务"链路，一次回归即可覆盖；改动集中在两个文件 |
| B | 4 + 6 | 一个是稳定性、一个是批量入口，互不耦合，回归面小 |
| C | 7 + 8 | 都是独立小改，可放在同一轮跑全量回归 |
| D | 5 | 唯一动并发路径的一项，单独一批，专项压测限速准确性后再合并 |

每批完成后都应能独立通过全量回归，避免多项一起改导致定位困难。

## 7. 测试矩阵

所有命令在 `py310_env` 下执行。

| 层级 | 命令 | 覆盖 |
|---|---|---|
| 单元/回归 | `python -B -m unittest discover -s tests` | 现有 388 项 + 本轮新增 |
| 专项 | `python -B -m unittest tests.test_features tests.test_download_regressions tests.test_gui_workflows -v` | 改动集中区 |
| 限速压测 | 新增脚本或用例：本地 HTTP 夹具 + 1/3 任务并发 | 总量不超限、动态生效 |
| EXE 冒烟 | `python scripts/smoke_exe.py` | 打包后启动、添加、下载、完成 |
| 安装包冒烟 | `python scripts/smoke_installer.py --installer dist/daw-downloader-<ver>-windows-x64-setup.exe` | 安装/升级/卸载保留数据 |
| 数据保护 | 构建与验收前后对 `dist/data`、`dist/temp` 取哈希比对 | 用户配置、数据库、断点不变 |

验收记录要求：每项给出"命令 + 真实输出摘要"，未跑的项明确写"未运行"，不得用设计推演代替测试结果。

## 8. 风险与回滚

| 风险 | 影响 | 缓解 / 回滚 |
|---|---|---|
| 全局限速语义变更让老用户觉得"变慢了" | 用户感知 | 设置页文案 + 发布说明明示；`speed_limit=0` 时完全无影响 |
| 共享限速器死锁或饥饿 | 下载停摆 | sleep 在锁外、预扣上限 1/8 秒、单实例无嵌套锁；专项压测 |
| 磁盘预检误报（网络盘容量不准） | 阻止正常下载 | 拿不到容量一律放行；仅在同一卷可读时判定 |
| Content-Disposition 带来异常文件名 | 文件无法打开/安全隐患 | 只取单路径段 + `validate_filename`；`text/html` 忽略；失败回退 |
| 孤儿清扫误删活断点 | 用户重下 | 三条规则同时满足才删；7 天阈值；只扫顶层 UUID 文件；可用一次真实强杀场景验证 |
| 后台添加改变单链接交互习惯 | 用户反馈 | 成功不弹框改状态栏（与 v0.8.1 口径一致）；若用户不接受，可只对失败改文案 |
| SQL 聚合与旧统计不一致 | 状态栏数字错 | 用旧实现做对照断言，逐字段比较 |

回滚方式：每批独立提交，出问题单独 revert 该批；不引入数据库迁移，因此无需回滚数据。

## 9. 待确认问题（实施前需要用户拍板）

1. **单链接添加成功后不再弹"任务添加成功！"**，改为状态栏闪 3 秒（与 v0.8.1 设置保存一致）——接受吗？
2. **`speed_limit` 语义从"每任务"改为"所有任务合计"**，设置页文案改为「总速度上限」。老用户可能感觉变慢，但这是限速本来的意思。确认改？
3. **合并前空间不足置为 `paused`（可恢复）而不是 `failed`** ——确认？
4. 「重试失败」在失败任务 ≥ 10 个时弹确认框 ——阈值 10 合适吗？
5. 孤儿临时文件清扫的 **7 天**阈值是否合适（更短会更积极回收，但误删风险略升）？

## 10. 交付边界（本轮不做的事）

- 本文件只做设计，**未修改任何源码、测试、配置、`dist/` 或用记忆**。
- 设计通过后再进入编码；编码完成后另行更新进度档与两套本地记忆，并重新核对 `dist/`、双分支与发布状态。
- 发布前须同时更新 `master` 与 `main`（当前 `master` 落后 `origin/master` 一个文档提交 `13a77b6`，届时一并处理）。

## 11. 实施记录（2026-10-21，代码已完成，测试未执行）

按用户要求先实现、后由用户确认再跑测试。本节记录实际落地与设计的差异，**不代表已验证**。

### 已改文件

| 文件 | 实际改动 |
|---|---|
| `downloader/utils/file_utils.py` | 新增 `parse_content_disposition` / `_single_path_segment` / `check_free_space` / `_existing_ancestor`；新增余量常量 |
| `downloader/core/chunk_downloader.py` | `SpeedLimiter` 改为锁内结算 + 锁外 sleep + 1/8 秒切片；新增 `limiter=` 共享参数；保留 `speed_limit=` 兼容 |
| `downloader/core/download_engine.py` | 全局限速器与 `set_speed_limit`；探测原因分类与 `metadata` 扩展；文件名优先级；创建/合并前空间检查；`sweep_orphan_temp`；线程本地 `last_add_error/detail` |
| `downloader/core/task_manager.py` | `get_statistics` 改聚合、`get_total_speed`、`retry_failed`、`last_add_error/detail` 转发 |
| `downloader/database/db_manager.py` | `get_status_counts` / `get_total_speed` / `get_referenced_temp_files` |
| `downloader/ui/main_window.py` | 单链接改后台；失败原因进汇总；单条成功改状态栏提示；「↻ 重试失败」按钮与确认；状态栏改聚合（带失败兜底）；启动后触发孤儿清扫 |
| `downloader/ui/settings_dialog.py` | 文案改「总速度上限(KB/s)」+ 提示「所有任务合计」 |
| `tests/test_gui_workflows.py` | 新增 `wait_tasks()` 辅助，3 个用例改为等待后台添加落库（行为变更是预期的） |
| `tests/test_robustness_batch.py`（新增） | 19 项回归：取名/空间/限速/失败原因/清扫/统计与重试 |
| `scripts/smoke_exe.py` | 工具栏按钮数 5→6，历史按钮索引 3→5（新按钮插在左侧） |
| `README.md` / `PROJECT_SUMMARY.md` | 同步限速语义、批量按钮、新增能力描述 |

### 与设计不同的处理

1. **空间需求不按 `st_dev` 判断同卷**：`check_free_space` 直接按盘符归并需求。同盘时「临时目录 + 下载目录」相加自动等于 2×，跨盘各算 1×，逻辑等价且少了 `stat` 失败分支。
2. **余量改为 `5% + min(64 MiB, 5%)`**：固定 64 MiB 会让「剩余 50 MB 的盘下不了 100 KB 文件」，属误伤。
3. **名字优先级的实现顺序**：先由显式名/URL 算出候选并在发请求前校验（保留「非法名不探测」的既有约束），探测后再用 Content-Disposition / 重定向地址覆盖。
4. **`filename*` 优先于 `filename`**；带路径的服务器名字收敛到最后一段（`../../evil.exe`→`evil.exe`），保留名/ADS 直接放弃。比设计里「一律拒绝并回退」更宽容，但仍在下载目录内、仍过 `validate_filename`。
5. **限速下限 1 KB/s**：`_normalize` 把小而正的值抬到 1024，避免误填 1 时几乎停摆。
6. **`set_speed_limit` 同时写 `config.speed_limit`**，设置页不再自己维护两份语义。
7. **孤儿清扫用两条证据保护活断点**：任务表 `task_id` 集合 + `download_chunks.temp_file` 路径集合，任一中则跳过。
8. **`retry_failed` 不区分致命/临时错误**（如设计所述），`verify_failed` 不纳入重试。
9. **限速器实现改为「虚拟时间轴排队」**：原计划的令牌桶在测试中暴露了等待时间被重复计入（实测速度翻倍），换成每次预扣占一段发送时长、后到的排在前面，多线程天然不叠加。

### 验证结果（2026-10-21，已获用户同意执行测试）

| 项 | 命令 | 结果 |
|---|---|---|
| 全量回归 | `python -B -m unittest discover -s tests` | **407 项通过**（原 388 + 新增 19），耗时 594 s |
| 新增专项 | `-p test_robustness_batch.py` | **19 项通过**，3.7 s |
| GUI 轻量检查（一次性脚本，未入库） | 真 Tk 窗口 + 桩引擎 | 7/7：重试失败改下载中、<10 个不弹确认、状态栏提示、单条成功不弹框、单条失败弹错、批量汇总仍弹框 |

测试中发现并修好的三个缺陷：

1. **限速器等待时间重复计入**：令牌桶版实测速度约为上限 2 倍，`test_multithread_speed_limit_applies_to_whole_download` 0.61 s（期望 ≥0.9 s），改为虚拟时间轴后通过。
2. **404 被误判成 `html_page`**：`send_error(404)` 的响应体也是 `text/html`，原来会提示「可能需要登录」。改为仅 401/403 的网页才算登录墙。
3. **重定向后地址没参与取名**：设计里写了第 4 优先级但初版没实现，`/redir → /files/real.zip` 会存成 `redir`；已补上。

未执行：安装包构建（`dist/` 里的 `daw-downloader-v0.8.1-windows-x64-setup.exe` 仍是旧版，与本轮新 EXE 不配套）、真实站点下载、视频平台链路。`dist/data`、`dist/temp` 未被测试写入；期间 `dist/data/config.json` 的悬浮条坐标变化来自正在运行的桌面程序自身关闭时保存，与测试无关。

### 正式 dist 重建与 EXE 冒烟（2026-10-21 10:33）

| 项 | 结果 |
|---|---|
| `scripts/build_exe.py` | 成功，双 EXE 均更新 |
| `scripts/smoke_exe.py` | **3 组全部 PASS** |

冒烟产物：`C:\Users\lenovo\AppData\Local\Temp\downloader-exe-smoke-ywnhj5sa`（含 `main-window.png`、`paused.png`、`completed.png`、`history-cleared.png`、`result.json`）。任务 `range.bin` 与 `no-range.bin` 都 completed、257,024 B、`hash_verified=1`，历史 2 条；`stderr.log` 为空。

构建前后对比：

| 文件 | 构建前 | 构建后 |
|---|---|---|
| `daw下载器.exe` | 21,692,842 B / `259ac0fa…` | 21,702,007 B / `5730b1ae…` |
| `BrowserBridge.exe` | 9,986,402 B / `908ebe00…` | 9,987,279 B / `cbbbea13…` |

`dist/data/config.json`、`dist/data/downloads.db` 的 SHA256 与构建前完全一致；`dist/temp` 仍是 5 个空子目录；`chrome-extension` 与源码 12 文件逐字节一致。

冒烟过程中修正：`smoke_exe.py` 的历史按钮索引初版写成 5，实际因右侧 pack 顺序（设置先 pack，历史在其左侧）应为 **4**，否则会点到「设置」导致超时；已修正并重跑通过。

### 新增测试文件

`tests/test_robustness_batch.py`（19 项）：Content-Disposition 取名、空间预检（同盘/未知容量/小文件余量）、共享限速（单任务/多线程/不限速）、添加失败原因分类、CD/重定向/显式文件名优先级、非法名不探测、空间不足拒绝、合并前空间不足→paused、孤儿清扫、统计聚合等价性、批量重试。全部用临时目录 + 本地 HTTP 夹具，不联网、不读写真实数据。
