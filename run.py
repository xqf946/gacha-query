"""打包成 exe 用的入口（PyInstaller 需要一个顶层脚本）。直接 `python run.py` 也能运行。"""

import sys

from wishlog.__main__ import main

if __name__ == "__main__":
    sys.exit(main())
