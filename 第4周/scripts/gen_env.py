# -*- coding: utf-8 -*-
"""生成 第4周/.env：随机强口令 + 继承根目录 .env 里已配置的大模型 Key。"""
import secrets
from pathlib import Path

ROOT = Path(r"E:/dsh project")
OUT = ROOT / "第4周/.env"

# 从根 .env 读取大模型 Key（已存在则沿用，不打印明文）
keys = {}
root_env = ROOT / ".env"
if root_env.exists():
    for line in root_env.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        keys[k.strip()] = v.strip()

content = f"""# ============================================================
# 第 4 周 环境变量（本文件已被 .gitignore 排除，切勿提交）
# 由 scripts/gen_env.py 生成；口令为随机值，请勿外传
# ============================================================

# ---------- 大模型 API 密钥 ----------
DEEPSEEK_API_KEY={keys.get('DEEPSEEK_API_KEY', '')}
DASHSCOPE_API_KEY={keys.get('DASHSCOPE_API_KEY', '')}
OPENAI_API_KEY={keys.get('OPENAI_API_KEY', '')}
LLM_PROVIDER={keys.get('LLM_PROVIDER', 'deepseek') or 'deepseek'}

# ---------- ClickHouse ----------
CLICKHOUSE_USER=ydsh
CLICKHOUSE_PASSWORD={secrets.token_urlsafe(24)}

# ---------- Superset ----------
SUPERSET_SECRET_KEY={secrets.token_urlsafe(48)}
SUPERSET_ADMIN_USER=admin
SUPERSET_ADMIN_PASSWORD={secrets.token_urlsafe(18)}
SUPERSET_ADMIN_EMAIL=admin@example.com
"""

OUT.write_text(content, encoding="utf-8")
print(f"已写入 {OUT}")

# 只回显口令长度，不回显明文
for line in content.splitlines():
    if "=" in line and not line.startswith("#"):
        k, v = line.split("=", 1)
        if "KEY" in k or "PASSWORD" in k or "SECRET" in k:
            print(f"  {k:26s} 长度 {len(v):3d} {'(空)' if not v else ''}")
