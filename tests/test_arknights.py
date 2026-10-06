import json
import tempfile
import unittest
from pathlib import Path

from tests.fakes import AK_ACCOUNT_TOKEN, FakeArknights, ak_item
from wishlog.client import ApiError, AuthExpired, InvalidUrl
from wishlog.games import ARKNIGHTS
from wishlog.games import arknights
from wishlog.locate import NeedsInput
from wishlog.net import SafeTransport
from wishlog.stats import analyze
from wishlog.store import Store

NORMAL = [ak_item(n, "银灰" if n == 25 else "芬", 5 if n == 25 else 2) for n in range(29, -1, -1)]   # 30 条，从新到旧
SPRING = [ak_item(n, "限定干员" if n == 7 else "杜林", 5 if n == 7 else 3, pool="spring_2026") for n in range(9, -1, -1)]


class TokenParsing(unittest.TestCase):
    TOKEN = "AbCdEf0123456789xyzXYZ+/=_-"

    def test_accepts_the_bare_token_and_the_whole_page(self):
        pages = [self.TOKEN, f'"{self.TOKEN}"', f"  {self.TOKEN}\n",
                 json.dumps({"status": 0, "msg": "OK", "data": {"token": self.TOKEN}}),
                 json.dumps({"status": 0, "data": {"content": self.TOKEN}}),
                 '{"status":0,\n  "data": {\n    "token": "' + self.TOKEN + '"\n  }\n}']
        for text in pages:
            self.assertEqual(arknights.parse_account_token(text), self.TOKEN, text)

    def test_empty_input_means_the_user_still_has_to_provide_it(self):
        with self.assertRaises(NeedsInput) as ctx:
            arknights.parse_account_token("   ")
        self.assertIn("web-api.hypergryph.com/account/info/hg", str(ctx.exception))

    def test_garbage_is_refused(self):
        truncated = '{"data":{"token":"' + self.TOKEN          # 复制得不完整：缺了结尾的引号
        for text in ("short", '{"a":1}', "<html>", "hello world this is not a token", truncated):
            with self.assertRaises(InvalidUrl, msg=text):
                arknights.parse_account_token(text)


