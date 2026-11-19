#!/bin/bash
# ============================================================
# 应用容器入口：同时拉起 API（8000）与 Streamlit（8501）
#
# 为什么不用 supervisor：只需要两个进程，加一层进程管理器
# 反而让日志与排障变复杂；这里用 & + wait，任一进程退出即整体退出，
# 配合 compose 的 restart 策略自动恢复。
# ============================================================
set -uo pipefail

echo "==> 启动 FastAPI（:8000）"
uvicorn main:app --host 0.0.0.0 --port 8000 --log-level info &
API_PID=$!

# 等 API 起来再起 Streamlit（Streamlit 启动时不做取数，但避免日志交错误导）
for _ in $(seq 1 30); do
    if curl -fsS http://127.0.0.1:8000/api/health >/dev/null 2>&1; then
        echo "    API 就绪"
        break
    fi
    sleep 1
done

echo "==> 启动 Streamlit（:8501）"
streamlit run app_streamlit.py \
    --server.port 8501 \
    --server.address 0.0.0.0 \
    --server.headless true \
    --browser.gatherUsageStats false &
ST_PID=$!

# 任一进程退出就把另一个也带走，避免「半死不活」的容器
trap 'kill -TERM $API_PID $ST_PID 2>/dev/null' TERM INT
wait -n
EXIT_CODE=$?
echo "==> 有进程退出（code=$EXIT_CODE），停止全部服务"
kill -TERM $API_PID $ST_PID 2>/dev/null
wait
exit $EXIT_CODE
