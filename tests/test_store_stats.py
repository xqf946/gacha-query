import json
import tempfile
import unittest
from pathlib import Path

from tests.fakes import make_records
from wishlog.games import ARKNIGHTS, ENDFIELD, GENSHIN, HSR, WUWA, ZZZ
from wishlog.settings import Settings
from wishlog.stats import analyze, analyze_pool, display_name
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


class StoreExtras(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = Store(Path(self.tmp.name))

    def test_optional_fields_are_kept_and_unknown_ones_are_dropped(self):
        record = {**rec(1, "special", 6), "free": "1", "pool_id": "special_1_0_3", "secret": "should-not-be-stored"}
        self.store.merge("100000001", [record])
        saved = self.store.load("100000001")["records"][0]
        self.assertEqual((saved["free"], saved["pool_id"]), ("1", "special_1_0_3"))
        self.assertNotIn("secret", saved)

    def test_empty_optional_fields_are_not_stored(self):
        self.store.merge("100000001", [{**rec(1, "special", 6), "free": "", "pool_id": ""}])
        saved = self.store.load("100000001")["records"][0]
        self.assertNotIn("free", saved)
        self.assertNotIn("pool_id", saved)

    def test_meta_is_saved_merged_and_does_not_count_as_new_records(self):
        self.store.merge("100000001", [rec(1, "normal", 5)], meta={"pool_names": {"normal": "标准寻访"}})
        stamp = self.store.load("100000001")["updated_at"]
        self.assertEqual(self.store.merge("100000001", [], meta={"pool_names": {"normal": "标准寻访", "x": "新类别"}}), 0)
        self.assertEqual(self.store.meta("100000001")["pool_names"]["x"], "新类别")
        self.assertEqual(self.store.load("100000001")["updated_at"], stamp)       # “最近一次新增”不能被只改了名字的更新刷新

    def test_an_account_without_meta_has_an_empty_one(self):
        self.store.merge("100000001", [rec(1, "301", 5)])
        self.assertEqual(self.store.meta("100000001"), {})
        self.assertEqual(self.store.meta("999"), {})


class Absorb(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = Store(Path(self.tmp.name))

    def test_records_are_merged_without_duplicates_and_the_old_file_is_kept_aside(self):
        self.store.merge("0", [rec(1, "special", 5), rec(2, "special", 4)], meta={"a": 1})
        self.store.merge("555", [rec(2, "special", 4), rec(3, "special", 6)])
        self.assertEqual(self.store.absorb("0", "555"), 2)
        self.assertEqual([r["id"] for r in self.store.load("555")["records"]], ["1001", "1002", "1003"])
        self.assertEqual(self.store.meta("555"), {"a": 1})
        self.assertEqual(self.store.uids(), ["555"])
        self.assertTrue((Path(self.tmp.name) / "0.json.merged").is_file())

    def test_nothing_to_merge_changes_nothing(self):
        self.store.merge("555", [rec(1, "special", 5)])
        self.assertEqual(self.store.absorb("0", "555"), 0)       # 没有临时账号
        self.assertEqual(self.store.absorb("555", "555"), 0)     # 自己并自己
        self.assertEqual(self.store.uids(), ["555"])
        self.assertEqual(len(self.store.load("555")["records"]), 1)


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

    def test_free_pulls_do_not_advance_the_pity_counter_but_do_count_as_pulls(self):
        records = [{**rec(1, "special", 4), "free": ""}, {**rec(2, "special", 4), "free": "1"},
                   {**rec(3, "special", 4), "free": "1"}, {**rec(4, "special", 6), "free": ""}]
        pool = analyze_pool(ENDFIELD, ENDFIELD.pools[0], records)
        top = next(e for e in pool["records"] if e["tier"] == "top")
        self.assertEqual(top["pity"], 2)                       # 4 抽里有 2 次免费，只算 2 抽
        self.assertEqual((pool["total"], pool["free_count"]), (4, 2))
        self.assertEqual(pool["current_pity_top"], 0)

    def test_a_free_top_pull_does_not_reset_the_counter_and_is_not_averaged(self):
        records = [rec(1, "special", 4), rec(2, "special", 4), {**rec(3, "special", 6), "free": "1"}, rec(4, "special", 4)]
        pool = analyze_pool(ENDFIELD, ENDFIELD.pools[0], records)
        self.assertEqual(pool["current_pity_top"], 3)          # 免费出的六星没有清零保底
        self.assertIsNone(pool["avg_pity_top"])                # 也不计入平均出货抽数
        self.assertTrue(next(e for e in pool["records"] if e["tier"] == "top")["free"])

    def test_cost_leaves_out_free_pulls(self):
        records = [{**rec(1, "301", 3), "free": "1"}, rec(2, "301", 3), rec(3, "301", 3)]
        self.assertEqual(analyze_pool(GENSHIN, GENSHIN.pools[0], records)["cost"], 2 * 160)

    def test_pity_restarts_when_the_banner_changes_only_for_pools_that_say_so(self):
        records = [{**rec(1, "weapon_special", 4), "pool_id": "a"}, {**rec(2, "weapon_special", 4), "pool_id": "a"},
                   {**rec(3, "weapon_special", 4), "pool_id": "b"}]
        weapon = next(p for p in ENDFIELD.pools if p.key == "weapon_special")
        self.assertTrue(weapon.reset_on_new_pool)
        self.assertEqual(analyze_pool(ENDFIELD, weapon, records)["current_pity_top"], 1)
        shared = [{**rec(1, "special", 4), "pool_id": "a"}, {**rec(2, "special", 4), "pool_id": "b"}]
        self.assertEqual(analyze_pool(ENDFIELD, ENDFIELD.pools[0], shared)["current_pity_top"], 2)   # 角色池的保底共享

    def test_a_record_without_a_pool_id_never_triggers_a_reset(self):
        records = [{**rec(1, "weapon_special", 4), "pool_id": "a"}, rec(2, "weapon_special", 4)]
        weapon = next(p for p in ENDFIELD.pools if p.key == "weapon_special")
        self.assertEqual(analyze_pool(ENDFIELD, weapon, records)["current_pity_top"], 2)

    def test_dynamic_pools_follow_the_fixed_ones_newest_first_and_use_the_stored_names(self):
        records = [rec(1, "normal", 2), {**rec(2, "old_fest", 2), "time": "2026-01-01 00:00:01"},
                   {**rec(3, "new_fest", 2), "time": "2026-09-01 00:00:01"}]
        pools = analyze(ARKNIGHTS, records, {"old_fest": "限定寻访 旧", "new_fest": "限定寻访 新"})
        self.assertEqual([p["name"] for p in pools], ["标准寻访", "中坚寻访", "限定寻访 新", "限定寻访 旧"])

    def test_a_dynamic_pool_without_a_stored_name_falls_back_to_its_key(self):
        pools = analyze(ARKNIGHTS, [rec(1, "mystery_fest", 2)])
        self.assertEqual(pools[-1]["name"], "mystery_fest")

    def test_games_without_dynamic_pools_ignore_unknown_types(self):
        self.assertEqual(sum(p["total"] for p in analyze(ENDFIELD, [rec(1, "unknown", 5)])), 0)

    def test_names_wrapped_in_corner_brackets_are_shown_without_them(self):
        # 官方数据里有的名字带「」、有的不带，显示时统一
        self.assertEqual(display_name("「墨丘利」"), "墨丘利")
        self.assertEqual(display_name("『某某』"), "某某")
        self.assertEqual(display_name("艾瑞儿"), "艾瑞儿")
        self.assertEqual(display_name("  「墨丘利」 "), "墨丘利")

    def test_only_a_bracket_pair_around_the_whole_name_is_removed(self):
        for name in ("原初长刃·朴石", "「甲」和「乙」", "「半边", "半边」", "「」", "「「嵌套」」", "A「B」C", ""):
            self.assertEqual(display_name(name), name.strip(), name)

    def test_the_statistics_show_uniform_names_but_the_stored_records_keep_the_official_ones(self):
        records = [{**rec(1, "5", 4, "「墨丘利」", "邦布")}, {**rec(2, "5", 4, "艾瑞儿", "邦布")}]
        pool = analyze_pool(ZZZ, next(p for p in ZZZ.pools if p.key == "5"), records)
        self.assertEqual([e["name"] for e in pool["records"]], ["艾瑞儿", "墨丘利"])
        self.assertEqual(records[0]["name"], "「墨丘利」")         # 原始数据没被改

    def test_the_standard_character_check_still_uses_the_official_name(self):
        records = [rec(1, "1", 5, "凌阳")]
        self.assertTrue(analyze(WUWA, records)[0]["records"][0]["standard"])

    def test_each_game_has_its_own_currency(self):
        for game, label in ((GENSHIN, "原石"), (HSR, "星琼"), (ZZZ, "菲林"), (WUWA, "星声")):
            self.assertEqual(game.meta()["currency"], label)


if __name__ == "__main__":
    unittest.main()
