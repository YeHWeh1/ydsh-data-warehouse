# -*- coding: utf-8 -*-
"""
sync_clickhouse.py —— Hive ADS 层 → ClickHouse 同步

为什么需要这一步：
  Hive 走 MapReduce/Tez，一条聚合查询动辄十几秒，大屏轮询会很难看。
  ClickHouse 列存 + 向量化，同样数据量毫秒级返回。
  所以架构是「Hive 重算 T+1，ClickHouse 供大屏实时查」。

同步策略：
  - 每张表先 truncate 再整表写入（ADS 表本身是全量重算的，天然幂等）
  - 只同步需要的列，列名与 ADS 表一致，前端不用做字段映射
  - 同步完写一行 sync_meta，/api/health 可据此判断数据是否新鲜

用法：
  python sync_clickhouse.py            # 同步全部表
  python sync_clickhouse.py kpi trend  # 只同步指定表（调试用）
"""
import logging
import os
import sys
import time

import clickhouse_connect
import pandas as pd
from pyhive import hive

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("sync")

HIVE_HOST = os.environ.get("HIVE_HOST", "hive-server")
HIVE_PORT = int(os.environ.get("HIVE_PORT", "10000"))
HIVE_USER = os.environ.get("HIVE_USER", "hive")

CH_HOST = os.environ.get("CLICKHOUSE_HOST", "clickhouse")
CH_PORT = int(os.environ.get("CLICKHOUSE_PORT", "8123"))
CH_USER = os.environ.get("CLICKHOUSE_USER", "ydsh")
CH_PASSWORD = os.environ.get("CLICKHOUSE_PASSWORD", "")
CH_DB = os.environ.get("CLICKHOUSE_DB", "ydsh_ads")


# ============================================================
# 表映射：CH 表名 → (Hive 表名, 列清单)
# 列清单必须与 01_schema.sql 的建表列顺序一致
# ============================================================
TABLES = {
    "screen_kpi": (
        "ydsh_ads.ads_screen_kpi",
        ["total_gmv", "order_cnt", "aov", "buyer_cnt", "avg_pay"],
    ),
    "sales_daily": (
        "ydsh_ads.ads_sales_daily_report",
        ["order_date", "order_cnt", "valid_order_cnt", "gmv", "aov", "buyer_cnt",
         "item_qty", "dod_pct", "wow_pct", "ma7", "ma30"],
    ),
    "category_topn": (
        "ydsh_ads.ads_category_topn",
        ["rank_no", "category", "order_cnt", "item_qty", "gmv", "gmv_share_pct", "aov"],
    ),
    "region_rank": (
        "ydsh_ads.ads_region_rank",
        ["rank_no", "province", "gmv", "buyer_cnt", "order_cnt", "aov", "share_pct"],
    ),
    "paytype_share": (
        "ydsh_ads.ads_paytype_share",
        ["pay_type", "order_cnt", "gmv", "order_share_pct", "gmv_share_pct"],
    ),
    "hour_category_heat": (
        "ydsh_ads.ads_hour_category_heat",
        ["order_hour", "category", "order_cnt", "item_qty", "gmv"],
    ),
    "category_month_pivot": (
        "ydsh_ads.ads_category_month_pivot",
        ["ym", "cat_yundong", "cat_shuma", "cat_fushi", "cat_shipin",
         "cat_meizhuang", "cat_jiaju", "cat_muying", "cat_tushu", "total_gmv"],
    ),
    "store_rank": (
        "ydsh_ads.ads_store_rank",
        ["rank_no", "store_id", "store_name", "province", "city", "area",
         "order_cnt", "buyer_cnt", "gmv", "aov", "gmv_per_sqm", "share_pct"],
    ),
    "anomaly_daily": (
        "ydsh_ads.ads_anomaly_daily",
        ["order_date", "gmv", "ma30", "sd30", "z_score", "anomaly_type",
         "wow_pct", "order_cnt", "aov"],
    ),
    "channel_month_share": (
        "ydsh_ads.ads_channel_month_share",
        ["ym", "channel", "order_cnt", "gmv", "buyer_cnt", "share_pct"],
    ),
}


def hive_fetch(table: str, cols: list[str], timeout_s: int = 600) -> pd.DataFrame:
    """从 Hive 读一张表的指定列。"""
    conn = hive.Connection(host=HIVE_HOST, port=HIVE_PORT,
                           username=HIVE_USER, database="default")
    try:
        cur = conn.cursor()
        cur.execute(f"select {', '.join(cols)} from {table}")
        rows = cur.fetchall()
        return pd.DataFrame(rows, columns=cols)
    finally:
        conn.close()


def ch_client():
    return clickhouse_connect.get_client(
        host=CH_HOST, port=CH_PORT,
        username=CH_USER, password=CH_PASSWORD, database=CH_DB,
        connect_timeout=5, send_receive_timeout=60,
    )


def sync_one(client, name: str, hive_table: str, cols: list[str]) -> int:
    """同步单表：truncate → 读 Hive → 写入 → 记水位。返回行数。"""
    t0 = time.time()
    df = hive_fetch(hive_table, cols)

    # None / NaN 统一成 None，避免 ClickHouse 把 NaN 写进非 Nullable 列报错
    df = df.astype(object).where(pd.notna(df), None)

    client.command(f"truncate table {CH_DB}.{name}")
    if len(df):
        client.insert(f"{CH_DB}.{name}", df.values.tolist(), column_names=cols)

    client.insert(
        f"{CH_DB}.sync_meta",
        [[name, len(df), pd.Timestamp.now().to_pydatetime()]],
        column_names=["table_name", "row_count", "synced_at"],
    )
    log.info("  %-22s %5d 行  %5.1fs", name, len(df), time.time() - t0)
    return len(df)


def main(argv: list[str]) -> int:
    only = set(argv[1:]) if len(argv) > 1 else None
    targets = {k: v for k, v in TABLES.items() if only is None or k in only}
    if not targets:
        log.error("没有匹配的表：%s", only)
        return 2

    log.info("开始同步 %d 张表 → ClickHouse %s:%s/%s", len(targets), CH_HOST, CH_PORT, CH_DB)
    client = ch_client()
    total, failed = 0, []
    try:
        for name, (hive_table, cols) in targets.items():
            try:
                total += sync_one(client, name, hive_table, cols)
            except Exception as e:
                log.error("  %-22s 失败：%s: %s", name, type(e).__name__, e)
                failed.append(name)
    finally:
        client.close()

    log.info("同步完成：共 %d 行，失败 %d 张%s",
             total, len(failed), f"（{', '.join(failed)}）" if failed else "")
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
