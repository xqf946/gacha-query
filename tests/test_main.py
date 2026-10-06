import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from wishlog.demo import DEMO_UID, DEMO_UID_2, seed
from wishlog.games import GAMES
from wishlog.paths import default_data_dir
from wishlog.stats import analyze
from wishlog.store import Store


class DefaultDataDir(unittest.TestCase):
    def test_running_from_source_uses_the_project_folder(self):
        self.assertEqual(default_data_dir(), Path(__file__).resolve().parent.parent / "data")

    def test_installed_app_on_windows_uses_localappdata_not_the_program_folder(self):
        with tempfile.TemporaryDirectory() as tmp, \
                mock.patch.object(sys, "frozen", True, create=True), \
                mock.patch.object(sys, "platform", "win32"), \
                mock.patch.dict("os.environ", {"LOCALAPPDATA": tmp}):
            self.assertEqual(default_data_dir(), Path(tmp) / "GenshinWishLog" / "data")

    def test_installed_app_falls_back_to_home_when_localappdata_is_missing(self):
        with mock.patch.object(sys, "frozen", True, create=True), \
                mock.patch.object(sys, "platform", "win32"), \
                mock.patch.dict("os.environ"):   # 退出时自动还原；只去掉这一项，别的（如用户主目录）保留
            os.environ.pop("LOCALAPPDATA", None)
            self.assertEqual(
                default_data_dir(),
                Path.home() / "AppData" / "Local" / "GenshinWishLog" / "data",
            )


class DemoData(unittest.TestCase):
    def test_seed_produces_realistic_data_for_every_game(self):
        with tempfile.TemporaryDirectory() as tmp:
            seed(Path(tmp))
            for game in GAMES:
                store = Store(Path(tmp) / game.key)
                self.assertIn(DEMO_UID, store.uids(), game.key)
                pools = analyze(game, store.load(DEMO_UID)["records"], store.meta(DEMO_UID).get("pool_names"))
                self.assertGreater(sum(p["total"] for p in pools), 100, game.key)
                for pool in pools:
                    # 模拟的保底规律必须成立：没有任何一个最高档的垫抽超过硬保底
                    if pool["hard_pity"] and pool["total"]:
                        self.assertLessEqual(pool["max_pity_top"] or 0, pool["hard_pity"], (game.key, pool["key"]))
            self.assertEqual(Store(Path(tmp) / "genshin").uids(), [DEMO_UID, DEMO_UID_2])

    def test_demo_zzz_data_uses_the_s_a_b_ranks(self):
        with tempfile.TemporaryDirectory() as tmp:
            seed(Path(tmp))
            ranks = {r["rank_type"] for r in Store(Path(tmp) / "zzz").load(DEMO_UID)["records"]}
            self.assertEqual(ranks, {"4", "3", "2"})

    def test_demo_wuwa_data_has_same_second_ten_pulls_with_unique_ids(self):
        with tempfile.TemporaryDirectory() as tmp:
            seed(Path(tmp))
            records = Store(Path(tmp) / "wuwa").load(DEMO_UID)["records"]
            self.assertEqual(len({r["id"] for r in records}), len(records))
            times = [r["time"] for r in records if r["gacha_type"] == "1"]
            self.assertGreater(max(times.count(t) for t in times), 1)   # 同一秒里有多条


if __name__ == "__main__":
    unittest.main()
