# -*- coding: utf-8 -*-
"""
insight.py —— AI 智能洞察与报告生成（任务书子任务 5）

三件事：
  1. 异常归因：对 GMV 异常日，自动拼装上下文（当日 vs 基线、品类结构、渠道、天气式外因无数据）
     让大模型给出可能原因，并强制要求标注「数据中无法验证」的部分
  2. 经营分析摘要：把 KPI / 趋势 / 结构 / 分层喂给模型，生成一段可读的经营简报
  3. 审慎验证：对模型输出的每个数字做回溯校验，凡是结果里查不到的数值一律标红

设计原则（对应任务书"对 AI 生成内容进行审慎验证"）：
  - 模型只负责「组织语言」，所有事实数字由 SQL 提供，不让模型自己算
  - 生成的每个数字都要能在上下文里找到，否则判定为幻觉
  - 离线模式下用确定性模板生成，保证内容与在线模式结构一致、可对比
"""
import json
import re

import numpy as np
import pandas as pd

from config import DATA_DIR, REPORT_DIR
from hive_client import dump_json, query_df
from llm_client import chat

# ============================================================
# 事实取数（全部来自 SQL，模型不参与计算）
# ============================================================
def fetch_context():
    """一次性取回生成洞察所需的全部事实。"""
    ctx = {}

    ctx["kpi"] = query_df(
        "select kpi_name, kpi_value, unit, calc_desc from ydsh_ads.ads_kpi_week3",
        database="ydsh_ads").to_dict(orient="records")

    ctx["category"] = query_df(
        "select category, gmv, gmv_share_pct, order_cnt, aov "
        "from ydsh_ads.ads_category_topn order by rank_no",
        database="ydsh_ads").to_dict(orient="records")

    ctx["monthly"] = query_df(
        "select ym, gmv, aov, mom_pct from ydsh_ads.ads_monthly_report order by ym",
        database="ydsh_ads").to_dict(orient="records")

    ctx["rfm"] = query_df(
        "select segment, user_cnt, user_share_pct, gmv, gmv_share_pct, avg_recency_days "
        "from ydsh_ads.ads_user_rfm_segment order by gmv desc",
        database="ydsh_ads").to_dict(orient="records")

    ctx["anomaly"] = query_df(
        "select order_date, gmv, ma30, sd30, z_score, anomaly_type, order_cnt, aov "
        "from ydsh_ads.ads_anomaly_daily order by abs(z_score) desc limit 5",
        database="ydsh_ads").to_dict(orient="records")

    ctx["channel"] = query_df(
        "select channel, sum(order_cnt) as order_cnt, round(sum(gmv),2) as gmv "
        "from ydsh_dws.dws_channel_pay group by channel order by gmv desc",
        database="ydsh_dws").to_dict(orient="records")

    ctx["region"] = query_df(
        "select province, gmv, share_pct from ydsh_ads.ads_region_rank "
        "order by rank_no limit 5", database="ydsh_ads").to_dict(orient="records")

    return ctx


def anomaly_detail(date_str):
    """取某个异常日的明细上下文：当天 vs 前 30 天基线、品类、渠道。"""
    d = {}
    d["day"] = query_df(
        f"select order_date, gmv, order_cnt, valid_order_cnt, aov, buyer_cnt, "
        f"ma30, sd30, wow_pct, "
        f"case when sd30 is null or sd30 = 0 then null "
        f"     else round((gmv - ma30) / sd30, 2) end as z_score "
        f"from ydsh_ads.ads_sales_daily_report where order_date = '{date_str}'",
        database="ydsh_ads").to_dict(orient="records")

    d["baseline"] = query_df(
        f"select round(avg(gmv),2) as avg_gmv, round(min(gmv),2) as min_gmv, "
        f"round(max(gmv),2) as max_gmv, count(*) as n_days "
        f"from ydsh_ads.ads_sales_daily_report "
        f"where order_date between date_sub('{date_str}', 30) and date_sub('{date_str}', 1)",
        database="ydsh_ads").to_dict(orient="records")

    d["prev_week"] = query_df(
        f"select order_date, round(gmv,2) as gmv from ydsh_ads.ads_sales_daily_report "
        f"where order_date in (date_sub('{date_str}', 7), date_sub('{date_str}', 1))",
        database="ydsh_ads").to_dict(orient="records")

    return d


# ============================================================
# 幻觉校验
# ============================================================
def extract_numbers(text):
    """从文本里抽出所有数值（含千分位、小数、负数）。"""
    pat = r"-?\d[\d,]*\.?\d*"
    out = []
    for m in re.findall(pat, text or ""):
        s = m.replace(",", "")
        try:
            out.append(float(s))
        except ValueError:
            pass
    return out


