"""统计：把一个账号的全部记录按卡池拆开，算出保底、出金间隔等（各游戏通用）。"""

from __future__ import annotations

from .games.base import Game, Pool


def analyze(game: Game, records: list) -> list:
    """按游戏里卡池的顺序，返回每个卡池的统计结果（可直接转成 JSON）。"""
    grouped: dict = {p.key: [] for p in game.pools}
    for r in records:
        pool = game.pool_for_type(r["gacha_type"])
        if pool:
            grouped[pool.key].append(r)
    return [
        analyze_pool(game, p, sorted(grouped[p.key], key=lambda r: int(r["id"])))
        for p in game.pools
    ]


def analyze_pool(game: Game, pool: Pool, records: list) -> dict:
    """records 必须按时间从旧到新排好。"""
    ranks = game.ranks
    since_top = since_second = 0    # 距离上一个最高档 / 上一个次高档（含最高档）已经抽了多少
    entries: list = []              # 每条记录一项，最后翻转成从新到旧
    counts = {ranks.top: 0, ranks.second: 0, ranks.other: 0}

    for n, r in enumerate(records, 1):
        rank = int(r["rank_type"])
        counts[rank] = counts.get(rank, 0) + 1
        since_top += 1
        since_second += 1

        pity = None
        if rank == ranks.top:
            pity, since_top, since_second = since_top, 0, 0
        elif rank == ranks.second:
            pity, since_second = since_second, 0

        std = game.standard
        entries.append({
            "n": n,
            "name": r["name"],
            "item_type": r["item_type"],
            "rank": rank,
            "mark": ranks.marks.get(rank, str(rank)),
            "tier": "top" if rank == ranks.top else "second" if rank == ranks.second else "other",
            "time": r["time"],
            "pity": pity,
            "standard": bool(
                std and pool.key in std.pool_keys and rank == ranks.top
                and r["item_type"] in std.item_types and r["name"] in std.names
            ),
        })

    top_pities = [e["pity"] for e in entries if e["tier"] == "top"]
    total = len(records)
    return {
        "key": pool.key,
        "name": pool.name,
        "hard_pity": pool.hard_pity,
        "soft_pity": pool.soft_pity,
        "total": total,
        "top_count": counts[ranks.top],
        "second_count": counts[ranks.second],
        "other_count": counts[ranks.other],
        "current_pity_top": since_top,
        "current_pity_second": since_second,
        "avg_pity_top": round(sum(top_pities) / len(top_pities), 1) if top_pities else None,
        "min_pity_top": min(top_pities) if top_pities else None,
        "max_pity_top": max(top_pities) if top_pities else None,
        "standard_count": sum(1 for e in entries if e["standard"]),
        "cost": total * game.cost_per_pull,
        "records": entries[::-1],
    }
