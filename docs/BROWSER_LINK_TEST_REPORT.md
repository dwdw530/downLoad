# 浏览器右键普通链接下载：独立审查与测试报告

日期：2026-10-14（Asia/Shanghai）。范围：Pi 实现后的独立代码审查、问题修复、源码回归、隔离 EXE 验收，以及 v0.7.0 正式构建和安装验收。

## 结论

本轮源码审查和隔离验收已完成。Codex 修复了右键入口的上下文竞态、视频入口误用、编码清单绕过及反馈/监听问题；全量 Python/GUI、扩展测试、既有视频浏览器冒烟和隔离 EXE 冒烟均通过。

真实浏览器原生菜单到 EXE 的冷启动、带 Cookie 的签名 GET 下载、iframe 来源信息，以及关闭视频识别后继续手动下载均已实际操作验证。普通左键仍由浏览器下载，不创建桌面任务。

**正式 `dist/` 已重建为本次 v0.7.0 载荷，扩展为 0.6.0，安装包及正式 EXE 验收通过。** 原隔离审查后的交付结果见文末。本报告不代表所有真实站点、Edge 和各旧版本组合都已现场重测，具体范围见下文。

## 审查发现及修复

| 问题 | 复现与影响 | 修复 |
|---|---|---|
| 初始 frame 缺失或在首次查询前导航 | 原实现可能使用新页面的 frame/Cookie 环境处理旧右键链接 | 必须取得有效 documentId，并核对点击来源 URL；发送前再次检查 documentId 与来源 URL |
| 已知视频页面进入普通文件流程 | YouTube 等页面会在最终 HTML 拒绝前先读取账号 Cookie，不符合已有视频入口边界 | 复用既有平台 URL 解析器，先拒绝平台视频页、抖音页面/专用 CDN 和播放清单，再考虑连接桥接与读取 Cookie |
| 编码后的清单扩展名漏检 | `/master%2Em3u8`、`/manifest.%6Dpd` 及重定向目标能被当普通文件创建 | 扩展与新文件后端分支在分类时解码路径；发送的签名 URL 保持原串 |
| 回复丢失被当成确定失败 | Native 连接断开时任务可能已创建，原反馈容易诱发重复手动提交 | 未收到确定业务拒绝的断连、异常回复及发送后异常统一报告“结果尚未确认”，不自动重发 |
| 未预期的 Native 错误原文落入本地状态 | 原实现直接保存/通知 error 字段，可能带入 URL 等信息 | 反馈使用受控状态码及固定文案，不持久化原始错误文本 |
| 通知不可用时看不到发送中状态 | popup 只看到上一次结果 | 发送开始就写入独立 local 状态，popup 正确显示“正在发送”，不作为错误着色 |
| 生命周期监听重复及初始化异常外溢 | 重复安装会追加监听；可恢复 API 异常可能打断整个 Worker 初始化 | 按事件实例记录订阅，注册入口单例化，局部处理可恢复错误，不影响旧视频 Worker |

产品修复只涉及新增 `link-download.js`、独立 popup 状态渲染及 `security.py` 的新文件 URL 检查。Pi 已完成的独立协议、文件模式持久化和旧视频能力隔离经复核保留；未修改下载引擎、数据库、任务状态机、`content.js`、`media.js`、`douyin-page.js` 或 `youtube_downloader.py`。

新增独立用例：

- `tests/browser_link_download.test.mjs`：19 项，覆盖新入口的行为与边界；修复前其中 8 项未通过，修复后全部通过。
- `tests/test_browser_file_download.py`：15 项，使用本地 HTTP 服务、临时数据库与真实 DPAPI；修复前复现编码清单及重定向漏检。另修正了新增测试夹具中模拟 requests.Response 的关闭状态，未把该夹具错误计作产品缺陷。
- `scripts/browser_link_test_fixture.py`：真实浏览器/EXE 的隔离样本服务和独立 Native Host 注册生命周期。
- `scripts/smoke_browser.cjs`：只增加可选 `DAW_SMOKE_OUTPUT` 输出目录，保留原有断言，避免覆盖既有截图。

## 自动化结果

