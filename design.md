# daw下载器 · 架构设计

> 本文档由项目早期的需求澄清会话记录重写而来，原文（含当时的对话与技术选型过程）保留在 git 提交历史中。内容以当前代码为准。

## 1. 概述与平台约束

daw下载器是一个 Windows 桌面多线程下载器，核心能力是分块并发下载与断点续传，并额外提供一条浏览器视频下载链路。项目刻意保持结构简单（KISS），不做过度抽象。

**平台约束：仅支持 64 位 Windows。** 以下能力强绑定 Windows，代码中没有跨平台分支：

- 浏览器请求上下文使用 DPAPI（`CryptProtectData` / `CryptUnprotectData`）加密后入库；
- 单实例互斥与进程清理使用 Win32 互斥体与 `taskkill`；
- 浏览器原生消息主机通过注册表 `HKCU\Software\Google\Chrome\NativeMessagingHosts` 注册；
- 子进程以 `CREATE_NO_WINDOW` 启动；
- 安装包基于 NSIS。

## 2. 目录与模块职责

```
downloader/
├── core/
│   ├── download_engine.py     下载引擎总控：URL 探测、任务/分块创建、启动、合并、校验、降级
│   ├── chunk_downloader.py    单个分块下载器；含令牌桶限速器 SpeedLimiter
│   ├── task_manager.py        任务队列与状态机：增删改查、并发门禁、FIFO 调度、退出清理
│   └── youtube_downloader.py  视频页/流媒体封装：URL 规范化、yt-dlp 命令构造、进度解析、结果校验
├── database/
│   └── db_manager.py          SQLite 封装：任务表、分块表、历史表与老库字段迁移
├── ui/
│   ├── main_window.py         主窗口、任务卡片，及添加/删除/退出确认对话框
│   ├── settings_dialog.py     设置对话框（目录、线程、并发、超时、代理、限速、完成后动作）
│   ├── history_dialog.py      下载历史对话框
│   └── tray_manager.py        系统托盘图标与下载完成通知
├── browser/
│   ├── bridge.py              本机回环 HTTP IPC 服务、单实例互斥、消息分发
│   ├── native_host.py         Chrome 原生消息主机：读写长度前缀 JSON，转发到桌面程序
│   ├── registration.py        原生消息主机注册表注册与注销
│   └── security.py            DPAPI 加解密、请求头白名单、跨源跳转与 Content-Type 校验
└── utils/
    ├── app_log.py             日志配置（文件日志、可回退目录、stdout 禁区）
    ├── config.py              配置读写与 get_app_root()
    └── file_utils.py          分块合并、哈希计算、大小/速度格式化
```

入口：`main.py`（桌面程序，初始化配置→数据库→引擎→任务管理器→GUI→桥接服务），`browser_host.py`（桥接 EXE 入口，直接调用 `native_host.main`）。

## 3. 核心下载链路

### 3.1 任务创建与探测

`DownloadEngine.create_download_task`（`download_engine.py:126`）：

1. 规范化浏览器请求上下文（如有）。
2. `check_url_support_range` 用一次流式 `GET` 带 `Range: bytes=0-0` 同时判定 Range 支持与真实大小：响应 `206` 且 `Content-Range` 合法 → 支持分块；`200` → 单线程，大小取 `Content-Length`。这样兼容禁用 HEAD 的站点。
3. 计算线程数：不支持 Range 时为 1；支持时取 `min(config.thread_count, total_size)`，避免分块区间出现 `end=-1`。
4. 任务与分块在**同一个事务**内写入（`create_task_with_chunks`），防止半截脏数据。

### 3.2 多线程分块下载

`_start_multithread_download`（`download_engine.py:320`）：

- 分块区间由 `_build_chunks` 计算：`chunk_size = total_size // thread_count`，最后一块到文件末尾；临时文件为 `temp/{task_id}.part{i}`。
- 每个分块一个 `ChunkDownloader`，共用一个 `ThreadPoolExecutor(max_workers=thread_count)`。
- 任务级限速按分块数均分：`chunk_speed_limit = speed_limit // len(chunks)`。
- 进度由独立的监控线程 `_monitor_progress` 每秒汇总各分块已下载字节并回写数据库。

