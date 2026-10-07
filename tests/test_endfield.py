import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tests.fakes import EF_ACCOUNT_TOKEN, EF_LINK, EF_TOKEN, FakeEndfield, ef_char, ef_weapon
from wishlog.client import ApiError, AuthExpired, InvalidUrl
from wishlog.games import ENDFIELD
from wishlog.games import endfield
from wishlog.locate import GameNotFound, LocateError
from wishlog.store import Store


def make_home(root: Path, text: str) -> Path:
    log = root.joinpath(*endfield.LOG_PARTS)
    log.parent.mkdir(parents=True)
    log.write_text(text, encoding="utf-8")
    return root


def log_text(*links: str) -> str:
    return "".join(f"[2026-10-06 12:00:00] INFO webview open url={link} flags=0\n" for link in links)


class Links(unittest.TestCase):
    def test_parse_a_real_looking_link(self):
        self.assertEqual(endfield.parse_link(EF_LINK), (EF_TOKEN, "1"))

    def test_server_comes_from_the_link_when_it_is_a_number(self):
        self.assertEqual(endfield.parse_link(EF_LINK.replace("server=1", "server_id=2"))[1], "2")
        self.assertEqual(endfield.parse_link(EF_LINK.replace("server=1", "server=abc"))[1], "1")
        self.assertEqual(endfield.parse_link(EF_LINK.replace("&server=1", ""))[1], "1")

    def test_foreign_or_malformed_links_are_refused(self):
        bad = [
            EF_LINK.replace("ef-webview.hypergryph.com", "ef-webview.hypergryph.com.evil.com"),
            EF_LINK.replace("ef-webview.hypergryph.com", "evil.com"),
            EF_LINK.replace("ef-webview.hypergryph.com", "ef-webview.gryphline.com"),   # 国际服暂不支持
            EF_LINK.replace("https://", "http://"),
            EF_LINK.replace("/page/gacha_char", "/page/announcement"),
            EF_LINK.replace(f"&u8_token={EF_TOKEN}", ""),
            "garbage",
        ]
        for url in bad:
            with self.assertRaises(InvalidUrl, msg=url):
                endfield.parse_link(url)

    def test_find_link_takes_the_newest_one_and_ignores_other_pages(self):
        old, new = EF_LINK.replace(EF_TOKEN, "OLDTOKEN"), EF_LINK
        text = log_text(old) + "https://ef-webview.hypergryph.com/page/announcement?x=1\n" + log_text(new)
        self.assertEqual(endfield.find_link(text), new)

    def test_find_link_stops_at_quotes_and_spaces(self):
        text = f'json={{"url":"{EF_LINK}","x":1}} tail'
        self.assertEqual(endfield.find_link(text), EF_LINK)

    def test_no_link(self):
        self.assertIsNone(endfield.find_link("nothing here\nat all"))

    def test_find_links_lists_every_distinct_token_newest_first(self):
        old, mid, new = (EF_LINK.replace(EF_TOKEN, t) for t in ("OLD", "MID", "NEW"))
        text = log_text(old, mid, mid, new)               # mid 出现了两次，只算一条
        self.assertEqual(endfield.find_links(text), [new, mid, old])
        self.assertEqual(endfield.find_links(text, limit=2), [new, mid])

    def test_two_links_on_one_line_newest_is_the_one_on_the_right(self):
        old, new = EF_LINK.replace(EF_TOKEN, "OLD"), EF_LINK.replace(EF_TOKEN, "NEW")
        self.assertEqual(endfield.find_links(f"a {old} b {new} c"), [new, old])

    def test_json_escaped_ampersands_and_trailing_punctuation_are_cleaned(self):
        escaped = EF_LINK.replace("&", "\\u0026")
        self.assertEqual(endfield.parse_link(endfield.find_link(f'x={escaped}",\\')), (EF_TOKEN, "1"))
        self.assertEqual(endfield.parse_link(endfield.find_link(EF_LINK.replace("&", "&amp;") + ")")), (EF_TOKEN, "1"))

    def test_a_token_with_a_plus_sign_is_tried_both_ways(self):
        variants, _ = endfield.parse_link_all(EF_LINK.replace(EF_TOKEN, "ab+cd%2Fef=="))
        self.assertEqual(variants, ["ab+cd/ef==", "ab cd/ef==", "ab+cd%2Fef=="])
        self.assertEqual(endfield.parse_link(EF_LINK.replace(EF_TOKEN, "ab+cd"))[0], "ab+cd")   # 默认保留 +

    def test_a_plain_token_has_a_single_spelling(self):
        self.assertEqual(endfield.parse_link_all(EF_LINK)[0], [EF_TOKEN])


