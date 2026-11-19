# -*- coding: utf-8 -*-
"""
smoke_test.py —— 全栈冒烟测试（任务书「验证所有服务可用」的落地）

检查项：
  1. 6 个服务容器是否全部 running / healthy
  2. 5 个对外端口是否可访问
  3. 10 个数据接口是否返回真实数据（不是空数组）
  4. Superset 是否真能查出数据（调用 verify_superset 的逻辑）
  5. Streamlit 页面是否正常返回

输出：终端表格 + reports/冒烟测试结果.md（可贴进部署手册）
"""
import json
import os
import re
import shutil
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
REPORT = ROOT / "reports"

SERVICES = ["meta-mysql", "hive-metastore", "hive-server",
            "clickhouse", "superset", "dsh-app"]

PORTS = {
    3307: "MySQL（元数据库）",
    10000: "HiveServer2",
    8123: "ClickHouse HTTP",
    8088: "Superset",
    8000: "App API + 大屏",
    8501: "Streamlit 问答",
}

API_CHECKS = [
    ("/api/live",           "存活探针",      None),
    ("/api/health",         "引擎健康",      None),
    ("/api/kpi",            "KPI 卡片",      "data"),
    ("/api/trend",          "GMV 趋势",      "dates"),
    ("/api/category",       "品类排行",      "rows"),
    ("/api/region",         "区域分布",      "rows"),
    ("/api/paytype",        "支付占比",      "rows"),
    ("/api/heatmap",        "时段热力",      "data"),
    ("/api/category-month", "品类月度",      "months"),
    ("/api/store",          "门店排行",      "rows"),
    ("/api/channel",        "渠道月度",      "series"),
    ("/api/scatter",        "客单价散点",    "data"),
]


def sh(cmd: list[str], timeout=30) -> tuple[int, str]:
    """跑一条命令，返回 (returncode, 输出)。"""
    try:
        p = subprocess.run(cmd, capture_output=True, text=True,
                           timeout=timeout, encoding="utf-8", errors="replace")
        return p.returncode, (p.stdout or "") + (p.stderr or "")
    except Exception as e:
        return 1, f"{type(e).__name__}: {e}"


def check_services() -> list[dict]:
    """检查容器状态。"""
    results = []
    for name in SERVICES:
        code, out = sh(["docker", "inspect", name,
                        "--format", "{{.State.Status}}|{{if .State.Health}}{{.State.Health.Status}}{{else}}-{{end}}"])
        if code != 0:
            results.append({"name": name, "status": "缺失", "ok": False})
            continue
        status, health = (out.strip().split("|") + ["-"])[:2]
        ok = status == "running" and health in ("healthy", "-")
        results.append({"name": name, "status": f"{status}/{health}", "ok": ok})
    return results


def check_ports() -> list[dict]:
    """检查端口是否监听（用 TCP 连接探测，不依赖 netstat 输出格式）。"""
    import socket
    results = []
    for port, label in PORTS.items():
        s = socket.socket()
        s.settimeout(2)
        ok = s.connect_ex(("127.0.0.1", port)) == 0
        s.close()
        results.append({"port": port, "label": label, "ok": ok})
    return results


def check_apis(base="http://127.0.0.1:8000") -> list[dict]:
    """检查数据接口，并验证返回的内容不是空壳。"""
    results = []
    for path, label, key in API_CHECKS:
        t0 = time.time()
        try:
            r = requests.get(base + path, timeout=30)
            ms = round((time.time() - t0) * 1000)
            if r.status_code != 200:
                results.append({"path": path, "label": label, "ok": False,
                                "detail": f"HTTP {r.status_code}", "ms": ms})
                continue
            body = r.json()
            if body.get("ok") is False:
                results.append({"path": path, "label": label, "ok": False,
                                "detail": str(body.get("error"))[:80], "ms": ms})
                continue
            # 关键：不仅看 200，还要看有没有数据
            if key:
                val = body.get(key)
                empty = (val is None) or (isinstance(val, (list, dict)) and len(val) == 0)
                if empty:
                    results.append({"path": path, "label": label, "ok": False,
                                    "detail": f"{key} 为空", "ms": ms})
                    continue
                n = len(val)
                results.append({"path": path, "label": label, "ok": True,
                                "detail": f"{key}={n}", "ms": ms,
                                "engine": body.get("engine", "-")})
            else:
                results.append({"path": path, "label": label, "ok": True,
                                "detail": "OK", "ms": ms,
                                "engine": body.get("engine", "-")})
        except Exception as e:
            results.append({"path": path, "label": label, "ok": False,
                            "detail": f"{type(e).__name__}", "ms": 0})
    return results


