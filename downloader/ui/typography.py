# -*- coding: utf-8 -*-
"""
只用于左侧分类树，按逻辑像素与现有 CTk 按钮的 13px 字号对齐。

原生 tkinter.Label 的正数字号是磅，不是 CTk 使用的像素：16pt 约为 21px，
会导致侧栏明显偏大。负值明确指定像素，创建控件时只跟随既有 CTk 缩放，
不修改全局字体、Tk scaling 或系统 DPI。标题只加粗，不额外放大。
"""

UI_FONT_FAMILY = "Microsoft YaHei UI"

# 分类项（全部/下载中/视频…）与分组标题（分类/状态/类型/站点）
SIDEBAR_ITEM_FONT = (UI_FONT_FAMILY, -13)
SIDEBAR_HEADING_FONT = (UI_FONT_FAMILY, -13, "bold")


def scale_sidebar_font(font: tuple, widget_scaling: float) -> tuple:
    """逻辑像素转原生 Tk 的负像素值，避免再次混用磅和像素。"""
    return (font[0], -max(1, round(abs(font[1]) * widget_scaling)), *font[2:])