class Flow(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = Store(Path(self.tmp.name) / "arknights")
        self.fake = FakeArknights(
            history={"normal": NORMAL, "spring_fest": SPRING, "classic": []},
            categories=[{"id": "normal", "name": "标准寻访"}, {"id": "spring_fest", "name": "限定寻访\n春节"},
                        {"id": "classic", "name": "中坚寻访"}])
        self.client = arknights.ArknightsClient(transport=self.fake, sleep=lambda s: None)

    def connect(self, text=AK_ACCOUNT_TOKEN):
        return ARKNIGHTS.connect(self.client, text, "")

    def sync(self):
        return ARKNIGHTS.sync(self.client, self.connect(), self.store)

    # ---- 认证链 ----
    def test_connect_walks_the_whole_auth_chain(self):
        auth = self.connect()
        self.assertEqual(auth.bindings, [{"uid": "555000111", "channel": "官服", "nickname": "博士"}])
        paths = [c[2] for c in self.fake.calls]
        self.assertEqual(paths, ["/user/oauth2/v2/grant", "/account/binding/v1/binding_list"])

    def test_the_pasted_whole_page_works_too(self):
        page = json.dumps({"status": 0, "msg": "OK", "data": {"token": AK_ACCOUNT_TOKEN}})
        self.assertEqual(len(self.connect(page).bindings), 1)

    def test_a_wrong_or_expired_account_token_is_reported_as_such(self):
        with self.assertRaisesRegex(AuthExpired, "重新登录官网"):
            self.connect("WRONG-TOKEN-0123456789abcdef")

    def test_an_account_without_arknights_roles_is_explained(self):
        self.fake.bindings = []
        with self.assertRaisesRegex(ApiError, "没有绑定明日方舟"):
            self.connect()

    # ---- 凭证去向 ----
    def test_the_account_token_is_only_ever_sent_in_the_grant_request(self):
        self.sync()
        for method, host, path, query, headers, body in self.fake.calls:
            blob = json.dumps([query, headers, body], ensure_ascii=False)
            if path == "/user/oauth2/v2/grant":
                self.assertEqual(body["token"], AK_ACCOUNT_TOKEN)
            else:
                self.assertNotIn(AK_ACCOUNT_TOKEN, blob, f"{path} 不该带账号令牌")

    def test_the_auth_object_never_prints_a_token(self):
        auth = self.connect()
        self.assertNotIn("OAUTH", repr(auth))
        self.assertNotIn(AK_ACCOUNT_TOKEN, repr(auth) + str(vars(auth)))

    def test_nothing_is_written_to_disk_except_the_records(self):
        self.sync()
        for path in Path(self.tmp.name).rglob("*"):
            if path.is_file():
                text = path.read_text(encoding="utf-8")
                self.assertNotIn(AK_ACCOUNT_TOKEN, text, path)
                self.assertNotIn("OAUTH", text, path)
                self.assertNotIn("U8-", text, path)

    def test_every_request_goes_to_a_hypergryph_host(self):
        self.sync()
        for _, host, *_ in self.fake.calls:
            self.assertTrue(host.endswith("hypergryph.com"), host)

    def test_a_request_to_any_other_host_is_blocked_before_it_leaves(self):
        called = []
        safe = SafeTransport(lambda *a: called.append(a))
        with self.assertRaises(InvalidUrl):
            safe("POST", "https://evil.example.com/steal", {}, {"token": AK_ACCOUNT_TOKEN})
        self.assertEqual(called, [])

    # ---- 取记录 ----
    def test_first_sync_walks_every_category_with_cursor_paging(self):
        result = self.sync()
        self.assertEqual(result["uid"], "555000111")
        self.assertEqual(result["new"], {"标准寻访": 30, "限定寻访 春节": 10, "中坚寻访": 0})
        history = [c for c in self.fake.calls if c[2].endswith("/history")]
        normal = [c[3] for c in history if c[3]["category"] == "normal"]
        self.assertEqual(len(normal), 8)                       # 30 条，页大小 4
        self.assertNotIn("pos", normal[0])                     # 第一页不带游标
        self.assertTrue(all("pos" in q and "gachaTs" in q for q in normal[1:]))

    def test_role_login_comes_before_the_record_requests(self):
        self.sync()
        paths = [c[2] for c in self.fake.calls]
        self.assertLess(paths.index("/user/api/role/login"), paths.index("/user/api/inquiry/gacha/cate"))

    def test_ids_are_unique_and_a_ten_pull_keeps_all_ten_records(self):
        self.sync()
        records = self.store.load("555000111")["records"]
        self.assertEqual(len(records), 40)
        self.assertEqual(len({r["id"] for r in records}), 40)
        same_second = {}
        for r in records:
            if r["gacha_type"] == "normal":
                same_second.setdefault(r["time"], []).append(r)
        self.assertEqual(sorted(len(v) for v in same_second.values()), [10, 10, 10])

    def test_rarity_is_zero_based_so_five_means_six_stars(self):
        self.sync()
        pools = {p["key"]: p for p in analyze(ARKNIGHTS, self.store.load("555000111")["records"], self.store.meta("555000111")["pool_names"])}
        self.assertEqual(pools["normal"]["top_count"], 1)
        top = [r for r in pools["normal"]["records"] if r["tier"] == "top"][0]
        self.assertEqual((top["name"], top["mark"]), ("银灰", "★★★★★★"))

    def test_category_names_are_remembered_and_new_categories_get_their_own_tab(self):
        self.sync()
        meta = self.store.meta("555000111")
        self.assertEqual(meta["pool_names"]["spring_fest"], "限定寻访 春节")      # 换行已经变成空格
        names = [p["name"] for p in analyze(ARKNIGHTS, self.store.load("555000111")["records"], meta["pool_names"]) if p["total"]]
        self.assertEqual(names, ["标准寻访", "限定寻访 春节"])

    def test_second_sync_adds_nothing_and_stops_after_the_first_page(self):
        self.sync()
        before = len(self.fake.calls)
        self.assertEqual(self.sync()["total_new"], 0)
        pages = [c for c in self.fake.calls[before:] if c[2].endswith("/history") and c[3]["category"] == "normal"]
        self.assertEqual(len(pages), 1)

    def test_new_pulls_are_added_on_the_next_sync(self):
        self.sync()
        NORMAL.insert(0, ak_item(30, "新来的", 5))
        try:
            self.assertEqual(self.sync()["new"]["标准寻访"], 1)
        finally:
            NORMAL.pop(0)

    def test_two_bound_roles_are_synced_into_separate_accounts(self):
        self.fake.bindings = [("555000111", "官服", "博士"), ("555000222", "B服", "小号")]
        self.fake.history = {"normal": NORMAL[:10]}
        self.fake.categories = [{"id": "normal", "name": "标准寻访"}]
        result = self.sync()
        self.assertEqual(self.store.uids(), ["555000111", "555000222"])
        self.assertEqual(result["uid"], "555000111")
        self.assertEqual(len(result["new"]), 2)             # 两个角色各自一行，名字里带渠道

    def test_an_account_without_any_records_is_reported(self):
        self.fake.history = {"normal": []}
        self.fake.categories = [{"id": "normal", "name": "标准寻访"}]
        with self.assertRaisesRegex(ApiError, "没有寻访记录"):
            self.sync()

    # ---- 登录状态问题 ----
    def test_a_missing_cookie_is_reported_as_a_login_problem(self):
        self.fake.drop_cookie = True
        with self.assertRaisesRegex(AuthExpired, "登录状态"):
            self.sync()

    def test_some_endpoints_also_want_the_account_token_header_and_it_is_only_added_then(self):
        self.fake.needs_account_header = True
        self.assertEqual(self.sync()["total_new"], 40)
        with_header = [c for c in self.fake.calls if c[4].get("x-account-token")]
        without = [c for c in self.fake.calls if c[2].startswith("/user/api/inquiry") and not c[4].get("x-account-token")]
        self.assertTrue(with_header)
        self.assertEqual(len(without), 1)                  # 只有第一次（被拒绝的那次）没带，之后才补上
        self.assertTrue(all(c[1] == "ak.hypergryph.com" for c in with_header))

    def test_the_cursor_cannot_loop_forever(self):
        stuck = [ak_item(1, "甲", 2, pos=0), ak_item(2, "乙", 2, pos=0)]
        for item in stuck:
            item["gachaTs"] = "1770697079082"
        self.fake.history = {"normal": stuck}
        self.fake.categories = [{"id": "normal", "name": "标准寻访"}]
        self.fake.page_size = 1
        self.assertGreaterEqual(self.sync()["total_new"], 1)      # 能结束就行


class Stats(unittest.TestCase):
    def pool(self, key, records, names=None):
        return next(p for p in analyze(ARKNIGHTS, records, names) if p["key"] == key)

    def rec(self, n, rarity, category="normal", pool_id="a", name="某"):
        stamp = 1770697079082 + n * 60_000
        return arknights.record({"poolId": pool_id, "charId": f"c{n}", "charName": name, "rarity": rarity,
                                 "gachaTs": str(stamp), "pos": 0}, category)

    def test_pity_counts_up_to_the_last_six_star(self):
        records = [self.rec(i, 5 if i == 4 else 2) for i in range(1, 8)]
        pool = self.pool("normal", records)
        self.assertEqual(pool["current_pity_top"], 3)
        self.assertEqual((pool["hard_pity"], pool["soft_pity"]), (99, 50))

    def test_limited_categories_restart_their_pity_with_each_new_pool_but_normal_does_not(self):
        names = {"spring_fest": "限定寻访 春节"}
        limited = [self.rec(1, 2, "spring_fest", "p1"), self.rec(2, 2, "spring_fest", "p1"),
                   self.rec(3, 2, "spring_fest", "p2"), self.rec(4, 2, "spring_fest", "p2")]
        self.assertEqual(self.pool("spring_fest", limited, names)["current_pity_top"], 2)   # 换了一期：从头算
        standard = [self.rec(1, 2, "normal", "p1"), self.rec(2, 2, "normal", "p1"),
                    self.rec(3, 2, "normal", "p2"), self.rec(4, 2, "normal", "p2")]
        self.assertEqual(self.pool("normal", standard)["current_pity_top"], 4)              # 标准寻访跨期继承

    def test_lower_rarities_are_all_counted_as_the_rest(self):
        records = [self.rec(1, 0), self.rec(2, 1), self.rec(3, 2), self.rec(4, 3), self.rec(5, 4), self.rec(6, 5)]
        pool = self.pool("normal", records)
        self.assertEqual((pool["top_count"], pool["second_count"], pool["other_count"]), (1, 1, 4))

    def test_no_cost_estimate(self):
        self.assertIsNone(self.pool("normal", [self.rec(1, 2)])["cost"])


if __name__ == "__main__":
    unittest.main()
