# -*- coding: utf-8 -*-
"""
nl2sql.py —— NL2SQL 查询接口（任务书子任务 4）

链路：自然语言问题 → 注入 Schema 的 Prompt → 大模型生成 SQL
      → 安全校验（只读 / 表白名单 / 语法）→ 执行 → 返回结果

三层防护（对应手册 1.3 "大模型 SQL 不可控"难点）：
  1. Prompt 约束：把真实 Schema 注入，加 few-shot，禁止 DML/DDL
  2. 静态校验：正则 + AST 式检查，非 SELECT 一律拒绝；表名必须在白名单
  3. 执行兜底：LIMIT 自动追加、超时控制、异常转成可读错误

无 API Key 时：走「规则引擎」兜底，用关键词匹配生成等价 SQL，
保证现场演示不因断网而失败（同时把该 SQL 标记为 rule_based，可审计）。
"""
import json
import re
import time

from config import DATA_DIR, DB_ADS, DB_DWD, DB_DWS
from hive_client import query_df
from llm_client import chat
from schema_extract import load_schema, render_schema_text

MAX_ROWS = 200

# ============================================================
# Prompt
# ============================================================
SYSTEM_PROMPT = """你是 Hive SQL 专家，负责把业务问题翻译成可执行的 Hive SQL。

严格遵守：
1. 只输出一条 SELECT 语句，不要 INSERT / UPDATE / DELETE / DROP / CREATE / ALTER / TRUNCATE。
2. 表名必须带库名前缀（如 ydsh_dwd.dwd_order），字段名必须与给定 Schema 完全一致，不要臆造。
3. 金额类指标只统计 is_valid_amount = 1 的订单（订单事实表）。
4. 时间字段 order_date 是字符串 'yyyy-MM-dd'，比较时用字符串字面量。
5. 需要聚合时用 GROUP BY；需要排名用 ORDER BY + LIMIT；需要占比用窗口函数。
6. 结果列请用中文别名（用反引号包裹），便于业务方阅读。
7. 不要输出 SQL 之外的任何解释文字。"""

FEW_SHOT = """示例 1：
问题：广东省上月销售额是多少？
SQL：select round(sum(o.pay_amount), 2) as `销售额`
from ydsh_dwd.dwd_order o
join ydsh_dwd.dim_store s on o.store_id = s.store_id
where s.province = '广东省'
  and o.is_valid_amount = 1
  and substr(o.order_date, 1, 7) = '2024-12';

示例 2：
问题：销量最高的前 5 个品类
SQL：select category as `品类`, round(gmv, 2) as `销售额`
from ydsh_dws.dws_category_sales
order by gmv desc
limit 5;

示例 3：
问题：每个月的 GMV 是多少
SQL：select ym as `月份`, round(gmv, 2) as `GMV`
from ydsh_dws.dws_sales_month
order by ym;

示例 4：
问题：哪个门店卖得最好
SQL：select store_name as `门店`, round(gmv, 2) as `销售额`
from ydsh_ads.ads_store_rank
order by gmv desc
limit 1;"""


def build_messages(question, schema_text, history=None):
    """组装 messages。history 用于多轮追问。"""
    user = (
        f"可用表结构如下：\n\n{schema_text}\n\n"
        f"{FEW_SHOT}\n\n"
        f"现在请回答：\n问题：{question}\nSQL："
    )
    msgs = [{"role": "system", "content": SYSTEM_PROMPT}]
    if history:
        msgs.extend(history)
    msgs.append({"role": "user", "content": user})
    return msgs


# ============================================================
# SQL 抽取与清洗
# ============================================================
def clean_sql(text):
    """从模型输出里抠出纯 SQL（去掉 ```sql 包裹、解释文字、结尾分号）。"""
    if not text:
        return ""
    t = text.strip()
    # 去 markdown 代码块
    m = re.search(r"```(?:sql)?\s*(.*?)```", t, re.S | re.I)
    if m:
        t = m.group(1)
    # 从第一个 select/with 开始截取
    m = re.search(r"\b(select|with)\b", t, re.I)
    if m:
        t = t[m.start():]
    # 去掉尾部说明（分号之后的内容）
    if ";" in t:
        t = t.split(";")[0]
    t = t.replace("\n", " ").strip()
    t = re.sub(r"\s+", " ", t)
    return t


