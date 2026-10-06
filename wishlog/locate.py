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
    "miHoYo Launcher/games/Genshin Impact Game",   # 实际用户的安装位置（来自真机截图）
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


def read_file_shared(path) -> bytes:
    """读整个文件，并允许别的进程同时读、写、删除它。

    游戏开着的时候，缓存文件正被它打开着。Python 自带的 open() 在 Windows 上不允许
    “别的进程同时删除或改名这个文件”，只要游戏那边的句柄带了删除权限，open() 就会
    被拒绝（报 Permission denied）。这里直接调用 Windows 的 CreateFile，把三种共享
    方式都打开，行为和 Node.js 等工具一致。其他系统没有这个问题，用普通读取。
    """
    path = Path(path)
    if os.name != "nt":
        return path.read_bytes()
    return _read_shared_windows(str(path))


def _read_shared_windows(path: str) -> bytes:
    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateFileW.argtypes = [
        wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID,
        wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE,
    ]
    kernel32.CreateFileW.restype = wintypes.HANDLE
    kernel32.ReadFile.argtypes = [
        wintypes.HANDLE, wintypes.LPVOID, wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD), wintypes.LPVOID,
    ]
    kernel32.ReadFile.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL

    generic_read, share_read_write_delete, open_existing, attribute_normal = 0x80000000, 0x7, 3, 0x80
    handle = kernel32.CreateFileW(
        path, generic_read, share_read_write_delete, None, open_existing, attribute_normal, None
    )
    if handle is None or handle == ctypes.c_void_p(-1).value:  # INVALID_HANDLE_VALUE
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        chunks = []
        buffer = ctypes.create_string_buffer(1 << 20)
        count = wintypes.DWORD(0)
        while True:
            if not kernel32.ReadFile(handle, buffer, len(buffer), ctypes.byref(count), None):
                raise ctypes.WinError(ctypes.get_last_error())
            if count.value == 0:
                return b"".join(chunks)
            chunks.append(buffer.raw[: count.value])
    finally:
        kernel32.CloseHandle(handle)


def _explain_read_failure(path: Path, error: OSError) -> str:
    code = getattr(error, "winerror", None)
    detail = f"（Windows 错误码 {code}）" if code else f"（{error}）"
    if code in (32, 33):   # 共享冲突 / 锁定冲突：别的程序独占了这个文件
        hint = "文件正被游戏独占使用。请先完全退出游戏，再点一次「更新记录」。"
    elif code == 5 or isinstance(error, PermissionError):
        hint = ("没有权限读取这个文件。如果游戏是以管理员身份运行的，本软件也需要："
                "右键软件图标 → 以管理员身份运行。")
    else:
        hint = "可以先退出游戏再试一次。"
    return f"读取游戏缓存文件失败{detail}：\n{path}\n{hint}"


def find_wish_urls(data_dir: Path) -> list[str]:
    """返回缓存里的祈愿链接，最新的在前，同一个 authkey 只保留一条。"""
    cache = find_cache_file(data_dir)
    try:
        blob = read_file_shared(cache)
    except OSError as e:
        raise LocateError(_explain_read_failure(cache, e)) from e

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
