# -*- coding: utf-8 -*-
"""console_shim.py —— 控制台 UTF-8 兜底（与第 3 周 console.py 同职责）。

Windows 控制台默认 GBK，直接 print ✓ / ✗ / 中文标点会抛 UnicodeEncodeError。
"""
import sys


def init_console():
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
