"""测试用的假官方接口和假数据。"""

from __future__ import annotations

import random
from urllib.parse import parse_qsl, urlparse

VALID_KEY = "VALIDKEY%2Babc%2Fdef%3D%3D"  # 链接里的 authkey 是百分号编码过的
FIVE_STARS = ["胡桃", "钟离", "刻晴", "雷电将军", "那维莱特"]
FOUR_STARS = ["香菱", "行秋", "北斗", "砂糖"]


def wish_url(authkey: str = VALID_KEY, host: str = "public-operation-hk4e.mihoyo.com", **override) -> str:
    query = {
        "authkey_ver": "1", "sign_type": "2", "auth_appid": "webview_gacha", "init_type": "301",
        "gacha_id": "abc", "timestamp": "1700000000", "lang": "zh-cn", "device_type": "pc",
        "game_version": "CNRELWin5.0_R1_", "region": "cn_gf01", "authkey": authkey,
        "game_biz": "hk4e_cn", "gacha_type": "301", "page": "1", "size": "5", "end_id": "0",
    }
    query.update(override)
    return f"https://{host}/gacha_info/api/getGachaLog?" + "&".join(f"{k}={v}" for k, v in query.items())


_counter = [0]


def make_records(gacha_type: str, n: int, uid: str = "100000001", seed: int = 1) -> list:
    """生成 n 条按时间从旧到新排好的记录，id 全局递增。"""
    rng = random.Random(seed)
    out = []
    for _ in range(n):
        _counter[0] += 1
        roll = rng.random()
        rank, name, kind = (
            ("5", rng.choice(FIVE_STARS), "角色") if roll < 0.03
            else ("4", rng.choice(FOUR_STARS), "角色") if roll < 0.2
            else ("3", "黑缨枪", "武器")
        )
        out.append({
            "uid": uid, "gacha_type": gacha_type, "item_id": "", "count": "1",
            "time": f"2026-01-{1 + _counter[0] % 28:02d} 12:00:00", "name": name,
            "item_type": kind, "rank_type": rank, "id": str(1_700_000_000_000_000_000 + _counter[0]),
            "lang": "zh-cn",
        })
    return out


class FakeApi:
    """模拟 getGachaLog：按 end_id 翻页、从新到旧返回，authkey 不对就返回 -101。"""

    def __init__(self, pools: dict):
        self.pools = pools            # {"301": [...旧到新...], ...}
        self.calls = []               # 每次请求的 (gacha_type, page, end_id)
        self.fail_on = None           # 形如 ("302", 2)：该卡池第 2 页返回 -101
        self.frequent_times = 0       # 前 N 次请求返回 -110
        self.valid_key = VALID_KEY

    def fetch(self, url: str) -> dict:
        q = dict(parse_qsl(urlparse(url).query))
        self.calls.append((q["gacha_type"], int(q["page"]), q["end_id"]))
        if self.frequent_times > 0:
            self.frequent_times -= 1
            return {"retcode": -110, "message": "visit too frequently", "data": None}
        if q["authkey"] != _decoded(self.valid_key) or self.fail_on == (q["gacha_type"], int(q["page"])):
            return {"retcode": -101, "message": "authkey timeout", "data": None}
        records = sorted(self.pools.get(q["gacha_type"], []), key=lambda r: int(r["id"]), reverse=True)
        end_id = int(q["end_id"])
        if end_id:
            records = [r for r in records if int(r["id"]) < end_id]
        page = records[: int(q["size"])]
        return {"retcode": 0, "message": "OK", "data": {"page": q["page"], "list": page}}


def _decoded(key: str) -> str:
    from urllib.parse import unquote
    return unquote(key)
