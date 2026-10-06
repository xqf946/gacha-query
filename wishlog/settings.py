"""软件的小设置：记住用户手动选过的各游戏安装目录，下次不用再选。"""

from __future__ import annotations

import json
import os
import threading
from pathlib import Path


class Settings:
    def __init__(self, path):
        self._path = Path(path)
        self._lock = threading.Lock()

    def _load(self) -> dict:
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            return {}

    def game_dir(self, game_key: str) -> str:
        dirs = self._load().get("game_dirs")
        return str(dirs.get(game_key, "")) if isinstance(dirs, dict) else ""

    def set_game_dir(self, game_key: str, path: str) -> None:
        with self._lock:
            data = self._load()
            dirs = data.get("game_dirs") if isinstance(data.get("game_dirs"), dict) else {}
            dirs[game_key] = str(path)
            data["game_dirs"] = dirs
            self._path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self._path.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
            os.replace(tmp, self._path)
