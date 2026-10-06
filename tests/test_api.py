"""界面调用的后台方法：用假官方接口、假的游戏缓存文件、假的系统对话框跑完整流程。"""

import tempfile
import threading
import time
import unittest
from pathlib import Path

from tests.fakes import FakeApi, VALID_KEY, make_records, wish_url
from tests.test_locate import blob, build_game, write_cache
from wishlog import __version__
from wishlog.api import Api
from wishlog.client import Client
from wishlog.job import SyncJob
from wishlog.store import Store


class FakeDialogs:
    def __init__(self):
        self.save_to = None
        self.folder = None
        self.save_calls = []

    def save_file(self, filename, file_types):
        self.save_calls.append((filename, file_types))
        return self.save_to

    def pick_folder(self):
        return self.folder


class ApiTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

        self.fake = FakeApi({"301": make_records("301", 45), "302": make_records("302", 8, seed=3)})
        self.gate = None  # 需要让接口卡住时，换成一个 threading.Event
        self.data_dir = build_game(self.root)
        # 缓存里放两条链接：靠前的是过期的旧链接，靠后（最新）的有效
        write_cache(self.data_dir, "5.0.0.0", blob(wish_url("OLDEXPIRED"), wish_url(VALID_KEY)))

        def fetch(url):
            if self.gate:
                self.gate.wait(10)
            return self.fake.fetch(url)

        self.store = Store(self.root / "data")
        job = SyncJob(
            self.store,
            client_factory=lambda: Client(fetch=fetch, sleep=lambda s: None),
            default_game_dir=str(self.data_dir),
        )
        self.dialogs = FakeDialogs()
        self.opened = []
        self.api = Api(self.store, job, self.root / "data", self.dialogs, opener=self.opened.append)

    def sync_and_wait(self, **kwargs):
        self.api.start_sync(**kwargs)
        for _ in range(200):
            status = self.api.sync_status()
            if status["state"] != "running":
                return status
            time.sleep(0.05)
        self.fail("同步一直没有结束")

    # ---- 只暴露该暴露的 ----
    def test_only_the_intended_methods_are_public(self):
        public = {n for n in dir(self.api) if not n.startswith("_")}
        self.assertEqual(public, {
            "get_state", "get_wishes", "app_info", "start_sync", "sync_status",
            "export_records", "pick_game_dir", "open_data_dir",
        })

    # ---- 完整流程 ----
    def test_full_flow_from_game_cache_to_stats(self):
        self.assertEqual(self.api.get_state()["uids"], [])

        status = self.sync_and_wait()
        self.assertEqual((status["state"], status["uid"]), ("done", "100000001"))
        self.assertIn("53", status["message"])    # 45 + 8 条
        self.assertEqual(self.api.get_state()["uids"], ["100000001"])

        wishes = self.api.get_wishes("100000001")
        totals = {p["key"]: p["total"] for p in wishes["pools"]}
        self.assertEqual(totals, {"301": 45, "302": 8, "500": 0, "200": 0, "100": 0})

        status = self.sync_and_wait()    # 再更新一次：没有新记录
        self.assertEqual(status["state"], "done")
        self.assertIn("没有新记录", status["message"])

    def test_pasting_a_url_manually(self):
        status = self.sync_and_wait(url=wish_url(VALID_KEY, page="2"), game_dir="/does/not/exist")
        self.assertEqual(status["state"], "done")

    def test_expired_link_reports_a_helpful_error(self):
        self.fake.valid_key = "SOMETHINGELSE"
        status = self.sync_and_wait()
        self.assertEqual(status["state"], "error")
        self.assertIn("历史记录", status["message"])

    def test_missing_game_gives_a_helpful_error(self):
        status = self.sync_and_wait(game_dir="/does/not/exist")
        self.assertEqual(status["state"], "error")
        self.assertIn("YuanShen_Data", status["message"])

    def test_pasted_link_for_a_foreign_host_is_refused_without_any_request(self):
        status = self.sync_and_wait(url=wish_url(host="evil.example.com"))
        self.assertEqual(status["state"], "error")
        self.assertEqual(self.fake.calls, [])    # authkey 没有被发给任何地方

    def test_second_start_while_running_does_not_start_another(self):
        self.gate = threading.Event()
        first = self.api.start_sync()
        second = self.api.start_sync()
        self.assertTrue(first["started"])
        self.assertFalse(second["started"])
        self.assertEqual(second["status"]["state"], "running")
        self.gate.set()
        for _ in range(200):
            if self.api.sync_status()["state"] != "running":
                break
            time.sleep(0.05)
        self.assertEqual(self.api.sync_status()["state"], "done")

    # ---- 查询 ----
    def test_unknown_uid_and_path_traversal(self):
        self.sync_and_wait()
        for uid in ("999", "../../etc/passwd", "abc", "", None):
            self.assertIn("error", self.api.get_wishes(uid), uid)
            self.assertIn("error", self.api.export_records(uid, "csv"), uid)

    def test_app_info(self):
        info = self.api.app_info()
        self.assertEqual(info["version"], __version__)
        self.assertEqual(info["data_dir"], str(self.root / "data"))

    # ---- 导出 ----
    def test_export_csv_writes_the_file_chosen_in_the_save_dialog(self):
        self.sync_and_wait()
        target = self.root / "out.csv"
        self.dialogs.save_to = str(target)

        result = self.api.export_records("100000001", "csv")

        self.assertEqual(result, {"ok": True, "path": str(target)})
        self.assertEqual(self.dialogs.save_calls[0][0], "genshin_wishes_100000001.csv")
        raw = target.read_bytes()
        self.assertTrue(raw.startswith("\ufeff".encode()))        # 带 BOM，Excel 能正确显示中文
        lines = raw.decode("utf-8-sig").strip().splitlines()
        self.assertEqual(len(lines), 1 + 53)
        self.assertTrue(lines[0].startswith("时间,卡池"))

    def test_export_json(self):
        import json
        self.sync_and_wait()
        target = self.root / "out.json"
        self.dialogs.save_to = str(target)
        self.assertEqual(self.api.export_records("100000001", "json")["ok"], True)
        doc = json.loads(target.read_text(encoding="utf-8"))
        self.assertEqual(len(doc["records"]), 53)
        self.assertEqual(doc["records"][0]["uid"], "100000001")

    def test_export_cancelled_in_the_dialog_writes_nothing(self):
        self.sync_and_wait()
        self.dialogs.save_to = None
        self.assertEqual(self.api.export_records("100000001", "csv"), {"cancelled": True})

    def test_export_to_an_unwritable_place_reports_the_error(self):
        self.sync_and_wait()
        self.dialogs.save_to = str(self.root / "no_such_dir" / "out.csv")
        self.assertIn("保存失败", self.api.export_records("100000001", "csv")["error"])

    def test_export_rejects_unknown_format(self):
        self.sync_and_wait()
        self.assertIn("error", self.api.export_records("100000001", "exe"))

    # ---- 对话框和文件夹 ----
    def test_pick_game_dir(self):
        self.dialogs.folder = r"D:\Genshin Impact\Genshin Impact Game"
        self.assertEqual(self.api.pick_game_dir(), {"path": r"D:\Genshin Impact\Genshin Impact Game"})
        self.dialogs.folder = None    # 用户在对话框里点了取消
        self.assertEqual(self.api.pick_game_dir(), {"path": None})

    def test_open_data_dir_creates_it_and_opens_it(self):
        self.assertFalse((self.root / "data").exists())
        self.assertEqual(self.api.open_data_dir(), {"ok": True})
        self.assertTrue((self.root / "data").is_dir())
        self.assertEqual(self.opened, [self.root / "data"])


if __name__ == "__main__":
    unittest.main()
