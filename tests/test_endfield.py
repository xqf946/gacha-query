import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tests.fakes import EF_LINK, EF_TOKEN, FakeEndfield, ef_char, ef_weapon
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

    def test_uid_falls_back_to_the_role_id(self):
        fake = FakeEndfield(uid="not-a-number")
        self.assertEqual(self.client(fake).role(EF_TOKEN, "1")[0], "555")

    def test_an_invalid_token_is_reported_as_expired(self):
        with self.assertRaisesRegex(AuthExpired, "寻访记录"):
            self.client(FakeEndfield(token="OTHER")).role(EF_TOKEN, "1")

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

    def test_the_diagnosis_when_the_game_was_never_run(self):
        with mock.patch.object(endfield, "home_dir", return_value=Path(self.tmp.name) / "nothing"):
            self.assertIn("没找到", "\n".join(ENDFIELD.diagnose("")))


if __name__ == "__main__":
    unittest.main()
