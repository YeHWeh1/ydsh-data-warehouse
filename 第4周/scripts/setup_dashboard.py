# -*- coding: utf-8 -*-
"""
setup_dashboard.py —— 用 API 自动搭建 Superset 数据集、5 个图表与仪表板

为什么脚本化：
  任务书要求「至少 5 个图表 + 联动筛选」。手工点 UI 不可复现，
  换台机器就要重做一遍，答辩现场也无法演示「一键部署」。
  这里用 Superset REST API 把整条链路固化下来。

幂等性：
  每一步都先查是否存在（按名称），存在则跳过/更新，可重复执行。

前置：Superset 已启动、已注册 ClickHouse 数据源、ClickHouse 已同步数据。
用法：python setup_dashboard.py
"""
import json
import os
import re
import subprocess
import sys

import requests

BASE = os.environ.get("SUPERSET_URL", "http://127.0.0.1:8088")
DB_NAME = "ClickHouse 加速层（ydsh_ads）"
SCHEMA = "ydsh_ads"

# ------------------------------------------------------------
# 图表用的聚合指标：用「内联 adhoc metric」而不是保存到数据集的命名指标
#
# 为什么：Superset 4.x 的 /api/v1/dataset/{id}/metric/ 端点在本版本返回 404，
# 命名指标建不出来，图表引用它就会渲染失败。adhoc metric 直接写进图表 params，
# 不依赖任何预置指标，自包含、可复现。
# ------------------------------------------------------------
def metric(column: str, aggregate: str, label: str) -> dict:
    return {
        "expressionType": "SIMPLE",
        "column": {"column_name": column},
        "aggregate": aggregate,
        "label": label,
        "optionName": f"metric_{aggregate}_{column}",
    }


SUM_GMV = metric("gmv", "SUM", "GMV")
AVG_AOV = metric("aov", "AVG", "客单价")

