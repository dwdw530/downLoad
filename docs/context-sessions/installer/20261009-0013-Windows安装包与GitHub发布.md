# Session: Windows 安装包与 GitHub 发布

## 元信息

- 创建时间：2026-10-09 00:13 +08:00（Asia/Shanghai）
- 更新时间：2026-10-22 17:05 +08:00（运行数据取消跟踪与进度文档本地提交）
- 状态：已完成（本轮 v0.11.1 原 dist、EXE/安装包、提交与 GitHub 发布）
- 项目路径：`D:/vscode_workspace/downLoad_project`
- 用户要求：下载器支持自选安装路径和卸载；提供完整操作说明；将安装包提交 GitHub，并更新进度、Codex 和 Claude Code 本地记忆。

## v0.11.1 构建、验收与正式发布（2026-10-22）

- 本次用户明确授权提交、打包发布并更新 GitHub，包含配置原子保存、在途探测退出保护、后台任务操作和仅左侧 13 逻辑像素修正。扩展 0.6.2 内容不变，原 v0.11.0 及更早 Release/标签/附件不覆盖。
- 远端 main/master 基线均为 `97690a3`，17 个历史 Release 元数据已快照；凭据选 owner dwdw530 并经内存/curl 标准输入使用，不写仓库或日志。
- 原 dist 双 EXE 重建，包内 5 个修复模块与源码编译一致，字号明确为 -13 像素。原生 EXE 三组（`j-tth8xj`）和安装包七组（`j-wkya9k`）通过；报告 `output/installer-smoke/run-ckav_9ai/result.json`。最终 Python/GUI 全量 564 项/728.305 秒（`j-vuenad`）与扩展 43 项通过。
- 安装包 `dist/daw-downloader-v0.11.1-windows-x64-setup.exe`：155,870,471 字节，SHA256 `ce85a756ab5877f61ed06a32ebc7c0cbabee61cc9fe0e95d84bab955d0225fb1`；默认构建器与安装验收脚本已更新 v0.11.1。
- 真实配置、数据库和断点、28 个运行源码的构建前后指纹一致，原浏览器注册恢复，旧安装包保留。ZIP、Git 提交/标签、Release 与远端附件证据在实际完成后继续追加。
- 新聊天接续核验：原打包已成功，辅助 SHA256 脚本误用 Python 3.11 才有的接口，改复用现有 Python 3.10 流式哈希函数。便携 ZIP 为 213,911,031 字节 / `a17e6fc0ac51cd16ab5d892cd3ad5a600fbd20251b7e328fdd152bcafd9ba339`；扩展 ZIP 为 30,514 字节 / `ee2c04026aa9dba9026ade7137b62a50262b342ba7c3cebeb36b4931cf11fc1d`。CRC、白名单、逐项字节、安装验收哈希与数据指纹全部通过；远端仍无 v0.11.1，历史 17 个 Release 未改动。
- 源码提交 `933d778f2c48a96df57681b929fb1603425e5dcf`、main/master 与新 annotated 标签 v0.11.1 已原子推送，标签解引用一致。Release `420434179` 于 2026-10-22 15:47:26 +08:00 发布，五附件 size/digest/state 与本地一致，latest 正确，历史 17 个 Release 全部保留。发布页 `https://github.com/dwdw530/downLoad/releases/tag/v0.11.1`；远端完整核验报告 `output/releases/v0.11.1/verification-remote.json`。
- 本轮接续仅修测试子进程编码和旧字体基线的有效缩放换算；4 项字号及 19 项稳定性复验通过，运行源码/EXE 不变。原 564 项全量、EXE 三组和安装包七组日志已核实，不冒充本轮重跑。
- 五个公开下载 URL 跟随重定向均为 HTTP 200，Content-Length 与附件相同；两个 SHA256 校验文本实际 GET 字节一致。大型二进制未重复全量下载，已核对官方远端 SHA256 digest。用户 data、断点和 output 均未提交或清理。

- 发布记录收尾提交 `426f1063fd2849f81f4e2fbc913d1eddddef0502` 已同步 main/master，v0.11.1 标签保持源码提交 `933d778`。前一轮按用户要求更新原进度档及 Codex/Claude 共用本地记忆；随后用户要求工作区干净，本轮提交进度文档和忽略规则、不推送或重新发布。
- 根 data 属本机运行数据，自动初始化已在隔离目录验证；用户要求工作区干净后已取消两文件跟踪、忽略 `/data/` 与 `/output/`。本机数据及 wal/shm 文件原样保留，详细核对见原功能分析档“干净工作区与运行数据取消跟踪”。

