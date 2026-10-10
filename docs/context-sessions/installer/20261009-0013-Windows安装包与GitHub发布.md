# Session: Windows 安装包与 GitHub 发布

## 元信息

- 创建时间：2026-10-09 00:13 +08:00（Asia/Shanghai）
- 更新时间：2026-10-10 18:01 +08:00（v0.3.1 构建与发布）
- 状态：已完成（v0.3.0 安装包与 v0.3.1 新版本均已交付；v0.3.1 的 GitHub Release 由用户创建上传）
- 项目路径：`D:/vscode_workspace/downLoad_project`
- 用户要求：下载器支持自选安装路径和卸载；提供完整操作说明；将安装包提交 GitHub，并更新进度、Codex 和 Claude Code 本地记忆。

## 上下文摘要

沿用原 `dist` 已验证的主程序、BrowserBridge、浏览器扩展与视频组件，使用 NSIS 制作离线安装包。中文向导可选安装路径，提供开始菜单、可选桌面快捷方式及 Windows 系统卸载入口；默认当前用户安装，升级和卸载保留配置、记录、下载文件和断点。

安装包和独立 SHA256 文件已补充到既有 `v0.3.0` Release。配套源码和说明已提交 `8fe9831be738e1e0bebadb0fbd826bcb02056d83` 并推送远端 `main`。本地分支为 `master`，跟踪 `origin/main`；没有创建新分支，也没有移动 `v0.3.0` 标签。

2026-10-10：桌面程序新增运行日志、分块进度节流、数据库 WAL 与视频失败诊断后，按用户要求**另开 `v0.3.1` 发布**，不替换也不改动 `v0.3.0` 的记录。安装包从已验证的 `dist` 封装（未 `--rebuild`），便携包与校验文件由 `scripts/package_release.py v0.3.1` 生成。

## 已完成任务

- [x] 新增 `build_installer.bat` 和构建脚本，复用发布文件清单；缺少 NSIS 时下载并验证固定工具归档。
- [x] 完整安装、覆盖升级、文件占用拦截、卸载保留数据、重装和浏览器注册归属保护通过。
- [x] 安装后的双 EXE 实际完成带测试授权信息的视频下载，字节一致性、去重、单实例及正常退出通过。
- [x] 完整操作说明在 `docs/INSTALLER.md`，README、BUILD_EXE 和发布说明均已更新。
- [x] 两个新附件上传成功，GitHub 返回的大小和 SHA256 与本地一致；既有三个附件保持不变。
- [x] 代码和文档已推送，Release 已补充安装版入口及源码提交链接。
- [x] Codex、Claude Code 记忆正文和索引已同步，Claude 入口已更新；JSON、ID、source_file/anchor、入口链接、条目预算及双库内容一致性验证通过。
- [x] 已建立本专题进度档，并在原下载器交付进度档增加接续链接。
- [x] 按 v0.3.1 重建安装包与发布产物：`build_installer.py v0.3.1`（仅封装已验证 dist）+ `package_release.py v0.3.1`。
- [x] 校对安装包：SHA256 与 `.sha256` 一致、`FileVersion 0.3.1.0`、7z 核查 25 项载荷且不含 `data`/`temp`/`logs`。
- [x] 校对便携 ZIP：CRC 通过、24 项（23 个发布文件 + 发布说明 `README.md`）、无用户运行数据。
- [x] 新增 `docs/releases/v0.3.1.md`，README、PROJECT_SUMMARY、BUILD_EXE、INSTALLER 同步到 v0.3.1 链接与校验值。
- [x] 提交 `45701e4` 并推送 `main`，创建并推送 annotated 标签 `v0.3.1`；`v0.3.0` 标签仍指向 `5c00c3d`。

## v0.3.1 发布（2026-10-10）

本次发布为“运行日志 + 进度节流 + WAL + 视频失败诊断”，桌面程序与新载荷双 EXE 均重新打包并实测；安装脚本、向导与卸载逻辑未改动。

- 发布标识：`v0.3.1`，标签指向 `45701e4075a9cdf0b48461fd8b13399aff6d30b9`（源码提交 `45701e4`）。
- 安装包：`dist/daw-downloader-v0.3.1-windows-x64-setup.exe`，155,645,269 字节（148.44 MiB），`FileVersion 0.3.1.0`，SHA256 `a1a137a5409c38f34f0247396db2a3d477028c72d1e1c1e8e281a3d75a857bfa`。
- 便携包：`daw-downloader-v0.3.1-windows-x64.zip`，213,802,439 字节，SHA256 `b6ea05ce3594493d7b0f6d040af1ebe8ed8be98cef32f101c7f74cca906a1f62`；本地产物在 `output/releases/v0.3.1/`（不提交 Git）。
- 扩展包：`daw-browser-extension-v0.5.0.zip`，17,768 字节，SHA256 `b0bdbb7bf475905711b8a77370fa381102c5d7e062cbe96aae025f18f168682a`（扩展与 v0.3.0 相同）。
- `SHA256SUMS.txt`（205 字节）校验上述两个 ZIP。
- 载荷 EXE：主程序 21,634,509 字节 SHA256 `dcc22670…29fc4c`，桥接 9,971,991 字节 SHA256 `c207f9f3…a05b90b`。
- 附件上传与 Release 创建由用户自行完成（当前环境无 `gh` CLI 也没有 GitHub 令牌，代理无法代传）。
- 本次未重复执行完整安装向导验收；载荷双 EXE 已在独立目录实测启动写日志、WAL 生效、正常退出，并通过 111 项 Python 回归。此边界已记入 `docs/INSTALLER.md`。

## 发布与产物

