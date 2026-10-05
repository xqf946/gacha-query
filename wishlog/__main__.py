"""用法：python -m wishlog [--port 8765] [--game-dir 游戏目录] [--no-browser]"""

from __future__ import annotations

import argparse
import sys
import threading
import webbrowser
from pathlib import Path

from .job import SyncJob
from .server import create_server
from .store import Store


def app_root() -> Path:
    """记录保存在哪个目录旁边。

    打包成 exe 后，程序代码会被解压到一个退出就删除的临时目录，
    所以数据必须放在 exe 本身所在的目录，不能跟着 __file__ 走。
    """
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


def main() -> None:
    # 在中文 Windows 的控制台里，个别字符编码不了也不要让程序崩掉
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(errors="replace")

    root = app_root()
    parser = argparse.ArgumentParser(prog="wishlog", description="原神抽卡记录查询")
    parser.add_argument("--port", type=int, default=8765, help="网页端口（被占用时自动往后找）")
    parser.add_argument("--data-dir", default=str(root / "data"), help="记录保存目录")
    parser.add_argument("--game-dir", default="", help="游戏安装目录；一般不用填，会自动找")
    parser.add_argument("--no-browser", action="store_true", help="启动后不自动打开浏览器")
    args = parser.parse_args()

    store = Store(args.data_dir)
    server = create_server(store, SyncJob(store, default_game_dir=args.game_dir or None), args.port)
    url = f"http://127.0.0.1:{server.server_address[1]}/"

    print("原神抽卡记录查询已启动")
    print(f"  网页地址：{url}")
    print(f"  记录保存在：{Path(args.data_dir).resolve()}")
    print("  用完后关闭这个窗口（或按 Ctrl+C）即可退出。")
    if not args.no_browser:
        threading.Timer(0.5, webbrowser.open, args=(url,)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