# ============================================================
# 静态安全校验
# ============================================================
FORBIDDEN = re.compile(
    r"(?<![\w.])"
    r"(insert|update|delete|drop|create|alter|truncate|grant|revoke|"
    r"load|overwrite|msck|analyze|set|add|dfs|shell|system)"
    r"(?![\w.])", re.I)


def validate_sql(sql, allowed_tables):
    """
    返回 (ok, errors, warnings)。
    检查项：
      - 非空、以 select/with 开头
      - 不含 DML/DDL 关键字
      - 引用的表全部在白名单内
      - 有库名前缀（防止落到 default 库）
      - 至少有一个 from
    """
    allowed = set(allowed_tables)
    errors, warnings = [], []
    if not sql:
        return False, ["SQL 为空"], warnings

    low = sql.lower().strip()
    if not (low.startswith("select") or low.startswith("with")):
        errors.append("不是以 SELECT / WITH 开头")

    bad = FORBIDDEN.search(sql)
    if bad:
        errors.append(f"包含被禁止的关键字：{bad.group(0).upper()}")

    # 表名检查：抓 from / join 后面的标识符（跳过子查询与函数调用）
    refs = re.findall(r"\b(?:from|join)\s+([`\w.]+)", sql, re.I)
    used = []
    for r in refs:
        name = r.strip("`").replace("`", "")
        if name.startswith("(") or name.endswith("("):   # 子查询 / 函数
            continue
        if not name:
            continue
        used.append(name)
        if name in allowed:
            continue
        short = name.split(".")[-1]
        if short in allowed:
            continue
        errors.append(f"表不在白名单：{name}")

    if not refs:
        errors.append("没有 FROM 子句")

    # 无库名前缀 → 警告（可能落到 default 库）
    for name in used:
        if "." not in name:
            warnings.append(f"表 {name} 未带库名前缀，可能解析失败")

    # 没有 LIMIT 且可能是明细查询 → 警告
    if "limit" not in low and not re.search(r"\b(count|sum|avg|max|min)\s*\(", low):
        warnings.append("明细查询未加 LIMIT，已自动追加")

    return (len(errors) == 0), errors, warnings


def enforce_limit(sql, max_rows=MAX_ROWS):
    """明细查询没有 LIMIT 时自动追加，防止一次拉全表。"""
    low = sql.lower()
    if "limit" in low:
        return sql
    if re.search(r"\b(count|sum|avg|max|min)\s*\(", low) and "group by" not in low:
        return sql   # 单行聚合，不用加
    return sql + f" limit {max_rows}"


# ============================================================
# 规则兜底引擎（无 API Key / 调用失败时使用）
# ============================================================
RULES = [
    # 顺序即优先级：越具体的规则放越前面，避免被泛化规则抢先匹配
    (["异常", "突降", "突增", "波动"],
     lambda q: "select order_date as `日期`, round(gmv, 2) as `GMV`, "
               "z_score as `z值`, anomaly_type as `类型` "
               "from ydsh_ads.ads_anomaly_daily order by abs(z_score) desc limit 10"),
    (["用户分层", "价值分层", "rfm", "客户分层", "分层"],
     lambda q: "select segment as `分层`, user_cnt as `用户数`, "
               "user_share_pct as `用户占比%`, round(gmv, 2) as `GMV`, "
               "gmv_share_pct as `GMV占比%` "
               "from ydsh_ads.ads_user_rfm_segment order by gmv desc"),
    (["复购"],
     lambda q: "select kpi_name as `指标`, round(kpi_value, 2) as `数值`, unit as `单位`, "
               "calc_desc as `口径` from ydsh_ads.ads_kpi_week3 "
               "where kpi_name like '%复购%'"),
    (["每月", "每个月", "月度", "月份", "月报", "按月"],
     lambda q: "select ym as `月份`, round(gmv, 2) as `GMV`, "
               "round(aov, 2) as `客单价`, mom_pct as `环比%` "
               "from ydsh_ads.ads_monthly_report order by ym"),
    (["每周", "周度", "周报", "按周"],
     lambda q: "select week_start as `周起点`, week_end as `周终点`, "
               "round(gmv, 2) as `GMV`, wow_pct as `周环比%` "
               "from ydsh_ads.ads_weekly_report order by week_start"),
    (["品类", "top", "前5", "前 5", "排行", "排名", "卖得最多"],
     lambda q: "select rank_no as `排名`, category as `品类`, "
               "round(gmv, 2) as `销售额`, gmv_share_pct as `占比%` "
               "from ydsh_ads.ads_category_topn order by rank_no limit 5"),
    (["门店"],
     lambda q: "select rank_no as `排名`, store_name as `门店`, province as `省份`, "
               "round(gmv, 2) as `销售额`, gmv_per_sqm as `坪效` "
               "from ydsh_ads.ads_store_rank order by rank_no limit 10"),
    (["渠道"],
     lambda q: "select channel as `渠道`, sum(order_cnt) as `订单量`, "
               "round(sum(gmv), 2) as `销售额` "
               "from ydsh_dws.dws_channel_pay group by channel order by 3 desc"),
    (["省份", "地区", "区域", "省"],
     lambda q: "select rank_no as `排名`, province as `省份`, "
               "round(gmv, 2) as `销售额`, share_pct as `占比%` "
               "from ydsh_ads.ads_region_rank order by rank_no limit 10"),
    (["gmv", "总销售", "总销售额", "营业额", "指标", "kpi"],
     lambda q: "select kpi_name as `指标`, round(kpi_value, 2) as `数值`, unit as `单位` "
               "from ydsh_ads.ads_kpi_week3 order by kpi_value desc"),
]


