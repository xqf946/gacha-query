"""测试用的假官方接口和假数据。"""

from __future__ import annotations

import random
from urllib.parse import parse_qsl, unquote, urlparse

VALID_KEY = "VALIDKEY%2Babc%2Fdef%3D%3D"  # 链接里的 authkey 是百分号编码过的
FIVE_STARS = ["胡桃", "钟离", "刻晴", "雷电将军", "那维莱特"]
FOUR_STARS = ["香菱", "行秋", "北斗", "砂糖"]

HOSTS = {
    "hk4e": "public-operation-hk4e.mihoyo.com",
    "hkrpg": "public-operation-hkrpg.mihoyo.com",
    "nap": "public-operation-nap.mihoyo.com",
}


def wish_url(authkey: str = VALID_KEY, host: str = HOSTS["hk4e"], game_biz: str = "hk4e_cn", **override) -> str:
    query = {
        "authkey_ver": "1", "sign_type": "2", "auth_appid": "webview_gacha", "init_type": "301",
        "gacha_id": "abc", "timestamp": "1700000000", "lang": "zh-cn", "device_type": "pc",
        "game_version": "CNRELWin5.0_R1_", "region": "cn_gf01", "authkey": authkey,
        "game_biz": game_biz, "gacha_type": "301", "page": "1", "size": "5", "end_id": "0",
    }
    query.update(override)
    return f"https://{host}/gacha_info/api/getGachaLog?" + "&".join(f"{k}={v}" for k, v in query.items())


def game_url(biz: str, authkey: str = VALID_KEY, **override) -> str:
    """某个米哈游游戏的链接。biz 是 hk4e / hkrpg / nap。"""
    return wish_url(authkey, host=HOSTS[biz], game_biz=f"{biz}_cn", **override)


_counter = [0]


def make_records(gacha_type: str, n: int, uid: str = "100000001", seed: int = 1, ranks=("5", "4", "3")) -> list:
    """生成 n 条按时间从旧到新排好的记录，id 全局递增。ranks 依次是最高、次高、最低档。"""
    rng = random.Random(seed)
    out = []
    for _ in range(n):
        _counter[0] += 1
        roll = rng.random()
        rank, name, kind = (
            (ranks[0], rng.choice(FIVE_STARS), "角色") if roll < 0.03
            else (ranks[1], rng.choice(FOUR_STARS), "角色") if roll < 0.2
            else (ranks[2], "黑缨枪", "武器")
        )
        out.append({
            "uid": uid, "gacha_type": gacha_type, "item_id": "", "count": "1",
            "time": f"2026-01-{1 + _counter[0] % 28:02d} 12:00:00", "name": name,
            "item_type": kind, "rank_type": rank, "id": str(1_700_000_000_000_000_000 + _counter[0]),
            "lang": "zh-cn",
        })
    return out


class FakeApi:
    """模拟米哈游的 getGachaLog：按 end_id 翻页、从新到旧返回，authkey 不对就返回 -101。"""

    def __init__(self, pools: dict):
        self.pools = pools            # {"301": [...旧到新...], ...}
        self.calls = []               # 每次请求的 (卡池, 页码, end_id, 路径)
        self.fail_on = None           # 形如 ("302", 2)：该卡池第 2 页返回 -101
        self.fail_pools = {}          # {"21": (retcode, message)}：这个卡池一律返回错误
        self.frequent_times = 0       # 前 N 次请求返回 -110
        self.valid_key = VALID_KEY

    def fetch(self, url: str) -> dict:
        parsed = urlparse(url)
        q = dict(parse_qsl(parsed.query))
        pool = q.get("gacha_type") or q.get("real_gacha_type")
        self.calls.append((pool, int(q["page"]), q["end_id"], parsed.path))
        if self.frequent_times > 0:
            self.frequent_times -= 1
            return {"retcode": -110, "message": "visit too frequently", "data": None}
        if q["authkey"] != unquote(self.valid_key) or self.fail_on == (pool, int(q["page"])):
            return {"retcode": -101, "message": "authkey timeout", "data": None}
        if pool in self.fail_pools:
            code, message = self.fail_pools[pool]
            return {"retcode": code, "message": message, "data": None}
        records = sorted(self.pools.get(pool, []), key=lambda r: int(r["id"]), reverse=True)
        end_id = int(q["end_id"])
        if end_id:
            records = [r for r in records if int(r["id"]) < end_id]
        page = records[: int(q["size"])]
        return {"retcode": 0, "message": "OK", "data": {"page": q["page"], "list": page}}


# ---------- 鸣潮 ----------
WUWA_URL = (
    "https://aki-gm-resources.aki-game.com/aki/gacha/index.html#/record"
    "?svr_id=76402e5b20be2c39f095a152090afddc&player_id=100000001&lang=zh-Hans"
    "&gacha_id=100003&gacha_type=1&svr_area=cn&record_id=TOKEN123&resources_id=POOL456"
)
WUWA_ANNOUNCEMENT_URL = "https://aki-gm-resources.aki-game.com/aki/announcement/index.html#/notice?x=1"


def encrypt_client_log(text: str) -> bytes:
    """鸣潮日志的“加密”：开头 3 字节 BOM；解码时奇数字节异或 0xA5、偶数字节异或 0xEF，
    所以编码是它的逆运算：明文字节为偶数就异或 0xA5，为奇数就异或 0xEF。"""
    body = bytes((c ^ 0xA5) if c % 2 == 0 else (c ^ 0xEF) for c in text.encode("utf-8"))
    return b"\xef\xbb\xbf" + body


def wuwa_log_line(url: str, stamp: str = "2026.10.06-12.00.00:123", kind: str = "OpenWebView") -> str:
    escaped = url.replace("&", "\\u0026")
    return f'[{stamp}][ 77]LogGame: Display: {kind} sdkJson={{"title":"x","url":"{escaped}"}}\n'


def wuwa_item(name: str, quality: int, stamp: str, kind: str = "角色", rid: int = 1, pool_name: str = "角色精准调谐") -> dict:
    return {"cardPoolType": pool_name, "resourceId": rid, "qualityLevel": quality,
            "resourceType": kind, "name": name, "count": 1, "time": stamp}


class FakeWuwaApi:
    """模拟鸣潮的 POST 接口：校验 playerId/recordId，按 cardPoolType 返回该卡池的全部记录（从新到旧）。"""

    def __init__(self, pools: dict, player_id: str = "100000001", record_id: str = "TOKEN123"):
        self.pools = pools            # {"1": [...从新到旧...]}
        self.player_id, self.record_id = player_id, record_id
        self.calls = []               # (url, 请求体)
        self.int_only = False         # True：服务器只认数字的 cardPoolType
        self.fail_pools = {}          # {"13": (code, message)}

    def post(self, url: str, body: dict) -> dict:
        self.calls.append((url, dict(body)))
        pool = body["cardPoolType"]
        if self.int_only and not isinstance(pool, int):
            return {"code": -1, "message": "param error", "data": []}
        if body["playerId"] != self.player_id or body["recordId"] != self.record_id:
            return {"code": -1, "message": "record expired", "data": []}
        if str(pool) in self.fail_pools:
            code, message = self.fail_pools[str(pool)]
            return {"code": code, "message": message, "data": []}
        return {"code": 0, "message": "success", "data": list(self.pools.get(str(pool), []))}