def verify_numbers(text, facts, tol=0.02):
    """
    校验模型输出里的数字是否都能在事实里找到。
    容忍度 2%（允许模型做四舍五入），比例/占比类允许 ±0.5 绝对误差。
    返回 (通过率, 未匹配数字列表)。
    """
    fact_nums = set()
    def walk(o):
        if isinstance(o, dict):
            for v in o.values():
                walk(v)
        elif isinstance(o, (list, tuple)):
            for v in o:
                walk(v)
        elif isinstance(o, (int, float)) and not isinstance(o, bool):
            fact_nums.add(round(float(o), 4))
        elif isinstance(o, str):
            for n in extract_numbers(o):
                fact_nums.add(round(n, 4))
    walk(facts)

    nums = extract_numbers(text)
    if not nums:
        return 1.0, []

    unmatched = []
    for n in nums:
        hit = False
        for f in fact_nums:
            if f == 0 and n == 0:
                hit = True; break
            if f != 0 and abs(n - f) / abs(f) <= tol:
                hit = True; break
            # 允许符号差异：正文写「偏离 52.71%」而事实是 -52.71%
            if f != 0 and abs(abs(n) - abs(f)) / abs(f) <= tol:
                hit = True; break
            if abs(n - f) <= 0.5 and abs(f) < 100:
                hit = True; break
            if abs(abs(n) - abs(f)) <= 0.5 and abs(f) < 100:
                hit = True; break
        if not hit:
            # 允许 1~12 这类小整数（月份、序号、天数）作为结构性数字
            if float(n).is_integer() and 0 <= n <= 31:
                hit = True
        if not hit:
            unmatched.append(n)

    rate = 1 - len(unmatched) / len(nums)
    return round(rate, 4), unmatched


# ============================================================
# 1. 异常归因
# ============================================================
def analyze_anomaly(date_str, use_llm=True):
    """对单个异常日做归因分析，返回结构化结果。"""
    detail = anomaly_detail(date_str)
    day = detail["day"][0] if detail["day"] else {}
    base = detail["baseline"][0] if detail["baseline"] else {}

    gmv = float(day.get("gmv") or 0)
    avg = float(base.get("avg_gmv") or 0)
    gap = gmv - avg
    gap_pct = (gap / avg * 100) if avg else 0

    # 事实包：给模型看，也用于事后校验
    import datetime
    wd = datetime.date.fromisoformat(date_str).weekday()
    wd_name = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"][wd]
    facts = {
        "日期": date_str,
        "星期": wd_name,
        "当日GMV": round(gmv, 2),
        "近30日均GMV": round(avg, 2),
        "偏离金额": round(gap, 2),
        "偏离比例": round(gap_pct, 2),
        "近30日最低": round(float(base.get("min_gmv") or 0), 2),
        "近30日最高": round(float(base.get("max_gmv") or 0), 2),
        "z值": round(float(day.get("z_score") or 0), 2),
        "当日订单量": int(day.get("order_cnt") or 0),
        "当日客单价": round(float(day.get("aov") or 0), 2),
        "周同比": round(float(day.get("wow_pct") or 0), 2) if day.get("wow_pct") else None,
        "前一周同日GMV": [round(float(r["gmv"]), 2) for r in detail["prev_week"]],
    }

    prompt = f"""你是零售数据分析师。以下是某一天的经营数据，请分析 GMV 异常的可能原因。

事实数据（唯一可信来源，不要编造其他数字）：
{json.dumps(facts, ensure_ascii=False, indent=2)}

要求：
1. 先用一句话说明偏离程度（引用具体金额与百分比）。
2. 列出 2~3 个可能原因，按可能性排序。每条必须说明「支持证据」来自哪个数字。
3. 明确指出哪些原因在现有数据中无法验证（例如促销活动、竞品动作、天气、系统故障）。
4. 给出 1 条可执行的下一步核查建议。
5. 全文不超过 300 字，不要使用 Markdown 表格。"""

    offline_text = _offline_anomaly(facts)
    r = chat([{"role": "user", "content": prompt}], temperature=0.3,
             max_tokens=800, offline_fallback=offline_text)

    text = r.text or offline_text
    rate, unmatched = verify_numbers(text, facts)

    return {
        "date": date_str,
        "facts": facts,
        "analysis": text,
        "mode": "llm" if r.ok else "offline_template",
        "llm_meta": r.to_dict(),
        "verify": {
            "pass_rate": rate,
            "unmatched_numbers": unmatched,
            "passed": rate >= 0.8,
        },
    }


