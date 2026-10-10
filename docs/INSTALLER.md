# Windows 安装包

安装包提供中文向导、安装路径选择、开始菜单入口、可选桌面快捷方式，以及 Windows 系统卸载入口。包含主程序、BrowserBridge、浏览器扩展、视频组件和第三方许可证，安装和运行不需要 Python。

[下载安装包](https://github.com/dwdw530/downLoad/releases/download/v0.5.0/daw-downloader-v0.5.0-windows-x64-setup.exe) · [SHA256 校验文件](https://github.com/dwdw530/downLoad/releases/download/v0.5.0/daw-downloader-v0.5.0-windows-x64-setup.exe.sha256) · [发布页](https://github.com/dwdw530/downLoad/releases/tag/v0.5.0)

## 安装、升级和卸载

1. 双击 `dist/daw-downloader-v0.5.0-windows-x64-setup.exe`。
2. 选择组件和安装目录。默认安装到 `%LOCALAPPDATA%\Programs\dawDownloader`，仅安装给当前 Windows 用户，无需管理员权限。自选目录必须对当前用户可写。
3. 从开始菜单或桌面快捷方式启动。勾选浏览器连接时会将当前用户的桥接指向此目录；仍需在 Chrome / Edge 的扩展管理页面手动加载安装目录内的 `chrome-extension`，详见安装后的 `使用说明.txt`。
4. 升级前正常退出下载器及托盘进程，再运行新版安装包，沿用原安装目录。安装程序只覆盖发布文件，不打包或覆盖现有配置、数据库、下载文件和断点。
5. 在 Windows“设置 → 应用 → 已安装的应用”中找到 `daw下载器` 并卸载，或使用开始菜单中的卸载入口、安装目录的 `Uninstall.exe`。

卸载保留 `data/`、`temp/`、下载文件和用户自行放入的文件。只删除明确列出的程序文件，不递归删除安装目录。旧位置的卸载程序不会移除已指向其他位置的浏览器连接。保留下来的数据可在同一目录重新安装后继续使用。

浏览器内手动加载的扩展，如不再使用，可在 Chrome / Edge 的扩展管理页面移除。

从便携版迁移时，正常退出后可直接将安装位置设为原便携版目录，复用已有数据。便携版仍从 `dist/daw下载器.exe` 启动。安装包不会自动搬迁其他目录的数据，也不会自动在浏览器内安装或重新加载扩展。

## 构建

完整重建主程序、桥接及安装包：

```powershell
conda run --no-capture-output -n py310_env python -B scripts/build_installer.py v0.4.0 --rebuild
```

也可双击项目根目录的 `build_installer.bat`。构建前应准备好 `vendor/video`（沿用 `scripts/setup_video_tools.py`），并退出正在使用原 `dist` 的下载器。

仅将已验证的当前 `dist` 制作成安装包：

```powershell
conda run --no-capture-output -n py310_env python -B scripts/build_installer.py v0.4.0
```

输出为 `dist/daw-downloader-v0.4.0-windows-x64-setup.exe` 和相邻的 `.exe.sha256` 校验文件。安装包沿用发布版号，软件原有窗口版号不由安装器修改。

构建器复用 `scripts/package_release.py` 的明确文件清单，检查组件存在、非空、位于发布目录内，并核对扩展与源码一致。用户数据库、配置、下载内容和本机桥接清单不属于发布清单。

如果 PATH 中没有 `makensis`，首次构建会从 [NSIS 官方 SourceForge](https://sourceforge.net/projects/nsis/files/NSIS%203/3.13/) 下载固定的 NSIS 3.13 便携工具到 `build/installer/tools`，验证 SHA256 后解压；不会全局安装编译器或改系统 PATH。可用 `--makensis C:\path\to\makensis.exe` 指定已有编译器。

固定工具 ZIP 的 SHA256：`ba63dffc4410ee89193e1cb5a41989991bd77c61068da17e3156d136b7b0b3d8`。2026-10-08 核对了官方下载内容和 [Scoop Extras 的 NSIS 清单](https://github.com/ScoopInstaller/Extras/blob/master/bucket/nsis.json) 中的归档校验值，构建脚本固定使用上述 SHA256。

## 验证

在尚未安装本安装版的 Windows 测试用户下运行：

```powershell
conda run --no-capture-output -n py310_env python -B scripts/smoke_installer.py
```

脚本操作真实中文安装向导，选择包含中文和空格的隔离路径，验证文件、快捷方式、注册表卸载入口和浏览器连接。随后直接通过安装后的 BrowserBridge 冷启动安装后的 EXE，复用现有带测试授权信息的视频下载、字节一致性、去重、单实例及正常退出检查；再验证文件占用拦截、覆盖升级、卸载保留数据、重装及浏览器注册归属。已有安装版或同名快捷方式时拒绝测试，结束后恢复原浏览器注册。

验收输出保存在 `output/installer-smoke/`，包含安装路径页截图、完成页截图、应用测试日志和 `result.json`。应用验收在一次性安装目录内使用 `smoke_browser_bridge.py --app-dir`；该参数遇到已有 `data` 目录会拒绝运行，不能用于已有用户数据的目录。普通 EXE 的鼠标操作回归仍使用 `scripts/smoke_exe.py`，需要未锁定的交互桌面。

静默安装支持 `/S`、`/NODESKTOP`、`/NOBRIDGE`；自选路径使用最后一个参数 `/D=完整路径`。静默卸载使用 `Uninstall.exe /S`，同样保留用户数据。

## 2026-10-08 实际验收

- 安装包：`dist/daw-downloader-v0.3.0-windows-x64-setup.exe`，155,650,713 字节（148.44 MiB），文件版本 `0.3.0.0`。
- SHA256：`aaa6ef2d89a3ed15e5b3eeeecd2711c0efc96c5c1502fd7e2604e545129b1f88`。
- 使用当前已验证的正式 `dist` 制作安装包，主程序、桥接、扩展和视频组件不变；25 项发布文件在实际安装后逐一验证哈希一致，没有携带用户数据。
- 真实中文向导可选择包含中文和空格的路径；桌面、开始菜单、系统卸载注册项和浏览器连接通过。锁定主程序文件时，升级与卸载都返回错误码 2，并保留文件及注册项。
- 安装目录中的 BrowserBridge 实际冷启动该目录中的 EXE，经本地 HTTP 服务完成带测试授权信息的视频下载，校验完整字节、去重、单实例与正常退出。未重复执行未改动站点的实网下载。
- 同目录覆盖升级、卸载、重装通过。配置、数据库、断点、下载文件和用户自行创建的文件哈希保持；卸载只删除程序文件，保留指向其他安装位置的桥接注册，移除属于自身的注册。
- `python -B -m unittest discover -s tests -p test_browser_integration.py`：12 项通过。`git diff --check` 通过。
- 产物和报告：`output/installer-smoke/run-45uilqcb/result.json`；应用日志：同目录 `installed-app-smoke.log`。原 `dist/data` 未改变，测试创建的安装注册项和快捷方式已卸载，原浏览器注册已恢复。

首轮鼠标操作验收被 Windows 锁屏的 `LockScreenBackstopFrame` 拦截，没有记为通过。后续通过本机桥接完成了安装后程序的真实下载和退出验证；安装向导使用原生控件消息完成操作。锁屏下截图为空白或不完整，未作为视觉验收证据。

## 2026-10-10 v0.3.1 构建

- 安装包：`dist/daw-downloader-v0.3.1-windows-x64-setup.exe`，155,645,269 字节（148.44 MiB），文件版本 `0.3.1.0`。
- SHA256：`a1a137a5409c38f34f0247396db2a3d477028c72d1e1c1e8e281a3d75a857bfa`。
- 载荷为本次重新打包并实测的 `dist` 双 EXE（新增文件日志、分块进度节流、WAL 与视频失败诊断）；安装脚本、向导与卸载逻辑未改动。
- 用 `7z` 核对安装包内容：25 项发布文件与发布清单一致，未包含 `data`、`temp`、`logs` 等运行数据。
- 本次未重复执行完整安装向导验收；载荷中的双 EXE 已在独立目录实测启动与日志写入、WAL 生效、正常退出，并通过 111 项 Python 回归。

## 2026-10-11 v0.5.0 构建

- 安装包：`dist/daw-downloader-v0.5.0-windows-x64-setup.exe`，155,670,061 字节（148.46 MiB），文件版本 `0.5.0.0`。
- SHA256：`d457875472a84e91304b41600ee4f86b8ca14e442ba93d718c4074c193a80b43`，与相邻 `.exe.sha256` 一致。
- 载荷为本次重新打包并实测的 `dist` 双 EXE（速度曲线详情窗、剪贴板监视、设置对话框快速关闭修复）；`daw下载器.exe` SHA256 `a511e0f2da80d9fa1db10cb45a0e7ba7c192a0fa4dda8060f815ecc01132436c`，`BrowserBridge.exe` SHA256 `f69255b91567681b8a88bce7d0b27a053c385329f9332deb37ace56dbd398d50`。
- 本次执行了完整安装验收 `scripts/smoke_installer.py --installer dist/daw-downloader-v0.5.0-windows-x64-setup.exe`：中文向导、含空格自定义路径、白名单载荷哈希、快捷方式、卸载项与浏览器注册、占用时拒绝升级/卸载、同目录升级保留配置与断点、卸载保留他方注册、重装保留数据共 6 组 PASS；结束后原浏览器注册已还原、`dist/data` 哈希未变。

## 2026-10-10 v0.4.0 构建

- 安装包：`dist/daw-downloader-v0.4.0-windows-x64-setup.exe`，155,717,565 字节（148.50 MiB），文件版本 `0.4.0.0`。
- SHA256：`28227c9fe60f96fef763605e5ab6c864bf8576ec6544e613d194c1d1d02e36b2`，与相邻 `.exe.sha256` 一致。
- 载荷为本次重新打包并实测的 `dist` 双 EXE（任务卡片信息、批量添加、稍后下载、列表筛选、下载完成后动作、窗口居中）。
- 本次执行了完整安装验收 `scripts/smoke_installer.py --installer dist/daw-downloader-v0.4.0-windows-x64-setup.exe`：中文向导、含空格自定义路径、白名单载荷哈希、快捷方式、卸载项与浏览器注册、占用时拒绝升级/卸载、同目录升级保留配置与断点、卸载保留他方注册、重装保留数据共 6 组 PASS；结束后原浏览器注册已还原、`dist/data` 哈希未变。
- 安装后应用验收需要 `output/playwright/sample.webm`（本轮本机缺失，已用随包 ffmpeg 生成 2 秒 VP9 样本补齐，该目录在 `.gitignore` 内）。

## 发布到 GitHub

安装包与相邻的 `.exe.sha256` 文件作为 GitHub Release 附件上传。构建脚本、安装脚本和说明文档提交到源码仓库；编译工具、测试产物和用户数据不提交。

发布前按上述方法完成构建和验收，再进入 [GitHub Releases](https://github.com/dwdw530/downLoad/releases) 创建或编辑对应版本，上传这两个文件并保存。可在 PowerShell 中运行 `Get-FileHash -Algorithm SHA256 -LiteralPath .\dist\daw-downloader-v0.4.0-windows-x64-setup.exe` 核对本地校验值，并核对远端附件的大小和校验值。

2026-10-09 已在既有 `v0.3.0` 下补充安装包及其独立校验文件，GitHub 返回的大小和 SHA256 与本地一致。既有便携版 ZIP、扩展 ZIP 和 `SHA256SUMS.txt` 保持原样，后者仍校验原有两个 ZIP。安装器构建脚本及完整操作说明从仓库 `main` 分支获取，原 `v0.3.0` 标签保持不变。
