# -*- coding: utf-8 -*-
"""
图标生成器
老王说：给下载器整个好看的图标！
"""
from PIL import Image, ImageDraw


def create_download_icon(size=256):
    """
    创建下载图标
    Args:
        size: 图标尺寸
    """
    # 创建画布（透明背景）
    img = Image.new('RGBA', (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    # 渐变蓝色背景圆
    center = size // 2
    radius = int(size * 0.45)

    # 绘制圆形背景（深蓝到浅蓝渐变效果，用多层圆模拟）
    for i in range(radius, 0, -2):
        alpha = int(255 * (1 - i / radius * 0.3))
        # 从深蓝(30, 144, 255)到亮蓝(100, 200, 255)
        r = 30 + int(70 * (1 - i / radius))
        g = 144 + int(56 * (1 - i / radius))
        b = 255
        color = (r, g, b, alpha)
        draw.ellipse([center - i, center - i, center + i, center + i], fill=color)

    # 绘制下载箭头（白色）
    arrow_color = (255, 255, 255, 255)
    line_width = int(size * 0.08)

    # 箭头杆（垂直线）
    shaft_top = int(size * 0.25)
    shaft_bottom = int(size * 0.6)
    shaft_x = center
    draw.line([(shaft_x, shaft_top), (shaft_x, shaft_bottom)], fill=arrow_color, width=line_width)

    # 箭头头（三角形）
    arrow_head_size = int(size * 0.2)
    arrow_points = [
        (center, shaft_bottom + int(arrow_head_size * 0.6)),  # 下尖
        (center - arrow_head_size, shaft_bottom - int(arrow_head_size * 0.2)),  # 左
        (center + arrow_head_size, shaft_bottom - int(arrow_head_size * 0.2)),  # 右
    ]
    draw.polygon(arrow_points, fill=arrow_color)

    # 底部横线（下载到的位置）
    line_y = int(size * 0.75)
    line_padding = int(size * 0.25)
    draw.line([(line_padding, line_y), (size - line_padding, line_y)],
              fill=arrow_color, width=int(line_width * 0.8))

    return img


def save_as_ico(img, output_path):
    """
    保存为多尺寸ico文件
    Args:
        img: PIL Image对象
        output_path: 输出路径
    """
    # 生成多个尺寸（Windows推荐）
    sizes = [256, 128, 64, 48, 32, 16]
    images = []

    for size in sizes:
        resized = img.resize((size, size), Image.Resampling.LANCZOS)
        images.append(resized)

    # 保存为ico（包含多个尺寸）
    images[0].save(output_path, format='ICO', sizes=[(s, s) for s in sizes])
    print(f"[成功] 图标已生成: {output_path}")


def create_png_icon(output_path, size=256):
    """
    创建PNG图标（用于预览）
    Args:
        output_path: 输出路径
        size: 尺寸
    """
    img = create_download_icon(size)
    img.save(output_path, format='PNG')
    print(f"[成功] PNG图标已生成: {output_path}")


if __name__ == "__main__":
    print("[启动] 老王图标生成器")

    # 生成ICO图标
    icon_img = create_download_icon(256)
    save_as_ico(icon_img, "assets/icon.ico")

    # 生成PNG预览（方便查看）
    create_png_icon("assets/icon.png", 256)

    print("[完成] 图标生成完毕！")
    print("       ICO图标: assets/icon.ico")
    print("       PNG预览: assets/icon.png")
