"""鸣潮（库洛游戏）。和米哈游的游戏机制不同：

1. 记录页链接在游戏目录的 Client\\Saved\\Logs\\Client.log 里，而且这个日志是加密的，要先解码。
2. 取记录用 POST 请求，参数放在请求体里，一次返回一个卡池的全部记录。
3. 返回的记录没有唯一编号，所以要自己编号才能去重。

解码方式、链接格式、请求体字段、13 个卡池的编号，参考了开源项目
juliy819/wuwa-gagha-tool（Apache-2.0，https://github.com/juliy819/wuwa-gagha-tool）。
这里是用 Python 按本项目的结构重新实现的，没有搬用它的代码。
"""

from __future__ import annotations

import calendar
import json
import re
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from ..client import ApiError, AuthExpired, InvalidUrl, NetworkError
from ..locate import (
    GameNotFound, LocateError, default_drive_roots, describe_url, explain_read_failure,
    mask_path, read_file_shared,
)
from .base import STARS, Game, Pool, Standard

API_CN = "https://gmserver-api.aki-game2.com/gacha/record/query"
API_GLOBAL = "https://gmserver-api.aki-game2.net/gacha/record/query"
_USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"

# ---------- 解码日志 ----------
# 日志文件开头 3 个字节是 BOM，之后每个字节：奇数异或 0xA5，偶数异或 0xEF。
_DECODE_TABLE = bytes((b ^ 0xA5) if b % 2 else (b ^ 0xEF) for b in range(256))


def decode_client_log(blob: bytes) -> str:
    return blob[3:].translate(_DECODE_TABLE).decode("utf-8", errors="replace")


_LINE_RE = re.compile(r'OpenWebView.*?sdkJson.*?"url":"([^"]+)"')
_TIME_RE = re.compile(r"\[(\d{4}\.\d{2}\.\d{2}-\d{2}\.\d{2}\.\d{2}:\d{3})\]")
_LOOSE_RE = re.compile(r"""https[^\s"']*/aki/gacha/index\.html#/record[^\s"']*""")
_HOST_RE = re.compile(r"^aki-gm-resources(-oversea)?\.aki-game\.(com|net)$")


def is_record_url(raw: str) -> bool:
    """必须是 https、库洛的域名、抽卡记录页。日志里还会有同域名的公告页链接，要排除。"""
    u = urlparse(raw)
    return (
        u.scheme == "https"
        and bool(u.hostname and _HOST_RE.match(u.hostname))
        and u.path == "/aki/gacha/index.html"
        and (u.fragment == "/record" or u.fragment.startswith("/record?"))
    )


def extract_record_url(text: str) -> str | None:
    """从解码后的日志里取出最新的一条记录页链接。"""
    best_url = best_time = None
    for line in text.splitlines():
        if "OpenWebView" not in line or "sdkJson" not in line:
            continue
        m = _LINE_RE.search(line)
        if not m:
            continue
        url = m.group(1).replace("\\u0026", "&")
        if not is_record_url(url):
            continue
        stamp = _TIME_RE.search(line)
        stamp = stamp.group(1) if stamp else None
        if best_time is None or (stamp or "") > best_time:
            best_url, best_time = url, stamp or ""
    if best_url:
        return best_url
    for m in _LOOSE_RE.finditer(text):   # 退一步：用宽松的写法找
        url = m.group(0).replace("\\u0026", "&")
        if is_record_url(url):
            best_url = url
    return best_url


def find_url_in_log(blob: bytes) -> str | None:
    """先按加密日志解码来找；找不到再当作普通文本找（以防以后游戏不再加密）。"""
    return extract_record_url(decode_client_log(blob)) or extract_record_url(
        blob.decode("utf-8", errors="replace")
    )


# ---------- 链接参数 ----------
@dataclass(frozen=True)
class WuwaAuth:
    player_id: str
    record_id: str
    resources_id: str
    server_id: str
    lang: str
    oversea: bool


