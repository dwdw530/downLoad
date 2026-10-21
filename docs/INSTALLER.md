# Windows 安装包

安装包提供中文向导、安装路径选择、开始菜单入口、可选桌面快捷方式，以及 Windows 系统卸载入口。包含主程序、BrowserBridge、浏览器扩展、视频组件和第三方许可证，安装和运行不需要 Python。

[下载安装包](https://github.com/dwdw530/downLoad/releases/download/v0.9.0/daw-downloader-v0.9.0-windows-x64-setup.exe) · [SHA256 校验文件](https://github.com/dwdw530/downLoad/releases/download/v0.9.0/daw-downloader-v0.9.0-windows-x64-setup.exe.sha256) · [发布页](https://github.com/dwdw530/downLoad/releases/tag/v0.9.0)

本版为下载健壮性与全局限速：添加任务改后台探测并给出具体失败原因、文件名优先取服务器给的名字、下载前与合并前检查磁盘空间、限速改为所有任务合计的总量上限、新增「重试失败」按钮、状态栏改数据库聚合、启动清理残留临时文件；悬浮条改为真逐像素透明。浏览器扩展保持 **0.6.2** 未改动：[独立插件 ZIP](https://github.com/dwdw530/downLoad/releases/download/v0.9.0/daw-browser-extension-v0.6.2.zip) · [更新日志](releases/v0.9.0.md)。升级桌面端后重启下载器即可，无需重新加载扩展。

## 安装、升级和卸载

1. 双击 `dist/daw-downloader-v0.9.0-windows-x64-setup.exe`。
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
conda run --no-capture-output -n py310_env python -B scripts/build_installer.py v0.9.0 --rebuild
```

也可双击项目根目录的 `build_installer.bat`。构建前应准备好 `vendor/video`（沿用 `scripts/setup_video_tools.py`），并退出正在使用原 `dist` 的下载器。

仅将已验证的当前 `dist` 制作成安装包：

```powershell
conda run --no-capture-output -n py310_env python -B scripts/build_installer.py v0.9.0
```

输出为 `dist/daw-downloader-v0.9.0-windows-x64-setup.exe` 和相邻的 `.exe.sha256` 校验文件。`build_installer.bat` 的默认版本同步为 v0.9.0；安装包沿用发布版号，软件原有窗口版号不由安装器修改。

构建器复用 `scripts/package_release.py` 的明确文件清单，检查组件存在、非空、位于发布目录内，并核对扩展与源码一致。用户数据库、配置、下载内容和本机桥接清单不属于发布清单。

如果 PATH 中没有 `makensis`，首次构建会从 [NSIS 官方 SourceForge](https://sourceforge.net/projects/nsis/files/NSIS%203/3.13/) 下载固定的 NSIS 3.13 便携工具到 `build/installer/tools`，验证 SHA256 后解压；不会全局安装编译器或改系统 PATH。可用 `--makensis C:\path\to\makensis.exe` 指定已有编译器。

固定工具 ZIP 的 SHA256：`ba63dffc4410ee89193e1cb5a41989991bd77c61068da17e3156d136b7b0b3d8`。2026-10-08 核对了官方下载内容和 [Scoop Extras 的 NSIS 清单](https://github.com/ScoopInstaller/Extras/blob/master/bucket/nsis.json) 中的归档校验值，构建脚本固定使用上述 SHA256。

## 验证

在尚未安装本安装版的 Windows 测试用户下运行：

```powershell
conda run --no-capture-output -n py310_env python -B scripts/smoke_installer.py --installer dist/daw-downloader-v0.9.0-windows-x64-setup.exe
```

脚本操作真实中文安装向导，选择包含中文和空格的隔离路径，验证文件、快捷方式、注册表卸载入口和浏览器连接。随后直接通过安装后的 BrowserBridge 冷启动安装后的 EXE，复用现有带测试授权信息的视频下载、字节一致性、去重、单实例及正常退出检查；再验证文件占用拦截、覆盖升级、卸载保留数据、重装及浏览器注册归属。已有安装版或同名快捷方式时拒绝测试，结束后恢复原浏览器注册。

验收输出保存在 `output/installer-smoke/`，包含安装路径页截图、完成页截图、应用测试日志和 `result.json`。应用验收在一次性安装目录内使用 `smoke_browser_bridge.py --app-dir`；该参数遇到已有 `data` 目录会拒绝运行，不能用于已有用户数据的目录。普通 EXE 的鼠标操作回归仍使用 `scripts/smoke_exe.py`，需要未锁定的交互桌面。

静默安装支持 `/S`、`/NODESKTOP`、`/NOBRIDGE`；自选路径使用最后一个参数 `/D=完整路径`。静默卸载使用 `Uninstall.exe /S`，同样保留用户数据。

## 2026-10-21 v0.9.0 构建与验收

- 桌面版 v0.9.0、扩展 0.6.2（未改动），下载健壮性与全局限速一批改动 + 悬浮条真逐像素透明，[更新日志](releases/v0.9.0.md)。
- 构建：`conda run --no-capture-output -n py310_env python -B scripts/build_exe.py`，随后 `python -B scripts/build_installer.py v0.9.0`（注意顺序：`build_exe.py` 会清理 `dist`，必须在重建 EXE 之后再打安装包）。
- 详情见下方发布记录。

## 2026-10-21 v0.8.1 构建与验收

- 桌面版 v0.8.1、扩展 0.6.2（未改动），界面细节调整：左侧分类树字号放大、保存设置改为状态栏提示、悬浮条新增迷你模式并整体收窄，[更新日志](releases/v0.8.1.md)。
- 构建：`conda run --no-capture-output -n py310_env python -B scripts/build_exe.py`，随后 `python -B scripts/build_installer.py v0.8.1`（注意顺序：`build_exe.py` 会清理 `dist`，必须在重建 EXE 之后再打安装包）。
- 安装包：`dist/daw-downloader-v0.8.1-windows-x64-setup.exe`，155,758,991 字节；SHA256 `d00b4869fcc8324bf759c5d5d02718ffd3b529839bb59d565a31f0b82522d170`。
- 载荷双 EXE：`daw下载器.exe` 21,692,842 字节 / `259ac0fa53b8a917950d6c11e8173360b81b010419534c8d7e9ffd5c7252af09`；`BrowserBridge.exe` 9,986,402 字节 / `908ebe00cd03e31ecd4cc443357fe1da6b909f1f3a65e68e7e1fdbe02bf7be47`。
- 便携包：`daw-downloader-v0.8.1-windows-x64.zip` 213,886,283 字节 / `5d2204acd628ba56d7eef6c114df42b2b6a00c1bb5c7ed56be72c8049367f182`（27 项载荷，CRC 通过）；扩展包 `daw-browser-extension-v0.6.2.zip` 30,514 字节 / `ee2c04026aa9dba9026ade7137b62a50262b342ba7c3cebeb36b4931cf11fc1d`（与 v0.8.0 相同）；`SHA256SUMS.txt` 205 字节。
- 验收：`py310_env` 全量 388 项（GUI 185 项）通过；`scripts/smoke_exe.py` 3 组通过；`scripts/smoke_installer.py` 七组 PASS（中文向导、含空格自定义路径、白名单载荷、快捷方式与卸载项、安装后真实下载与退出、占用时阻止覆盖、升级保留数据、卸载保留他方注册、重装保留数据、原浏览器注册恢复），报告 `output/installer-smoke/run-phjg2g1h/result.json`。
- 安装验收需要 `output/playwright/sample.webm`（gitignore 内），本轮用随包 ffmpeg 生成 2 秒 VP9 320×240 样本补齐。
- 发布：提交 `2189e88`（`master` 与 `master:main` 同指向），annotated 标签 `v0.8.1`，Release id `418986605`；五附件远端大小与 `digest` 与本地一致，`latest` 指向 v0.8.1。

## 2026-10-21 v0.8.0 构建与验收

- 桌面版 v0.8.0、扩展 0.6.2（未改动），新增桌面速度悬浮条（可拖动、置顶、默认关闭）与失败分块单独重试（临时性错误只重下失败分块并按指数退避），[更新日志](releases/v0.8.0.md)。
- 构建：`conda run --no-capture-output -n py310_env python -B scripts/build_installer.py v0.8.0 --rebuild`，随后 `python -B scripts/package_release.py v0.8.0`。
- 安装包：`dist/daw-downloader-v0.8.0-windows-x64-setup.exe`，155,735,310 字节；SHA256 `3c8f8073a6c942e3d0b45e7aca6e4ffbfcf9efe50c93460556279536cfbec73e`。
- 载荷双 EXE：`daw下载器.exe` 21,689,633 字节 / `1b2bfecc02b43ad496b2860ed6a6aa5545a2c756f3945852e803ebf36fc60e9b`；`BrowserBridge.exe` 9,985,459 字节 / `d5f8b40944b52f97362024d67d77ff1f5b35f28597685e1cb864aeb45eb2a2cb`。
- 便携包：`daw-downloader-v0.8.0-windows-x64.zip` 213,882,363 字节 / `088f3db7678a005f5207bfa72510e7699ae9f216a310e18bc1cb0c31c72b1e14`（27 项载荷，CRC 通过）；扩展包 `daw-browser-extension-v0.6.2.zip` 30,514 字节 / `ee2c04026aa9dba9026ade7137b62a50262b342ba7c3cebeb36b4931cf11fc1d`（未改动）；`SHA256SUMS.txt` 205 字节 / `60486d9398bae2381d25df049ea3a48d1001b37f6bf9ce3db4e8f4ae5a8f761b`。
- 验收：`py310_env` 全量 385 项（GUI 182 项）通过；`scripts/smoke_exe.py` 3 组通过；`scripts/smoke_installer.py` 七组 PASS（中文向导、含空格自定义路径、白名单载荷、快捷方式与卸载项、安装后真实下载与退出、占用时阻止覆盖、升级保留数据、卸载保留他方注册、重装保留数据、原浏览器注册恢复），报告 `output/installer-smoke/run-_lvj8t32/result.json`。
- 构建与安装验收前后核对 `dist/data`、`dist/temp` 哈希一致（用户 `config.json`、`downloads.db`），未改动配置与数据库。
- 发布：提交 `6f70aba`（`master` 与 `master:main` 同指向），annotated 标签 `v0.8.0`，Release id `418907040`；五附件远端大小与 `digest` 与本地一致，`latest` 指向 v0.8.0，12 个历史 Release 未改动。

## 2026-10-20 v0.7.3 构建与验收

- 桌面版 v0.7.3、扩展 0.6.2（未改动），设置页新增可配置的临时文件目录并改为「下载 / 其他」标签页；[更新日志](releases/v0.7.3.md)。
- 安装包：`dist/daw-downloader-v0.7.3-windows-x64-setup.exe`，155,765,325 字节；SHA256 `6ad2a74bf2f8ceec8041543ef2af2459b38f47272971deb67649a4f71d6895e8`。
- 便携包：`daw-downloader-v0.7.3-windows-x64.zip` 213,866,498 字节 / `5cc2d5b99ad61195630279629a259ecdd3c2c0b9e75971007a8650beef96cf3a`；扩展包 `daw-browser-extension-v0.6.2.zip` 30,514 字节 / `ee2c04026aa9dba9026ade7137b62a50262b342ba7c3cebeb36b4931cf11fc1d`（与 v0.7.2 相同）。
- `py310_env` 全量 337 项、GUI 137 项通过；`smoke_exe.py` 3 组、`smoke_installer.py` 全部通过。构建前后 `dist/data`、`dist/temp` 五个文件哈希一致（含用户配置、数据库和一条 HLS 断点）。
- 发布：提交 `b0b3757`，annotated 标签 `v0.7.3`（tag 对象 `70dd38f`），Release id `418506719`；五附件远端大小与 digest 与本地一致，latest 指向 v0.7.3，历史 Release 未改动。

## 2026-10-14 v0.7.0 构建与验收

- 桌面版 v0.7.0、扩展 0.6.0，新增普通文件链接手动右键下载；[更新日志](releases/v0.7.0.md)。
- 安装包：`dist/daw-downloader-v0.7.0-windows-x64-setup.exe`，155,772,595 字节；SHA256 `25d86688a6d27dad694e746d625106916a5c20831bda9b45f42b92f23ab613ff`。
- 正式双 EXE 重建，25 项发布载荷与 2 项安装许可证/说明组成安装包；源码和正式目录的 12 项扩展文件一致。
- 正式 EXE 三组冒烟通过，安装包六组验收通过；安装后的真实 BrowserBridge 完成视频及带 Cookie 的签名普通文件下载，校验文件内容并验证重复提交。
- 中文和空格安装目录、程序占用拦截、覆盖升级、卸载、重装和数据保留均通过；原浏览器注册恢复，正式 `dist` 用户数据哈希保持。
- 本次安装验收记录：`output/installer-smoke/run-_4eq7x45/result.json`；新增功能的完整测试范围见[链接下载测试报告](BROWSER_LINK_TEST_REPORT.md)。

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

## 2026-10-11 v0.6.1 构建

- 更新内容：9 项下载安全与交互修复、同卷无复制保存、目标目录直接合并；完整说明见 [v0.6.1 更新日志](releases/v0.6.1.md)。
- 安装包：`dist/daw-downloader-v0.6.1-windows-x64-setup.exe`，155,717,801 字节，SHA256 `23a902762c255bf3bbf2a00ad386f7591732adef451c7e5b14e8e7062eb2954f`。
- 主程序：21,670,267 字节 / `abc5198164b697832695f38ba2a3ac4ececb11818cb8106b33997fe9c5d14d1f`；BrowserBridge：9,982,279 字节 / `4be555a447c2dce629914f29679e1612a9fea9e94dc9f43a92ce548f466696d2`。
- `py310_env` 全量 308 项、扩展 14 项通过；正式双 EXE 冒烟通过，安装包 6 组验收通过，报告：`output/installer-smoke/run-jo4xr9_v/result.json`。
- 原 `dist/data` / `dist/temp` 与构建前基线哈希一致，安装验收结束后原浏览器注册已恢复。升级保留旧任务，但没有资源版本记录的旧断点会安全重下，不保证直接续接。

## 2026-10-11 v0.6.0 构建

- 安装包：`dist/daw-downloader-v0.6.0-windows-x64-setup.exe`，155,713,090 字节（148.50 MiB），文件版本 `0.6.0.0`。
- SHA256：`6c5d2dbe23562288986e16453e6f44b781194cb1f3856bc62e55c5602219caae`，与相邻 `.exe.sha256` 一致。
- 载荷为本次重新打包并实测的 `dist` 双 EXE（左侧分类树、任务右键菜单与手动排序、主题切换；分类树字号对齐正文并修复跟随系统/切主题后侧栏未同步配色的问题）；`daw下载器.exe` SHA256 `69a1f59e6fc91cff0718a524f622dc183c5f623e378c055dd7ce6cac40236a6a`，`BrowserBridge.exe` SHA256 `097104083381a4427dd4a0b249904848f3db481c12b934480c2da5328f38468e`。
- 本次执行了完整安装验收 `scripts/smoke_installer.py --installer dist/daw-downloader-v0.6.0-windows-x64-setup.exe`：中文向导、含空格自定义路径、白名单载荷哈希、快捷方式、卸载项与浏览器注册、占用时拒绝升级/卸载、同目录升级保留配置与断点、卸载保留他方注册、重装保留数据共 6 组 PASS；结束后原浏览器注册已还原、`dist/data` 哈希未变。

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
