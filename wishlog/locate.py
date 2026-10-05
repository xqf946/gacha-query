"""在本机找到游戏目录和缓存文件，并从缓存里取出祈愿链接。

原理：游戏每次打开“祈愿 → 历史记录”页面，内置浏览器都会把页面请求的地址
（里面带有 authkey）写进 webCaches 下的 Chromium 缓存文件 data_2。
这里只读这个文件，不修改游戏的任何内容，也不监听网络流量。
"""

from __future__ import annotations

import os
import re
import string
from pathlib import Path

DATA_DIR_NAME = "YuanShen_Data"  # 国服（官服 / B服）的游戏数据目录名

# 游戏每次启动都会把日志覆盖写一遍，里面有游戏的安装路径。
LOG_RELATIVE = ("AppData", "LocalLow", "miHoYo", "原神", "output_log.txt")

# 日志里找不到时，在各个盘符下试这些常见的安装位置。
DEFAULT_ROOTS = (
    "Program Files/Genshin Impact/Genshin Impact Game",
    "Genshin Impact/Genshin Impact Game",
    "Program Files/HoYoPlay/games/Genshin Impact game",
    "HoYoPlay/games/Genshin Impact game",
    "Games/Genshin Impact/Genshin Impact Game",
)

_LOG_PATH_RE = re.compile(r"[A-Za-z]:[/\\][^\r\n\"]*?" + DATA_DIR_NAME)
_URL_RE = re.compile(rb"https://[\x21-\x7e]+")
_AUTHKEY_RE = re.compile(r"authkey=([^&#]+)")


class LocateError(Exception):
    """找不到游戏目录、缓存或链接。消息会直接显示给用户。"""


def data_dirs_from_log_text(text: str) -> list[str]:
    """从日志文本里取出游戏数据目录（去重、保持出现顺序）。"""
    seen: list[str] = []
    for m in _LOG_PATH_RE.finditer(text):
        if m.group() not in seen:
            seen.append(m.group())
    return seen


def resolve_data_dir(path) -> Path | None:
    """接受 YuanShen_Data 本身、它的上一级（Genshin Impact Game），或再往上一级。"""
    p = Path(str(path).strip().strip('"'))
    if p.name == DATA_DIR_NAME and p.is_dir():
        return p
    candidates = [p / DATA_DIR_NAME, *sorted(p.glob(f"*/{DATA_DIR_NAME}"))]
    return next((c for c in candidates if c.is_dir()), None)


def _default_log_paths() -> list[Path]:
    home = Path(os.environ.get("USERPROFILE") or Path.home())
    return [home.joinpath(*LOG_RELATIVE)]


def _default_drive_roots() -> list[Path]:
    if os.name != "nt":
        return []
    drives = (Path(f"{c}:/") for c in string.ascii_uppercase)
    return [d for d in drives if d.exists()]


def find_data_dir(explicit=None, log_paths=None, drive_roots=None) -> Path:
    """按“手动指定 → 游戏日志 → 常见安装位置”的顺序查找 YuanShen_Data 目录。"""
    if explicit:
        found = resolve_data_dir(explicit)
        if found:
            return found
        raise LocateError(
            f"在你填的目录里没有找到 {DATA_DIR_NAME}：{explicit}\n"
            "请填游戏安装目录（里面有 YuanShen.exe 的那个文件夹）。"
        )

    for log in log_paths if log_paths is not None else _default_log_paths():
        try:
            text = Path(log).read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        for candidate in data_dirs_from_log_text(text):
            if Path(candidate).is_dir():
                return Path(candidate)

    for root in drive_roots if drive_roots is not None else _default_drive_roots():
        for rel in DEFAULT_ROOTS:
            candidate = Path(root) / rel / DATA_DIR_NAME
            if candidate.is_dir():
                return candidate

    raise LocateError(
        "没能自动找到游戏目录。请先启动一次游戏，或者在“高级”里手动填写游戏安装目录。"
    )


def find_cache_file(data_dir: Path) -> Path:
    """返回最近被写过的那个 data_2（游戏更新后 webCaches 下会多出一个版本号目录）。"""
    web = Path(data_dir) / "webCaches"
    found = [*web.glob("*/Cache/Cache_Data/data_2"), web / "Cache/Cache_Data/data_2"]
    found = [f for f in found if f.is_file()]
    if not found:
        raise LocateError(
            "没有找到游戏的网页缓存。请先在游戏里打开一次“祈愿 → 历史记录”页面，再回来更新。"
        )
    return max(found, key=lambda f: f.stat().st_mtime)


def extract_wish_urls(blob: bytes) -> list[str]:
    """从缓存文件的原始字节里取出所有带 authkey 的祈愿链接，按文件里的先后顺序。"""
    urls: list[str] = []
    for m in _URL_RE.finditer(blob):
        url = m.group().decode("ascii")
        if "authkey=" in url and "webview_gacha" in url and url not in urls:
            urls.append(url)
    return urls


def find_wish_urls(data_dir: Path) -> list[str]:
    """返回缓存里的祈愿链接，最新的在前，同一个 authkey 只保留一条。"""
    cache = find_cache_file(data_dir)
    try:
        blob = cache.read_bytes()
    except OSError as e:
        raise LocateError(f"读取缓存文件失败（{e}）。可以先关闭游戏再试一次。") from e

    result: list[str] = []
    keys: set[str] = set()
    for url in reversed(extract_wish_urls(blob)):
        m = _AUTHKEY_RE.search(url)
        if m and m.group(1) not in keys:
            keys.add(m.group(1))
            result.append(url)
    if not result:
        raise LocateError(
            "缓存里没有祈愿链接。请先在游戏里打开“祈愿 → 历史记录”页面，再回来更新。"
        )
    return result
