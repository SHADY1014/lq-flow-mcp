#!/bin/bash
cd "$(dirname "$0")" || exit 1
python3 install.py
echo
read -r -p "按回车关闭窗口" _
