"""导出：把本地记录整理成 CSV / JSON 的字节内容。"""

from __future__ import annotations

import csv
import io
import json
from datetime import datetime

from .pools import POOL_BY_GACHA_TYPE


def build_rows(uid: str, records: list) -> list:
    return [
        {**r, "uid": uid, "pool": POOL_BY_GACHA_TYPE[r["gacha_type"]].name}
        for r in records
        if r["gacha_type"] in POOL_BY_GACHA_TYPE
    ]


def to_csv(rows: list) -> bytes:
    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow(["时间", "卡池", "名称", "类别", "星级", "记录ID"])
    for r in rows:
        writer.writerow([r["time"], r["pool"], r["name"], r["item_type"], r["rank_type"], r["id"]])
    # 加 BOM，Excel 才会把中文当成 UTF-8 正确显示
    return ("﻿" + out.getvalue()).encode("utf-8")


def to_json(uid: str, rows: list) -> bytes:
    doc = {"uid": uid, "exported_at": datetime.now().isoformat(timespec="seconds"), "records": rows}
    return json.dumps(doc, ensure_ascii=False, indent=1).encode("utf-8")
