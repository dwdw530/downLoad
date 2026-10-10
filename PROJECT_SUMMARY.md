# daw下载器 · 项目总结

老王一句话：一个 Windows 桌面多线程下载器，走 IDM 那套玩法——分块并发、断点续传、队列调度，外挂一条浏览器视频下载链路（扩展 + 原生消息桥接）。

> **仅支持 64 位 Windows。** 程序依赖 DPAPI 加密、Chrome 原生消息注册表和 NSIS 安装包，代码中没有跨平台分支。

---

## 正式产物

| 产物 | 位置 | 说明 |
|------|------|------|
| 桌面程序 | `dist/daw下载器.exe` | 便携版入口，原名“老王下载器” |
| 桥接程序 | `dist/BrowserBridge.exe` | Chrome 原生消息主机 |
| 浏览器扩展 | `dist/chrome-extension/` | Manifest V3，当前版本 0.5.0 |
| 离线视频组件 | `dist/video-tools/` | yt-dlp + FFmpeg + ffprobe + Node |
| 安装包 | `dist/daw-downloader-v0.3.0-windows-x64-setup.exe` | NSIS 离线安装包，可选安装路径、可从系统卸载 |

发布统一走 GitHub Releases（当前 `v0.3.0`）：便携版 ZIP、独立扩展 ZIP 与安装包，均附 SHA256 校验。

---

## 功能特性

| 功能 | 状态 | 说明 |
|------|------|------|
| 多线程分块下载 | 已实现 | 1-16 线程，按文件大小自动收敛线程数 |
| 断点续传 | 已实现 | 分块进度实时入库，暂停/重启可续 |
| 任务队列管理 | 已实现 | 并发上限 1-5，FIFO 调度 |
| 实时进度显示 | 已实现 | 进度条、速度、状态实时刷新 |
| 批量操作 | 已实现 | 暂停全部 / 继续全部 |
| 代理支持 | 已实现 | HTTP/HTTPS 代理，设置对话框配置 |
| 速度限制 | 已实现 | 令牌桶限速，按任务总量在分块间分配 |
| 文件校验 | 已实现 | MD5 / SHA256，下载后自动比对 |
| 下载历史 | 已实现 | 历史对话框查看与清空 |
| 系统托盘 | 已实现 | 最小化到托盘、下载完成通知 |
| 浏览器集成 | 已实现 | 扩展识别网页视频，经桥接送入下载器 |
| 通用流媒体 | 已实现 | HLS（M3U8）/ DASH（MPD）分片下载与合并 |
| 自动分类 | 未实现 | 按文件类型分目录 |
| 计划任务 | 未实现 | 定时下载 |

---

## 源码结构

```
downLoad_project/
├── downloader/                   # 核心包（21 个模块，约 5000 行）
│   ├── core/
│   │   ├── download_engine.py    # 引擎总控：探测、分块、调度、合并、校验
│   │   ├── chunk_downloader.py   # 单分块下载器 + 令牌桶限速
│   │   ├── task_manager.py       # 任务队列、状态机、并发门禁
│   │   └── youtube_downloader.py # 视频页/流媒体封装（yt-dlp + FFmpeg）
│   ├── database/db_manager.py    # SQLite：任务、分块、历史
│   ├── ui/
│   │   ├── main_window.py        # 主窗口、任务卡片与各对话框
│   │   ├── settings_dialog.py    # 设置对话框
│   │   ├── history_dialog.py     # 下载历史对话框
│   │   └── tray_manager.py       # 系统托盘与完成通知
│   ├── browser/
│   │   ├── bridge.py             # 本机回环 IPC 服务与单实例互斥
│   │   ├── native_host.py        # Chrome 原生消息主机
│   │   ├── registration.py       # 原生消息主机注册表注册
│   │   └── security.py           # DPAPI 加密与请求头/域校验
│   └── utils/
│       ├── config.py             # 配置读写
│       └── file_utils.py         # 分块合并、哈希计算、格式化
├── chrome-extension/             # 浏览器扩展（Manifest V3）
├── installer/                    # NSIS 安装脚本与使用说明
├── scripts/                      # 构建、发布与冒烟测试脚本
├── assets/                       # 图标资源
├── main.py                       # 桌面程序入口
├── browser_host.py               # 桥接 EXE 入口
├── 老王下载器.spec               # 主程序 PyInstaller 配置
└── browser_bridge.spec           # 桥接 PyInstaller 配置
```

运行数据 `data/`（`downloads.db` + `config.json`）和 `temp/` 由程序自动创建；发布包按白名单排除这些运行数据，源码仓库中保留初始状态的文件。

---

## 快速开始

### 方式一：直接运行 EXE

1. 运行 `dist/daw下载器.exe`（便携版），或安装 `dist/*-setup.exe` 后从开始菜单启动
2. 添加下载链接，开始下载

首次运行会自动在程序目录下创建 `data/` 和 `temp/`。

### 方式二：源码运行（开发调试）

```bash
conda activate py310_env
pip install -r requirements.txt
python main.py
```

---

## 打包与发布

```bash
# 只重建两个 EXE，更新 dist
python -B scripts/build_exe.py

# 重建 EXE 并打 Windows 安装包（缺少 NSIS 时自动下载固定版本）
python -B scripts/build_installer.py v0.3.0 --rebuild

# 打便携版 ZIP 与独立扩展 ZIP
python -B scripts/package_release.py v0.3.0
```

`build_exe.py` 通过两个 spec 分别打包主程序与桥接，再把 `video-tools/`、`chrome-extension/` 和浏览器安装脚本拷进 `dist`；构建时把当前环境的 `Library/bin` 置于 PATH 最前，避免混入 base 环境的 Tcl/Tk。发布文件清单在 `scripts/package_release.py` 中显式白名单，排除用户运行数据。详细步骤见 `docs/INSTALLER.md`。

---

## 数据存储

```
data/
├── downloads.db    # SQLite：任务、分块、下载历史
└── config.json     # 用户配置
temp/               # 临时分块文件（.partN / .tmp）
```

删除任务默认只删数据库记录，是否同时删除已下载文件由用户在确认框中勾选。

---

## 技术栈

- **语言**：Python 3.10（`py310_env`，实测 3.10.14）
- **GUI**：CustomTkinter
- **HTTP**：requests（`concurrent.futures` 线程池）
- **数据库**：SQLite3
- **视频**：yt-dlp + FFmpeg + ffprobe + Node（随包分发）
- **打包**：PyInstaller（EXE）+ NSIS（安装包）

---

## 文档索引

| 文档 | 说明 |
|------|------|
| `README.md` | 项目说明、功能特性、使用教程 |
| `BUILD_EXE.md` | 打包 EXE 详细教程 |
| `design.md` | 架构设计：模块职责、状态机、数据模型 |
| `docs/BROWSER_VIDEO.md` | 浏览器视频集成说明 |
| `docs/INSTALLER.md` | 安装包构建、安装、升级与卸载 |
| 本文档 | 项目总结与快速开始 |

---

## 后续计划

- [ ] 自动分类（按文件类型分目录）
- [ ] 计划任务（定时下载）

---

**用得爽就给个 Star，用得憋屈就提 Issue！** 😤
