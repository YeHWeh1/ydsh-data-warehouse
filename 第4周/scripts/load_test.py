# -*- coding: utf-8 -*-
"""
load_test.py —— 压力测试（任务书子任务 5）

测什么：
  1. 并发查询：N 个线程同时打同一个接口，看 P50/P95/P99 与错误率
  2. 引擎对比：同一份数据，ClickHouse vs Hive 的响应时间差异
  3. 慢查询：故意不加分区条件的 DWD 全表聚合，对比加分区条件的差异

为什么这样测：
  任务书只要求「简单工具测试并发查询响应时间」，但只报一个平均数没意义。
  这里给出分位数 + 引擎对比 + 分区裁剪前后对比，能直接支撑「查询优化到位」的结论。

输出：reports/压力测试原始数据.json + 终端摘要（供手册引用）
"""
import concurrent.futures as cf
import json
import statistics
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
REPORT = ROOT / "reports"
BASE = "http://127.0.0.1:8000"

# 压测场景：接口 + 说明
SCENARIOS = [
    ("/api/kpi",            "KPI 卡片（单行）"),
    ("/api/trend",          "GMV 趋势（366 行）"),
    ("/api/heatmap",        "时段热力（192 行）"),
    ("/api/category",       "品类排行（8 行）"),
    ("/api/region",         "区域分布（15 行）"),
]


def pct(values: list[float], p: float) -> float:
    """计算分位数（线性插值）。"""
    if not values:
        return 0.0
    s = sorted(values)
    if len(s) == 1:
        return s[0]
    k = (len(s) - 1) * p
    lo, hi = int(k), min(int(k) + 1, len(s) - 1)
    return s[lo] + (s[hi] - s[lo]) * (k - lo)


def one_request(path: str) -> tuple[bool, float]:
    t0 = time.time()
    try:
        r = requests.get(BASE + path, timeout=60)
        ok = r.status_code == 200 and r.json().get("ok") is not False
        return ok, (time.time() - t0) * 1000
    except Exception:
        return False, (time.time() - t0) * 1000


def run_concurrency(path: str, concurrency: int, total: int) -> dict:
    """固定并发度打 total 次请求。"""
    latencies, ok_cnt = [], 0
    with cf.ThreadPoolExecutor(max_workers=concurrency) as pool:
        futures = [pool.submit(one_request, path) for _ in range(total)]
        for f in cf.as_completed(futures):
            ok, ms = f.result()
            latencies.append(ms)
            ok_cnt += 1 if ok else 0
    return {
        "concurrency": concurrency,
        "total": total,
        "ok": ok_cnt,
        "error_rate_pct": round((total - ok_cnt) * 100.0 / total, 2),
        "avg_ms": round(statistics.mean(latencies), 1),
        "p50_ms": round(pct(latencies, 0.50), 1),
        "p95_ms": round(pct(latencies, 0.95), 1),
        "p99_ms": round(pct(latencies, 0.99), 1),
        "max_ms": round(max(latencies), 1),
    }


def hive_beeline(sql: str, timeout=600) -> tuple[float, bool]:
    """用 beeline 跑一条 SQL，返回 (耗时秒, 是否成功)。"""
    t0 = time.time()
    p = subprocess.run(
        ["docker", "exec", "hive-server", "beeline",
         "-u", "jdbc:hive2://localhost:10000", "--silent=true", "-e", sql],
        capture_output=True, text=True, timeout=timeout,
        encoding="utf-8", errors="replace",
    )
    out = (p.stdout or "") + (p.stderr or "")
    ok = p.returncode == 0 and "Error:" not in out and "FAILED:" not in out
    return time.time() - t0, ok


def ch_client(sql: str) -> tuple[float, bool]:
    """用 clickhouse-client 跑一条 SQL，返回 (耗时秒, 是否成功)。"""
    t0 = time.time()
    p = subprocess.run(
        ["docker", "exec", "clickhouse", "sh", "-c",
         f'clickhouse-client --user "$CLICKHOUSE_USER" --password "$CLICKHOUSE_PASSWORD" '
         f'--query "{sql}"'],
        capture_output=True, text=True, timeout=300,
        encoding="utf-8", errors="replace",
    )
    ok = p.returncode == 0 and not (p.stderr or "").strip()
    return time.time() - t0, ok