# ------------------------------------------------------------
# 5 个图表定义（对应任务书要求）
# ------------------------------------------------------------
CHARTS = [
    {
        "name": "01 销售趋势（GMV 日线）",
        "table": "sales_daily",
        "viz_type": "line",
        "params": {
            "metrics": [SUM_GMV],
            # 关键：x_axis 必须同时出现在 groupby 里。
            # Superset 4.x 的 line 图表不会自动把 x_axis 加进 GROUP BY，
            # 只写 x_axis 会生成 "SELECT sum(gmv) FROM ... LIMIT 10000"，
            # 结果只有 1 行全局汇总，折线图退化成一条水平线。
            "groupby": ["order_date"],
            "time_grain_sqla": "P1D",
            "x_axis": "order_date",
            "granularity_sqla": "order_date",
            "adhoc_filters": [],
            # 必须显式按日期升序：不排序时 ClickHouse 返回的是任意顺序，
            # 折线图会按乱序把点连起来，画成锯齿乱线。
            "orderby": [["order_date", True]],
            "row_limit": 10000,
            "show_legend": True,
            "rich_tooltip": True,
            "line_interpolation": "smooth",
            "y_axis_format": "SMART_NUMBER",
        },
    },
    {
        "name": "02 品类销售排行",
        # 用 ECharts 版柱状图（echarts_timeseries_bar）。
        # 为什么不用 "bar"：Superset 4.1.2 里 "bar" 属于旧版图表，
        # 仪表板会用旧接口 /superset/explore_json/ 取数，而该接口在 4.1.2
        # 只读 POST body、前端却把 form_data 放 URL query，
        # 导致 json.decoder.JSONDecodeError → 400 → 图表显示「意外错误」。
        # echarts_timeseries_bar 走新接口 /api/v1/chart/data，不存在这个问题。
        "table": "category_topn",
        "viz_type": "echarts_timeseries_bar",
        "params": {
            "metrics": [SUM_GMV],
            # 注意：x_axis 与 groupby 不能同时写同一个列！
            # 两者都会进 queries[].columns，生成
            #   [BASE_AXIS(category), category]
            # 重复列让后端校验失败 → 400 → 图表显示「意外错误」。
            # echarts_timeseries_bar 用 x_axis 指定分类轴，groupby 留空。
            "x_axis": "category",
            "groupby": [],
            "xAxisForceCategorical": True,
            "adhoc_filters": [],
            "row_limit": 20,
            "order_desc": True,
            "show_legend": False,
            "y_axis_format": "SMART_NUMBER",
        },
    },
    {
        "name": "03 区域销售分布",
        # 省份排行：同样用新版柱状图，x_axis 指定省份，groupby 留空
        "table": "region_rank",
        "viz_type": "echarts_timeseries_bar",
        "params": {
            "metrics": [SUM_GMV],
            "x_axis": "province",
            "groupby": [],
            "xAxisForceCategorical": True,
            "adhoc_filters": [],
            "row_limit": 15,
            "order_desc": True,
            "show_legend": False,
            "y_axis_format": "SMART_NUMBER",
        },
    },
    {
        "name": "04 支付方式占比",
        "table": "paytype_share",
        "viz_type": "pie",
        "params": {
            # Superset 4.x 的 pie 图表后端读的是 metrics（复数数组），
            # 只写 metric（单数）会导致生成的 SQL 里没有聚合列，
            # 饼图退化成「只有分组列、没有数值」。两个都写上以兼容不同小版本。
            "metric": SUM_GMV,
            "metrics": [SUM_GMV],
            "groupby": ["pay_type"],
            "adhoc_filters": [],
            "row_limit": 20,
            "donut": True,
            "show_legend": True,
            "labels_outside": True,
            "label_type": "key_percent",
        },
    },
    {
        "name": "05 门店 TopN 排行",
        "table": "store_rank",
        "viz_type": "table",
        "params": {
            "metrics": [SUM_GMV, AVG_AOV],
            "groupby": ["store_name", "province"],
            "adhoc_filters": [],
            "row_limit": 10,
            "order_desc": True,
            "show_cell_bars": True,
            "include_search": True,
        },
    },
]

def login() -> requests.Session:
    """Session 登录 + 专用 CSRF token。"""
    user = os.environ.get("SUPERSET_ADMIN_USER", "admin")
    pwd = os.environ.get("SUPERSET_ADMIN_PASSWORD", "")
    if not pwd:
        raise SystemExit("未设置 SUPERSET_ADMIN_PASSWORD")

    s = requests.Session()
    r = s.get(f"{BASE}/login/", timeout=20)
    m = re.search(r'name="csrf_token" type="hidden" value="([^"]+)"', r.text)
    if not m:
        raise SystemExit("登录页找不到 csrf_token")

    r = s.post(f"{BASE}/login/",
               data={"username": user, "password": pwd, "csrf_token": m.group(1)},
               timeout=30, allow_redirects=True)
    if "/login" in r.url:
        raise SystemExit("登录失败")

    tok = s.get(f"{BASE}/api/v1/security/csrf_token/", timeout=20).json()["result"]
    s.headers.update({"X-CSRFToken": tok, "Referer": BASE,
                      "Content-Type": "application/json"})
    return s


def get_db_id(s: requests.Session) -> int:
    r = s.get(f"{BASE}/api/v1/database/", timeout=30)
    for d in r.json().get("result", []):
        if d["database_name"] == DB_NAME:
            return d["id"]
    raise SystemExit(f"找不到数据源：{DB_NAME}")


