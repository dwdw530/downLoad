# 悬浮条渲染质量：从色键透明改成真逐像素 alpha

- 日期：2026-10-21（Asia/Shanghai）
- 项目：`D:/vscode_workspace/downLoad_project`
- 起因：用户反馈悬浮条「有毛边」，随后对第一版修复给出否定（边缘发黑、仍不合格）
- 状态：源码已改并自测；**`dist/` 未重新打包**

## 1. 问题

Tk 8.6 的 Canvas 不做抗锯齿，原实现用 7 段折线逼近圆角 + `-transparentcolor` 色键，圆角是台阶状。

第一轮修复尝试（已废弃）：改用 Pillow 4 倍超采样绘制图形，仍走色键透明。结果是**更糟**——色键透明是二值的，抗锯齿必然在边缘留下一圈「卡面 → 键色」的过渡像素，而这圈过渡像素本身不透明，最外圈轮廓依然按像素阶梯排布。在浅色背景上就是**一圈锯齿状的黑边**（深色卡面配近黑键色时）。换键色只能把黑边换成灰边，根因无法消除。

## 2. 最终方案：`UpdateLayeredWindow`

Windows 分层窗口支持 `ULW_ALPHA`，可以给窗口提供 **32 位预乘 BGRA 位图**，实现真正的逐像素 alpha：圆角外侧 `alpha=0` 完全透明，边缘像素半透明，与桌面上任何内容自然混合，不存在任何色键过渡环。

改动要点（`downloader/ui/float_bar.py`）：

1. **不再用 Canvas 画任何东西**，只保留 Toplevel 窗口接收鼠标事件（事件直接绑在窗口上）。
2. `LayeredSurface`：`SetWindowLongW(GWL_EXSTYLE, ... | WS_EX_LAYERED | WS_EX_TOOLWINDOW)` + `UpdateLayeredWindow(..., ULW_ALPHA)`，图像先转预乘 `RGBa` 再取 `BGRA` 字节喂给 DIB section。
3. **绘制抽成纯函数** `render_bar(mode, theme, state, samples, hovered, scale) -> PIL.Image`：
   - 4 倍尺寸画圆角、进度条、状态点、曲线、**文字**，`LANCZOS` 降采样；
   - 文字改由 Pillow 用微软雅黑渲染（`msyh.ttc` / `msyhbd.ttc`，带缓存；取不到退回 `load_default`）。
4. `_draw()` = `render_bar()` + `push()`，每秒一次；位置/尺寸变化后补推一次。
5. 删除 `TRANSPARENT_KEY`、`_CardPaint`、`_round_rect`、`-transparentcolor`。

同时修掉一个显示 bug：**空闲态底部原本会画一条贴底的绿色实线**（速度采样全为 0 时，折线的点全落在底边上，且末端还画一个绿点）。现在 `_curve_points()` 在「采样不足或全 0」时返回 `None`，改为画一条中性轨道色基线。

## 3. 取舍

| 项 | 说明 |
|---|---|
| 文字清晰度 | 由 Tk/ClearType 改成 Pillow 灰阶抗锯齿 + 4 倍超采样，观感略软但更均匀，中文小字仍清晰 |
| 性能 | 每秒重绘一张 4 倍图（280×118 @150% → 1680×708）再降采样，实测无明显开销；字体对象已缓存 |
| 点击穿透 | 分层窗口 alpha=0 区域点击自动穿透到下层，圆角外区域不再拦截鼠标（比色键更符合直觉） |
| 依赖 | 无新增。`Pillow` 原本就是依赖（托盘图标） |

## 4. 验证

| 项 | 结果 |
|---|---|
| 纯函数渲染 | `render_bar` 输出 RGBA；圆角 89 级 alpha、约 1900 个半透明像素；四角 `alpha=0` |
| 白底合成检查 | 白底 10 倍放大圆角平滑，无黑边；深色底同样干净 |
| 实机检查（一次性脚本） | 窗口句柄有效、`exstyle=0x00080088`（含 `WS_EX_LAYERED`）、屏幕截取确认卡片真实绘制、悬停重绘、切迷你、拖动夹屏幕、主题切换、隐藏/重显 均正常 |
| `tests/test_robustness_batch.py::CardPaintTests` | 7 项：alpha 多级过渡、四角全透明、迷你/完整尺寸、空闲无主色像素、下载中有主色、主题调色板完整 |
| `test_gui_workflows.py::TransientUiTests` | 49 项通过（含新增 `test_float_bar_uses_layered_alpha_rendering`） |
| 全量回归 | **414 项通过**（594 s） |

## 5. 本轮踩的坑（已修）

重写时把三处不该动的东西动了，用户当场指出：

1. **右键菜单字号被放大**：旧代码是 `tkfont.Font(size=-max(10, round(12 * scale)))`（负值 = 像素），我写成元组 `(family, max(9, round(12 * scale)))`（正值 = 磅）。在 200% 缩放下就是 `24 磅 ≈ 32px`，比原来 24px 大一整圈。原生 Tk 控件的字号口径是「正数=磅、负数=像素」，这个坑 v0.8.1 就踩过一次，不能第二次。已恢复负值。
2. **“显示主窗口”菜单项被弄丢**：重建 `_build_menu` 时漏了一行。已补回，并用用例锁住完整菜单项列表。
3. **两处字号悄悄改小**：完整模式的百分比 15→13、文件名 11→10。已改回原值。

教训：只换渲染引擎，**字号、坐标、菜单项一律照旧**。已新增 `test_float_bar_menu_font_matches_previous_style`，把菜单字号（必须是负像素值且等于旧公式）和 11 个菜单项逐项锁死。

## 6. 未做 / 已知限制

- 非 Windows 平台会降级为「有窗口但推不上像素」，仅用于测试环境，不影响打包产物。
- 悬浮条没有投影；如果以后要加，真 alpha 下可以直接画柔和阴影（色键方案做不到）。
- 未做多显示器 / 高 DPI 混合缩放（150% + 200% 混接）的专项验证。
