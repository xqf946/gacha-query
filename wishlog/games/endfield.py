"""明日方舟：终末地（鹰角）。

和米哈游的游戏思路一样：游戏打开“寻访记录”页面时，会在本机日志里留下一条带 u8_token 的页面链接，
软件从日志里读出它，再用它去官方接口取记录。软件不登录账号，不接触账号密码。

接口地址、请求参数、卡池类型、返回结构、保底规则，参考了几个开源的终末地记录工具
（bhaoo/endfield-gacha、RoLingG/endfield-gacha-app、AceDroidX/arknights-gacha-export 的 API.md 等），
这里是用 Python 按本项目的结构重新实现的。
"""

from __future__ import annotations

import re
import time
import zlib
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlparse

from ..client import ApiError, AuthExpired, InvalidUrl
from ..locate import (
    GameNotFound, LocateError, describe_url, explain_read_failure, home_dir, mask_path,
    read_file_shared,
)
from ..net import default_transport, format_ts
from .base import SIX_STARS, Game, Pool

WEB_HOST = "https://ef-webview.hypergryph.com"
U8_HOST = "https://u8.hypergryph.com"
LINK_HOST = "ef-webview.hypergryph.com"
LOG_PARTS = ("AppData", "LocalLow", "Hypergryph", "Endfield", "sdklogs", "HGWebview.log")
_LINK_RE = re.compile(r"""https://ef-webview\.hypergryph\.com/page/gacha_[^\s"'<>]*""")
_FEED_CHAR, _FEED_WEAPON = 1, 2


# ---------- 找日志、取链接 ----------
def resolve_log(path) -> Path | None:
    """接受 HGWebview.log 本身，或它上面任意一级的文件夹。"""
    p = Path(str(path).strip().strip('"'))
    if p.is_file():
        return p if p.name.lower() == "hgwebview.log" else None
    if not p.is_dir():
        return None
    tail = LOG_PARTS[-1]
    candidates = [p / tail, p / "sdklogs" / tail, p / "Endfield" / "sdklogs" / tail,
                  p / "Hypergryph" / "Endfield" / "sdklogs" / tail]
    return next((c for c in candidates if c.is_file()), None)


def find_log(explicit=None, home: Path | None = None) -> Path:
    if explicit:
        found = resolve_log(explicit)
        if found:
            return found
        raise LocateError(
            f"在你选的位置里没有找到终末地的 HGWebview.log：{explicit}\n"
            r"它通常在 C:\Users\你的用户名\AppData\LocalLow\Hypergryph\Endfield\sdklogs 里。"
        )
    log = (home or home_dir()).joinpath(*LOG_PARTS)
    if not log.is_file():
        raise GameNotFound("没有找到终末地的日志文件。如果已经安装，请先启动一次游戏并打开「寻访记录」。")
    return log


def find_link(text: str) -> str | None:
    """日志里最新的一条寻访记录页链接（从后往前找第一条）。"""
    for line in reversed(text.splitlines()):
        m = _LINK_RE.search(line)
        if m:
            return m.group(0)
    return None


@dataclass(frozen=True)
class EndfieldAuth:
    token: str = field(repr=False)     # 万一这个对象被打印进日志或报错信息，不能把令牌带出去
    server_id: str
    uid: str
    nickname: str


def parse_link(url: str) -> tuple:
    """从链接里取出 (u8_token, server_id)。域名必须是鹰角的终末地页面，token 只会发给固定的官方地址。"""
    u = urlparse(url.strip().strip('"'))
    if u.scheme != "https" or u.hostname != LINK_HOST or not u.path.startswith("/page/gacha_"):
        raise InvalidUrl(f"这不是终末地的寻访记录链接（应该来自 {LINK_HOST}）。")
    params = dict(parse_qsl(u.query))
    token = params.get("u8_token", "").strip()
    if not token:
        raise InvalidUrl("链接里没有 u8_token。请重新在游戏里打开一次「寻访记录」。")
    server = (params.get("server_id") or params.get("server") or "1").strip()
    return token, (server if server.isdigit() else "1")


# ---------- 官方接口 ----------
def _looks_like_auth_problem(message: str) -> bool:
    text = message.lower()
    return any(w in text for w in ("token", "登录", "login", "expire", "invalid", "失效", "过期", "unauthorized"))


