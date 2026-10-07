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


# ---------- 终末地 ----------
EF_TOKEN = "U8TOKEN-abc123"
EF_ACCOUNT_TOKEN = "EFACCOUNT-token-0123456789abcdef"     # 用户从官网复制的账号令牌
EF_LINK = (
    "https://ef-webview.hypergryph.com/page/gacha_char?pool_id=special_1_0_3&platform=Windows"
    f"&channel=1&subChannel=1&lang=zh-cn&server=1&u8_token={EF_TOKEN}"
)


def ef_char(seq: int, name: str, rarity: int, pool_id: str = "special_1_0_3", ts: int = 1770439342803,
            free: bool = False, kind: str = "draw") -> dict:
    if kind != "draw":
        return {"kind": kind, "nameText": "寻访情报书", "poolId": pool_id, "poolName": "某池",
                "gachaTs": str(ts + seq), "seqId": str(seq)}
    return {"kind": "draw", "nameText": name, "poolId": pool_id, "poolName": "某池", "charId": f"chr_{seq}",
            "charName": name, "rarity": rarity, "isFree": free, "isNew": False,
            "gachaTs": str(ts + seq), "seqId": str(seq)}


def ef_weapon(seq: int, name: str, rarity: int, pool_id: str = "weponbox_1_0_1", ts: int = 1769565182199) -> dict:
    return {"kind": "draw", "nameText": name, "poolId": pool_id, "poolName": "某武器池", "weaponId": f"wpn_{seq}",
            "weaponName": name, "weaponType": "E_WeaponType_Lance", "rarity": rarity, "isNew": False,
            "gachaTs": str(ts + seq), "seqId": str(seq)}


class FakeEndfield:
    """模拟终末地官方接口：transport(method, url, headers, body) -> 已解析的 JSON。

    chars：{"special": [...从新到旧...]}；weapons：{poolId: [...从新到旧...]}。
    """

    def __init__(self, chars=None, weapons=None, token: str = EF_TOKEN, uid: str = "987654321", page_size: int = 3):
        self.chars = chars or {}
        self.weapons = weapons or {}
        self.token, self.uid, self.page_size = token, uid, page_size
        self.calls = []                 # (方法, 主机, 路径, 查询参数, 请求头, 请求体)
        self.fail_pool = {}             # {"rerun": (code, msg)}
        self.role_status = 0
        self.role_uid = None            # 默认用 uid；设成 "" 可以模拟接口没给 uid
        self.account_token = EF_ACCOUNT_TOKEN
        self.bindings = None            # 账号令牌这条路：[(uid, 昵称)]；默认是 [(uid, "管理员")]

    def _page(self, items: list, seq_id):
        start = 0
        if seq_id:
            start = next((i + 1 for i, it in enumerate(items) if it["seqId"] == seq_id), len(items))
        page = items[start:start + self.page_size]
        return {"code": 0, "msg": "", "data": {"list": page, "hasMore": start + self.page_size < len(items)}}

    def __call__(self, method, url, headers, body):
        parsed = urlparse(url)
        query = dict(parse_qsl(parsed.query))
        self.calls.append((method, parsed.hostname, parsed.path, query, dict(headers), body))
        if parsed.hostname == "as.hypergryph.com":                    # 账号令牌 → 授权令牌
            if (body or {}).get("token") != self.account_token or body.get("appCode") != "be36d44aa36bfb5b":
                return {"status": 3, "msg": "token expired"}
            return {"status": 0, "msg": "OK", "data": {"token": "EF-OAUTH"}}
        if parsed.hostname == "binding-api-account-prod.hypergryph.com":
            if parsed.path.endswith("/binding_list"):
                if query.get("token") != "EF-OAUTH" or query.get("appCode") != "endfield":
                    return {"status": 3, "msg": "bad oauth"}
                pairs = self.bindings if self.bindings is not None else [(self.uid, "管理员")]
                return {"status": 0, "msg": "OK", "data": {"list": [
                    {"appCode": "arknights", "bindingList": [{"uid": "111", "channelName": "官服"}]},
                    {"appCode": "endfield", "appName": "终末地", "bindingList": [
                        {"uid": uid, "channelName": "官服", "nickName": nick,
                         "roles": [{"serverId": "1", "serverName": "China", "nickName": nick, "roleId": "555"}]}
                        for uid, nick in pairs]},
                ]}}
            if parsed.path.endswith("/u8_token_by_uid"):
                if body.get("token") != "EF-OAUTH":
                    return {"status": 3, "msg": "bad oauth"}
                return {"status": 0, "msg": "OK", "data": {"token": self.token}}
        if parsed.hostname == "u8.hypergryph.com":
            if not body or body.get("token") != self.token:
                return {"status": 3, "msg": "token invalid"}
            if self.role_status != 0:       # 账号服务拒绝（记录接口那边可能仍然认这个令牌）
                return {"status": self.role_status, "msg": "role service refused"}
            uid = self.uid if self.role_uid is None else self.role_uid
            return {"status": self.role_status, "msg": "OK", "data": {
                "uid": uid, "roles": [{"serverId": "1", "roleId": "555", "nickname": "管理员", "serverName": "China"}]}}
        if query.get("token") != self.token:
            return {"code": 40001, "msg": "token invalid or expired", "data": None}
        if parsed.path == "/api/record/char":
            key = query["pool_type"].rsplit("_", 1)[-1].lower()
            if key in self.fail_pool:
                code, msg = self.fail_pool[key]
                return {"code": code, "msg": msg, "data": None}
            return self._page(self.chars.get(key, []), query.get("seq_id"))
        if parsed.path == "/api/record/weapon/pool":
            return {"code": 0, "msg": "", "data": [{"poolId": pid, "poolName": pid} for pid in self.weapons]}
        if parsed.path == "/api/record/weapon":
            return self._page(self.weapons.get(query["pool_id"], []), query.get("seq_id"))
        return {"code": 404, "msg": "not found"}


