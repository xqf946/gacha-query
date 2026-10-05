"""打包成 exe 用的入口（PyInstaller 需要一个顶层脚本）。直接 `python run.py` 也能运行。"""

import sys
import traceback

from wishlog.__main__ import main

if __name__ == "__main__":
    try:
        main()
    except Exception:
        # 双击 exe 运行时，出错后窗口会立刻消失，用户来不及看错误；先停住让人能看到。
        traceback.print_exc()
        print("\n程序出错了，请把上面的内容截图或复制给开发者。")
        if sys.stdin and sys.stdin.isatty():
            input("按回车键关闭窗口…")
        sys.exit(1)
