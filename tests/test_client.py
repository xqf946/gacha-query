import unittest
from urllib.parse import parse_qsl, unquote, urlparse

from tests.fakes import VALID_KEY, FakeApi, make_records, wish_url
from wishlog.client import (
    ApiError, AuthExpired, Client, InvalidUrl, parse_wish_url,
)


class ParseUrl(unittest.TestCase):
    def test_keeps_auth_params_and_drops_paging(self):
        auth = parse_wish_url(wish_url())
        params = dict(auth.params)
        for key in ("page", "size", "gacha_type", "end_id"):
            self.assertNotIn(key, params)
        self.assertEqual(params["region"], "cn_gf01")
        self.assertEqual(auth.authkey, unquote(VALID_KEY))

    def test_accepts_surrounding_whitespace_and_quotes(self):
        parse_wish_url(f'  "{wish_url()}"\n')

    def test_accepts_the_web_page_form_of_the_link(self):
        url = wish_url().replace("/gacha_info/api/getGachaLog", "/hk4e/event/e20190909gacha-v3/index.html") + "#/log"
        self.assertEqual(parse_wish_url(url).authkey, unquote(VALID_KEY))

    def test_rejects_foreign_hosts(self):
        for host in ("evil.com", "mihoyo.com.evil.com", "notmihoyo.com", "public-operation-hk4e-sg.hoyoverse.com"):
            with self.assertRaises(InvalidUrl, msg=host):
                parse_wish_url(wish_url(host=host))

    def test_rejects_plain_http(self):
        with self.assertRaises(InvalidUrl):
            parse_wish_url(wish_url().replace("https://", "http://"))

    def test_rejects_other_games_and_broken_links(self):
        for url in (
            wish_url(game_biz="hk4e_global"),
            wish_url(authkey=""),
            wish_url(authkey="bad key!<>"),
            wish_url(region=""),
            "not a url",
        ):
            with self.assertRaises(InvalidUrl, msg=url):
                parse_wish_url(url)


class FetchPage(unittest.TestCase):
    def make(self, responses):
        urls = []
        sleeps = []

        def fetch(url):
            urls.append(url)
            return responses.pop(0)

        return Client(fetch=fetch, sleep=sleeps.append), urls, sleeps

    def test_request_goes_to_official_host_with_encoded_authkey(self):
        client, urls, _ = self.make([{"retcode": 0, "data": {"list": []}}])
        client.fetch_page(parse_wish_url(wish_url()), "302", 3, "999")
        parsed = urlparse(urls[0])
        self.assertEqual(parsed.netloc, "public-operation-hk4e.mihoyo.com")
        self.assertEqual(parsed.path, "/gacha_info/api/getGachaLog")
        q = dict(parse_qsl(parsed.query))
        self.assertEqual((q["gacha_type"], q["page"], q["size"], q["end_id"]), ("302", "3", "20", "999"))
        self.assertEqual(q["authkey"], unquote(VALID_KEY))  # 特殊字符经过往返后不变
        self.assertIn("%2B", parsed.query)                   # 而且在 URL 里是编码的

    def test_null_data_is_treated_as_empty(self):
        client, _, _ = self.make([{"retcode": 0, "data": None}])
        self.assertEqual(client.fetch_page(parse_wish_url(wish_url()), "301", 1), [])

    def test_authkey_timeout(self):
        client, _, _ = self.make([{"retcode": -101, "message": "authkey timeout"}])
        with self.assertRaises(AuthExpired):
            client.fetch_page(parse_wish_url(wish_url()), "301", 1)

    def test_too_frequent_is_retried_then_succeeds(self):
        client, urls, sleeps = self.make([
            {"retcode": -110, "message": "visit too frequently"},
            {"retcode": -110, "message": "visit too frequently"},
            {"retcode": 0, "data": {"list": [{"id": "1"}]}},
        ])
        self.assertEqual(client.fetch_page(parse_wish_url(wish_url()), "301", 1), [{"id": "1"}])
        self.assertEqual(len(urls), 3)
        self.assertIn(3, sleeps)  # 退避等待过

    def test_too_frequent_forever_gives_up(self):
        client, _, _ = self.make([{"retcode": -110, "message": "x"}] * 5)
        with self.assertRaises(ApiError):
            client.fetch_page(parse_wish_url(wish_url()), "301", 1)

    def test_other_error_surfaces_message(self):
        client, _, _ = self.make([{"retcode": -999, "message": "boom"}])
        with self.assertRaisesRegex(ApiError, "boom"):
            client.fetch_page(parse_wish_url(wish_url()), "301", 1)

    def test_requests_are_paced(self):
        client, _, sleeps = self.make([{"retcode": 0, "data": {"list": []}}] * 3)
        auth = parse_wish_url(wish_url())
        for page in (1, 2, 3):
            client.fetch_page(auth, "301", page)
        self.assertEqual(sleeps, [0.3, 0.3])  # 第一次请求前不用等


class PickValidAuth(unittest.TestCase):
    def setUp(self):
        self.api = FakeApi({"301": make_records("301", 3)})
        self.client = Client(fetch=self.api.fetch, sleep=lambda s: None)

    def test_skips_expired_links_and_returns_first_valid(self):
        urls = [wish_url("EXPIRED1"), wish_url("EXPIRED2"), wish_url(VALID_KEY)]
        auth = self.client.pick_valid_auth(urls)
        self.assertEqual(auth.authkey, unquote(VALID_KEY))

    def test_all_expired(self):
        with self.assertRaises(AuthExpired):
            self.client.pick_valid_auth([wish_url("A"), wish_url("B")])

    def test_unparseable_links_are_skipped(self):
        auth = self.client.pick_valid_auth(["garbage", wish_url(VALID_KEY)])
        self.assertEqual(auth.authkey, unquote(VALID_KEY))

    def test_gives_up_after_a_few_probes(self):
        urls = [wish_url(f"EXPIRED{i}") for i in range(20)] + [wish_url(VALID_KEY)]
        with self.assertRaises(AuthExpired):
            self.client.pick_valid_auth(urls)
        self.assertLessEqual(len(self.api.calls), 6)


if __name__ == "__main__":
    unittest.main()
