"""卡池配置。接口里的 gacha_type 与卡池的对应关系、保底抽数都集中在这里。"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Pool:
    key: str                    # 请求接口时用的 gacha_type
    name: str
    hard_pity: int | None       # 五星硬保底；None 表示这个池子没有保底
    soft_pity: int | None       # 概率开始大幅提升的抽数，只用来给界面上色
    gacha_types: tuple          # 接口返回的记录里，哪些 gacha_type 算进这个池子


# 顺序就是界面上标签页的顺序，也是抓取顺序。
# 角色活动祈愿有两个 gacha_type（301 和 400），共用保底，请求时用 301 即可拿到两者。
POOLS = (
    Pool("301", "角色活动祈愿", 90, 74, ("301", "400")),
    Pool("302", "武器活动祈愿", 80, 63, ("302",)),
    Pool("500", "集录祈愿", 90, 74, ("500",)),
    Pool("200", "常驻祈愿", 90, 74, ("200",)),
    Pool("100", "新手祈愿", None, None, ("100",)),
)

POOL_BY_GACHA_TYPE = {t: p for p in POOLS for t in p.gacha_types}

# 常驻池里可以出的五星角色。游戏版本更新后可能增加，需要时直接在这里加名字。
# 注意：这些角色自己做 UP 的复刻池里出了不算歪，但从记录里分辨不出来，
# 所以界面上只说“出了常驻角色”，不直接断言“歪了”。
STANDARD_FIVE_STAR_CHARACTERS = frozenset(
    {"琴", "迪卢克", "七七", "莫娜", "刻晴", "提纳里"}
)

PRIMOGEMS_PER_PULL = 160
