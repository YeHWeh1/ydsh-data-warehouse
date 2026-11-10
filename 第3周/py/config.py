# -*- coding: utf-8 -*-
"""
config.py —— 第 3 周公共配置

口径与路径的唯一定义处：任何脚本都不应硬编码连接串或路径。
"""
import os
from pathlib import Path

# ---------- 目录 ----------
WEEK_DIR = Path(__file__).resolve().parent.parent          # 第3周/
PROJ_ROOT = WEEK_DIR.parent                                # 项目根
PY_DIR = WEEK_DIR / "py"
REPORT_DIR = WEEK_DIR / "reports"
DOC_DIR = WEEK_DIR / "docs"
SHOT_DIR = DOC_DIR / "screenshots"
FIG_DIR = REPORT_DIR / "figures"
DATA_DIR = REPORT_DIR / "data"

for d in (REPORT_DIR, DOC_DIR, SHOT_DIR, FIG_DIR, DATA_DIR):
    d.mkdir(parents=True, exist_ok=True)

# ---------- Hive ----------
HIVE_HOST = os.environ.get("HIVE_HOST", "localhost")
HIVE_PORT = int(os.environ.get("HIVE_PORT", "10000"))
HIVE_USER = os.environ.get("HIVE_USER", "hive")

DB_DWD = "ydsh_dwd"
DB_DWS = "ydsh_dws"
DB_ADS = "ydsh_ads"

# ---------- 口径常量（与 docs/第3周_指标定义文档.md 保持一致）----------
DATA_START = "2024-01-01"
DATA_END = "2024-12-31"
FORECAST_DAYS = 7          # 任务书要求：预测未来 7 天 GMV
ANOMALY_Z = 2.0            # 异常日阈值 |z| >= 2
RFM_SNAPSHOT = "2024-12-31"

# ---------- 大模型 ----------
ENV_FILE = PROJ_ROOT / ".env"
# 支持多家：DeepSeek / 通义千问 / OpenAI 兼容端点
LLM_PROVIDERS = {
    "deepseek": {
        "base_url": "https://api.deepseek.com",
        "model": "deepseek-chat",
        "key_env": "DEEPSEEK_API_KEY",
    },
    "dashscope": {
        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "model": "qwen-plus",
        "key_env": "DASHSCOPE_API_KEY",
    },
    "openai": {
        "base_url": "https://api.openai.com/v1",
        "model": "gpt-4o-mini",
        "key_env": "OPENAI_API_KEY",
    },
}
LLM_PROVIDER = os.environ.get("LLM_PROVIDER", "deepseek")
LLM_TIMEOUT = 60
LLM_MAX_RETRY = 2

# ---------- 图表 ----------
FIG_DPI = 150
CN_FONTS = ["Microsoft YaHei", "SimHei", "SimSun", "DejaVu Sans"]
