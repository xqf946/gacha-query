import tempfile
import unittest

from tests.fakes import FakeApi, game_url, make_records, wish_url
from wishlog.client import ApiError, AuthExpired, Client, parse_wish_url
from wishlog.games import GENSHIN, HSR, ZZZ
from wishlog.store import Store
from wishlog.sync import sync_all


class SyncTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = Store(self.tmp.name)
        self.auth = parse_wish_url(wish_url(), GENSHIN.api)

    def make_api(self, **sizes):
        pools = {k: make_records(k, n, seed=int(k)) for k, n in sizes.items()}
        return FakeApi(pools), pools

    def client(self, api, game=GENSHIN):
        return Client(game.api, fetch=api.fetch, sleep=lambda s: None)

    def sync(self, api, game=GENSHIN, auth=None, progress=None):
        return sync_all(self.client(api, game), auth or self.auth, game, self.store, progress)

    def test_first_sync_fetches_everything_including_page_boundaries(self):
        # 20 条正好一页，45 条跨三页，其余为空或很少
        api, pools = self.make_api(**{"301": 45, "302": 20, "500": 0, "200": 3, "100": 0})
        result = self.sync(api)
        self.assertEqual(result["uid"], "100000001")
        self.assertEqual(result["new"], {"角色活动祈愿": 45, "武器活动祈愿": 20, "集录祈愿": 0, "常驻祈愿": 3, "新手祈愿": 0})
        self.assertEqual(len(self.store.load("100000001")["records"]), 68)

    def test_character_pool_includes_both_gacha_types(self):
        api, pools = self.make_api(**{"301": 10})
        pools["301"][3]["gacha_type"] = "400"   # 角色活动祈愿-2 也由 301 的请求返回
        self.sync(api)
        types = {r["gacha_type"] for r in self.store.load("100000001")["records"]}
        self.assertEqual(types, {"301", "400"})   # 原神信任记录自带的类型，所以 400 保持原样

    def test_second_sync_adds_nothing_and_stops_early(self):
        api, _ = self.make_api(**{"301": 100})
        self.sync(api)
        api.calls.clear()
        result = self.sync(api)
        self.assertEqual(result["total_new"], 0)
        # 301 只读第一页就停；其余 4 个卡池各读一页
        self.assertEqual(len(api.calls), 5)

    def test_incremental_sync_only_adds_new_records(self):
        api, pools = self.make_api(**{"301": 30})
        self.sync(api)
        pools["301"].extend(make_records("301", 7, seed=99))
        result = self.sync(api)
        self.assertEqual(result["new"]["角色活动祈愿"], 7)
        self.assertEqual(len(self.store.load("100000001")["records"]), 37)

    def test_failure_mid_pool_discards_partial_pool_so_next_sync_has_no_gap(self):
        api, pools = self.make_api(**{"301": 5, "302": 50})
        api.fail_on = ("302", 2)   # 武器池抓到第 2 页时链接失效
        with self.assertRaises(AuthExpired):
            self.sync(api)
        saved = self.store.load("100000001")["records"]
        self.assertEqual({r["gacha_type"] for r in saved}, {"301"})  # 已完成的卡池保留，半截的丢弃

        api.fail_on = None
        self.sync(api)
        weapons = [r for r in self.store.load("100000001")["records"] if r["gacha_type"] == "302"]
        self.assertEqual(len(weapons), 50)   # 补全了，中间没有缺口

    def test_account_without_any_records(self):
        api, _ = self.make_api()
        with self.assertRaises(ApiError):
            self.sync(api)

    def test_progress_callback(self):
        api, _ = self.make_api(**{"301": 25})
        seen = []
        self.sync(api, progress=lambda *a: seen.append(a))
        self.assertIn(("角色活动祈愿", 25, 0), seen)


class OtherMihoyoGames(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = Store(self.tmp.name)

    def test_hsr_collaboration_pools_are_fetched_from_the_ld_endpoint(self):
        api = FakeApi({"11": make_records("11", 3), "21": make_records("21", 2, seed=5)})
        auth = parse_wish_url(game_url("hkrpg"), HSR.api)
        result = sync_all(Client(HSR.api, fetch=api.fetch, sleep=lambda s: None), auth, HSR, self.store)
        self.assertEqual(result["new"]["角色活动跃迁"], 3)
        self.assertEqual(result["new"]["角色联动跃迁"], 2)
        paths = {pool: path for pool, _, _, path in api.calls}
        self.assertTrue(paths["11"].endswith("/getGachaLog"))
        self.assertTrue(paths["21"].endswith("/getLdGachaLog"))

    def test_optional_pool_that_errors_is_skipped_with_a_warning(self):
        api = FakeApi({"11": make_records("11", 3)})
        api.fail_pools = {"21": (-1, "no such pool"), "22": (-1, "no such pool")}
        auth = parse_wish_url(game_url("hkrpg"), HSR.api)
        result = sync_all(Client(HSR.api, fetch=api.fetch, sleep=lambda s: None), auth, HSR, self.store)
        self.assertEqual(result["total_new"], 3)           # 联动池取不到，不影响其他卡池
        self.assertEqual(len(result["warnings"]), 2)
        self.assertIn("角色联动跃迁", result["warnings"][0])

    def test_a_required_pool_that_errors_fails_the_sync(self):
        api = FakeApi({"11": make_records("11", 3)})
        api.fail_pools = {"12": (-1, "broken")}
        auth = parse_wish_url(game_url("hkrpg"), HSR.api)
        with self.assertRaises(ApiError):
            sync_all(Client(HSR.api, fetch=api.fetch, sleep=lambda s: None), auth, HSR, self.store)

    def test_expired_link_on_an_optional_pool_is_never_swallowed(self):
        api = FakeApi({"11": make_records("11", 3)})
        api.fail_on = ("21", 1)
        auth = parse_wish_url(game_url("hkrpg"), HSR.api)
        with self.assertRaises(AuthExpired):
            sync_all(Client(HSR.api, fetch=api.fetch, sleep=lambda s: None), auth, HSR, self.store)

    def test_pool_membership_follows_the_requested_pool_not_the_record_own_type(self):
        # 绝区零：独家重映(102) 的记录自己带的类型可能是 2；必须归到请求的 102 里，否则会混进独家频段
        records = make_records("2", 4, ranks=("4", "3", "2"))
        api = FakeApi({"102": records})
        auth = parse_wish_url(game_url("nap"), ZZZ.api)
        sync_all(Client(ZZZ.api, fetch=api.fetch, sleep=lambda s: None), auth, ZZZ, self.store)
        saved = self.store.load("100000001")["records"]
        self.assertEqual({r["gacha_type"] for r in saved}, {"102"})

    def test_zzz_requests_use_real_gacha_type(self):
        api = FakeApi({"2": make_records("2", 3, ranks=("4", "3", "2"))})
        auth = parse_wish_url(game_url("nap"), ZZZ.api)
        sync_all(Client(ZZZ.api, fetch=api.fetch, sleep=lambda s: None), auth, ZZZ, self.store)
        self.assertEqual({pool for pool, *_ in api.calls}, {"2", "3", "1", "5", "102", "103"})


if __name__ == "__main__":
    unittest.main()
