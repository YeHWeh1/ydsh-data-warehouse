# -*- coding: utf-8 -*-
"""
app_streamlit.py —— 对话式经营问答（任务书子任务 3）

闭环：用户提问 → NL2SQL 生成 SQL → 执行 → 结果表格 + 图表渲染

设计要点（对应手册步骤 4 与任务书要求）：
  1. 复用第 3 周的 nl2sql 链路，不重复实现 Prompt 与安全校验
  2. 三种展示：SQL 原文（可审计）、结果表格、自动选型图表
  3. 图表用 ECharts 渲染（不是 Streamlit 原生图表），满足「ECharts 嵌入 Streamlit」
  4. 历史对话 + 加载状态 + 错误提示
  5. 无 API Key 时走规则引擎兜底，现场断网也能演示

图表自动选型规则（基于结果的列类型与行数）：
  - 1 行 × 1 列        → 大数字卡片
  - 含时间列 + 数值列   → 折线图
  - 1 个分类列 + 1 数值 → 柱状图
  - 2 列且第二列是百分比 → 饼图
  - 其它               → 仅表格
"""
import json
import os
import sys
import time
from pathlib import Path

import pandas as pd
import streamlit as st

# 第 3 周的模块通过 volume 挂载到 /app/week3_py
WEEK3_PY = Path(os.environ.get("WEEK3_PY", "/app/week3_py"))
if WEEK3_PY.exists() and str(WEEK3_PY) not in sys.path:
    sys.path.insert(0, str(WEEK3_PY))

