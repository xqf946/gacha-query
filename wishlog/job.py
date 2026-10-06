"""后台更新任务：在线程里跑“找链接 → 验证 → 抓取”，界面轮询它的进度。

可以更新单个游戏，也可以“更新全部”：依次检查每个游戏，没装的静默跳过，
某个游戏失败不影响其他游戏。
"""

from __future__ import annotations

import threading
from pathlib import Path

from .client import WishError
from .games.base import Game
from .locate import GameNotFound, LocateError, NeedsInput
from .settings import Settings
from .store import Store


def _first_line(text: str) -> str:
    return str(text).strip().splitlines()[0] if str(text).strip() else ""


class SyncJob:
    def __init__(self, data_dir, games, settings: Settings, client_factory=None, default_game_dirs=None):
        self._data_dir = Path(data_dir)
        self._games = {g.key: g for g in games}
        self._settings = settings
        self._client_factory = client_factory or (lambda game: game.make_client())
        self._default_dirs = default_game_dirs or {}
        self._lock = threading.Lock()
        self._status = self._idle()

    @staticmethod
    def _idle() -> dict:
        return {"state": "idle", "message": "", "game": None, "uid": None, "results": [], "warn": False}

    def status(self) -> dict:
        with self._lock:
            return dict(self._status)

    def _set(self, **fields) -> None:
        with self._lock:
            self._status.update(fields)

    def start(self, game: str = "all", url: str = "", game_dir: str = "") -> bool:
        """已经有任务在跑时返回 False。"""
        if game != "all" and game not in self._games:
            raise ValueError(f"未知的游戏：{game}")
        with self._lock:
            if self._status["state"] == "running":
                return False
            self._status = {**self._idle(), "state": "running", "message": "正在查找祈愿链接…"}
        threading.Thread(target=self.run, args=(game, url, game_dir), daemon=True).start()
        return True

    def _connect(self, game: Game, client, url: str, explicit_dir: str):
        if explicit_dir:
            return game.connect(client, url, explicit_dir)
        remembered = self._settings.game_dir(game.key) or self._default_dirs.get(game.key, "")
        if remembered:
            try:
                return game.connect(client, url, remembered)
            except (GameNotFound, NeedsInput):
                raise
            except LocateError:
                pass   # 以前记住的目录可能已经失效（游戏搬过家），退回自动查找
        return game.connect(client, url, "")

    def _run_one(self, game: Game, index: int, total: int, url: str, game_dir: str) -> dict:
        single = total == 1
        prefix = "" if single else f"（{index}/{total}）"
        self._set(game=game.key, message=f"{prefix}正在检查「{game.name}」…")
        try:
            client = self._client_factory(game)
            explicit = game_dir.strip() if single else ""
            auth = self._connect(game, client, url if single else "", explicit)

            def progress(pool_name, fetched, new_total):
                self._set(message=f"{prefix}「{game.name}」正在获取「{pool_name}」…已读到 {fetched} 条新记录")

            result = game.sync(client, auth, Store(self._data_dir / game.key), progress)
            if explicit and not url.strip():
                self._settings.set_game_dir(game.key, explicit)   # 手动选的目录能用，就记住
            return {"game": game.key, "name": game.name, "state": "done", "uid": result["uid"],
                    "new": result["total_new"], "warnings": result.get("warnings", []), "message": ""}
        except GameNotFound as e:
            return {"game": game.key, "name": game.name, "state": "skipped", "reason": "not_found", "message": str(e)}
        except NeedsInput as e:   # 需要用户手动粘贴凭证（明日方舟）：“更新全部”时不能替用户做，只能提示
            return {"game": game.key, "name": game.name, "state": "skipped", "reason": "needs_input", "message": str(e)}
        except (WishError, LocateError) as e:
            return {"game": game.key, "name": game.name, "state": "error", "message": str(e)}
        except Exception as e:  # 兜底：不管出什么问题，界面都应该看到一个结果而不是一直转圈
            return {"game": game.key, "name": game.name, "state": "error",
                    "message": f"出现了意料之外的错误：{type(e).__name__}: {e}"}

    def run(self, game: str = "all", url: str = "", game_dir: str = "") -> None:
        targets = list(self._games.values()) if game == "all" else [self._games[game]]
        results = [self._run_one(g, i, len(targets), url, game_dir) for i, g in enumerate(targets, 1)]
        if len(targets) == 1:
            self._finish_single(results[0])
        else:
            self._finish_all(results)

    def _finish_single(self, r: dict) -> None:
        if r["state"] != "done":
            self._set(state="error", message=r["message"], results=[r], warn=False)
            return
        message = f"更新完成，新增 {r['new']} 条记录。" if r["new"] else "已经是最新的，没有新记录。"
        if r["warnings"]:
            message += "\n" + "\n".join(r["warnings"])
        self._set(state="done", message=message, uid=r["uid"], results=[r], warn=bool(r["warnings"]))

    def _finish_all(self, results: list) -> None:
        lines = []
        for r in results:
            if r["state"] == "done":
                lines.append(f"{r['name']}：" + (f"新增 {r['new']} 条" if r["new"] else "已是最新"))
                lines.extend(f"　{w}" for w in r["warnings"])
            elif r["state"] == "skipped" and r.get("reason") == "needs_input":
                lines.append(f"{r['name']}：{_first_line(r['message'])}（请到它自己的页面单独更新）")
            elif r["state"] == "skipped":
                lines.append(f"{r['name']}：没有检测到这个游戏")
            else:
                lines.append(f"{r['name']}：{_first_line(r['message'])}")
        any_done = any(r["state"] == "done" for r in results)
        any_error = any(r["state"] == "error" or r.get("warnings") for r in results)
        self._set(
            state="done" if any_done else "error",
            message="\n".join(lines), results=results, warn=any_error,
        )
