"""统计：把一个账号的全部记录按卡池拆开，算出保底进度、出金间隔等。"""

from __future__ import annotations

from .pools import (
    POOL_BY_GACHA_TYPE,
    POOLS,
    PRIMOGEMS_PER_PULL,
    STANDARD_FIVE_STAR_CHARACTERS,
    Pool,
)


def analyze(records: list) -> list:
    """按 POOLS 的顺序返回每个卡池的统计结果（可直接转成 JSON）。"""
    grouped: dict = {p.key: [] for p in POOLS}
    for r in records:
        pool = POOL_BY_GACHA_TYPE.get(r["gacha_type"])
        if pool:
            grouped[pool.key].append(r)
    return [
        analyze_pool(p, sorted(grouped[p.key], key=lambda r: int(r["id"]))) for p in POOLS
    ]


def analyze_pool(pool: Pool, records: list) -> dict:
    """records 必须按时间从旧到新排好。"""
    since5 = since4 = 0       # 距离上一个五星 / 上一个四星（含五星）已经抽了多少
    entries: list = []        # 每条记录一项，最后翻转成从新到旧
    ranks = {3: 0, 4: 0, 5: 0}

    for n, r in enumerate(records, 1):
        rank = int(r["rank_type"])
        ranks[rank] = ranks.get(rank, 0) + 1
        since5 += 1
        since4 += 1

        pity = None
        if rank == 5:
            pity, since5, since4 = since5, 0, 0
        elif rank == 4:
            pity, since4 = since4, 0

        entries.append({
            "n": n,
            "name": r["name"],
            "item_type": r["item_type"],
            "rank": rank,
            "time": r["time"],
            "pity": pity,
            "standard": (
                pool.key == "301" and rank == 5 and r["item_type"] == "角色"
                and r["name"] in STANDARD_FIVE_STAR_CHARACTERS
            ),
        })

    five_pities = [e["pity"] for e in entries if e["rank"] == 5]
    total = len(records)
    return {
        "key": pool.key,
        "name": pool.name,
        "hard_pity": pool.hard_pity,
        "soft_pity": pool.soft_pity,
        "total": total,
        "five_count": ranks[5],
        "four_count": ranks[4],
        "three_count": ranks[3],
        "current_pity5": since5,
        "current_pity4": since4,
        "avg_pity5": round(sum(five_pities) / len(five_pities), 1) if five_pities else None,
        "min_pity5": min(five_pities) if five_pities else None,
        "max_pity5": max(five_pities) if five_pities else None,
        "standard_count": sum(1 for e in entries if e["standard"]),
        "primogems": total * PRIMOGEMS_PER_PULL,
        "records": entries[::-1],
    }
