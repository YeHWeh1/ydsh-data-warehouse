# -*- coding: utf-8 -*-
"""
add_filters.py —— 给 Superset 仪表板添加原生筛选器（native filter）

任务书要求：「将图表组合成一个完整的经营分析仪表板（Dashboard），
并配置筛选器与联动」。联动已配好，这里补筛选器。

加两个筛选器：
  1. 日期范围（time_range）—— 控制销售趋势图的时间窗
  2. 品类多选（value）—— 控制品类/门店相关图表

为什么直接改 json_metadata 而不是调 API：
  Superset 4.x 的 dashboard PUT 接口对 native_filter_configuration
  的校验很严，字段缺一个就 400；而它的结构本身是 List[Dict]，
  直接写元数据最稳，前端读的是同一份数据。

筛选器 id 用固定值（不随机），保证脚本可重复执行且幂等。
"""
import json
import os
import subprocess
import sys

DASH_ID = 1

# 两个筛选器的完整定义（字段取自 Superset 4.x 前端 hydrate 逻辑）
FILTERS = [
    {
        "id": "FILTER-date-range",
        "name": "日期范围",
        "filterType": "time_range",
        "type": "time_range",
        "targets": [{"datasetId": 1, "column": {"name": "order_date"}}],
        "defaultDataMask": {"filterState": {"value": "No filter"}, "extraFormData": {}},
        "cascadeParentIds": [],
        "scope": {"rootPath": ["ROOT_ID"], "excluded": []},
        "controlValues": {
            "enableEmptyFilter": False,
            "defaultToNoFilter": True,
            "defaultToLatest": False,
        },
        "description": "筛选销售趋势的时间范围",
    },
    {
        "id": "FILTER-category",
        "name": "品类",
        "filterType": "filter_select",
        "type": "filter_select",
        "targets": [
            {"datasetId": 2, "column": {"name": "category"}},
        ],
        "defaultDataMask": {"filterState": {"value": None}, "extraFormData": {}},
        "cascadeParentIds": [],
        "scope": {"rootPath": ["ROOT_ID"], "excluded": []},
        "controlValues": {
            "enableEmptyFilter": False,
            "multiSelect": True,
            "searchAllOptions": True,
            "inverseSelection": False,
        },
        "description": "按品类筛选图表",
    },
]


def main() -> int:
    # 用 base64 传 JSON：直接拼进 Python 源码会被当成字面量
    # （false/true/null 是 JSON 写法，Python 里叫 False/True/None），
    # 转义处理很容易出错，base64 最省事。
    import base64

    payload = base64.b64encode(
        json.dumps(FILTERS, ensure_ascii=False).encode("utf-8")
    ).decode("ascii")

    script = f'''
import base64, json
from superset.app import create_app
app = create_app()
with app.app_context():
    from superset import db
    from superset.models.dashboard import Dashboard

    filters = json.loads(base64.b64decode("{payload}").decode("utf-8"))

    dash = db.session.query(Dashboard).filter_by(id={DASH_ID}).first()
    if not dash:
        print("ERROR: dashboard {DASH_ID} not found")
        raise SystemExit(1)

    meta = json.loads(dash.json_metadata) if dash.json_metadata else {{}}
    meta["native_filter_configuration"] = filters
    dash.json_metadata = json.dumps(meta, ensure_ascii=False)
    db.session.commit()

    m2 = json.loads(db.session.query(Dashboard).filter_by(id={DASH_ID}).first().json_metadata)
    nf = m2.get("native_filter_configuration") or []
    print(f"FILTERS={{len(nf)}}")
    for f in nf:
        print(f"  - {{f['name']}} ({{f['filterType']}})")
'''
    p = subprocess.run(["docker", "exec", "-i", "superset", "python"],
                       input=script.encode("utf-8"),
                       capture_output=True, timeout=180)
    out = (p.stdout or b"").decode("utf-8", errors="replace")
    err = (p.stderr or b"").decode("utf-8", errors="replace")
    print(out.strip())
    if "FILTERS=" not in out:
        print("失败输出：", (out + err)[-400:])
        return 1
    return 0


if __name__ == "__main__":
    from console_shim import init_console
    init_console()
    sys.exit(main())
