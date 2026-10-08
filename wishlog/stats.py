"""统计：把一个账号的全部记录按卡池拆开，算出保底、出金间隔等（各游戏通用）。"""

from __future__ import annotations

from .games.base import Game, Pool


_WRAPPERS = (("「", "」"), ("『", "』"))


def display_name(name: str) -> str:
    """官方接口返回的名字，有的整个被「」括着、有的不括（比如绝区零邦布“「墨丘利」”和“艾瑞儿”），
    列表里看着不整齐。显示时统一去掉整个名字外面的那一层括号；只改显示，保存和导出仍是官方原名。
    """
    text = str(name).strip()
    for left, right in _WRAPPERS:
        if len(text) > 2 and text[0] == left and text[-1] == right and left not in text[1:-1] and right not in text[1:-1]:
            return text[1:-1].strip()
    return text


def _chronological(records: list) -> list:
    # 先按时间，同一时刻再按编号：十连里同一秒的记录靠编号保持抽出的先后顺序
    return sorted(records, key=lambda r: (r["time"], int(r["id"])))


def analyze(game: Game, records: list, pool_names: dict | None = None) -> list:
    """按游戏里卡池的顺序，返回每个卡池的统计结果（可直接转成 JSON）。

    pool_names：账号附加信息里记着的卡池名（明日方舟的卡池类别名只有接口才告诉我们）。
    """
    pool_names = pool_names or {}
    grouped: dict = {p.key: [] for p in game.pools}
    dynamic: dict = {}              # 记录里出现、但游戏定义里没有的卡池类型 → 动态生成的卡池
    for r in records:
        key = str(r["gacha_type"])
        pool = game.pool_for_type(key)
        if pool is None:
            if key not in dynamic:
                made = game.make_pool(key, pool_names)
                if made is None:    # 这个游戏不支持动态卡池：忽略未知类型的记录
                    continue
                dynamic[key] = made
                grouped[key] = []
            pool = dynamic[key]
        grouped[pool.key].append(r)

    # 动态生成的卡池放在固定卡池后面，最近有记录的排前面
    extra = sorted(dynamic.values(), key=lambda p: max(r["time"] for r in grouped[p.key]), reverse=True)
    return [analyze_pool(game, p, _chronological(grouped[p.key])) for p in [*game.pools, *extra]]


def ten_pull_stats(records: list, ranks) -> dict:
    """十连统计。同一次十连的 10 条记录时间相同，所以把时间相同、恰好 10 条的一组当作一次十连。

    top2 / second2：十连里出了 2 个及以上（包括 3 个、4 个……）最高档 / 次高档的次数。
    """
    batches = top2 = second2 = 0
    i, n = 0, len(records)
    while i < n:
        j = i
        while j < n and records[j]["time"] == records[i]["time"]:
            j += 1
        if j - i == 10:
            batches += 1
            tops = sum(1 for r in records[i:j] if int(r["rank_type"]) == ranks.top)
            seconds = sum(1 for r in records[i:j] if int(r["rank_type"]) == ranks.second)
            top2 += tops >= 2
            second2 += seconds >= 2
        i = j
    return {"ten_pulls": batches, "ten_top2": top2, "ten_second2": second2}


def lose_stats(entries: list) -> dict:
    """“歪”的概率：在没有大保底的最高档里，出了常驻角色的比例。entries 要从旧到新。

    一次歪了，下一个最高档就是大保底（必定是 UP），它不算一次“50/50”，所以不计入分母。
    """
    guaranteed = False
    eligible = lost = 0
    for e in entries:
        if e["tier"] != "top":
            continue
        if guaranteed:
            guaranteed = False
            continue
        eligible += 1
        if e["standard"]:
            lost += 1
            guaranteed = True
    return {"lose_eligible": eligible, "lose_count": lost,
            "lose_rate": round(lost / eligible * 100, 1) if eligible else None}


def analyze_pool(game: Game, pool: Pool, records: list) -> dict:
    """records 必须按时间从旧到新排好。"""
    ranks = game.ranks
    since_top = since_second = 0    # 距离上一个最高档 / 上一个次高档（含最高档）已经抽了多少（免费抽不算）
    last_pool_id = None
    entries: list = []              # 每条记录一项，最后翻转成从新到旧
    counts = {ranks.top: 0, ranks.second: 0}
    free_count = 0

    for n, r in enumerate(records, 1):
        rank = int(r["rank_type"])
        counts[rank] = counts.get(rank, 0) + 1
        free = r.get("free") == "1"
        free_count += free

        # 有的游戏保底不跨具体卡池继承（明日方舟限定池、终末地武器池）：换了一期卡池就从头算
        pool_id = r.get("pool_id")
        if pool.reset_on_new_pool and pool_id and last_pool_id is not None and pool_id != last_pool_id:
            since_top = since_second = 0
        last_pool_id = pool_id or last_pool_id

        if not free:
            since_top += 1
            since_second += 1

        pity = None
        if rank == ranks.top:
            pity = since_top
            if not free:
                since_top = since_second = 0
        elif rank == ranks.second:
            pity = since_second
            if not free:
                since_second = 0

        std = game.standard
        entries.append({
            "n": n,
            "name": display_name(r["name"]),
            "item_type": r["item_type"],
            "rank": rank,
            "mark": ranks.marks.get(rank, str(rank)),
            "tier": "top" if rank == ranks.top else "second" if rank == ranks.second else "other",
            "time": r["time"],
            "pity": pity,
            "free": free,
            "standard": bool(
                std and pool.key in std.pool_keys and rank == ranks.top
                and r["item_type"] in std.item_types and r["name"] in std.names
            ),
        })

    top_pities = [e["pity"] for e in entries if e["tier"] == "top" and not e["free"]]
    total = len(records)
    has_standard = bool(game.standard and pool.key in game.standard.pool_keys)
    return {
        "key": pool.key,
        "name": pool.name,
        "hard_pity": pool.hard_pity,
        "soft_pity": pool.soft_pity,
        "total": total,
        "free_count": free_count,
        "top_count": counts[ranks.top],
        "second_count": counts[ranks.second],
        "other_count": total - counts[ranks.top] - counts[ranks.second],
        "current_pity_top": since_top,
        "current_pity_second": since_second,
        "avg_pity_top": round(sum(top_pities) / len(top_pities), 1) if top_pities else None,
        "min_pity_top": min(top_pities) if top_pities else None,
        "max_pity_top": max(top_pities) if top_pities else None,
        "standard_count": sum(1 for e in entries if e["standard"]),
        **ten_pull_stats(records, ranks),
        **(lose_stats(entries) if has_standard else {"lose_eligible": 0, "lose_count": 0, "lose_rate": None}),
        "cost": (total - free_count) * game.cost_per_pull if game.cost_per_pull else None,
        "records": entries[::-1],
    }
