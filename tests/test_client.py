import unittest
from urllib.parse import parse_qsl, unquote, urlparse

from tests.fakes import VALID_KEY, FakeApi, game_url, make_records, wish_url
from wishlog.client import ApiError, AuthExpired, Client, InvalidUrl, parse_wish_url
from wishlog.games import GENSHIN, HSR, ZZZ

SPEC = GENSHIN.api


class ParseUrl(unittest.TestCase):
    def test_keeps_auth_params_and_drops_paging(self):
        auth = parse_wish_url(wish_url(), SPEC)
        params = dict(auth.params)
        for key in ("page", "size", "gacha_type", "real_gacha_type", "end_id"):
            self.assertNotIn(key, params)
        self.assertEqual(params["region"], "cn_gf01")
        self.assertEqual(auth.authkey, unquote(VALID_KEY))

    def test_accepts_surrounding_whitespace_and_quotes(self):
        parse_wish_url(f'  "{wish_url()}"\n', SPEC)

    def test_accepts_the_web_page_form_of_the_link(self):
        url = wish_url().replace("/gacha_info/api/getGachaLog", "/hk4e/event/e20190909gacha-v3/index.html") + "#/log"
        self.assertEqual(parse_wish_url(url, SPEC).authkey, unquote(VALID_KEY))

    def test_bilibili_server_links_are_accepted(self):
        parse_wish_url(wish_url(game_biz="hk4e_bilibili"), SPEC)
        parse_wish_url(game_url("hkrpg").replace("hkrpg_cn", "hkrpg_bilibili"), HSR.api)

    def test_rejects_foreign_hosts(self):
        for host in ("evil.com", "mihoyo.com.evil.com", "notmihoyo.com", "public-operation-hk4e-sg.hoyoverse.com"):
            with self.assertRaises(InvalidUrl, msg=host):
                parse_wish_url(wish_url(host=host), SPEC)

    def test_rejects_plain_http(self):
        with self.assertRaises(InvalidUrl):
            parse_wish_url(wish_url().replace("https://", "http://"), SPEC)

    def test_rejects_other_games_and_broken_links(self):
        for url in (
            wish_url(game_biz="hk4e_global"),
            wish_url(authkey=""),
            wish_url(authkey="bad key!<>"),
            wish_url(region=""),
            "not a url",
        ):
            with self.assertRaises(InvalidUrl, msg=url):
                parse_wish_url(url, SPEC)

    def test_a_link_from_another_game_is_refused(self):
        with self.assertRaises(InvalidUrl):
            parse_wish_url(game_url("hkrpg"), GENSHIN.api)   # 崩铁的链接不能当原神的用
        with self.assertRaises(InvalidUrl):
            parse_wish_url(game_url("hk4e"), ZZZ.api)

    def test_the_game_specific_type_parameter_is_dropped_from_the_link(self):
        url = game_url("nap", real_gacha_type="2")
        self.assertNotIn("real_gacha_type", dict(parse_wish_url(url, ZZZ.api).params))