| 验证 | 结果 | 说明 |
|---|---|---|
| 全量 Python/GUI | **323 项通过，451.190 秒** | `py310_env`，含原 308 项与新增 15 项；暂停/重启、Range、同名保护、哈希、队列、设置、主题、历史、托盘、完成后动作等均包含在现有回归中 |
| JavaScript | **33 项通过** | 原媒体测试 14 项 + 新链接测试 19 项 |
| 原视频浏览器冒烟 | **9 组通过** | 真实 Chromium 中运行本地/拦截夹具，覆盖视频直链、iframe、HLS、blob、YouTube、X 首页、B 站分 P、抖音推荐/详情/modal_id 等旧路径 |
| 隔离 EXE 冒烟 | **3 组通过** | 原生按钮、暂停恢复、重启、队列、Range/非 Range、SHA256、历史清空确认、空列表、数据位置与正常退出 |
| 发布白名单核对 | **12 个扩展文件一致** | 当前源码与隔离包 `app/chrome-extension` 逐文件字节比较通过 |
| Git 差异检查 | **通过** | 使用仓库原有换行设置运行 `git diff --check`；未改动整仓换行格式 |

实际执行的核心命令：

```text
node --test --test-reporter=spec tests/browser_extension.test.mjs tests/browser_link_download.test.mjs
D:/anaconda3/envs/py310_env/python.exe -B -m unittest discover -s tests -v
node scripts/smoke_browser.cjs C:/Users/lenovo/AppData/Local/npm-cache/_npx/84539a01c0e4c364/node_modules/playwright
D:/anaconda3/envs/py310_env/python.exe -B scripts/build_exe.py --dist-dir <本次隔离目录>/app
D:/anaconda3/envs/py310_env/python.exe -B scripts/smoke_exe.py --exe <本次隔离目录>/app/daw下载器.exe
```

其中视频冒烟的 `DAW_SMOKE_OUTPUT` 指向本次证据目录。上述隔离目录为 `D:/vscode_workspace/downLoad_project/output/playwright/link-review-20261014-av2neo`。

首次 EXE 冒烟已通过下载/恢复/哈希部分，但历史窗口定位超时，诊断显示测试窗口被最小化。未因此修改产品代码；随后通过 Windows 桌面操作独立验证历史显示、清空确认及空列表，再重跑原脚本，三组均通过。保留第一次失败与复验记录，不把它隐去。

## 真实右键到 EXE 的验证

使用独立 Chromium 配置、隔离程序目录、临时 Cookie 和本地文件服务。测试扩展副本仅将 Native Host 名称改为随机测试名，其他代码保持相同；真实系统菜单通过鼠标右键打开，再用 Windows 桌面工具选择菜单项，不是直接调用下载函数冒充菜单验收。

| 场景 | 实际结果 |
|---|---|
| 普通左键点击 ZIP | 浏览器生成 `public.zip` 下载，桌面任务数仍为 0 |
| 原生右键菜单 | 唯一显示“使用 daw 下载器下载此链接”，浏览器原菜单项保留 |
| 桌面未启动时选择菜单 | BrowserBridge 冷启动隔离 EXE，创建并完成 `private.zip` |
| Cookie 与签名 | 服务端确认 Cookie 正确；`sig=a%2Bb+c&part=1&part=2` 原样保留 |
| 关闭视频识别 | popup 仍显示原文件结果，普通右键入口仍可用 |
| iframe 中选择链接 | 已运行的同一 EXE 完成 `iframe.zip`，Referer 正确为 iframe 的 `/frame` |
| 文件完整性 | 两个 ZIP 均为 completed，SHA256 与本地样本一致 |
| 历史清空 | 确认后历史表计数为 0；两条任务与两个 ZIP 文件仍在，哈希未变；没有多余“清空成功”确认框 |

样本 SHA256：`4ca9b55c1f1505ad2816c0987bb2bc564b21ce2282ba3a67eeeed7b3a9ab4636`。

实际测试过程中发现 Node 工具启动的 Chromium 环境缺少 `COMSPEC`，浏览器日志明确为 `COMSPEC is not set` / `Failed to start native messaging host`。同一个 BrowserBridge 单独运行 ping 正常；只给新的隔离浏览器进程提供标准 Windows 环境后，真实 Native Messaging 与下载均通过。未修改系统环境变量或为此改动产品代码。

桌面工具还曾因检测到新的窗口输入而中止单次操作；这是交互保护，并非取消整个审查。按要求重新观察窗口后继续，未绕开工具的输入保护。

## 证据与隔离包

证据目录：[link-review-20261014-av2neo](../output/playwright/link-review-20261014-av2neo)。

- [原生右键菜单](../output/playwright/link-review-20261014-av2neo/context-menu-ready.jpg)
- [下载器完成文件的界面](../output/playwright/link-review-20261014-av2neo/native-app.jpg)
- [关闭视频识别后的文件状态](../output/playwright/link-review-20261014-av2neo/link-popup.png)
- [历史窗口](../output/playwright/link-review-20261014-av2neo/history-open.jpg)
- [清空后的历史窗口](../output/playwright/link-review-20261014-av2neo/history-cleared.jpg)
- [首次原生下载结果](../output/playwright/link-review-20261014-av2neo/native-download.json)
- [iframe 下载与来源记录](../output/playwright/link-review-20261014-av2neo/native-iframe.json)
- [注册清理核对](../output/playwright/link-review-20261014-av2neo/cleanup.json)：原生产注册值保持不变，测试 host 已删除。