def rule_based_sql(question):
    """关键词匹配生成 SQL；匹配不到时返回 KPI 总览（最稳妥的默认）。"""
    q = question.lower()
    for keywords, fn in RULES:
        if any(k in q for k in keywords):
            return fn(question), True
    return ("select kpi_name as `指标`, round(kpi_value, 2) as `数值`, "
            "unit as `单位`, calc_desc as `口径` from ydsh_ads.ads_kpi_week3 "
            "order by kpi_name"), True


# ============================================================
# 主链路
# ============================================================
class NL2SQLResult:
    def __init__(self):
        self.question = ""
        self.sql = ""
        self.rows = []
        self.columns = []
        self.answer = ""
        self.elapsed = 0.0
        self.mode = ""            # llm / rule_based
        self.validation = {}
        self.error = ""
        self.llm_meta = {}

    def to_dict(self):
        return {
            "question": self.question,
            "sql": self.sql,
            "columns": self.columns,
            "rows": self.rows,
            "row_count": len(self.rows),
            "answer": self.answer,
            "elapsed_s": round(self.elapsed, 2),
            "mode": self.mode,
            "validation": self.validation,
            "error": self.error,
            "llm": self.llm_meta,
        }


def nl2sql(question, schema_text=None, schemas=None, use_llm=True, explain=True):
    """
    完整链路：问题 → SQL → 校验 → 执行 → 结果。

    explain=True 时会再调一次大模型把结果翻译成业务结论；
    离线模式下用模板拼装结论，保证输出不为空。
    """
    t0 = time.time()
    res = NL2SQLResult()
    res.question = question

    if schema_text is None:
        schemas = schemas or load_schema()
        schema_text = render_schema_text(schemas)
    allowed = {f"{s['db']}.{s['table']}" for s in (schemas or load_schema())}
    # 同时允许短表名
    allowed |= {t.split(".")[-1] for t in list(allowed)}
    # ---- 1. 生成 SQL ----
    if use_llm:
        msgs = build_messages(question, schema_text)
        llm_res = chat(msgs, temperature=0.0, max_tokens=800,
                       offline_fallback="__OFFLINE__")
        res.llm_meta = llm_res.to_dict()
        if llm_res.ok and llm_res.text and llm_res.text != "__OFFLINE__":
            res.sql = clean_sql(llm_res.text)
            res.mode = "llm"
        else:
            res.sql, _ = rule_based_sql(question)
            res.mode = "rule_based"
            res.llm_meta["fallback_reason"] = llm_res.error or "LLM 未返回有效内容"
    else:
        res.sql, _ = rule_based_sql(question)
        res.mode = "rule_based"

    # ---- 2. 校验 ----
    ok, errors, warnings = validate_sql(res.sql, allowed)
    res.validation = {"ok": ok, "errors": errors, "warnings": warnings}

    if not ok:
        # 模型生成的 SQL 不合法 → 降级到规则引擎，并记录原因（可审计）
        fallback_sql, _ = rule_based_sql(question)
        ok2, err2, warn2 = validate_sql(fallback_sql, allowed)
        res.validation["llm_sql_rejected"] = res.sql
        res.validation["errors"] = errors
        res.sql = fallback_sql
        res.mode = "rule_based(降级)"
        res.validation["fallback_ok"] = ok2
        if not ok2:
            res.error = "生成的 SQL 与兜底 SQL 均未通过校验"
            res.elapsed = time.time() - t0
            return res

    res.sql = enforce_limit(res.sql)

    # ---- 3. 执行 ----
    try:
        df = query_df(res.sql, database=DB_ADS)
        res.columns = list(df.columns)
        # NaN → None，保证 JSON 可序列化
        res.rows = json.loads(df.to_json(orient="records", force_ascii=False))
    except Exception as e:
        res.error = f"SQL 执行失败：{type(e).__name__}: {e}"
        res.elapsed = time.time() - t0
        return res

    # ---- 4. 结果解释 ----
    if explain:
        res.answer = explain_result(question, res.sql, res.columns, res.rows,
                                    use_llm=use_llm)

    res.elapsed = time.time() - t0
    return res