class EndfieldClient:
    def __init__(self, transport=None, sleep=time.sleep):
        self._transport = transport or default_transport()
        self._sleep = sleep
        self._calls = 0

    def _pace(self) -> None:
        if self._calls:
            self._sleep(0.2)     # 翻页之间歇一下，免得被风控
        self._calls += 1

    def _expired(self) -> AuthExpired:
        return AuthExpired(
            "寻访记录的凭证已经失效。请在游戏里重新打开一次「寻访」→「寻访记录」，再回来更新。"
        )

    def role(self, token: str, server_id: str) -> tuple:
        """用 token 换出账号的 UID 和角色名。同时也验证了 token 有没有失效。"""
        data = self._transport("POST", f"{U8_HOST}/game/role/v1/query_role_list", {},
                               {"token": token, "serverId": server_id})
        if not isinstance(data, dict) or data.get("status") != 0:
            raise self._expired()
        info = data.get("data") or {}
        roles = info.get("roles") or []
        role = next((r for r in roles if str(r.get("serverId", "")) == str(server_id)), roles[0] if roles else {})
        uid = str(info.get("uid") or "").strip()
        role_id = str(role.get("roleId") or "").strip()
        key = uid if uid.isdigit() else role_id
        if not key.isdigit():
            raise ApiError("没能从官方接口取到这个账号的 UID。")
        return key, str(role.get("nickname") or role.get("nickName") or "").strip()

    def _get(self, path: str, params: dict, auth: EndfieldAuth, page: str):
        self._pace()
        query = {"lang": "zh-cn", "token": auth.token, "server_id": auth.server_id, **params}
        referer = f"{WEB_HOST}/page/{page}?" + urlencode(
            {"u8_token": auth.token, "server": auth.server_id, "lang": "zh-cn"})
        data = self._transport("GET", f"{WEB_HOST}{path}?{urlencode(query)}", {"Referer": referer}, None)
        if not isinstance(data, dict):
            raise ApiError("官方接口返回的内容不对。")
        if data.get("code") != 0:
            message = str(data.get("msg") or data.get("message") or "")
            if _looks_like_auth_problem(message):
                raise self._expired()
            raise ApiError(f"官方接口返回错误：{message}（code {data.get('code')}）")
        return data.get("data")

    def char_page(self, auth: EndfieldAuth, pool_type: str, seq_id: str | None) -> tuple:
        params = {"pool_type": pool_type, **({"seq_id": seq_id} if seq_id else {})}
        data = self._get("/api/record/char", params, auth, "gacha_char") or {}
        return data.get("list") or [], bool(data.get("hasMore"))

    def weapon_pools(self, auth: EndfieldAuth) -> list:
        return self._get("/api/record/weapon/pool", {}, auth, "gacha_weapon") or []

    def weapon_page(self, auth: EndfieldAuth, pool_id: str, seq_id: str | None) -> tuple:
        params = {"pool_id": pool_id, **({"seq_id": seq_id} if seq_id else {})}
        data = self._get("/api/record/weapon", params, auth, "gacha_weapon") or {}
        return data.get("list") or [], bool(data.get("hasMore"))


# ---------- 记录整理 ----------
def weapon_group(pool_id: str) -> str:
    """武器池按名字里的特征分三类：常驻、重构、限定。"""
    value = (pool_id or "").lower()
    if "constant" in value:
        return "weapon_constant"
    if value.startswith("rerun") or "rerun_" in value:
        return "weapon_rerun"
    return "weapon_special"


def record_id(seq_id, pool_id: str, feed: int) -> str:
    """记录的本地编号 = 官方序号 + 卡池编号摘要 + 来源。

    不同卡池的序号有可能重复，所以把卡池 ID 也揉进去，免得两条不同的记录被当成同一条去重。
    """
    return f"{int(seq_id)}{zlib.crc32(str(pool_id).encode('utf-8')) % 10000:04d}{feed}"


def char_record(item: dict, pool_key: str) -> dict | None:
    """一条角色记录。奖励类条目（寻访情报书、赠礼）不是抽卡，返回 None 跳过。"""
    kind = str(item.get("kind") or "draw")
    if kind != "draw" or item.get("rarity") is None or not item.get("charName"):
        return None
    return {
        "id": record_id(item["seqId"], item.get("poolId", ""), _FEED_CHAR),
        "gacha_type": pool_key, "item_id": str(item.get("charId", "")), "count": "1",
        "time": format_ts(item["gachaTs"]), "name": item["charName"], "item_type": "角色",
        "rank_type": str(int(item["rarity"])),
        "free": "1" if item.get("isFree") else "", "pool_id": str(item.get("poolId", "")),
    }


def weapon_record(item: dict, pool_key: str) -> dict | None:
    kind = str(item.get("kind") or "draw")
    if kind != "draw" or item.get("rarity") is None or not item.get("weaponName"):
        return None
    return {
        "id": record_id(item["seqId"], item.get("poolId", ""), _FEED_WEAPON),
        "gacha_type": pool_key, "item_id": str(item.get("weaponId", "")), "count": "1",
        "time": format_ts(item["gachaTs"]), "name": item["weaponName"], "item_type": "武器",
        "rank_type": str(int(item["rarity"])), "free": "", "pool_id": str(item.get("poolId", "")),
    }


