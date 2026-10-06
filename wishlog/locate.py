"""在本机找到游戏目录和缓存文件，并从缓存里取出祈愿链接（米哈游的游戏通用）。

原理：游戏每次打开“历史记录”页面，内置浏览器都会把页面请求的地址
（里面带有 authkey）写进 webCaches 下的 Chromium 缓存文件 data_2。
这里只读这个文件，不修改游戏的任何内容，也不监听网络流量。
"""

from __future__ import annotations

import os
import re
import string
import time
from pathlib import Path
from urllib.parse import parse_qsl, urlparse

# 游戏日志会记录安装路径。各游戏日志所在的文件夹名不同（原神叫“原神”，崩铁叫“崩坏：星穹铁道”……），
# 所以不写死，扫描 LocalLow\miHoYo 下所有子文件夹里的这两种日志，再从内容里找路径。
LOG_FILE_NAMES = ("output_log.txt", "Player.log")

# 日志里找不到时，在各个盘符下找。先试这些常见安装位置，再试盘符根目录下所有非系统文件夹；
# 每个位置下再往里找一两层，这样游戏文件夹叫什么（Genshin Impact Game / Star Rail Game ……）都无所谓。
SEARCH_BASES = (
    "Program Files",
    "Program Files (x86)",
    "Games",
    "miHoYo Launcher/games",
    "HoYoPlay/games",
    "Program Files/miHoYo Launcher/games",
    "Program Files/HoYoPlay/games",
)
# 盘符根目录下这些文件夹里不会有游戏，而且又大又慢，扫描时直接跳过。
SYSTEM_DIRS = frozenset({
    "windows", "users", "programdata", "$recycle.bin", "system volume information",
    "recovery", "perflogs", "msocache", "documents and settings", "config.msi",
})
# 自动查找最多花这么多秒。装得很满的电脑上全盘找太慢，超时就当作没找到，让用户手动选。
SCAN_BUDGET = 6.0

_URL_RE = re.compile(rb"https://[\x21-\x7e]+")
_AUTHKEY_RE = re.compile(r"authkey=([^&#]+)")


class LocateError(Exception):
    """找不到游戏目录、缓存或链接。消息会直接显示给用户。"""


class GameNotFound(LocateError):
    """没找到这个游戏的安装目录（多半是电脑上没装）。“更新全部”时会据此静默跳过。"""


class NeedsInput(LocateError):
    """这个游戏没有本地文件可读，需要用户手动输入凭证（明日方舟）。“更新全部”时会据此跳过。"""


def mask_path(path) -> str:
    """把路径里的 Windows 用户名换掉，方便用户把诊断信息发给别人。"""
    return re.sub(r"(?i)([\\/]Users[\\/])[^\\/]+", r"\1<用户>", str(path))


def describe_url(url: str) -> str:
    """只描述链接的结构（域名和参数名），不含任何参数的值，用于诊断信息。"""
    u = urlparse(url)
    query = u.query or (u.fragment.split("?", 1)[1] if "?" in u.fragment else "")
    names = sorted({k for k, _ in parse_qsl(query)})
    return f"域名 {u.hostname}，参数 {', '.join(names) or '无'}"


def data_dirs_from_log_text(text: str, data_dir_name: str) -> list[str]:
    """从日志文本里取出游戏数据目录（去重、保持出现顺序）。"""
    pattern = re.compile(r"[A-Za-z]:[/\\][^\r\n\"]*?" + re.escape(data_dir_name))
    seen: list[str] = []
    for m in pattern.finditer(text):
        if m.group() not in seen:
            seen.append(m.group())
    return seen


def resolve_data_dir(path, data_dir_name: str) -> Path | None:
    """接受数据目录本身、它的上一级（游戏文件夹），或再往上一级（启动器的 games 文件夹）。"""
    p = Path(str(path).strip().strip('"'))
    if p.name == data_dir_name and p.is_dir():
        return p
    candidates = [p / data_dir_name, *sorted(p.glob(f"*/{data_dir_name}"))]
    return next((c for c in candidates if c.is_dir()), None)


def home_dir() -> Path:
    return Path(os.environ.get("USERPROFILE") or Path.home())


def default_log_paths() -> list[Path]:
    root = home_dir() / "AppData" / "LocalLow" / "miHoYo"
    if not root.is_dir():
        return []
    return [
        log for folder in sorted(root.iterdir()) if folder.is_dir()
        for name in LOG_FILE_NAMES if (log := folder / name).is_file()
    ]


def default_drive_roots() -> list[Path]:
    if os.name != "nt":
        return []
    drives = (Path(f"{c}:/") for c in string.ascii_uppercase)
    return [d for d in drives if d.exists()]


