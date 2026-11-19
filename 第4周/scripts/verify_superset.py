# -*- coding: utf-8 -*-
"""
verify_superset.py —— 验证 Superset 侧是否真的连通 Hive / ClickHouse

为什么单独验证：
  「数据源已注册」不等于「能查出数据」。驱动、连接串、库名任何一环错，
  UI 上都要等人点了才报错。这里用 Superset 自己的 API 做真实查询，提前暴露问题。

认证说明：
  实测 Bearer token 方式在这套配置下 /api/v1/me 返回 401（JWT 与 CSRF 组合问题），
  而 session cookie 方式正常，所以这里用「表单登录 + Session」，
  这也是 Superset Web UI 自己用的方式。

流程：
  1. 取登录页 csrf_token，表单登录，建立 Session
  2. 列出所有数据库连接
  3. 对每个连接调 /api/v1/sqllab/execute 跑一条真实查询
"""
import os
import re
import sys

import requests

BASE = os.environ.get("SUPERSET_URL", "http://127.0.0.1:8088")


def login(user: str, password: str) -> requests.Session | None:
    s = requests.Session()
    r = s.get(f"{BASE}/login/", timeout=20)
    r.raise_for_status()
    m = re.search(r'name="csrf_token" type="hidden" value="([^"]+)"', r.text)
    if not m:
        print("✗ 登录页找不到 csrf_token")
        return None

    r = s.post(f"{BASE}/login/",
               data={"username": user, "password": password,
                     "csrf_token": m.group(1)},
               timeout=30, allow_redirects=True)
    if r.status_code != 200 or "/login" in r.url:
        print(f"✗ 登录失败（{r.status_code} → {r.url}）")
        return None

    # POST 类 API 需要 X-CSRFToken 头。
    # 注意：不能用 cookie 里的 csrf_token（登录后才有、且格式不同），
    #       必须调 /api/v1/security/csrf_token/ 拿专用 token。
    r = s.get(f"{BASE}/api/v1/security/csrf_token/", timeout=20)
    if r.status_code == 200:
        s.headers.update({
            "X-CSRFToken": r.json().get("result", ""),
            "Referer": BASE,
        })
    return s


def main() -> int:
    user = os.environ.get("SUPERSET_ADMIN_USER", "admin")
    pwd = os.environ.get("SUPERSET_ADMIN_PASSWORD", "")
    if not pwd:
        print("未提供 SUPERSET_ADMIN_PASSWORD，跳过")
        return 2

    s = login(user, pwd)
    if not s:
        return 1
    print(f"✓ 登录成功（{user}）")

    # ---- 1. 列出数据源 ----
    r = s.get(f"{BASE}/api/v1/database/", timeout=30)
    r.raise_for_status()
    dbs = r.json().get("result", [])
    print(f"\n已注册数据源 {len(dbs)} 个：")
    for d in dbs:
        uri = d.get("sqlalchemy_uri", "") or ""
        head = uri.split("://")[0] if "://" in uri else "(已脱敏)"
        tail = uri.rsplit("/", 1)[-1] if "/" in uri else ""
        print(f"  - [{d['id']}] {d['database_name']}  协议={head}  库={tail}")

    if not dbs:
        print("✗ 没有可用的数据源")
        return 1

    # ---- 2. 真实查询 ----
    probes = {
        "Hive 数仓（ydsh_ads）": "select count(*) as cnt from ydsh_ads.ads_kpi_week3",
        "ClickHouse 加速层（ydsh_ads）": "select count(*) as cnt from ydsh_ads.screen_kpi",
    }

    print("\n真实查询验证（每张数据源跑一条 count）：")
    all_ok = True
    for d in dbs:
        name = d["database_name"]
        sql = probes.get(name)
        if not sql:
            print(f"  - {name}: 无预设探测语句，跳过")
            continue

        r = s.post(
            f"{BASE}/api/v1/sqllab/execute/",
            json={"database_id": d["id"], "sql": sql, "json": True,
                  "runAsync": False, "schema": "ydsh_ads"},
            timeout=180,
        )
        if r.status_code != 200:
            print(f"  ✗ {name}: HTTP {r.status_code}  {r.text[:260]}")
            all_ok = False
            continue

        body = r.json()
        if body.get("errors"):
            print(f"  ✗ {name}: {str(body['errors'])[:260]}")
            all_ok = False
            continue

        print(f"  ✓ {name}: {body.get('data')}")
        if body.get("status") not in (None, "success"):
            print(f"      状态异常：{body.get('status')}")
            all_ok = False

    print(f"\n结论：{'Hive 与 ClickHouse 均连通' if all_ok else '存在失败项'}")
    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
