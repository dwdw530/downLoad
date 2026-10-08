# daw下载器 (PyDownloader)

一个类似IDM的多线程下载器，支持断点续传、队列管理等功能。

正式入口：`dist/daw下载器.exe`（原名“老王下载器”）。窗口、托盘和 EXE 使用统一的绿色下载箭头图标。旧配置和下载目录保持原样；浏览器扩展更新后需重新加载，原桥接标识不变。

Windows 用户可直接下载 [安装包 EXE](https://github.com/dwdw530/downLoad/releases/download/v0.3.0/daw-downloader-v0.3.0-windows-x64-setup.exe)，安装时选择目录，之后可从系统中卸载。[GitHub Releases](https://github.com/dwdw530/downLoad/releases/latest) 同时提供完整便携版 ZIP 和独立浏览器扩展；GitHub 自动生成的 Source code 是源码。

项目现支持生成中文 Windows 安装包：安装时可选择目录、创建快捷方式，之后可从系统“已安装的应用”中卸载。双击 `build_installer.bat` 构建，产物在 `dist/*-setup.exe`；卸载保留配置、下载记录、断点和下载文件。完整步骤见 [安装包说明](docs/INSTALLER.md)。

## 功能特性

✅ **多线程分块下载** - 最多16线程，充分利用带宽
✅ **断点续传** - 随时暂停，下次继续，不浪费流量
✅ **队列管理** - 支持同时下载多个任务
✅ **实时进度显示** - 进度条、速度、状态一目了然
✅ **数据持久化** - SQLite存储，任务重启不丢失
✅ **简洁GUI** - 基于CustomTkinter，现代化界面

## 项目结构

```
downDemo/
├── downloader/
│   ├── core/              # 核心下载引擎
│   │   ├── chunk_downloader.py
│   │   ├── download_engine.py
│   │   └── task_manager.py
│   ├── database/          # 数据库管理
│   │   └── db_manager.py
│   ├── ui/                # GUI界面
│   │   ├── main_window.py
│   │   └── settings_dialog.py
│   └── utils/             # 工具函数
│       ├── config.py
│       └── file_utils.py
├── data/                  # 数据目录（自动创建）
│   ├── downloads.db       # SQLite数据库
│   └── config.json        # 用户配置
├── downloads/             # 默认下载目录（自动创建）
├── temp/                  # 临时分块文件（自动创建）
├── main.py                # 启动入口
├── requirements.txt       # 依赖清单
└── README.md
```

## Chrome 视频下载助手

网页视频识别和播放器悬浮下载按钮支持 MP4、WebM 直链、HLS/DASH 播放列表，以及 YouTube、X、B 站和抖音普通视频。页面视频复用 yt-dlp + FFmpeg，可选最高 480p/720p/1080p。抖音使用当前网页取得的完整 MP4 信息，校验音视频轨和完整时长；不会把分离的音轨、视频轨当成完整视频。推荐页无法确认视频或取得详情信息时，需要打开该视频的详情页。

2026-10-08：原 `dist` 已更新到包含抖音修复的正式包，扩展版本 0.5.0。用户提供的视频已通过真实扩展、正式 EXE 下载和整片解码验证；通用 HLS/DASH 的实网验证边界见测试记录。

安装需要完整发布目录，包括 `BrowserBridge.exe`、`chrome-extension/` 和 `video-tools/`，具体步骤、权限与限制见 [浏览器视频集成说明](docs/BROWSER_VIDEO.md)。本功能不自动接管 Chrome 的普通文件下载，也不读取 YouTube 账号 Cookie。更新后须完全退出并重启下载器，重新加载扩展及刷新视频页。

重新加载扩展后，对已经打开的 X、YouTube、B 站、抖音标签页分别按 **F5 / Ctrl+F5 完整刷新**。只在网站内切换视频或进入帖子详情，不会重新加载旧页面中的扩展脚本，可能导致悬浮按钮消失、工具栏资源列表为空。X 仍支持首页浏览时下载，无需逐条进入详情页。

## 安装依赖

**重要：使用py310_env环境！**

```bash
# 激活conda环境
conda activate py310_env

# 安装依赖
pip install -r requirements.txt
```

## 运行程序

```bash
# 确保在py310_env环境中
python main.py
```

## 使用说明

### 1. 添加下载任务
- 点击 **"➕ 添加任务"** 按钮
- 输入下载链接
- 选择保存位置（可选，默认保存到 `downloads/` 目录）

### 2. 管理任务
- **▶ 开始** - 启动下载
- **⏸ 暂停** - 暂停下载（支持断点续传）
- **✗ 取消** - 取消任务
- **🗑 删除** - 删除任务记录
- **文件位置** - 打开文件所在文件夹（已下载会定位到文件）

### 3. 批量操作
- **⏸ 暂停全部** - 暂停下载中及排队中的任务，阻止队列继续启动
- **▶ 继续全部** - 继续所有暂停的任务

### 4. 设置
点击 **"⚙ 设置"** 按钮可配置：
- 默认下载目录
- 默认线程数（1-16）
- 同时下载任务数（1-5）
- 请求超时时间
- 下载速度限制（每个任务的总速度，0表示不限速）
- HTTP/HTTPS代理地址

### 5. 关闭程序
- 点击窗口右上角关闭会弹出 **退出确认**（居中显示、布局更紧凑）：可选择“最小化到任务栏（继续后台下载）”或“退出程序（停止所有下载）”
- 勾选“记住我的选择”后，会写入 `data/config.json` 的 `close_behavior`（ask|minimize|exit）

## 技术架构

### 核心技术栈
- **GUI框架**: CustomTkinter
- **HTTP库**: requests
- **并发方案**: concurrent.futures.ThreadPoolExecutor
- **数据存储**: SQLite3
- **Python版本**: 3.10+（推荐 `py310_env`）

### 核心功能实现

#### 多线程分块下载
1. 流式GET探测 `bytes=0-0`，获取文件大小（兼容不支持HEAD的站点）
2. 根据真实响应确认Range支持，分块下载时核对Content-Range和实际字节数
3. 计算分块（文件大小 / 线程数）
4. 创建线程池，并发下载各分块
5. 实时监控进度
6. 下载完成后按顺序合并全部分块，核对文件大小并计算哈希

#### 断点续传
- 每个分块的下载进度实时写入数据库
- 暂停时保存当前进度
- 继续时从已下载位置重新开始

#### 任务队列管理
- 状态机：pending → downloading → completed/failed/cancelled
- 支持并发控制（默认同时下载3个任务）
- 任务完成后自动启动队列中的下一个任务

## 回归验证

在 `py310_env` 环境中，从项目根目录运行：

```bash
python -B -m unittest discover -s tests -v
```

测试使用本地HTTP服务和独立临时数据库，无需访问外部下载站点。覆盖续传漏块、异常Range响应、短文件/超长文件、断流重试、跨磁盘保存、暂停/取消、队列调度、限速、代理、MD5/SHA256校验、重启恢复、历史记录、设置和真实Tk界面回调。

Windows打包后，还可以验证实际EXE：

```bash
python -B scripts/smoke_exe.py
```

脚本将EXE复制到临时目录，用本地HTTP服务验证实际按钮操作、暂停、关闭重启、断点续传、队列、SHA256和正常退出；测试会短暂操作独立测试窗口，结束后恢复鼠标位置。原有下载数据不参与测试，日志和截图位于命令输出的临时目录。

2026-09-11 验证：45项回归测试通过，重新打包的EXE也通过实际下载与重启续传检查。包内15个应用模块与源码一致，Tcl/Tk DLL来自py310_env（8.6.12）。原有配置、数据库和断点文件保留。详细范围见 [测试报告](docs/TEST_REPORT_2026-09-11.md)。

此前首版EXE“双击打不开”的问题也已修复：构建时混用了Tcl/Tk 8.6.15 DLL和8.6.12脚本，现已统一依赖来源并实际运行验证。现象、根因、修复措施和各版验证结果见 [打包启动故障记录](docs/TEST_REPORT_2026-09-11.md#已修复的打包启动故障)。

## 配置文件说明

配置和数据库位于程序目录下的 `data/`；从其他工作目录启动EXE也使用同一份数据。配置文件为 `data/config.json`：

```json
{
    "download_dir": "downloads",
    "temp_dir": "temp",
    "thread_count": 8,
    "max_concurrent_downloads": 3,
    "retry_times": 3,
    "chunk_size": 1048576,
    "timeout": 30,
    "user_agent": "PyDownloader/1.0"
}
```

## 数据库结构

### download_tasks（任务表）
- task_id: 任务ID（UUID）
- url: 下载链接
- filename: 文件名
- save_path: 保存路径
- total_size: 文件总大小
- downloaded_size: 已下载大小
- status: 状态（pending/downloading/paused/completed/failed/cancelled）
- support_range: 是否支持断点续传
- thread_count: 线程数
- speed: 当前速度

### download_chunks（分块表）
- chunk_id: 分块ID
- task_id: 关联任务ID
- chunk_index: 分块序号
- start_byte: 起始字节
- end_byte: 结束字节
- downloaded_bytes: 已下载字节数
- status: 状态
- temp_file: 临时文件路径

## 常见问题

### Q1: 程序启动报错 "No module named 'customtkinter'"
**A:** 请确保已激活py310_env环境并安装了依赖：
```bash
conda activate py310_env
pip install customtkinter
```

### Q2: 下载速度为什么不快？
**A:** 可能原因：
1. 服务器不支持Range请求（自动降级为单线程）
2. 网络带宽限制
3. 服务器限速
4. 可以尝试调整线程数（设置 → 下载线程数）

### Q3: 为什么有些文件不支持断点续传？
**A:** 服务器需要支持HTTP Range请求。部分动态生成的链接或临时链接不支持断点续传。

### Q4: 任务列表太多了，怎么清理？
**A:** 可以手动删除已完成或失败的任务。数据库文件位于 `data/downloads.db`。

## 开发者说明

### 代码规范
- UTF-8编码（无BOM）
- 中文注释优先
- 遵循KISS原则（简单就是王道）

### 添加新功能
1. 核心逻辑 → `downloader/core/`
2. UI组件 → `downloader/ui/`
3. 工具函数 → `downloader/utils/`

## TODO（未来计划）

- [ ] 代理支持
- [ ] 速度限制
- [ ] 下载历史查看
- [ ] 文件校验（MD5/SHA256）
- [ ] 自动分类（按文件类型）
- [ ] 浏览器集成
- [ ] 系统托盘

## 开源协议

MIT License

## 作者

老王 - 一个暴躁的技术流程序员

---

**艹，用得爽就给个Star！用得不爽就提Issue！老王我虚心接受批评！** 😤