def ensure_dataset(s: requests.Session, db_id: int, table: str) -> int:
    """建数据集，返回 dataset id。已存在则直接返回。"""
    r = s.get(f"{BASE}/api/v1/dataset/", timeout=30)
    for d in r.json().get("result", []):
        if d["table_name"] == table and d.get("schema") == SCHEMA:
            print(f"    - 数据集 {table} 已存在（id={d['id']}）")
            fix_datetime_column(s, d["id"], table)
            return d["id"]

    body = {"database": db_id, "schema": SCHEMA, "table_name": table}
    r = s.post(f"{BASE}/api/v1/dataset/", data=json.dumps(body), timeout=60)
    if r.status_code not in (200, 201):
        raise SystemExit(f"建数据集失败 {table}: {r.status_code} {r.text[:300]}")

    ds_id = r.json()["id"]
    print(f"    - 数据集 {table} 已创建（id={ds_id}）")
    fix_datetime_column(s, ds_id, table)
    return ds_id


# 各数据集里应作为「时间列」的字段
DTTM_COL = {
    "sales_daily": "order_date",
}


def fix_datetime_column(s: requests.Session, ds_id: int, table: str):
    """
    把时间列标记为 datetime 列并设为 main_dttm_col。

    为什么必须做：
      ClickHouse 的 order_date 是 String 类型，Superset 同步列信息时
      不会把它识别成时间列（is_dttm=0，main_dttm_col=NULL）。
      而 line 图表要求「必须有时间列」，否则渲染时报
        Datetime column not provided as part table configuration
        and is required by this type of chart
      结果是图表框里只显示「意外错误」。这是仪表板一直渲染不出来的真正原因。

    为什么走数据库：
      Superset 4.x 没有 /dataset/{id}/column/ 这样的列级 REST 端点
      （调用会 404 并返回 HTML，导致 JSONDecodeError）。
      is_dttm 与 main_dttm_col 都是纯元数据，直接改表最可靠。
    """
    col = DTTM_COL.get(table)
    if not col:
        return

    script = f'''
from superset.app import create_app
app = create_app()
with app.app_context():
    from superset import db
    from sqlalchemy import text

    ds_id, col = {ds_id}, "{col}"

    db.session.execute(
        text("update table_columns set is_dttm = 1 "
             "where table_id = :d and column_name = :c"),
        {{"d": ds_id, "c": col}},
    )
    db.session.execute(
        text("update tables set main_dttm_col = :c where id = :d"),
        {{"d": ds_id, "c": col}},
    )
    db.session.commit()

    n = db.session.execute(
        text("select count(*) from table_columns "
             "where table_id = :d and column_name = :c and is_dttm = 1"),
        {{"d": ds_id, "c": col}},
    ).scalar()
    print(f"DTTM={{n}}")
'''
    try:
        p = subprocess.run(["docker", "exec", "-i", "superset", "python"],
                           input=script.encode(), capture_output=True, timeout=120)
        out = (p.stdout or b"").decode("utf-8", errors="replace")
        if "DTTM=1" in out:
            print(f"      时间列已设为 {col}")
        else:
            print(f"      时间列设置失败：{out[-160:]}")
    except Exception as e:
        print(f"      时间列设置异常：{type(e).__name__}: {e}")


def chart_uuid(chart_id: int) -> str:
    """
    从数据库读图表的 uuid（标准 8-4-4-4-12 字符串）。

    为什么不用 API：Superset 4.x 的 GET /api/v1/chart/{id} 不返回 uuid 字段
    （返回 null），但仪表板布局里的 meta.uuid 必须是它，前端靠它匹配图表。
    数据库里存的是 16 字节 binary，用 uuid.UUID(bytes=...) 还原成字符串。
    """
    script = f'''
import sqlite3, uuid
c = sqlite3.connect('/app/superset_home/superset.db')
row = c.execute('select uuid from slices where id = {chart_id}').fetchone()
if row and row[0]:
    v = row[0]
    print("UUID=" + (uuid.UUID(bytes=v) if isinstance(v, bytes) else uuid.UUID(v)).__str__())
else:
    print("UUID=")
'''
    try:
        p = subprocess.run(["docker", "exec", "-i", "superset", "python"],
                           input=script.encode(), capture_output=True, timeout=120)
        out = (p.stdout or b"").decode("utf-8", errors="replace")
        for line in out.splitlines():
            if line.startswith("UUID="):
                return line[5:].strip()
    except Exception:
        pass
    return ""


