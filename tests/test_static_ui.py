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
        self.assertEqual(set(variables(light)), set(auto))

    def test_the_picker_and_all_three_choices_exist(self):
        self.assertIn('id="theme"', self.html)
        self.assertIn('const THEMES = ["auto", "light", "dark"]', self.html)
        self.assertIn('call("set_theme"', self.html)


if __name__ == "__main__":
    unittest.main()
