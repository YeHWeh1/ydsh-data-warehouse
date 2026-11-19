# -*- coding: utf-8 -*-
"""
hive_run_sql.py —— 执行一个 .sql 文件（通过 stdin 传给 beeline）

为什么要这个脚本，而不是在 PowerShell 里 Get-Content | docker exec：
  PowerShell 5.1 的 `Get-Content -Raw -Encoding UTF8` 会把文件 BOM（EF BB BF）
  一起写进管道。beeline 读到后报：
      ParseException line 1:0 character '﻿' not supported here
  结果建表/导入全部失败，而外层脚本只看到「有输出」，很容易误判成功。

  这里用 Python 读文件并按 UTF-8 无 BOM 重新编码后写 stdin，彻底规避。

用法：
  python hive_run_sql.py <sql文件> [--container hive-server]
  python hive_run_sql.py <sql文件> --check        # 只打印是否会报错
"""
import argparse
import subprocess
import sys
from pathlib import Path


def read_sql_no_bom(path: Path) -> str:
    """读 SQL 文件，去掉 BOM，统一成 UTF-8 文本。"""
    raw = path.read_bytes()
    if raw.startswith(b"\xef\xbb\xbf"):
        raw = raw[3:]                      # 去掉 UTF-8 BOM
    for enc in ("utf-8", "gbk"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def run(sql_path: Path, container: str, jdbc: str, silent: bool = True) -> tuple[bool, str]:
    text = read_sql_no_bom(sql_path)
    if not text.strip():
        return False, f"文件为空：{sql_path}"

    cmd = ["docker", "exec", "-i", container, "beeline", "-u", jdbc]
    if silent:
        cmd.append("--silent=true")

    p = subprocess.run(
        cmd,
        input=text.encode("utf-8"),        # 关键：显式 UTF-8 编码，不带 BOM
        capture_output=True,
        timeout=1800,
    )
    out = (p.stdout or b"").decode("utf-8", errors="replace")
    err = (p.stderr or b"").decode("utf-8", errors="replace")
    combined = out + err

    bad = ("Error:" in combined) or ("FAILED:" in combined)
    ok = p.returncode == 0 and not bad
    return ok, combined


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("sql_file")
    ap.add_argument("--container", default="hive-server")
    ap.add_argument("--jdbc", default="jdbc:hive2://localhost:10000")
    ap.add_argument("--check", action="store_true", help="只检查，不执行")
    args = ap.parse_args()

    path = Path(args.sql_file)
    if not path.exists():
        print(f"找不到文件：{path}")
        return 2

    if args.check:
        raw = path.read_bytes()
        print(f"文件：{path}")
        print(f"大小：{len(raw)} 字节")
        print(f"BOM：{'有（会被 beeline 报错）' if raw.startswith(b'\xef\xbb\xbf') else '无'}")
        return 0

    ok, out = run(path, args.container, args.jdbc)
    if ok:
        print(f"OK  {path.name}")
        return 0

    print(f"FAIL  {path.name}")
    errs = [ln for ln in out.splitlines() if "Error:" in ln or "FAILED:" in ln]
    for ln in errs[:5]:
        print("   " + ln.strip())
    return 1


if __name__ == "__main__":
    sys.exit(main())
