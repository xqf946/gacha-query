"""支持的游戏。顺序就是界面左侧列表里的顺序。"""

from __future__ import annotations

from .base import Game, Pool, Ranks, Standard
from .arknights import ARKNIGHTS
from .endfield import ENDFIELD
from .mihoyo import GENSHIN, HSR, ZZZ
from .wuwa import WUWA

GAMES = (GENSHIN, HSR, ZZZ, WUWA, ARKNIGHTS, ENDFIELD)
GAMES_BY_KEY = {g.key: g for g in GAMES}


def get_game(key: str) -> Game | None:
    return GAMES_BY_KEY.get(key)


__all__ = [
    "ARKNIGHTS", "ENDFIELD", "GAMES", "GAMES_BY_KEY", "GENSHIN", "HSR", "WUWA", "ZZZ",
    "Game", "Pool", "Ranks", "Standard", "get_game",
]
