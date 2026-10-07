"""桌面窗口外壳：创建软件窗口、加载界面、处理启动失败。"""

from __future__ import annotations

import json
import sys
import tempfile
import time
import traceback
from pathlib import Path

from . import __version__
from .api import Api
from .demo import seed
from .games import GAMES
from .job import SyncJob
from .paths import default_data_dir
from .settings import Settings
from .store import migrate_legacy

TITLE = "抽卡查询"
INDEX = Path(__file__).parent / "static" / "index.html"
WEBVIEW2_URL = "https://developer.microsoft.com/microsoft-edge/webview2/"
_WEBVIEW2_CLIENT_KEY = r"Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}"


def webview2_available() -> bool:
    """Windows 的窗口内容由 Edge WebView2 运行时绘制。Win11 自带，旧的 Win10 可能没有。

    没有它的话 pywebview 会退回到古老的 IE 内核，界面会彻底错乱，所以启动前先查一下。
    """
    if sys.platform != "win32":
        return True
    import winreg

    locations = [
        (winreg.HKEY_LOCAL_MACHINE, "SOFTWARE\\WOW6432Node\\" + _WEBVIEW2_CLIENT_KEY),
        (winreg.HKEY_LOCAL_MACHINE, "SOFTWARE\\" + _WEBVIEW2_CLIENT_KEY),
        (winreg.HKEY_CURRENT_USER, "Software\\" + _WEBVIEW2_CLIENT_KEY),
    ]
    for hive, key in locations:
        try:
            with winreg.OpenKey(hive, key) as handle:
                version, _ = winreg.QueryValueEx(handle, "pv")
            if version and version != "0.0.0.0":
                return True
        except OSError:
            continue
    return False


def show_error(title: str, message: str) -> None:
    """没有控制台窗口的软件出错时，只能用对话框告诉用户。"""
    if sys.platform == "win32":
        import ctypes

        ctypes.windll.user32.MessageBoxW(None, message, title, 0x10)  # MB_ICONERROR
    else:
        print(f"{title}: {message}", file=sys.stderr)


def report_fatal() -> None:
    """记录当前异常到 error.log，并弹出对话框。"""
    text = traceback.format_exc()
    log = default_data_dir().parent / "error.log"
    try:
        log.parent.mkdir(parents=True, exist_ok=True)
        log.write_text(text, encoding="utf-8")
        where = f"\n\n详细信息已保存到：\n{log}"
    except OSError:
        where = ""
    last_line = text.strip().splitlines()[-1] if text.strip() else "未知错误"
    show_error(TITLE, f"软件启动时出错了。\n\n{last_line}{where}")


def _first(result):
    """文件对话框有的平台返回路径，有的返回只含一个路径的列表。"""
    if isinstance(result, (list, tuple)):
        return result[0] if result else None
    return result or None


class WebviewDialogs:
    """把 pywebview 的系统文件对话框包成 Api 需要的两个方法。"""

    def __init__(self, webview_module):
        self._wv = webview_module
        self.window = None  # 窗口创建之后再填，因为创建窗口时需要先有 Api

    def save_file(self, filename: str, file_types: tuple):
        result = self.window.create_file_dialog(
            self._wv.FileDialog.SAVE, save_filename=filename, file_types=file_types
        )
        return _first(result)

    def pick_folder(self):
        return _first(self.window.create_file_dialog(self._wv.FileDialog.FOLDER))


_PROBE = """(() => ({
  bridge: !!(window.pywebview && window.pywebview.api),
  games: [...document.querySelectorAll('.game')].map(t => t.textContent),
  tabs: [...document.querySelectorAll('.tab')].map(t => t.textContent),
  total: (document.querySelector('.stat .v') || {}).textContent || null,
  status: (document.querySelector('#status') || {}).textContent || ''
}))()"""


