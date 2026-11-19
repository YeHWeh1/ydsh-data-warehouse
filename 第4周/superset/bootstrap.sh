#!/bin/bash
# ============================================================
# superset 容器入口脚本
#
# 首次启动要做的四件事（手册步骤 1 的自动化版本）：
#   1. 建管理员账号
#   2. 升级元数据库
#   3. 初始化角色与权限
#   4. 注册 Hive / ClickHouse 数据源（幂等：已存在则跳过）
# 之后启动 gunicorn 提供 Web 服务。
#
# 注意：
#   - 不要装 pyhive[hive]：它会拉 sasl 这个 C 扩展，superset 镜像没有 g++，
#     装不上会让整条 pip 命令失败；NOSASL 连接不需要 sasl。
#   - 注册数据源必须在 create_app() 之后再 import superset 的模型，
#     否则 config 读不到 app.config，会抛 "Working outside of application context"。
# ============================================================
set -euo pipefail

echo "==> [1/6] 安装数据库驱动（Hive / ClickHouse）"
pip install --no-cache-dir -q \
    "pyhive>=0.7.0" \
    "thrift>=0.16" \
    "thrift-sasl>=0.4" \
    "pure-sasl>=0.6" \
    "clickhouse-connect>=0.7" \
    "pymysql>=1.1" \
    || echo "驱动安装失败，Hive/ClickHouse 连接可能不可用（继续启动）"

echo "==> [2/6] 建管理员账号"
superset fab create-admin \
    --username "${SUPERSET_ADMIN_USER}" \
    --firstname Admin --lastname User \
    --email "${SUPERSET_ADMIN_EMAIL}" \
    --password "${SUPERSET_ADMIN_PASSWORD}" \
    || echo "管理员已存在，跳过"

echo "==> [3/6] 升级元数据库"
superset db upgrade

echo "==> [4/6] 初始化角色权限"
superset init

echo "==> [5/6] 注册数据源"
cat > /tmp/register_db.py <<'PY'
import os
from superset.app import create_app

app = create_app()

# 关键：必须在 create_app() 之后 import 模型，
# 否则 superset.models.helpers 在导入期就访问 app.config，报应用上下文错误
with app.app_context():
    from superset import db
    from superset.models.core import Database

    targets = [
        ("Hive 数仓（ydsh_ads）", os.environ["HIVE_CONN_URI"]),
        ("ClickHouse 加速层（ydsh_ads）", os.environ["CLICKHOUSE_CONN_URI"]),
    ]
    for name, uri in targets:
        exists = db.session.query(Database).filter_by(database_name=name).first()
        if exists:
            # 连接串可能因 .env 变更而变化，这里保持与当前环境一致
            exists.sqlalchemy_uri = uri
            print(f"    - {name} 已存在，已更新连接串")
            continue
        db.session.add(Database(
            database_name=name,
            sqlalchemy_uri=uri,
            expose_in_sqllab=True,
            allow_ctas=False,
            allow_cvas=False,
            allow_dml=False,
            allow_run_async=True,
            extra='{"metadata_params": {}, "engine_params": {}}',
        ))
        print(f"    - {name} 已注册")
    db.session.commit()
PY
python /tmp/register_db.py || echo "数据源注册失败（可在 UI 手动添加）"

echo "==> [6/6] 启动 Superset"
exec /usr/bin/run-server.sh
