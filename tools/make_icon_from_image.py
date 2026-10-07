"""用一张图片生成应用图标 assets/icon.ico 和 assets/icon.png（带圆角）。

这是开发时用的小工具，软件运行不需要它。需要 Pillow：pip install pillow

用法：
    python tools/make_icon_from_image.py 图片路径 --box 左 上 右 下

--box 是要保留的那块正方形区域（像素）。不写的话取图片中间最大的正方形。
例如当前图标是这样做出来的（原图 1024×1025，比正方形多一行，所以取左上角 1024×1024）：
    python tools/make_icon_from_image.py 原图.jpg --box 0 0 1024 1024

注意：图标图片的版权属于原作者，原图本身不放进仓库，仓库里只有生成好的 icon.ico / icon.png。
没有指定图片、想要原来那个纯代码画的星形图标，运行 tools/make_icon.py。
"""

from __future__ import annotations

import argparse
from pathlib import Path

from PIL import Image, ImageDraw

SIZES = [(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)]
MASTER = 1024
SUPERSAMPLE = 4


def rounded_mask(size: int, radius_fraction: float) -> Image.Image:
    """圆角方块的透明度蒙版。先放大 4 倍再缩回来，边缘才平滑。"""
    big = size * SUPERSAMPLE
    mask = Image.new("L", (big, big), 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, big - 1, big - 1), radius=int(big * radius_fraction), fill=255)
    return mask.resize((size, size), Image.LANCZOS)


def build(source: Path, box, radius: float) -> Image.Image:
    image = Image.open(source).convert("RGB")
    if box is None:
        side = min(image.size)
        left, top = (image.width - side) // 2, (image.height - side) // 2
        box = (left, top, left + side, top + side)
    left, top, right, bottom = box
    if right - left != bottom - top:
        raise SystemExit(f"--box 必须是正方形，现在是 {right - left}×{bottom - top}")
    if not (0 <= left < right <= image.width and 0 <= top < bottom <= image.height):
        raise SystemExit(f"--box 超出了图片范围（图片是 {image.width}×{image.height}）")
    master = image.crop(box).resize((MASTER, MASTER), Image.LANCZOS).convert("RGBA")
    master.putalpha(rounded_mask(MASTER, radius))
    return master


def main() -> None:
    parser = argparse.ArgumentParser(description="用图片生成应用图标")
    parser.add_argument("image", type=Path)
    parser.add_argument("--box", type=int, nargs=4, metavar=("左", "上", "右", "下"))
    parser.add_argument("--radius", type=float, default=0.2, help="圆角半径占边长的比例，默认 0.2")
    args = parser.parse_args()

    master = build(args.image, tuple(args.box) if args.box else None, args.radius)
    target = Path(__file__).resolve().parent.parent / "assets"
    target.mkdir(exist_ok=True)
    master.save(target / "icon.ico", format="ICO", sizes=SIZES)
    master.resize((256, 256), Image.LANCZOS).save(target / "icon.png")
    print(f"已生成 {target / 'icon.ico'}（{len(SIZES)} 个尺寸）和 {target / 'icon.png'}")


if __name__ == "__main__":
    main()