### 3.3 单线程下载

`_start_singlethread_download`（`download_engine.py:479`）：临时文件为 `temp/{task_id}.tmp`。非 Range 任务恢复时必须从 0 重下（不能 append 脏数据），进度用指数平滑计算速度并夹逼防止毛刺。

### 3.4 合并与校验

全部完成后 `_merge_and_finish` 调 `merge_chunks` 按序拼接并删除临时文件，随后进入 `_verify_and_finish`（`download_engine.py:613`）：

1. 核对文件大小与 `total_size`，不符直接 `failed`。
2. 状态置 `verifying`，计算哈希（`calculate_file_hash`，分块读取，支持 md5/sha256）。
3. 有预期哈希且匹配 → `completed`；不匹配 → `verify_failed`；无预期哈希 → 记录实际哈希并 `completed`。

完成时按 `started_at` 计算耗时与平均速度，写入 `download_history`。

### 3.5 断点续传

- 每个分块的已下载字节实时写入 `download_chunks.downloaded_bytes`；`ChunkDownloader` 每次重试都按临时文件真实大小重算偏移。
- 程序启动时 `TaskManager._recover_stale_downloading_tasks` 把遗留的 `downloading`/`verifying` 纠偏为 `paused`；`_reconcile_incomplete_tasks_progress` 再按临时文件真实字节回填进度（分块任务逐个分块核对，单线程任务读 `.tmp` 大小）。
- 暂停分块任务时销毁下载线程、恢复时按临时文件续传；暂停单线程任务时保留活跃下载器，直接 `resume()`，避免进度归零。

### 3.6 失败降级

分块模式若有分块失败，`_fallback_to_singlethread`（`download_engine.py:439`）会清理分块临时文件、把任务切为单线程（`mark_task_singlethread`）并从头重试一次；仍失败才判 `failed`。

### 3.7 进度上报节流与数据库日志模式

分块下载每读 8 KB 就回调一次进度，若每次都写库，1 GB 文件会产生约 13 万次「新建连接 + 全局锁 + UPDATE + commit」。因此：

- **节流**：`ChunkDownloader._report_progress()` 只在累计 **256 KB**（`PROGRESS_INTERVAL_BYTES`）或距上次 **0.5 s**（`PROGRESS_INTERVAL_SECONDS`）后才回调；**收尾必须补报**（正常结束、发现分块已完成、取消退出时用 `force=True`）。单线程任务复用同一节流，因为它同样经由 `ChunkDownloader` 发起回调。
- **为何不影响续传**：数据库里的分块字节数不是续传真值——续传偏移来自临时文件实际大小（`download()` 每次重试重新 `getsize`），合并前终态写入用内存值，重启后 `_reconcile_incomplete_tasks_progress` 仍会按临时文件回填。数据库最多滞后一个阈值，只影响进度显示。
- **WAL**：`DatabaseManager._init_database` 设置 `PRAGMA journal_mode=WAL`，让写入不必等待读锁；不支持时（网络盘、只读目录）只记警告并退回默认日志模式。未改动 `synchronous`，掉电耐久性与改动前一致。

## 4. 任务状态机

任务状态定义在 `download_tasks.status`，共 8 个：

| 状态 | 含义 |
|------|------|
| `pending` | 已创建，等待调度 |
| `downloading` | 下载中 |
| `paused` | 已暂停（含程序重启纠偏） |
| `verifying` | 合并后正在校验哈希 |
| `completed` | 完成 |
| `failed` | 失败 |
| `verify_failed` | 哈希校验不通过 |
| `cancelled` | 已取消 |

合法流转：

```
                 ┌────────────► cancelled ◄────────── 任意非 completed
                 │                 ▲
pending ──start──► downloading ──pause──► paused ──resume──┐
   ▲                 │  ▲                                   │
   │                 │  └───────────────────────────────────┘
   │                 │
   │            (完成/失败)
   │                 ▼
   └── retry ── failed / verify_failed
                 │
              verifying ──► completed
```