# ---------- 明日方舟 ----------
AK_ACCOUNT_TOKEN = "ACCOUNT-TOKEN-secret-0123456789"


def ak_item(n: int, name: str, rarity: int, category_ts: int = 1770697079082, pos: int | None = None,
            pool: str = "pool_a") -> dict:
    """第 n 条记录（n 越大越新）；每 10 条是一次十连，共用一个时间戳，pos 为 0..9。"""
    return {"poolId": pool, "poolName": "某卡池", "charId": f"char_{n}", "charName": name, "rarity": rarity,
            "isNew": False, "gachaTs": str(category_ts + n // 10 * 60_000), "pos": n % 10 if pos is None else pos}


class FakeArknights:
    """模拟原版明日方舟的整条接口链：授权 → 绑定 → 角色令牌 → 登录(Cookie) → 类别 → 翻页记录。

    history：{类别id: [...从新到旧...]}；bindings：[(uid, 渠道, 昵称)]
    """

    def __init__(self, history=None, categories=None, bindings=None, account_token: str = AK_ACCOUNT_TOKEN,
                 page_size: int = 4):
        self.history = history or {}
        self.categories = categories or [{"id": cid, "name": cid} for cid in self.history]
        self.bindings = bindings or [("555000111", "官服", "博士")]
        self.account_token, self.page_size = account_token, page_size
        self.calls = []                  # (方法, 主机, 路径, 查询参数, 请求头, 请求体)
        self.logged_in_as = set()
        self.needs_account_header = False   # True：没有 x-account-token 请求头就回 UN_LOGIN（模拟个别接口要求）
        self.drop_cookie = False            # True：永远回 MissingCookie

    def _u8(self, uid):
        return f"U8-{uid}"

    def __call__(self, method, url, headers, body):
        parsed = urlparse(url)
        query = dict(parse_qsl(parsed.query))
        self.calls.append((method, parsed.hostname, parsed.path, query, dict(headers), body))
        path = parsed.path
        if path == "/user/oauth2/v2/grant":
            if body.get("token") != self.account_token or body.get("appCode") != "be36d44aa36bfb5b" or body.get("type") != 1:
                return {"status": 3, "msg": "token expired"}
            return {"status": 0, "msg": "OK", "data": {"token": "OAUTH-xyz"}}
        if path == "/account/binding/v1/binding_list":
            if query.get("token") != "OAUTH-xyz":
                return {"status": 3, "msg": "bad oauth"}
            return {"status": 0, "msg": "OK", "data": {"list": [
                {"appCode": "arknights", "appName": "明日方舟", "bindingList": [
                    {"uid": uid, "channelName": channel, "nickName": nick} for uid, channel, nick in self.bindings]},
                {"appCode": "endfield", "appName": "终末地", "bindingList": [{"uid": "777", "channelName": "官服"}]},
            ]}}
        if path == "/account/binding/v1/u8_token_by_uid":
            return {"status": 0, "msg": "OK", "data": {"token": self._u8(body["uid"])}}
        if path == "/user/api/role/login":
            self.logged_in_as.add(body["token"])
            return {"code": 0, "msg": "", "data": {}}
        # 以下接口要先登录（Cookie），并带角色令牌
        role = headers.get("x-role-token")
        if self.drop_cookie or role not in self.logged_in_as:
            return {"message": "缺少 Cookie", "reason": "MissingCookie"}
        if self.needs_account_header and headers.get("x-account-token") != self.account_token:
            return {"message": "未登录", "reason": "UN_LOGIN"}
        if path == "/user/api/inquiry/gacha/cate":
            return {"code": 0, "msg": "", "data": self.categories}
        if path == "/user/api/inquiry/gacha/history":
            items = self.history.get(query["category"], [])
            start = 0
            if query.get("gachaTs"):
                start = next((i + 1 for i, it in enumerate(items)
                              if it["gachaTs"] == query["gachaTs"] and str(it["pos"]) == query["pos"]), len(items))
            page = items[start:start + self.page_size]
            return {"code": 0, "msg": "", "data": {"list": page, "hasMore": start + self.page_size < len(items)}}
        return {"code": 404, "msg": "not found"}
