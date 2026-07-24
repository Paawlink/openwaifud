#!/usr/bin/env python3
"""将桌宠 PNG 帧序列转换为 LVGL RGB565A8 格式的 C 源文件。

用法:
    python3 tools/convert_avatar_frames.py

从 ~/Downloads/小火人_GIF尺寸统一_135x185_透明背景 读取各形象文件夹的 PNG 帧，
输出到 TuyaOpen/apps/openwaifu/src/assets/ 目录下，命名规则:
    <emotion>_<NN>.c  (如 coding_01.c, error_01.c ...)
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from PIL import Image

# ── 路径配置 ──────────────────────────────────────────────
SRC_BASE = Path.home() / "Downloads" / "小火人_GIF尺寸统一_135x185_透明背景"
DST_DIR  = Path.home() / "Documents" / "TuyaDev" / "TuyaOpen" / "apps" / "openwaifu" / "src" / "assets"

# ── 形象文件夹 -> C 变量前缀 映射 ─────────────────────────
# (子目录名, C 变量前缀, 是否跳过已有文件)
EMOTION_MAP = [
    ("01_Thinking_reference",  "thinking",    True),   # 已存在，跳过
    ("02_Coding_reference",    "coding",      False),
    ("03_Error_unified",       "error",       False),
    ("04_Celebration_unified", "celebration", False),
    ("05_Confused_unified",    "confused",    False),
    ("07_Moyu_unified",        "moyu",        False),
    ("08_Sleep_unified",       "sleep",       False),
    ("09_Cycling",             "cycling",     False),
]

# 预期帧尺寸
FRAME_W = 135
FRAME_H = 185
BYTES_PER_LINE = 128  # 每行输出的字节数


def png_to_rgb565a8(img: Image.Image) -> bytes:
    """将 RGBA PNG 转换为 LVGL RGB565A8 原始数据。

    布局: 先全部 RGB565 颜色数据 (w*h*2 字节)，再全部 A8 alpha 数据 (w*h 字节)。
    """
    if img.mode != "RGBA":
        img = img.convert("RGBA")

    w, h = img.size
    pixels = img.load()

    color_data = bytearray(w * h * 2)
    alpha_data = bytearray(w * h)

    idx = 0
    for y in range(h):
        for x in range(w):
            r, g, b, a = pixels[x, y]
            # RGB565 编码
            r5 = (r >> 3) & 0x1F
            g6 = (g >> 2) & 0x3F
            b5 = (b >> 3) & 0x1F
            rgb565 = (r5 << 11) | (g6 << 5) | b5
            # 小端: 低字节在前
            color_data[idx * 2]     = rgb565 & 0xFF
            color_data[idx * 2 + 1] = (rgb565 >> 8) & 0xFF
            alpha_data[idx] = a
            idx += 1

    return bytes(color_data) + bytes(alpha_data)


def format_data_bytes(data: bytes) -> str:
    """将原始字节数据格式化为 C 数组文本 (每行 BYTES_PER_LINE 个字节)。"""
    lines = []
    for i in range(0, len(data), BYTES_PER_LINE):
        chunk = data[i:i + BYTES_PER_LINE]
        hex_vals = ",".join(f"0x{b:02x}" for b in chunk)
        lines.append(f"    {hex_vals},")
    return "\n".join(lines)


def generate_c_file(var_name: str, data: bytes, w: int, h: int) -> str:
    """生成单个 C 源文件内容。"""
    stride = w * 2  # RGB565: 2 bytes per pixel
    map_name = f"{var_name}_map"
    data_text = format_data_bytes(data)

    return f"""\
#if defined(LV_LVGL_H_INCLUDE_SIMPLE)
#include "lvgl.h"
#elif defined(LV_BUILD_TEST)
#include "../lvgl.h"
#else
#include "lvgl/lvgl.h"
#endif


#ifndef LV_ATTRIBUTE_MEM_ALIGN
#define LV_ATTRIBUTE_MEM_ALIGN
#endif

#ifndef LV_ATTRIBUTE_IMG_DUST
#define LV_ATTRIBUTE_IMG_DUST
#endif

static const
LV_ATTRIBUTE_MEM_ALIGN LV_ATTRIBUTE_LARGE_CONST LV_ATTRIBUTE_IMG_DUST
uint8_t {map_name}[] = {{

{data_text}

}};

const lv_image_dsc_t {var_name} = {{
  .header.magic = LV_IMAGE_HEADER_MAGIC,
  .header.cf = LV_COLOR_FORMAT_RGB565A8,
  .header.flags = 0,
  .header.w = {w},
  .header.h = {h},
  .header.stride = {stride},
  .data_size = sizeof({map_name}),
  .data = {map_name},
}};
"""


def collect_png_frames(src_dir: Path) -> list[Path]:
    """收集目录下所有 PNG 帧文件，按文件名排序。"""
    return sorted(src_dir.glob("*.png"), key=lambda p: p.name)


def main() -> int:
    if not SRC_BASE.is_dir():
        print(f"ERROR: 源目录不存在: {SRC_BASE}", file=sys.stderr)
        return 1

    DST_DIR.mkdir(parents=True, exist_ok=True)

    total_generated = 0
    total_skipped = 0

    for sub_dir, prefix, skip_existing in EMOTION_MAP:
        src_dir = SRC_BASE / sub_dir
        if not src_dir.is_dir():
            print(f"WARN: 跳过不存在的目录: {sub_dir}")
            continue

        pngs = collect_png_frames(src_dir)
        if not pngs:
            print(f"WARN: 目录中无 PNG 文件: {sub_dir}")
            continue

        print(f"\n=== {sub_dir} -> {prefix} ({len(pngs)} 帧) ===")

        for i, png_path in enumerate(pngs, start=1):
            var_name = f"{prefix}_{i:02d}"
            out_path = DST_DIR / f"{var_name}.c"

            if skip_existing and out_path.exists():
                print(f"  跳过已存在: {var_name}.c")
                total_skipped += 1
                continue

            # 读取并转换
            img = Image.open(png_path)
            if img.size != (FRAME_W, FRAME_H):
                print(f"  WARN: {png_path.name} 尺寸 {img.size} != ({FRAME_W}, {FRAME_H})，将缩放")
                img = img.resize((FRAME_W, FRAME_H), Image.LANCZOS)

            data = png_to_rgb565a8(img)
            c_content = generate_c_file(var_name, data, FRAME_W, FRAME_H)

            out_path.write_text(c_content, encoding="utf-8")
            print(f"  生成: {var_name}.c ({len(data)} bytes)")
            total_generated += 1

    print(f"\n完成: 生成 {total_generated} 个文件, 跳过 {total_skipped} 个已有文件")
    print(f"输出目录: {DST_DIR}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