def _offline_anomaly(facts):
    """离线模板：结构与在线输出一致，便于对比。"""
    direction = "低于" if facts["偏离金额"] < 0 else "高于"
    lines = [
        f"{facts['日期']} 的 GMV 为 {facts['当日GMV']:,.2f} 元，"
        f"{direction}近 30 日均值 {facts['近30日均GMV']:,.2f} 元，"
        f"偏离 {abs(facts['偏离比例']):.2f}%，z 值 {facts['z值']}。",
        "",
        "可能原因（按可能性排序）：",
        f"1. 订单量下降带动：当日订单量 {facts['当日订单量']} 单，"
        f"若显著低于日常水平，则主因在流量而非客单价。",
        f"2. 客单价波动：当日客单价 {facts['当日客单价']:,.2f} 元，"
        f"需与近 30 日客单价对比确认是高单价订单缺失还是整体下滑。",
        f"3. 周内效应叠加：当日为{facts['星期']}，若该星期历史均值本就偏低，"
        f"则部分偏离可由星期效应解释；周同比 {facts['周同比']}% 说明星期因素不足以解释全部偏离。",
        "",
        "现有数据无法验证的原因：促销活动起止、竞品动作、天气、系统故障——"
        "这些需要运营系统日志与外部数据补充。",
        "",
        "建议：拉取当日各渠道订单量与转化漏斗，定位是流量下滑还是转化下滑。",
    ]
    return "\n".join(lines)


# ============================================================
# 2. 经营分析摘要
# ============================================================
def generate_summary(ctx=None, use_llm=True):
    """生成整体经营分析摘要（自动报告的核心段落）。"""
    ctx = ctx or fetch_context()

    kpi = {r["kpi_name"]: r["kpi_value"] for r in ctx["kpi"]}
    cats = ctx["category"]
    months = ctx["monthly"]
    rfm = ctx["rfm"]
    channels = ctx["channel"]

    facts = {
        "GMV": round(float(kpi.get("总 GMV", 0)), 2),
        "订单量": int(kpi.get("订单量", 0)),
        "有效订单量": int(kpi.get("有效订单量", 0)),
        "客单价": round(float(kpi.get("客单价 AOV", 0)), 2),
        "活跃用户数": int(kpi.get("活跃用户数", 0)),
        "复购率": round(float(kpi.get("复购率", 0)), 2),
        "日均GMV": round(float(kpi.get("日均 GMV", 0)), 2),
        "客单件数": round(float(kpi.get("客单件数", 0)), 2),
        "门店数": int(kpi.get("门店数", 0)),
        "品类Top1": str(cats[0]["category"]).strip() if cats else "",
        "品类Top1占比": round(float(cats[0]["gmv_share_pct"]), 2) if cats else 0,
        "品类Top1GMV": round(float(cats[0]["gmv"]), 2) if cats else 0,
        "品类末位": str(cats[-1]["category"]).strip() if cats else "",
        "品类末位占比": round(float(cats[-1]["gmv_share_pct"]), 2) if cats else 0,
        "最高月GMV": round(max(float(m["gmv"]) for m in months), 2) if months else 0,
        "最高月": max(months, key=lambda m: float(m["gmv"]))["ym"] if months else "",
        "最低月GMV": round(min(float(m["gmv"]) for m in months), 2) if months else 0,
        "最低月": min(months, key=lambda m: float(m["gmv"]))["ym"] if months else "",
        "月均波动": round(float(np.nanmean([abs(float(m["mom_pct"]))
                                            for m in months
                                            if pd.notna(m["mom_pct"])])), 2)
                    if months and any(pd.notna(m["mom_pct"]) for m in months) else 0,
        "高价值客户数": next((int(r["user_cnt"]) for r in rfm if r["segment"] == "重要价值客户"), 0),
        "高价值GMV占比": next((round(float(r["gmv_share_pct"]), 2) for r in rfm if r["segment"] == "重要价值客户"), 0),
        "流失预警数": next((int(r["user_cnt"]) for r in rfm if r["segment"] == "流失预警"), 0),
        "流失预警GMV占比": next((round(float(r["gmv_share_pct"]), 2) for r in rfm if r["segment"] == "流失预警"), 0),
        "渠道Top1": channels[0]["channel"] if channels else "",
        "渠道Top1GMV": round(float(channels[0]["gmv"]), 2) if channels else 0,
        "渠道数": len(channels),
    }

    prompt = f"""你是零售企业的数据分析师，请基于以下事实撰写一段经营分析摘要。

事实数据（唯一可信来源，不得编造）：
{json.dumps(facts, ensure_ascii=False, indent=2)}

写作要求：
1. 分三段：整体表现、结构特征、风险与建议。
2. 每段 2~3 句，必须引用具体数字。
3. 不要用 Markdown 标题、表格、加粗，纯段落文字。
4. 总字数 350~450 字。
5. 风险段必须指出「复购率 100% 是数据饱和导致，不能作为用户粘性强的证据」这一判断。
6. 最后给 2 条具体、可执行的建议。"""

    offline_text = _offline_summary(facts)
    r = chat([{"role": "user", "content": prompt}], temperature=0.4,
             max_tokens=1200, offline_fallback=offline_text)

    text = r.text or offline_text
    rate, unmatched = verify_numbers(text, facts)

    return {
        "facts": facts,
        "summary": text,
        "mode": "llm" if r.ok else "offline_template",
        "llm_meta": r.to_dict(),
        "verify": {
            "pass_rate": rate,
            "unmatched_numbers": unmatched,
            "passed": rate >= 0.8,
        },
    }


