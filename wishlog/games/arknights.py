"""明日方舟（原版，鹰角）。

和别的游戏最大的区别：它没有任何本地文件可以读，查记录必须有鹰角账号的令牌。
所以这里的做法是：你在自己的浏览器里登录官网，把官网页面上显示的令牌粘贴进来；
软件**不做登录、不接触手机号、密码和验证码**，令牌只在内存里用这一次，不写进硬盘、不写日志，
并且只会发给鹰角自己的域名（见 net.SafeTransport）。

接口链条（账号令牌 → 授权令牌 → 绑定角色 → 角色令牌 → 登录角色 → 按卡池类别翻页取记录）
参考了开源工具 AceDroidX/arknights-gacha-export 的 API.md 和 Gordenghost/arklog，
这里是用 Python 按本项目的结构重新实现的。账号这一侧（前四步）和终末地共用，见 hg_account.py。
"""

from __future__ import annotations

import time
import zlib
from urllib.parse import urlencode

from ..client import ApiError, AuthExpired
from ..locate import NeedsInput
from ..net import format_ts
from . import hg_account
from .base import ARKNIGHTS_RANKS, Game, Pool
from .hg_account import HgAccount, fine, text, url

AK_HOST = "https://ak.hypergryph.com"
PAGE_SIZE = 50

TOKEN_HELP = (
    "原版明日方舟没有本地文件可以读，需要一个账号令牌：\n"
    "① 用浏览器打开 https://ak.hypergryph.com/user/home ，登录你的鹰角账号；\n"
    "② 登录后，在同一个浏览器里打开 https://web-api.hypergryph.com/account/info/hg"
    "（B 服账号打开 https://web-api.hypergryph.com/account/info/ak-b ）；\n"
    "③ 页面上会显示一段文字，整段复制，粘贴到下面的框里，再点「更新记录」。\n"
    "令牌相当于账号的钥匙：软件只在内存里用一次，不会保存，也只发给鹰角官方，"
    "但请不要把它发给别人，也不要贴到别的地方。"
)


TOKEN_GUIDE = (
    text("① 用浏览器打开下面的网址，登录你的鹰角账号："),
    url("https://ak.hypergryph.com/user/home", "官网"),
    text("② 登录后，在同一个浏览器里打开下面的网址（官服账号用第一个，B 服账号用第二个）："),
    url("https://web-api.hypergryph.com/account/info/hg", "官服"),
    url("https://web-api.hypergryph.com/account/info/ak-b", "B 服"),
    text("③ 页面上会显示一段文字，整段复制，粘贴到上面的框里，再点「更新记录」。"),
    fine("令牌相当于账号的钥匙：软件只在内存里用一次，不会保存，也只发给鹰角官方，但请不要把它发给别人，也不要贴到别的地方。"),
)


def parse_account_token(token_text: str) -> str:
    """接受官网页面上的整段内容（JSON），或者单独的令牌。"""
    if not token_text.strip():
        raise NeedsInput("原版明日方舟需要先粘贴账号令牌才能更新。\n" + TOKEN_HELP)
    return hg_account.parse_account_token(token_text)


class ArknightsClient(HgAccount):
    def __init__(self, transport=None, sleep=time.sleep):
        super().__init__(transport, sleep)
        self._send_account_token = False   # 服务器拒绝过一次、要求带上它之后，后面的请求就直接带，不再每次先被拒一遍
        self._pages = 0

    # ---- 寻访记录这一侧（ak.hypergryph.com） ----
    def _ak(self, method: str, path: str, u8: str, body=None):
        for attempt in (1, 2):
            headers = {"x-role-token": u8, "Referer": f"{AK_HOST}/user/inquiryGacha"}
            if self._send_account_token:
                headers["x-account-token"] = self._account_token
            data = self._transport(method, f"{AK_HOST}{path}", headers, body)
            reason = data.get("reason") if isinstance(data, dict) else None
            if reason in ("UN_LOGIN", "MissingCookie") or (isinstance(data, dict) and data.get("code") == 401):
                if attempt == 1 and self._account_token and not self._send_account_token:
                    self._send_account_token = True      # 个别情况下还要带上账号令牌：记住，之后每次都带
                    continue
                raise AuthExpired("寻访记录的登录状态无效。请重新登录官网复制令牌，再更新一次。")
            if not isinstance(data, dict) or data.get("code") != 0:
                message = data.get("msg") if isinstance(data, dict) else ""
                raise ApiError(f"官方接口返回错误：{message or data}")
            return data.get("data")
        raise AuthExpired("寻访记录的登录状态无效。")

    def role_login(self, u8: str) -> None:
        self._ak("POST", "/user/api/role/login", u8, {"token": u8, "source_from": "", "share_type": "", "share_by": ""})

    def categories(self, uid: str, u8: str) -> list:
        return [c for c in (self._ak("GET", f"/user/api/inquiry/gacha/cate?{urlencode({'uid': uid})}", u8) or [])
                if isinstance(c, dict) and c.get("id")]

    def history_page(self, uid: str, u8: str, category: str, cursor: tuple | None) -> tuple:
        if self._pages:
            self._sleep(0.6)    # 翻页之间歇一下，免得被风控
        self._pages += 1
        query = {"uid": uid, "category": category, "size": PAGE_SIZE}
        if cursor:
            query["pos"], query["gachaTs"] = cursor
        data = self._ak("GET", f"/user/api/inquiry/gacha/history?{urlencode(query)}", u8) or {}
        return data.get("list") or [], bool(data.get("hasMore"))