def ensure_chart(s: requests.Session, ds_id: int, spec: dict) -> tuple[int, str]:
    """建图表，返回 (chart_id, uuid)。"""
    r = s.get(f"{BASE}/api/v1/chart/", timeout=30)
    for c in r.json().get("result", []):
        if c["slice_name"] == spec["name"]:
            print(f"    - 图表 {spec['name']} 已存在（id={c['id']}）")
            return c["id"], chart_uuid(c["id"])

    params = dict(spec["params"])
    params["datasource"] = f"{ds_id}__table"
    params["viz_type"] = spec["viz_type"]

    # 去掉值为 None 的键。
    # Superset 前端判断「是否时间序列图」看的是 key 是否存在，
    # 而不是值是否为 null——把 time_grain_sqla 写成 None 仍然会被
    # 当成「有时间粒度」，bar 图于是要求数据集必须有 main_dttm_col，
    # 报 Datetime column not provided。必须彻底删掉这个键。
    params = {k: v for k, v in params.items() if v is not None}

    body = {
        "slice_name": spec["name"],
        "datasource_id": ds_id,
        "datasource_type": "table",
        "viz_type": spec["viz_type"],
        "params": json.dumps(params, ensure_ascii=False),
    }
    r = s.post(f"{BASE}/api/v1/chart/", data=json.dumps(body), timeout=60)
    if r.status_code not in (200, 201):
        print(f"    ✗ 图表 {spec['name']} 创建失败：{r.status_code} {r.text[:200]}")
        return -1, ""

    cid = r.json()["id"]

    # 补写 query_context：
    # Superset 仪表板前端读的是 query_context（不是 params），
    # 缺失时图表组件在「取到配置、还没发数据请求」的阶段就抛
    # TypeError: Cannot read properties of undefined (reading 'length')，
    # 页面显示「意外错误」。POST /api/v1/chart/ 不会自动生成它，
    # 必须在创建后单独 PUT 一次。
    qc = {
        "datasource": {"id": ds_id, "type": "table"},
        "force": False,
        "queries": [{
            "filters": [],
            "extras": {"having": "", "where": ""},
            "applied_time_extras": {},
            "columns": params.get("groupby", []),
            "metrics": params.get("metrics", []),
            "orderby": params.get("orderby", []),
            "annotation_layers": [],
            "row_limit": params.get("row_limit", 1000),
            "series_limit": 0,
            "order_desc": params.get("order_desc", True),
            "url_params": {},
            "custom_params": {},
            "custom_form_data": {},
            "granularity": "all",
            "time_range": "No filter",
        }],
        "form_data": params,
        "result_format": "json",
        "result_type": "full",
    }
    s.put(f"{BASE}/api/v1/chart/{cid}",
          data=json.dumps({"query_context": json.dumps(qc, ensure_ascii=False)}),
          timeout=60)

    info = s.get(f"{BASE}/api/v1/chart/{cid}", timeout=30).json()["result"]
    uid = chart_uuid(cid)
    print(f"    - 图表 {spec['name']} 已创建（id={cid}, uuid={uid[:8]}…）")
    return cid, uid


