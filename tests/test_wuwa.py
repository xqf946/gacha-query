import tempfile
import unittest
from pathlib import Path

from tests.fakes import (
    WUWA_ANNOUNCEMENT_URL, WUWA_URL, FakeWuwaApi, encrypt_client_log, wuwa_item, wuwa_log_line,
)
from wishlog.client import ApiError, AuthExpired, InvalidUrl
from wishlog.games import WUWA
from wishlog.games import wuwa
from wishlog.locate import GameNotFound, LocateError
from wishlog.store import Store


class Decoding(unittest.TestCase):
    def test_known_vector(self):
        # 解码规则：奇数字节 ^0xA5，偶数字节 ^0xEF（00→EF，01→A4，02→ED，03→A6）
        decoded = bytes(wuwa._DECODE_TABLE[b] for b in (0x00, 0x01, 0x02, 0x03))
        self.assertEqual(decoded, bytes([0xEF, 0xA4, 0xED, 0xA6]))

    def test_the_three_byte_header_is_skipped(self):
        blob = bytes([0xEF, 0xBB, 0xBF]) + encrypt_client_log("hi")[3:]
        self.assertEqual(wuwa.decode_client_log(blob), "hi")

    def test_encrypt_then_decode_roundtrips_chinese_text(self):
        text = "[2026.10.06-12.00.00:123]LogGame: 唤取记录 {\"url\":\"https://x\"}\n"
        self.assertEqual(wuwa.decode_client_log(encrypt_client_log(text)), text)

    def test_the_whole_byte_range_roundtrips(self):
        text = "".join(chr(c) for c in range(1, 128))
        self.assertEqual(wuwa.decode_client_log(encrypt_client_log(text)), text)


class FindingTheUrl(unittest.TestCase):
    def test_extracts_the_url_and_restores_escaped_ampersands(self):
        text = "noise\n" + wuwa_log_line(WUWA_URL) + "more noise\n"
        self.assertEqual(wuwa.extract_record_url(text), WUWA_URL)

    def test_picks_the_newest_record_link(self):
        old = WUWA_URL.replace("TOKEN123", "OLD")
        text = wuwa_log_line(old, "2026.10.01-08.00.00:000") + wuwa_log_line(WUWA_URL, "2026.10.06-12.00.00:000")
        self.assertIn("TOKEN123", wuwa.extract_record_url(text))
        # 顺序反过来写，也按时间戳而不是文件位置取最新
        text = wuwa_log_line(WUWA_URL, "2026.10.06-12.00.00:000") + wuwa_log_line(old, "2026.10.01-08.00.00:000")
        self.assertIn("TOKEN123", wuwa.extract_record_url(text))

    def test_a_newer_announcement_page_does_not_hide_the_record_link(self):
        text = wuwa_log_line(WUWA_URL, "2026.10.06-12.00.00:000") + wuwa_log_line(WUWA_ANNOUNCEMENT_URL, "2026.10.06-13.00.00:000")
        self.assertEqual(wuwa.extract_record_url(text), WUWA_URL)

    def test_no_record_link_in_the_log(self):
        self.assertIsNone(wuwa.extract_record_url(wuwa_log_line(WUWA_ANNOUNCEMENT_URL)))
        self.assertIsNone(wuwa.extract_record_url("nothing here"))

    def test_loose_fallback_when_the_line_format_changes(self):
        self.assertEqual(wuwa.extract_record_url(f'blah {WUWA_URL} blah'), WUWA_URL)

    def test_encrypted_and_plain_logs_both_work(self):
        text = wuwa_log_line(WUWA_URL)
        self.assertEqual(wuwa.find_url_in_log(encrypt_client_log(text)), WUWA_URL)
        self.assertEqual(wuwa.find_url_in_log(text.encode("utf-8")), WUWA_URL)   # 以后若不再加密也能读

    def test_host_whitelist(self):
        good = ["https://aki-gm-resources.aki-game.com/aki/gacha/index.html#/record?a=1",
                "https://aki-gm-resources-oversea.aki-game.net/aki/gacha/index.html#/record"]
        bad = ["https://aki-gm-resources.aki-game.com.evil.com/aki/gacha/index.html#/record?a=1",
               "https://evil.com/aki/gacha/index.html#/record?a=1",
               "http://aki-gm-resources.aki-game.com/aki/gacha/index.html#/record?a=1",
               "https://aki-gm-resources.aki-game.com/aki/gacha/other.html#/record?a=1",
               "https://aki-gm-resources.aki-game.com/aki/gacha/index.html#/notice?a=1"]
        for url in good:
            self.assertTrue(wuwa.is_record_url(url), url)
        for url in bad:
            self.assertFalse(wuwa.is_record_url(url), url)


