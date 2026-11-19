# -*- coding: utf-8 -*-
"""
db.py —— 数据访问层（ClickHouse 优先，Hive 兜底）

设计原则：
  1. 大屏与问答都只走这一个出口，不在业务代码里散落连接逻辑
  2. ClickHouse 快（毫秒级），优先用；连不上自动降级到 Hive，保证演示不中断
  3. 所有查询统一加超时与行数上限，防止一条 SQL 拖垮服务
  4. 返回结构统一为 {columns, rows}，Decimal/datetime 全部转原生类型

为什么要有降级：
  现场演示最怕「ClickHouse 没同步数据」或「网络抖动」，
  降级到 Hive 虽然慢（秒级），但数据口径完全一致，图表不会空。
"""
import logging
import os
import time
from decimal import Decimal
from typing import Any

import pandas as pd

log = logging.getLogger("db")

CLICKHOUSE_HOST = os.environ.get("CLICKHOUSE_HOST", "clickhouse")
CLICKHOUSE_PORT = int(os.environ.get("CLICKHOUSE_PORT", "8123"))
CLICKHOUSE_USER = os.environ.get("CLICKHOUSE_USER", "ydsh")
CLICKHOUSE_PASSWORD = os.environ.get("CLICKHOUSE_PASSWORD", "")
CLICKHOUSE_DB = os.environ.get("CLICKHOUSE_DB", "ydsh_ads")

HIVE_HOST = os.environ.get("HIVE_HOST", "hive-server")
HIVE_PORT = int(os.environ.get("HIVE_PORT", "10000"))
HIVE_USER = os.environ.get("HIVE_USER", "hive")

QUERY_MAX_ROWS = int(os.environ.get("QUERY_MAX_ROWS", "500"))
API_TIMEOUT = int(os.environ.get("API_TIMEOUT", "30"))

# 最近一次查询走的引擎，供 /api/health 展示
_last_engine = "unknown"


# ============================================================
# 类型清洗：Decimal / numpy / NaN 都要能进 JSON
# ============================================================
def _native(v: Any) -> Any:
    if v is None:
        return None
    if isinstance(v, Decimal):
        return float(v)
    if isinstance(v, (pd.Timestamp,)):
        return v.isoformat()
    if hasattr(v, "isoformat"):
        return v.isoformat()
    # pandas 的 NaN / NaT
    try:
        if pd.isna(v):
            return None
    except (TypeError, ValueError):
        pass
    if hasattr(v, "item"):          # numpy 标量
        return v.item()
    return v


def to_records(df: pd.DataFrame) -> list[dict]:
    """DataFrame → 可直接 json 化的字典列表。"""
    return [
        {str(k): _native(v) for k, v in row.items()}
        for row in df.to_dict(orient="records")
    ]


# ============================================================
# ClickHouse
# ============================================================
def ch_query(sql: str, limit: int = QUERY_MAX_ROWS) -> pd.DataFrame:
    """查 ClickHouse（HTTP 接口，依赖 clickhouse-connect）。"""
    import clickhouse_connect

    client = clickhouse_connect.get_client(
        host=CLICKHOUSE_HOST,
        port=CLICKHOUSE_PORT,
        username=CLICKHOUSE_USER,
        password=CLICKHOUSE_PASSWORD,
        database=CLICKHOUSE_DB,
        connect_timeout=5,
        send_receive_timeout=API_TIMEOUT,
    )
    try:
        res = client.query(sql)
        df = pd.DataFrame(res.result_rows, columns=list(res.column_names))
        return df.head(limit)
    finally:
        client.close()


def ch_ping() -> tuple[bool, str]:
    try:
        df = ch_query("select 1 as ok", limit=1)
        return True, f"ClickHouse 正常（{len(df)} 行）"
    except Exception as e:
        return False, f"ClickHouse 不可用：{type(e).__name__}: {e}"


# ============================================================
# Hive（降级通道）
# ============================================================
def hive_query(sql: str, limit: int = QUERY_MAX_ROWS) -> pd.DataFrame:
    """查 Hive（pyhive）。"""
    from pyhive import hive

    conn = hive.Connection(host=HIVE_HOST, port=HIVE_PORT,
                           username=HIVE_USER, database="default")
    try:
        cur = conn.cursor()
        cur.execute(sql)
        rows = cur.fetchall()
        cols = [d[0].split(".")[-1] for d in cur.description] if cur.description else []
        return pd.DataFrame(rows, columns=cols).head(limit)
    finally:
        conn.close()


def hive_ping() -> tuple[bool, str]:
    try:
        hive_query("select 1 as ok", limit=1)
        return True, "Hive 正常"
    except Exception as e:
        return False, f"Hive 不可用：{type(e).__name__}: {e}"


# ============================================================
# 统一入口：优先 ClickHouse，失败降级 Hive
# ============================================================
# ClickHouse 表名 → Hive 表名（降级时改写 SQL 用）
CH_TO_HIVE = {
    "screen_kpi":          "ydsh_ads.ads_screen_kpi",
    "sales_daily":         "ydsh_ads.ads_sales_daily_report",
    "category_topn":       "ydsh_ads.ads_category_topn",
    "region_rank":         "ydsh_ads.ads_region_rank",
    "paytype_share":       "ydsh_ads.ads_paytype_share",
    "hour_category_heat":  "ydsh_ads.ads_hour_category_heat",
    "category_month_pivot":"ydsh_ads.ads_category_month_pivot",
    "store_rank":          "ydsh_ads.ads_store_rank",
    "anomaly_daily":       "ydsh_ads.ads_anomaly_daily",
    "channel_month_share": "ydsh_ads.ads_channel_month_share",
}


def query(table_key: str, sql_ch: str, sql_hive: str | None = None,
          limit: int = QUERY_MAX_ROWS) -> dict:
    """
    查一张 ADS 表。

    table_key: CH_TO_HIVE 的键，仅用于日志与降级判断
    sql_ch   : ClickHouse 版 SQL
    sql_hive : Hive 版 SQL（不传则按表名自动改写）
    """
    global _last_engine
    t0 = time.time()

    try:
        df = ch_query(sql_ch, limit)
        _last_engine = "clickhouse"
        return {
            "ok": True,
            "engine": "clickhouse",
            "elapsed_ms": round((time.time() - t0) * 1000, 1),
            "columns": list(df.columns),
            "rows": to_records(df),
            "row_count": len(df),
        }
    except Exception as e:
        log.warning("ClickHouse 查询失败，降级 Hive：%s", e)

    if sql_hive is None:
        hive_tbl = CH_TO_HIVE.get(table_key)
        if not hive_tbl:
            return {"ok": False, "error": f"ClickHouse 失败且无 Hive 降级表：{table_key}"}
        # 把 from <ch表名> 换成 Hive 的 库.表
        sql_hive = sql_ch.replace(f"from {table_key}", f"from {hive_tbl}")

    try:
        df = hive_query(sql_hive, limit)
        _last_engine = "hive"
        return {
            "ok": True,
            "engine": "hive",
            "elapsed_ms": round((time.time() - t0) * 1000, 1),
            "columns": list(df.columns),
            "rows": to_records(df),
            "row_count": len(df),
            "degraded": True,
        }
    except Exception as e:
        return {"ok": False, "error": f"两个引擎都失败：{type(e).__name__}: {e}"}


def engine_status() -> dict:
    ch_ok, ch_msg = ch_ping()
    hive_ok, hive_msg = hive_ping()
    return {
        "clickhouse": {"ok": ch_ok, "msg": ch_msg},
        "hive": {"ok": hive_ok, "msg": hive_msg},
        "last_engine": _last_engine,
    }
