"""生成应用图标 assets/icon.ico 和 assets/icon.png。

纯 Python（只用标准库）绘制：靛蓝到紫色的渐变圆角方块，中间一颗金色四芒星。
改了样式后运行一次：python tools/make_icon.py
"""

from __future__ import annotations

import struct
import zlib
from pathlib import Path

SIZES = (16, 24, 32, 48, 64, 128, 256)
SAMPLES = 3  # 每个像素在横纵方向各取几个点做抗锯齿


def _pixel(u: float, v: float) -> tuple:
    """(u, v) 取值 0..1，返回 (r, g, b, a)，a 为 0..255。"""
    # 圆角方块：到“收缩后的方块”的距离小于圆角半径就算在里面
    radius, inset = 0.22, 0.02
    dx = max(abs(u - 0.5) - (0.5 - inset - radius), 0.0)
    dy = max(abs(v - 0.5) - (0.5 - inset - radius), 0.0)
    if (dx * dx + dy * dy) ** 0.5 > radius:
        return (0, 0, 0, 0)

    t = (u + v) / 2                                   # 对角渐变
    r, g, b = (int(a + (c - a) * t) for a, c in zip((52, 56, 150), (128, 72, 200)))

    # 四芒星：星形线 √|x| + √|y| ≤ √R；中心更亮一点
    x, y = abs(u - 0.5), abs(v - 0.5)
    if x ** 0.5 + y ** 0.5 <= 0.62 ** 0.5 * 0.5 ** 0.5 * 1.15:
        glow = 1 - min(1.0, (x * x + y * y) ** 0.5 * 2.6)
        r, g, b = int(245 + 10 * glow), int(190 + 55 * glow), int(70 + 130 * glow)
    return (r, g, b, 255)


def _render(size: int) -> bytes:
    """返回 size×size 的 RGBA 原始像素。"""
    rows = bytearray()
    step = 1 / (size * SAMPLES)
    for py in range(size):
        for px in range(size):
            acc = [0, 0, 0, 0]
            for sy in range(SAMPLES):
                for sx in range(SAMPLES):
                    r, g, b, a = _pixel(px / size + (sx + 0.5) * step, py / size + (sy + 0.5) * step)
                    acc[0] += r * a
                    acc[1] += g * a
                    acc[2] += b * a
                    acc[3] += a
            if acc[3]:
                rows += bytes((acc[0] // acc[3], acc[1] // acc[3], acc[2] // acc[3], acc[3] // (SAMPLES * SAMPLES)))
            else:
                rows += b"\x00\x00\x00\x00"
    return bytes(rows)


def _png(size: int) -> bytes:
    raw = _render(size)
    stride = size * 4
    scan = b"".join(b"\x00" + raw[y * stride:(y + 1) * stride] for y in range(size))

    def chunk(kind: bytes, data: bytes) -> bytes:
        body = kind + data
        return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body))

    header = struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0)  # 8 位 RGBA
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header) + chunk(b"IDAT", zlib.compress(scan, 9)) + chunk(b"IEND", b"")


def _ico(images: dict) -> bytes:
    """ICO 文件：文件头 + 每张图的目录项 + 各张 PNG 数据（Vista 以后都支持 PNG 格式）。"""
    out = struct.pack("<HHH", 0, 1, len(images))
    offset = 6 + 16 * len(images)
    blobs = b""
    for size, png in images.items():
        out += struct.pack("<BBBBHHII", size % 256, size % 256, 0, 0, 1, 32, len(png), offset + len(blobs))
        blobs += png
    return out + blobs


def main() -> None:
    target = Path(__file__).resolve().parent.parent / "assets"
    target.mkdir(exist_ok=True)
    images = {size: _png(size) for size in SIZES}
    (target / "icon.ico").write_bytes(_ico(images))
    (target / "icon.png").write_bytes(images[256])
    print(f"已生成 {target / 'icon.ico'} 和 {target / 'icon.png'}")


if __name__ == "__main__":
    main()
