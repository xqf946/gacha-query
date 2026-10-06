"""界面调用的后台方法：用假官方接口、假的游戏缓存文件和日志、假的系统对话框跑完整流程（四个游戏）。"""

import json
import tempfile
import threading
import time
import unittest
from pathlib import Path

from tests.fakes import (
    VALID_KEY, WUWA_URL, FakeApi, FakeWuwaApi, encrypt_client_log, game_url, make_records,
    wish_url, wuwa_item, wuwa_log_line,
)
from tests.test_locate import blob, build_game, write_cache
from wishlog import __version__
from wishlog.api import Api
from wishlog.client import Client
from wishlog.games import GAMES, GENSHIN, HSR, ZZZ
from wishlog.games.wuwa import WuwaClient
from wishlog.job import SyncJob
from wishlog.settings import Settings
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
        self.data = self.root / "data"

        # 每个游戏一个假接口
        self.fakes = {
            "genshin": FakeApi({"301": make_records("301", 45), "302": make_records("302", 8, seed=3)}),
            "hsr": FakeApi({"11": make_records("11", 14, seed=5), "12": make_records("12", 6, seed=6)}),
            "zzz": FakeApi({"2": make_records("2", 9, seed=7, ranks=("4", "3", "2"))}),
        }
        self.wuwa = FakeWuwaApi({"1": [wuwa_item("今汐", 5, "2026-01-02 10:00:00"), wuwa_item("白芷", 4, "2026-01-01 10:00:00")]})
        self.gate = None  # 需要让接口卡住时，换成一个 threading.Event

        def gated(fetch):
            def call(*args):
                if self.gate:
                    self.gate.wait(10)
                return fetch(*args)
            return call

        def client_for(game):
            if game.key == "wuwa":
                return WuwaClient(post=gated(self.wuwa.post), sleep=lambda s: None)
            return Client(game.api, fetch=gated(self.fakes[game.key].fetch), sleep=lambda s: None)

        # 每个游戏在“电脑上”的安装：缓存里放过期的旧链接 + 有效的新链接
        self.dirs = {}
        for game, biz in ((GENSHIN, "hk4e"), (HSR, "hkrpg"), (ZZZ, "nap")):
            install = build_game(self.root / game.key, game.data_dir_name)
            write_cache(install, "5.0.0.0", blob(game_url(biz, "OLDEXPIRED"), game_url(biz, VALID_KEY)))
            self.dirs[game.key] = str(install)
        log = self.root / "wuwa" / "Wuthering Waves" / "Wuthering Waves Game" / "Client" / "Saved" / "Logs" / "Client.log"
        log.parent.mkdir(parents=True)
        log.write_bytes(encrypt_client_log(wuwa_log_line(WUWA_URL)))
        self.dirs["wuwa"] = str(self.root / "wuwa")

        self.settings = Settings(self.data / "settings.json")
        for key, path in self.dirs.items():
            self.settings.set_game_dir(key, path)   # 等于用户以前选过目录；“更新”就能找到这些假安装
        self.job = SyncJob(self.data, GAMES, self.settings, client_factory=client_for)
        self.dialogs = FakeDialogs()
        self.opened = []
        self.api = Api(self.data, GAMES, self.job, self.settings, self.dialogs, opener=self.opened.append)

    def wait_done(self):
        for _ in range(300):
            status = self.api.sync_status()
            if status["state"] != "running":
                return status
            time.sleep(0.03)
        self.fail("同步一直没有结束")

    def sync_and_wait(self, game="genshin", **kwargs):
        self.api.start_sync(game, **kwargs)
        return self.wait_done()

    # ---- 只暴露该暴露的 ----
    def test_only_the_intended_methods_are_public(self):
        public = {n for n in dir(self.api) if not n.startswith("_")}
        self.assertEqual(public, {
            "get_state", "get_wishes", "app_info", "start_sync", "sync_status",
            "export_records", "pick_game_dir", "open_data_dir", "diagnose",
        })

    # ---- 状态 ----
    def test_state_lists_all_games_in_order_with_their_accounts(self):
        state = self.api.get_state()
        self.assertEqual([g["key"] for g in state["games"]], ["genshin", "hsr", "zzz", "wuwa"])
        self.assertTrue(all(g["uids"] == [] for g in state["games"]))
        meta = {g["key"]: g for g in state["games"]}
        self.assertEqual(meta["zzz"]["top_name"], "S 级")
        self.assertEqual(meta["hsr"]["currency"], "星琼")
        self.assertTrue(meta["genshin"]["hint"])

    # ---- 单个游戏的完整流程 ----
    def test_genshin_full_flow_from_game_cache_to_stats(self):
        status = self.sync_and_wait("genshin")
        self.assertEqual((status["state"], status["uid"], status["game"]), ("done", "100000001", "genshin"))
        self.assertIn("53", status["message"])    # 45 + 8 条
        wishes = self.api.get_wishes("genshin", "100000001")
        totals = {p["key"]: p["total"] for p in wishes["pools"]}
        self.assertEqual(totals, {"301": 45, "302": 8, "500": 0, "200": 0, "100": 0})
        self.assertEqual(wishes["game"]["key"], "genshin")
        again = self.sync_and_wait("genshin")
        self.assertIn("没有新记录", again["message"])

    def test_each_game_is_stored_separately(self):
        for key in ("genshin", "hsr", "zzz", "wuwa"):
            self.assertEqual(self.sync_and_wait(key)["state"], "done", key)
        games = {g["key"]: g["uids"] for g in self.api.get_state()["games"]}
        self.assertEqual(games, {"genshin": ["100000001"], "hsr": ["100000001"], "zzz": ["100000001"], "wuwa": ["100000001"]})
        for key, total in (("genshin", 53), ("hsr", 20), ("zzz", 9), ("wuwa", 2)):
            pools = self.api.get_wishes(key, "100000001")["pools"]
            self.assertEqual(sum(p["total"] for p in pools), total, key)
        for key in ("genshin", "hsr", "zzz", "wuwa"):
            self.assertTrue((self.data / key / "100000001.json").is_file())

    def test_the_same_uid_in_two_games_does_not_mix(self):
        self.sync_and_wait("genshin")
        self.sync_and_wait("hsr")
        genshin = sum(p["total"] for p in self.api.get_wishes("genshin", "100000001")["pools"])
        hsr = sum(p["total"] for p in self.api.get_wishes("hsr", "100000001")["pools"])
        self.assertEqual((genshin, hsr), (53, 20))     # 同一个 UID 在两个游戏里，各算各的

    def test_zzz_shows_s_a_b_tiers(self):
        self.sync_and_wait("zzz")
        pools = {p["key"]: p for p in self.api.get_wishes("zzz", "100000001")["pools"]}
        marks = {r["mark"] for r in pools["2"]["records"]}
        self.assertTrue(marks <= {"S", "A", "B"} and marks)

    def test_pasting_a_url_manually(self):
        status = self.sync_and_wait("genshin", url=wish_url(VALID_KEY, page="2"), game_dir="/does/not/exist")
        self.assertEqual(status["state"], "done")

    def test_a_pasted_link_for_the_wrong_game_is_refused_without_any_request(self):
        status = self.sync_and_wait("hsr", url=wish_url(VALID_KEY))     # 原神的链接粘给崩铁
        self.assertEqual(status["state"], "error")
        self.assertEqual(self.fakes["hsr"].calls, [])

    def test_pasted_link_for_a_foreign_host_is_refused_without_any_request(self):
        status = self.sync_and_wait("genshin", url=wish_url(host="evil.example.com"))
        self.assertEqual(status["state"], "error")
        self.assertEqual(self.fakes["genshin"].calls, [])    # authkey 没有被发给任何地方

    def test_expired_link_reports_a_helpful_error(self):
        self.fakes["genshin"].valid_key = "SOMETHINGELSE"
        status = self.sync_and_wait("genshin")
        self.assertEqual(status["state"], "error")
        self.assertIn("历史记录", status["message"])

    def test_choosing_a_game_directory_by_hand_is_remembered_once_it_works(self):
        self.settings.set_game_dir("genshin", "")
        status = self.sync_and_wait("genshin", game_dir=self.dirs["genshin"])
        self.assertEqual(status["state"], "done")
        self.assertEqual(Settings(self.data / "settings.json").game_dir("genshin"), self.dirs["genshin"])

    def test_a_wrong_hand_picked_directory_is_not_remembered(self):
        self.settings.set_game_dir("genshin", "")
        status = self.sync_and_wait("genshin", game_dir="/does/not/exist")
        self.assertEqual(status["state"], "error")
        self.assertIn("YuanShen_Data", status["message"])
        self.assertEqual(self.settings.game_dir("genshin"), "")

    def test_a_remembered_directory_that_no_longer_exists_falls_back_to_auto_detection(self):
        self.settings.set_game_dir("genshin", "/the/game/moved")
        status = self.sync_and_wait("genshin")
        # 自动查找在这台测试机上找不到游戏，但错误应该是“没找到游戏”，而不是被旧目录误导
        self.assertEqual(status["state"], "error")
        self.assertNotIn("/the/game/moved", status["message"])

    def test_second_start_while_running_does_not_start_another(self):
        self.gate = threading.Event()
        first = self.api.start_sync("genshin")
        second = self.api.start_sync("genshin")
        self.assertTrue(first["started"])
        self.assertFalse(second["started"])
        self.assertEqual(second["status"]["state"], "running")
        self.gate.set()
        self.assertEqual(self.wait_done()["state"], "done")

    def test_unknown_game_is_rejected(self):
        self.assertFalse(self.api.start_sync("starcraft")["started"])

    # ---- 更新全部 ----
    def test_update_all_syncs_every_installed_game(self):
        status = self.sync_and_wait("all")
        self.assertEqual(status["state"], "done")
        self.assertFalse(status["warn"])
        for name in ("原神", "崩坏：星穹铁道", "绝区零", "鸣潮"):
            self.assertIn(name, status["message"])
        self.assertEqual(sum(1 for r in status["results"] if r["state"] == "done"), 4)

    def test_update_all_silently_skips_games_that_are_not_installed(self):
        self.settings.set_game_dir("zzz", "")
        self.settings.set_game_dir("wuwa", "")     # 没有记住的目录，自动查找在测试机上找不到
        status = self.sync_and_wait("all")
        states = {r["game"]: r["state"] for r in status["results"]}
        self.assertEqual(states["genshin"], "done")
        self.assertEqual(states["hsr"], "done")
        self.assertEqual(states["zzz"], "skipped")
        self.assertEqual(states["wuwa"], "skipped")
        self.assertEqual(status["state"], "done")
        self.assertFalse(status["warn"])           # 没装不算错误
        self.assertIn("绝区零：没有检测到这个游戏", status["message"])

    def test_update_all_one_game_failing_does_not_stop_the_others_and_is_flagged(self):
        self.fakes["hsr"].valid_key = "SOMETHINGELSE"
        status = self.sync_and_wait("all")
        states = {r["game"]: r["state"] for r in status["results"]}
        self.assertEqual(states, {"genshin": "done", "hsr": "error", "zzz": "done", "wuwa": "done"})
        self.assertEqual(status["state"], "done")
        self.assertTrue(status["warn"])            # 界面据此显示成黄色提示
        self.assertIn("崩坏：星穹铁道：", status["message"])

    def test_update_all_with_nothing_installed_is_an_error(self):
        for key in self.dirs:
            self.settings.set_game_dir(key, "")
        status = self.sync_and_wait("all")
        self.assertEqual(status["state"], "error")

    # ---- 查询 ----
    def test_unknown_account_and_path_traversal(self):
        self.sync_and_wait("genshin")
        for game, uid in (("genshin", "999"), ("genshin", "../../etc/passwd"), ("genshin", "abc"),
                          ("genshin", ""), ("genshin", None), ("nogame", "100000001"), ("../x", "100000001")):
            self.assertIn("error", self.api.get_wishes(game, uid), (game, uid))
            self.assertIn("error", self.api.export_records(game, uid, "csv"), (game, uid))

    def test_app_info(self):
        info = self.api.app_info()
        self.assertEqual(info["version"], __version__)
        self.assertEqual(info["data_dir"], str(self.data))

    # ---- 导出 ----
    def test_export_csv_writes_the_file_chosen_in_the_save_dialog(self):
        self.sync_and_wait("genshin")
        target = self.root / "out.csv"
        self.dialogs.save_to = str(target)
        result = self.api.export_records("genshin", "100000001", "csv")
        self.assertEqual(result, {"ok": True, "path": str(target)})
        self.assertEqual(self.dialogs.save_calls[0][0], "genshin_wishes_100000001.csv")
        raw = target.read_bytes()
        self.assertTrue(raw.startswith("\ufeff".encode()))        # 带 BOM，Excel 能正确显示中文
        lines = raw.decode("utf-8-sig").strip().splitlines()
        self.assertEqual(len(lines), 1 + 53)
        self.assertTrue(lines[0].startswith("时间,卡池"))

    def test_export_names_the_file_after_the_game_and_uses_that_games_pool_names(self):
        self.sync_and_wait("wuwa")
        target = self.root / "wuwa.json"
        self.dialogs.save_to = str(target)
        self.assertTrue(self.api.export_records("wuwa", "100000001", "json")["ok"])
        self.assertEqual(self.dialogs.save_calls[0][0], "wuwa_wishes_100000001.json")
        doc = json.loads(target.read_text(encoding="utf-8"))
        self.assertEqual((doc["game"], doc["game_name"]), ("wuwa", "鸣潮"))
        self.assertEqual({r["pool"] for r in doc["records"]}, {"角色活动唤取"})

    def test_export_cancelled_in_the_dialog_writes_nothing(self):
        self.sync_and_wait("genshin")
        self.dialogs.save_to = None
        self.assertEqual(self.api.export_records("genshin", "100000001", "csv"), {"cancelled": True})

    def test_export_to_an_unwritable_place_reports_the_error(self):
        self.sync_and_wait("genshin")
        self.dialogs.save_to = str(self.root / "no_such_dir" / "out.csv")
        self.assertIn("保存失败", self.api.export_records("genshin", "100000001", "csv")["error"])

    def test_export_rejects_unknown_format(self):
        self.sync_and_wait("genshin")
        self.assertIn("error", self.api.export_records("genshin", "100000001", "exe"))

    # ---- 对话框和文件夹 ----
    def test_pick_game_dir(self):
        self.dialogs.folder = r"D:\Games\Star Rail"
        self.assertEqual(self.api.pick_game_dir(), {"path": r"D:\Games\Star Rail"})
        self.dialogs.folder = None    # 用户在对话框里点了取消
        self.assertEqual(self.api.pick_game_dir(), {"path": None})

    def test_open_data_dir_creates_it_and_opens_it(self):
        shutil_target = self.root / "fresh"
        api = Api(shutil_target, GAMES, self.job, self.settings, self.dialogs, opener=self.opened.append)
        self.assertFalse(shutil_target.exists())
        self.assertEqual(api.open_data_dir(), {"ok": True})
        self.assertTrue(shutil_target.is_dir())
        self.assertEqual(self.opened, [shutil_target])

    # ---- 环境检测 ----
    def test_diagnose_covers_every_game_and_never_leaks_secrets(self):
        report = self.api.diagnose()["report"]
        for name in ("原神", "崩坏：星穹铁道", "绝区零", "鸣潮"):
            self.assertIn(f"【{name}】", report)
        self.assertIn(__version__, report)
        for secret in ("VALIDKEY", "OLDEXPIRED", "TOKEN123", "POOL456", "76402e5b20be2c39f095a152090afddc"):
            self.assertNotIn(secret, report)       # 只描述链接的结构，绝不带出任何值
        self.assertGreaterEqual(report.count("祈愿链接：找到"), 3)
        self.assertIn("唤取记录链接：找到", report)

    def test_diagnose_survives_a_game_whose_check_blows_up(self):
        original = ZZZ.diagnose
        ZZZ.diagnose = lambda game_dir: (_ for _ in ()).throw(RuntimeError("boom"))
        try:
            report = self.api.diagnose()["report"]
        finally:
            ZZZ.diagnose = original
        self.assertIn("检测时出错：RuntimeError: boom", report)
        self.assertIn("【鸣潮】", report)          # 后面的游戏照样检测


if __name__ == "__main__":
    unittest.main()
