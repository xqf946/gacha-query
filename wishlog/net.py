"""鹰角的两个游戏（明日方舟、终末地）用的 HTTP 小工具：统一的 JSON 请求、Cookie、重试，
以及一道安全闸门：令牌这类凭证只会发给鹰角自己的域名。"""

from __future__ import annotations

import http.cookiejar
import json
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse

from .client import ApiError, InvalidUrl, NetworkError

ALLOWED_SUFFIX = "hypergryph.com"
_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)


def host_allowed(url: str) -> bool:
    u = urlparse(url)
    host = u.hostname or ""
    return u.scheme == "https" and (host == ALLOWED_SUFFIX or host.endswith("." + ALLOWED_SUFFIX))


class UrllibTransport:
    """真正发请求的那一层。一个实例一个 Cookie 罐，所以一次更新用一个新实例，用完就丢。

    调用方式：transport(method, url, headers, json_body) -> 已解析的 JSON（dict 或 list）。
    """

    def __init__(self, sleep=time.sleep):
        self._sleep = sleep
        self._opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar())
        )

    def __call__(self, method: str, url: str, headers: dict, body):
        data = json.dumps(body).encode("utf-8") if body is not None else None
        merged = {"User-Agent": _USER_AGENT, "Accept": "application/json", **headers}
        if data is not None:
            merged["Content-Type"] = "application/json;charset=utf-8"
        request = urllib.request.Request(url, data=data, method=method, headers=merged)

        last: Exception | None = None
        for attempt in range(3):
            try:
                with self._opener.open(request, timeout=15) as resp:
                    raw = resp.read()
                break
            except urllib.error.HTTPError as e:
                raw = e.read()           # 有的鉴权失败是 4xx，但响应体里有可读的原因
                break
            except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
                last = e
                self._sleep(1 + attempt)
        else:
            raise NetworkError(f"连不上官方接口，请检查网络（{last}）。")

        text = raw.decode("utf-8", errors="replace").strip()
        try:
            return json.loads(text)
        except ValueError:
            if text[:1] == "<":
                raise ApiError("官方接口返回了一个网页而不是数据，接口可能已经改版。") from None
            raise ApiError("官方接口返回了无法解析的内容。") from None


class SafeTransport:
    """包一层：任何发往非鹰角域名的请求一律拒绝。令牌在这里被拦住，不会发到别处。"""

    def __init__(self, inner):
        self._inner = inner

    def __call__(self, method: str, url: str, headers: dict, body):
        if not host_allowed(url):
            raise InvalidUrl(f"拒绝向非官方域名发送请求：{urlparse(url).hostname}")
        return self._inner(method, url, headers, body)


def default_transport() -> SafeTransport:
    return SafeTransport(UrllibTransport())


_CHINA_TIME = timezone(timedelta(hours=8))


def format_ts(value) -> str:
    """接口里的时间戳是数字字符串：13 位是毫秒，10 位是秒。统一显示成北京时间，和别的游戏一致。"""
    raw = str(value).strip()
    if not raw.isdigit():
        raise ApiError(f"记录里的时间认不出来：{raw!r}")
    millis = int(raw) * 1000 if len(raw) <= 10 else int(raw)
    return datetime.fromtimestamp(millis / 1000, tz=_CHINA_TIME).strftime("%Y-%m-%d %H:%M:%S")
