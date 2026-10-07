"""窗口里的界面调用的全部后台方法。

界面（JavaScript）通过 window.pywebview.api.<方法名>(...) 直接调用这里的公开方法，
没有网络端口、没有 HTTP 服务。

注意：pywebview 会把对象上所有不以下划线开头的属性都当成接口暴露出去，
所以这里的属性一律带下划线，只有真正要给界面用的方法才是公开的。
"""

from __future__ import annotations

import os
import platform
import subprocess
import sys
from pathlib import Path

from . import __version__, exporting
from .job import SyncJob
from .settings import Settings
from .stats import analyze
from .store import Store


def open_folder(path: Path) -> None:
    """用系统的文件管理器打开一个文件夹。"""
    if sys.platform == "win32":
        os.startfile(str(path))  # type: ignore[attr-defined]
    elif sys.platform == "darwin":
        subprocess.run(["open", str(path)], check=False)
    else:
        subprocess.run(["xdg-open", str(path)], check=False)


class Api:
    def __init__(self, data_dir, games, job: SyncJob, settings: Settings, dialogs, opener=open_folder):
        self._data_dir = Path(data_dir)
        self._games = {g.key: g for g in games}
        self._order = [g.key for g in games]
        self._job = job
        self._settings = settings
        self._dialogs = dialogs   # 需要有 save_file(filename, file_types) 和 pick_folder()
        self._opener = opener

    def _store(self, game_key: str) -> Store:
        return Store(self._data_dir / game_key)

    def _known(self, game_key, uid):
        """校验游戏和 UID；都存在才返回 (游戏, UID)，否则返回 None。"""
        game = self._games.get(str(game_key))
        uid = str(uid)
        if game and uid in self._store(game.key).uids():
            return game, uid
        return None

    # ---- 查询 ----
    def get_state(self) -> dict:
        return {
            "games": [{**self._games[k].meta(), "uids": self._store(k).uids()} for k in self._order],
            "sync": self._job.status(),
            "theme": self._settings.theme(),
        }

    def set_theme(self, theme) -> str:
        """记住用户选的外观（auto/light/dark），返回实际保存的值。"""
        return self._settings.set_theme(str(theme))

    def get_wishes(self, game, uid) -> dict:
        found = self._known(game, uid)
        if not found:
            return {"error": "没有这个账号的记录"}
        game, uid = found
        doc = self._store(game.key).load(uid)
        return {
            "game": game.meta(), "uid": uid, "updated_at": doc["updated_at"],
            "pools": analyze(game, doc["records"], (doc.get("meta") or {}).get("pool_names")),
        }

    def app_info(self) -> dict:
        return {"version": __version__, "data_dir": str(self._data_dir)}

    # ---- 更新记录 ----
    def start_sync(self, game="all", url="", game_dir="") -> dict:
        game = str(game or "all")
        if game != "all" and game not in self._games:
            return {"started": False, "status": self._job.status(), "error": "未知的游戏"}
        started = self._job.start(game=game, url=str(url or ""), game_dir=str(game_dir or ""))
        return {"started": started, "status": self._job.status()}

    def sync_status(self) -> dict:
        return self._job.status()

    # ---- 需要系统对话框的操作 ----
    def export_records(self, game, uid, fmt) -> dict:
        found = self._known(game, uid)
        if not found or fmt not in ("csv", "json"):
            return {"error": "参数不对"}
        game, uid = found
        rows = exporting.build_rows(game, uid, self._store(game.key).load(uid)["records"])
        content = exporting.to_csv(rows) if fmt == "csv" else exporting.to_json(game, uid, rows)
        label = "CSV 文件 (*.csv)" if fmt == "csv" else "JSON 文件 (*.json)"
        path = self._dialogs.save_file(f"{game.key}_wishes_{uid}.{fmt}", (label,))
        if not path:
            return {"cancelled": True}
        try:
            Path(path).write_bytes(content)
        except OSError as e:
            return {"error": f"保存失败：{e}"}
        return {"ok": True, "path": str(path)}

    def pick_game_dir(self) -> dict:
        return {"path": self._dialogs.pick_folder() or None}

    def open_data_dir(self) -> dict:
        self._data_dir.mkdir(parents=True, exist_ok=True)
        self._opener(self._data_dir)
        return {"ok": True}

    # ---- 诊断 ----
    def diagnose(self) -> dict:
        """检查本机上每个游戏的安装、缓存/日志、链接情况。只看本机，不联网，报告里没有任何密钥。"""
        lines = [f"抽卡查询 v{__version__} 环境检测", f"系统：{platform.platform()}", ""]
        for key in self._order:
            game = self._games[key]
            lines.append(f"【{game.name}】")
            remembered = self._settings.game_dir(key)
            if remembered:
                lines.append(f"手动选过的目录：{remembered}")
            try:
                lines.extend(game.diagnose(remembered))
            except Exception as e:  # 诊断本身不能把软件弄崩
                lines.append(f"检测时出错：{type(e).__name__}: {e}")
            lines.append("")
        return {"report": "\n".join(lines).rstrip() + "\n"}
