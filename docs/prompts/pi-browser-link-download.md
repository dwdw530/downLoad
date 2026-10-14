# 给 Pi Agent：实现浏览器右键发送普通文件链接

日期：2026-10-14。下面的任务由用户交给 Pi 执行；源码实现完成后交回 Codex 独立审查和测试。不要启动其他代理代替 Codex 的审查与测试角色。

## 可以直接复制给 Pi 的完整提示词

你在项目 `D:/vscode_workspace/downLoad_project` 中工作，请按下面的明确范围完成源码实现。

**我要的功能是：在 Chrome / Edge 的网页链接上点鼠标右键，选择“使用 daw 下载器下载此链接”，才把这个链接交给 daw 下载器。正常点击、另存为、浏览器自带下载必须保持原样。不要实现浏览器全局自动接管，不要擅自扩展其他功能。**

实现前必须阅读：

1. `D:/vscode_workspace/downLoad_project/docs/designs/browser-link-download.md`：本次完整设计、接口、允许改动范围和 Codex 验收矩阵。
2. `D:/vscode_workspace/downLoad_project/docs/context-sessions/20251214-1555-老王下载器功能分析.md`：重点读最新“右键普通链接下载专项交接”，不要把前面的全部候选功能当成本次任务。
3. `D:/vscode_workspace/downLoad_project/docs/BROWSER_VIDEO.md`：已有视频功能、凭据和站点边界。
4. 当前环境中的项目/全局 AGENTS.md 及适用工作流。需要本地历史约定时，按已有索引读取 `C:/Users/lenovo/.claude/projects/D--vscode-workspace-downLoad-project/memory/`；不要用远程记忆服务。

本次角色分工：

- **你负责实现源码、必要的静态自检及准确交接。**
- **Codex 负责独立代码审查、补充必要用例、实际测试和回归验收。**
- 你完成后停止，不代替 Codex 宣布功能验收通过，不打包部署到用户正在使用的程序。

### A. 实施前核对

- 先查看实际 Git HEAD、工作区差异及目标文件。设计调研时 HEAD 为 `bdaa2951f2f05185daa3af75445b5433b5c834b0`，但不能为了对齐它回退仓库。
- 功能分析档已有修改，`output/` 中已有产物；保留其他会话和用户的工作。
- 先读现有扩展 `background.js`、`native_host.forward`、`BridgeServer.dispatch`、`normalize_context/browser_get` 与 `TaskManager.add_task`，再改动。
- 所有文本源码和文档编辑使用 `apply_patch`。不提交、不建分支、不推送、不 reset、不删除用户数据；不要用 shell 覆盖写文件或全仓格式化。
- 若现状与设计不同，先判断是否只是行号/小接口变化。可在既定范围内适配并记录；实质性范围变化或需改动受保护模块时，先给出具体原因和最小替代方案，不擅自大改。

### B. 按设计实现

1. 新增独立 `chrome-extension/link-download.js`，通过 `registerLinkDownloadMenu(native)` 一类接口复用已有 Native Messaging 传输。`background.js` 只增加导入与初始化，不重写 `download()`、媒体记录、平台判断及旧消息分发。
2. 注册唯一的链接右键菜单，固定 ID `daw-download-link-v1`。MV3 使用顶层 `contextMenus.onClicked`；只接受 `info.linkUrl` 中的 HTTP/HTTPS。注册幂等、支持 Worker 重新唤醒，不能接管普通点击。
3. 手动右键入口独立于“识别视频”开关。普通文件不进入视频列表，不占用视频 badge，不更改视频按钮、清晰度和站点上下文。
4. 新 Native 操作使用 `action: "download_file"`。在 ping 增加 `features: ["file_download_v1"]`，保留原视频 `capabilities` 与 `VIDEO_KINDS` 不变。文件 feature 在冷启动/已启动和 FFmpeg 缺失时均正确；旧组件不支持时明确提示升级。
5. 只在用户选择菜单后读取正确 tab/frame 和 cookie store 的必要下载上下文；保留 URL 原串。只发允许的 UA/Referer/Cookie，不支持 POST、特殊 Authorization 和分区 Cookie；不新增网页可任意提交下载 URL 的消息入口。
6. 桌面桥接独立处理新 action，复用请求 ID 检查、容量有界的回执机制、单实例和 `TaskManager.add_task`。实际文件命名与同名保护交给原引擎/数据库，回执读取真实任务文件名。
7. 按设计第 7 节扩展文件请求模式：新文件上下文 `media: False` 必须经 `normalize_context` 的二次处理、DPAPI 落库、重启和分块请求完整保留；缺省仍是原媒体模式。旧视频的 MIME 白名单、跨域凭据规则和校验不能放松。
8. 普通文件路径拒绝 HTML 登录页、已知清单及抖音专用 CDN（含重定向目标），不要把 PDF/ZIP 伪装成 MP4，也不要绕过已有完整视频校验。未知大小仍按当前引擎能力明确拒绝，本次不补该功能。
9. 按设计提供浏览器通知和 popup 中独立的链接状态区。状态存储只保留脱敏结果，不写 URL/Cookie/请求本体；不复用视频 `#status`，不受关闭视频识别时的 `storage.session.clear()` 影响。超时属于结果未知，不自动重发。
10. 新扩展运行文件加入 `scripts/package_release.py` 的 `EXTENSION_FILES`。更新相关使用文档，说明这是按需右键发送，源码实现状态与正式 EXE 状态要分开。

