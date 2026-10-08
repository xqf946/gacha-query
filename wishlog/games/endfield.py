"""明日方舟：终末地（鹰角）。

有两条路拿到取记录要用的凭证（u8_token），软件都不登录账号、不接触账号密码：
1. 读日志：游戏打开“寻访记录”页面时，会在本机日志里留下一条带 u8_token 的页面链接。
   但官方可能已经调整了日志的写法，这条路在有的机器上已经走不通（官方不再认日志里的令牌）。
2. 账号令牌：用户自己在浏览器里登录官网，把官网页面上显示的账号令牌粘贴进来，
   软件用它换出 u8_token（和明日方舟共用同一套账号接口，见 hg_account.py）。

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
from urllib.parse import parse_qsl, unquote, unquote_plus, urlencode, urlparse

from ..client import ApiError, AuthExpired, InvalidUrl
from ..locate import (
    GameNotFound, LocateError, describe_url, explain_read_failure, home_dir, mask_path,
    read_file_shared,
)
from ..net import default_transport, format_ts
from .base import SIX_STARS, Game, Pool
from .hg_account import HgAccount, fine, parse_account_token, text, url

WEB_HOST = "https://ef-webview.hypergryph.com"
U8_HOST = "https://u8.hypergryph.com"
LINK_HOST = "ef-webview.hypergryph.com"
LOG_PARTS = ("AppData", "LocalLow", "Hypergryph", "Endfield", "sdklogs", "HGWebview.log")
_LINK_RE = re.compile(r"""https://ef-webview\.hypergryph\.com/page/gacha_[^\s"'<>]*""")
_FEED_CHAR, _FEED_WEAPON = 1, 2
MAX_TRIES = 6              # 日志里有好几条链接、token 又有几种写法时，最多试几次
UNKNOWN_UID = "0"          # 官方没告诉我们 UID 时，记录先放在这个临时账号下


TOKEN_STEPS = (
    "① 用浏览器打开 https://user.hypergryph.com/ ，登录你的鹰角账号；\n"
    "② 登录后，在同一个浏览器里打开 https://web-api.hypergryph.com/account/info/hg ；\n"
    "③ 页面上会显示一段文字，整段复制，粘贴到「高级」里的输入框，再点「更新记录」。\n"
    "令牌相当于账号的钥匙：软件只在内存里用一次，不会保存，也只发给鹰角官方，"
    "但请不要把它发给别人，也不要贴到别的地方。"
)
TOKEN_HELP = (
    "软件会先试着从游戏日志里读凭证；官方可能已经调整了日志的写法，读不到或官方不认的话，请改用账号令牌"
    "（和明日方舟用的是同一个）：\n" + TOKEN_STEPS + "\n也可以在这里粘贴一条寻访记录页面的链接。"
)


TOKEN_GUIDE = (
    text("软件会先试着从游戏日志里读凭证；官方可能已经调整了日志的写法，读不到或官方不认的话，请改用账号令牌（和明日方舟用的是同一个）："),
    text("① 用浏览器打开下面的网址，登录你的鹰角账号："),
    url("https://user.hypergryph.com/", "登录"),
    text("② 登录后，在同一个浏览器里打开下面的网址："),
    url("https://web-api.hypergryph.com/account/info/hg", "令牌"),
    text("③ 页面上会显示一段文字，整段复制，粘贴到上面的框里，再点「更新记录」。"),
    fine("令牌相当于账号的钥匙：软件只在内存里用一次，不会保存，也只发给鹰角官方，但请不要把它发给别人，也不要贴到别的地方。也可以在这里粘贴一条寻访记录页面的链接。"),
)


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


_TOKEN_RE = re.compile(r"[?&]u8_token=([^&#\s\"'<>\\]+)")


def _clean_link(url: str) -> str:
    """日志里的链接有时是 JSON 转义过的（& 写成 \\u0026）或后面粘着标点，先还原干净。"""
    url = url.replace("\\u0026", "&").replace("&amp;", "&")
    return url.rstrip("\\,;)]}")


