"""本地存储：每个 UID 一个 JSON 文件，多次更新时按记录 id 去重合并。

官方接口只保留最近半年的记录，所以要靠每次更新把新记录攒在本地。
"""

from __future__ import annotations

import json
import os
import threading
from datetime import datetime
from pathlib import Path

# 只留需要的字段；uid 记在文件名和文件头里，不在每条记录里重复。
KEEP = ("id", "gacha_type", "item_id", "count", "time", "name", "item_type", "rank_type")


class Store:
    def __init__(self, root):
        self.root = Path(root)
        self._lock = threading.Lock()

    def _path(self, uid: str) -> Path:
        if not (isinstance(uid, str) and uid.isdigit() and len(uid) <= 20):
            raise ValueError(f"UID 不合法：{uid!r}")
        return self.root / f"{uid}.json"

    def uids(self) -> list[str]:
        if not self.root.is_dir():
            return []
        return sorted(p.stem for p in self.root.glob("*.json") if p.stem.isdigit())

    def load(self, uid: str) -> dict:
        try:
            return json.loads(self._path(uid).read_text(encoding="utf-8"))
        except FileNotFoundError:
            return {"uid": uid, "updated_at": None, "records": []}

    def known_ids(self, uid: str) -> set:
        return {r["id"] for r in self.load(uid)["records"]}

    def merge(self, uid: str, records: list) -> int:
        """合并新记录，返回真正新增的条数。"""
        with self._lock:
            doc = self.load(uid)
            by_id = {r["id"]: r for r in doc["records"]}
            added = 0
            for r in records:
                if r["id"] not in by_id:
                    by_id[r["id"]] = {k: r[k] for k in KEEP}
                    added += 1
            if added == 0 and doc["updated_at"]:
                return 0
            doc["uid"] = uid
            doc["updated_at"] = datetime.now().isoformat(timespec="seconds")
            doc["records"] = sorted(by_id.values(), key=lambda r: int(r["id"]))
            self._write(self._path(uid), doc)
            return added

    def _write(self, path: Path, doc: dict) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, path)  # 先写临时文件再替换，中途出错不会弄坏旧数据
