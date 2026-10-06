"""记录保存在哪里。"""

from __future__ import annotations

import os
import sys
from pathlib import Path

APP_DIR_NAME = "GenshinWishLog"


def default_data_dir() -> Path:
    """打包安装后的软件把记录放在系统规定的用户数据目录（Windows 是 %LOCALAPPDATA%）。

    不能放在程序自己的目录里：安装在 Program Files 时没有写入权限，
    而且升级、卸载时程序目录会被整个替换掉，记录就丢了。
    从源码运行（开发时）仍然放在项目目录下的 data 里。
    """
    if getattr(sys, "frozen", False):
        if sys.platform == "win32":
            base = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local")
        elif sys.platform == "darwin":
            base = Path.home() / "Library" / "Application Support"
        else:
            base = Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share")
        return base / APP_DIR_NAME / "data"
    return Path(__file__).resolve().parent.parent / "data"