## 上下文摘要

**当前 v0.11.1 已发布**：配置原子保存、在途探测退出保护、后台任务操作和仅侧栏 13 逻辑像素；原 dist、安装包、便携 ZIP、main/master 和 annotated 标签均完成，扩展保持 0.6.2。源码提交 933d778、Release 420434179，五附件 size/digest 一致，latest 正确，历史 17 个 Release 保留；详见上方 v0.11.1 节。v0.11.0 的 16/15 磅仅属历史产物。

发布记录提交 `97690a3` 已在上轮同步 main/master，v0.11.0 标签仍指向源码发布提交 `90e39cc`。2026-10-22 按用户要求补齐项目原进度档和 Codex、Pi/Claude 共用本地记忆：长期记录保存功能边界、当前字号与接续入口，测试/提交/哈希细节保留本档与原功能分析档；本次不另做 Git 提交或发布。

历史 v0.7.3：设置页新增可配置临时文件目录和「下载 / 其他」标签；337 项 Python 全量、137 项 GUI、EXE 与安装验收通过。该版本已于 2026-10-20 发布，原记录与附件保留；之后 v0.8.0 至 v0.10.0 的结果参见各自发布日志及功能进度档。

### v0.11.0 正式构建（2026-10-21）

- 先 `scripts/build_exe.py` 重建双 EXE，再 `scripts/build_installer.py v0.11.0`，保留原 dist/data 与 dist/temp；未在 EXE 后再次重建导致安装包被清掉。
- 安装包 155,797,617 字节，SHA256 `8deb8b2e3766b87a382d0916d3d0e318b0bec940c1fa406fd282632cf7b59410`，28 项明确白名单载荷。
- 安装验收 `output/installer-smoke/run-erbxz4e3/result.json`，七组通过，含原浏览器注册恢复。正式 EXE 三组与新功能实测通过，当前包内侧栏确认为 16/15 磅。
- 发布前最终 `py310_env` 全量 537 项 / 714.945 秒通过，扩展 43 项通过；功能和源码保护边界见原功能分析档的 v0.11.0 节。
- 便携 ZIP 213,909,114 字节 / `f3507f8786a2b2c87b9741d2b1f21e1c4a4ce9e2cf8d51d5df7759468a4a7bda`，扩展 ZIP 30,514 字节 / `ee2c04026aa9dba9026ade7137b62a50262b342ba7c3cebeb36b4931cf11fc1d`；CRC、白名单及逐项字节通过。用户四文件与 28 个源码指纹一致。
- Release id 419694874，2026-10-21 23:03:10 +08:00 发布；五附件逐一 size/digest 匹配，公开 CDN 的状态与长度、SHA 文本字节一致。直连 github.com 受本机网络影响，使用官方附件 API 跳转完成等效检查，未改系统代理/TLS。实际 Git 和上传证据见原功能分析档 v0.11.0 节及 `output/releases/v0.11.0/verification-remote.json`。

### v0.7.0 正式构建（2026-10-14）

