"""把界面用的三款开源字体裁剪成小文件，放进 wishlog/static/fonts/。

这是开发时用的小工具，软件运行不需要它。需要 fonttools 和 brotli：pip install fonttools brotli

用到的字体（都是 SIL OFL 1.1 开源协议，可以随软件一起发布，协议原文在 wishlog/static/fonts/OFL-*.txt）：
  思源黑体 Noto Sans SC（Regular、Medium）  界面里的正文和按钮
  思源宋体 Noto Serif SC（SemiBold）        大数字（只裁剪出数字和英文符号）
  霞鹜文楷 LXGW WenKai（Bold）              角色名和标题

用法：
    python tools/make_fonts.py 字体源文件夹
源文件夹里需要有：
    NotoSansSC-Regular.otf  NotoSansSC-Medium.otf  NotoSerifSC-SemiBold.otf
    wk/files/lxgwwenkai-bold-subset-*.woff2   （lxgw-wenkai-webfont 这个 npm 包里的分片，合并后再裁剪）

裁剪的范围：英文数字标点 + 中文标点 + GB2312 一级汉字（3755 个最常用字）+ 软件源码里出现过的所有汉字。
范围之外的字（极个别生僻字）界面会自动退回系统字体，不会显示成方块。
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

from fontTools import subset
from fontTools.merge import Merger
from fontTools.ttLib import TTFont

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "wishlog" / "static" / "fonts"

ASCII = set(range(0x20, 0x7F))
PUNCT = (set(range(0x3000, 0x3040)) | set(range(0xFF00, 0xFFF0)) | set(range(0x2010, 0x2028))
         | set(range(0x2030, 0x205F)) | set(range(0x2190, 0x219A)) | {0xB7, 0xD7, 0xF7, 0x2713, 0x2714})
NUMBER_ONLY = ASCII | {0xB7, 0xD7, 0x2013, 0x2014, 0x2026}      # 大数字用的宋体：只要数字、英文和少数符号


def gb2312_level1() -> set:
    chars = set()
    for hi in range(0xB0, 0xD8):
        for lo in range(0xA1, 0xFF):
            try:
                chars.add(ord(bytes([hi, lo]).decode("gb2312")))
            except UnicodeDecodeError:
                pass
    return {c for c in chars if 0x4E00 <= c <= 0x9FFF}


def project_chars() -> set:
    found = set()
    for path in [*(ROOT / "wishlog").rglob("*.py"), *(ROOT / "wishlog").rglob("*.html")]:
        found |= {ord(c) for c in path.read_text(encoding="utf-8") if ord(c) > 0x7F}
    return {c for c in found if 0x2E80 <= c <= 0x9FFF or 0xFF00 <= c <= 0xFFEF or 0x3000 <= c <= 0x303F}


def write_subset(font: TTFont, unicodes: set, target: Path, features: list) -> None:
    options = subset.Options()
    options.flavor = "woff2"
    options.layout_features = features
    options.hinting = False
    options.notdef_outline = True
    options.name_IDs = [1, 2, 3, 4, 6]
    options.drop_tables += ["DSIG", "MATH"]
    subsetter = subset.Subsetter(options)
    subsetter.populate(unicodes=sorted(unicodes))
    subsetter.subset(font)
    OUT.mkdir(parents=True, exist_ok=True)
    options.flavor = "woff2"
    font.flavor = "woff2"
    font.save(target)
    print(f"{target.name}: {target.stat().st_size / 1024:.0f} KB")


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit(__doc__)
    src = Path(sys.argv[1])
    text_chars = ASCII | PUNCT | gb2312_level1() | project_chars()
    print(f"正文字体收录 {len(text_chars)} 个字符")

    for weight in ("Regular", "Medium"):
        write_subset(TTFont(src / f"NotoSansSC-{weight}.otf"), text_chars, OUT / f"NotoSansSC-{weight}.woff2", ["kern", "tnum", "lnum"])

    write_subset(TTFont(src / "NotoSerifSC-SemiBold.otf"), NUMBER_ONLY, OUT / "NotoSerifSC-SemiBold.woff2", ["kern", "tnum", "lnum"])

    slices = sorted((src / "wk" / "files").glob("lxgwwenkai-bold-subset-*.woff2"), key=lambda p: int(re.search(r"(\d+)\.woff2$", p.name).group(1)))
    print(f"合并霞鹜文楷 {len(slices)} 个分片…")
    merged = Merger().merge([str(p) for p in slices])
    write_subset(merged, text_chars, OUT / "LXGWWenKai-Bold.woff2", ["kern"])


if __name__ == "__main__":
    main()