class ParsingTheUrl(unittest.TestCase):
    def test_fields(self):
        auth = wuwa.parse_record_url(WUWA_URL)
        self.assertEqual((auth.player_id, auth.record_id, auth.resources_id, auth.server_id, auth.lang, auth.oversea),
                         ("100000001", "TOKEN123", "POOL456", "76402e5b20be2c39f095a152090afddc", "zh-Hans", False))

    def test_parameters_before_the_hash_are_understood_too(self):
        url = ("https://aki-gm-resources.aki-game.com/aki/gacha/index.html"
               "?svr_id=S&player_id=P&record_id=R&resources_id=X&lang=zh-Hans#/record")
        auth = wuwa.parse_record_url(url)
        self.assertEqual((auth.player_id, auth.record_id), ("P", "R"))

    def test_global_server_is_detected(self):
        url = WUWA_URL.replace("aki-gm-resources.aki-game.com", "aki-gm-resources-oversea.aki-game.net")
        self.assertTrue(wuwa.parse_record_url(url).oversea)

    def test_missing_parameters_are_reported_by_name(self):
        url = "https://aki-gm-resources.aki-game.com/aki/gacha/index.html#/record?player_id=1"
        with self.assertRaisesRegex(InvalidUrl, "record_id"):
            wuwa.parse_record_url(url)

    def test_foreign_links_are_refused(self):
        for url in (WUWA_ANNOUNCEMENT_URL, "https://evil.com/aki/gacha/index.html#/record?player_id=1", "garbage"):
            with self.assertRaises(InvalidUrl, msg=url):
                wuwa.parse_record_url(url)