### C. 保护现有功能

- 不改动 `content.js`、`douyin-page.js`、`media.js`、`youtube_downloader.py` 的视频行为，不修改 YouTube、X 首页、B 站分 P、抖音推荐页/详情/modal_id 的识别和下载策略。
- 不改下载引擎、数据库结构、队列状态机、暂停续传规则、文件名安全和“不覆盖已有文件”策略；不新增另一个下载器或本地服务。
- 不改现有托盘、历史、主题、分类树、剪贴板、批量添加、稍后下载、速度曲线和完成后动作。
- 不改扩展 key、主机名、注册表绑定、已有视频能力数组和原协议行为。
- 不删、不 skip、不弱化现有测试。遇到真实问题原样记录，不靠改断言掩盖。
- 不实施前期清单中的全局接管、动态分段、刷新链接、未知大小下载、磁盘预检、定时队列等其他功能。
- 不运行构建/发布脚本，不修改 `dist/`、用户配置、数据库或断点，不注册/卸载浏览器桥接，不关闭或强杀用户程序。

### D. 自检与交接

可以做与改动相关的语法/导入检查以及 `git diff --check`，记录实际命令、运行环境和结果。不要用源码检查冒充浏览器操作、真实下载或 EXE 验收。本轮正式功能测试、旧功能回归和实际浏览器/EXE 测试留给 Codex。

检查差异，清除你新增的 TODO、假实现和未接通的路径。实现必须接通“菜单 → 原生主机 → 新文件分支 → 原普通任务引擎”；仅增加一个能点的菜单不算完成。若存在未解决阻碍，明确标注尚未完成的链路，不伪报可用。

然后更新原进度档 `D:/vscode_workspace/downLoad_project/docs/context-sessions/20251214-1555-老王下载器功能分析.md` 的“右键普通链接下载专项交接”，记录：

1. 实际修改与新增的文件，每个文件的目的。
2. 新协议、普通文件模式、旧视频兼容与重复请求处理的实现位置。
3. 自检命令和真实结果；未运行的测试明确列出。
4. 已知限制、未解决问题和任何与设计不同的处理。
5. `dist/` 未部署、等待 Codex 独立审查与测试。

最终回复提供简洁的文件清单与交接说明，最后明确写：

**“源码实现已交接，待 Codex 独立代码审查和测试；正式 dist 尚未更新。”**

如果源码尚未完全实现，把这句话中的完成状态改成真实状态。不要宣称“所有旧功能绝对不受影响”，由 Codex 的基线对比和验收证据决定。

## 用户交回 Codex 时可用的提示词

```text
Pi 已完成浏览器右键发送链接功能，请按
D:/vscode_workspace/downLoad_project/docs/designs/browser-link-download.md
先独立审查实际变更，再补充必要测试并执行新增功能与旧功能回归。
先确认 Pi 的改动范围和交接记录，保留工作区原有改动及用户数据。
不能用模拟回调通过代替真实浏览器右键菜单验收，不能用源码通过代替 EXE 验收。
发现问题请修复并复验，报告实际结果和仍未验证的部分。
```

这段后续提示词不包含 Git 发布授权。实际打包与正式目录部署按用户届时要求推进。
