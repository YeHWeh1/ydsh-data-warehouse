# -*- coding: utf-8 -*-
"""
main.py —— FastAPI 数据接口（ECharts 大屏 + Streamlit 共用的后端）

接口清单（全部 GET，便于浏览器直接调试与截图）：
  GET /api/health           健康检查（含两个引擎状态）
  GET /api/kpi              顶部 KPI 卡片
  GET /api/trend            GMV 日趋势 + 7 日均线 + 异常日标记
  GET /api/category         品类排行（柱状）
  GET /api/region           省份分布（地图）
  GET /api/paytype          支付方式占比（饼图）
  GET /api/heatmap          时段 × 品类 热力图
  GET /api/category-month   品类月度结构（堆叠面积）
  GET /api/store            TopN 门店表
  GET /api/channel          渠道月度结构（多系列折线）
  GET /api/scatter          客单价 × 订单量散点（相关性）
  GET /api/nl2sql?q=...     NL2SQL 问答（复用第 3 周链路）

设计约定：
  - 所有接口返回 {ok, engine, elapsed_ms, ...data}，前端只判断 ok
  - 统一 CORS，方便本地 file:// 打开大屏调试
  - 静态目录挂 /static，大屏页在 /
"""
import logging
import os
import sys
from pathlib import Path

from fastapi import FastAPI, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

import db

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)s %(name)s %(message)s")
log = logging.getLogger("api")

BASE_DIR = Path(__file__).resolve().parent
WEB_DIR = Path(os.environ.get("WEB_DIR", "/app/web"))

app = FastAPI(
    title="云购生活 · 经营分析数据接口",
    description="第 4 周可视化大屏与 NL2SQL 问答的统一后端",
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET"],
    allow_headers=["*"],
)


# ============================================================
# 基础
# ============================================================
@app.get("/api/health")
def health():
    st = db.engine_status()
    return {
        "ok": st["clickhouse"]["ok"] or st["hive"]["ok"],
        "service": "dsh-app",
        "engines": st,
    }


@app.get("/api/live")
def live():
    """
    轻量存活探针（给容器 healthcheck 用）。

    为什么单独做一个：/api/health 会真的去连 ClickHouse 与 Hive，
    而 Docker 健康检查默认 15 秒一次，等于每 15 秒往 Hive 发一条查询，
    日志里全是噪音、还白白占连接。这里只回进程是否活着。
    """
    return {"ok": True, "service": "dsh-app"}


def _fail(res: dict):
    return JSONResponse(status_code=503, content=res)


# ============================================================
# 1. KPI 卡片
# ============================================================
@app.get("/api/kpi")
def kpi():
    res = db.query("screen_kpi", "select * from screen_kpi limit 1")
    if not res["ok"]:
        return _fail(res)
    row = res["rows"][0] if res["rows"] else {}
    return {
        "ok": True, "engine": res["engine"], "elapsed_ms": res["elapsed_ms"],
        "data": {
            "total_gmv": row.get("total_gmv") or 0,
            "order_cnt": row.get("order_cnt") or 0,
            "aov": row.get("aov") or 0,
            "buyer_cnt": row.get("buyer_cnt") or 0,
            "avg_pay": row.get("avg_pay") or 0,
        },
    }


# ============================================================
# 2. GMV 日趋势 + 异常标记
# ============================================================
@app.get("/api/trend")
def trend(limit: int = Query(366, ge=30, le=400)):
    sql = (
        "select order_date, gmv, ma7, ma30, dod_pct, order_cnt "
        "from sales_daily order by order_date"
    )
    res = db.query("sales_daily", sql, limit=limit)
    if not res["ok"]:
        return _fail(res)

    # 异常日单独取，前端在折线图上打点
    ano = db.query("anomaly_daily",
                   "select order_date, gmv, z_score, anomaly_type from anomaly_daily "
                   "order by abs(z_score) desc limit 20")
    anomalies = ano["rows"] if ano["ok"] else []

    rows = res["rows"]
    return {
        "ok": True, "engine": res["engine"], "elapsed_ms": res["elapsed_ms"],
        "dates": [r["order_date"] for r in rows],
        "gmv": [r["gmv"] for r in rows],
        "ma7": [r.get("ma7") for r in rows],
        "ma30": [r.get("ma30") for r in rows],
        "order_cnt": [r.get("order_cnt") for r in rows],
        "anomalies": anomalies,
    }


# ============================================================
# 3. 品类排行
# ============================================================
@app.get("/api/category")
def category():
    res = db.query("category_topn",
                   "select rank_no, category, gmv, gmv_share_pct, order_cnt, aov "
                   "from category_topn order by rank_no")
    if not res["ok"]:
        return _fail(res)
    return {"ok": True, "engine": res["engine"], "elapsed_ms": res["elapsed_ms"],
            "rows": res["rows"]}


# ============================================================
# 4. 省份分布
# ============================================================
@app.get("/api/region")
def region():
    res = db.query("region_rank",
                   "select province, gmv, share_pct, buyer_cnt, order_cnt "
                   "from region_rank order by gmv desc")
    if not res["ok"]:
        return _fail(res)
    return {"ok": True, "engine": res["engine"], "elapsed_ms": res["elapsed_ms"],
            "rows": res["rows"],
            "max_gmv": max((r["gmv"] for r in res["rows"]), default=0),
            "min_gmv": min((r["gmv"] for r in res["rows"]), default=0)}