st.set_page_config(
    page_title="云购生活 · 智能经营问答",
    page_icon="💬",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ============================================================
# 样式
# ============================================================
st.markdown("""
<style>
  .main .block-container { padding-top: 1.6rem; max-width: 1280px; }
  .hero {
    background: linear-gradient(120deg, rgba(34,211,238,0.14), rgba(16,34,64,0.05));
    border: 1px solid rgba(34,211,238,0.32);
    border-radius: 10px;
    padding: 18px 22px;
    margin-bottom: 18px;
  }
  .hero h1 { margin: 0; font-size: 26px; }
  .hero p  { margin: 6px 0 0; color: #64748b; font-size: 14px; }
  .sql-box {
    background: #0f172a; color: #7dd3fc;
    padding: 12px 14px; border-radius: 8px;
    font-family: Consolas, Monaco, monospace;
    font-size: 13px; line-height: 1.6;
    overflow-x: auto; white-space: pre-wrap; word-break: break-all;
  }
  .meta { color: #64748b; font-size: 12px; }
  .answer {
    background: rgba(34,211,238,0.08);
    border-left: 3px solid #22d3ee;
    padding: 12px 16px; border-radius: 0 8px 8px 0;
    margin: 10px 0; font-size: 15px; line-height: 1.7;
  }
</style>
""", unsafe_allow_html=True)

st.markdown("""
<div class="hero">
  <h1>💬 云购生活 · 智能经营问答</h1>
  <p>用自然语言提问，系统自动生成 SQL 并渲染图表 · 数据源 Hive 数仓（ydsh_ads）</p>
</div>
""", unsafe_allow_html=True)


# ============================================================
# 会话状态
# ============================================================
if "history" not in st.session_state:
    st.session_state.history = []      # [{q, sql, answer, mode, elapsed, rows, cols, error}]

# ============================================================
# 侧边栏
# ============================================================
with st.sidebar:
    st.header("⚙️ 设置")

    use_llm = st.toggle("启用大模型", value=True,
                        help="关闭后强制走规则引擎（关键词匹配），用于离线演示")
    show_sql = st.toggle("显示 SQL", value=True)
    show_table = st.toggle("显示结果表格", value=True)
    max_rows = st.slider("表格最多显示行数", 5, 200, 50, step=5)

    st.divider()
    st.subheader("📚 示例问题")
    EXAMPLES = [
        "总 GMV 是多少",
        "销量最高的前 5 个品类",
        "每个月的 GMV 是多少",
        "各省份的销售额排名",
        "各支付方式的销售占比",
        "哪些天 GMV 出现异常波动",
        "用户价值分层各有多少人",
        "哪个门店卖得最好",
    ]
    for q in EXAMPLES:
        if st.button(q, use_container_width=True, key=f"ex_{q}"):
            st.session_state.pending = q
            st.rerun()

    st.divider()
    if st.button("🗑️ 清空对话", use_container_width=True):
        st.session_state.history = []
        st.rerun()

    st.caption("密钥仅从环境变量读取，不落盘、不回显。")

    # 引擎与密钥状态（不显示明文）
    try:
        import requests
        h = requests.get("http://127.0.0.1:8000/api/health", timeout=3).json()
        ch_ok = h["engines"]["clickhouse"]["ok"]
        hv_ok = h["engines"]["hive"]["ok"]
        st.caption(f"ClickHouse：{'✅' if ch_ok else '❌'}　Hive：{'✅' if hv_ok else '❌'}")
    except Exception:
        st.caption("后端接口未就绪")


# ============================================================
# 图表自动选型
# ============================================================
TIME_HINTS = ("date", "day", "month", "week", "ym", "日期", "月份", "周", "时间")
PCT_HINTS = ("pct", "percent", "占比", "比例", "%")


def _is_time_col(name: str) -> bool:
    n = str(name).lower()
    return any(h in n for h in TIME_HINTS)


def _is_pct_col(name: str) -> bool:
    n = str(name).lower()
    return any(h in n for h in PCT_HINTS)


def pick_chart(df: pd.DataFrame) -> dict | None:
    """根据结果形状决定图表类型，返回 {type, x, y, name}。"""
    if df.empty:
        return None
    cols = list(df.columns)
    num_cols = [c for c in cols if pd.api.types.is_numeric_dtype(df[c])]
    cat_cols = [c for c in cols if c not in num_cols]

    # 单值：大数字
    if len(df) == 1 and len(num_cols) == 1:
        return {"type": "metric", "col": num_cols[0]}

    # 时间序列 → 折线
    tcols = [c for c in cols if _is_time_col(c)]
    if tcols and num_cols:
        return {"type": "line", "x": tcols[0], "y": num_cols[0]}

    # 分类 + 单数值 → 柱状 / 饼
    if cat_cols and len(num_cols) == 1:
        y = num_cols[0]
        if _is_pct_col(y) and 2 <= len(df) <= 8:
            return {"type": "pie", "x": cat_cols[0], "y": y}
        return {"type": "bar", "x": cat_cols[0], "y": y}

    # 分类 + 多个数值 → 分组柱状（取第一个数值列）
    if cat_cols and len(num_cols) >= 2:
        return {"type": "bar", "x": cat_cols[0], "y": num_cols[0]}

    return None


def render_chart(df: pd.DataFrame, spec: dict, key: str):
    """用 ECharts 渲染（通过 HTML + CDN，满足「ECharts 嵌入 Streamlit」）。"""
    if spec["type"] == "metric":
        val = df.iloc[0][spec["col"]]
        st.metric(spec["col"], f"{val:,.2f}" if isinstance(val, (int, float)) else str(val))
        return

    if spec["type"] == "pie":
        data = [{"name": str(r[spec["x"]]), "value": float(r[spec["y"]])}
                for _, r in df.iterrows()]
        option = {
            "tooltip": {"trigger": "item", "formatter": "{b}: {c} ({d}%)"},
            "legend": {"bottom": 0, "type": "scroll"},
            "series": [{
                "type": "pie", "radius": ["42%", "66%"], "center": ["50%", "46%"],
                "itemStyle": {"borderRadius": 4, "borderColor": "#fff", "borderWidth": 2},
                "label": {"formatter": "{b}\n{d}%"},
                "data": data,
            }],
        }
    elif spec["type"] == "line":
        option = {
            "tooltip": {"trigger": "axis"},
            "grid": {"left": 60, "right": 24, "top": 30, "bottom": 50},
            "xAxis": {"type": "category", "data": [str(v) for v in df[spec["x"]]],
                      "axisLabel": {"rotate": 30, "fontSize": 11}},
            "yAxis": {"type": "value"},
            "series": [{"type": "line", "smooth": True, "showSymbol": len(df) < 40,
                        "areaStyle": {"opacity": 0.18},
                        "data": [float(v) if pd.notna(v) else None for v in df[spec["y"]]]}],
        }
    else:  # bar
        option = {
            "tooltip": {"trigger": "axis"},
            "grid": {"left": 70, "right": 24, "top": 30, "bottom": 70},
            "xAxis": {"type": "category", "data": [str(v) for v in df[spec["x"]]],
                      "axisLabel": {"rotate": 30, "fontSize": 11}},
            "yAxis": {"type": "value"},
            "series": [{"type": "bar", "barMaxWidth": 44,
                        "itemStyle": {"borderRadius": [4, 4, 0, 0]},
                        "data": [float(v) if pd.notna(v) else None for v in df[spec["y"]]]}],
        }

    html = f"""
    <div id="chart_{key}" style="width:100%;height:380px"></div>
    <script src="https://cdn.jsdelivr.net/npm/echarts@5.5.1/dist/echarts.min.js"></script>
    <script>
      (function() {{
        var el = document.getElementById('chart_{key}');
        if (!el || !window.echarts) return;
        var chart = echarts.init(el);
        chart.setOption({json.dumps(option, ensure_ascii=False)});
        window.addEventListener('resize', function() {{ chart.resize(); }});
      }})();
    </script>
    """
    st.components.v1.html(html, height=400)


# ============================================================
# 主流程
# ============================================================
def run_query(question: str) -> dict:
    """调后端 NL2SQL 接口，返回统一结构。"""
    import requests
    t0 = time.time()
    try:
        r = requests.get(
            "http://127.0.0.1:8000/api/nl2sql",
            params={"q": question},
            timeout=90,
        )
        r.raise_for_status()
        return r.json()
    except Exception as e:
        return {
            "ok": False,
            "question": question,
            "error": f"后端调用失败：{type(e).__name__}: {e}",
            "elapsed_s": round(time.time() - t0, 2),
        }


# 渲染历史
for i, item in enumerate(st.session_state.history):
    with st.chat_message("user"):
        st.write(item["q"])
    with st.chat_message("assistant"):
        if item.get("error"):
            st.error(item["error"])
            continue
        if item.get("answer"):
            st.markdown(f'<div class="answer">{item["answer"]}</div>', unsafe_allow_html=True)
        if show_sql and item.get("sql"):
            st.markdown(f'<div class="sql-box">{item["sql"]}</div>', unsafe_allow_html=True)
        if show_table and item.get("rows"):
            df = pd.DataFrame(item["rows"])
            st.dataframe(df.head(max_rows), use_container_width=True, height=260)
            spec = pick_chart(df)
            if spec:
                render_chart(df, spec, f"h{i}")
        st.markdown(
            f'<div class="meta">模式 {item.get("mode", "-")} · '
            f'返回 {len(item.get("rows", []))} 行 · 耗时 {item.get("elapsed_s", 0)}s</div>',
            unsafe_allow_html=True,
        )

# 输入
pending = st.session_state.pop("pending", None)
question = st.chat_input("请输入你的问题，例如：广东省上月销售额是多少？")
question = pending or question

if question:
    with st.chat_message("user"):
        st.write(question)

    with st.chat_message("assistant"):
        with st.spinner("正在生成 SQL 并查询…"):
            res = run_query(question)

        item = {
            "q": question,
            "sql": res.get("sql", ""),
            "answer": res.get("answer", ""),
            "mode": res.get("mode", "-"),
            "elapsed_s": res.get("elapsed_s", 0),
            "rows": res.get("rows", []),
            "columns": res.get("columns", []),
            "error": res.get("error", ""),
        }
        st.session_state.history.append(item)

        if item["error"]:
            st.error(item["error"])
        else:
            if item["answer"]:
                st.markdown(f'<div class="answer">{item["answer"]}</div>',
                            unsafe_allow_html=True)
            if show_sql and item["sql"]:
                st.markdown(f'<div class="sql-box">{item["sql"]}</div>',
                            unsafe_allow_html=True)
            if show_table and item["rows"]:
                df = pd.DataFrame(item["rows"])
                st.dataframe(df.head(max_rows), use_container_width=True, height=260)
                spec = pick_chart(df)
                if spec:
                    render_chart(df, spec, f"new{len(st.session_state.history)}")
            st.markdown(
                f'<div class="meta">模式 {item["mode"]} · '
                f'返回 {len(item["rows"])} 行 · 耗时 {item["elapsed_s"]}s</div>',
                unsafe_allow_html=True,
            )
