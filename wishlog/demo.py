"""演示数据：按真实的保底规则模拟抽卡，用来预览界面和做自检。里面没有任何真实账号的数据。"""

from __future__ import annotations

import random
from datetime import datetime, timedelta
from pathlib import Path

from .games import GENSHIN, HSR, WUWA, ZZZ
from .games.base import Game
from .games.wuwa import build_records
from .store import Store

DEMO_UID = "100000001"
DEMO_UID_2 = "200000002"

# 每个游戏三个品级各自能抽到的东西：(名字, 类别)
_NAMES = {
    "genshin": {
        "top": [("胡桃", "角色"), ("钟离", "角色"), ("刻晴", "角色"), ("那维莱特", "角色")],
        "top_weapon": [("天空之刃", "武器"), ("和璞鸢", "武器")],
        "second": [("香菱", "角色"), ("行秋", "角色"), ("祭礼剑", "武器"), ("弓藏", "武器")],
        "other": [("黑缨枪", "武器"), ("冷刃", "武器"), ("魔导绪论", "武器")],
    },
    "hsr": {
        "top": [("符玄", "角色"), ("镜流", "角色"), ("姬子", "角色")],
        "top_weapon": [("于夜色中", "光锥"), ("时节不居", "光锥")],
        "second": [("三月七", "角色"), ("艾丝妲", "角色"), ("以世界之名", "光锥")],
        "other": [("喜悦之花", "光锥"), ("锋镝", "光锥")],
    },
    "zzz": {
        "top": [("艾莲", "代理人"), ("雅", "代理人"), ("猫又", "代理人")],
        "top_weapon": [("深海访客", "音擎"), ("嵌合编译器", "音擎")],
        "second": [("安比", "代理人"), ("珂蕾妲", "代理人"), ("街头巨星", "音擎")],
        "other": [("加农转子", "音擎"), ("钢铁肉垫", "音擎")],
    },
    "wuwa": {
        "top": [("今汐", "角色"), ("长离", "角色"), ("凌阳", "角色"), ("安可", "角色")],
        "top_weapon": [("苍鳞千嶂", "武器"), ("千古洑流", "武器")],
        "second": [("白芷", "角色"), ("炽霞", "角色"), ("灼阳", "武器")],
        "other": [("原初长刃·朴石", "武器"), ("原初佩枪·基础", "武器")],
    },
}


def _simulate(rng, game: Game, pool_key: str, count: int, hard: int, soft: int, weapon_pool: bool) -> list:
    """按保底规则抽 count 次，返回从旧到新的 (品级, 名字, 类别, 第几抽) 。"""
    ranks, names = game.ranks, _NAMES[game.key]
    pulls, since_top, since_second = [], 0, 0
    for n in range(count):
        since_top += 1
        since_second += 1
        p_top = 1.0 if since_top >= hard else 0.006 if since_top < soft else min(1.0, 0.006 + 0.06 * (since_top - soft + 1))
        roll = rng.random()
        if roll < p_top:
            rank, since_top, since_second = ranks.top, 0, 0
            name, kind = rng.choice(names["top_weapon"] if weapon_pool else names["top"])
        elif since_second >= 10 or roll < p_top + 0.051:
            rank, since_second = ranks.second, 0
            name, kind = rng.choice(names["second"])
        else:
            rank = ranks.other
            name, kind = rng.choice(names["other"])
        pulls.append((rank, name, kind, n))
    return pulls


def _when(n: int, same_second: bool = False) -> str:
    base = datetime(2026, 3, 1, 20, 0, 0) + timedelta(days=n // 10 * 2)
    return (base if same_second else base + timedelta(minutes=n % 10)).strftime("%Y-%m-%d %H:%M:%S")


def _mihoyo_records(rng, game: Game, plan: list, start_id: int) -> list:
    """plan: [(卡池编号, 抽数, 硬保底, 软保底, 是不是武器池)]"""
    records = []
    for offset, (pool_key, count, hard, soft, weapon) in enumerate(plan):
        for rank, name, kind, n in _simulate(rng, game, pool_key, count, hard, soft, weapon):
            records.append({
                "id": str(start_id + offset * 1000 + n), "gacha_type": pool_key, "item_id": "",
                "count": "1", "time": _when(n), "name": name, "item_type": kind, "rank_type": str(rank),
            })
    return records


def _wuwa_records(rng, plan: list) -> list:
    records = []
    for pool_key, count, hard, soft, weapon in plan:
        pulls = _simulate(rng, WUWA, pool_key, count, hard, soft, weapon)
        # 接口返回的是从新到旧，而且十连的记录在同一秒
        items = [
            {"cardPoolType": "", "resourceId": 0, "qualityLevel": rank, "resourceType": kind,
             "name": name, "count": 1, "time": _when(n, same_second=True)}
            for rank, name, kind, n in pulls
        ]
        records += build_records(list(reversed(items)), pool_key)
    return records


def seed(data_dir) -> None:
    """往 data_dir 里写入四个游戏的演示记录（原神、崩铁、绝区零各一个账号，原神再多一个小号）。"""
    root = Path(data_dir)
    rng = random.Random(7)
    base = 1_700_000_000_000_000_000

    Store(root / "genshin").merge(DEMO_UID, _mihoyo_records(rng, GENSHIN, [
        ("301", 214, 90, 74, False), ("302", 96, 80, 63, True), ("200", 140, 90, 74, False),
        ("500", 31, 90, 74, False), ("100", 20, 10_000, 10_000, False),
    ], base))
    Store(root / "genshin").merge(DEMO_UID_2, _mihoyo_records(rng, GENSHIN, [
        ("301", 12, 90, 74, False)], base + 50_000))
    Store(root / "hsr").merge(DEMO_UID, _mihoyo_records(rng, HSR, [
        ("11", 168, 90, 74, False), ("12", 80, 80, 66, True), ("1", 120, 90, 74, False),
        ("2", 50, 10_000, 10_000, False),
    ], base))
    Store(root / "zzz").merge(DEMO_UID, _mihoyo_records(rng, ZZZ, [
        ("2", 130, 90, 74, False), ("3", 60, 80, 65, True), ("1", 90, 90, 74, False),
        ("5", 40, 80, 65, False),
    ], base))
    Store(root / "wuwa").merge(DEMO_UID, _wuwa_records(rng, [
        ("1", 156, 80, 65, False), ("2", 70, 80, 65, True), ("3", 100, 80, 65, False),
        ("5", 50, 50, 10_000, False),
    ]))