# ---------- 游戏 ----------
class EndfieldGame(Game):
    def make_client(self) -> EndfieldClient:
        return EndfieldClient()

    def connect(self, client, url: str, game_dir: str) -> EndfieldAuth:
        if url.strip():
            link = url.strip()
        else:
            log = find_log(game_dir or None)
            try:
                blob = read_file_shared(log)
            except OSError as e:
                raise LocateError(explain_read_failure(log, e, "游戏日志文件")) from e
            link = find_link(blob.decode("utf-8", errors="replace"))
            if not link:
                raise LocateError(
                    "日志里没有找到寻访记录链接。请先在游戏里打开一次「寻访」→「寻访记录」，再回来更新。"
                )
        token, server_id = parse_link(link)
        uid, nickname = client.role(token, server_id)
        return EndfieldAuth(token, server_id, uid, nickname)

    def _feed(self, fetch_page, make_record, known: set) -> list:
        """翻完一个来源（一种角色池，或一个武器池）。遇到本地已有的记录就停。返回新记录，从新到旧。"""
        fresh: list = []
        seq_id = None
        for _ in range(10_000):
            items, has_more = fetch_page(seq_id)
            stop = False
            for item in items:
                record = make_record(item)
                if record is None:
                    continue
                if record["id"] in known:
                    stop = True
                    break
                fresh.append(record)
            if stop or not has_more or not items or not items[-1].get("seqId"):
                break
            seq_id = str(items[-1]["seqId"])    # 翻页游标取“过滤前”的最后一条，它可能是奖励条目
        return fresh

    def sync(self, client, auth: EndfieldAuth, store, progress=None) -> dict:
        known = store.known_ids(auth.uid)
        new: dict = {}
        warnings: list = []
        total_fresh = 0

        def report(name: str, count: int) -> None:
            if progress:
                progress(name, count, sum(new.values()))

        for pool in self.pools:
            if pool.key.startswith("weapon_"):
                continue
            pool_type = f"E_CharacterGachaPoolType_{pool.key.capitalize()}"
            try:
                fresh = self._feed(
                    lambda seq, t=pool_type: client.char_page(auth, t, seq),
                    lambda item, k=pool.key: char_record(item, k), known)
            except ApiError as e:
                if not pool.optional:
                    raise
                warnings.append(f"「{pool.name}」暂时取不到，已跳过（{e}）")
                new[pool.name] = 0
                continue
            total_fresh += len(fresh)
            new[pool.name] = store.merge(auth.uid, fresh) if fresh else 0
            report(pool.name, len(fresh))

        by_group: dict = {p.key: [] for p in self.pools if p.key.startswith("weapon_")}
        try:
            for entry in client.weapon_pools(auth):
                pool_id = str(entry.get("poolId") or "")
                if not pool_id:
                    continue
                group = weapon_group(pool_id)
                by_group[group].extend(self._feed(
                    lambda seq, i=pool_id: client.weapon_page(auth, i, seq),
                    lambda item, k=group: weapon_record(item, k), known))
        except ApiError as e:
            warnings.append(f"武器池暂时取不到，已跳过（{e}）")
        for pool in self.pools:
            if pool.key in by_group:
                fresh = by_group[pool.key]
                total_fresh += len(fresh)
                new[pool.name] = store.merge(auth.uid, fresh) if fresh else 0
                report(pool.name, len(fresh))

        if not known and not total_fresh:
            raise ApiError("这个账号在所有卡池里都没有寻访记录。")
        return {"uid": auth.uid, "new": new, "total_new": sum(new.values()), "warnings": warnings}

    def diagnose(self, game_dir: str) -> list:
        try:
            log = find_log(game_dir or None)
        except LocateError as e:
            return [f"游戏日志：没找到（{str(e).splitlines()[0]}）"]
        info = log.stat()
        when = datetime.fromtimestamp(info.st_mtime).strftime("%Y-%m-%d %H:%M")
        lines = [f"游戏日志：{mask_path(log)}（{info.st_size / 1024:.0f} KB，最后写入 {when}）"]
        try:
            blob = read_file_shared(log)
        except OSError as e:
            return lines + [f"读取日志：失败（{explain_read_failure(log, e, '日志').splitlines()[0]}）"]
        link = find_link(blob.decode("utf-8", errors="replace"))
        if not link:
            return lines + ["寻访记录链接：没找到（请先在游戏里打开一次「寻访」→「寻访记录」）"]
        has_token = "u8_token=" in link
        return lines + [f"寻访记录链接：找到；{describe_url(link)}；{'含' if has_token else '不含'} u8_token"]


ENDFIELD = EndfieldGame(
    key="endfield", name="明日方舟：终末地", short_name="终末地",
    pools=(
        # 角色池（key 就是官方 pool_type 去掉前缀后的小写）。80 抽小保底，约 65 抽起概率提升；120 抽大保底这里不统计。
        Pool("special", "特许寻访", 80, 65),
        Pool("rerun", "重构寻访", 80, 65, optional=True),
        Pool("joint", "辉光庆典", None, None, optional=True),
        Pool("standard", "基础寻访", 80, 65),
        Pool("beginner", "启程寻访", None, None, optional=True),
        # 武器池：40 抽小保底。限定、重构武器池的保底不跨期继承，换一期就从头算。
        Pool("weapon_special", "限定申领", 40, None, optional=True, reset_on_new_pool=True),
        Pool("weapon_constant", "常驻申领", 40, None, optional=True),
        Pool("weapon_rerun", "重构申领", 40, None, optional=True, reset_on_new_pool=True),
    ),
    ranks=SIX_STARS, currency="", cost_per_pull=None,
    hint="进入「寻访」页面，打开「寻访记录」，随便点开一个卡池翻一翻。",
    retention="最近 90 天", trust_record_type=False,
)
