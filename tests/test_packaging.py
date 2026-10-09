import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


class PackagingNames(unittest.TestCase):
    """打包脚本里的名字要前后一致。这些步骤只在云端的 Windows 上才会真的跑，改名时最容易改漏，所以在这里先对一遍。"""

    def setUp(self):
        self.workflow = (ROOT / ".github" / "workflows" / "build-windows.yml").read_text(encoding="utf-8")
        scripts = list((ROOT / "installer").glob("*.iss"))
        self.assertEqual(len(scripts), 1)
        self.script_path = scripts[0]
        self.script = self.script_path.read_text(encoding="utf-8-sig")
        self.readme = (ROOT / "README.md").read_text(encoding="utf-8")
        self.name = re.search(r"--name (\w+)", self.workflow).group(1)

    def test_the_program_folder_exe_installer_and_zip_all_use_the_same_name(self):
        name = self.name
        self.assertEqual(name, "GachaQuery")
        self.assertIn(f'#define MyAppExe "{name}.exe"', self.script)
        self.assertIn(f"..\\dist\\{name}\\*", self.script)
        self.assertIn(f"OutputBaseFilename={name}-Setup-", self.script)
        self.assertEqual(self.script_path.name, f"{name}.iss")
        self.assertIn(f"installer\\{name}.iss", self.workflow)
        self.assertIn(f"dist\\{name}\\{name}.exe".replace("\\", "\\"), self.workflow.replace("/", "\\"))
        self.assertIn(f"dist/{name}-Setup-*.exe", self.workflow)
        self.assertIn(f"dist/{name}-*-portable.zip", self.workflow)

    def test_the_readme_tells_people_the_right_file_names(self):
        self.assertIn(f"`{self.name}-Setup-x.y.z.exe`", self.readme)
        self.assertIn(f"`{self.name}-x.y.z-portable.zip`", self.readme)
        self.assertIn("github.com/xqf946/gacha-query/releases", self.readme)

    def test_upgrading_cleans_up_what_the_old_names_left_behind(self):
        # AppId 不变 → 升级装回原来的文件夹；旧名字的程序和快捷方式要清掉
        self.assertIn('Name: "{app}\\GenshinWishLog.exe"', self.script)
        self.assertIn("原神抽卡记录.lnk", self.script)
        self.assertIn("AppId={{E55F1A55-5CBC-4311-8E3B-EF57182406FA}", self.script)

    def test_the_folder_that_holds_the_users_records_keeps_its_name(self):
        # 改了这个文件夹名，升级后用户就找不到自己的记录了
        paths = (ROOT / "wishlog" / "paths.py").read_text(encoding="utf-8")
        self.assertIn('APP_DIR_NAME = "GenshinWishLog"', paths)


if __name__ == "__main__":
    unittest.main()