def link_charts(dash_id: int, chart_ids: list[int]) -> bool:
    """
    在容器内用 SQLAlchemy 建立 dashboard ↔ chart 关联。

    Superset 的 REST API 在 4.x 不允许通过 PUT 改 charts 关联，
    但这是「仪表板能不能显示图表」的决定性一步，所以用数据库直连补齐。
    幂等：先删同 id 的旧关联再插入，重复执行不会重复。
    """
    script = f'''
from superset.app import create_app
app = create_app()
with app.app_context():
    from superset import db
    from sqlalchemy import text

    dash_id = {dash_id}
    want = {chart_ids}

    # 用原生 SQL 而不是 ORM：
    # ORM 查 Slice 时会连带加载一堆关系字段，在独立进程里容易触发
    # "Could not parse SQLAlchemy URL" / 关系加载异常。
    # dashboard_slices 是纯关联表（dashboard_id, slice_id），直连最稳。
    db.session.execute(
        text("delete from dashboard_slices where dashboard_id = :d"),
        {{"d": dash_id}},
    )
    n = 0
    for cid in want:
        exists = db.session.execute(
            text("select 1 from slices where id = :c"), {{"c": cid}}
        ).fetchone()
        if not exists:
            continue
        db.session.execute(
            text("insert into dashboard_slices (dashboard_id, slice_id) "
                 "values (:d, :c)"),
            {{"d": dash_id, "c": cid}},
        )
        n += 1
    db.session.commit()
    print(f"LINKED={{n}}")
'''
    try:
        p = subprocess.run(
            ["docker", "exec", "-i", "superset", "python"],
            input=script.encode("utf-8"),
            capture_output=True, timeout=180,
        )
        out = (p.stdout or b"").decode("utf-8", errors="replace")
        err = (p.stderr or b"").decode("utf-8", errors="replace")
        if "LINKED=" in out:
            return True
        print(f"      关联脚本输出：{(out + err)[-300:]}")
        return False
    except Exception as e:
        print(f"      关联脚本异常：{type(e).__name__}: {e}")
        return False