def parse_record_url(url: str) -> WuwaAuth:
    url = url.strip().strip('"')
    if not is_record_url(url):
        raise InvalidUrl("这不是鸣潮的唤取记录链接（应该来自 aki-gm-resources…aki-game.com 的“唤取记录”页面）。")
    u = urlparse(url)
    values: dict[str, str] = {}
    # 参数通常在 # 后面；有的写法在 # 前面，两处都看
    for query in (u.fragment.split("?", 1)[1] if "?" in u.fragment else "", u.query):
        for key, vals in parse_qs(query).items():
            values.setdefault(key, vals[0])
    missing = [k for k in ("player_id", "record_id", "resources_id", "svr_id") if not values.get(k)]
    if missing:
        raise InvalidUrl(f"链接里缺少参数：{', '.join(missing)}。请重新在游戏里打开一次“唤取记录”。")
    return WuwaAuth(
        player_id=values["player_id"], record_id=values["record_id"],
        resources_id=values["resources_id"], server_id=values["svr_id"],
        lang=values.get("lang") or "zh-Hans",
        oversea=(u.hostname or "").endswith(".aki-game.net"),
    )


# ---------- 找日志 ----------
_CLIENT_LOG = "Client/Saved/Logs/Client.log"
_GAME_DIR = "Wuthering Waves Game"
_SEARCH_BASES = (
    "", "Program Files", "Program Files (x86)", "Games", "Game", "Kuro", "KuroGames",
    "Steam", "SteamLibrary", "Program Files (x86)/Steam", "Program Files/Steam",
)
_SEARCH_PATTERNS = (
    f"Wuthering Waves*/{_GAME_DIR}/{_CLIENT_LOG}",
    f"Wuthering Waves*/{_CLIENT_LOG}",
    f"steamapps/common/Wuthering Waves*/{_GAME_DIR}/{_CLIENT_LOG}",
    f"*/Wuthering Waves*/{_GAME_DIR}/{_CLIENT_LOG}",
    f"*/steamapps/common/Wuthering Waves*/{_GAME_DIR}/{_CLIENT_LOG}",
)


def resolve_client_log(path) -> Path | None:
    """接受 Client.log 文件本身，或它上面任意一级的文件夹（日志文件夹、游戏文件夹、启动器文件夹）。"""
    p = Path(str(path).strip().strip('"'))
    if p.is_file():
        return p if p.name.lower() == "client.log" else None
    if not p.is_dir():
        return None
    direct = [p / "Client.log", p / "Saved/Logs/Client.log", p / _CLIENT_LOG, p / _GAME_DIR / _CLIENT_LOG]
    nested = [*sorted(p.glob(f"*/{_CLIENT_LOG}")), *sorted(p.glob(f"*/*/{_CLIENT_LOG}"))]
    return next((c for c in [*direct, *nested] if c.is_file()), None)


def find_client_log(explicit=None, drive_roots=None) -> Path:
    if explicit:
        found = resolve_client_log(explicit)
        if found:
            return found
        raise LocateError(
            f"在你选的位置里没有找到鸣潮的 Client.log：{explicit}\n"
            "请选择游戏安装目录（里面有 Wuthering Waves Game 文件夹的那个文件夹）。"
        )
    found_logs: list[Path] = []
    for root in drive_roots if drive_roots is not None else default_drive_roots():
        for base in _SEARCH_BASES:
            folder = Path(root) / base if base else Path(root)
            for pattern in _SEARCH_PATTERNS:
                found_logs.extend(p for p in folder.glob(pattern) if p.is_file())
    if not found_logs:
        raise GameNotFound(
            "没能自动找到鸣潮。如果已经安装，请在“高级”里手动选择游戏安装目录"
            "（里面有 Wuthering Waves Game 文件夹的那个文件夹）。"
        )
    return max(found_logs, key=lambda p: p.stat().st_mtime)