def check_superset() -> list[dict]:
    """复用 verify_superset 的逻辑做真实查询验证。"""
    sys.path.insert(0, str(ROOT / "scripts"))
    try:
        import verify_superset as vs
    except Exception as e:
        return [{"name": "Superset", "ok": False, "detail": f"导入失败 {e}"}]

    pwd = os.environ.get("SUPERSET_ADMIN_PASSWORD", "")
    if not pwd:
        return [{"name": "Superset", "ok": False, "detail": "未提供口令，跳过"}]

    s = vs.login(os.environ.get("SUPERSET_ADMIN_USER", "admin"), pwd)
    if not s:
        return [{"name": "Superset 登录", "ok": False, "detail": "登录失败"}]

    out = [{"name": "Superset 登录", "ok": True, "detail": "成功"}]
    r = s.get(f"{vs.BASE}/api/v1/database/", timeout=30)
    dbs = r.json().get("result", []) if r.ok else []
    out.append({"name": "Superset 数据源", "ok": len(dbs) >= 2,
                "detail": f"{len(dbs)} 个"})

    probes = {
        "Hive 数仓（ydsh_ads）": "select count(*) as cnt from ydsh_ads.ads_kpi_week3",
        "ClickHouse 加速层（ydsh_ads）": "select count(*) as cnt from ydsh_ads.screen_kpi",
    }
    for d in dbs:
        sql = probes.get(d["database_name"])
        if not sql:
            continue
        try:
            r = s.post(f"{vs.BASE}/api/v1/sqllab/execute/",
                       json={"database_id": d["id"], "sql": sql, "json": True,
                             "runAsync": False, "schema": "ydsh_ads"},
                       timeout=180)
            body = r.json() if r.ok else {}
            ok = r.status_code == 200 and not body.get("errors") and body.get("data")
            out.append({"name": f"查询 {d['database_name']}",
                        "ok": bool(ok),
                        "detail": str(body.get("data"))[:40] if ok
                                  else str(body.get("errors"))[:60]})
        except Exception as e:
            out.append({"name": f"查询 {d['database_name']}", "ok": False,
                        "detail": type(e).__name__})
    return out


def check_charts() -> list[dict]:
    """验证 Superset 5 个图表是否都能查出数据（复用 verify_charts）。"""
    sys.path.insert(0, str(ROOT / "scripts"))
    try:
        import verify_charts as vc
    except Exception as e:
        return [{"name": "图表验证", "ok": False, "detail": f"导入失败 {e}"}]

    pwd = os.environ.get("SUPERSET_ADMIN_PASSWORD", "")
    if not pwd:
        return [{"name": "图表验证", "ok": False, "detail": "未提供口令，跳过"}]

    try:
        s = vc.login()
        r = s.get(f"{vc.BASE}/api/v1/chart/", timeout=30)
        charts = r.json().get("result", [])
    except Exception as e:
        return [{"name": "图表验证", "ok": False, "detail": type(e).__name__}]

    if not charts:
        return [{"name": "Superset 图表", "ok": False, "detail": "一个图表都没有"}]

    out = []
    for c in sorted(charts, key=lambda x: x["slice_name"]):
        try:
            res = vc.chart_data(s, c["id"])
            out.append({"name": c["slice_name"], "ok": res["ok"],
                        "detail": res["detail"]})
        except Exception as e:
            out.append({"name": c["slice_name"], "ok": False,
                        "detail": type(e).__name__})
    return out


def check_dashboard_render() -> dict:
    """
    用 CDP 打开 Superset 仪表板，确认 5 个图表全部画出且无「意外错误」。

    为什么必须做：/api/v1/chart/data 返回 200 不代表页面能看。
    实际踩过的坑：图表走旧接口 explore_json 返回 400、query_context 为 NULL、
    x_axis 与 groupby 重复导致 400——这些只有真正渲染才暴露。
    """
    node = os.environ.get("NODE_BIN") or shutil.which("node")
    if not node:
        # 项目里一直用的 node 运行时
        cand = r"C:\Users\wei\.dsh\dsh-runtimes\dsh-primary-runtime\dependencies\node\bin\node.exe"
        node = cand if Path(cand).exists() else None
    if not node:
        return {"ok": False, "detail": "找不到 node，跳过渲染验证"}

    # 先确认 Chrome CDP 可用
    try:
        requests.get("http://127.0.0.1:9222/json/version", timeout=3)
    except Exception:
        return {"ok": False,
                "detail": "Chrome 调试端口 9222 未开启（跳过；手动验证用 verify_dashboard.js）"}

    script = ROOT / "scripts" / "verify_dashboard.js"
    if not script.exists():
        return {"ok": False, "detail": "缺少 verify_dashboard.js"}

    p = subprocess.run([node, str(script)], capture_output=True, timeout=180)
    out = (p.stdout or b"").decode("utf-8", errors="replace")
    if p.returncode == 0:
        # 从输出里摘出关键行
        for line in out.splitlines():
            if "图表容器" in line:
                return {"ok": True, "detail": line.strip()}
        return {"ok": True, "detail": "渲染通过"}
    err = [l for l in out.splitlines() if "结论" in l or "✗" in l]
    return {"ok": False, "detail": (err[0] if err else "渲染验证失败")}