def _interaction_checks(window) -> dict:
    """在真实窗口里点几下，检查切换游戏时界面状态不会串到别的游戏上。"""

    def run(code: str):
        try:
            return window.evaluate_js(code)
        except Exception:
            return None

    def pause() -> None:
        time.sleep(0.6)

    checks = {}
    # 上一次操作留下的提示条（“更新完成，新增 N 条记录”）不能带到别的游戏的页面上
    run("setStatus('ok', '更新完成，新增 136 条记录。'); document.querySelectorAll('.game')[1].click(); 0")
    pause()
    checks["banner_cleared_when_switching_games"] = run("document.querySelector('#status').textContent === ''") is True
    # 明日方舟必须手动粘贴令牌，切到它会自动展开「高级」；切到别的游戏要自动收起，别一直开着
    run("document.querySelector('.game[data-key=arknights]').click(); 0")
    pause()
    opened = run("document.querySelector('details.adv').open") is True
    token_is_hidden = run("document.querySelector('#manual-url').type") == "password"
    run("document.querySelector('.game[data-key=genshin]').click(); 0")
    pause()
    closed = run("document.querySelector('details.adv').open") is False
    plain_again = run("document.querySelector('#manual-url').type") == "text"
    checks["advanced_panel_follows_the_game"] = bool(opened and closed)
    checks["token_box_only_hides_text_for_arknights"] = bool(token_is_hidden and plain_again)
    # 终末地读日志失败时（这里的自检环境里没有这个游戏），「高级」要自动展开，把「粘贴账号令牌」的说明亮出来；换游戏再收起
    run("document.querySelector('.game[data-key=endfield]').click(); 0")
    pause()
    before = run("document.querySelector('details.adv').open")
    run("document.querySelector('#sync').click(); 0")
    deadline = time.time() + 15
    while time.time() < deadline and run("document.querySelector('#sync').disabled") is True:
        time.sleep(0.4)
    pause()
    opened_after_failure = run("document.querySelector('details.adv').open") is True
    shows_help = "账号令牌" in (run("document.querySelector('#manual-help').textContent") or "")
    run("document.querySelector('.game[data-key=genshin]').click(); 0")
    pause()
    closed_again = run("document.querySelector('details.adv').open") is False
    checks["advanced_opens_after_a_failed_update_when_a_token_can_help"] = bool(
        before is False and opened_after_failure and shows_help and closed_again)
    # 侧栏的「外观」按钮：跟随系统 → 浅色 → 深色 → 回到跟随系统，每一步页面真的换了配色
    seen = []
    for _ in range(3):
        run("document.querySelector('#theme').click(); 0")
        pause()
        seen.append(run("[document.documentElement.getAttribute('data-theme'), getComputedStyle(document.body).backgroundColor]"))
    dark_bg = seen[1][1] if seen[1] else None
    checks["appearance_button_cycles_light_dark_auto"] = bool(
        seen[0] and seen[1] and seen[2]
        and [seen[0][0], seen[1][0], seen[2][0]] == ["light", "dark", None]
        and seen[0][1] != dark_bg
    )
    return checks


def _selftest(window, out_path: str) -> None:
    """自检：等界面通过后台接口把演示数据画出来，把结果写进文件，然后关掉窗口。

    出现卡池标签页，就同时证明了：窗口能打开、界面能加载、界面到后台的调用是通的。
    """
    result: dict = {"ok": False, "version": __version__}
    try:
        data = None
        deadline = time.time() + 40
        while time.time() < deadline:
            try:
                data = window.evaluate_js(_PROBE)
            except Exception:
                data = None
            if data and data.get("tabs"):
                break
            time.sleep(0.5)
        if data:
            result.update(data)
        result["checks"] = _interaction_checks(window) if data and data.get("tabs") else {}
        result["ok"] = bool(
            data and data.get("bridge") and data.get("tabs") and len(data.get("games") or []) == len(GAMES)
            and result["checks"] and all(result["checks"].values())
        )
    except Exception:
        result["error"] = traceback.format_exc()
    finally:
        Path(out_path).write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")
        window.destroy()


def run(data_dir=None, demo=False, selftest_out=None, debug=False) -> int:
    if not webview2_available():
        show_error(
            TITLE,
            "这台电脑缺少 Microsoft Edge WebView2 运行时，软件的界面无法显示。\n\n"
            f"请到下面的网址下载安装（选 Evergreen Bootstrapper 即可），装好后再打开本软件：\n{WEBVIEW2_URL}",
        )
        return 1

    import webview

    if demo or selftest_out:  # 演示和自检用临时目录，不碰真实记录
        data_dir = Path(tempfile.mkdtemp(prefix="wishlog-demo-"))
        seed(data_dir)
    data_dir = Path(data_dir or default_data_dir())
    migrate_legacy(data_dir)   # 旧版本把原神记录直接放在 data 下，搬进 genshin 子文件夹（留备份）

    settings = Settings(data_dir / "settings.json")
    dialogs = WebviewDialogs(webview)
    api = Api(data_dir, GAMES, SyncJob(data_dir, GAMES, settings), settings, dialogs)
    window = webview.create_window(
        TITLE, url=str(INDEX), js_api=api,
        width=1000, height=680, min_size=(820, 520),  # 留出余量：常见笔记本屏幕只有 1366×768
        background_color="#17171a", text_select=True,
    )
    dialogs.window = window

    if selftest_out:
        webview.start(_selftest, (window, selftest_out), debug=debug)
        ok = json.loads(Path(selftest_out).read_text(encoding="utf-8")).get("ok")
        return 0 if ok else 1
    webview.start(debug=debug)
    return 0
