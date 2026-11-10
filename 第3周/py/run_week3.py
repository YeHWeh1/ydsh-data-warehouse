# -*- coding: utf-8 -*-
"""
run_week3.py —— 第 3 周 Python 侧一键执行

顺序：
  1. 环境自检（Hive 连通、依赖齐全、.env 与 .gitignore）
  2. Schema 抽取
  3. 预测建模
  4. NL2SQL 测试
  5. AI 洞察
  6. 报告与看板生成

用法：
  python run_week3.py              # 全流程（有 Key 用在线模型，无 Key 自动离线）
  python run_week3.py --no-llm     # 强制离线模式
  python run_week3.py --only 3     # 只跑第 3 步（可多次传）
"""
import argparse
import importlib
import subprocess
import sys
import time
from pathlib import Path

from config import PY_DIR, REPORT_DIR, WEEK_DIR
from console import init_console
from hive_client import ping

PY = sys.executable

STEPS = [
    ("Schema 抽取", "schema_extract.py"),
    ("预测建模", "forecast.py"),
    ("NL2SQL 测试", "nl2sql.py"),
    ("AI 洞察", "insight.py"),
    ("报告与看板", "report_build.py"),
]


def title(t):
    print()
    print("=" * 70)
    print(f"  {t}")
    print("=" * 70)


def check_deps():
    """检查关键依赖，缺哪个报哪个。"""
    need = {
        "pandas": "pandas", "numpy": "numpy", "sklearn": "scikit-learn",
        "matplotlib": "matplotlib", "pyhive": "pyhive",
        "openai": "openai", "docx": "python-docx",
    }
    missing = []
    for mod, pkg in need.items():
        try:
            importlib.import_module(mod)
        except ImportError:
            missing.append(pkg)
    return missing


def check_env():
    """检查 .env 与 .gitignore。"""
    from llm_client import ensure_env_file, get_api_key, mask_key
    created, ignored = ensure_env_file()
    key, prov, cfg = get_api_key()
    print(f"  .env 位置      : {WEEK_DIR.parent / '.env'}")
    print(f"  .env 是否新建  : {created}")
    print(f"  .gitignore 排除: {ignored}")
    print(f"  provider       : {prov} / {cfg.get('model')}")
    print(f"  API Key        : {mask_key(key)}")
    print(f"  运行模式       : {'在线调用大模型' if key else '离线兜底（未配置 Key）'}")
    return bool(key)


def run_step(idx, name, script, extra_args):
    title(f"[{idx}/{len(STEPS)}] {name}")
    cmd = [PY, str(PY_DIR / script)] + extra_args
    t0 = time.time()
    r = subprocess.run(cmd, cwd=str(PY_DIR))
    dt = time.time() - t0
    if r.returncode == 0:
        print(f"\n  ✓ {name} 完成（{dt:.1f}s）")
    else:
        print(f"\n  ✗ {name} 失败（exit {r.returncode}，{dt:.1f}s）")
    return r.returncode == 0


def main():
    init_console()
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-llm", action="store_true", help="强制离线模式")
    ap.add_argument("--only", type=int, nargs="*", help="只跑指定步骤序号")
    args = ap.parse_args()

    title("云仓五号 · 第 3 周 Python 一键执行")
    print(f"  工作目录: {WEEK_DIR}")

    # ---- 1. 环境自检 ----
    print("\n[自检] 依赖包 ...")
    missing = check_deps()
    if missing:
        print(f"  ✗ 缺少依赖: {', '.join(missing)}")
        print(f"    安装: pip install {' '.join(missing)}")
        return 1
    print("  ✓ 依赖齐全")

    print("\n[自检] Hive 连通性 ...")
    ok, msg = ping()
    print(f"  {'✓' if ok else '✗'} {msg}")
    if not ok:
        print("    请先启动环境：docker compose -f 第1周/docker-compose.yml up -d")
        return 1

    print("\n[自检] 大模型配置 ...")
    has_key = check_env()

    # ---- 2. 按步骤执行 ----
    extra = ["--no-llm"] if (args.no_llm or not has_key) else []
    results = []
    for i, (name, script) in enumerate(STEPS, 1):
        if args.only and i not in args.only:
            continue
        ok = run_step(i, name, script, extra)
        results.append((name, ok))

    # ---- 3. 汇总 ----
    title("执行汇总")
    for name, ok in results:
        print(f"  {'✓' if ok else '✗'} {name}")
    failed = [n for n, ok in results if not ok]

    print("\n  产出文件：")
    for p in [
        REPORT_DIR / "第3周_预测建模分析报告.md",
        REPORT_DIR / "第3周_AI洞察与经营简报.md",
        REPORT_DIR / "第3周_运行看板.html",
        REPORT_DIR / "figures",
    ]:
        mark = "✓" if p.exists() else "✗"
        print(f"    {mark} {p}")

    if failed:
        print(f"\n  有 {len(failed)} 个步骤失败：{', '.join(failed)}")
        return 1

    print("\n  全部成功。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
