"""启动软件：python -m wishlog（打包后的 exe 就是从这里进来的）。"""

from __future__ import annotations

import argparse
import sys


def main(argv=None) -> int:
    # 在中文 Windows 的控制台里，个别字符编码不了也不要让程序崩掉
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(errors="replace")

    parser = argparse.ArgumentParser(prog="wishlog", description="原神抽卡记录查询")
    parser.add_argument("--data-dir", help="记录保存目录（默认放在系统的用户数据目录）")
    parser.add_argument("--demo", action="store_true", help="用演示数据预览界面（不碰真实记录）")
    parser.add_argument("--selftest-out", metavar="文件", help="自检：打开窗口、确认界面正常后写出结果并退出")
    parser.add_argument("--debug", action="store_true", help="打开网页调试工具")
    args = parser.parse_args(argv)

    from . import app  # 放在这里导入：只看 --help 时不需要加载窗口框架
    try:
        return app.run(args.data_dir, args.demo, args.selftest_out, args.debug)
    except Exception:
        app.report_fatal()
        return 1


if __name__ == "__main__":
    sys.exit(main())
