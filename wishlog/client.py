"""官方祈愿记录接口的客户端。"""

from __future__ import annotations

import json
import re
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from urllib.parse import parse_qsl, urlencode, urlparse

API_HOST = "https://public-operation-hk4e.mihoyo.com"
API_PATH = "/gacha_info/api/getGachaLog"
ALLOWED_HOST_SUFFIX = "mihoyo.com"   # authkey 只会发给这个域名下的服务器
GAME_BIZ = "hk4e_cn"                 # 国服（官服 / B服）
PAGE_SIZE = 20                       # 接口单页最多 20 条
MAX_PROBES = 6                       # 缓存里有多条链接时，最多试几条
_PAGING_KEYS = {"page", "size", "gacha_type", "end_id"}
_AUTHKEY_OK = re.compile(r"^[A-Za-z0-9%+/=_.~-]+$")
_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)


class WishError(Exception):
    """所有会直接显示给用户的错误的基类。"""


class InvalidUrl(WishError):
    pass


class AuthExpired(WishError):
    pass


class ApiError(WishError):
    pass


class NetworkError(WishError):
    pass


@dataclass(frozen=True)
class Auth:
    """从祈愿链接里取出的、请求接口所需的固定参数（不含翻页参数）。"""

    params: tuple  # ((key, value), ...)，保持原顺序

    @property
    def authkey(self) -> str:
        return dict(self.params)["authkey"]


def parse_wish_url(url: str) -> Auth:
    u = urlparse(url.strip().strip('"'))
    host = u.hostname or ""
    if u.scheme != "https" or not (host == ALLOWED_HOST_SUFFIX or host.endswith("." + ALLOWED_HOST_SUFFIX)):
        raise InvalidUrl("这不是国服的祈愿链接（域名应该是 mihoyo.com）。")

    params: dict[str, str] = {}
    for key, value in parse_qsl(u.query):
        params.setdefault(key, value)
    if params.get("game_biz") != GAME_BIZ:
        raise InvalidUrl("这不是原神国服的祈愿链接（game_biz 不是 hk4e_cn）。")
    if not params.get("region"):
        raise InvalidUrl("链接里缺少 region 参数，请重新在游戏里打开一次历史记录页面。")
    authkey = params.get("authkey", "")
    if not authkey or not _AUTHKEY_OK.match(authkey):
        raise InvalidUrl("链接里的 authkey 缺失或不完整。")

    for key in _PAGING_KEYS:
        params.pop(key, None)
    params.setdefault("auth_appid", "webview_gacha")
    params.setdefault("authkey_ver", "1")
    params.setdefault("sign_type", "2")
    params.setdefault("lang", "zh-cn")
    return Auth(tuple(params.items()))


def _http_get_json(url: str) -> dict:
    request = urllib.request.Request(
        url, headers={"User-Agent": _USER_AGENT, "Accept": "application/json"}
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


class Client:
    def __init__(self, fetch=_http_get_json, sleep=time.sleep, api_host: str = API_HOST):
        self._fetch = fetch
        self._sleep = sleep
        self._api = api_host + API_PATH
        self._calls = 0

    def _pace(self) -> None:
        # 请求太密接口会返回“访问过于频繁”，所以每页之间歇一下。
        if self._calls:
            self._sleep(1.0 if self._calls % 10 == 0 else 0.3)
        self._calls += 1

    def fetch_page(self, auth: Auth, gacha_type: str, page: int, end_id: str = "0") -> list:
        query = dict(auth.params)
        query.update(gacha_type=gacha_type, page=page, size=PAGE_SIZE, end_id=end_id)
        url = f"{self._api}?{urlencode(query)}"

        for attempt in range(1, 6):
            self._pace()
            data = self._fetch(url)
            code, message = data.get("retcode"), data.get("message", "")
            if code == 0:
                return (data.get("data") or {}).get("list") or []
            if code in (-100, -101):
                raise AuthExpired(
                    "祈愿链接已经失效（有效期约 24 小时）。请在游戏里重新打开一次"
                    "“祈愿 → 历史记录”，再回来更新。"
                )
            if code == -110:  # 访问过于频繁：等一会儿再试
                self._sleep(3 * attempt)
                continue
            raise ApiError(f"官方接口返回错误：{message}（retcode {code}）")
        raise ApiError("官方接口提示访问过于频繁，请稍后再试。")

    def pick_valid_auth(self, urls: list) -> Auth:
        """缓存里可能留着好几条链接，从最新的开始试，返回第一条还有效的。"""
        invalid: WishError | None = None
        tried = 0
        for url in urls:
            try:
                auth = parse_wish_url(url)
            except InvalidUrl as e:
                invalid = e
                continue
            if tried >= MAX_PROBES:
                break
            tried += 1
            try:
                self.fetch_page(auth, "301", 1)
                return auth
            except AuthExpired as e:
                invalid = e
        raise invalid or AuthExpired("缓存里没有可用的祈愿链接。")