def find_links(text: str, limit: int = 8) -> list:
    """日志里所有寻访记录页链接，最新的在前；同一个 token 只留一条。"""
    seen, found = set(), []
    for line in reversed(text.splitlines()):
        for m in reversed(list(_LINK_RE.finditer(line))):
            url = _clean_link(m.group(0))
            t = _TOKEN_RE.search(url)
            if t and t.group(1) not in seen:
                seen.add(t.group(1))
                found.append(url)
                if len(found) >= limit:
                    return found
    return found


def find_link(text: str) -> str | None:
    """日志里最新的一条寻访记录页链接。"""
    links = find_links(text, limit=1)
    return links[0] if links else None


@dataclass(frozen=True)
class EndfieldAuth:
    token: str = field(repr=False)     # 万一这个对象被打印进日志或报错信息，不能把令牌带出去
    server_id: str
    uid: str
    nickname: str
    uid_known: bool = True             # False：官方没回 UID，记录暂存在临时账号 UNKNOWN_UID 下
    note: str = ""                     # 要提醒用户的话（例如这个账号下有多个角色），会放进更新结果的提示里


def parse_link_all(url: str) -> tuple:
    """从链接里取出 ([可能的 token 写法], server_id)。

    链接里的 token 可能含 + 号：按网页规则 + 会被当成空格，但 token 里不会有空格，
    所以两种还原方式都准备好，先试保留 + 的那种。域名必须是鹰角的终末地页面，token 只会发给固定的官方地址。
    """
    u = urlparse(_clean_link(url.strip().strip('"')))
    if u.scheme != "https" or u.hostname != LINK_HOST or not u.path.startswith("/page/gacha_"):
        raise InvalidUrl(f"这不是终末地的寻访记录链接（应该来自 {LINK_HOST}）。")
    found = _TOKEN_RE.search("?" + u.query)
    if not found:
        raise InvalidUrl("链接里没有 u8_token。请重新在游戏里打开一次「寻访记录」。")
    raw = found.group(1)
    variants = []
    for candidate in (unquote(raw), unquote_plus(raw), raw):
        if candidate and candidate not in variants:
            variants.append(candidate)
    params = dict(parse_qsl(u.query))
    server = (params.get("server_id") or params.get("server") or "1").strip()
    return variants, (server if server.isdigit() else "1")


def parse_link(url: str) -> tuple:
    """从链接里取出 (u8_token, server_id)。"""
    variants, server = parse_link_all(url)
    return variants[0], server


# ---------- 官方接口 ----------
def _looks_like_auth_problem(message: str) -> bool:
    """只有说到“令牌/登录/过期”的才算凭证问题。单独一个 invalid 不算：
    “invalid pool_type”这类是在说别的参数，把它当成凭证失效会把真正的原因藏起来。"""
    text = message.lower()
    return any(w in text for w in ("token", "登录", "login", "expire", "失效", "过期", "unauthorized"))


def _detail(data, token: str = "") -> str:
    """把官方的回复整理成一句能看懂的话，用在报错里。令牌如果被原样回显，替换掉。"""
    if isinstance(data, dict):
        code = data.get("status", data.get("code"))
        text = f"code={code}, msg={data.get('msg') or data.get('message') or ''}"
    else:
        text = f"返回的不是预期的内容：{str(data)[:80]}"
    for secret in {token, unquote_plus(token) if token else ""}:
        if secret:
            text = text.replace(secret, "***")
    return text[:160]


def _expired_text(*reasons: str) -> str:
    return (
        f"官方没有接受这个凭证（{'；'.join(reasons)}）。\n"
        "用日志的话，请在游戏里重新打开一次「寻访」→「寻访记录」，过几秒再点更新；用账号令牌的话，请重新复制一遍。"
        "如果已经这样做过还是不行，请点「高级」→「环境检测」，把结果发给开发者。"
    )