def explain_result(question, sql, columns, rows, use_llm=True):
    """把查询结果翻译成一句业务结论。离线时用模板。"""
    if not rows:
        return "查询没有返回数据，可能是筛选条件不匹配。"

    preview = json.dumps(rows[:10], ensure_ascii=False)
    if use_llm:
        msgs = [
            {"role": "system",
             "content": "你是数据分析师。根据用户问题与查询结果，用 1~2 句中文给出结论，"
                        "必须引用具体数字，不要编造结果里没有的数据。"},
            {"role": "user",
             "content": f"问题：{question}\nSQL：{sql}\n列：{columns}\n结果：{preview}"},
        ]
        r = chat(msgs, temperature=0.2, max_tokens=300, offline_fallback="")
        if r.ok and r.text:
            return r.text.strip()

    # 模板兜底
    if len(rows) == 1 and len(columns) <= 4:
        parts = [f"{c} 为 {rows[0].get(c)}" for c in columns]
        return f"查询结果为：" + "，".join(parts) + "。"
    return f"共返回 {len(rows)} 行，首行：{rows[0]}。"


# ============================================================
# 测试用例（任务书要求的典型问题 + 边界问题）
# ============================================================
TEST_CASES = [
    # (问题, 期望验证点)
    ("广东省上月销售额是多少？", "跨表 join + 地区筛选 + 月份筛选"),
    ("销量最高的前 5 个品类", "排序 + limit + 品类汇总表"),
    ("每个月的 GMV 是多少", "月度汇总 + 时间排序"),
    ("复购率是多少", "KPI 表 + 指标口径"),
    ("哪些天 GMV 出现异常波动", "异常检测表 + 排序"),
    ("用户价值分层各有多少人", "RFM 分层表 + 聚合"),
    ("哪个门店卖得最好", "门店排行 + limit 1"),
    ("各渠道的销售占比", "渠道汇总 + 占比"),
]


def run_tests(use_llm=True, verbose=True):
    """跑全部测试用例，返回结果列表（供报告与看板使用）。"""
    schemas = load_schema()
    schema_text = render_schema_text(schemas)
    results = []
    for i, (q, point) in enumerate(TEST_CASES, 1):
        if verbose:
            print(f"\n[{i}/{len(TEST_CASES)}] {q}")
            print(f"        验证点：{point}")
        r = nl2sql(q, schema_text=schema_text, schemas=schemas,
                   use_llm=use_llm, explain=True)
        d = r.to_dict()
        d["test_point"] = point
        d["passed"] = bool(r.rows) and not r.error
        results.append(d)

        if verbose:
            status = "✓" if d["passed"] else "✗"
            print(f"        {status} 模式={d['mode']}  返回 {d['row_count']} 行"
                  f"  耗时 {d['elapsed_s']}s")
            print(f"        SQL: {d['sql'][:120]}{'...' if len(d['sql']) > 120 else ''}")
            if d["error"]:
                print(f"        错误: {d['error']}")
    return results


if __name__ == "__main__":
    import sys
    from console import init_console
    init_console()

    print("=" * 70)
    print("  NL2SQL 接口测试")
    print("=" * 70)

    use_llm = "--no-llm" not in sys.argv
    results = run_tests(use_llm=use_llm)

    passed = sum(1 for r in results if r["passed"])
    print("\n" + "=" * 70)
    print(f"  结果：{passed}/{len(results)} 通过")
    by_mode = {}
    for r in results:
        by_mode[r["mode"]] = by_mode.get(r["mode"], 0) + 1
    print(f"  模式分布：{by_mode}")
    print("=" * 70)

    out = DATA_DIR / "nl2sql_tests.json"
    with open(out, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)
    print(f"  已保存：{out}")

    sys.exit(0 if passed == len(results) else 1)
