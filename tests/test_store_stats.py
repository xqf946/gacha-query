import json
import tempfile
import unittest
from pathlib import Path

from tests.fakes import make_records
from wishlog.games import GENSHIN, HSR, WUWA, ZZZ
from wishlog.settings import Settings
from wishlog.stats import analyze, analyze_pool
from wishlog.store import Store, migrate_legacy


def rec(n, gacha_type, rank, name="某物", kind="角色"):
    return {"id": str(1000 + n), "gacha_type": gacha_type, "item_id": "", "count": "1",
            "time": f"2026-01-01 00:00:{n:02d}", "name": name, "item_type": kind, "rank_type": str(rank)}


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = Store(Path(self.tmp.name) / "data")  # 目录还不存在，应自动创建

    def test_merge_is_idempotent_sorted_and_trimmed(self):
        records = make_records("301", 10)
        self.assertEqual(self.store.merge("100000001", records[::-1]), 10)
        self.assertEqual(self.store.merge("100000001", records), 0)
        saved = self.store.load("100000001")["records"]
        self.assertEqual([r["id"] for r in saved], [r["id"] for r in records])
        self.assertNotIn("uid", saved[0])
        self.assertNotIn("lang", saved[0])

    def test_uids_and_unknown_uid(self):
        self.assertEqual(self.store.uids(), [])
        self.assertEqual(self.store.load("123")["records"], [])
        self.store.merge("200000002", make_records("301", 1))
        self.store.merge("100000001", make_records("301", 1))
        self.assertEqual(self.store.uids(), ["100000001", "200000002"])

    def test_rejects_path_traversal_uids(self):
        for bad in ("../evil", "12/34", "", "abc", "1" * 30):
            with self.assertRaises(ValueError, msg=bad):
                self.store.load(bad)

    def test_file_is_valid_json_and_no_temp_file_left_behind(self):
        self.store.merge("100000001", make_records("301", 3))
        files = sorted(p.name for p in (Path(self.tmp.name) / "data").iterdir())
        self.assertEqual(files, ["100000001.json"])
        json.loads((Path(self.tmp.name) / "data" / "100000001.json").read_text(encoding="utf-8"))


