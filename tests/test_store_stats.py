import json
import tempfile
import unittest
from pathlib import Path

from tests.fakes import make_records
from wishlog.stats import analyze, analyze_pool
from wishlog.pools import POOLS
from wishlog.store import Store


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


class StatsTests(unittest.TestCase):
    def test_pity_counting(self):
        # 第 5、8 抽出五星；第 3 抽出四星；之后又抽了 2 个三星
        ranks = [3, 3, 4, 3, 5, 3, 3, 5, 3, 3]
        records = [rec(i, "301", r) for i, r in enumerate(ranks, 1)]
        pool = analyze_pool(POOLS[0], records)
        by_n = {e["n"]: e for e in pool["records"]}
        self.assertEqual(by_n[5]["pity"], 5)
        self.assertEqual(by_n[8]["pity"], 3)
        self.assertEqual(by_n[3]["pity"], 3)          # 四星的垫抽
        self.assertEqual(pool["current_pity5"], 2)
        self.assertEqual(pool["current_pity4"], 2)    # 五星出现后四星计数也清零
        self.assertEqual(pool["avg_pity5"], 4.0)
        self.assertEqual((pool["min_pity5"], pool["max_pity5"]), (3, 5))
        self.assertEqual((pool["five_count"], pool["four_count"], pool["three_count"]), (2, 1, 7))
        self.assertEqual(pool["primogems"], 10 * 160)
        self.assertEqual([e["n"] for e in pool["records"]][:3], [10, 9, 8])   # 从新到旧

    def test_empty_pool(self):
        pool = analyze_pool(POOLS[0], [])
        self.assertEqual((pool["total"], pool["current_pity5"], pool["avg_pity5"]), (0, 0, None))

    def test_groups_by_pool_and_folds_gacha_type_400_into_301(self):
        records = [rec(1, "301", 3), rec(2, "400", 5, "胡桃"), rec(3, "302", 3, "黑缨枪", "武器"),
                   rec(4, "200", 3), rec(5, "999", 5)]   # 999 是未知卡池，应忽略
        pools = {p["key"]: p for p in analyze(records)}
        self.assertEqual([p["key"] for p in analyze(records)], [p.key for p in POOLS])
        self.assertEqual(pools["301"]["total"], 2)
        self.assertEqual(pools["301"]["records"][0]["pity"], 2)   # 400 的五星接在 301 后面算保底
        self.assertEqual(pools["302"]["total"], 1)
        self.assertEqual(pools["200"]["total"], 1)
        self.assertEqual(sum(p["total"] for p in pools.values()), 4)

    def test_standard_character_flag_only_in_character_event_pool(self):
        records = [rec(1, "301", 5, "刻晴"), rec(2, "301", 5, "胡桃"),
                   rec(3, "301", 5, "刻晴", "武器"), rec(4, "200", 5, "刻晴")]
        pools = {p["key"]: p for p in analyze(records)}
        flags = {e["name"] + e["item_type"]: e["standard"] for e in pools["301"]["records"]}
        self.assertTrue(flags["刻晴角色"])
        self.assertFalse(flags["胡桃角色"])
        self.assertFalse(flags["刻晴武器"])
        self.assertEqual(pools["301"]["standard_count"], 1)
        self.assertEqual(pools["200"]["standard_count"], 0)


if __name__ == "__main__":
    unittest.main()
