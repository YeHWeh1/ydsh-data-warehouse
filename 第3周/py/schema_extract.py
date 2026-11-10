# -*- coding: utf-8 -*-
"""
schema_extract.py —— 自动抽取 Hive 表 Schema（任务书子任务 4 第一项）

做法：读 metastore 的 `describe formatted` + `show create table`，
输出「表名 + 字段名 + 字段注释」的结构化元信息，再渲染成给大模型看的文本。

为什么不手写 Schema 字典：
  手写会与真实表结构漂移（第 2 周就出现过列名对不上的情况）。
  自动抽取保证 Prompt 里的字段名永远和 Hive 一致。
"""
import json
import re

from config import DATA_DIR, DB_ADS, DB_DWD, DB_DWS
from hive_client import query_rows

# 暴露给大模型的表（白名单，避免把 100 多张表全塞进 Prompt）
# 只放业务分析真正会用到的表，控制 Prompt 长度与模型幻觉概率
EXPOSED_TABLES = [
    # ---- DWD 明细层（最常用的分析入口）----
    (DB_DWD, "dwd_order", "订单事实表（一行一单）"),
    (DB_DWD, "dwd_order_item", "订单明细事实表（一行一商品）"),
    (DB_DWD, "dim_user", "用户维度表"),
    (DB_DWD, "dim_product", "商品维度表"),
    (DB_DWD, "dim_store", "门店维度表"),
    # ---- DWS 汇总层 ----
    (DB_DWS, "dws_sales_daily", "日销售汇总（第2周）"),
    (DB_DWS, "dws_category_sales", "品类销售汇总（第2周）"),
    (DB_DWS, "dws_region_sales", "区域销售汇总（第2周）"),
    (DB_DWS, "dws_store_sales", "门店销售汇总（第2周）"),
    (DB_DWS, "dws_channel_pay", "渠道×支付方式汇总（第2周）"),
    (DB_DWS, "dws_sales_week", "周销售汇总（第3周）"),
    (DB_DWS, "dws_sales_month", "月销售汇总（第3周）"),
    (DB_DWS, "dws_user_rfm", "用户 RFM 分层明细（第3周）"),
    # ---- ADS 应用层（直接可查的指标表）----
    (DB_ADS, "ads_kpi_week3", "核心 KPI 总览（第3周）"),
    (DB_ADS, "ads_sales_daily_report", "销售日报（含环比/周同比，第3周）"),
    (DB_ADS, "ads_category_topn", "品类销售排行（第3周）"),
    (DB_ADS, "ads_region_rank", "省份销售排行（第2周）"),
    (DB_ADS, "ads_store_rank", "门店销售排行（第2周）"),
    (DB_ADS, "ads_user_rfm_segment", "用户 RFM 分层汇总（第3周）"),
    (DB_ADS, "ads_monthly_report", "月报（第3周）"),
    (DB_ADS, "ads_anomaly_daily", "异常日明细（第3周）"),
]


def describe_table(db, table):
    """
    用 describe 抽取字段与注释（比 describe formatted 轻，且注释完整）。
    Hive 返回列：col_name / data_type / comment
    """
    _, rows = query_rows(f"describe `{db}`.`{table}`", database=db)
    cols = []
    for r in rows:
        if not r or r[0] is None:
            continue
        name = str(r[0]).strip()
        if not name or name.startswith("#") or name.lower() in ("col_name",):
            continue
        dtype = str(r[1]).strip() if len(r) > 1 and r[1] else ""
        comment = str(r[2]).strip() if len(r) > 2 and r[2] else ""
        if comment in ("NULL", "None", ""):
            comment = ""
        # 分区字段在 describe 里会重复出现，用 name 去重
        cols.append({"name": name, "type": dtype, "comment": comment})

    # 去重（保留第一次出现）
    seen, uniq = set(), []
    for c in cols:
        if c["name"] in seen:
            continue
        seen.add(c["name"])
        uniq.append(c)
    return uniq


def table_comment(db, table):
    """取表注释：describe formatted 里的 'Comment:' 行。"""
    try:
        _, rows = query_rows(f"describe formatted `{db}`.`{table}`", database=db)
        for r in rows:
            if r and r[0] and str(r[0]).strip().lower().startswith("comment"):
                val = str(r[1]).strip() if len(r) > 1 and r[1] else ""
                if val and val != "NULL":
                    return val
    except Exception:
        pass
    return ""


def row_count(db, table):
    try:
        _, rows = query_rows(f"select count(*) from `{db}`.`{table}`", database=db)
        return int(rows[0][0]) if rows else 0
    except Exception:
        return -1


def extract_all(include_counts=True):
    """抽取白名单内所有表的元信息，返回结构化 dict。"""
    result = []
    for db, table, desc in EXPOSED_TABLES:
        try:
            cols = describe_table(db, table)
            item = {
                "db": db,
                "table": table,
                "full_name": f"{db}.{table}",
                "desc": desc or table_comment(db, table),
                "columns": cols,
                "n_cols": len(cols),
            }
            if include_counts:
                item["rows"] = row_count(db, table)
            result.append(item)
            print(f"    ✓ {db}.{table:<24} {len(cols):>2} 字段"
                  + (f" / {item.get('rows'):,} 行" if include_counts else ""))
        except Exception as e:
            print(f"    ✗ {db}.{table}: {type(e).__name__}: {e}")
            result.append({"db": db, "table": table, "full_name": f"{db}.{table}",
                           "desc": desc, "columns": [], "n_cols": 0, "error": str(e)})
    return result


# ============================================================
# 渲染成 Prompt 文本
# ============================================================
def render_schema_text(schemas, max_tables=None):
    """
    渲染成紧凑的文本 Schema：
      ydsh_dwd.dwd_order — 订单事实表（15,000 行）
        order_id string  订单编号（主键）
        ...
    注释里的换行与多余空格会被压缩，控制 token 数。
    """
    lines = []
    items = schemas[:max_tables] if max_tables else schemas
    for s in items:
        if not s.get("columns"):
            continue
        head = f"{s['full_name']} — {s.get('desc', '')}"
        if s.get("rows", -1) > 0:
            head += f"（{s['rows']:,} 行）"
        lines.append(head)
        for c in s["columns"]:
            cm = re.sub(r"\s+", " ", c["comment"]).strip()
            lines.append(f"  {c['name']} {c['type']}" + (f"  {cm}" if cm else ""))
        lines.append("")
    return "\n".join(lines).rstrip()


def save_schema(schemas, path=None):
    """落盘 JSON，供 NL2SQL 与文档复用。"""
    path = path or (DATA_DIR / "schema.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(schemas, f, ensure_ascii=False, indent=2)
    # 同时存一份纯文本，方便人工核对
    txt = DATA_DIR / "schema.txt"
    txt.write_text(render_schema_text(schemas), encoding="utf-8")
    return path


def load_schema(path=None):
    path = path or (DATA_DIR / "schema.json")
    with open(path, encoding="utf-8") as f:
        return json.load(f)


if __name__ == "__main__":
    from console import init_console
    init_console()
    print("=" * 60)
    print("  抽取 Hive Schema")
    print("=" * 60)
    schemas = extract_all()
    p = save_schema(schemas)
    total_cols = sum(s["n_cols"] for s in schemas)
    print(f"\n  共 {len(schemas)} 张表 / {total_cols} 个字段")
    print(f"  已保存：{p}")