class MigrateLegacy(unittest.TestCase):
    """v0.2.x 的记录直接放在 data 下；多游戏之后要搬进 genshin 子文件夹。这是唯一会动到用户现有数据的改动。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.data = Path(self.tmp.name) / "data"
        Store(self.data).merge("100000001", make_records("301", 12))   # 模拟旧版本留下的数据
        Store(self.data).merge("200000002", make_records("301", 3))
        self.original = {p.name: p.read_bytes() for p in self.data.glob("*.json")}

    def test_records_move_into_the_genshin_folder_unchanged(self):
        self.assertEqual(migrate_legacy(self.data), 2)
        for name, content in self.original.items():
            self.assertEqual((self.data / "genshin" / name).read_bytes(), content)   # 一个字节都没变
        self.assertEqual(Store(self.data / "genshin").uids(), ["100000001", "200000002"])
        self.assertEqual(len(Store(self.data / "genshin").load("100000001")["records"]), 12)

    def test_a_backup_of_the_old_files_is_kept(self):
        migrate_legacy(self.data)
        backup = self.data / "backup-before-multi-game"
        for name, content in self.original.items():
            self.assertEqual((backup / name).read_bytes(), content)
        self.assertEqual(list(self.data.glob("*.json")), [])   # 原位置不再有旧文件

    def test_running_it_twice_is_harmless(self):
        migrate_legacy(self.data)
        self.assertEqual(migrate_legacy(self.data), 0)
        self.assertEqual(len(Store(self.data / "genshin").load("100000001")["records"]), 12)

    def test_never_overwrites_data_already_in_the_new_location(self):
        Store(self.data / "genshin").merge("100000001", make_records("301", 40, seed=9))
        newer = (self.data / "genshin" / "100000001.json").read_bytes()
        migrate_legacy(self.data)
        self.assertEqual((self.data / "genshin" / "100000001.json").read_bytes(), newer)

    def test_nothing_to_do_on_a_fresh_install(self):
        empty = Path(self.tmp.name) / "fresh"
        self.assertEqual(migrate_legacy(empty), 0)
        self.assertFalse(empty.exists())

    def test_settings_file_is_not_mistaken_for_a_record_file(self):
        Settings(self.data / "settings.json").set_game_dir("wuwa", "D:/x")
        migrate_legacy(self.data)
        self.assertTrue((self.data / "settings.json").is_file())


class SettingsTests(unittest.TestCase):
    def test_remembers_directories_per_game(self):
        with tempfile.TemporaryDirectory() as tmp:
            s = Settings(Path(tmp) / "sub" / "settings.json")
            self.assertEqual(s.game_dir("wuwa"), "")
            s.set_game_dir("wuwa", r"E:\Wuthering Waves")
            s.set_game_dir("hsr", r"E:\Star Rail")
            self.assertEqual(Settings(Path(tmp) / "sub" / "settings.json").game_dir("wuwa"), r"E:\Wuthering Waves")
            self.assertEqual(s.game_dir("hsr"), r"E:\Star Rail")

    def test_a_corrupt_file_is_treated_as_empty(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "settings.json"
            path.write_text("{not json", encoding="utf-8")
            self.assertEqual(Settings(path).game_dir("wuwa"), "")
            Settings(path).set_game_dir("wuwa", "D:/x")   # 也能直接覆盖修复
            self.assertEqual(Settings(path).game_dir("wuwa"), "D:/x")


class StatsTests(unittest.TestCase):
    def pool(self, game, key, records):
        return analyze_pool(game, next(p for p in game.pools if p.key == key), records)

    def test_pity_counting(self):
        # 第 5、8 抽出五星；第 3 抽出四星；之后又抽了 2 个三星
        ranks = [3, 3, 4, 3, 5, 3, 3, 5, 3, 3]
        pool = self.pool(GENSHIN, "301", [rec(i, "301", r) for i, r in enumerate(ranks, 1)])
        by_n = {e["n"]: e for e in pool["records"]}
        self.assertEqual(by_n[5]["pity"], 5)
        self.assertEqual(by_n[8]["pity"], 3)
        self.assertEqual(by_n[3]["pity"], 3)          # 四星的垫抽
        self.assertEqual(pool["current_pity_top"], 2)
        self.assertEqual(pool["current_pity_second"], 2)    # 五星出现后四星计数也清零
        self.assertEqual(pool["avg_pity_top"], 4.0)
        self.assertEqual((pool["min_pity_top"], pool["max_pity_top"]), (3, 5))
        self.assertEqual((pool["top_count"], pool["second_count"], pool["other_count"]), (2, 1, 7))
        self.assertEqual(pool["cost"], 10 * 160)
        self.assertEqual([e["n"] for e in pool["records"]][:3], [10, 9, 8])   # 从新到旧

    def test_tiers_and_marks(self):
        pool = self.pool(GENSHIN, "301", [rec(1, "301", 3), rec(2, "301", 4), rec(3, "301", 5)])
        tiers = {e["n"]: (e["tier"], e["mark"]) for e in pool["records"]}
        self.assertEqual(tiers, {1: ("other", "★★★"), 2: ("second", "★★★★"), 3: ("top", "★★★★★")})

    def test_zzz_uses_s_a_b_tiers_with_s_as_the_top(self):
        # 绝区零：品级 4=S、3=A、2=B。S 是最高档，不是 5
        pool = self.pool(ZZZ, "2", [rec(1, "2", 2), rec(2, "2", 3), rec(3, "2", 2), rec(4, "2", 4), rec(5, "2", 2)])
        self.assertEqual((pool["top_count"], pool["second_count"], pool["other_count"]), (1, 1, 3))
        by_n = {e["n"]: e for e in pool["records"]}
        self.assertEqual((by_n[4]["tier"], by_n[4]["mark"], by_n[4]["pity"]), ("top", "S", 4))
        self.assertEqual(by_n[2]["mark"], "A")
        self.assertEqual(pool["current_pity_top"], 1)

    def test_empty_pool(self):
        pool = self.pool(GENSHIN, "301", [])
        self.assertEqual((pool["total"], pool["current_pity_top"], pool["avg_pity_top"]), (0, 0, None))

    def test_groups_by_pool_and_folds_gacha_type_400_into_301(self):
        records = [rec(1, "301", 3), rec(2, "400", 5, "胡桃"), rec(3, "302", 3, "黑缨枪", "武器"),
                   rec(4, "200", 3), rec(5, "999", 5)]   # 999 是未知卡池，应忽略
        pools = {p["key"]: p for p in analyze(GENSHIN, records)}
        self.assertEqual([p["key"] for p in analyze(GENSHIN, records)], [p.key for p in GENSHIN.pools])
        self.assertEqual(pools["301"]["total"], 2)
        self.assertEqual(pools["301"]["records"][0]["pity"], 2)   # 400 的五星接在 301 后面算保底
        self.assertEqual(pools["302"]["total"], 1)
        self.assertEqual(pools["200"]["total"], 1)
        self.assertEqual(sum(p["total"] for p in pools.values()), 4)

    def test_standard_character_flag_only_in_character_event_pool(self):
        records = [rec(1, "301", 5, "刻晴"), rec(2, "301", 5, "胡桃"),
                   rec(3, "301", 5, "刻晴", "武器"), rec(4, "200", 5, "刻晴")]
        pools = {p["key"]: p for p in analyze(GENSHIN, records)}
        flags = {e["name"] + e["item_type"]: e["standard"] for e in pools["301"]["records"]}
        self.assertTrue(flags["刻晴角色"])
        self.assertFalse(flags["胡桃角色"])
        self.assertFalse(flags["刻晴武器"])
        self.assertEqual(pools["301"]["standard_count"], 1)
        self.assertEqual(pools["200"]["standard_count"], 0)

    def test_wuwa_standard_characters_are_flagged_in_limited_pools_only(self):
        records = [rec(1, "1", 5, "凌阳"), rec(2, "1", 5, "今汐"), rec(3, "3", 5, "凌阳")]
        pools = {p["key"]: p for p in analyze(WUWA, records)}
        flags = {e["name"]: e["standard"] for e in pools["1"]["records"]}
        self.assertEqual(flags, {"凌阳": True, "今汐": False})
        self.assertEqual(pools["3"]["standard_count"], 0)    # 常驻池里出常驻角色是正常的

    def test_games_without_a_known_standard_list_never_flag_anything(self):
        pools = analyze(HSR, [rec(1, "11", 5, "姬子")])
        self.assertEqual(sum(p["standard_count"] for p in pools), 0)

    def test_each_game_has_its_own_currency(self):
        for game, label in ((GENSHIN, "原石"), (HSR, "星琼"), (ZZZ, "菲林"), (WUWA, "星声")):
            self.assertEqual(game.meta()["currency"], label)


if __name__ == "__main__":
    unittest.main()
