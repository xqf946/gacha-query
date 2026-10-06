import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from wishlog.demo import DEMO_UID, seed
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
    def test_seed_produces_a_realistic_two_account_dataset(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = Store(tmp)
            seed(store)
            self.assertEqual(store.uids(), [DEMO_UID, "200000002"])
            totals = {p["key"]: p["total"] for p in analyze(store.load(DEMO_UID)["records"])}
            self.assertEqual(totals, {"301": 214, "302": 96, "500": 31, "200": 140, "100": 20})
            # 模拟的保底规律必须成立：没有任何一个五星的垫抽超过硬保底
            for pool in analyze(store.load(DEMO_UID)["records"]):
                if pool["hard_pity"]:
                    self.assertLessEqual(pool["max_pity5"] or 0, pool["hard_pity"])


if __name__ == "__main__":
    unittest.main()
