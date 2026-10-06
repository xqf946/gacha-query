"""游戏的统一定义：卡池、品级、保底，以及每个游戏要实现的几个动作。"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Pool:
    key: str                      # 请求接口时用的卡池编号
    name: str
    hard_pity: int | None         # 最高品级的硬保底；None 表示没有（或不确定），界面就不画进度条
    soft_pity: int | None         # 概率开始大幅提升的抽数，只用来给界面上色（约值）
    gacha_types: tuple = ()       # 存下来的记录里 gacha_type 取这些值时归入本池；留空就只有 key
    optional: bool = False        # 接口不一定支持（联动池、重映池等）：取不到就跳过，不算失败
    ld: bool = False              # 崩铁联动池要走另一个接口路径

    @property
    def types(self) -> tuple:
        return self.gacha_types or (self.key,)


@dataclass(frozen=True)
class Ranks:
    """品级。top 是最高档（原神五星、绝区零 S 级），second 是次一档，other 是最低档。"""

    top: int
    second: int
    other: int
    names: dict = field(default_factory=dict)   # {品级: "五星"}，用在统计卡片上
    marks: dict = field(default_factory=dict)   # {品级: "★★★★★"}，用在记录表格里


@dataclass(frozen=True)
class Standard:
    """常驻角色名单：在这些卡池里出了名单里的角色，多半是歪了（但无法保证）。"""

    names: frozenset
    pool_keys: frozenset
    item_types: frozenset = frozenset({"角色"})


STARS = Ranks(5, 4, 3,
              names={5: "五星", 4: "四星", 3: "三星"},
              marks={5: "★★★★★", 4: "★★★★", 3: "★★★"})
TIERS = Ranks(4, 3, 2,
              names={4: "S 级", 3: "A 级", 2: "B 级"},
              marks={4: "S", 3: "A", 2: "B"})


class Game:
    """一个游戏。子类要实现 make_client / connect / sync / diagnose。"""

    def __init__(self, key: str, name: str, short_name: str, pools: tuple, ranks: Ranks,
                 currency: str, hint: str, standard: Standard | None = None,
                 trust_record_type: bool = True, cost_per_pull: int = 160):
        self.key = key
        self.name = name
        self.short_name = short_name
        self.pools = pools
        self.ranks = ranks
        self.currency = currency
        self.hint = hint                              # 在游戏里怎么打开记录页
        self.standard = standard
        self.trust_record_type = trust_record_type    # False：以请求的卡池为准，不信记录自带的类型
        self.cost_per_pull = cost_per_pull
        self._by_type = {t: p for p in pools for t in p.types}

    def pool_for_type(self, gacha_type) -> Pool | None:
        return self._by_type.get(str(gacha_type))

    def meta(self) -> dict:
        return {
            "key": self.key, "name": self.name, "short_name": self.short_name,
            "currency": self.currency, "hint": self.hint,
            "top_name": self.ranks.names[self.ranks.top],
            "second_name": self.ranks.names[self.ranks.second],
            "has_standard": self.standard is not None,
        }

    # ---- 子类实现 ----
    def make_client(self):
        raise NotImplementedError

    def connect(self, client, url: str, game_dir: str):
        """找到凭证并验证，返回之后传给 sync 的 auth。"""
        raise NotImplementedError

    def sync(self, client, auth, store, progress=None) -> dict:
        """返回 {"uid", "new": {卡池名: 条数}, "total_new", "warnings": [...]}。"""
        raise NotImplementedError

    def diagnose(self, game_dir: str) -> list:
        """不联网，只检查本机：返回给人看的几行文字（不含任何密钥）。"""
        raise NotImplementedError