- 创建后为 `pending`；`start_task` 接受 `pending/paused/failed/verify_failed/cancelled` 作为可启动状态。
- `verify_failed` 重试前会把任务进度与分块进度清零。
- 程序重启时 `downloading`/`verifying` 统一纠偏为 `paused`。
- 并发已满时 `resume_task` 会把任务落回 `pending` 排队，而非强启。
- 「稍后下载」的新任务直接以 `paused` 状态落库（`db.create_task_with_chunks(status=...)`），且 `add_task(start_later=True)` 不触发 `_try_start_next_task()`；不新增状态，也不区分「用户暂停」与「排队等候」。

## 5. 并发与线程模型

- **并发门禁**：`TaskManager` 维护 `_running_tasks` 集合与 `max_concurrent`（1-5，默认 3），加锁保护；`_scheduling_paused` 用于批量暂停期间阻止自动调度。
- **调度顺序**：FIFO。`db.get_all_tasks` 按 `created_at ASC` 排序，`_try_start_next_task` 取 `pending` 队首，`resume_all` 也按创建时间升序恢复。
- **会话隔离**：`DownloadEngine` 为每个任务维护自增 `run_id`。启动/暂停/取消都会作废旧 `run_id`，所有回调与收尾逻辑先校验 `_is_current_task_run`，防止旧线程回写覆盖新状态（`download_engine.py:44`）。
- **线程归属**：每个任务独立 `ThreadPoolExecutor`；多线程任务另有监控线程；视频任务为单 worker 线程。
- **UI 线程安全**：工作线程只向 `SimpleQueue` 入队，主线程用 `after` 定时器（50ms 刷新事件、1s 刷新状态栏）消费，所有控件操作都在 Tk 主线程执行。

## 6. 数据模型（SQLite）

`data/downloads.db`，`DatabaseManager` 加线程锁串行访问，开启外键级联删除。

**download_tasks**

| 字段 | 说明 |
|------|------|
| task_id | UUID 主键 |
| url / filename / save_path | 链接、文件名、保存全路径 |
| total_size / downloaded_size | 总大小、已下载 |
| status | 见第 4 节 |
| support_range / thread_count | 是否支持 Range、线程数 |
| speed | 当前速度 |
| created_at / started_at / completed_at | 时间戳 |
| error_message | 失败原因 |
| expected_hash / expected_hash_type / actual_hash / hash_verified | 校验相关（迁移新增） |
| browser_context | DPAPI 加密的浏览器上下文（迁移新增） |
| download_type / video_height | 任务类型（file/youtube/x/bilibili/douyin/hls/dash）与视频清晰度（迁移新增） |

**download_chunks**：chunk_id、task_id、chunk_index、start_byte、end_byte、downloaded_bytes、status、temp_file、retry_count。

**download_history**：id、task_id、filename、file_size、download_time、avg_speed、completed_at。

索引：`idx_task_status`、`idx_chunk_task`、`idx_chunk_status`。老库通过 `ALTER TABLE` 补列，不做整库迁移。日志模式为 WAL（不支持时退回默认），见第 3.7 节。

## 7. 浏览器视频链路

### 7.1 组件与消息流

```
网页 ──扩展(background.js/webRequest)──► 扩展内存列表
用户点下载 ──chrome.runtime.connectNative──► BrowserBridge.exe (native_host)
   ──长度前缀 JSON──► 转发 ──回环 HTTP POST 127.0.0.1 + Bearer token──► 桌面程序 BridgeServer
   ──► TaskManager.add_task / add_youtube_task ──► 引擎下载
```

- `BrowserBridge.exe` 只做转发：先 `ping` 唤醒桌面程序，必要时拉起 `daw下载器.exe`，再转发 `download`。它通过读取 `%LOCALAPPDATA%\LaoWangDownloader\{instance}.bridge`（DPAPI 加密，含端口与 token）定位桌面程序。
- `BridgeServer` 只监听 `127.0.0.1`，校验 `Host`、禁止带 `Origin`、用 `secrets.compare_digest` 比对 Bearer token；`request_id` 幂等去重，重名文件自动加序号。

