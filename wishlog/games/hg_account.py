"""鹰角账号这一侧的接口，明日方舟和终末地共用。

链条：账号令牌 → 授权令牌（grant）→ 这个账号绑定的游戏角色（binding_list）→ 角色令牌（u8_token_by_uid）。
账号令牌是用户自己在浏览器里登录官网之后复制来的；软件**不做登录、不接触手机号、密码和验证码**，
令牌只在内存里用这一次，不写进硬盘、不写日志，并且只会发给鹰角自己的域名（见 net.SafeTransport）。

接口链条参考了开源工具 AceDroidX/arknights-gacha-export 的 API.md 和 bhaoo/endfield-gacha，
这里是用 Python 按本项目的结构重新实现的。
"""

from __future__ import annotations

import json
import re
import time
from urllib.parse import urlencode

from ..client import ApiError, AuthExpired, InvalidUrl
from ..net import default_transport

AS_HOST = "https://as.hypergryph.com"
BINDING_HOST = "https://binding-api-account-prod.hypergryph.com"
APP_CODE = "be36d44aa36bfb5b"            # 鹰角账号授权用的应用码；森空岛的授权码换不到寻访记录
_TOKEN_RE = re.compile(r"^[A-Za-z0-9+/=_.\-%]{16,}$")
_JSON_TOKEN_RE = re.compile(r'"(?:content|token)"\s*:\s*"([^"]+)"')


def parse_account_token(text: str) -> str:
    """接受官网页面上的整段内容（JSON），或者单独的令牌。"""
    raw = text.strip()
    token = None
    if raw[:1] in "{[":
        try:
            token = _find_token(json.loads(raw))
        except ValueError:
            pass
        if token is None:
            m = _JSON_TOKEN_RE.search(raw)    # 复制得不完整、不是合法 JSON 时，退一步用正则找
            token = m.group(1) if m else None
    else:
        token = raw.strip("\"' \r\n")
    if not token or not _TOKEN_RE.match(token):
        raise InvalidUrl("没能从你粘贴的内容里认出账号令牌。请照说明重新复制官网页面上显示的整段文字。")
    return token


def _find_token(node):
    if isinstance(node, dict):
        for key in ("content", "token"):
            if isinstance(node.get(key), str) and node[key]:
                return node[key]
        for value in node.values():
            found = _find_token(value)
            if found:
                return found
    return None


class HgAccount:
    def __init__(self, transport=None, sleep=time.sleep):
        self._transport = transport or default_transport()
        self._sleep = sleep
        self._account_token = ""       # 只在内存里，仅用于个别接口要求时作为备用请求头

    # 账号这一侧的接口都返回 {"status": 0, "data": ...}
    def _as(self, method: str, url: str, body=None) -> dict:
        data = self._transport(method, url, {}, body)
        if not isinstance(data, dict) or data.get("status") != 0:
            message = data.get("msg") if isinstance(data, dict) else ""
            raise AuthExpired(f"账号令牌无效或已经过期（{message or '无详细信息'}）。请重新登录官网，再复制一遍页面上的内容。")
        return data.get("data") or {}

    def grant(self, account_token: str) -> str:
        self._account_token = account_token
        data = self._as("POST", f"{AS_HOST}/user/oauth2/v2/grant",
                        {"token": account_token, "appCode": APP_CODE, "type": 1})
        token = data.get("token")
        if not token:
            raise ApiError("账号授权没有返回令牌，接口可能已经改版。")
        return token

    def bindings(self, oauth: str, app_code: str = "arknights") -> list:
        """这个账号绑定的某个游戏的角色。有角色详情（roles）的话一并带上。"""
        data = self._as("GET", f"{BINDING_HOST}/account/binding/v1/binding_list?"
                        + urlencode({"token": oauth, "appCode": app_code}))
        found = []
        for app in data.get("list") or []:
            if app.get("appCode") != app_code:
                continue
            for b in app.get("bindingList") or []:
                uid = str(b.get("uid") or "").strip()
                if uid.isdigit():
                    entry = {"uid": uid, "channel": str(b.get("channelName") or ""),
                             "nickname": str(b.get("nickName") or "")}
                    roles = [r for r in (b.get("roles") or []) if isinstance(r, dict)]
                    if roles:
                        entry["roles"] = roles
                    found.append(entry)
        return found

    def u8_token(self, oauth: str, uid: str) -> str:
        token = self._as("POST", f"{BINDING_HOST}/account/binding/v1/u8_token_by_uid",
                         {"token": oauth, "uid": uid}).get("token")
        if not token:
            raise ApiError("没能换到角色令牌，接口可能已经改版。")
        return token
