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

    def pause(seconds: float = 0.6) -> None:
        time.sleep(seconds)

    checks = {}
    # 开屏还盖在上面的时候，保底的条要先空着（不然在开屏后面长完了，没人看到）；开屏已经走了的话，这一条不适用
    on_splash = run("(() => ({ splash: !!document.querySelector('#splash'), empty: [...document.querySelectorAll('.bar > i')].every((i) => i.getBoundingClientRect().width === 0) }))()")
    checks["bars_wait_for_the_splash_to_leave"] = bool(on_splash and (not on_splash["splash"] or on_splash["empty"]))
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
    shows_help = "账号令牌" in (run("document.querySelector('#manual-guide').textContent") or "")
    run("document.querySelector('.game[data-key=genshin]').click(); 0")
    pause()
    closed_again = run("document.querySelector('details.adv').open") is False
    checks["advanced_opens_after_a_failed_update_when_a_token_can_help"] = bool(
        before is False and opened_after_failure and shows_help and closed_again)
    # 右下角的太阳/月亮：点一下在浅色和深色之间切换，页面底色真的跟着变；再点一下回来
    probe = "[document.documentElement.dataset.mode, document.documentElement.getAttribute('data-theme'), getComputedStyle(document.body).backgroundColor]"
    before = run(probe)
    run("document.querySelector('#theme').click(); 0")
    pause(1.3)                       # 切换有一个 0.65 秒的圆形扩散动画
    flipped = run(probe)
    run("document.querySelector('#theme').click(); 0")
    pause(1.3)
    back = run(probe)
    checks["theme_button_flips_light_and_dark_and_back"] = bool(
        before and flipped and back
        and flipped[0] != before[0] and flipped[1] == flipped[0] and flipped[2] != before[2]
        and back[0] == before[0] and back[2] == before[2])
    checks["theme_button_is_an_icon_not_text"] = run(
        "document.querySelector('#theme').textContent.trim() === '' && document.querySelectorAll('#theme svg').length === 2") is True
    # 开屏淡出之后被移走；侧栏和页面顶部都用游戏的官方图标，并且图片真的加载出来了
    deadline = time.time() + 12
    while time.time() < deadline and run("document.querySelector('#splash') !== null") is True:
        time.sleep(0.4)
    checks["splash_goes_away"] = run("document.querySelector('#splash') === null") is True
    checks["official_game_icons_load"] = run(
        "(() => { const icons = [...document.querySelectorAll('.game img')]; "
        "return icons.length === %d && icons.every((i) => i.complete && i.naturalWidth > 0) "
        "&& document.querySelector('#title-logo').naturalWidth > 0; })()" % len(GAMES)) is True
    # 主页的精简：没有「更新全部游戏」、统计里没有括号百分比、写「已垫」
    run("document.querySelector('.game[data-key=genshin]').click(); 0")
    pause()
    checks["main_page_is_trimmed"] = run(
        "document.querySelector('#sync-all') === null "
        "&& [...document.querySelectorAll('.stat .k')].every((k) => !/[%％]/.test(k.textContent)) "
        "&& document.querySelector('.pity .row span').textContent.startsWith('已垫') "
        "&& document.querySelector('details.adv summary').textContent.trim() === '高级' "
        "&& document.querySelector('#manual-guide').hidden === true") is True
    # 「全部记录」每页 10 条，下面有翻页箭头，而且箭头够大（按钮和里面的箭头都比原来大了一半）
    first_page = run("[document.querySelectorAll('tbody tr').length, document.querySelector('tbody tr td').textContent]")
    arrow = run("(() => { const b = document.querySelector('.pager button[aria-label=下一页]'); const r = b.getBoundingClientRect();"
                "return [r.width, r.height, parseFloat(getComputedStyle(b).fontSize)]; })()")
    run("document.querySelector('.pager button[aria-label=下一页]').click(); 0")
    pause()
    second_page = run("[document.querySelectorAll('tbody tr').length, document.querySelector('tbody tr td').textContent, document.querySelector('.pager .where').textContent]")
    checks["records_are_paged_by_10_with_big_arrows"] = bool(
        first_page and second_page and first_page[0] == 10 and second_page[0] == 10
        and first_page[1] != second_page[1] and second_page[2].startswith("第 2 /")
        and arrow and arrow[0] >= 58 and arrow[1] >= 50 and arrow[2] >= 26)
    # 明日方舟：三个网址各占一行，每个网址后面紧跟一个「复制」图标按钮（不是文字，也不在最右边），点一下变成对勾
    run("document.querySelector('.game[data-key=arknights]').click(); 0")
    pause()
    rows = run("document.querySelectorAll('#manual-guide .urlrow').length")
    layout = run("(() => { const row = document.querySelector('#manual-guide .urlrow'); const code = row.querySelector('code'); const b = row.querySelector('button');"
                 "return [b.textContent.trim() === '', b.querySelectorAll('svg').length, b.getBoundingClientRect().left - code.getBoundingClientRect().right, "
                 "document.querySelector('#manual-guide').textContent.includes('B 服账号用'), document.querySelector('#manual-guide .fine') === null]; })()")
    run("document.querySelector('#manual-guide .urlrow button').click(); 0")
    pause(0.5)
    copied = run("document.querySelector('#manual-guide .urlrow button').classList.contains('done')")
    checks["token_page_urls_have_copy_icons_right_after_them"] = bool(
        rows == 3 and layout and layout[0] and layout[1] == 1 and layout[2] < 40 and not layout[3] and layout[4] and copied is True)
    # 侧栏选中的游戏：灰/白色高亮块从“上一个选中的位置”滑到被点的游戏（不是从最上面滑下来），字不加粗。
    # 点击的那一刻马上读滑块的位置：它还在老位置，说明动画是从老位置开始的。
    slide = """(() => {
      const pill = document.querySelector('%s .pill');
      const y = () => new DOMMatrix(getComputedStyle(pill).transform).m41 + ',' + new DOMMatrix(getComputedStyle(pill).transform).m42;
      const before = y();
      pill.__sameElement = true;                       // 给滑块做个记号：点击之后它必须还是这同一个元素，不能被换成新生成的
      document.querySelector('%s').click();
      const right_after = y();
      return [before, right_after, document.querySelector('%s .pill').__sameElement === true];
    })()"""
    where = "(() => { const m = new DOMMatrix(getComputedStyle(document.querySelector('%s .pill')).transform); return [m.m41, m.m42]; })()"
    run("document.querySelector('.game[data-key=genshin]').click(); 0")
    pause(0.9)
    moved = run(slide % ("#games", ".game[data-key=zzz]", "#games"))
    pause(0.12)
    midway = run(where % "#games")           # 动画进行到一半时：滑块应该正好在“起点”和“终点”之间
    pause(0.9)
    arrived = run("(() => { const sel = document.querySelector('#games [aria-current=true]'); const pill = document.querySelector('#games .pill');"
                  "const m = new DOMMatrix(getComputedStyle(pill).transform);"
                  "return [Math.abs(m.m42 - sel.offsetTop) < 2, Math.abs(pill.offsetHeight - sel.offsetHeight) < 2,"
                  "parseFloat(getComputedStyle(sel.querySelector('.gname')).fontWeight) < 600, m.m42]; })()")
    start_y = float(moved[0].split(",")[1]) if moved else 0
    checks["sidebar_highlight_slides_from_the_previous_game"] = bool(
        moved and moved[0] == moved[1] and moved[2] is True and arrived and arrived[:3] == [True, True, True]
        and midway and start_y + 2 < midway[1] < arrived[3] - 2)
    # 卡池标签：最底下是底板，白色（浅色模式下是黑色）滑块在底板上方、文字下方，从上一个标签滑到被点的那个
    run("document.querySelector('.game[data-key=zzz]').click(); 0")
    pause(0.9)
    slide_tab = run(slide % ("#tabs", "#tabs .tab:nth-child(4)", "#tabs"))      # #tabs 的第 1 个孩子是滑块，所以第 4 个孩子是第 3 个标签
    pause(0.12)
    tab_midway = run(where % "#tabs")
    pause(0.9)
    tabs_ok = run("(() => { const tab = document.querySelector('.tab[aria-selected=true]'); const pill = document.querySelector('#tabs .pill');"
                  "const m = new DOMMatrix(getComputedStyle(pill).transform);"
                  "const track = getComputedStyle(document.querySelector('#tabs'));"
                  "return [Math.abs(m.m41 - tab.offsetLeft) < 2 && Math.abs(pill.offsetWidth - tab.offsetWidth) < 2"
                  " && track.backgroundColor !== 'rgba(0, 0, 0, 0)'"                                              # 底板不是透明的
                  " && getComputedStyle(tab).backgroundColor === 'rgba(0, 0, 0, 0)'"                                # 标签自己不盖住滑块
                  " && Number(getComputedStyle(tab).zIndex) > Number(getComputedStyle(pill).zIndex), m.m41]; })()")   # 文字在滑块上面
    checks["pool_tabs_highlight_slides_between_text_and_base"] = bool(
        slide_tab and slide_tab[0] == slide_tab[1] and slide_tab[2] is True and tabs_ok and tabs_ok[0] is True
        and tab_midway and float(slide_tab[0].split(",")[0]) + 2 < tab_midway[0] < tabs_ok[1] - 2)
    # 外观按钮在左下角
    checks["theme_button_is_bottom_left"] = run(
        "(() => { const r = document.querySelector('#theme').getBoundingClientRect(); return r.left < 80 && r.top > innerHeight / 2; })()") is True
    # 原神：歪的标记（和名字在同一条水平中线上）、歪的概率、十连双金；不再有“出过常驻角色”和“十连出 2+ 个四星”
    run("document.querySelector('.game[data-key=genshin]').click(); 0")
    pause()
    checks["genshin_shows_lose_tag_and_extra_stats"] = run(
        "(() => { const labels = [...document.querySelectorAll('.stat .k')].map((k) => k.textContent);"
        "const lose = document.querySelector('.tops .tag.lose');"
        "return !!lose && lose.textContent === '歪' && labels.includes('歪常驻角色概率') && labels.includes('十连双金')"
        " && !labels.includes('出过常驻角色') && !labels.some((l) => l.includes('四星') && l.includes('十连')); })()") is True
    offset = run("(() => { const line = document.querySelector('.tops .nm-line'); const tag = line.querySelector('.tag.lose');"
                 "const range = document.createRange(); range.selectNodeContents(line.firstChild);"
                 "const t = range.getBoundingClientRect(); const g = tag.getBoundingClientRect();"
                 "return Math.abs((t.top + t.bottom) / 2 - (g.top + g.bottom) / 2); })()")
    checks["lose_tag_is_centered_with_the_name"] = offset is not None and offset <= 2.5
    # 保底的条：先是空的，进到眼前后各自从左往右长到该有的长度；同时出现的几根错开先后；翻页后重新显示时再长一遍
    width_js = "(i) => i.getBoundingClientRect().width"
    run("window.scrollTo(0, 0); 0")
    pause(2.0)
    pity_grown = run("(() => { const i = document.querySelector('.pity .bar > i'); const full = i.parentElement.getBoundingClientRect().width;"
                     "return Math.abs(i.getBoundingClientRect().width - parseFloat(i.dataset.width) / 100 * full) < 2 && i.getBoundingClientRect().width > 4; })()")
    run("document.querySelector('.tops').scrollIntoView({ block: 'center' }); 0")
    pause(2.4)
    tops_grown = run("(() => { const fills = [...document.querySelectorAll('.tops .bar > i')];"
                     "const ok = fills.every((i) => Math.abs(i.getBoundingClientRect().width - parseFloat(i.dataset.width) / 100 * i.parentElement.getBoundingClientRect().width) < 2);"
                     "const delays = new Set(fills.map((i) => getComputedStyle(i).transitionDelay));"
                     "return [fills.length, ok, delays.size]; })()")
    restarted = run("(() => { document.querySelector('.pager button[aria-label=下一页]').click();"          # 翻页后页面重新生成，条又是空的
                    "return [...document.querySelectorAll('.bar > i')].every((i) => i.getBoundingClientRect().width === 0); })()")
    checks["pity_bars_grow_left_to_right_one_by_one_and_restart_on_paging"] = bool(
        pity_grown is True and tops_grown and tops_grown[0] >= 2 and tops_grown[1] is True
        and tops_grown[2] == tops_grown[0] and restarted is True)
    # 字体：随软件带的几款字体都真的加载出来了，并且用在了该用的地方（大数字用宋体、角色名用文楷、正文用黑体）
    run("document.querySelector('.game[data-key=genshin]').click(); 0")
    pause()
    checks["bundled_fonts_load_and_are_used"] = run(
        "(() => { const loaded = [...document.fonts].filter((f) => f.status === 'loaded').map((f) => f.family.replace(/\"/g, '') + f.weight);"
        "const fam = (sel) => getComputedStyle(document.querySelector(sel)).fontFamily.replace(/[\"']/g, '');"
        "return ['Noto Sans SC400', 'Noto Sans SC500', 'Noto Serif SC600', 'LXGW WenKai700'].every((x) => loaded.includes(x))"
        " && fam('.stat .v').startsWith('Noto Serif SC') && fam('.tops .name').startsWith('LXGW WenKai')"
        " && fam('body').startsWith('Noto Sans SC'); })()") is True
    # 窗口拉宽时内容跟着变宽（不再卡在一个最大宽度上）
    try:
        window.resize(1500, 820)
    except Exception:
        pass
    pause(1.0)
    checks["layout_follows_the_window_width"] = run(
        "(() => { const main = document.querySelector('.main'); return getComputedStyle(main).maxWidth === 'none'"
        " && main.getBoundingClientRect().right >= innerWidth - 80; })()") is True
    try:
        window.resize(1000, 680)
    except Exception:
        pass
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
        on_top=bool(selftest_out),   # 自检要看动画有没有真的在走：窗口被别的窗口挡住时，系统会把动画暂停
    )
    dialogs.window = window

    if selftest_out:
        webview.start(_selftest, (window, selftest_out), debug=debug)
        ok = json.loads(Path(selftest_out).read_text(encoding="utf-8")).get("ok")
        return 0 if ok else 1
    webview.start(debug=debug)
    return 0
