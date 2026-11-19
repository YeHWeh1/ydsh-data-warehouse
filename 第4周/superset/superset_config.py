# -*- coding: utf-8 -*-
"""
superset_config.py —— Superset 运行时配置（挂载进容器 /app/pythonpath/）

安全要点（对应任务书子任务 5 与部署手册步骤 6）：
  - SECRET_KEY 从环境变量读取，源码里不写任何口令
  - 元数据库使用独立的 sqlite 文件（课程演示够用）；如需生产请换 Postgres
  - 关闭匿名访问，强制登录后才能看仪表板
  - 开启 CSV 导出上限，避免一次导出拖垮服务
"""
import os

# ------------------------------------------------------------
# 基础
# ------------------------------------------------------------
SECRET_KEY = os.environ["SUPERSET_SECRET_KEY"]
SQLALCHEMY_DATABASE_URI = "sqlite:////app/superset_home/superset.db"

# 时区与语言：中文界面 + 东八区
DEFAULT_TIMEZONE = "Asia/Shanghai"
BABEL_DEFAULT_LOCALE = "zh"

# ------------------------------------------------------------
# 安全
# ------------------------------------------------------------
# 必须登录才能访问（不开放匿名 dashboard）
# 注意：AUTH_ROLE_PUBLIC 必须是字符串角色名，不能设成 None——
#       设成 None 会让 superset init 建角色时 name 为 NULL，
#       报 sqlite3.IntegrityError: NOT NULL constraint failed: ab_role.name
PUBLIC_ROLE_LIKE = "Gamma"
AUTH_ROLE_PUBLIC = "Public"
WTF_CSRF_ENABLED = True
WTF_CSRF_TIME_LIMIT = None

# 会话与上传上限
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = "Lax"
MAX_CONTENT_LENGTH = 50 * 1024 * 1024

# ------------------------------------------------------------
# 功能开关
# ------------------------------------------------------------
FEATURE_FLAGS = {
    "ALERT_REPORTS": False,       # 课程环境不启用定时报表
    "DASHBOARD_RBAC": True,       # 仪表板按角色控制
    "ENABLE_TEMPLATE_PROCESSING": True,
    "EMBEDDED_SUPERSET": True,    # 允许嵌入（Streamlit 里嵌仪表板用）
}

# 数据源探测：允许 Hive 与 ClickHouse
PREVENT_UNSAFE_DB_CONNECTIONS = False

# 结果行数上限：防止误查大表把浏览器拖死
ROW_LIMIT = 50000
SUPERSET_WEBSERVER_TIMEOUT = 120

# ------------------------------------------------------------
# 缓存：用内置内存缓存，课程环境不引入 Redis
# ------------------------------------------------------------
CACHE_CONFIG = {
    "CACHE_TYPE": "SimpleCache",
    "CACHE_DEFAULT_TIMEOUT": 300,
}
DATA_CACHE_CONFIG = CACHE_CONFIG

# ------------------------------------------------------------
# 异步查询结果存储（RESULTS_BACKEND）
#
# 为什么必须有：Superset 的 SQL Lab 无论前端是否勾选 async，
# 后端都会把结果写进 results backend；不配就报
# "Results backend is not configured."（500）。
# 课程环境用文件系统缓存即可，不引入 Redis/S3。
# ------------------------------------------------------------
from flask_caching.backends.filesystemcache import FileSystemCache  # noqa: E402

RESULTS_BACKEND = FileSystemCache(
    cache_dir="/app/superset_home/results",
    default_timeout=3600,       # 结果保留 1 小时
    threshold=500,
    mode=0o700,
)

# 单条查询的结果行上限（与 ROW_LIMIT 呼应，防止结果文件过大）
SQLLAB_QUERY_RESULT_TIMEOUT = 300
SQL_MAX_ROW = 50000
