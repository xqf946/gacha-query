import ctypes
import os
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest import mock

from tests.fakes import wish_url
from wishlog import locate


def build_game(root: Path, data_dir_name: str = "YuanShen_Data") -> Path:
    data = root / "Genshin Impact Game" / data_dir_name
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
            locate.data_dirs_from_log_text(text, "YuanShen_Data"),
            ["D:/Genshin Impact/Genshin Impact Game/YuanShen_Data", "D:\\Other\\YuanShen_Data"],
        )

    def test_only_the_requested_game_is_picked_out(self):
        text = "x E:/miHoYo Launcher/games/Star Rail Game/StarRail_Data/y\nz D:/G/YuanShen_Data/w"
        self.assertEqual(locate.data_dirs_from_log_text(text, "StarRail_Data"),
                         ["E:/miHoYo Launcher/games/Star Rail Game/StarRail_Data"])

    def test_no_match(self):
        self.assertEqual(locate.data_dirs_from_log_text("nothing here", "YuanShen_Data"), [])


class FindDataDir(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / "Genshin Impact"
        self.data = build_game(self.root)

    def test_resolve_accepts_data_dir_game_dir_and_launcher_dir(self):
        for given in (self.data, self.data.parent, self.root, f'"{self.data.parent}"'):
            self.assertEqual(locate.resolve_data_dir(given, "YuanShen_Data"), self.data, given)

    def test_resolve_unknown_dir(self):
        self.assertIsNone(locate.resolve_data_dir(self.root / "nope", "YuanShen_Data"))

    def test_explicit_wrong_dir_is_an_error(self):
        with self.assertRaises(locate.LocateError):
            locate.find_data_dir("YuanShen_Data", explicit=self.root / "nope")

    def test_explicit_dir(self):
        self.assertEqual(locate.find_data_dir("YuanShen_Data", explicit=self.root), self.data)

    def test_falls_back_to_default_install_location(self):
        drive = Path(self.tmp.name) / "D"
        target = drive / "Genshin Impact" / "Genshin Impact Game" / "YuanShen_Data"
        target.mkdir(parents=True)
        found = locate.find_data_dir("YuanShen_Data", log_paths=[drive / "missing.txt"], drive_roots=[drive])
        self.assertEqual(found, target)

    def test_falls_back_to_the_miHoYo_launcher_install_location(self):
        drive = Path(self.tmp.name) / "E"
        target = drive / "miHoYo Launcher" / "games" / "Genshin Impact Game" / "YuanShen_Data"
        target.mkdir(parents=True)
        found = locate.find_data_dir("YuanShen_Data", log_paths=[drive / "missing.txt"], drive_roots=[drive])
        self.assertEqual(found, target)

    def test_nothing_found(self):
        with self.assertRaises(locate.GameNotFound):
            locate.find_data_dir("YuanShen_Data", log_paths=[], drive_roots=[])

    def test_game_folder_name_does_not_matter_when_searching_install_locations(self):
        drive = Path(self.tmp.name) / "F"
        hsr = drive / "HoYoPlay" / "games" / "Any Name Here" / "StarRail_Data"
        zzz = drive / "Games" / "ZZZ" / "Whatever" / "ZenlessZoneZero_Data"
        hsr.mkdir(parents=True)
        zzz.mkdir(parents=True)
        self.assertEqual(locate.find_data_dir("StarRail_Data", log_paths=[], drive_roots=[drive]), hsr)
        self.assertEqual(locate.find_data_dir("ZenlessZoneZero_Data", log_paths=[], drive_roots=[drive]), zzz)

    def test_system_folders_are_never_searched(self):
        drive = Path(self.tmp.name) / "G"
        hidden = [drive / "Windows" / "System32" / "YuanShen_Data",
                  drive / "Users" / "x" / "YuanShen_Data",
                  drive / "$Recycle.Bin" / "x" / "YuanShen_Data"]
        for path in hidden:
            path.mkdir(parents=True)
        with self.assertRaises(locate.GameNotFound):
            locate.find_data_dir("YuanShen_Data", log_paths=[], drive_roots=[drive])
        real = drive / "MyGames" / "YuanShen_Data"
        real.mkdir(parents=True)
        self.assertEqual(locate.find_data_dir("YuanShen_Data", log_paths=[], drive_roots=[drive]), real)

    def test_the_scan_gives_up_when_its_time_budget_runs_out(self):
        drive = Path(self.tmp.name) / "H"
        (drive / "Games" / "A" / "YuanShen_Data").mkdir(parents=True)
        with self.assertRaises(locate.GameNotFound):     # 预算已经用完：哪怕游戏就在那里，也不再继续扫
            locate.find_data_dir("YuanShen_Data", log_paths=[], drive_roots=[drive], budget=-1)
        self.assertTrue(locate.find_data_dir("YuanShen_Data", log_paths=[], drive_roots=[drive]).is_dir())

    def test_game_not_found_is_a_locate_error_too(self):
        self.assertTrue(issubclass(locate.GameNotFound, locate.LocateError))

    def test_scans_every_miHoYo_log_folder_whatever_it_is_called(self):
        home = Path(self.tmp.name) / "home"
        root = home / "AppData" / "LocalLow" / "miHoYo"
        (root / "崩坏：星穹铁道").mkdir(parents=True)
        (root / "崩坏：星穹铁道" / "Player.log").write_text("x")
        (root / "原神").mkdir()
        (root / "原神" / "output_log.txt").write_text("x")
        (root / "空文件夹").mkdir()
        with mock.patch.dict(os.environ, {"USERPROFILE": str(home)}):
            found = sorted(p.parent.name for p in locate.default_log_paths())
        self.assertEqual(found, ["原神", "崩坏：星穹铁道"])

    def test_paths_are_masked_for_sharing(self):
        self.assertEqual(locate.mask_path(r"C:\Users\张三\AppData\Local\x"), r"C:\Users\<用户>\AppData\Local\x")


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

    def test_links_are_filtered_by_game(self):
        from tests.fakes import game_url
        write_cache(self.data, "2.0.0.0", blob(game_url("hk4e", "GENSHIN"), game_url("hkrpg", "STARRAIL")))
        only_hsr = locate.find_wish_urls(self.data, "hkrpg")
        self.assertEqual(len(only_hsr), 1)
        self.assertIn("authkey=STARRAIL", only_hsr[0])
        with self.assertRaises(locate.LocateError):
            locate.find_wish_urls(self.data, "nap")

    def test_cache_without_wish_url(self):
        write_cache(self.data, "2.0.0.0", blob("https://webstatic.mihoyo.com/x?y=1"))
        with self.assertRaises(locate.LocateError):
            locate.find_wish_urls(self.data)


class ReadingTheCache(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.data = build_game(Path(self.tmp.name))

    def test_reads_a_normal_file(self):
        path = Path(self.tmp.name) / "plain.bin"
        path.write_bytes(b"\x00abc" * 500_000)   # 2 MB，要跨越多次分块读取
        self.assertEqual(locate.read_file_shared(path), path.read_bytes())

    def test_missing_file_is_an_oserror(self):
        with self.assertRaises(OSError):
            locate.read_file_shared(Path(self.tmp.name) / "nope.bin")

    def test_sharing_violation_tells_the_user_to_quit_the_game(self):
        error = OSError("busy")
        error.winerror = 32
        text = locate.explain_read_failure(Path("C:/x/data_2"), error)
        self.assertIn("32", text)
        self.assertIn("退出游戏", text)
        self.assertIn("data_2", text)

    def test_access_denied_mentions_running_as_administrator(self):
        error = PermissionError("denied")
        error.winerror = 5
        text = locate.explain_read_failure(Path("C:/x/data_2"), error)
        self.assertIn("5", text)
        self.assertIn("管理员", text)

    def test_unreadable_cache_surfaces_a_helpful_locate_error(self):
        write_cache(self.data, "5.0.0.0", blob(wish_url("AAA")))
        with mock.patch.object(locate, "read_file_shared", side_effect=PermissionError("denied")):
            with self.assertRaises(locate.LocateError) as ctx:
                locate.find_wish_urls(self.data)
        self.assertIn("管理员", str(ctx.exception))


@unittest.skipUnless(os.name == "nt", "文件共享冲突是 Windows 才有的行为")
class WindowsFileLocking(unittest.TestCase):
    """模拟游戏开着缓存文件的各种方式（真机上读缓存报过 Permission denied）。"""

    GENERIC_READ, GENERIC_WRITE, DELETE = 0x80000000, 0x40000000, 0x00010000
    SHARE_READ, SHARE_WRITE = 0x1, 0x2

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "data_2"
        self.path.write_bytes(b"cache contents " * 1000)

    @contextmanager
    def held_open_like_the_game(self, access, share):
        from ctypes import wintypes
        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        k32.CreateFileW.argtypes = [
            wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID,
            wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE,
        ]
        k32.CreateFileW.restype = wintypes.HANDLE
        k32.CloseHandle.argtypes = [wintypes.HANDLE]
        handle = k32.CreateFileW(str(self.path), access, share, None, 3, 0x80, None)
        self.assertNotIn(handle, (None, ctypes.c_void_p(-1).value), ctypes.WinError(ctypes.get_last_error()))
        try:
            yield
        finally:
            k32.CloseHandle(handle)

    def test_reads_a_file_the_game_holds_open_with_delete_access(self):
        access = self.GENERIC_READ | self.GENERIC_WRITE | self.DELETE
        with self.held_open_like_the_game(access, self.SHARE_READ | self.SHARE_WRITE):
            self.assertEqual(locate.read_file_shared(self.path), b"cache contents " * 1000)

    def test_plain_python_open_is_refused_in_that_situation(self):
        # 这条记录“为什么不能直接用 open()”：同样的情形下，Python 自带的 open() 会被拒绝
        access = self.GENERIC_READ | self.GENERIC_WRITE | self.DELETE
        with self.held_open_like_the_game(access, self.SHARE_READ | self.SHARE_WRITE):
            with self.assertRaises(PermissionError):
                self.path.read_bytes()

    def test_a_file_locked_exclusively_gives_the_sharing_violation_message(self):
        with self.held_open_like_the_game(self.GENERIC_READ | self.GENERIC_WRITE, 0):
            with self.assertRaises(OSError) as ctx:
                locate.read_file_shared(self.path)
        self.assertEqual(ctx.exception.winerror, 32)
        self.assertIn("退出游戏", locate.explain_read_failure(self.path, ctx.exception))


if __name__ == "__main__":
    unittest.main()
