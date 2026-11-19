# -*- coding: utf-8 -*-
"""
verify_charts.py —— 验证 Superset 图表是否真的能查出数据

为什么必须单独验证：
  图表「创建成功」（HTTP 201）不代表「能渲染」。指标名写错、列名不存在、
  数据集未同步，都要等到浏览器打开才报错。这里直接调 Superset 的
  chart data 接口，把每个图表跑一遍真实查询。

判定标准：
  - HTTP 200
  - 返回的 data 里至少有一行（或 series 有数据点）
  - 没有 errors 字段
"""
import json
import os
import re
import sys

import requests

BASE = os.environ.get("SUPERSET_URL", "http://127.0.0.1:8088")


def login() -> requests.Session:
    user = os.environ.get("SUPERSET_ADMIN_USER", "admin")
    pwd = os.environ.get("SUPERSET_ADMIN_PASSWORD", "")
    if not pwd:
        raise SystemExit("未设置 SUPERSET_ADMIN_PASSWORD")

    s = requests.Session()
    r = s.get(f"{BASE}/login/", timeout=20)
    m = re.search(r'name="csrf_token" type="hidden" value="([^"]+)"', r.text)
    if not m:
        raise SystemExit("登录页找不到 csrf_token")
    s.post(f"{BASE}/login/",
           data={"username": user, "password": pwd, "csrf_token": m.group(1)},
           timeout=30, allow_redirects=True)
    tok = s.get(f"{BASE}/api/v1/security/csrf_token/", timeout=20).json()["result"]
    s.headers.update({"X-CSRFToken": tok, "Referer": BASE,
                      "Content-Type": "application/json"})
    return s


def chart_data(s: requests.Session, chart_id: int) -> dict:
    """取图表的查询结果（Superset 内部接口）。"""
    r = s.get(f"{BASE}/api/v1/chart/{chart_id}", timeout=30)
    if r.status_code != 200:
        return {"ok": False, "detail": f"取图表失败 HTTP {r.status_code}"}

    info = r.json()["result"]
    params = json.loads(info["params"])

    # Superset 4.x 的 /api/v1/chart/{id} 不返回 datasource_id 字段，
    # 但 params 里有 "datasource": "<id>__table"，从那里取。
    ds_raw = str(params.get("datasource", ""))
    if "__" in ds_raw:
        ds_id = int(ds_raw.split("__")[0])
    else:
        return {"ok": False, "detail": f"params 里没有 datasource：{ds_raw}"}

    # 时间粒度要用图表自己的配置，不能写死 "all"。
    # "all" 是 Superset 的特殊值，直接传给后端会被当成列名，
    # 报 Columns missing in dataset: ['all']。
    # 图表没配时间列时传 None（等价于不分时间粒度）。
    gran = params.get("granularity_sqla") or None

    # 分组列：groupby + x_axis 都要算进去。
    # echarts_timeseries_bar 把分类轴放在 x_axis，groupby 是空的，
    # 只取 groupby 会导致查询没有分组列、只返回 1 行全局汇总。
    cols = list(params.get("groupby") or [])
    x_axis = params.get("x_axis")
    if x_axis and x_axis not in cols:
        cols.append(x_axis)

    body = {
        "datasource": {"id": ds_id, "type": "table"},
        "queries": [{
            "granularity": gran,
            "time_range": "No filter",
            "filters": [],
            "extras": {"having": "", "where": ""},
            "applied_time_extras": {},
            "columns": cols,
            "metrics": params.get("metrics", []),
            # 用图表自己配的排序：不传的话 Superset 按默认顺序返回，
            # 折线图会按乱序连线（验证结果也会和真实图表不一致）
            "orderby": params.get("orderby", []),
            "annotation_layers": [],
            "row_limit": params.get("row_limit", 1000),
            "series_limit": 0,
            "order_desc": params.get("order_desc", True),
            "url_params": {},
            "custom_params": {},
            "custom_form_data": {},
        }],
        "form_data": params,
        "result_format": "json",
        "result_type": "full",
    }

    r = s.post(f"{BASE}/api/v1/chart/data", data=json.dumps(body), timeout=180)
    if r.status_code != 200:
        return {"ok": False, "detail": f"HTTP {r.status_code} {r.text[:200]}"}

    j = r.json()
    if j.get("errors"):
        return {"ok": False, "detail": str(j["errors"])[:200]}

    res = j.get("result", [])
    if not res:
        return {"ok": False, "detail": "result 为空"}

    q0 = res[0]
    data = q0.get("data") or []
    colnames = q0.get("colnames") or q0.get("columns") or []
    if not data:
        return {"ok": False, "detail": f"无数据（keys={list(q0.keys())}）"}

    # 关键校验：除了分组列，必须还有一个数值列。
    # 只看「有行」是不够的——饼图如果漏了 metrics，SQL 只 SELECT 分组列，
    # 依然会返回行，但图上是空饼。
    metric_names = {m.get("label") for m in params.get("metrics", []) if isinstance(m, dict)}
    metric_names |= {m for m in params.get("metrics", []) if isinstance(m, str)}
    has_value = any(c in metric_names for c in colnames)
    if not has_value:
        return {"ok": False,
                "detail": f"缺数值列（列={colnames}，期望含 {metric_names}）"}

    return {"ok": True, "detail": f"{len(data)} 行", "sample": data[0]}


def main() -> int:
    print("=" * 62)
    print("  Superset 图表真实查询验证")
    print("=" * 62)

    s = login()
    print("✓ 登录成功\n")

    r = s.get(f"{BASE}/api/v1/chart/", timeout=30)
    charts = r.json().get("result", [])
    if not charts:
        print("✗ 没有任何图表")
        return 1

    print(f"共 {len(charts)} 个图表：\n")
    all_ok = True
    for c in sorted(charts, key=lambda x: x["slice_name"]):
        res = chart_data(s, c["id"])
        mark = "✓" if res["ok"] else "✗"
        if not res["ok"]:
            all_ok = False
        print(f"  {mark} {c['slice_name']:<28} {res['detail']}")
        if res["ok"] and res.get("sample"):
            print(f"      首行：{json.dumps(res['sample'], ensure_ascii=False)[:110]}")

    print(f"\n结论：{'全部图表可正常出数' if all_ok else '存在无法出数的图表'}")
    return 0 if all_ok else 1


if __name__ == "__main__":
    from console_shim import init_console
    init_console()
    sys.exit(main())