def check_filters() -> dict:
    """
    验证 Superset 仪表板筛选器是否配置且能渲染。

    两级检查：
      1. 元数据层：json_metadata.native_filter_configuration 非空
      2. 渲染层：浏览器里确实出现筛选器控件（需要 CDP，可选）
    """
    # ---- 1. 元数据检查 ----
    script = '''
import json
from superset.app import create_app
app = create_app()
with app.app_context():
    from superset import db
    from superset.models.dashboard import Dashboard
    d = db.session.query(Dashboard).filter_by(id=1).first()
    m = json.loads(d.json_metadata) if d and d.json_metadata else {}
    nf = m.get("native_filter_configuration") or []
    print("FILTER_COUNT=%d" % len(nf))
    for f in nf:
        print("NAME=%s" % f.get("name", "?"))
'''
    p = subprocess.run(["docker", "exec", "-i", "superset", "python"],
                       input=script.encode(), capture_output=True, timeout=120)
    out = (p.stdout or b"").decode("utf-8", errors="replace")
    if "FILTER_COUNT=" not in out:
        return {"ok": False, "detail": "筛选器元数据读取失败"}

    import re as _re
    n = int(_re.search(r"FILTER_COUNT=(\d+)", out).group(1))
    names = _re.findall(r"NAME=(.+)", out)
    if n < 2:
        return {"ok": False, "detail": f"筛选器只有 {n} 个（需 ≥2）"}

    # ---- 2. 渲染检查（有 CDP 才做）----
    node = os.environ.get("NODE_BIN") or shutil.which("node")
    if not node:
        cand = r"C:\Users\wei\.dsh\dsh-runtimes\dsh-primary-runtime\dependencies\node\bin\node.exe"
        node = cand if Path(cand).exists() else None
    if not node:
        return {"ok": True, "detail": f"{n} 个筛选器（{', '.join(names)}），未做渲染检查"}

    try:
        requests.get("http://127.0.0.1:9222/json/version", timeout=3)
    except Exception:
        return {"ok": True, "detail": f"{n} 个筛选器（{', '.join(names)}），Chrome 未开跳过渲染检查"}

    js = ROOT / "scripts" / "verify_filters.js"
    if not js.exists():
        return {"ok": True, "detail": f"{n} 个筛选器（{', '.join(names)}）"}

    r = subprocess.run([node, str(js)], capture_output=True, timeout=180)
    o = (r.stdout or b"").decode("utf-8", errors="replace")
    if r.returncode == 0:
        return {"ok": True, "detail": f"{n} 个筛选器（{', '.join(names)}），页面渲染正常"}
    line = [l for l in o.splitlines() if "结论" in l]
    return {"ok": False, "detail": line[0].strip() if line else "筛选器渲染失败"}


def check_streamlit() -> dict:
    try:
        r = requests.get("http://127.0.0.1:8501/", timeout=20)
        return {"ok": r.status_code == 200 and len(r.text) > 500,
                "detail": f"HTTP {r.status_code}, {len(r.text)} 字节"}
    except Exception as e:
        return {"ok": False, "detail": type(e).__name__}


def check_clickhouse_sync() -> dict:
    """确认 ClickHouse 里真的有数据（不是只有空表）。"""
    code, out = sh(["docker", "exec", "clickhouse", "sh", "-c",
                    'clickhouse-client --user "$CLICKHOUSE_USER" '
                    '--password "$CLICKHOUSE_PASSWORD" '
                    '--query "select sum(row_count) from ydsh_ads.sync_meta"'])
    if code != 0:
        return {"ok": False, "detail": "查询失败"}
    try:
        total = int(out.strip().splitlines()[-1])
        return {"ok": total > 0, "detail": f"{total} 行已同步"}
    except Exception:
        return {"ok": False, "detail": out.strip()[:60]}