- 安装包：`dist/daw-downloader-v0.7.0-windows-x64-setup.exe`，155,772,595 字节，SHA256 `25d86688a6d27dad694e746d625106916a5c20831bda9b45f42b92f23ab613ff`。
- 安装包载荷为 25 项发布文件及 2 项许可证/说明，安装向导、自选中文空格路径、程序占用拦截、升级/卸载/重装的数据保留均验证通过。
- 安装后的 BrowserBridge 和 EXE 同时通过视频与带 Cookie 的签名普通文件下载；文件字节、重复请求、单实例和正常退出检查通过。
- `output/installer-smoke/run-_4eq7x45/result.json` 为本次安装报告。原浏览器注册恢复，原 `dist` 用户文件哈希未变。
- 便携 ZIP：213,856,339 字节，SHA256 `2c0169f6949dc0ce5c81b9d0c4498c07f7940c10a63761e260684662e512212c`；扩展 ZIP：26,346 字节，SHA256 `1787193ae487755e2fa342c234b32387fb181035e6f5c793f6cf6b5fb723138a`。ZIP CRC 与载荷字节核对通过。
- 构建器及安装验收默认版本已同步为 v0.7.0；文档和下载链接同步，完整变更见 `docs/releases/v0.7.0.md`。
- 发布提交 `6925ecc`，`master`/`main` 与 annotated 标签 v0.7.0 均已推送。Release ID `412655534`，2026-10-14 19:48:12 +08:00 发布；五附件 digest 匹配，历史 9 个 Release 的发布时间和附件元数据未变。
- 用户已确认本轮全部完成；收尾复核远端 `master`/`main` 为发布记录提交 `768aa28`，标签仍指向源码提交 `6925ecc`，本专项暂无剩余发布待办。
- Codex 与 Claude Code 本地记忆的安装发布约定已同步：本仓库取 Git 凭据时明确 `username=dwdw530`，推送用临时 `credential.username` 配置；令牌仅经内存与 curl 标准输入使用。正文、索引及 Claude 入口已验证，详细关联见原功能分析档。

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
- 两库安装包约定和 v0.11.1 交付接续均指向本档；新增同名运行 data 边界索引，已纠正仅源码未打包/现行16至15磅等过期摘要，Claude 入口同步。具体提交、哈希和临时验证输出只保留在进度/说明文档中。

## 下一步行动

本轮暂无剩余实施任务。后续修改安装器时从本档和 `docs/INSTALLER.md` 接续，先核对当前源码、发布附件及用户数据，再按本次具体需求构建和验收。

下一次发布的起点：核对 `dist/data` 与当前双 EXE 哈希 → 跑 `python -B -m unittest discover -s tests` → 需要重建时 `scripts/build_exe.py` → `scripts/build_installer.py <新版本>` 与 `scripts/package_release.py <新版本>` → 更新 `docs/releases/<新版本>.md` 与相关链接 → 提交推送并另开标签，保留原 Release。

## v0.4.0 发布（2026-10-10）

- 更新内容：任务卡片信息（已下载/总大小、剩余时间）、多行批量添加与去重、添加对话框「开始下载/稍后下载」、任务列表搜索与「隐藏已完成」、下载完成后动作（打开文件/文件夹/全部完成后关机）、主窗口启动居中。详见 `docs/releases/v0.4.0.md` 与 `docs/context-sessions/20261010-2025-IDM界面借鉴四项改进.md`。
- 发布标识：`v0.4.0`，annotated 标签指向 `2274440789437685aa9f61fc7b5002d92d27d81a`（源码提交 `2274440`）；`v0.3.1`（`45701e4`）与 `v0.3.0` 标签、Release 及附件均未改动。
- 安装包：`dist/daw-downloader-v0.4.0-windows-x64-setup.exe`，155,717,565 字节（148.50 MiB），FileVersion `0.4.0.0`，SHA256 `28227c9fe60f96fef763605e5ab6c864bf8576ec6544e613d194c1d1d02e36b2`。
- 便携包：`daw-downloader-v0.4.0-windows-x64.zip`，213,809,906 字节，SHA256 `31fecabad47cf537371495e55ee7d2d7a1807715d44d901bdb899b0806e5f8e3`；扩展包 `daw-browser-extension-v0.5.0.zip`，17,768 字节，SHA256 `b0bdbb7bf475905711b8a77370fa381102c5d7e062cbe96aae025f18f168682a`；本地产物在 `output/releases/v0.4.0/`（不提交 Git）。
- 构建：`build_exe.py` 重建双 EXE → `build_installer.py v0.4.0`（仅封装已验证 dist，未 `--rebuild`）→ `package_release.py v0.4.0`。构建前确认无运行中的下载器进程，`dist/data` 与 `dist/temp` 保留。
- 验收：`smoke_exe.py`、`smoke_browser_bridge.py`、`smoke_installer.py`（6 组）、打包版窗口居中物理坐标测量、Python 全量 130 项与扩展 JS 14 项全部通过，细节见本次进度档第 6 节。
- 发布方式：GitHub REST API（本机无 `gh` CLI，使用 Windows 凭据管理器中仓库所有者账号的已存凭据，令牌仅在内存中使用、用完即删）。Release `v0.4.0` 已创建并上传 5 个附件：安装包、安装包 `.sha256`、便携 ZIP、扩展 ZIP、`SHA256SUMS.txt`。
- 发布后核对：GitHub API 返回的 5 个附件大小与本地一致、`digest`（sha256）与本地逐项一致；远端 `.sha256` 与本地逐字节一致；`latest` 指向 `v0.4.0`；`v0.3.1`、`v0.3.0` 发布日期与附件数量未变。
- 发布页：`https://github.com/dwdw530/downLoad/releases/tag/v0.4.0`。README 与 `docs/INSTALLER.md` 的下载链接已更新到 `v0.4.0`（提交 `2274440`）。

