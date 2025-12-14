# 老王下载器 (PyDownloader)

一个类似IDM的多线程下载器，支持断点续传、队列管理等功能。

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
- **⏸ 暂停全部** - 暂停所有下载中的任务
- **▶ 继续全部** - 继续所有暂停的任务

### 4. 设置
点击 **"⚙ 设置"** 按钮可配置：
- 默认下载目录
- 默认线程数（1-16）
- 同时下载任务数（1-5）
- 请求超时时间

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
1. HEAD请求获取文件大小
2. 检查服务器是否支持Range请求
3. 计算分块（文件大小 / 线程数）
4. 创建线程池，并发下载各分块
5. 实时监控进度
6. 下载完成后合并分块文件

#### 断点续传
- 每个分块的下载进度实时写入数据库
- 暂停时保存当前进度
- 继续时从已下载位置重新开始

#### 任务队列管理
- 状态机：pending → downloading → completed/failed/cancelled
- 支持并发控制（默认同时下载3个任务）
- 任务完成后自动启动队列中的下一个任务

## 配置文件说明

配置文件位于 `data/config.json`：

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