def main() -> int:
    REPORT.mkdir(parents=True, exist_ok=True)
    result = {"generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
              "concurrency": [], "engine_compare": [], "partition_compare": []}

    print("=" * 66)
    print("  第 4 周 压力测试")
    print("=" * 66)

    # ---- 1. 并发测试 ----
    print("\n[1/3] 并发查询（每个场景 3 档并发 × 60 次请求）")
    for path, label in SCENARIOS:
        print(f"\n  {path}  {label}")
        print(f"    {'并发':<6}{'成功':<8}{'错误率':<10}{'均值':<10}{'P50':<9}{'P95':<9}{'P99':<9}{'最大'}")
        for conc in (1, 5, 20):
            r = run_concurrency(path, conc, 60)
            r["path"] = path
            r["label"] = label
            result["concurrency"].append(r)
            print(f"    {conc:<6}{r['ok']:<8}{str(r['error_rate_pct'])+'%':<10}"
                  f"{str(r['avg_ms'])+'ms':<10}{str(r['p50_ms'])+'ms':<9}"
                  f"{str(r['p95_ms'])+'ms':<9}{str(r['p99_ms'])+'ms':<9}{r['max_ms']}ms")

    # ---- 2. 引擎对比 ----
    print("\n[2/3] 引擎对比（同一份数据的等价比聚合）")
    compare_sql = [
        ("日 GMV 汇总（366 行）",
         "select count(*) from ydsh_ads.ads_sales_daily_report",
         "select count(*) from ydsh_ads.sales_daily"),
        ("品类汇总（8 行）",
         "select count(*) from ydsh_ads.ads_category_topn",
         "select count(*) from ydsh_ads.category_topn"),
        ("时段热力（192 行）",
         "select count(*) from ydsh_ads.ads_hour_category_heat",
         "select count(*) from ydsh_ads.hour_category_heat"),
    ]
    print(f"    {'场景':<24}{'Hive':<12}{'ClickHouse':<14}{'提速'}")
    for label, hive_sql, ch_sql in compare_sql:
        t_hive, ok_h = hive_beeline(hive_sql)
        t_ch, ok_c = ch_client(ch_sql)
        speedup = round(t_hive / t_ch, 1) if t_ch > 0 else 0
        row = {"label": label, "hive_s": round(t_hive, 2), "hive_ok": ok_h,
               "clickhouse_s": round(t_ch, 3), "clickhouse_ok": ok_c,
               "speedup_x": speedup}
        result["engine_compare"].append(row)
        print(f"    {label:<24}{str(row['hive_s'])+'s':<12}"
              f"{str(row['clickhouse_s'])+'s':<14}{speedup}x")

    # ---- 3. 分区裁剪对比 ----
    print("\n[3/3] 分区裁剪 / 预聚合对比（DWD 明细 vs ADS 汇总）")
    # 注意：本项目的 DWD 表未做物理分区（数据量 1.5 万行，分区收益不明显），
    # 所以这里对比的是「查明细表 vs 查预聚合表」，这才是本周真正生效的优化
    part_sql = [
        ("DWD 明细全表聚合（无预聚合）",
         "select round(sum(pay_amount),2) from ydsh_dwd.dwd_order where is_valid_amount=1"),
        ("DWD 明细按月聚合（带日期条件）",
         "select substr(order_date,1,7) ym, round(sum(pay_amount),2) from ydsh_dwd.dwd_order "
         "where is_valid_amount=1 and order_date >= '2024-01-01' group by substr(order_date,1,7)"),
        ("ADS 预聚合月报（本周大屏实际用法）",
         "select ym, gmv from ydsh_ads.ads_monthly_report"),
    ]
    print(f"    {'查询':<38}{'耗时':<10}{'结论'}")
    for label, sql in part_sql:
        t, ok = hive_beeline(sql)
        row = {"label": label, "sql": sql, "elapsed_s": round(t, 2), "ok": ok}
        result["partition_compare"].append(row)
        print(f"    {label:<38}{str(row['elapsed_s'])+'s':<10}{'成功' if ok else '失败'}")

    # ---- 写文件 ----
    out = REPORT / "压力测试原始数据.json"
    out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n  原始数据已写入：{out}")

    # ---- 汇总判定 ----
    total_err = sum(r["error_rate_pct"] for r in result["concurrency"])
    print(f"\n  并发错误率合计：{total_err:.2f}%")
    print(f"  结论：{'无错误，服务稳定' if total_err == 0 else '存在错误，需排查'}")

    return 0


if __name__ == "__main__":
    from console_shim import init_console
    init_console()
    sys.exit(main())
