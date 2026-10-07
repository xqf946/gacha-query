"""本地存储：每个 UID 一个 JSON 文件，多次更新时按记录 id 去重合并。

官方接口只保留最近半年的记录，所以要靠每次更新把新记录攒在本地。
"""

from __future__ import annotations

import json
import os
import shutil
import threading
from datetime import datetime
from pathlib import Path

# 只留需要的字段；uid 记在文件名和文件头里，不在每条记录里重复。
KEEP = ("id", "gacha_type", "item_id", "count", "time", "name", "item_type", "rank_type")
# 只有个别游戏才有的附加字段，有就留着：free=免费抽（"1"），pool_id=具体是哪一期卡池
OPTIONAL = ("free", "pool_id")


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

    def meta(self, uid: str) -> dict:
        """随账号一起保存的附加信息，例如明日方舟各卡池类别的名字。"""
        return self.load(uid).get("meta") or {}

    def merge(self, uid: str, records: list, meta: dict | None = None) -> int:
        """合并新记录，返回真正新增的条数。meta 里的内容会合并进账号的附加信息。"""
        with self._lock:
            doc = self.load(uid)
            by_id = {r["id"]: r for r in doc["records"]}
            added = 0
            for r in records:
                if r["id"] not in by_id:
                    by_id[r["id"]] = {k: r[k] for k in KEEP} | {k: r[k] for k in OPTIONAL if r.get(k) not in (None, "")}
                    added += 1
            merged_meta = {**(doc.get("meta") or {}), **(meta or {})}
            meta_changed = merged_meta != (doc.get("meta") or {})
            if added == 0 and doc["updated_at"] and not meta_changed:
                return 0
            doc["uid"] = uid
            if added or not doc["updated_at"]:
                doc["updated_at"] = datetime.now().isoformat(timespec="seconds")
            if merged_meta:
                doc["meta"] = merged_meta
            doc["records"] = sorted(by_id.values(), key=lambda r: int(r["id"]))
            self._write(self._path(uid), doc)
            return added

    def absorb(self, source_uid: str, target_uid: str) -> int:
        """把临时账号 source_uid 里的记录并进 target_uid（已经有的按 id 去重），成功后把临时文件改名留作备份。
        返回临时账号里的记录条数。"""
        if source_uid == target_uid:
            return 0
        with self._lock:
            source = self.load(source_uid)
            if not source["records"]:
                return 0
            source_path = self._path(source_uid)
            target = self.load(target_uid)
            by_id = {r["id"]: r for r in target["records"]}
            for r in source["records"]:
                by_id.setdefault(r["id"], r)
            target["uid"] = target_uid
            target["updated_at"] = target["updated_at"] or source["updated_at"]
            meta = {**(source.get("meta") or {}), **(target.get("meta") or {})}
            if meta:
                target["meta"] = meta
            target["records"] = sorted(by_id.values(), key=lambda r: int(r["id"]))
            self._write(self._path(target_uid), target)
            os.replace(source_path, source_path.with_suffix(".json.merged"))  # 合并成功后才动旧文件
            return len(source["records"])

    def _write(self, path: Path, doc: dict) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, path)  # 先写临时文件再替换，中途出错不会弄坏旧数据


def migrate_legacy(data_dir, game_key: str = "genshin") -> int:
    """v0.2.x 把记录直接放在 data 目录下（只有原神）。多游戏之后每个游戏一个子文件夹。

    先把旧文件复制到新位置，确认复制成功后，才把旧文件挪进 backup-before-multi-game，
    这样中途出任何问题都不会丢记录。返回搬了几个文件。
    """
    data_dir = Path(data_dir)
    legacy = [p for p in data_dir.glob("*.json") if p.stem.isdigit()] if data_dir.is_dir() else []
    if not legacy:
        return 0
    target = data_dir / game_key
    backup = data_dir / "backup-before-multi-game"
    target.mkdir(parents=True, exist_ok=True)
    backup.mkdir(parents=True, exist_ok=True)
    for path in legacy:
        destination = target / path.name
        if not destination.exists():
            shutil.copy2(path, destination)
        shutil.move(str(path), str(backup / path.name))
    return len(legacy)
