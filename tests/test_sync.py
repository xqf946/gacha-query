import tempfile
import unittest

from tests.fakes import FakeApi, make_records, wish_url
from wishlog.client import ApiError, AuthExpired, Client, parse_wish_url
from wishlog.store import Store
from wishlog.sync import sync_all


class SyncTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = Store(self.tmp.name)
        self.auth = parse_wish_url(wish_url())

    def make_api(self, **sizes):
        pools = {k: make_records(k, n, seed=int(k)) for k, n in sizes.items()}
        return FakeApi(pools), pools

    def client(self, api):
        return Client(fetch=api.fetch, sleep=lambda s: None)

    def test_first_sync_fetches_everything_including_page_boundaries(self):
        # 20 条正好一页，45 条跨三页，其余为空或很少
        api, pools = self.make_api(**{"301": 45, "302": 20, "500": 0, "200": 3, "100": 0})
        result = sync_all(self.client(api), self.auth, self.store)
        self.assertEqual(result["uid"], "100000001")
        self.assertEqual(result["new"], {"角色活动祈愿": 45, "武器活动祈愿": 20, "集录祈愿": 0, "常驻祈愿": 3, "新手祈愿": 0})
        self.assertEqual(len(self.store.load("100000001")["records"]), 68)

    def test_character_pool_includes_both_gacha_types(self):
        api, pools = self.make_api(**{"301": 10})
        pools["301"][3]["gacha_type"] = "400"   # 角色活动祈愿-2 也由 301 的请求返回
        sync_all(self.client(api), self.auth, self.store)
        types = {r["gacha_type"] for r in self.store.load("100000001")["records"]}
        self.assertEqual(types, {"301", "400"})

    def test_second_sync_adds_nothing_and_stops_early(self):
        api, _ = self.make_api(**{"301": 100})
        sync_all(self.client(api), self.auth, self.store)
        api.calls.clear()
        result = sync_all(self.client(api), self.auth, self.store)
        self.assertEqual(result["total_new"], 0)
        # 301 只读第一页就停；其余 4 个卡池各读一页
        self.assertEqual(len(api.calls), 5)

    def test_incremental_sync_only_adds_new_records(self):
        api, pools = self.make_api(**{"301": 30})
        sync_all(self.client(api), self.auth, self.store)
        pools["301"].extend(make_records("301", 7, seed=99))
        result = sync_all(self.client(api), self.auth, self.store)
        self.assertEqual(result["new"]["角色活动祈愿"], 7)
        self.assertEqual(len(self.store.load("100000001")["records"]), 37)

    def test_failure_mid_pool_discards_partial_pool_so_next_sync_has_no_gap(self):
        api, pools = self.make_api(**{"301": 5, "302": 50})
        api.fail_on = ("302", 2)   # 武器池抓到第 2 页时链接失效
        with self.assertRaises(AuthExpired):
            sync_all(self.client(api), self.auth, self.store)
        saved = self.store.load("100000001")["records"]
        self.assertEqual({r["gacha_type"] for r in saved}, {"301"})  # 已完成的卡池保留，半截的丢弃

        api.fail_on = None
        sync_all(self.client(api), self.auth, self.store)
        weapons = [r for r in self.store.load("100000001")["records"] if r["gacha_type"] == "302"]
        self.assertEqual(len(weapons), 50)   # 补全了，中间没有缺口

    def test_account_without_any_records(self):
        api, _ = self.make_api()
        with self.assertRaises(ApiError):
            sync_all(self.client(api), self.auth, self.store)

    def test_progress_callback(self):
        api, _ = self.make_api(**{"301": 25})
        seen = []
        sync_all(self.client(api), self.auth, self.store, lambda *a: seen.append(a))
        self.assertIn(("角色活动祈愿", 25, 0), seen)


if __name__ == "__main__":
    unittest.main()