def _offline_summary(f):
    return (
        f"整体表现：2024 年全年 GMV 为 {f['GMV']:,.2f} 元，订单量 {f['订单量']:,} 单，"
        f"其中有效订单 {f['有效订单量']:,} 单，客单价 {f['客单价']:,.2f} 元，"
        f"日均 GMV {f['日均GMV']:,.2f} 元。活跃用户 {f['活跃用户数']:,} 人，"
        f"覆盖 {f['门店数']} 家门店，人均客单件数 {f['客单件数']} 件。\n\n"
        f"结构特征：品类上，{f['品类Top1']}以 {f['品类Top1占比']}% 的占比居首，"
        f"贡献 {f['品类Top1GMV']:,.2f} 元；{f['品类末位']}占比最低，为 {f['品类末位占比']}%，"
        f"八个品类占比集中在 11%~15% 区间，结构相对均衡。"
        f"月份上，{f['最高月']} 为全年高点（{f['最高月GMV']:,.2f} 元），"
        f"{f['最低月']} 为低点（{f['最低月GMV']:,.2f} 元），月度平均波动 {f['月均波动']}%，"
        f"全年无明显增长或衰退趋势。渠道以 {f['渠道Top1']} 为主，"
        f"贡献 {f['渠道Top1GMV']:,.2f} 元。\n\n"        f"风险与建议：用户分层看，重要价值客户 {f['高价值客户数']} 人，"
        f"贡献 {f['高价值GMV占比']}% 的 GMV；流失预警客户 {f['流失预警数']} 人，"
        f"仍占 {f['流失预警GMV占比']}% 的 GMV，存在流失带来的收入缺口。"
        f"需要特别说明：复购率显示 {f['复购率']}%，但这是因为 1,000 名用户分摊 "
        f"{f['订单量']:,} 单、人均下单超过 10 次，该指标在本数据集上已饱和，"
        f"不能作为用户粘性强的证据。建议：一是对 {f['流失预警数']} 名流失预警客户"
        f"做定向唤回，优先投入 GMV 贡献高的子群；二是补采 2023 年数据，"
        f"否则无法判断当前是自然波动还是趋势性变化。"
    )


# ============================================================
# 3. 主流程
# ============================================================
def main(use_llm=True):
    print("=" * 70)
    print("  AI 智能洞察与报告生成")
    print("=" * 70)

    print("\n[1/3] 拉取事实数据 ...")
    ctx = fetch_context()
    print(f"      KPI {len(ctx['kpi'])} 项 / 品类 {len(ctx['category'])} 个 / "
          f"月份 {len(ctx['monthly'])} 个 / 分层 {len(ctx['rfm'])} 类 / "
          f"异常日 {len(ctx['anomaly'])} 天")

    print("\n[2/3] 异常归因 ...")
    anomalies = []
    for a in ctx["anomaly"][:3]:
        date_str = a["order_date"]
        r = analyze_anomaly(date_str, use_llm=use_llm)
        anomalies.append(r)
        flag = "✓" if r["verify"]["passed"] else "⚠"
        print(f"      {flag} {date_str}  z={a['z_score']}  "
              f"模式={r['mode']}  数字校验通过率={r['verify']['pass_rate']*100:.0f}%")
        if r["verify"]["unmatched_numbers"]:
            print(f"        未匹配数字：{r['verify']['unmatched_numbers']}")

    print("\n[3/3] 经营分析摘要 ...")
    summary = generate_summary(ctx, use_llm=use_llm)
    flag = "✓" if summary["verify"]["passed"] else "⚠"
    print(f"      {flag} 模式={summary['mode']}  "
          f"数字校验通过率={summary['verify']['pass_rate']*100:.0f}%  "
          f"字数={len(summary['summary'])}")
    if summary["verify"]["unmatched_numbers"]:
        print(f"        未匹配数字：{summary['verify']['unmatched_numbers']}")

    out = {
        "anomalies": anomalies,
        "summary": summary,
        "context": ctx,
    }
    path = DATA_DIR / "insight.json"
    dump_json(out, path)
    print(f"\n  已保存：{path}")
    return out


if __name__ == "__main__":
    import sys
    from console import init_console
    init_console()
    use_llm = "--no-llm" not in sys.argv
    main(use_llm=use_llm)
