"""演示数据：按真实的保底规则模拟抽卡，用来预览界面和做自检。里面没有任何真实账号的数据。"""

from __future__ import annotations

import random
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .games import ARKNIGHTS, ENDFIELD, GENSHIN, HSR, WUWA, ZZZ
from .games.arknights import record as arknights_record
from .games.endfield import char_record, weapon_record
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
        "top": [("艾莲", "代理人"), ("雅", "代理人"), ("「墨丘利」", "代理人"), ("猫又", "代理人")],
        "top_weapon": [("深海访客", "音擎"), ("嵌合编译器", "音擎")],
        "second": [("安比", "代理人"), ("珂蕾妲", "代理人"), ("街头巨星", "音擎")],
        "other": [("加农转子", "音擎"), ("钢铁肉垫", "音擎")],
    },
    "arknights": {
        "top": [("银灰", "干员"), ("艾雅法拉", "干员"), ("能天使", "干员"), ("夕", "干员")],
        "top_weapon": [("棘刺", "干员")],
        "second": [("拉普兰德", "干员"), ("星熊", "干员"), ("白面鸮", "干员")],
        "other": [("芬", "干员"), ("香草", "干员"), ("杜林", "干员")],
    },
    "endfield": {
        "top": [("管理员", "角色"), ("莱万汀", "角色"), ("洛茜", "角色")],
        "top_weapon": [("寻路者道标", "武器"), ("熔铸之剑", "武器")],
        "second": [("大潘", "角色"), ("陈千语", "角色"), ("蓝闪", "武器")],
        "other": [("见习者长刀", "武器"), ("学徒手铳", "武器")],
    },
    "wuwa": {
        "top": [("今汐", "角色"), ("长离", "角色"), ("凌阳", "角色"), ("安可", "角色")],
        "top_weapon": [("苍鳞千嶂", "武器"), ("千古洑流", "武器")],
        "second": [("白芷", "角色"), ("炽霞", "角色"), ("灼阳", "武器")],
        "other": [("原初长刃·朴石", "武器"), ("原初佩枪·基础", "武器")],
    },
}


def _simulate(rng, game: Game, pool_key: str, count: int, hard: int, soft: int, weapon_pool: bool,
              other_rank: int | None = None) -> list:
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
            rank = ranks.other if other_rank is None else other_rank
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
                "count": "1", "time": _when(n, same_second=True), "name": name, "item_type": kind, "rank_type": str(rank),
            })    # 十连的 10 条记录同一秒，和真实游戏一样
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


_CHINA = timezone(timedelta(hours=8))


def _ms(n: int) -> str:
    """第 n 抽的毫秒时间戳（十连里的 10 抽同一时刻，和接口一致）。"""
    when = datetime(2026, 3, 1, 20, 0, 0, tzinfo=_CHINA) + timedelta(days=n // 10 * 2)
    return str(int(when.timestamp() * 1000))


def _arknights_records(rng, plan: list) -> list:
    """plan: [(卡池类别, 抽数, 每多少抽换一期卡池)]"""
    records = []
    for index, (category, count, per_pool) in enumerate(plan):
        for rank, name, kind, n in _simulate(rng, ARKNIGHTS, category, count, 99, 50, False, other_rank=2):
            records.append(arknights_record({
                "poolId": f"{category}_{n // per_pool}", "charId": f"char_{n}", "charName": name,
                "rarity": rank, "gachaTs": _ms(n + index * 1000), "pos": n % 10}, category))   # 各类别错开时间，真实数据里也不会撞
    return records


def _endfield_records(rng, plan: list) -> list:
    """plan: [(卡池, 抽数, 硬保底, 软保底, 每多少抽换一期卡池)]；key 以 weapon_ 开头的是武器池。"""
    records, seq = [], 0
    for index, (key, count, hard, soft, per_pool) in enumerate(plan):
        weapon = key.startswith("weapon_")
        for rank, name, kind, n in _simulate(rng, ENDFIELD, key, count, hard, soft, weapon):
            seq += 1
            item = {"kind": "draw", "seqId": str(seq), "gachaTs": _ms(n + index * 1000), "rarity": rank, "isFree": n % 23 == 5 and not weapon,
                    "poolId": f"{key}_1_0_{n // per_pool}"}
            if weapon:
                records.append(weapon_record({**item, "weaponName": name, "weaponId": f"wpn_{n}"}, key))
            else:
                records.append(char_record({**item, "charName": name, "charId": f"chr_{n}"}, key))
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
    Store(root / "arknights").merge(
        DEMO_UID, _arknights_records(rng, [("normal", 260, 50), ("classic", 40, 40), ("spring_fest", 70, 35)]),
        meta={"pool_names": {"normal": "标准寻访", "classic": "中坚寻访", "spring_fest": "限定寻访 春节"}})
    Store(root / "endfield").merge(DEMO_UID, _endfield_records(rng, [
        ("special", 150, 80, 65, 80), ("standard", 60, 80, 65, 60), ("beginner", 20, 10_000, 10_000, 20),
        ("weapon_special", 70, 40, 10_000, 30), ("weapon_constant", 30, 40, 10_000, 30),
    ]))
    Store(root / "wuwa").merge(DEMO_UID, _wuwa_records(rng, [
        ("1", 156, 80, 65, False), ("2", 70, 80, 65, True), ("3", 100, 80, 65, False),
        ("5", 50, 50, 10_000, False),
    ]))