def search_folders(root, bases=SEARCH_BASES) -> list:
    """一个盘符下值得找的文件夹：常见安装位置在前，再加盘符根目录下所有非系统文件夹。

    盘符根目录本身不在其中：在根目录下用通配符会直接钻进 Windows 这类系统目录。
    """
    root = Path(root)
    folders = [root / base for base in bases]
    try:
        tops = sorted(p for p in root.iterdir() if p.is_dir() and p.name.lower() not in SYSTEM_DIRS)
    except OSError:
        tops = []
    folders += [p for p in tops if p not in folders]
    return folders


def scan(folders, patterns, deadline: float):
    """在每个文件夹下依次按 patterns 找，超过 deadline 就停。找不到的、没权限的位置直接跳过。"""
    for folder in folders:
        for pattern in patterns:
            if time.monotonic() > deadline:
                return
            try:
                yield from folder.glob(pattern)
            except OSError:
                continue


def find_data_dir(data_dir_name: str, explicit=None, log_paths=None, drive_roots=None,
                  budget: float = SCAN_BUDGET) -> Path:
    """按“手动指定 → 游戏日志 → 常见安装位置”的顺序查找游戏数据目录。"""
    if explicit:
        found = resolve_data_dir(explicit, data_dir_name)
        if found:
            return found
        raise LocateError(
            f"在你选的目录里没有找到 {data_dir_name}：{explicit}\n"
            "请选择游戏的安装目录（里面有游戏主程序 .exe 的那个文件夹）。"
        )

    for log in log_paths if log_paths is not None else default_log_paths():
        try:
            text = Path(log).read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        for candidate in data_dirs_from_log_text(text, data_dir_name):
            if Path(candidate).is_dir():
                return Path(candidate)

    deadline = time.monotonic() + budget
    patterns = (data_dir_name, f"*/{data_dir_name}", f"*/*/{data_dir_name}")
    for root in drive_roots if drive_roots is not None else default_drive_roots():
        for candidate in scan(search_folders(root), patterns, deadline):
            if candidate.is_dir():
                return candidate

    raise GameNotFound(
        "没能自动找到这个游戏。如果已经安装，请先启动一次游戏，或者在“高级”里手动选择游戏安装目录。"
    )


def find_cache_file(data_dir: Path) -> Path:
    """返回最近被写过的那个 data_2（游戏更新后 webCaches 下会多出一个版本号目录）。"""
    web = Path(data_dir) / "webCaches"
    found = [*web.glob("*/Cache/Cache_Data/data_2"), web / "Cache/Cache_Data/data_2"]
    found = [f for f in found if f.is_file()]
    if not found:
        raise LocateError(
            "没有找到游戏的网页缓存。请先在游戏里打开一次“历史记录”页面，再回来更新。"
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


def explain_read_failure(path: Path, error: OSError, what: str = "游戏缓存文件") -> str:
    code = getattr(error, "winerror", None)
    detail = f"（Windows 错误码 {code}）" if code else f"（{error}）"
    if code in (32, 33):   # 共享冲突 / 锁定冲突：别的程序独占了这个文件
        hint = "文件正被游戏独占使用。请先完全退出游戏，再点一次「更新记录」。"
    elif code == 5 or isinstance(error, PermissionError):
        hint = ("没有权限读取这个文件。如果游戏是以管理员身份运行的，本软件也需要："
                "右键软件图标 → 以管理员身份运行。")
    else:
        hint = "可以先退出游戏再试一次。"
    return f"读取{what}失败{detail}：\n{path}\n{hint}"


def find_wish_urls(data_dir: Path, game_biz_prefix: str | None = None) -> list[str]:
    """返回缓存里的祈愿链接，最新的在前，同一个 authkey 只保留一条。

    game_biz_prefix 例如 "hkrpg"：只保留属于这个游戏的链接。
    """
    cache = find_cache_file(data_dir)
    try:
        blob = read_file_shared(cache)
    except OSError as e:
        raise LocateError(explain_read_failure(cache, e)) from e

    result: list[str] = []
    keys: set[str] = set()
    for url in reversed(extract_wish_urls(blob)):
        if game_biz_prefix and f"game_biz={game_biz_prefix}_" not in url:
            continue
        m = _AUTHKEY_RE.search(url)
        if m and m.group(1) not in keys:
            keys.add(m.group(1))
            result.append(url)
    if not result:
        raise LocateError(
            "缓存里没有祈愿链接。请先在游戏里打开“历史记录”页面，再回来更新。"
        )
    return result
