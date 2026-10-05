#!/bin/bash
# Mac 上双击这个文件即可启动（Windows 请用「启动.bat」）
cd "$(dirname "$0")"

if ! command -v python3 >/dev/null 2>&1; then
    echo "没有找到 python3。请先安装 Python 3：https://www.python.org/downloads/"
    read -n 1 -s -r -p "按任意键关闭…"
    exit 1
fi

python3 -m wishlog
read -n 1 -s -r -p "程序已退出，按任意键关闭…"
