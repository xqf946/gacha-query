"""演示数据：按真实的保底规则模拟抽卡，用来预览界面和做自检。里面没有任何真实账号的数据。"""

from __future__ import annotations

import random
from datetime import datetime, timedelta

from .store import Store

DEMO_UID = "100000001"
_FIVE_CHARACTERS = ["胡桃", "钟离", "雷电将军", "那维莱特", "刻晴", "纳西妲"]
_FIVE_WEAPONS = ["天空之刃", "和璞鸢", "护摩之杖"]
_FOUR_CHARACTERS = ["香菱", "行秋", "北斗", "砂糖", "班尼特"]
_FOUR_WEAPONS = ["祭礼剑", "西风长枪", "弓藏"]
_THREE_WEAPONS = ["黑缨枪", "冷刃", "飞天御剑", "魔导绪论"]


def _simulate(rng, gacha_type: str, count: int, hard: int, soft: int, weapon: bool, start_id: int) -> list:
    records, since5, since4 = [], 0, 0
    clock = datetime(2026, 3, 1, 20, 0)
    for n in range(count):
        since5 += 1
        since4 += 1
        p5 = 1.0 if since5 >= hard else 0.006 if since5 < soft else min(1.0, 0.006 + 0.06 * (since5 - soft + 1))
        roll = rng.random()
        if roll < p5:
            rank, since5, since4 = 5, 0, 0
            name, kind = (rng.choice(_FIVE_WEAPONS), "武器") if weapon else (rng.choice(_FIVE_CHARACTERS), "角色")
        elif since4 >= 10 or roll < p5 + 0.051:
            rank, since4 = 4, 0
            name, kind = (rng.choice(_FOUR_WEAPONS), "武器") if rng.random() < 0.5 else (rng.choice(_FOUR_CHARACTERS), "角色")
        else:
            rank, name, kind = 3, rng.choice(_THREE_WEAPONS), "武器"
        when = clock + timedelta(days=n // 10 * 2, minutes=n % 10)
        records.append({
            "id": str(start_id + n), "gacha_type": gacha_type, "item_id": "", "count": "1",
            "time": when.strftime("%Y-%m-%d %H:%M:%S"), "name": name, "item_type": kind,
            "rank_type": str(rank),
        })
    return records


def seed(store: Store) -> None:
    rng = random.Random(7)
    base = 1_700_000_000_000_000_000
    records = (
        _simulate(rng, "301", 214, 90, 74, False, base)
        + _simulate(rng, "302", 96, 80, 63, True, base + 1_000)
        + _simulate(rng, "200", 140, 90, 74, False, base + 2_000)
        + _simulate(rng, "500", 31, 90, 74, False, base + 3_000)
        + _simulate(rng, "100", 20, 10_000, 10_000, False, base + 4_000)
    )
    store.merge(DEMO_UID, records)
    store.merge("200000002", _simulate(rng, "301", 12, 90, 74, False, base + 5_000))
