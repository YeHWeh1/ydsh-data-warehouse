# ============================================================
# 第 4 周应用镜像
#
# 一个镜像同时跑两个进程：
#   - uvicorn  :8000  FastAPI 数据接口 + ECharts 大屏静态页
#   - streamlit :8501 对话式问答（NL2SQL）
# 由 entrypoint.sh 统一拉起（supervisord 会增加依赖，这里用最简单的 &
#
# 镜像选择 slim：不需要编译型依赖，pyhive/clickhouse-connect 都是纯 Python
# ============================================================
FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    TZ=Asia/Shanghai

WORKDIR /app

# curl 用于健康检查，tzdata 让日志时间与本地一致
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl tzdata \
    && rm -rf /var/lib/apt/lists/*

# 先装依赖（利用层缓存：改代码不必重装依赖）
COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

# 再拷代码
COPY . /app

# 非 root 运行：降低容器被突破后的影响面
RUN useradd -m -u 1000 appuser \
    && chown -R appuser:appuser /app
USER appuser

EXPOSE 8000 8501

HEALTHCHECK --interval=20s --timeout=5s --start-period=60s --retries=15 \
    CMD curl -fsS http://127.0.0.1:8000/api/live || exit 1

ENTRYPOINT ["/bin/bash", "/app/entrypoint.sh"]
