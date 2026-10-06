"""把官方接口里的记录拉到本地：增量抓取、按卡池提交（米哈游的游戏通用）。"""

from __future__ import annotations

from .client import PAGE_SIZE, ApiError, Auth, Client, WishError
from .games.base import Game
from .store import Store


def sync_all(client: Client, auth: Auth, game: Game, store: Store, progress=None) -> dict:
    """依次抓取每个卡池，返回 {"uid", "new": {卡池名: 新增条数}, "total_new", "warnings"}。

    接口按时间从新到旧返回，所以遇到本地已有的记录就可以停。
    每个卡池抓完整了才写入本地：中途出错的话，已经抓到的半截数据会被丢弃，
    否则下次更新会在“本地最新记录”处停下，永远补不上中间缺的那一段。
    """
    uid: str | None = None
    known: set = set()
    new: dict = {}
    warnings: list = []

    for pool in game.pools:
        fresh: list = []
        end_id = "0"
        try:
            for page in range(1, 10_000):
                batch = client.fetch_page(auth, pool.key, page, end_id, ld=pool.ld)
                if batch and uid is None:
                    uid = str(batch[0]["uid"])
                    known = store.known_ids(uid)
                hit_known = False
                for record in batch:
                    if record["id"] in known:
                        hit_known = True
                        break
                    fresh.append(record)
                if progress:
                    progress(pool.name, len(fresh), sum(new.values()))
                if hit_known or len(batch) < PAGE_SIZE:
                    break
                end_id = batch[-1]["id"]
        except ApiError as e:
            # 联动池、重映池这类接口不一定支持：取不到就跳过，不影响其他卡池
            if not pool.optional:
                raise
            warnings.append(f"「{pool.name}」暂时取不到，已跳过（{e}）")
            new[pool.name] = 0
            continue

        if fresh and not game.trust_record_type:
            for record in fresh:   # 以请求的卡池为准，不信记录自带的类型
                record["gacha_type"] = pool.key
        new[pool.name] = store.merge(uid, fresh) if fresh else 0

    if uid is None:
        raise ApiError("这个账号在所有卡池里都没有抽卡记录。")
    return {"uid": uid, "new": new, "total_new": sum(new.values()), "warnings": warnings}


__all__ = ["sync_all", "WishError"]