def main() -> int:
    REPORT.mkdir(parents=True, exist_ok=True)
    lines, md = [], []

    def section(title):
        lines.append("")
        lines.append("=" * 66)
        lines.append(f"  {title}")
        lines.append("=" * 66)
        md.append(f"\n## {title}\n")

    # ---- 1. 容器 ----
    section("1. 服务容器")
    svc = check_services()
    md.append("| 容器 | 状态 | 结论 |\n|---|---|---|")
    for s in svc:
        mark = "✓" if s["ok"] else "✗"
        lines.append(f"  {mark} {s['name']:<16} {s['status']}")
        md.append(f"| {s['name']} | {s['status']} | {mark} |")

    # ---- 2. 端口 ----
    section("2. 对外端口")
    ports = check_ports()
    md.append("\n| 端口 | 服务 | 结论 |\n|---|---|---|")
    for p in ports:
        mark = "✓" if p["ok"] else "✗"
        lines.append(f"  {mark} {p['port']:<6} {p['label']}")
        md.append(f"| {p['port']} | {p['label']} | {mark} |")

    # ---- 3. 接口 ----
    section("3. 数据接口（含数据非空校验）")
    apis = check_apis()
    md.append("\n| 接口 | 说明 | 结果 | 引擎 | 耗时 |\n|---|---|---|---|---|")
    for a in apis:
        mark = "✓" if a["ok"] else "✗"
        lines.append(f"  {mark} {a['path']:<20} {a['label']:<10} {a['detail']:<16} {a['ms']}ms")
        md.append(f"| {a['path']} | {a['label']} | {mark} {a['detail']} | "
                  f"{a.get('engine', '-')} | {a['ms']}ms |")

    # ---- 4. ClickHouse 数据 ----
    section("4. ClickHouse 数据同步")
    ch = check_clickhouse_sync()
    lines.append(f"  {'✓' if ch['ok'] else '✗'} {ch['detail']}")
    md.append(f"\n{'✓' if ch['ok'] else '✗'} {ch['detail']}\n")

    # ---- 5. Superset ----
    section("5. Superset 真实查询")
    sp = check_superset()
    md.append("\n| 检查项 | 结论 | 说明 |\n|---|---|---|")
    for s in sp:
        mark = "✓" if s["ok"] else "✗"
        lines.append(f"  {mark} {s['name']:<28} {s['detail']}")
        md.append(f"| {s['name']} | {mark} | {s['detail']} |")

    # ---- 6. Superset 图表 ----
    section("6. Superset 图表出数验证")
    chs = check_charts()
    md.append("\n| 图表 | 结论 | 说明 |\n|---|---|---|")
    for c in chs:
        mark = "✓" if c["ok"] else "✗"
        lines.append(f"  {mark} {c['name']:<28} {c['detail']}")
        md.append(f"| {c['name']} | {mark} | {c['detail']} |")

    # ---- 7. Streamlit ----
    section("7. Streamlit 问答页")
    stl = check_streamlit()
    lines.append(f"  {'✓' if stl['ok'] else '✗'} {stl['detail']}")
    md.append(f"\n{'✓' if stl['ok'] else '✗'} {stl['detail']}\n")

    # ---- 8. 仪表板渲染 ----
    section("8. Superset 仪表板渲染")
    dash = check_dashboard_render()
    lines.append(f"  {'✓' if dash['ok'] else '✗'} {dash['detail']}")
    md.append(f"\n{'✓' if dash['ok'] else '✗'} {dash['detail']}\n")

    # ---- 9. 筛选器 ----
    section("9. Superset 筛选器")
    filt = check_filters()
    lines.append(f"  {'✓' if filt['ok'] else '✗'} {filt['detail']}")
    md.append(f"\n{'✓' if filt['ok'] else '✗'} {filt['detail']}\n")

    # ---- 汇总 ----
    total = (len(svc) + len(ports) + len(apis) + 1 + len(sp) + len(chs) + 1 + 1 + 1)
    passed = (sum(s["ok"] for s in svc) + sum(p["ok"] for p in ports)
              + sum(a["ok"] for a in apis) + ch["ok"]
              + sum(s["ok"] for s in sp) + sum(c["ok"] for c in chs)
              + stl["ok"] + (1 if dash["ok"] else 0) + (1 if filt["ok"] else 0))

    section("汇总")
    lines.append(f"  通过 {passed}/{total}")
    lines.append(f"  结论：{'全部通过' if passed == total else '存在失败项，见上'}")
    print("\n".join(lines))

    # ---- 写 Markdown 报告 ----
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    header = (f"# 第 4 周 · 全栈冒烟测试结果\n\n"
              f"> 生成时间：{now}\n"
              f"> 结论：**{passed}/{total} 通过**"
              f"{'（全部通过）' if passed == total else '（存在失败项）'}\n")
    (REPORT / "冒烟测试结果.md").write_text(header + "\n".join(md) + "\n",
                                            encoding="utf-8")
    print(f"\n  报告已写入：{REPORT / '冒烟测试结果.md'}")

    return 0 if passed == total else 1


if __name__ == "__main__":
    from console_shim import init_console  # noqa: E402
    init_console()
    sys.exit(main())