class FetchPage(unittest.TestCase):
    def make(self, responses, spec=SPEC):
        urls = []
        sleeps = []

        def fetch(url):
            urls.append(url)
            return responses.pop(0)

        return Client(spec, fetch=fetch, sleep=sleeps.append), urls, sleeps

    def test_request_goes_to_official_host_with_encoded_authkey(self):
        client, urls, _ = self.make([{"retcode": 0, "data": {"list": []}}])
        client.fetch_page(parse_wish_url(wish_url(), SPEC), "302", 3, "999")
        parsed = urlparse(urls[0])
        self.assertEqual(parsed.netloc, "public-operation-hk4e.mihoyo.com")
        self.assertEqual(parsed.path, "/gacha_info/api/getGachaLog")
        q = dict(parse_qsl(parsed.query))
        self.assertEqual((q["gacha_type"], q["page"], q["size"], q["end_id"]), ("302", "3", "20", "999"))
        self.assertEqual(q["authkey"], unquote(VALID_KEY))  # 特殊字符经过往返后不变
        self.assertIn("%2B", parsed.query)                   # 而且在 URL 里是编码的

    def test_zzz_uses_real_gacha_type_and_its_own_host(self):
        client, urls, _ = self.make([{"retcode": 0, "data": {"list": []}}], ZZZ.api)
        client.fetch_page(parse_wish_url(game_url("nap"), ZZZ.api), "3", 1)
        parsed = urlparse(urls[0])
        q = dict(parse_qsl(parsed.query))
        self.assertEqual(parsed.netloc, "public-operation-nap.mihoyo.com")
        self.assertEqual(parsed.path, "/common/gacha_record/api/getGachaLog")
        self.assertEqual(q["real_gacha_type"], "3")
        self.assertNotIn("gacha_type", q)

    def test_hsr_collaboration_pools_use_the_ld_endpoint(self):
        client, urls, _ = self.make([{"retcode": 0, "data": {"list": []}}] * 2, HSR.api)
        auth = parse_wish_url(game_url("hkrpg"), HSR.api)
        client.fetch_page(auth, "11", 1)
        client.fetch_page(auth, "21", 1, ld=True)
        self.assertEqual(urlparse(urls[0]).path, "/common/hkrpg_gacha_record/api/getGachaLog")
        self.assertEqual(urlparse(urls[1]).path, "/common/hkrpg_gacha_record/api/getLdGachaLog")

    def test_null_data_is_treated_as_empty(self):
        client, _, _ = self.make([{"retcode": 0, "data": None}])
        self.assertEqual(client.fetch_page(parse_wish_url(wish_url(), SPEC), "301", 1), [])

    def test_authkey_timeout(self):
        client, _, _ = self.make([{"retcode": -101, "message": "authkey timeout"}])
        with self.assertRaises(AuthExpired):
            client.fetch_page(parse_wish_url(wish_url(), SPEC), "301", 1)

    def test_too_frequent_is_retried_then_succeeds(self):
        client, urls, sleeps = self.make([
            {"retcode": -110, "message": "visit too frequently"},
            {"retcode": -110, "message": "visit too frequently"},
            {"retcode": 0, "data": {"list": [{"id": "1"}]}},
        ])
        self.assertEqual(client.fetch_page(parse_wish_url(wish_url(), SPEC), "301", 1), [{"id": "1"}])
        self.assertEqual(len(urls), 3)
        self.assertIn(3, sleeps)  # 退避等待过

    def test_too_frequent_forever_gives_up(self):
        client, _, _ = self.make([{"retcode": -110, "message": "x"}] * 5)
        with self.assertRaises(ApiError):
            client.fetch_page(parse_wish_url(wish_url(), SPEC), "301", 1)

    def test_other_error_surfaces_message(self):
        client, _, _ = self.make([{"retcode": -999, "message": "boom"}])
        with self.assertRaisesRegex(ApiError, "boom"):
            client.fetch_page(parse_wish_url(wish_url(), SPEC), "301", 1)

    def test_requests_are_paced(self):
        client, _, sleeps = self.make([{"retcode": 0, "data": {"list": []}}] * 3)
        auth = parse_wish_url(wish_url(), SPEC)
        for page in (1, 2, 3):
            client.fetch_page(auth, "301", page)
        self.assertEqual(sleeps, [0.3, 0.3])  # 第一次请求前不用等


class PickValidAuth(unittest.TestCase):
    def setUp(self):
        self.api = FakeApi({"301": make_records("301", 3)})
        self.client = Client(SPEC, fetch=self.api.fetch, sleep=lambda s: None)

    def test_skips_expired_links_and_returns_first_valid(self):
        urls = [wish_url("EXPIRED1"), wish_url("EXPIRED2"), wish_url(VALID_KEY)]
        auth = self.client.pick_valid_auth(urls, "301")
        self.assertEqual(auth.authkey, unquote(VALID_KEY))

    def test_all_expired(self):
        with self.assertRaises(AuthExpired):
            self.client.pick_valid_auth([wish_url("A"), wish_url("B")], "301")

    def test_unparseable_links_are_skipped(self):
        auth = self.client.pick_valid_auth(["garbage", wish_url(VALID_KEY)], "301")
        self.assertEqual(auth.authkey, unquote(VALID_KEY))

    def test_gives_up_after_a_few_probes(self):
        urls = [wish_url(f"EXPIRED{i}") for i in range(20)] + [wish_url(VALID_KEY)]
        with self.assertRaises(AuthExpired):
            self.client.pick_valid_auth(urls, "301")
        self.assertLessEqual(len(self.api.calls), 6)

    def test_probes_with_the_given_pool(self):
        self.client.pick_valid_auth([wish_url(VALID_KEY)], "302")
        self.assertEqual(self.api.calls[0][0], "302")


if __name__ == "__main__":
    unittest.main()
