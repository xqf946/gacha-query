"""米哈游的三个游戏：原神、崩坏：星穹铁道、绝区零。

三者机制完全一样（读 webCaches 缓存里的链接 → 请求 public-operation-xxx.mihoyo.com），
只有域名、路径、卡池编号、保底抽数不同，所以做成同一个引擎加三份配置。
卡池编号和接口路径来自 Starward、star-rail-warp-export 等开源工具的源码。
"""

from __future__ import annotations

from datetime import datetime

from ..client import ApiSpec, Client, parse_wish_url
from ..locate import (
    LocateError, describe_url, find_cache_file, find_data_dir, find_wish_urls, mask_path,
)
from ..sync import sync_all
from .base import STARS, TIERS, Game, Pool, Standard


class MihoyoGame(Game):
    def __init__(self, *, api: ApiSpec, data_dir_name: str, **common):
        super().__init__(**common)
        self.api = api
        self.data_dir_name = data_dir_name

    def make_client(self) -> Client:
        return Client(self.api)

    def connect(self, client, url: str, game_dir: str):
        probe = self.pools[0].key
        if url.strip():
            auth = parse_wish_url(url, self.api)
            client.fetch_page(auth, probe, 1)
            return auth
        data_dir = find_data_dir(self.data_dir_name, explicit=game_dir or None)
        urls = find_wish_urls(data_dir, self.api.biz_prefix)
        return client.pick_valid_auth(urls, probe)

    def sync(self, client, auth, store, progress=None) -> dict:
        return sync_all(client, auth, self, store, progress)

    def diagnose(self, game_dir: str) -> list:
        try:
            data_dir = find_data_dir(self.data_dir_name, explicit=game_dir or None)
        except LocateError as e:
            return [f"游戏目录：没找到（{str(e).splitlines()[0]}）"]
        lines = [f"游戏目录：{mask_path(data_dir)}"]
        try:
            cache = find_cache_file(data_dir)
            info = cache.stat()
            when = datetime.fromtimestamp(info.st_mtime).strftime("%Y-%m-%d %H:%M")
            lines.append(f"缓存文件：{mask_path(cache)}（{info.st_size / 1024 / 1024:.1f} MB，最后写入 {when}）")
        except LocateError as e:
            lines.append(f"缓存文件：没找到（{str(e).splitlines()[0]}）")
            return lines
        try:
            urls = find_wish_urls(data_dir, self.api.biz_prefix)
            lines.append(f"祈愿链接：找到 {len(urls)} 条；最新一条：{describe_url(urls[0])}")
        except LocateError as e:
            lines.append(f"祈愿链接：没找到（{str(e).splitlines()[0]}）")
        return lines


GENSHIN = MihoyoGame(
    key="genshin", name="原神", short_name="原神",
    data_dir_name="YuanShen_Data",
    api=ApiSpec("https://public-operation-hk4e.mihoyo.com", "/gacha_info/api/getGachaLog", "hk4e"),
    pools=(
        Pool("301", "角色活动祈愿", 90, 74, ("301", "400")),   # 301 和 400 共用保底，请求 301 即可拿到两者
        Pool("302", "武器活动祈愿", 80, 63),
        Pool("500", "集录祈愿", 90, 74),
        Pool("200", "常驻祈愿", 90, 74),
        Pool("100", "新手祈愿", None, None),
    ),
    ranks=STARS, currency="原石",
    hint="进入「祈愿」→ 左下角「历史记录」，随便点开一个卡池翻一翻。",
    # 常驻池里可以出的五星角色。游戏版本更新后可能增加，需要时直接在这里加名字。
    # 这些角色自己做 UP 的复刻池里出了不算歪，但从记录里分辨不出来，所以界面上只标“常驻”。
    standard=Standard(frozenset({"琴", "迪卢克", "七七", "莫娜", "刻晴", "提纳里"}), frozenset({"301"})),
    trust_record_type=True,
)

HSR = MihoyoGame(
    key="hsr", name="崩坏：星穹铁道", short_name="崩铁",
    data_dir_name="StarRail_Data",
    api=ApiSpec(
        "https://public-operation-hkrpg.mihoyo.com",
        "/common/hkrpg_gacha_record/api/getGachaLog", "hkrpg",
        ld_path="/common/hkrpg_gacha_record/api/getLdGachaLog",
    ),
    pools=(
        Pool("11", "角色活动跃迁", 90, 74),
        Pool("12", "光锥活动跃迁", 80, 66),
        Pool("1", "群星跃迁", 90, 74),
        Pool("2", "始发跃迁", None, None),
        Pool("21", "角色联动跃迁", 90, 74, optional=True, ld=True),
        Pool("22", "光锥联动跃迁", 80, 66, optional=True, ld=True),
    ),
    ranks=STARS, currency="星琼",
    hint="进入「跃迁」页面，打开「跃迁记录」，随便点开一个卡池翻一翻。",
    trust_record_type=False,
)

ZZZ = MihoyoGame(
    key="zzz", name="绝区零", short_name="绝区零",
    data_dir_name="ZenlessZoneZero_Data",
    api=ApiSpec(
        "https://public-operation-nap.mihoyo.com",
        "/common/gacha_record/api/getGachaLog", "nap",
        type_param="real_gacha_type",
    ),
    pools=(
        Pool("2", "独家频段", 90, 74),
        Pool("3", "音擎频段", 80, 65),
        Pool("1", "常驻频段", 90, 74),
        Pool("5", "邦布频段", 80, 65),
        Pool("102", "独家重映", None, None, optional=True),
        Pool("103", "音擎回响", None, None, optional=True),
    ),
    ranks=TIERS, currency="菲林",
    hint="进入「调频」页面，打开「调频记录」，随便点开一个频段翻一翻。",
    trust_record_type=False,
)