class ArknightsAuth:
    """换到的授权令牌和绑定的角色。故意没有保存最初粘贴进来的账号令牌。"""

    def __init__(self, oauth: str, bindings: list):
        self.oauth = oauth
        self.bindings = bindings

    def __repr__(self) -> str:      # 万一被打印出来，也不会带出令牌
        return f"ArknightsAuth(bindings={len(self.bindings)})"


def record(item: dict, category: str) -> dict:
    """一条记录。接口不给唯一编号：同一次十连里的记录时间相同，靠 pos(0..9) 区分先后。

    编号 = 毫秒时间戳 + pos + 卡池类别摘要。现实里不同类别不会在同一毫秒出现，把类别也算进去
    是为了万一撞上，也只是多一条记录，而不是静默丢掉一条。
    """
    pos = int(item.get("pos") or 0)
    stamp = str(item["gachaTs"])
    return {
        "id": f"{stamp}{pos:02d}{zlib.crc32(category.encode('utf-8')) % 100:02d}", "gacha_type": category, "item_id": str(item.get("charId", "")),
        "count": "1", "time": format_ts(stamp), "name": item["charName"], "item_type": "干员",
        "rank_type": str(int(item["rarity"])), "free": "", "pool_id": str(item.get("poolId", "")),
    }


_PERSISTENT = frozenset({"normal", "classic"})   # 这两类的保底跨卡池继承，其余（限定寻访、联动等）每期重新计算


class ArknightsGame(Game):
    def make_client(self) -> ArknightsClient:
        return ArknightsClient()

    def make_pool(self, key: str, names: dict) -> Pool:
        return Pool(key, names.get(key) or key, 99, 50, reset_on_new_pool=key not in _PERSISTENT)

    def connect(self, client, url: str, game_dir: str) -> ArknightsAuth:
        account_token = parse_account_token(url)
        oauth = client.grant(account_token)
        bindings = client.bindings(oauth)
        if not bindings:
            raise ApiError("这个鹰角账号下没有绑定明日方舟角色（官服和 B 服的令牌网址不同，请确认用的是对应的那个）。")
        return ArknightsAuth(oauth, bindings)

    def sync(self, client, auth: ArknightsAuth, store, progress=None) -> dict:
        new: dict = {}
        warnings: list = []
        first_uid = None
        many = len(auth.bindings) > 1
        for binding in auth.bindings:
            uid = binding["uid"]
            who = f"{binding['channel'] or '账号'}{uid[-4:]}·" if many else ""
            u8 = client.u8_token(auth.oauth, uid)
            client.role_login(u8)
            categories = client.categories(uid, u8)
            names = {c["id"]: str(c.get("name") or c["id"]).replace("\n", " ") for c in categories}
            known = store.known_ids(uid)
            for category in categories:
                cid, label = category["id"], who + names[category["id"]]
                fresh: list = []
                cursor = None
                for _ in range(10_000):
                    items, has_more = client.history_page(uid, u8, cid, cursor)
                    stop = False
                    for item in items:
                        rec = record(item, cid)
                        if rec["id"] in known:
                            stop = True
                            break
                        fresh.append(rec)
                    if progress:
                        progress(label, len(fresh), sum(new.values()))
                    if stop or not has_more or not items:
                        break
                    last = items[-1]
                    nxt = (str(last.get("pos", 0)), str(last["gachaTs"]))
                    if nxt == cursor:    # 游标没有前进，免得死循环
                        break
                    cursor = nxt
                new[label] = store.merge(uid, fresh, meta={"pool_names": names}) if fresh else 0
            if known or any(new.values()):
                first_uid = first_uid or uid
        if first_uid is None:
            raise ApiError("这个账号在所有卡池里都没有寻访记录。")
        return {"uid": first_uid, "new": new, "total_new": sum(new.values()), "warnings": warnings}

    def diagnose(self, game_dir: str) -> list:
        return ["没有可以读取的本地文件：原版明日方舟需要在「高级」里粘贴账号令牌才能更新（这里只检查本机，不联网）。"]


ARKNIGHTS = ArknightsGame(
    key="arknights", name="明日方舟", short_name="明日方舟",
    # 卡池类别会随活动增加，这里只固定两个常见的；其余在读到记录时按接口给的名字动态生成（见 make_pool）。
    pools=(
        Pool("normal", "标准寻访", 99, 50),           # 连续 50 抽没出六星后每抽提高概率，第 99 抽必出
        Pool("classic", "中坚寻访", None, None),
    ),
    ranks=ARKNIGHTS_RANKS, currency="", cost_per_pull=None,
    hint="原版明日方舟没有本地文件可以读，需要先在「高级」里粘贴账号令牌（见下方说明）。",
    retention="一段时间内", trust_record_type=False,
    manual_label="账号令牌（必填）", manual_help=TOKEN_HELP, manual_secret=True, manual_required=True,
    manual_guide=TOKEN_GUIDE,
)