- 发布页：<https://github.com/dwdw530/downLoad/releases/tag/v0.3.0>
- 安装包：<https://github.com/dwdw530/downLoad/releases/download/v0.3.0/daw-downloader-v0.3.0-windows-x64-setup.exe>
- 校验文件：同一下载地址追加 `.sha256`。
- 本地安装包：`dist/daw-downloader-v0.3.0-windows-x64-setup.exe`。
- 大小：155,650,713 字节（148.44 MiB）；文件版本：`0.3.0.0`。
- SHA256：`aaa6ef2d89a3ed15e5b3eeeecd2711c0efc96c5c1502fd7e2604e545129b1f88`。
- GitHub 安装包附件 ID：`622290548`；校验附件 ID：`622292469`；Release ID：`406914934`。
- 发布页说明更新于 2026-10-09 00:11:27 +08:00，当前共 5 个附件。
- 公开下载地址已通过 HTTP 200 检查，Content-Length 为 155,650,713，与本地和 GitHub 资产元数据一致。
- 原 `v0.3.0` 标签仍指向桌面程序提交 `5c00c3d1252bff98610ebf199edc4df870c1ad93`；安装器源码从 `main` 或上述 `8fe9831` 提交获取。

## 关键文件与操作

- `build_installer.bat`：双击重建主程序、桥接和安装包。
- `scripts/build_installer.py`：`python -B scripts/build_installer.py v0.3.0 --rebuild` 完整构建；去掉 `--rebuild` 仅封装当前已验证的 `dist`。
- `installer/daw-downloader.nsi`：中文向导、安装注册、快捷方式、程序占用检查和按文件卸载。
- `installer/使用说明.txt`：安装到用户电脑的操作说明。
- `scripts/package_release.py`：ZIP 和安装包共用明确的发布文件清单，排除用户运行数据。
- `scripts/smoke_installer.py`：真实安装向导、安装后程序、升级、卸载和重装验收。
- `scripts/smoke_browser_bridge.py --app-dir`：仅允许没有已有 `data` 目录的一次性安装路径，直接验证安装后的 EXE。
- `docs/INSTALLER.md`：构建前提、安装、升级、卸载、静默参数、测试和 GitHub 发布方法。

## 验证结果与边界

- NSIS 3.13 构建成功；便携工具 ZIP 的固定 SHA256 已核对，不安装全局编译器、不修改系统 PATH。
- `output/installer-smoke/run-45uilqcb/result.json` 记录完整验收结果；25 个安装文件与发布清单逐一比较哈希一致。
- 在中文和空格目录中安装，通过快捷方式、卸载注册项、桥接清单及其路径检查。主 EXE 被占用时升级/卸载均返回 2，未继续移除程序或注册项。
- 安装后的 BrowserBridge 冷启动同目录主程序，经本地 HTTP 服务实际下载视频。日志为同目录 `installed-app-smoke.log`，视频 SHA256 为 `f2ae004a94366f29ee1b8a624bbcffeab995d75057f1eaf766deed5384326478`。
- 同目录升级、卸载和重装后，测试配置、数据库、下载文件、断点及用户自建文件哈希保持。正常卸载只移除自身桥接注册，已有另一目录注册不会被误删。
- 浏览器集成 12 项 Python 回归通过；暂存区 `git diff --cached --check` 通过。
- 测试结束已移除测试安装注册项和快捷方式，恢复原浏览器连接，原 `dist/data` 哈希未变。
- 首轮鼠标操作测试被 Windows 锁屏拦截，未记为通过。安装向导通过原生控件消息操作；锁屏下截图为空白或不完整，未用于视觉验收。没有重复进行未修改站点的实网测试。

## 技术决策与注意事项

- 自选安装目录必须对当前用户可写；便携版原 `dist/daw下载器.exe` 继续保留。
- 不清空整个 `dist` 或安装目录；卸载不递归删除用户目录。不要将用户数据、本机桥接清单或构建缓存发布到 Git。
- 浏览器扩展仍需手动加载安装位置下的 `chrome-extension`，更新后完整刷新已打开的视频网页；卸载后可在浏览器中手动移除扩展。
- 大安装包通过 Releases 附件分发，使用独立的 `.exe.sha256` 校验；既有 `SHA256SUMS.txt` 仍对应两个旧 ZIP。
- 当前构建、发布结果与操作说明均已存在。后续修改后是否重建、覆盖或发布，以当时任务授权为准。
- **发布新版本时不覆盖既有 Release 记录**：用户明确要求发新版本、不要替换原版本的 GitHub 记录；新内容另开版本号（如 `v0.3.2`）。
- 安装包与便携包必须从已验证的 `dist` 生成；重建 EXE 前确认下载器已正常退出，并核对 `dist/data` 哈希未变。

## 本地记忆

- Codex：`C:/Users/lenovo/.codex/projects/downLoad_project/memory/project-handoff.md` 和同目录 `memory_index.json`。
- Claude Code：`C:/Users/lenovo/.claude/projects/D--vscode-workspace-downLoad-project/memory/project-handoff.md`、`memory_index.json`、`MEMORY.md`。
- 两库安装包约定指向本档；版本、哈希、提交和临时验证输出只保留在进度/说明文档中。

## 下一步行动

本轮暂无剩余实施任务。后续修改安装器时从本档和 `docs/INSTALLER.md` 接续，先核对当前源码、发布附件及用户数据，再按本次具体需求构建和验收。

下一次发布的起点：核对 `dist/data` 与当前双 EXE 哈希 → 跑 `python -B -m unittest discover -s tests` → 需要重建时 `scripts/build_exe.py` → `scripts/build_installer.py <新版本>` 与 `scripts/package_release.py <新版本>` → 更新 `docs/releases/<新版本>.md` 与相关链接 → 提交推送并另开标签，保留原 Release。