### 7.2 视频类型与路径

扩展通过 `webRequest` 的 `onHeadersReceived` 与内容脚本上报识别资源，分为：

| 类型 | 说明 | 处理路径 |
|------|------|----------|
| `mp4` / `webm` | 视频直链 | 普通分块/单线程下载 |
| `hls` / `dash` | M3U8 / MPD 播放列表 | yt-dlp + FFmpeg，native 分片下载器，保留断点 |
| `youtube` / `x` / `bilibili` | 视频页 | yt-dlp 解析页面 |
| `douyin` | 抖音视频页 | 浏览器已取得签名 formats，经 `--load-info-json` 交给 yt-dlp，避免重复请求未签名页面 API |

- `VIDEO_KINDS`（`youtube_downloader.py:17`）为可下载类型全集；`STREAM_KINDS` = hls/dash；`CONTEXT_KINDS` = douyin/hls/dash，需要携带 Cookie 与请求头。
- 清晰度固定 480/720/1080，由扩展设置项控制，通过 `--format` 过滤 `height<=N`。
- 视频任务统一走 `YoutubeDownloader`：构造 yt-dlp 命令、解析 `__LW_PROGRESS__` 进度行、用 ffprobe 校验音视频轨与时长，时长与页面声明不符则拒绝发布不完整文件。
- 抖音额外校验：只接受 `*.douyinvod.com` 的完整 MP4，拒绝 `media-video`/`media-audio` 分轨地址。

### 7.3 安全模型

- **加密**：浏览器上下文（含 Cookie）经 DPAPI 加密后存入 `browser_context` 字段；仅运行时解密，且只在子进程临时 Cookie 文件中落地。
- **请求头白名单**：`normalize_context` 只放行 `User-Agent`、`Referer`、`Cookie` 三个头，其余丢弃；值长度与字符集受限。
- **跨源保护**：`browser_get`（`security.py:110`）只在目标源与上下文源一致时才附带凭据，跳转到其它源时自动剥离，并拒绝 HTTPS 降级。
- **内容类型白名单**：直链下载只接受 `video/mp4`、`video/webm`、`application/octet-stream`；HLS/DASH 走 video 任务路径，不受此限制。
- **Cookie 作用域**：`normalize_stream_context` 校验每个 Cookie 的域必须在媒体域内，否则拒绝。

## 8. 配置

`data/config.json`，默认值见 `config.py`：`download_dir`（默认 `~/Downloads/daw下载器`）、`temp_dir`、`thread_count`（1-16）、`max_concurrent_downloads`（1-5）、`retry_times`、`chunk_size`、`timeout`、`user_agent`、`proxy{enabled,http,https}`、`close_behavior`（ask/minimize/exit）、`speed_limit`（字节/秒）、`after_download{open_file,open_folder,shutdown}`（下载完成后动作，默认全关）、`ui_hide_completed`（任务列表隐藏已完成，默认 false）。设置对话框保存后通过回调即时同步运行时对象（含活跃下载器限速）。

### 8.1 任务列表显示与过滤

- 每张任务卡片两行信息：行一是进度条 + 百分比 + 「文件位置」；行二是 `已下载 X / Y`、`剩余 mm:ss`（`file_utils.format_remaining`）、速度、状态。只有 `downloading` 且速度大于 0 时才显示剩余时间，其余情况显示 `--`，总大小未知时显示 `X / 未知`。
- 过滤由 `MainWindow._apply_task_filter()` 统一处理：搜索词按文件名/URL 子串（不区分大小写，不做 percent 解码）匹配，「隐藏已完成」按状态过滤；命中 `pack`、不命中 `pack_forget`，空结果显示「没有匹配的任务」。
- 过滤依赖 `task_widgets` 中缓存的 `filename`/`url`/`status`，不额外查库；「隐藏已完成」写入 `ui_hide_completed`，搜索词不持久化。