# ---------- 请求 ----------
def _post_json(url: str, body: dict) -> dict:
    request = urllib.request.Request(
        url, data=json.dumps(body).encode("utf-8"),
        headers={"User-Agent": _USER_AGENT, "Content-Type": "application/json", "Accept": "application/json"},
    )
    last: Exception | None = None
    for attempt in range(3):
        try:
            with urllib.request.urlopen(request, timeout=15) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except ValueError as e:
            raise ApiError("接口返回了无法解析的内容。") from e
        except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
            last = e
            time.sleep(1 + attempt)
    raise NetworkError(f"连不上官方接口，请检查网络（{last}）。")


class WuwaClient:
    def __init__(self, post=_post_json, sleep=time.sleep):
        self._post = post
        self._sleep = sleep
        self._calls = 0
        self._int_type = False   # 卡池编号在请求体里是用字符串还是数字，哪种行就记住哪种

    def query_pool(self, auth: WuwaAuth, pool_key: str) -> list:
        """返回这个卡池的全部记录（接口是从新到旧）。"""
        url = API_GLOBAL if auth.oversea else API_CN
        if self._calls:
            self._sleep(0.3)
        self._calls += 1

        def body(as_int: bool) -> dict:
            return {
                "playerId": auth.player_id, "recordId": auth.record_id,
                "cardPoolId": auth.resources_id, "serverId": auth.server_id,
                "languageCode": auth.lang,
                "cardPoolType": int(pool_key) if as_int else str(pool_key),
            }

        data = self._post(url, body(self._int_type))
        if data.get("code") != 0:
            # 开源工具里有的用字符串、有的用数字，换另一种写法再试一次
            retry = self._post(url, body(not self._int_type))
            if retry.get("code") == 0:
                self._int_type = not self._int_type
                data = retry
        code = data.get("code")
        if code == 0:
            return data.get("data") or []
        if code == -1:
            raise AuthExpired(
                "唤取记录链接已失效或被服务器拒绝（错误码 -1）。请在游戏里重新打开一次"
                "「唤取」→「唤取记录」，再回来更新。"
            )
        raise ApiError(f"官方接口返回错误：{data.get('message', '')}（错误码 {code}）")


# ---------- 编号去重 ----------
def build_records(items: list, pool_key: str) -> list:
    """把接口返回的记录（从新到旧）整理成本地格式（从旧到新），并给每条编号。

    接口返回的记录没有唯一编号，而且十连会有好几条记录在同一秒、甚至物品也相同。
    所以编号 = 时间 + “同一秒内的第几条” + 卡池。同一批记录再次抓到时编号一致，就能去重；
    如果直接用“账号+物品+时间”去重，同一秒的重复记录会被误删。
    """
    seen: dict = {}
    out = []
    for item in reversed(items):
        stamp = item["time"]
        try:
            epoch = calendar.timegm(time.strptime(stamp, "%Y-%m-%d %H:%M:%S"))
        except ValueError as e:
            raise ApiError(f"记录里的时间格式认不出来：{stamp}") from e
        order = seen.get(stamp, 0)
        seen[stamp] = order + 1
        out.append({
            "id": f"{epoch}{order:02d}{int(pool_key):02d}",
            "gacha_type": str(pool_key),
            "item_id": str(item.get("resourceId", "")),
            "count": str(item.get("count", 1)),
            "time": stamp,
            "name": item["name"],
            "item_type": item.get("resourceType", ""),
            "rank_type": str(item["qualityLevel"]),
        })
    return out


