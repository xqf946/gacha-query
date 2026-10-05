import os
import tempfile
import unittest
from pathlib import Path

from tests.fakes import wish_url
from wishlog import locate


def build_game(root: Path) -> Path:
    data = root / "Genshin Impact Game" / "YuanShen_Data"
    data.mkdir(parents=True)
    return data


def write_cache(data_dir: Path, version: str, content: bytes, mtime: float = None) -> Path:
    cache = data_dir / "webCaches" / version / "Cache" / "Cache_Data" / "data_2"
    cache.parent.mkdir(parents=True)
    cache.write_bytes(content)
    if mtime:
        os.utime(cache, (mtime, mtime))
    return cache


def blob(*urls: str) -> bytes:
    # 模拟缓存文件：二进制杂质 + "1/0/" 前缀 + 链接 + 零字节
    return b"".join(b"\x00\x01\xff junk" + b"1/0/" + u.encode() + b"\x00" * 20 for u in urls)


class LogParsing(unittest.TestCase):
    def test_extracts_path_with_spaces(self):
        text = (
            "Unity log...\n"
            "Warmup file D:/Genshin Impact/Genshin Impact Game/YuanShen_Data/StreamingAssets/x.bin\n"
            "other line D:\\Other\\YuanShen_Data\\foo\n"
            "Warmup file D:/Genshin Impact/Genshin Impact Game/YuanShen_Data/again\n"
        )
        self.assertEqual(
            locate.data_dirs_from_log_text(text),
            ["D:/Genshin Impact/Genshin Impact Game/YuanShen_Data", "D:\\Other\\YuanShen_Data"],
        )

    def test_no_match(self):
        self.assertEqual(locate.data_dirs_from_log_text("nothing here"), [])


class FindDataDir(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / "Genshin Impact"
        self.data = build_game(self.root)

    def test_resolve_accepts_data_dir_game_dir_and_launcher_dir(self):
        for given in (self.data, self.data.parent, self.root, f'"{self.data.parent}"'):
            self.assertEqual(locate.resolve_data_dir(given), self.data, given)

    def test_resolve_unknown_dir(self):
        self.assertIsNone(locate.resolve_data_dir(self.root / "nope"))

    def test_explicit_wrong_dir_is_an_error(self):
        with self.assertRaises(locate.LocateError):
            locate.find_data_dir(explicit=self.root / "nope")

    def test_explicit_dir(self):
        self.assertEqual(locate.find_data_dir(explicit=self.root), self.data)

    def test_falls_back_to_default_install_location(self):
        drive = Path(self.tmp.name) / "D"
        target = drive / "Genshin Impact" / "Genshin Impact Game" / "YuanShen_Data"
        target.mkdir(parents=True)
        found = locate.find_data_dir(log_paths=[drive / "missing.txt"], drive_roots=[drive])
        self.assertEqual(found, target)

    def test_nothing_found(self):
        with self.assertRaises(locate.LocateError):
            locate.find_data_dir(log_paths=[], drive_roots=[])


class Cache(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.data = build_game(Path(self.tmp.name))

    def test_picks_most_recently_written_cache(self):
        write_cache(self.data, "1.0.0.0", b"old", mtime=1_000_000)
        newest = write_cache(self.data, "2.0.0.0", b"new", mtime=2_000_000)
        self.assertEqual(locate.find_cache_file(self.data), newest)

    def test_missing_cache(self):
        with self.assertRaises(locate.LocateError):
            locate.find_cache_file(self.data)

    def test_extract_keeps_only_wish_urls_in_file_order(self):
        a, b = wish_url("AAA"), wish_url("BBB")
        noise = "https://webstatic.mihoyo.com/hk4e/event/index.html?foo=bar"
        self.assertEqual(locate.extract_wish_urls(blob(a, noise, b)), [a, b])

    def test_find_wish_urls_newest_first_and_deduplicated(self):
        # 文件顺序：AAA、BBB、AAA（翻到第 3 页）。靠后的更新，所以 AAA 最新、BBB 次之，AAA 只留一条。
        write_cache(self.data, "2.0.0.0", blob(wish_url("AAA"), wish_url("BBB", page="2"), wish_url("AAA", page="3")))
        urls = locate.find_wish_urls(self.data)
        self.assertEqual(len(urls), 2)
        self.assertIn("authkey=AAA", urls[0])
        self.assertIn("authkey=BBB", urls[1])

    def test_find_wish_urls_prefers_the_later_link(self):
        write_cache(self.data, "2.0.0.0", blob(wish_url("OLD"), wish_url("NEW")))
        self.assertIn("authkey=NEW", locate.find_wish_urls(self.data)[0])

    def test_cache_without_wish_url(self):
        write_cache(self.data, "2.0.0.0", blob("https://webstatic.mihoyo.com/x?y=1"))
        with self.assertRaises(locate.LocateError):
            locate.find_wish_urls(self.data)


if __name__ == "__main__":
    unittest.main()
