import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from wishlog.__main__ import app_root


class AppRoot(unittest.TestCase):
    def test_running_from_source_uses_the_project_folder(self):
        self.assertTrue((app_root() / "wishlog" / "static" / "index.html").is_file())

    def test_packaged_exe_keeps_data_next_to_the_exe_not_in_the_temp_unpack_dir(self):
        with tempfile.TemporaryDirectory() as tmp:
            exe = Path(tmp) / "GenshinWishLog.exe"
            with mock.patch.object(sys, "frozen", True, create=True), \
                    mock.patch.object(sys, "executable", str(exe)):
                self.assertEqual(app_root(), Path(tmp).resolve())


if __name__ == "__main__":
    unittest.main()