# ---------- 游戏 ----------
class WuwaGame(Game):
    def make_client(self) -> WuwaClient:
        return WuwaClient()

    def connect(self, client, url: str, game_dir: str) -> WuwaAuth:
        if url.strip():
            auth = parse_record_url(url)
        else:
            log = find_client_log(game_dir or None)
            try:
                blob = read_file_shared(log)
            except OSError as e:
                raise LocateError(explain_read_failure(log, e, "游戏日志文件")) from e
            found = find_url_in_log(blob)
            if not found:
                raise LocateError(
                    "日志里没有找到唤取记录链接。请先在游戏里打开一次「唤取」→「唤取记录」，再回来更新。"
                )
            auth = parse_record_url(found)
        client.query_pool(auth, self.pools[0].key)   # 先验证链接有没有失效
        return auth

    def sync(self, client, auth: WuwaAuth, store, progress=None) -> dict:
        uid = auth.player_id
        new: dict = {}
        warnings: list = []
        first_error: ApiError | None = None
        succeeded = 0
        for pool in self.pools:
            try:
                items = client.query_pool(auth, pool.key)
            except ApiError as e:
                new[pool.name] = 0
                first_error = first_error or e
                if not pool.optional:
                    warnings.append(f"「{pool.name}」获取失败：{e}")
                continue
            succeeded += 1
            if progress:
                progress(pool.name, len(items), sum(new.values()))
            new[pool.name] = store.merge(uid, build_records(items, pool.key)) if items else 0
        if not succeeded:
            raise first_error or ApiError("没有取到任何卡池的记录。")
        return {"uid": uid, "new": new, "total_new": sum(new.values()), "warnings": warnings}

    def diagnose(self, game_dir: str) -> list:
        try:
            log = find_client_log(game_dir or None)
        except LocateError as e:
            return [f"游戏日志：没找到（{str(e).splitlines()[0]}）"]
        info = log.stat()
        when = datetime.fromtimestamp(info.st_mtime).strftime("%Y-%m-%d %H:%M")
        lines = [f"游戏日志：{mask_path(log)}（{info.st_size / 1024 / 1024:.1f} MB，最后写入 {when}）"]
        try:
            blob = read_file_shared(log)
        except OSError as e:
            return lines + [f"读取日志：失败（{str(explain_read_failure(log, e, '日志')).splitlines()[0]}）"]
        decoded = decode_client_log(blob)
        encrypted_hits = decoded.count("OpenWebView")
        plain_hits = blob.decode("utf-8", errors="replace").count("OpenWebView")
        lines.append(f"日志内容：解码后出现 {encrypted_hits} 处“OpenWebView”（按原样读取 {plain_hits} 处）")
        url = find_url_in_log(blob)
        lines.append(f"唤取记录链接：{'找到；' + describe_url(url) if url else '没找到'}")
        return lines


_LIMITED_CHARACTER_POOLS = frozenset({"1", "8", "10", "12"})

WUWA = WuwaGame(
    key="wuwa", name="鸣潮", short_name="鸣潮",
    pools=(
        Pool("1", "角色活动唤取", 80, 65),
        Pool("2", "武器活动唤取", 80, 65),
        Pool("3", "角色常驻唤取", 80, 65),
        Pool("4", "武器常驻唤取", 80, 65),
        Pool("5", "新手唤取", 50, None),
        Pool("6", "新手自选唤取", 80, None),
        Pool("7", "感恩定向唤取", None, None),
        Pool("8", "角色新旅唤取", 80, 65, optional=True),
        Pool("9", "武器新旅唤取", 80, 65, optional=True),
        Pool("10", "角色联动唤取", 80, 65, optional=True),
        Pool("11", "武器联动唤取", 80, 65, optional=True),
        Pool("12", "角色忆旅唤取", 80, 65, optional=True),
        Pool("13", "武器忆旅唤取", 80, 65, optional=True),
    ),
    ranks=STARS, currency="星声",
    hint="进入「唤取」页面，打开「唤取记录」，随便点开一个卡池翻一翻。",
    # 鸣潮最初的五个常驻五星角色。在限定角色池里出了他们，就是没抽到 UP。
    standard=Standard(frozenset({"凌阳", "安可", "卡卡罗", "鉴心", "维里奈"}), _LIMITED_CHARACTER_POOLS),
    trust_record_type=False,
)