def ensure_dashboard(s: requests.Session, charts: list[tuple[int, str]]) -> int:
    """建仪表板，建立图表关联，并写入 v2 布局。charts 为 [(chart_id, uuid)]。"""
    name = "云购生活 · 经营分析仪表板"
    r = s.get(f"{BASE}/api/v1/dashboard/", timeout=30)
    dash_id = None
    for d in r.json().get("result", []):
        if d["dashboard_title"] == name:
            dash_id = d["id"]
            print(f"    - 仪表板已存在（id={dash_id}），更新布局")
            break

    if dash_id is None:
        r = s.post(f"{BASE}/api/v1/dashboard/",
                   data=json.dumps({"dashboard_title": name,
                                    "published": True}), timeout=60)
        if r.status_code not in (200, 201):
            raise SystemExit(f"建仪表板失败：{r.status_code} {r.text[:300]}")
        dash_id = r.json()["id"]
        print(f"    - 仪表板已创建（id={dash_id}）")

    valid = [c for c in charts if c[0] >= 0]
    chart_ids = [c[0] for c in valid]

    # ------------------------------------------------------------
    # 关键：必须建立 dashboard → chart 的关联（dashboard_slices 表）
    #
    # 只写 position_json 是不够的：那只是「布局描述」。
    # 前端拉取仪表板时会调 /api/v1/dashboard/{id}/charts，
    # 数据来自 dashboard_slices 关联表；关联为空 → 前端拿不到图表
    # → 页面显示 5 个空框、且不发起任何 chart/data 请求。
    #
    # 为什么走数据库而不是 API：Superset 4.x 的 DashboardPutSchema
    # 不接受 charts / dashboards 字段（PUT 会返回 "Unknown field"），
    # 而创建时用 POST 带 charts 又会被 position 校验拦住。
    # 直接在容器内用 SQLAlchemy 建关联，最稳且幂等。
    # ------------------------------------------------------------
    link_ok = link_charts(dash_id, chart_ids)
    if link_ok:
        print(f"    - 图表关联已建立（{len(valid)} 个）")
    else:
        print("      图表关联建立失败（仪表板会是空框）")

    # ------------------------------------------------------------
    # v2 布局
    #
    # 结构必须是 ROOT → GRID → ROW → CHART，**不要加 HEADER_ID**。
    #
    # 踩坑记录：前端 hydrate 逻辑是
    #   t = ROOT.children[0]
    #   t = (t.type === HEADER) ? x[x[HEADER].children[0]] : x[t]
    #   Z = x[t]; Z.children.push(...)
    # 一旦 ROOT.children[0] 是 HEADER 而 HEADER 自己没 children，
    # 取到的是 undefined 或被当成 GRID 用，最终 Z.children 为 undefined，
    # 抛 "TypeError: Cannot read properties of undefined (reading 'push')"，
    # 整页显示「意外错误」。
    # Superset 自己的默认布局（p.Zn/p._4/p.PV）也是 ROOT 直接挂 GRID，无 HEADER。
    # ------------------------------------------------------------
    position = {
        "DASHBOARD_VERSION_KEY": "v2",
        "ROOT_ID": {"children": ["GRID_ID"], "id": "ROOT_ID", "type": "ROOT"},
        "GRID_ID": {"children": [], "id": "GRID_ID",
                    "parents": ["ROOT_ID"], "type": "GRID"},
    }

    # 两两一行，宽 6 = 半屏，高 50
    #
    # meta.uuid 必须与图表的 uuid 一致！
    # 这是排查最久的一个坑：前端按 position 里的 meta.uuid 去匹配
    # /api/v1/dashboard/{id}/charts 返回的图表；uuid 缺失或不匹配时，
    # 图表组件拿到 undefined，抛
    # "TypeError: Cannot read properties of undefined (reading 'length')"，
    # 整个仪表板显示「意外错误」。chartId 只用于跳转编辑，不用于匹配。
    for i in range(0, len(valid), 2):
        row_id = f"ROW-{i // 2}"
        group = valid[i:i + 2]
        kids = [f"CHART-{c[0]}" for c in group]
        position["GRID_ID"]["children"].append(row_id)
        position[row_id] = {
            "children": kids, "id": row_id,
            "meta": {"background": "BACKGROUND_TRANSPARENT"},
            "parents": ["ROOT_ID", "GRID_ID"], "type": "ROW",
        }
        for cid, uid in group:
            position[f"CHART-{cid}"] = {
                "children": [], "id": f"CHART-{cid}",
                "meta": {"chartId": cid, "height": 50, "width": 6,
                         "sliceName": "", "uuid": uid},
                "parents": ["ROOT_ID", "GRID_ID", row_id],
                "type": "CHART",
            }

    r = s.put(f"{BASE}/api/v1/dashboard/{dash_id}",
              data=json.dumps({"position_json": json.dumps(position)}),
              timeout=60)
    if r.status_code not in (200, 201):
        print(f"      布局写入失败（图表已建，可手工排版）：{r.status_code} {r.text[:200]}")
    else:
        print(f"    - 布局已写入（{len(valid)} 个图表，含图表关联）")
    return dash_id


def main() -> int:
    print("=" * 62)
    print("  Superset 数据集 / 图表 / 仪表板 自动搭建")
    print("=" * 62)

    s = login()
    print("✓ 登录成功")

    db_id = get_db_id(s)
    print(f"✓ 数据源 id={db_id}（{DB_NAME}）")

    print("\n[1/3] 创建数据集")
    ds_map = {}
    for table in ["sales_daily", "category_topn", "region_rank",
                  "paytype_share", "store_rank"]:
        ds_map[table] = ensure_dataset(s, db_id, table)

    print("\n[2/3] 创建图表")
    charts = []
    for spec in CHARTS:
        ds_id = ds_map[spec["table"]]
        charts.append(ensure_chart(s, ds_id, spec))

    print("\n[3/3] 创建仪表板")
    dash_id = ensure_dashboard(s, charts)

    ok = sum(1 for c, _ in charts if c >= 0)
    print(f"\n{'=' * 62}")
    print(f"  完成：{ok}/{len(CHARTS)} 个图表，仪表板 id={dash_id}")
    print(f"  访问：{BASE}/superset/dashboard/{dash_id}/")
    print("=" * 62)
    return 0 if ok == len(CHARTS) else 1


if __name__ == "__main__":
    from console_shim import init_console
    init_console()
    sys.exit(main())
