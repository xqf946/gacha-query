import re
import unittest
from pathlib import Path

INDEX = Path(__file__).resolve().parent.parent / "wishlog" / "static" / "index.html"


def variables(block: str) -> dict:
    return dict(re.findall(r"(--[\w-]+):\s*([^;]+);", block))


class Appearance(unittest.TestCase):
    """界面有“跟随系统 / 浅色 / 深色”三种外观。深色的变量在样式里要写两遍（跟随系统时用的、手动选深色时用的），不能写岔。"""

    def setUp(self):
        self.html = INDEX.read_text(encoding="utf-8")

    def dark_blocks(self):
        auto = re.search(r'@media \(prefers-color-scheme: dark\) \{\s*:root:not\(\[data-theme="light"\]\) \{(.*?)\}\s*\}', self.html, re.S)
        manual = re.search(r':root\[data-theme="dark"\] \{(.*?)\}', self.html, re.S)
        self.assertTrue(auto and manual, "找不到深色变量块")
        return variables(auto.group(1)), variables(manual.group(1))

    def test_the_two_dark_blocks_are_identical(self):
        auto, manual = self.dark_blocks()
        self.assertGreater(len(auto), 20)
        self.assertEqual(auto, manual)

    def test_dark_overrides_every_colour_the_light_palette_defines(self):
        light = re.search(r":root \{(.*?)\}", self.html, re.S).group(1)
        auto, _ = self.dark_blocks()
        colours = {name for name in variables(light) if not name.startswith("--font-")}      # 字体变量不用跟着深色模式变
        self.assertEqual(colours, set(auto))

    def test_every_bundled_font_is_there_and_its_license_ships_with_it(self):
        import re
        fonts = INDEX.parent / "fonts"
        referenced = re.findall(r'url\("fonts/([^"]+)"\)', self.html)
        self.assertGreaterEqual(len(referenced), 4)
        for name in referenced:
            self.assertGreater((fonts / name).stat().st_size, 5_000, name)
        self.assertTrue(list(fonts.glob("OFL-*Noto*.txt")) and list(fonts.glob("OFL-*WenKai*.txt")))   # 开源协议要求带上原文

    def test_only_weights_the_bundled_fonts_really_have_are_requested(self):
        """黑体只带了 400 和 500 两档，宋体只有 600，文楷只有 700；要了别的粗细，浏览器就会硬把字加粗，看着糊。"""
        import re
        css = self.html.split("</style>")[0]
        for selector, body in re.findall(r"([^{}]+)\{([^{}]*)\}", css):
            if "@font-face" in selector or "monospace" in body:
                continue
            special = "var(--font-num)" in body or "var(--font-name)" in body
            for declaration in body.split(";"):
                found = re.match(r"\s*font-weight:\s*(\w+)", declaration) or re.match(r"\s*font:\s*(\w+)\s", declaration)
                if found and found.group(1).isdigit():
                    weight = int(found.group(1))
                    self.assertTrue(weight in (400, 500) or (special and weight in (600, 700)), f"{selector.strip()} {declaration.strip()}")

    def test_the_picker_and_all_three_choices_exist(self):
        self.assertIn('id="theme"', self.html)
        self.assertIn('const THEMES = ["auto", "light", "dark"]', self.html)
        self.assertIn('call("set_theme"', self.html)


if __name__ == "__main__":
    unittest.main()