class FindingTheLog(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home = make_home(Path(self.tmp.name), log_text(EF_LINK))
        self.log = self.home.joinpath(*endfield.LOG_PARTS)

    def test_default_location_under_the_users_home(self):
        self.assertEqual(endfield.find_log(home=self.home), self.log)

    def test_any_level_the_user_picks_resolves_to_the_log(self):
        for given in (self.log, self.log.parent, self.log.parents[1], self.log.parents[2], str(self.log.parents[2]), f'"{self.log.parent}"'):
            self.assertEqual(endfield.resolve_log(given), self.log, given)

    def test_not_installed_is_game_not_found(self):
        with self.assertRaises(GameNotFound):
            endfield.find_log(home=Path(self.tmp.name) / "empty")

    def test_a_wrong_explicit_choice_is_a_plain_locate_error(self):
        with self.assertRaises(LocateError) as ctx:
            endfield.find_log(Path(self.tmp.name) / "nope")
        self.assertNotIsInstance(ctx.exception, GameNotFound)


class Records(unittest.TestCase):
    def test_a_draw(self):
        rec = endfield.char_record(ef_char(212, "大潘", 5, free=False), "special")
        self.assertEqual((rec["name"], rec["rank_type"], rec["item_type"], rec["gacha_type"]), ("大潘", "5", "角色", "special"))
        self.assertEqual(rec["free"], "")
        self.assertRegex(rec["time"], r"^\d{4}-\d\d-\d\d \d\d:\d\d:\d\d$")

    def test_free_pulls_are_marked(self):
        self.assertEqual(endfield.char_record(ef_char(1, "x", 4, free=True), "special")["free"], "1")

    def test_bonus_entries_are_not_pulls(self):
        gift = ef_char(5, "", 0, kind="gift_intel_book")
        self.assertIsNone(endfield.char_record(gift, "special"))

    def test_the_same_sequence_number_in_two_pools_gets_different_ids(self):
        a = endfield.char_record(ef_char(7, "甲", 4, pool_id="special_1_0_3"), "special")
        b = endfield.char_record(ef_char(7, "乙", 4, pool_id="standard_1_0_1"), "standard")
        self.assertNotEqual(a["id"], b["id"])
        self.assertEqual(a["id"], endfield.char_record(ef_char(7, "甲", 4, pool_id="special_1_0_3"), "special")["id"])

    def test_a_character_and_a_weapon_with_the_same_numbers_do_not_collide(self):
        self.assertNotEqual(endfield.char_record(ef_char(3, "甲", 4, pool_id="p"), "special")["id"],
                            endfield.weapon_record(ef_weapon(3, "乙", 4, pool_id="p"), "weapon_special")["id"])

    def test_weapon_groups(self):
        self.assertEqual(endfield.weapon_group("weponbox_1_0_1"), "weapon_special")
        self.assertEqual(endfield.weapon_group("weaponbox_constant_2"), "weapon_constant")
        self.assertEqual(endfield.weapon_group("rerun_wpn_yvonne"), "weapon_rerun")


class Client(unittest.TestCase):
    def client(self, fake):
        return endfield.EndfieldClient(transport=fake, sleep=lambda s: None)

    def test_role_lookup_returns_the_uid(self):
        fake = FakeEndfield(uid="987654321")
        self.assertEqual(self.client(fake).role(EF_TOKEN, "1"), ("987654321", "管理员"))
        method, host, path, _, _, body = fake.calls[0]
        self.assertEqual((method, host, path), ("POST", "u8.hypergryph.com", "/game/role/v1/query_role_list"))
        self.assertEqual(body, {"token": EF_TOKEN, "serverId": "1"})

    def test_a_reply_that_says_code_zero_instead_of_status_is_accepted(self):
        def reply(method, url, headers, body):
            return {"code": 0, "data": {"uid": "42", "roles": []}}
        client = endfield.EndfieldClient(transport=reply, sleep=lambda s: None)
        self.assertEqual(client.role(EF_TOKEN, "1"), ("42", ""))

    def test_uid_falls_back_to_the_role_id(self):
        fake = FakeEndfield(uid="not-a-number")
        self.assertEqual(self.client(fake).role(EF_TOKEN, "1")[0], "555")

    def test_an_invalid_token_is_reported_as_expired(self):
        with self.assertRaisesRegex(AuthExpired, "寻访记录"):
            self.client(FakeEndfield(token="OTHER")).role(EF_TOKEN, "1")

    def test_the_error_says_which_step_failed_and_what_the_server_replied(self):
        with self.assertRaises(AuthExpired) as ctx:
            self.client(FakeEndfield(token="OTHER")).role(EF_TOKEN, "1")
        text = str(ctx.exception)
        self.assertIn("query_role_list", text)
        self.assertIn("status=3", text.replace("code=", "status="))
        self.assertIn("token invalid", text)
        self.assertIn("环境检测", text)
        self.assertNotIn(EF_TOKEN, text)

    def test_an_echoed_token_never_reaches_the_message(self):
        def echo(method, url, headers, body):
            return {"status": 3, "msg": f"bad token {EF_TOKEN} for you"}
        with self.assertRaises(AuthExpired) as ctx:
            endfield.EndfieldClient(transport=echo, sleep=lambda s: None).role(EF_TOKEN, "1")
        self.assertNotIn(EF_TOKEN, str(ctx.exception))
        self.assertIn("***", str(ctx.exception))

    def test_an_unrelated_server_complaint_is_not_called_an_expired_credential(self):
        def other(method, url, headers, body):
            return {"code": 7, "msg": "invalid pool_type", "data": None}
        client = endfield.EndfieldClient(transport=other, sleep=lambda s: None)
        auth = endfield.EndfieldAuth(EF_TOKEN, "1", "1", "")
        with self.assertRaises(ApiError) as ctx:
            client.char_page(auth, "E_CharacterGachaPoolType_Special", None)
        self.assertNotIsInstance(ctx.exception, AuthExpired)
        self.assertIn("invalid pool_type", str(ctx.exception))

    def test_requests_carry_the_referer_and_stay_on_official_hosts(self):
        fake = FakeEndfield(chars={"special": [ef_char(1, "x", 4)]})
        auth = endfield.EndfieldAuth(EF_TOKEN, "1", "987654321", "")
        self.client(fake).char_page(auth, "E_CharacterGachaPoolType_Special", None)
        _, host, path, query, headers, _ = fake.calls[0]
        self.assertEqual((host, path), ("ef-webview.hypergryph.com", "/api/record/char"))
        self.assertEqual((query["lang"], query["server_id"], query["pool_type"]), ("zh-cn", "1", "E_CharacterGachaPoolType_Special"))
        self.assertIn("/page/gacha_char", headers["Referer"])

    def test_requests_are_paced(self):
        sleeps = []
        client = endfield.EndfieldClient(transport=FakeEndfield(), sleep=sleeps.append)
        auth = endfield.EndfieldAuth(EF_TOKEN, "1", "1", "")
        for _ in range(3):
            client.char_page(auth, "E_CharacterGachaPoolType_Special", None)
        self.assertEqual(sleeps, [0.2, 0.2])


class WholeGame(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home = make_home(Path(self.tmp.name) / "home", log_text(EF_LINK))
        self.store = Store(Path(self.tmp.name) / "data" / "endfield")
        # 特许寻访：从新到旧，最后一页的末尾夹着一条奖励；页大小 3，所以会翻好几页
        self.special = [ef_char(9, "管理员", 6), ef_char(8, "甲", 4), ef_char(7, "", 0, kind="gift_intel_book"),
                        ef_char(6, "乙", 5), ef_char(5, "丙", 4, free=True), ef_char(4, "丁", 4),
                        ef_char(3, "戊", 4), ef_char(2, "己", 5), ef_char(1, "庚", 4)]
        self.fake = FakeEndfield(
            chars={"special": self.special, "standard": [ef_char(30, "辛", 4, pool_id="standard_1_0_1")]},
            weapons={"weponbox_1_0_1": [ef_weapon(12, "寻路者道标", 5), ef_weapon(11, "见习者长刀", 4)],
                     "weaponbox_constant_2": [ef_weapon(3, "常驻武器", 4, pool_id="weaponbox_constant_2")]},
        )
        self.client = endfield.EndfieldClient(transport=self.fake, sleep=lambda s: None)

    def run_sync(self):
        with mock.patch.object(endfield, "home_dir", return_value=self.home):
            auth = ENDFIELD.connect(self.client, "", "")
        return ENDFIELD.sync(self.client, auth, self.store)

    def test_connect_reads_the_log_and_finds_the_account(self):
        with mock.patch.object(endfield, "home_dir", return_value=self.home):
            auth = ENDFIELD.connect(self.client, "", "")
        self.assertEqual((auth.token, auth.server_id, auth.uid, auth.nickname), (EF_TOKEN, "1", "987654321", "管理员"))
        self.assertNotIn(EF_TOKEN, repr(auth) + str(auth.uid))

    def connect_from(self, text: str):
        home = make_home(Path(self.tmp.name) / "h-custom", text)
        with mock.patch.object(endfield, "home_dir", return_value=home):
            return ENDFIELD.connect(self.client, "", "")

    def test_a_stale_newest_link_falls_back_to_an_older_working_one(self):
        stale = EF_LINK.replace(EF_TOKEN, "STALE")
        auth = self.connect_from(log_text(EF_LINK, stale))      # 日志里 stale 更新，但官方不认
        self.assertEqual((auth.token, auth.uid), (EF_TOKEN, "987654321"))
        tried = [c[5]["token"] for c in self.fake.calls if c[1] == "u8.hypergryph.com"]
        self.assertEqual(tried, ["STALE", EF_TOKEN])

    def test_the_plus_sign_spelling_is_tried_first_and_the_space_one_as_a_second_chance(self):
        self.fake.token = "ab cd"                          # 假设官方认的是 + 被还原成空格的写法
        link = EF_LINK.replace(EF_TOKEN, "ab+cd")
        auth = self.connect_from(log_text(link))
        self.assertEqual((auth.token, auth.uid_known), ("ab cd", True))
        tried = [c[5]["token"] for c in self.fake.calls if c[1] == "u8.hypergryph.com"]
        self.assertEqual(tried, ["ab+cd", "ab cd"])

    def test_it_stops_after_a_handful_of_attempts(self):
        links = [EF_LINK.replace(EF_TOKEN, f"BAD{i}") for i in range(20)]
        with self.assertRaises(AuthExpired):
            self.connect_from(log_text(*links))
        role_calls = [c for c in self.fake.calls if c[1] == "u8.hypergryph.com"]
        self.assertEqual(len(role_calls), endfield.MAX_TRIES)

    def test_a_network_problem_is_not_mistaken_for_an_expired_credential(self):
        from wishlog.client import NetworkError

        def down(method, url, headers, body):
            raise NetworkError("连不上官方接口")
        client = endfield.EndfieldClient(transport=down, sleep=lambda s: None)
        with self.assertRaises(NetworkError):
            ENDFIELD.connect(client, EF_LINK, "")

    def test_when_only_the_account_lookup_refuses_the_records_still_come_through(self):
        self.fake.role_status = 3
        auth = self.connect_from(log_text(EF_LINK))
        self.assertEqual((auth.uid, auth.uid_known), (endfield.UNKNOWN_UID, False))
        result = ENDFIELD.sync(self.client, auth, self.store)
        self.assertEqual(result["uid"], "0")
        self.assertEqual(result["new"]["特许寻访"], 8)
        self.assertTrue(any("UID" in w for w in result["warnings"]))
        self.assertEqual(self.store.uids(), ["0"])

    def test_records_saved_under_the_placeholder_account_move_to_the_real_one_later(self):
        self.fake.role_status = 3
        auth = self.connect_from(log_text(EF_LINK))
        ENDFIELD.sync(self.client, auth, self.store)
        before = len(self.store.load("0")["records"])
        self.fake.role_status = 0                           # 官方恢复正常，这次拿到了真正的 UID
        result = self.run_sync()
        self.assertEqual(result["uid"], "987654321")
        self.assertEqual(self.store.uids(), ["987654321"])
        self.assertEqual(len(self.store.load("987654321")["records"]), before)
        self.assertTrue(any("账号 0" in w and "并入" in w for w in result["warnings"]))
        self.assertTrue((self.store.root / "0.json.merged").exists())    # 旧文件改名留底，没有删

    def test_when_neither_endpoint_accepts_the_token_the_error_lists_both_replies(self):
        self.fake.token = "SOMETHING-ELSE"
        with self.assertRaises(AuthExpired) as ctx:
            self.connect_from(log_text(EF_LINK))
        text = str(ctx.exception)
        self.assertIn("query_role_list", text)
        self.assertIn("/api/record/char", text)
        self.assertNotIn(EF_TOKEN, text)
        self.assertEqual(self.store.uids(), [])

    # ---- 账号令牌这条路（日志里的令牌官方不认时用） ----
    def test_a_pasted_account_token_finds_the_character_without_touching_the_log(self):
        for text in (EF_ACCOUNT_TOKEN, f'{{"status":0,"data":{{"content":"{EF_ACCOUNT_TOKEN}"}}}}', f"  {EF_ACCOUNT_TOKEN}\n"):
            fake = FakeEndfield()
            client = endfield.EndfieldClient(transport=fake, sleep=lambda s: None)
            with mock.patch.object(endfield, "home_dir", return_value=Path(self.tmp.name) / "no-such-home"):
                auth = ENDFIELD.connect(client, text, "")
            self.assertEqual((auth.uid, auth.nickname, auth.server_id, auth.token), ("987654321", "管理员", "1", EF_TOKEN), text)
            self.assertTrue(auth.uid_known)
            hosts = [c[1] for c in fake.calls]
            self.assertEqual(hosts, ["as.hypergryph.com", "binding-api-account-prod.hypergryph.com",
                                     "binding-api-account-prod.hypergryph.com"])      # 授权、查绑定、换角色令牌；没碰日志，也没问 query_role_list

    def test_syncing_with_the_account_token_end_to_end(self):
        auth = ENDFIELD.connect(self.client, EF_ACCOUNT_TOKEN, "")
        result = ENDFIELD.sync(self.client, auth, self.store)
        self.assertEqual(result["uid"], "987654321")
        self.assertEqual(result["new"]["特许寻访"], 8)
        self.assertNotIn(EF_ACCOUNT_TOKEN, repr(auth))
        self.assertNotIn(EF_TOKEN, repr(auth))
        saved = "".join(p.read_text(encoding="utf-8") for p in self.store.root.glob("*.json"))
        self.assertNotIn(EF_ACCOUNT_TOKEN, saved)       # 令牌绝不落盘
        self.assertNotIn(EF_TOKEN, saved)

    def test_the_records_land_in_the_same_account_whichever_way_you_connect(self):
        by_token = ENDFIELD.sync(self.client, ENDFIELD.connect(self.client, EF_ACCOUNT_TOKEN, ""), self.store)
        by_link = ENDFIELD.sync(self.client, ENDFIELD.connect(self.client, EF_LINK, ""), self.store)
        self.assertEqual(by_token["uid"], by_link["uid"])
        self.assertEqual(by_link["total_new"], 0)                 # 两条路取到的是同一批记录，不会重复

    def test_a_wrong_account_token_says_so(self):
        self.fake.account_token = "SOMETHING-ELSE-0123456789"
        with self.assertRaisesRegex(AuthExpired, "账号令牌"):
            ENDFIELD.connect(self.client, EF_ACCOUNT_TOKEN, "")

    def test_an_account_without_an_endfield_character(self):
        self.fake.bindings = []
        with self.assertRaisesRegex(ApiError, "没有绑定终末地"):
            ENDFIELD.connect(self.client, EF_ACCOUNT_TOKEN, "")

    def test_several_characters_use_the_first_and_say_so(self):
        self.fake.bindings = [("987654321", "甲"), ("123456789", "乙")]
        auth = ENDFIELD.connect(self.client, EF_ACCOUNT_TOKEN, "")
        self.assertEqual((auth.uid, auth.nickname), ("987654321", "甲"))
        result = ENDFIELD.sync(self.client, auth, self.store)
        self.assertTrue(any("2 个终末地角色" in w and "987654321" in w for w in result["warnings"]))

    def test_text_that_is_neither_a_link_nor_a_token_is_refused_before_any_request(self):
        for text in ("hello", "{not json", "12345"):
            with self.assertRaises(InvalidUrl, msg=text):
                ENDFIELD.connect(self.client, text, "")
        self.assertEqual(self.fake.calls, [])

    def test_a_link_to_another_site_is_not_mistaken_for_a_token(self):
        with self.assertRaises(InvalidUrl):
            ENDFIELD.connect(self.client, "https://evil.example.com/page/gacha_char?u8_token=" + "a" * 30, "")
        self.assertEqual(self.fake.calls, [])

    # ---- 日志里的令牌官方不认（官方调整了日志）：要把人引到账号令牌 ----
    def test_a_dead_log_token_points_the_user_to_the_account_token(self):
        self.fake.token = "TOKEN-ISSUED-LATER"            # 官方现在认的和日志里的不一样
        with mock.patch.object(endfield, "home_dir", return_value=self.home):
            with self.assertRaises(AuthExpired) as ctx:
                ENDFIELD.connect(self.client, "", "")
        text = str(ctx.exception)
        first = text.splitlines()[0]
        self.assertIn("日志里的凭证", first)
        self.assertIn("query_role_list", first)               # 官方的回复也在第一行里，“更新全部”只显示第一行也够用
        self.assertIn("多半走不通", first)
        self.assertIn("账号令牌", first)
        self.assertIn("高级", text)                           # 只指路，做法在输入框下面的说明里，不在提示条里重复一遍
        self.assertNotIn(EF_TOKEN, text)

    def test_no_link_in_the_log_also_offers_the_account_token(self):
        make_home(Path(self.tmp.name) / "h3", "nothing useful here\n")
        with mock.patch.object(endfield, "home_dir", return_value=Path(self.tmp.name) / "h3"):
            with self.assertRaisesRegex(LocateError, "账号令牌"):
                ENDFIELD.connect(self.client, "", "")

    def test_a_pasted_link_that_the_server_refuses_is_reported_as_such(self):
        self.fake.token = "OTHER"
        with self.assertRaises(AuthExpired) as ctx:
            ENDFIELD.connect(self.client, EF_LINK, "")
        self.assertNotIn("日志里的凭证", str(ctx.exception).splitlines()[0])      # 链接是用户贴的，不是从日志读的

    def test_the_game_asks_the_ui_for_a_hidden_input_and_explains_both_ways(self):
        meta = ENDFIELD.meta()
        self.assertTrue(meta["manual_secret"])
        self.assertFalse(meta["manual_required"])             # 日志好用的话不必手动粘贴
        self.assertIn("账号令牌", meta["manual_label"])
        self.assertIn("account/info/hg", meta["manual_help"])

    def test_connect_with_a_pasted_link(self):
        auth = ENDFIELD.connect(self.client, EF_LINK, "/does/not/exist")
        self.assertEqual(auth.uid, "987654321")

    def test_log_without_a_link_says_what_to_do(self):
        make_home(Path(self.tmp.name) / "h2", "nothing useful here\n")
        with mock.patch.object(endfield, "home_dir", return_value=Path(self.tmp.name) / "h2"):
            with self.assertRaisesRegex(LocateError, "寻访记录"):
                ENDFIELD.connect(self.client, "", "")

    def test_first_sync_collects_everything_across_pages_and_skips_bonus_entries(self):
        result = self.run_sync()
        self.assertEqual(result["uid"], "987654321")
        self.assertEqual(result["new"]["特许寻访"], 8)          # 9 条里有 1 条奖励
        self.assertEqual(result["new"]["基础寻访"], 1)
        self.assertEqual(result["new"]["限定申领"], 2)
        self.assertEqual(result["new"]["常驻申领"], 1)
        names = {r["name"] for r in self.store.load("987654321")["records"]}
        self.assertNotIn("", names)

    def test_the_page_cursor_is_the_last_item_even_if_it_is_a_bonus_entry(self):
        self.run_sync()
        special_calls = [c for c in self.fake.calls if c[3].get("pool_type") == "E_CharacterGachaPoolType_Special"]
        cursors = [c[3].get("seq_id") for c in special_calls]
        # 页大小 3：第一页 9,8,7（末尾 7 是奖励条目，游标照样用它），第二页 6,5,4，第三页 3,2,1
        self.assertEqual(cursors, [None, "7", "4"])

    def test_free_pulls_are_flagged_in_the_store(self):
        self.run_sync()
        records = {r["name"]: r for r in self.store.load("987654321")["records"]}
        self.assertEqual(records["丙"].get("free"), "1")
        self.assertNotIn("free", records["丁"])

    def test_pool_ids_are_kept_so_weapon_pity_can_reset_per_banner(self):
        self.run_sync()
        weapons = [r for r in self.store.load("987654321")["records"] if r["gacha_type"] == "weapon_special"]
        self.assertEqual({r["pool_id"] for r in weapons}, {"weponbox_1_0_1"})

    def test_second_sync_adds_nothing_and_stops_early(self):
        self.run_sync()
        before = len(self.fake.calls)
        result = self.run_sync()
        self.assertEqual(result["total_new"], 0)
        special_pages = [c for c in self.fake.calls[before:] if c[3].get("pool_type") == "E_CharacterGachaPoolType_Special"]
        self.assertEqual(len(special_pages), 1)        # 第一页就遇到已有记录，不再往后翻

    def test_new_pulls_are_added_on_the_next_sync(self):
        self.run_sync()
        self.special.insert(0, ef_char(10, "新来的", 6))
        self.assertEqual(self.run_sync()["new"]["特许寻访"], 1)

    def test_an_optional_pool_that_errors_is_skipped_with_a_warning(self):
        self.fake.fail_pool = {"rerun": (500, "pool type not supported")}
        result = self.run_sync()
        self.assertTrue(any("重构寻访" in w for w in result["warnings"]))
        self.assertEqual(result["new"]["特许寻访"], 8)       # 其他卡池不受影响

    def test_a_required_pool_that_errors_fails_the_sync(self):
        self.fake.fail_pool = {"special": (500, "server error")}
        with self.assertRaises(ApiError):
            self.run_sync()

    def test_a_token_that_expires_mid_way_is_reported_as_expired(self):
        with mock.patch.object(endfield, "home_dir", return_value=self.home):
            auth = ENDFIELD.connect(self.client, "", "")
        self.fake.token = "ROTATED"
        with self.assertRaises(AuthExpired):
            ENDFIELD.sync(self.client, auth, self.store)

    def test_an_account_with_no_records_at_all_is_reported(self):
        self.fake.chars, self.fake.weapons = {}, {}
        with self.assertRaisesRegex(ApiError, "没有寻访记录"):
            self.run_sync()

    def test_a_failing_weapon_list_does_not_lose_the_character_pools(self):
        original = self.client.weapon_pools

        def broken(auth):
            raise ApiError("boom")
        self.client.weapon_pools = broken
        result = self.run_sync()
        self.client.weapon_pools = original
        self.assertEqual(result["new"]["特许寻访"], 8)
        self.assertTrue(any("武器池" in w for w in result["warnings"]))

    def test_the_diagnosis_never_prints_the_token(self):
        with mock.patch.object(endfield, "home_dir", return_value=self.home):
            text = "\n".join(ENDFIELD.diagnose(""))
        self.assertIn("HGWebview.log", text)
        self.assertIn("u8_token", text)           # 只说“含 u8_token”和参数名
        self.assertNotIn(EF_TOKEN, text)

    def test_the_diagnosis_reports_the_shape_of_the_token_but_not_its_value(self):
        link = EF_LINK.replace(EF_TOKEN, "Zk+9/Qx%2Bw==")
        home = make_home(Path(self.tmp.name) / "h-shape", log_text(EF_LINK.replace(EF_TOKEN, "OLDER"), link))
        with mock.patch.object(endfield, "home_dir", return_value=home):
            text = "\n".join(ENDFIELD.diagnose(""))
        self.assertIn("2 条", text)
        self.assertIn("长 13", text)
        self.assertIn("含 +号 是", text)
        for secret in ("Zk+9", "OLDER", "Qx%2Bw", "Qx+w"):
            self.assertNotIn(secret, text)

    def test_the_diagnosis_when_the_game_was_never_run(self):
        with mock.patch.object(endfield, "home_dir", return_value=Path(self.tmp.name) / "nothing"):
            self.assertIn("没找到", "\n".join(ENDFIELD.diagnose("")))


if __name__ == "__main__":
    unittest.main()
