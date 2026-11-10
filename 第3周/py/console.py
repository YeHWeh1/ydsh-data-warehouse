# -*- coding: utf-8 -*-
"""
console.py —— 控制台 UTF-8 输出兜底

Windows 控制台默认 GBK，打印 ✓ / ⚠ / 中文符号会抛 UnicodeEncodeError。
所有脚本入口先调用 init_console()。
"""
import sys


def init_console():
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