## v0.5.0 发布（2026-10-11）

- 更新内容：双击任务卡片查看实时速度曲线详情窗、剪贴板监视（含设置项与忽略站点）；顺带修复设置对话框快速保存关闭时的 `bad window path name`。详见 `docs/releases/v0.5.0.md` 与 IDM 档第 7 节。
- 发布标识：`v0.5.0`，annotated 标签指向 `e8f9ddb`；发布记录提交 `1a719f8`；`v0.4.0` 及更早版本的标签、Release 与附件均未改动。
- 安装包：`dist/daw-downloader-v0.5.0-windows-x64-setup.exe`，155,670,061 字节（148.46 MiB），FileVersion `0.5.0.0`，SHA256 `d457875472a84e91304b41600ee4f86b8ca14e442ba93d718c4074c193a80b43`。
- 便携包：`daw-downloader-v0.5.0-windows-x64.zip`，213,825,580 字节；扩展包 `daw-browser-extension-v0.5.0.zip`，17,768 字节（与 v0.4.0 相同）。两者 SHA256 见该版本 Release 的 `SHA256SUMS.txt`（本地产物目录已清理）。
- 验收：`smoke_exe.py`、`smoke_browser_bridge.py`、`smoke_installer.py`（6 组）全过；Python 全量 232 项通过。
- 发布方式与核对：GitHub REST API + curl（Bearer token），5 个附件上传后逐项核对远端大小与 `digest`；`latest` 指向 `v0.5.0`。本次曾只推 `master` 而漏掉默认分支 `main`，已用 `git push origin master:main` 补齐；**之后每次推送两个分支都要更新**。

## v0.6.1 构建与发布准备（2026-10-11）

- 安装包：`dist/daw-downloader-v0.6.1-windows-x64-setup.exe`，155,717,801 字节，SHA256 `23a902762c255bf3bbf2a00ad386f7591732adef451c7e5b14e8e7062eb2954f`。
- 主程序：21,670,267 字节 / `abc5198164b697832695f38ba2a3ac4ececb11818cb8106b33997fe9c5d14d1f`；桥接：9,982,279 字节 / `4be555a447c2dce629914f29679e1612a9fea9e94dc9f43a92ce548f466696d2`。
- 便携 ZIP：213,848,008 字节 / `bdacd3f5415371d7a82d3e72d8fc421c9728892e741c91ca33f121887a7783d5`；扩展 ZIP：17,768 字节 / `b0bdbb7bf475905711b8a77370fa381102c5d7e062cbe96aae025f18f168682a`，仍为 0.5.0。
- 实际安装验收报告 `output/installer-smoke/run-jo4xr9_v/result.json` 六组 PASS，结束后浏览器注册恢复、原 dist 用户数据哈希不变；便携 ZIP 白名单与 CRC 验证通过。
- GitHub 发布前检查：远端 main/master 均为 `18d7ea7`、latest 为 v0.6.0，无 v0.6.1 标签/Release；通过仓库所有者凭据确认 push 权限。令牌只在内存与 curl 标准输入中使用，不写文件或仓库。
- 待办：提交源码与日志、推送 main/master、新建 annotated v0.6.1 标签和 Release、上传五附件并核对远端 digest；随后在此补充实际提交及发布结果。

### 发布结果（2026-10-11）