class FindingTheLog(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def make_log(self, *parts) -> Path:
        path = self.root.joinpath(*parts, "Client", "Saved", "Logs", "Client.log")
        path.parent.mkdir(parents=True)
        path.write_bytes(b"x")
        return path

    def test_found_in_common_install_locations(self):
        for parts in (("D", "Wuthering Waves", "Wuthering Waves Game"),
                      ("D", "Program Files", "Wuthering Waves", "Wuthering Waves Game"),
                      ("D", "Games", "Kuro", "Wuthering Waves", "Wuthering Waves Game"),
                      ("D", "Program Files (x86)", "Steam", "steamapps", "common", "Wuthering Waves", "Wuthering Waves Game"),
                      ("D", "SteamLibrary", "steamapps", "common", "Wuthering Waves", "Wuthering Waves Game")):
            with self.subTest(parts=parts):
                log = self.make_log(*parts)
                self.assertEqual(wuwa.find_client_log(drive_roots=[self.root / "D"]), log)
                log.unlink()

    def test_the_most_recently_written_log_wins(self):
        import os
        old = self.make_log("D", "Wuthering Waves", "Wuthering Waves Game")
        new = self.make_log("D", "Games", "Wuthering Waves", "Wuthering Waves Game")
        os.utime(old, (1_000_000, 1_000_000))
        os.utime(new, (2_000_000, 2_000_000))
        self.assertEqual(wuwa.find_client_log(drive_roots=[self.root / "D"]), new)

    def test_any_level_the_user_picks_resolves_to_the_log(self):
        log = self.make_log("Wuthering Waves", "Wuthering Waves Game")
        game_dir = log.parents[3]
        for given in (log, log.parent, game_dir, game_dir.parent, str(game_dir.parent), f'"{game_dir}"'):
            self.assertEqual(wuwa.resolve_client_log(given), log, given)

    def test_a_file_that_is_not_client_log_is_rejected(self):
        other = self.root / "something.log"
        other.write_bytes(b"x")
        self.assertIsNone(wuwa.resolve_client_log(other))

    def test_explicit_wrong_folder_is_a_locate_error_not_a_game_not_found(self):
        with self.assertRaises(LocateError) as ctx:
            wuwa.find_client_log(self.root / "nope")
        self.assertNotIsInstance(ctx.exception, GameNotFound)

    def test_system_folders_are_not_searched_and_the_time_budget_is_respected(self):
        self.make_log("D", "Windows", "Wuthering Waves", "Wuthering Waves Game")     # 在系统目录里：不该被找到
        with self.assertRaises(GameNotFound):
            wuwa.find_client_log(drive_roots=[self.root / "D"])
        log = self.make_log("D", "MyGames", "Wuthering Waves", "Wuthering Waves Game")
        self.assertEqual(wuwa.find_client_log(drive_roots=[self.root / "D"]), log)
        with self.assertRaises(GameNotFound):
            wuwa.find_client_log(drive_roots=[self.root / "D"], budget=-1)

    def test_nothing_installed_is_game_not_found(self):
        with self.assertRaises(GameNotFound):
            wuwa.find_client_log(drive_roots=[self.root / "D"])


class BuildingRecords(unittest.TestCase):
    def test_chronological_order_and_fields(self):
        items = [wuwa_item("卡卡罗", 5, "2026-01-02 10:00:00", rid=11),
                 wuwa_item("黑缨枪", 3, "2026-01-01 10:00:00", "武器", rid=22)]   # 接口是从新到旧
        records = wuwa.build_records(items, "1")
        self.assertEqual([r["name"] for r in records], ["黑缨枪", "卡卡罗"])
        self.assertLess(int(records[0]["id"]), int(records[1]["id"]))
        self.assertEqual(records[1], {
            "id": records[1]["id"], "gacha_type": "1", "item_id": "11", "count": "1",
            "time": "2026-01-02 10:00:00", "name": "卡卡罗", "item_type": "角色", "rank_type": "5"})

    def test_identical_items_in_the_same_second_are_all_kept(self):
        # 十连：同一秒里有三个一模一样的 3 星。用“账号+物品+时间”去重会把它们压成一个，这里不能
        same = "2026-01-01 10:00:00"
        items = [wuwa_item("黑缨枪", 3, same, "武器", rid=22) for _ in range(3)]
        ids = [r["id"] for r in wuwa.build_records(items, "1")]
        self.assertEqual(len(set(ids)), 3)

    def test_the_same_batch_always_gets_the_same_ids(self):
        items = [wuwa_item("黑缨枪", 3, "2026-01-01 10:00:00", "武器", rid=22) for _ in range(3)]
        items.append(wuwa_item("今汐", 5, "2026-01-01 10:00:00"))
        first = wuwa.build_records(items, "1")
        second = wuwa.build_records(list(items), "1")
        self.assertEqual([r["id"] for r in first], [r["id"] for r in second])

    def test_ids_never_collide_across_pools(self):
        item = [wuwa_item("黑缨枪", 3, "2026-01-01 10:00:00", "武器")]
        self.assertNotEqual(wuwa.build_records(item, "1")[0]["id"], wuwa.build_records(item, "2")[0]["id"])

    def test_unreadable_time_is_reported(self):
        with self.assertRaises(ApiError):
            wuwa.build_records([wuwa_item("x", 3, "yesterday")], "1")


class Client(unittest.TestCase):
    def auth(self, url=WUWA_URL):
        return wuwa.parse_record_url(url)

    def test_request_body_and_cn_host(self):
        api = FakeWuwaApi({"1": [wuwa_item("今汐", 5, "2026-01-01 10:00:00")]})
        client = wuwa.WuwaClient(post=api.post, sleep=lambda s: None)
        items = client.query_pool(self.auth(), "1")
        self.assertEqual(len(items), 1)
        url, body = api.calls[0]
        self.assertEqual(url, "https://gmserver-api.aki-game2.com/gacha/record/query")
        self.assertEqual(body, {
            "playerId": "100000001", "recordId": "TOKEN123", "cardPoolId": "POOL456",
            "serverId": "76402e5b20be2c39f095a152090afddc", "languageCode": "zh-Hans", "cardPoolType": "1"})

    def test_global_server_uses_the_net_host(self):
        api = FakeWuwaApi({})
        client = wuwa.WuwaClient(post=api.post, sleep=lambda s: None)
        client.query_pool(self.auth(WUWA_URL.replace("aki-gm-resources.aki-game.com", "aki-gm-resources-oversea.aki-game.net")), "1")
        self.assertEqual(api.calls[0][0], "https://gmserver-api.aki-game2.net/gacha/record/query")

    def test_falls_back_to_numeric_pool_type_and_remembers_it(self):
        api = FakeWuwaApi({"1": [wuwa_item("今汐", 5, "2026-01-01 10:00:00")]})
        api.int_only = True
        client = wuwa.WuwaClient(post=api.post, sleep=lambda s: None)
        self.assertEqual(len(client.query_pool(self.auth(), "1")), 1)
        before = len(api.calls)
        client.query_pool(self.auth(), "2")
        self.assertEqual(len(api.calls) - before, 1)         # 记住了要用数字，不再先试字符串
        self.assertIsInstance(api.calls[-1][1]["cardPoolType"], int)

    def test_an_expired_link_is_reported_as_expired(self):
        api = FakeWuwaApi({}, record_id="SOMETHING-ELSE")
        client = wuwa.WuwaClient(post=api.post, sleep=lambda s: None)
        with self.assertRaisesRegex(AuthExpired, "唤取记录"):
            client.query_pool(self.auth(), "1")

    def test_other_errors_carry_the_server_message(self):
        api = FakeWuwaApi({})
        api.fail_pools = {"1": (500, "server busy")}
        client = wuwa.WuwaClient(post=api.post, sleep=lambda s: None)
        with self.assertRaisesRegex(ApiError, "server busy"):
            client.query_pool(self.auth(), "1")

    def test_requests_are_paced(self):
        api, sleeps = FakeWuwaApi({}), []
        client = wuwa.WuwaClient(post=api.post, sleep=sleeps.append)
        for pool in ("1", "2", "3"):
            client.query_pool(self.auth(), pool)
        self.assertEqual(sleeps, [0.3, 0.3])


class WholeGame(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        game_dir = self.root / "D" / "Wuthering Waves" / "Wuthering Waves Game"
        self.log = game_dir / "Client" / "Saved" / "Logs" / "Client.log"
        self.log.parent.mkdir(parents=True)
        self.log.write_bytes(encrypt_client_log("noise\n" + wuwa_log_line(WUWA_URL)))
        self.game_dir = str(self.root / "D" / "Wuthering Waves")
        self.store = Store(self.root / "data" / "wuwa")

        same = "2026-01-02 10:00:00"
        self.pools = {
            "1": [wuwa_item("今汐", 5, same), wuwa_item("黑缨枪", 3, same, "武器"),
                  wuwa_item("黑缨枪", 3, same, "武器"), wuwa_item("白芷", 4, "2026-01-01 10:00:00")],
            "2": [wuwa_item("苍鳞千嶂", 5, "2026-01-03 10:00:00", "武器")],
        }
        self.api = FakeWuwaApi(self.pools)
        self.client = wuwa.WuwaClient(post=self.api.post, sleep=lambda s: None)

    def sync(self):
        auth = WUWA.connect(self.client, "", self.game_dir)
        return WUWA.sync(self.client, auth, self.store)

    def test_connect_reads_the_encrypted_log_and_verifies_the_link(self):
        auth = WUWA.connect(self.client, "", self.game_dir)
        self.assertEqual((auth.player_id, auth.record_id), ("100000001", "TOKEN123"))

    def test_connect_with_a_pasted_link(self):
        auth = WUWA.connect(self.client, WUWA_URL, "/does/not/exist")
        self.assertEqual(auth.player_id, "100000001")

    def test_log_without_a_record_link_says_what_to_do(self):
        self.log.write_bytes(encrypt_client_log(wuwa_log_line(WUWA_ANNOUNCEMENT_URL)))
        with self.assertRaisesRegex(LocateError, "唤取记录"):
            WUWA.connect(self.client, "", self.game_dir)

    def test_first_sync_stores_every_record_including_same_second_duplicates(self):
        result = self.sync()
        self.assertEqual(result["uid"], "100000001")
        self.assertEqual(result["new"]["角色活动唤取"], 4)     # 两个同一秒的 3 星都在
        self.assertEqual(result["new"]["武器活动唤取"], 1)
        self.assertEqual(len(self.store.load("100000001")["records"]), 5)

    def test_syncing_again_adds_nothing(self):
        self.sync()
        self.assertEqual(self.sync()["total_new"], 0)
        self.assertEqual(len(self.store.load("100000001")["records"]), 5)

    def test_new_pulls_are_added_and_old_records_dropped_by_the_server_are_kept(self):
        self.sync()
        self.pools["1"].insert(0, wuwa_item("长离", 5, "2026-02-01 10:00:00"))   # 新抽到一个
        self.assertEqual(self.sync()["total_new"], 1)
        removed = self.pools["1"].pop()      # 服务器只保留半年：最老的一条不再返回
        self.sync()
        names = [r["name"] for r in self.store.load("100000001")["records"]]
        self.assertIn(removed["name"], names)        # 本地仍然留着
        self.assertEqual(len(names), 6)

    def test_optional_pools_failing_are_silent_but_required_pools_warn(self):
        self.api.fail_pools = {"13": (500, "no such pool"), "4": (500, "broken")}
        result = self.sync()
        self.assertEqual(result["total_new"], 5)
        self.assertEqual(len(result["warnings"]), 1)
        self.assertIn("武器常驻唤取", result["warnings"][0])

    def test_if_every_pool_fails_the_sync_fails(self):
        self.api.fail_pools = {str(i): (500, "down") for i in range(1, 14)}
        with self.assertRaises(ApiError):
            self.sync()

    def test_the_diagnosis_finds_the_log_and_never_prints_the_token(self):
        text = "\n".join(WUWA.diagnose(self.game_dir))
        self.assertIn("Client.log", text)
        self.assertIn("找到", text)
        for secret in ("TOKEN123", "POOL456", "76402e5b20be2c39f095a152090afddc"):
            self.assertNotIn(secret, text)
        self.assertIn("player_id", text)         # 只列出参数名

    def test_the_diagnosis_when_nothing_is_installed(self):
        self.assertIn("没找到", "\n".join(WUWA.diagnose(str(self.root / "nowhere"))))


if __name__ == "__main__":
    unittest.main()