### 8.2 添加任务（批量与稍后）

- 「下载链接」为多行输入：每行一个链接，空行丢弃，同批内按 URL 去重。
- 行数大于 1 时自动禁用文件校验控件（批量无单一哈希概念），单链接时保持原哈希校验能力。
- 单链接沿用同步添加与即时提示；多链接走后台线程逐条 `add_task`（每条都要发探测请求，同步会阻塞 UI），完成后经 `_ui_events` 回主线程汇总成功/失败/跳过数量，失败原因最多列 3 条；日志只记数量，不记 URL。
- 按钮为「开始下载」（`pending` 并进入队列调度）与「稍后下载」（`paused`，不进调度）；多行输入框内回车为换行，提交用 Ctrl+Enter。

完成后动作的触发链路：引擎状态回调把 `completed` 事件投递到 UI 事件队列（`MainWindow._handle_download_completed`），只有 `completed`（校验通过）才触发，`verify_failed` 不触发。开启关机时先检查是否还有 `downloading/pending/verifying` 任务，还有则等最后一个完成再弹倒计时确认框（默认 60 秒，关窗即取消）；确认或倒计时结束才执行 `shutdown /s /t 0`。

## 9. 日志与排障

- **配置入口**：`main.py` 启动时调用 `setup_logging()`（`downloader/utils/app_log.py`），其余模块统一用 `logging.getLogger(__name__)`，全部位于 `downloader` 命名空间下。
- **输出目标**：默认写 `<程序目录>/logs/app.log`（`RotatingFileHandler`，2 MB × 3，UTF-8）；目录不可写时回退 `%LOCALAPPDATA%\LaoWangDownloader\logs`；两者都不可用时静默降级，不阻断启动。
- **stdout 禁区**：`native_host.py` 用 stdout 传输长度前缀 JSON，任何 handler 都不得写入 stdout。因此冻结环境只挂文件 handler，开发环境额外挂 stderr。
- **脱敏**：网络相关异常用 `app_log.safe_error(error, sensitive=True)` 只记类型名，避免异常消息里的链接或 Cookie 落盘；顶层未捕获异常默认也只记类型名，需要完整堆栈时设 `DAW_LOG_LEVEL=DEBUG`。
- **视频失败诊断**：`YoutubeDownloader.run` 失败时以 WARNING 记录失败原因，并用 `sanitize_output()` 把 yt-dlp 输出尾部（链接替换为 `<url>`，Cookie/Authorization 字段替换为 `<redacted>`）写入 INFO。`failure_message()` 优先区分限流（HTTP 429）与登录验证，前者提示稍后重试或更换网络与代理。
- **级别**：默认 `INFO`，可用环境变量 `DAW_LOG_LEVEL` 覆盖；开发环境 `logs/` 已被 `.gitignore` 排除。

## 10. 构建与发布

- **build_exe.py**：调用 PyInstaller 依次打包 `老王下载器.spec`（主程序，`console=False`）与 `browser_bridge.spec`（桥接，`console=True`），再把 `video-tools/`、`chrome-extension/` 与浏览器安装脚本拷入 `dist`。构建子进程的 PATH 前置 `sys.prefix/Library/bin`，保证 Tcl/Tk 等依赖来自当前环境。不清理整个 `dist`，保留用户数据。
- **build_installer.py**：用固定 SHA256 的便携 NSIS 3.13 编译 `installer/daw-downloader.nsi`，生成可选路径、可卸载的安装包与独立 `.sha256` 文件。
- **package_release.py**：按显式白名单（程序文件 + 扩展文件 + video-tools + 发布说明）打便携版 ZIP 与独立扩展 ZIP，并校验 `dist` 扩展与源码一致。
- **冒烟测试**：`scripts/smoke_exe.py`（EXE 实际下载与重启续传）、`smoke_browser_bridge.py`（安装后桥接）、`smoke_installer.py`（安装/升级/卸载/重装）等，均为真机验证，不替代用户端验收。

安装版与便携版都遵循同一数据保护约定：升级与卸载保留配置、下载记录、下载文件与断点。