- 发布提交 `ee28ca65495c96f8c476bfb9199757aaaade34c6`（`ee28ca6`）已推送 `master` 与 `master:main`，远端两分支同指向；annotated 标签 `v0.6.1`（tag 对象 `60d7b9ad8efa23916b3fb78a9ab1363f72ebb08d`）指向该提交。
- Release id `409657977`，非 draft、非 prerelease，`latest` 已指向 v0.6.1；发布页 `https://github.com/dwdw530/downLoad/releases/tag/v0.6.1`，正文为 `docs/releases/v0.6.1.md`。
- 远端附件与本地逐项一致：安装包 155,717,801 B / `23a90276…62954f`、安装包 `.sha256` 111 B / `fcaf7a45…f684db`、便携 ZIP 213,848,008 B / `bdacd3f5…a7783d5`、扩展 ZIP 17,768 B / `b0bdbb7b…f168682a`、`SHA256SUMS.txt` 205 B / `3057e60b…3af14ee`。
- 发布前已核对远端 main/master 为 `18d7ea7`、latest 为 v0.6.0，且不存在 v0.6.1 标签或 Release；比对发布前 7 个历史 Release 的 id、标签、发布时间与附件清单，发布后未变。
- 发布方式：`git credential fill` 取凭据管理器中仓库所有者账号令牌，经进程内变量与 curl 标准输入使用；未写入文件或仓库，临时发布脚本与响应体已删除。

## v0.7.3 发布（2026-10-20）

- 版本内容：设置页新增“临时文件目录”，可指向 U 盘/移动硬盘，保存后立即生效（新任务直接使用，新目录会建目录并做写探针；无断点数据的排队任务同步改分块路径，已有断点/进行中的任务保持原目录）；设置页改为「下载 / 其他」标签页，窗口 520×800 → 560×520。扩展保持 0.6.2 未改动。
- 构建：`conda run --no-capture-output -n py310_env python -B scripts/build_installer.py --rebuild v0.7.3`，随后 `scripts/package_release.py v0.7.3`。构建前后核对 `dist/data`、`dist/temp` 五个文件哈希一致（含用户 `config.json`、`downloads.db` 与一条 HLS 断点）。
- 安装包：`dist/daw-downloader-v0.7.3-windows-x64-setup.exe`，155,765,325 字节，SHA256 `6ad2a74bf2f8ceec8041543ef2af2459b38f47272971deb67649a4f71d6895e8`；校验文件 111 字节 / `64e8f2f6db5fe1c3e8745733a36a4edcee6121a44c94d347a40566efe5147228`。
- 便携包：`daw-downloader-v0.7.3-windows-x64.zip` 213,866,498 字节 / `5cc2d5b99ad61195630279629a259ecdd3c2c0b9e75971007a8650beef96cf3a`；扩展包 `daw-browser-extension-v0.6.2.zip` 30,514 字节 / `ee2c04026aa9dba9026ade7137b62a50262b342ba7c3cebeb36b4931cf11fc1d`；`SHA256SUMS.txt` 205 字节 / `6625550bfe5179038b871ac13bb3b680fff7636892738e42f4b8857263ca81f1`。
- 发布提交 `b0b3757`，`master` 与 `master:main` 已原子推送至同提交；annotated 标签 `v0.7.3`（tag 对象 `70dd38f`）指向该提交。
- Release id `418506719`，非 draft、非 prerelease，`latest` 指向 v0.7.3；发布页 `https://github.com/dwdw530/downLoad/releases/tag/v0.7.3`，正文与 `docs/releases/v0.7.3.md` 一致。五附件上传后经 API 核对，大小与 `digest` 与本地一致；11 个历史 Release 的 id、发布时间与附件未变。
- 验收：`py310_env` 全量 337 项、GUI 137 项通过；`smoke_exe.py` 3 组、`smoke_installer.py` 全部通过（中文向导、含空格自定义路径、占用时阻止覆盖、升级保留数据、卸载清理、重装与浏览器注册恢复）。
- 发布方式：同前，`git credential fill` 指定 `dwdw530` 取凭据管理器令牌，令牌仅经 `curl --config -` 标准输入使用；API 未认证读取触发速率限制后改用带令牌请求完成核对，临时响应体均在 `output/` 且未入库。

## v0.7.2 发布（2026-10-18）

