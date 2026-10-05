"""后台更新任务：在线程里跑一次完整的“找链接 → 验证 → 抓取”，界面轮询它的进度。"""

from __future__ import annotations

import threading

from .client import Client, WishError, parse_wish_url
from .locate import LocateError, find_data_dir, find_wish_urls
from .store import Store
from .sync import sync_all


class SyncJob:
    def __init__(self, store: Store, client_factory=Client, default_game_dir=None):
        self._store = store
        self._client_factory = client_factory
        self._default_game_dir = default_game_dir
        self._lock = threading.Lock()
        self._status = {"state": "idle", "message": "", "uid": None}

    def status(self) -> dict:
        with self._lock:
            return dict(self._status)

    def _set(self, **fields) -> None:
        with self._lock:
            self._status.update(fields)

    def start(self, url: str = "", game_dir: str = "") -> bool:
        """已经有任务在跑时返回 False。"""
        with self._lock:
            if self._status["state"] == "running":
                return False
            self._status = {"state": "running", "message": "正在查找祈愿链接…", "uid": None}
        threading.Thread(target=self.run, args=(url, game_dir), daemon=True).start()
        return True

    def run(self, url: str = "", game_dir: str = "") -> None:
        try:
            client = self._client_factory()
            if url.strip():
                auth = parse_wish_url(url)
                self._set(message="正在验证链接…")
                client.fetch_page(auth, "301", 1)
            else:
                data_dir = find_data_dir(game_dir.strip() or self._default_game_dir)
                self._set(message="正在验证缓存里的祈愿链接…")
                auth = client.pick_valid_auth(find_wish_urls(data_dir))

            def progress(pool_name, fetched, new_total):
                self._set(message=f"正在获取「{pool_name}」…已读到 {fetched} 条新记录")

            result = sync_all(client, auth, self._store, progress)
            total = result["total_new"]
            message = f"更新完成，新增 {total} 条记录。" if total else "已经是最新的，没有新记录。"
            self._set(state="done", message=message, uid=result["uid"])
        except (WishError, LocateError) as e:
            self._set(state="error", message=str(e))
        except Exception as e:  # 兜底：不管出什么问题，界面都应该看到一个结果而不是一直转圈
            self._set(state="error", message=f"出现了意料之外的错误：{type(e).__name__}: {e}")