# ============================================================
# 5. 支付方式占比
# ============================================================
@app.get("/api/paytype")
def paytype():
    res = db.query("paytype_share",
                   "select pay_type, gmv, order_cnt, gmv_share_pct "
                   "from paytype_share order by gmv desc")
    if not res["ok"]:
        return _fail(res)
    return {"ok": True, "engine": res["engine"], "elapsed_ms": res["elapsed_ms"],
            "rows": res["rows"]}


# ============================================================
# 6. 时段 × 品类 热力图
# ============================================================
@app.get("/api/heatmap")
def heatmap(metric: str = Query("order_cnt", pattern="^(order_cnt|gmv|item_qty)$")):
    # 白名单校验在上面用正则完成，metric 不会拼进 SQL 之外的位置
    res = db.query("hour_category_heat",
                   f"select order_hour, category, {metric} as val "
                   f"from hour_category_heat order by order_hour, category")
    if not res["ok"]:
        return _fail(res)

    rows = res["rows"]
    hours = sorted({int(r["order_hour"]) for r in rows})
    cats = sorted({r["category"] for r in rows})
    # ECharts heatmap 数据格式：[x索引, y索引, 值]
    data = [[hours.index(int(r["order_hour"])), cats.index(r["category"]), r["val"]]
            for r in rows]
    vmax = max((r["val"] for r in rows), default=0)
    return {
        "ok": True, "engine": res["engine"], "elapsed_ms": res["elapsed_ms"],
        "hours": [f"{h:02d}时" for h in hours],
        "categories": cats,
        "data": data,
        "max": vmax,
        "metric": metric,
    }


# ============================================================
# 7. 品类月度结构
# ============================================================
@app.get("/api/category-month")
def category_month():
    res = db.query("category_month_pivot",
                   "select * from category_month_pivot order by ym")
    if not res["ok"]:
        return _fail(res)
    rows = res["rows"]
    series = ["cat_yundong", "cat_shuma", "cat_fushi", "cat_shipin",
              "cat_meizhuang", "cat_jiaju", "cat_muying", "cat_tushu"]
    names = {"cat_yundong": "运动户外", "cat_shuma": "数码家电", "cat_fushi": "服饰鞋包",
             "cat_shipin": "食品生鲜", "cat_meizhuang": "美妆个护", "cat_jiaju": "家居生活",
             "cat_muying": "母婴玩具", "cat_tushu": "图书文娱"}
    return {
        "ok": True, "engine": res["engine"], "elapsed_ms": res["elapsed_ms"],
        "months": [r["ym"] for r in rows],
        "series": [{"key": k, "name": names[k], "data": [r.get(k) for r in rows]}
                   for k in series],
    }


# ============================================================
# 8. 门店 TopN
# ============================================================
@app.get("/api/store")
def store(top: int = Query(10, ge=1, le=60)):
    res = db.query("store_rank",
                   f"select rank_no, store_name, province, city, gmv, aov, gmv_per_sqm "
                   f"from store_rank order by rank_no limit {top}")
    if not res["ok"]:
        return _fail(res)
    return {"ok": True, "engine": res["engine"], "elapsed_ms": res["elapsed_ms"],
            "rows": res["rows"]}


# ============================================================
# 9. 渠道月度结构
# ============================================================
@app.get("/api/channel")
def channel():
    res = db.query("channel_month_share",
                   "select ym, channel, gmv, share_pct from channel_month_share "
                   "order by ym, channel")
    if not res["ok"]:
        return _fail(res)
    rows = res["rows"]
    months = sorted({r["ym"] for r in rows})
    channels = sorted({r["channel"] for r in rows})
    lookup = {(r["ym"], r["channel"]): r["gmv"] for r in rows}
    return {
        "ok": True, "engine": res["engine"], "elapsed_ms": res["elapsed_ms"],
        "months": months,
        "series": [{"name": c, "data": [lookup.get((m, c)) for m in months]}
                   for c in channels],
    }


# ============================================================
# 10. 客单价 × 订单量 散点（日粒度，用于相关性观察）
# ============================================================
@app.get("/api/scatter")
def scatter():
    res = db.query("sales_daily",
                   "select order_date, order_cnt, aov, gmv from sales_daily "
                   "order by order_date")
    if not res["ok"]:
        return _fail(res)
    return {
        "ok": True, "engine": res["engine"], "elapsed_ms": res["elapsed_ms"],
        # [订单量, 客单价, 日期, GMV]
        "data": [[r["order_cnt"], r["aov"], r["order_date"], r["gmv"]]
                 for r in res["rows"]],
    }


# ============================================================
# 11. NL2SQL（复用第 3 周链路）
# ============================================================
@app.get("/api/nl2sql")
def nl2sql_api(q: str = Query(..., min_length=1, max_length=200)):
    try:
        w3_dir = os.environ.get("WEEK3_PY", "/app/week3/py")
        if w3_dir not in sys.path:
            sys.path.insert(0, w3_dir)
        import nl2sql as w3  # noqa: E402  第 3 周的模块

        r = w3.nl2sql(q, use_llm=True, explain=True)
        return {"ok": not r.error, **r.to_dict()}
    except Exception as e:
        log.exception("NL2SQL 失败")
        return {"ok": False, "error": f"{type(e).__name__}: {e}", "question": q}


# ============================================================
# 静态资源：大屏页
# ============================================================
if WEB_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(WEB_DIR)), name="static")


@app.get("/")
def index():
    page = WEB_DIR / "index.html"
    if page.exists():
        return FileResponse(str(page))
    return {"ok": True, "msg": "API 已启动，但未找到大屏页面", "web_dir": str(WEB_DIR)}