- 版本内容：修复小红书网页视频无法下载（真实页面 `EF4/EF5` 档位键、`videoCodec` 存档位名、`masterUrl` 为 `http`、竖屏分辨率），扩展版本升至 0.6.2。`v0.7.1` 与 `extension-v0.6.1` **从未发布**（远端标签只到 `v0.7.0`），其内容并入本版。
- 构建：`conda run -n py310_env python -B scripts/build_installer.py --rebuild v0.7.2`。构建前后核对 `dist/data`、`dist/temp` 四个文件哈希未变（含用户 `config.json`、`downloads.db` 与两条断点）。
- 安装包：`dist/daw-downloader-v0.7.2-windows-x64-setup.exe`，155,741,923 字节，SHA256 `3de13850ed6033de8dff6e148a65da0fe8df67b2cc27645144ec77acdd00f327`。
- 便携包：`daw-downloader-v0.7.2-windows-x64.zip` 213,863,327 字节 / `6680e34bb2f7474132f5c584154d115689afe5a2f82bd604d1e9422f5259f585`；扩展包 `daw-browser-extension-v0.6.2.zip` 30,514 字节 / `ee2c04026aa9dba9026ade7137b62a50262b342ba7c3cebeb36b4931cf11fc1d`。
- 发布提交 `5f8a3a5`，记录提交 `e8e7f47`；`master` 与 `master:main` 同指向 `e8e7f47`（原子推送）。annotated 标签 `v0.7.2`（tag 对象 `22fa0c8`）指向发布提交 `5f8a3a5`。
- Release id `416188131`，非 draft、非 prerelease，`latest` 指向 v0.7.2；发布页 `https://github.com/dwdw530/downLoad/releases/tag/v0.7.2`，正文为 `docs/releases/v0.7.2.md` 加“v0.7.1 未单独发布”说明。五附件上传后经 API 核对，大小与 `digest` 与本地一致；`v0.7.0`、`v0.6.1`、`extension-v0.5.2` 的附件数与 digest 未变。
- 验收：`smoke_exe.py` 3 组、`smoke_installer.py` 6 组通过（中文向导、带空格自定义路径、升级保留数据、在用时阻止覆盖、卸载清理与浏览器注册恢复）。未执行 `scripts/smoke_xhs.cjs` 自动化实网脚本，由用户手工登录态实网确认代替。

## v0.6.0 发布（2026-10-11）

- 更新内容：左侧分类树（状态/文件类型/站点）、任务右键菜单与手动排序（落库 `list_order`，影响 pending 出队顺序）、主题切换（深色/浅色/跟随系统）；分类树改原生 `tkinter.Label` 并修复切主题后侧栏配色不同步。详见 `docs/releases/v0.6.0.md` 与 IDM 档第 8 节。
- 发布标识：`v0.6.0`，annotated 标签（tag 对象 `1352a70`）指向发布提交 `ec0628b`；发布记录提交 `18d7ea7`；`master` 与 `main` 均已推送；`v0.5.0` 及更早版本未改动。
- 安装包：`dist/daw-downloader-v0.6.0-windows-x64-setup.exe`，155,713,090 字节（148.50 MiB），FileVersion `0.6.0.0`，SHA256 `6c5d2dbe23562288986e16453e6f44b781194cb1f3856bc62e55c5602219caae`。
- 便携包：`daw-downloader-v0.6.0-windows-x64.zip`，213,834,880 字节，SHA256 `c681162b378b293bc856bac614fd7f132fece1f253687d160d85f71c4cbe9f16`；扩展包 `daw-browser-extension-v0.5.0.zip`，17,768 字节，SHA256 `b0bdbb7bf475905711b8a77370fa381102c5d7e062cbe96aae025f18f168682a`（未改动）。
- 载荷：`daw下载器.exe` SHA256 `69a1f59e6fc91cff0718a524f622dc183c5f623e378c055dd7ce6cac40236a6a`，`BrowserBridge.exe` SHA256 `097104083381a4427dd4a0b249904848f3db481c12b934480c2da5328f38468e`。
- 验收：`smoke_exe.py` 3 组、`smoke_installer.py` 6 组全过（测试后原浏览器注册还原、`dist/data` 未变）；Python 全量 268 项（1 项既有偶发超时，单独重跑通过）。
- 发布方式：改用 `git credential fill` 取凭据管理器中 `github.com`（账号 `dwdw530`）的令牌，curl 直连 API 创建 Release（id `409503116`）并上传 5 个附件；**踩坑**：`/tmp` 在 Windows Python 与 MSYS curl 之间解析不一致（首次 422），临时文件改放项目 `output/`，发布后已删除且未入库。
- 发布后核对：5 个附件大小与 GitHub `digest` 与本地逐项一致；`latest` 指向 `v0.6.0`；`v0.5.0` 至 `0.1` 的发布时间与附件数未变。发布页：`https://github.com/dwdw530/downLoad/releases/tag/v0.6.0`。README、`docs/INSTALLER.md`、`BUILD_EXE.md`、`PROJECT_SUMMARY.md` 的版本引用与下载链接已同步到 `v0.6.0`。
