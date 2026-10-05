"""把官方接口里的记录拉到本地：增量抓取、按卡池提交。"""

from __future__ import annotations

from .client import PAGE_SIZE, ApiError, Auth, Client
from .pools import POOLS
from .store import Store


def sync_all(client: Client, auth: Auth, store: Store, progress=None) -> dict:
    """依次抓取每个卡池，返回 {"uid": ..., "new": {卡池名: 新增条数}, "total_new": n}。

    接口按时间从新到旧返回，所以遇到本地已有的记录就可以停。
    每个卡池抓完整了才写入本地：中途出错的话，已经抓到的半截数据会被丢弃，
    否则下次更新会在“本地最新记录”处停下，永远补不上中间缺的那一段。
    """
    uid: str | None = None
    known: set = set()
    new: dict = {}

    for pool in POOLS:
        fresh: list = []
        end_id = "0"
        for page in range(1, 10_000):
            batch = client.fetch_page(auth, pool.key, page, end_id)
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

        new[pool.name] = store.merge(uid, fresh) if fresh else 0

    if uid is None:
        raise ApiError("这个账号在所有卡池里都没有抽卡记录。")
    return {"uid": uid, "new": new, "total_new": sum(new.values())}
