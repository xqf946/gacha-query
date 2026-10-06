"""导出：把本地记录整理成 CSV / JSON 的字节内容。"""

from __future__ import annotations

import csv
import io
import json
from datetime import datetime

from .games.base import Game


def build_rows(game: Game, uid: str, records: list) -> list:
    rows = []
    for r in records:
        pool = game.pool_for_type(r["gacha_type"])
        if pool:
            rows.append({**r, "game": game.key, "uid": uid, "pool": pool.name})
    return rows


def to_csv(rows: list) -> bytes:
    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow(["时间", "卡池", "名称", "类别", "星级", "记录ID"])
    for r in rows:
        writer.writerow([r["time"], r["pool"], r["name"], r["item_type"], r["rank_type"], r["id"]])
    # 加 BOM，Excel 才会把中文当成 UTF-8 正确显示
    return ("\ufeff" + out.getvalue()).encode("utf-8")


def to_json(game: Game, uid: str, rows: list) -> bytes:
    doc = {
        "game": game.key, "game_name": game.name, "uid": uid,
        "exported_at": datetime.now().isoformat(timespec="seconds"), "records": rows,
    }
    return json.dumps(doc, ensure_ascii=False, indent=1).encode("utf-8")
