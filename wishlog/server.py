"""本地网页服务：只监听 127.0.0.1，提供界面和一组 JSON 接口。"""

from __future__ import annotations

import csv
import io
import json
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from .job import SyncJob
from .pools import POOL_BY_GACHA_TYPE
from .stats import analyze
from .store import Store

STATIC_DIR = Path(__file__).parent / "static"
MAX_BODY = 64 * 1024


def make_handler(store: Store, job: SyncJob):
    class Handler(BaseHTTPRequestHandler):
        server_version = "wishlog"

        # ---- 工具 ----
        def log_message(self, fmt, *args):  # 不往控制台刷每个请求
            pass

        def _local_origins(self) -> set:
            port = self.server.server_address[1]
            return {f"127.0.0.1:{port}", f"localhost:{port}"}

        def _guard(self) -> bool:
            """只接受发给本机地址的请求，防止别的网页借浏览器来操作这个服务。"""
            if self.headers.get("Host", "") not in self._local_origins():
                self._send(403, b"forbidden", "text/plain")
                return False
            origin = self.headers.get("Origin")
            if origin and origin.split("://", 1)[-1] not in self._local_origins():
                self._send(403, b"forbidden", "text/plain")
                return False
            return True

        def _send(self, status, body: bytes, ctype: str, headers=None):
            self.send_response(status)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            for k, v in (headers or {}).items():
                self.send_header(k, v)
            self.end_headers()
            self.wfile.write(body)

        def _json(self, obj, status=200):
            body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
            self._send(status, body, "application/json; charset=utf-8")

        def _uid(self, query) -> str | None:
            uid = (query.get("uid") or [""])[0]
            return uid if uid.isdigit() and len(uid) <= 20 else None

        # ---- 路由 ----
        def do_GET(self):
            if not self._guard():
                return
            url = urlparse(self.path)
            query = parse_qs(url.query)
            if url.path == "/":
                self._send(200, (STATIC_DIR / "index.html").read_bytes(), "text/html; charset=utf-8")
            elif url.path == "/api/state":
                self._json({"uids": store.uids(), "sync": job.status()})
            elif url.path == "/api/sync/status":
                self._json(job.status())
            elif url.path == "/api/wishes":
                uid = self._uid(query)
                if not uid or uid not in store.uids():
                    self._json({"error": "没有这个 UID 的记录"}, 404)
                    return
                doc = store.load(uid)
                self._json({
                    "uid": uid,
                    "updated_at": doc["updated_at"],
                    "pools": analyze(doc["records"]),
                })
            elif url.path == "/api/export":
                self._export(query)
            else:
                self._send(404, b"not found", "text/plain")

        def do_POST(self):
            if not self._guard():
                return
            if urlparse(self.path).path != "/api/sync":
                self._send(404, b"not found", "text/plain")
                return
            if not self.headers.get("Content-Type", "").startswith("application/json"):
                self._send(415, b"json only", "text/plain")
                return
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if length > MAX_BODY:
                    raise ValueError("body too large")
                body = json.loads(self.rfile.read(length) or b"{}")
                url, game_dir = str(body.get("url", "")), str(body.get("game_dir", ""))
            except (ValueError, AttributeError):
                self._json({"error": "请求格式不对"}, 400)
                return
            started = job.start(url=url, game_dir=game_dir)
            self._json(job.status(), 202 if started else 409)

        def _export(self, query):
            uid = self._uid(query)
            fmt = (query.get("fmt") or ["csv"])[0]
            if not uid or uid not in store.uids() or fmt not in ("csv", "json"):
                self._json({"error": "参数不对"}, 400)
                return
            records = store.load(uid)["records"]
            rows = [
                {**r, "uid": uid, "pool": POOL_BY_GACHA_TYPE[r["gacha_type"]].name}
                for r in records if r["gacha_type"] in POOL_BY_GACHA_TYPE
            ]
            name = f"genshin_wishes_{uid}"
            if fmt == "json":
                body = json.dumps(
                    {"uid": uid, "exported_at": datetime.now().isoformat(timespec="seconds"),
                     "records": rows},
                    ensure_ascii=False, indent=1,
                ).encode("utf-8")
                self._send(200, body, "application/json; charset=utf-8",
                           {"Content-Disposition": f'attachment; filename="{name}.json"'})
                return
            out = io.StringIO()
            writer = csv.writer(out)
            writer.writerow(["时间", "卡池", "名称", "类别", "星级", "记录ID"])
            for r in rows:
                writer.writerow([r["time"], r["pool"], r["name"], r["item_type"], r["rank_type"], r["id"]])
            # 加 BOM，Excel 才会把中文当成 UTF-8 正确显示
            body = ("﻿" + out.getvalue()).encode("utf-8")
            self._send(200, body, "text/csv; charset=utf-8",
                       {"Content-Disposition": f'attachment; filename="{name}.csv"'})

    return Handler


def create_server(store: Store, job: SyncJob, port: int = 8765) -> ThreadingHTTPServer:
    """从 port 开始往后找一个没被占用的端口。"""
    last: OSError | None = None
    for p in range(port, port + 20):
        try:
            return ThreadingHTTPServer(("127.0.0.1", p), make_handler(store, job))
        except OSError as e:
            last = e
    raise last  # type: ignore[misc]
