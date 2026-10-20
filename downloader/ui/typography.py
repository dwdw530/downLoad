# -*- coding: utf-8 -*-
"""
侧栏字体

只用于左侧分类树。侧栏是原生 `tkinter.Label`，它不吃 CustomTkinter 的主题字体，
以前写的是 `("Arial", 14)`：Arial 显示中文要靠系统回退，字形对不上，而且 14pt
在 1080p 上看着偏小。这里换成微软雅黑 UI 并放大一档，只改侧栏，不动其它界面。

注意：原生 Tk 控件的 font 元组里**正数是磅**（负数是像素），所以这里给正值。
"""

UI_FONT_FAMILY = "Microsoft YaHei UI"

# 分类项（全部/下载中/视频…）与分组标题（分类/状态/类型/站点）
SIDEBAR_ITEM_FONT = (UI_FONT_FAMILY, 18)
SIDEBAR_HEADING_FONT = (UI_FONT_FAMILY, 17, "bold")