def _log_not_accepted(error: AuthExpired) -> AuthExpired:
    """日志里的凭证官方不认：告诉用户改用账号令牌（第一行自成一句，“更新全部”只显示第一行）。"""
    reasons = "；".join(getattr(error, "reasons", None) or [str(error).splitlines()[0]])
    return AuthExpired(
        f"官方没有接受日志里的凭证（{reasons}）；官方可能调整过日志，这个办法多半走不通了，请改用账号令牌。\n"
        "做法：把账号令牌粘贴到下面「高级」的输入框里，步骤见输入框下面的说明。"
    )


def _one_line(error) -> str:
    return str(error).strip().splitlines()[0][:160] if str(error).strip() else type(error).__name__


def _rejected(step: str, data, token: str = "") -> AuthExpired:
    reason = f"{step} {_detail(data, token)}"
    error = AuthExpired(_expired_text(reason))
    error.reason = reason          # 几次尝试都被拒绝时，把每次的原因合在一条提示里
    error.reasons = [reason]
    return error


class EndfieldClient(HgAccount):
    def __init__(self, transport=None, sleep=time.sleep):
        super().__init__(transport, sleep)
        self._calls = 0

    def account_auth(self, account_token: str) -> EndfieldAuth:
        """账号令牌 → 授权令牌 → 绑定的终末地角色 → 角色令牌（取记录用的 u8_token）。国服的服务器编号固定是 1。"""
        oauth = self.grant(account_token)
        found = self.bindings(oauth, "endfield")
        if not found:
            raise ApiError("这个鹰角账号下没有绑定终末地角色。请确认登录的是玩终末地的那个鹰角账号。")
        first = found[0]
        roles = first.get("roles") or [{}]
        nickname = (first["nickname"] or str(roles[0].get("nickName") or "")).strip()
        note = f"这个鹰角账号下有 {len(found)} 个终末地角色，这次只更新了第一个（UID {first['uid']}）。" if len(found) > 1 else ""
        return EndfieldAuth(self.u8_token(oauth, first["uid"]), "1", first["uid"], nickname, note=note)

    def _pace(self) -> None:
        if self._calls:
            self._sleep(0.2)     # 翻页之间歇一下，免得被风控
        self._calls += 1

    def role(self, token: str, server_id: str) -> tuple:
        """用 token 换出账号的 UID 和角色名。同时也验证了 token 有没有失效。"""
        data = self._transport("POST", f"{U8_HOST}/game/role/v1/query_role_list", {},
                               {"token": token, "serverId": server_id})
        ok = isinstance(data, dict) and (data.get("status") == 0 or (data.get("status") is None and data.get("code") == 0))
        if not ok:
            raise _rejected("查询账号 query_role_list", data, token)
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
            raise ApiError(f"官方接口返回的内容不对（{_detail(data, auth.token)}）。")
        if data.get("code") != 0:
            message = str(data.get("msg") or data.get("message") or "")
            if _looks_like_auth_problem(message):
                raise _rejected(f"取记录 {path}", data, auth.token)
            raise ApiError(f"官方接口返回错误：{_detail(data, auth.token)}（{path}）")
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
        text = url.strip()
        if text and "ef-webview" not in text and not text.lower().startswith("http"):
            return client.account_auth(parse_account_token(text))     # 粘贴的是账号令牌
        if text:
            return self._from_links(client, [text])                   # 粘贴的是寻访记录链接
        log = find_log(game_dir or None)
        try:
            blob = read_file_shared(log)
        except OSError as e:
            raise LocateError(explain_read_failure(log, e, "游戏日志文件")) from e
        links = find_links(blob.decode("utf-8", errors="replace"))
        if not links:
            raise LocateError(
                "日志里没有找到寻访记录链接。请先在游戏里打开一次「寻访」→「寻访记录」，再回来更新；"
                "如果打开过还是找不到，官方可能调整了日志的写法，请改用账号令牌（做法见下面「高级」里的说明）。"
            )
        try:
            return self._from_links(client, links)
        except AuthExpired as e:
            raise _log_not_accepted(e) from e

    def _from_links(self, client, links: list) -> EndfieldAuth:
        # 最新的链接排最前；每条链接里的 token 可能有几种写法，都排进候选里，一个个试到被官方接受为止
        candidates = []
        for link in links:
            variants, server_id = parse_link_all(link)
            candidates.extend((token, server_id) for token in variants)
        rejected = []
        for token, server_id in candidates[:MAX_TRIES]:
            try:
                uid, nickname = client.role(token, server_id)
            except AuthExpired as e:
                rejected.append(e)
                continue
            return EndfieldAuth(token, server_id, uid, nickname)
        return self._without_role(client, candidates[0], rejected)

    def _without_role(self, client, candidate, rejected: list) -> EndfieldAuth:
        """查账号被拒绝时，再直接问一次记录接口：它认这个令牌的话，就能取到记录，只是不知道 UID。"""
        token, server_id = candidate
        auth = EndfieldAuth(token, server_id, UNKNOWN_UID, "", uid_known=False)
        reasons = [getattr(e, "reason", str(e)) for e in rejected[:2]]
        try:
            client.char_page(auth, "E_CharacterGachaPoolType_Standard", None)
        except (AuthExpired, ApiError) as e:
            reasons.append(getattr(e, "reason", None) or f"取记录 {_one_line(e)}")
            error = AuthExpired(_expired_text(*reasons))
            error.reasons = reasons
            raise error from e
        return auth

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
        if auth.note:
            warnings.append(auth.note)
        if not auth.uid_known:
            warnings.append(
                "官方没有告诉软件这个账号的 UID，记录先放在「账号 0」下。"
                "等能取到 UID 时，软件会自动把它们并进真正的账号。"
            )

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
        if auth.uid_known and UNKNOWN_UID in store.uids():
            moved = store.absorb(UNKNOWN_UID, auth.uid)
            if moved:
                warnings.append(f"之前暂存在「账号 0」下的 {moved} 条记录，已并入这个账号。")
        return {"uid": auth.uid, "new": new, "total_new": sum(new.values()), "warnings": warnings}

    def diagnose(self, game_dir: str) -> list:
        by_token = "账号令牌方式：不读本机文件，在「高级」里粘贴账号令牌即可（这里只检查本机，不联网）"
        try:
            log = find_log(game_dir or None)
        except LocateError as e:
            return [f"游戏日志：没找到（{str(e).splitlines()[0]}）", by_token]
        info = log.stat()
        when = datetime.fromtimestamp(info.st_mtime).strftime("%Y-%m-%d %H:%M")
        lines = [f"游戏日志：{mask_path(log)}（{info.st_size / 1024:.0f} KB，最后写入 {when}）"]
        lines.append(by_token)
        try:
            blob = read_file_shared(log)
        except OSError as e:
            return lines + [f"读取日志：失败（{explain_read_failure(log, e, '日志').splitlines()[0]}）"]
        links = find_links(blob.decode("utf-8", errors="replace"))
        if not links:
            return lines + ["寻访记录链接：没找到（请先在游戏里打开一次「寻访」→「寻访记录」）"]
        lines.append(f"寻访记录链接：找到 {len(links)} 条不同的；最新一条 {describe_url(links[0])}")
        try:
            variants, server_id = parse_link_all(links[0])
        except InvalidUrl as e:
            return lines + [f"最新一条链接：解析失败（{e}）"]
        raw = _TOKEN_RE.search(_clean_link(links[0])).group(1)
        lines.append(
            f"最新一条的令牌：长 {len(raw)}；含 +号 {'是' if '+' in raw else '否'}、"
            f"%转义 {'是' if '%' in raw else '否'}、=号 {'是' if '=' in raw else '否'}；写法 {len(variants)} 种；服务器 {server_id}"
        )   # 只报形状，不报内容
        return lines


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
    hint="进入「寻访」页面，打开「寻访记录」，随便点开一个卡池翻一翻。读不到、官方不认日志里的凭证时，改用「高级」里的账号令牌。",
    retention="最近 90 天", trust_record_type=False,
    manual_label="账号令牌或记录链接（日志读不到时用）", manual_help=TOKEN_HELP, manual_secret=True,
    manual_guide=TOKEN_GUIDE,
)
