"""软件的小设置：记住用户手动选过的各游戏安装目录，以及界面外观（跟随系统/浅色/深色）。"""

from __future__ import annotations

import json
import os
import threading
from pathlib import Path


THEMES = ("auto", "light", "dark")


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

    def _save(self, data: dict) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
        os.replace(tmp, self._path)

    def set_game_dir(self, game_key: str, path: str) -> None:
        with self._lock:
            data = self._load()
            dirs = data.get("game_dirs") if isinstance(data.get("game_dirs"), dict) else {}
            dirs[game_key] = str(path)
            data["game_dirs"] = dirs
            self._save(data)

    def theme(self) -> str:
        """界面外观：auto 跟随系统，light 浅色，dark 深色。文件里是别的值就当作 auto。"""
        value = self._load().get("theme")
        return value if value in THEMES else "auto"

    def set_theme(self, theme: str) -> str:
        theme = theme if theme in THEMES else "auto"
        with self._lock:
            data = self._load()
            data["theme"] = theme
            self._save(data)
        return theme
