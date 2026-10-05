"""端到端：真的起一个本地服务，用假官方接口和假的游戏缓存文件跑完整流程。"""

import http.client
import json
import tempfile
import threading
import time
import unittest
from pathlib import Path

from tests.fakes import FakeApi, VALID_KEY, make_records, wish_url
from tests.test_locate import blob, build_game, write_cache
from wishlog.client import Client
from wishlog.job import SyncJob
from wishlog.server import create_server
from wishlog.store import Store


class ServerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)

        self.api = FakeApi({"301": make_records("301", 45), "302": make_records("302", 8, seed=3)})
        self.data_dir = build_game(root)
        # 缓存里放两条链接：靠前的是过期的旧链接，靠后（最新）的有效
        write_cache(self.data_dir, "5.0.0.0", blob(wish_url("OLDEXPIRED"), wish_url(VALID_KEY)))

        store = Store(root / "data")
        job = SyncJob(
            store,
            client_factory=lambda: Client(fetch=self.api.fetch, sleep=lambda s: None),
            default_game_dir=str(self.data_dir),
        )
        self.server = create_server(store, job, port=0)
        self.port = self.server.server_address[1]
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)

    def request(self, method, path, body=None, headers=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        conn.request(method, path, body=body, headers=headers or {})
        resp = conn.getresponse()
        data = resp.read()
        conn.close()
        return resp.status, dict(resp.getheaders()), data

    def get_json(self, path):
        status, _, data = self.request("GET", path)
        return status, json.loads(data)

    def sync_and_wait(self, payload=None):
        status, _, _ = self.request(
            "POST", "/api/sync", json.dumps(payload or {}), {"Content-Type": "application/json"})
        self.assertIn(status, (202, 409))
        for _ in range(100):
            _, st = self.get_json("/api/sync/status")
            if st["state"] != "running":
                return st
            time.sleep(0.05)
        self.fail("同步一直没有结束")

    def test_index_page_is_served(self):
        status, headers, body = self.request("GET", "/")
        self.assertEqual(status, 200)
        self.assertIn("text/html", headers["Content-Type"])
        self.assertIn("原神抽卡记录".encode(), body)

    def test_full_flow_from_game_cache_to_stats_and_export(self):
        self.assertEqual(self.get_json("/api/state")[1]["uids"], [])

        st = self.sync_and_wait()
        self.assertEqual((st["state"], st["uid"]), ("done", "100000001"))
        self.assertIn("53", st["message"])    # 45 + 8 条

        _, state = self.get_json("/api/state")
        self.assertEqual(state["uids"], ["100000001"])

        status, wishes = self.get_json("/api/wishes?uid=100000001")
        self.assertEqual(status, 200)
        totals = {p["key"]: p["total"] for p in wishes["pools"]}
        self.assertEqual(totals, {"301": 45, "302": 8, "500": 0, "200": 0, "100": 0})

        status, headers, body = self.request("GET", "/api/export?uid=100000001&fmt=csv")
        self.assertEqual(status, 200)
        self.assertTrue(body.startswith("﻿".encode()))          # 带 BOM，Excel 能正确显示中文
        self.assertEqual(len(body.decode("utf-8-sig").strip().splitlines()), 1 + 53)
        self.assertIn("attachment", headers["Content-Disposition"])

        status, _, body = self.request("GET", "/api/export?uid=100000001&fmt=json")
        exported = json.loads(body)
        self.assertEqual(len(exported["records"]), 53)
        self.assertEqual(exported["records"][0]["uid"], "100000001")

        # 再更新一次：没有新记录
        st = self.sync_and_wait()
        self.assertEqual(st["state"], "done")
        self.assertIn("没有新记录", st["message"])

    def test_pasting_a_url_manually(self):
        st = self.sync_and_wait({"url": wish_url(VALID_KEY, page="2"), "game_dir": "/does/not/exist"})
        self.assertEqual(st["state"], "done")

    def test_expired_link_reports_a_helpful_error(self):
        self.api.valid_key = "SOMETHINGELSE"
        st = self.sync_and_wait()
        self.assertEqual(st["state"], "error")
        self.assertIn("历史记录", st["message"])

    def test_missing_game_gives_a_helpful_error(self):
        st = self.sync_and_wait({"game_dir": "/does/not/exist"})
        self.assertEqual(st["state"], "error")
        self.assertIn("YuanShen_Data", st["message"])

    def test_pasted_link_for_a_foreign_host_is_refused_without_any_request(self):
        st = self.sync_and_wait({"url": wish_url(host="evil.example.com")})
        self.assertEqual(st["state"], "error")
        self.assertEqual(self.api.calls, [])    # authkey 没有被发给任何地方

    def test_unknown_uid_and_path_traversal(self):
        for uid in ("999", "..%2F..%2Fetc%2Fpasswd", "abc"):
            self.assertEqual(self.get_json(f"/api/wishes?uid={uid}")[0], 404, uid)
        self.assertEqual(self.request("GET", "/api/export?uid=999&fmt=csv")[0], 400)
        self.assertEqual(self.request("GET", "/static/../server.py")[0], 404)

    def test_requests_for_other_hostnames_are_rejected(self):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        conn.putrequest("GET", "/api/state", skip_host=True)
        conn.putheader("Host", "evil.example.com")   # DNS 重绑定攻击会带上别的域名
        conn.endheaders()
        self.assertEqual(conn.getresponse().status, 403)

    def test_cross_site_posts_are_rejected(self):
        headers = {"Content-Type": "application/json", "Origin": "http://evil.example.com"}
        self.assertEqual(self.request("POST", "/api/sync", "{}", headers)[0], 403)
        self.assertEqual(self.request("POST", "/api/sync", "{}", {"Content-Type": "text/plain"})[0], 415)
        self.assertEqual(self.request("POST", "/api/sync", "not json", {"Content-Type": "application/json"})[0], 400)
        self.assertEqual(self.get_json("/api/sync/status")[1]["state"], "idle")   # 以上都没有触发更新


if __name__ == "__main__":
    unittest.main()
