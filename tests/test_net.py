"""网络层：用一个真的本地 HTTP 服务器测 Cookie、错误响应、重试，不依赖外网。"""

import json
import threading
import unittest
import urllib.error
from http.server import BaseHTTPRequestHandler, HTTPServer

from wishlog.client import ApiError, InvalidUrl, NetworkError
from wishlog.net import SafeTransport, UrllibTransport, format_ts, host_allowed


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def _send(self, status, body, ctype="application/json", cookie=None):
        raw = body.encode("utf-8") if isinstance(body, str) else json.dumps(body).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(raw)))
        if cookie:
            self.send_header("Set-Cookie", cookie)
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self):
        if self.path == "/echo":
            self._send(200, {"ua": self.headers.get("User-Agent"), "x": self.headers.get("X-Test")})
        elif self.path == "/needs-cookie":
            ok = "session=abc" in (self.headers.get("Cookie") or "")
            self._send(200, {"code": 0} if ok else {"reason": "MissingCookie"})
        elif self.path == "/html":
            self._send(200, "<!doctype html><html></html>", "text/html")
        elif self.path == "/garbage":
            self._send(200, "not json at all", "text/plain")
        elif self.path == "/unauthorized":
            self._send(401, {"message": "未登录", "reason": "UN_LOGIN"})
        else:
            self._send(404, "nope", "text/plain")

    def do_POST(self):
        length = int(self.headers.get("Content-Length", "0"))
        body = json.loads(self.rfile.read(length) or b"{}")
        if self.path == "/login":
            self._send(200, {"code": 0, "got": body, "ctype": self.headers.get("Content-Type")},
                       cookie="session=abc; Path=/")
        else:
            self._send(200, {"got": body})


class Transport(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = HTTPServer(("127.0.0.1", 0), Handler)
        cls.base = f"http://127.0.0.1:{cls.server.server_address[1]}"
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def make(self, sleeps=None):
        return UrllibTransport(sleep=(sleeps.append if sleeps is not None else lambda s: None))

    def test_get_sends_the_headers_and_parses_json(self):
        data = self.make()("GET", f"{self.base}/echo", {"X-Test": "hello"}, None)
        self.assertEqual(data["x"], "hello")
        self.assertIn("Mozilla", data["ua"])

    def test_post_sends_a_json_body(self):
        data = self.make()("POST", f"{self.base}/other", {}, {"token": "t", "中文": "值"})
        self.assertEqual(data["got"], {"token": "t", "中文": "值"})

    def test_cookies_set_by_one_response_are_sent_with_the_next_request(self):
        transport = self.make()
        self.assertEqual(transport("GET", f"{self.base}/needs-cookie", {}, None).get("reason"), "MissingCookie")
        login = transport("POST", f"{self.base}/login", {}, {"token": "u8"})
        self.assertIn("json", login["ctype"])
        self.assertEqual(transport("GET", f"{self.base}/needs-cookie", {}, None), {"code": 0})

    def test_each_transport_has_its_own_cookie_jar(self):
        self.make()("POST", f"{self.base}/login", {}, {})
        self.assertEqual(self.make()("GET", f"{self.base}/needs-cookie", {}, None).get("reason"), "MissingCookie")

    def test_an_error_status_with_a_json_body_is_still_readable(self):
        data = self.make()("GET", f"{self.base}/unauthorized", {}, None)
        self.assertEqual(data["reason"], "UN_LOGIN")

    def test_a_web_page_instead_of_json_is_explained(self):
        with self.assertRaisesRegex(ApiError, "网页"):
            self.make()("GET", f"{self.base}/html", {}, None)

    def test_other_garbage_is_reported(self):
        with self.assertRaisesRegex(ApiError, "无法解析"):
            self.make()("GET", f"{self.base}/garbage", {}, None)

    def test_unreachable_server_is_retried_then_reported(self):
        sleeps = []
        with self.assertRaises(NetworkError):
            self.make(sleeps)("GET", "http://127.0.0.1:1/unreachable", {}, None)    # 1 号端口没人监听
        self.assertEqual(sleeps, [1, 2, 3])


class Safety(unittest.TestCase):
    def test_only_https_hypergryph_hosts_are_allowed(self):
        for url in ("https://ak.hypergryph.com/x", "https://as.hypergryph.com/", "https://u8.hypergryph.com/a?b=1",
                    "https://binding-api-account-prod.hypergryph.com/", "https://ef-webview.hypergryph.com/api"):
            self.assertTrue(host_allowed(url), url)
        for url in ("http://ak.hypergryph.com/", "https://evil.com/", "https://hypergryph.com.evil.com/",
                    "https://nothypergryph.com/", "https://ak.hypergryph.com@evil.com/", "ftp://ak.hypergryph.com/", ""):
            self.assertFalse(host_allowed(url), url)

    def test_the_gate_stops_the_request_before_it_reaches_the_inner_transport(self):
        seen = []
        safe = SafeTransport(lambda *args: seen.append(args) or {"ok": 1})
        self.assertEqual(safe("GET", "https://ak.hypergryph.com/x", {}, None), {"ok": 1})
        with self.assertRaises(InvalidUrl):
            safe("POST", "https://evil.example.com/steal", {"x-role-token": "SECRET"}, {"token": "SECRET"})
        self.assertEqual(len(seen), 1)            # 坏请求没有传到下一层


class Time(unittest.TestCase):
    def test_milliseconds_and_seconds_give_the_same_china_time(self):
        self.assertEqual(format_ts("1770697079082"), "2026-02-10 12:17:59")
        self.assertEqual(format_ts("1770697079"), "2026-02-10 12:17:59")

    def test_not_a_number_is_reported(self):
        for bad in ("", "abc", "-5", "1.5"):
            with self.assertRaises(ApiError, msg=bad):
                format_ts(bad)


if __name__ == "__main__":
    unittest.main()