全量 Python 日志：`C:/Users/lenovo/.fastctx/jobs/j-xuzw8y/output.log`。
视频浏览器冒烟日志：`C:/Users/lenovo/.fastctx/jobs/j-isijtq/output.log`。
EXE 首次/复验日志：`C:/Users/lenovo/.fastctx/jobs/j-jwuod8/output.log`、`C:/Users/lenovo/.fastctx/jobs/j-c6xr95/output.log`。
EXE 复验产物：`C:/Users/lenovo/AppData/Local/Temp/downloader-exe-smoke-rxvc6b_m`。

隔离构建产物 SHA256：

- `app/daw下载器.exe`：`cada83bfeb0938e4a5c69966d8155febe961a9dfc410f80384a5fcbd408e45fb`
- `app/BrowserBridge.exe`：`7c40c871265653dca49f9e5d2fd6f36a33f186dedbf3f57c107ca402751df44f`

## 验证边界与后续交付

- 实测浏览器为本机 Playwright 缓存的 Chromium/Chrome for Testing；未对 Edge、Chrome 最低版本 120、真实隐身 Cookie store 逐一现场重复。相关协议/上下文边界有独立用例覆盖，但不把单测称为这些真实环境验收。
- 未重新访问真实 YouTube/X/B 站/抖音账号做公开站点下载；旧路径的本地浏览器夹具、Python 用例已通过。
- FFmpeg 缺失和新旧协议能力检查已有隔离用例；未逐一安装所有历史 EXE 组合。
- 原隔离审查阶段未创建安装包或发布 ZIP；之后按用户授权完成正式 `dist/` 重建及安装验收，结果见下节。
- 隔离 `app/` 中已有测试数据库和配置；**不得整目录复制到正式 `dist/`**。后续升级只取程序白名单载荷，保留正式用户配置、记录、断点和下载文件。
- 未知大小、零字节、POST/特殊鉴权、分区 Cookie 仍按设计范围不支持；没有顺带扩展这些功能。

## v0.7.0 正式构建与验收

- 用户随后明确授权打包、提交 GitHub、发布新版本和更新日志。版本为桌面 v0.7.0、扩展 0.6.0；保留历史版本。
- 使用 `py310_env` 重建原 `dist` 的双 EXE 和扩展；未复制隔离测试数据库。25 项正式发布载荷核对通过，12 项扩展文件与源码一致。
- 发布前复跑：JavaScript 33 项通过；新增 Python 文件下载测试 15 项通过（29.427 秒）。复用并核实同批源码的原全量 323 项通过记录，没有把复跑数累加成新用例数。
- 正式 EXE 三组冒烟通过（`j-xt8b95`）；覆盖下载、暂停恢复、重启、队列、Range/非 Range、哈希、历史确认与正常退出。
- 安装包六组验收通过（`j-vrjwrf`）：中文向导及自选路径、完整载荷、安装后程序、占用拦截、同目录升级、卸载与重装的数据保护；原浏览器注册已恢复。
- 在原 `smoke_browser_bridge.py` 补充新功能成品检查：安装后的桥接声明 `file_download_v1`，真实发送带 Cookie 的签名二进制文件，校验成品逐字节一致、上下文加密及同请求重复提交不重复建任务。既有视频与单实例断言保留，全部通过。
- 安装记录：`output/installer-smoke/run-_4eq7x45/result.json`；桥接日志：同目录 `installed-app-smoke.log`。
- 原 `dist/data`、`dist/temp`、`dist/downloads` 按相对文件名、大小和 SHA256 核对，已有 2 个文件、共 156,346 字节保持不变。

| 成品 | 字节数 | SHA256 |
|---|---:|---|
| `dist/daw下载器.exe` | 21,669,170 | `2ac79d3268095a799c2089d730c5184d82007569d07ee6e56fc1515711189f65` |
| `dist/BrowserBridge.exe` | 9,984,156 | `7e38beee9d0db4b2cc7dcee21ac36d3eff19a7cf3e72a444cbf55a9344e35e26` |
| `dist/daw-downloader-v0.7.0-windows-x64-setup.exe` | 155,772,595 | `25d86688a6d27dad694e746d625106916a5c20831bda9b45f42b92f23ab613ff` |

设计依据：[browser-link-download.md](designs/browser-link-download.md)。专项进度继续维护在[原功能分析档](context-sessions/20251214-1555-老王下载器功能分析.md)。
