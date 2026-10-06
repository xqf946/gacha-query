"""窗口里的界面调用的全部后台方法。

界面（JavaScript）通过 window.pywebview.api.<方法名>(...) 直接调用这里的公开方法，
没有网络端口、没有 HTTP 服务。

注意：pywebview 会把对象上所有不以下划线开头的属性都当成接口暴露出去，
所以这里的属性一律带下划线，只有真正要给界面用的方法才是公开的。
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from . import __version__, exporting
from .job import SyncJob
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
    def __init__(self, store: Store, job: SyncJob, data_dir, dialogs, opener=open_folder):
        self._store = store
        self._job = job
        self._data_dir = Path(data_dir)
        self._dialogs = dialogs   # 需要有 save_file(filename, file_types) 和 pick_folder()
        self._opener = opener

    def _known_uid(self, uid) -> str | None:
        uid = str(uid)
        return uid if uid in self._store.uids() else None

    # ---- 查询 ----
    def get_state(self) -> dict:
        return {"uids": self._store.uids(), "sync": self._job.status()}

    def get_wishes(self, uid) -> dict:
        uid = self._known_uid(uid)
        if not uid:
            return {"error": "没有这个 UID 的记录"}
        doc = self._store.load(uid)
        return {"uid": uid, "updated_at": doc["updated_at"], "pools": analyze(doc["records"])}

    def app_info(self) -> dict:
        return {"version": __version__, "data_dir": str(self._data_dir)}

    # ---- 更新记录 ----
    def start_sync(self, url="", game_dir="") -> dict:
        started = self._job.start(url=str(url or ""), game_dir=str(game_dir or ""))
        return {"started": started, "status": self._job.status()}

    def sync_status(self) -> dict:
        return self._job.status()

    # ---- 需要系统对话框的操作 ----
    def export_records(self, uid, fmt) -> dict:
        uid = self._known_uid(uid)
        if not uid or fmt not in ("csv", "json"):
            return {"error": "参数不对"}
        rows = exporting.build_rows(uid, self._store.load(uid)["records"])
        content = exporting.to_csv(rows) if fmt == "csv" else exporting.to_json(uid, rows)
        label = "CSV 文件 (*.csv)" if fmt == "csv" else "JSON 文件 (*.json)"
        path = self._dialogs.save_file(f"genshin_wishes_{uid}.{fmt}", (label,))
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
