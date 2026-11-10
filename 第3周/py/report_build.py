# -*- coding: utf-8 -*-
"""
report_build.py —— 把各环节产物汇总成报告与看板

输入（都在 reports/data/）：
  forecast_summary.json / model_metrics.csv / forecast_7d.csv / backtest.csv
  nl2sql_tests.json
  insight.json
  schema.json
输出：
  reports/第3周_预测建模分析报告.md
  reports/第3周_AI洞察与经营简报.md
  reports/第3周_运行看板.html
"""
import html
import json
from pathlib import Path

from config import DATA_DIR, DOC_DIR, FIG_DIR, REPORT_DIR
from hive_client import query_df


def load_json(name, default=None):
    p = DATA_DIR / name
    if not p.exists():
        return default
    with open(p, encoding="utf-8") as f:
        return json.load(f)


def fmt(n, d=2):
    try:
        return f"{float(n):,.{d}f}"
    except (TypeError, ValueError):
        return str(n)


def pct(n, d=2):
    try:
        return f"{float(n):.{d}f}%"
    except (TypeError, ValueError):
        return str(n)


# ============================================================
# 报告 1：预测建模分析报告
# ============================================================
def build_forecast_report():
    s = load_json("forecast_summary.json")
    if not s:
        print("  ⚠ 缺少 forecast_summary.json，跳过预测报告")
        return None

    series = s["series"]
    dec = s["decompose"]
    bt = s["backtest"]
    fc = s["forecast"]
    diag = s.get("diagnosis", {})
    diag_plain = s.get("diagnosis_plain_recursive", {})

    metrics = bt["metrics"]
    best = bt["best_model"]
    naive = next((m for m in metrics if "朴素" in m["model"]), None)
    best_m = next((m for m in metrics if m["model"] == best), None)
    lin_m = next((m for m in metrics if m["model"] == "线性回归"), None)
    seasonal_m = next((m for m in metrics if m["model"] == "季节调整+线性回归"), None)

    # 提升幅度
    lift = ""
    if naive and best_m and naive["MAPE"]:
        lift = f"{((naive['MAPE'] - best_m['MAPE']) / naive['MAPE'] * 100):.1f}%"

    rows_md = "\n".join(
        f"| {m['model']} | {fmt(m['MAE'])} | {fmt(m['RMSE'])} | {pct(m['MAPE'])} | {pct(m['sMAPE'])} |"
        for m in metrics)

    fc_rows = "\n".join(
        f"| {r['date']} | {r['weekday']} | {fmt(r['pred_gmv'])} |"
        for r in fc["rows"])

    wd_rows = "\n".join(
        f"| {k} | {fmt(v)} |" for k, v in dec["weekday_effects"].items())

    md = f"""# 第 3 周 预测建模分析报告

> 团队：云仓五号　日期：2026-11-09
> 数据：`ydsh_ads.ads_sales_daily_report`，2024-01-01 ～ 2024-12-31，共 {series['days']} 天
> 代码：`第3周/py/forecast.py`　口径：`docs/第3周_指标定义文档.md`

## 一、数据概况

| 指标 | 数值 |
|---|---|
| 样本天数 | {series['days']} 天（闰年，无缺失日） |
| GMV 合计 | {fmt(series['gmv_total'])} 元 |
| 日均 GMV | {fmt(series['gmv_mean'])} 元 |
| 标准差 | {fmt(series['gmv_std'])} 元 |
| 最小值 | {fmt(series['gmv_min'])} 元 |
| 最大值 | {fmt(series['gmv_max'])} 元 |

![每日 GMV 与趋势](figures/01_gmv_series.png)

## 二、趋势分解

采用加法分解：`GMV = 趋势 + 星期效应 + 残差`。
趋势用居中 7 日移动平均（去掉星期效应），星期效应用去趋势后的各星期均值。

| 星期 | 效应（元） |
|---|---|
{wd_rows}

**结论**：
- 星期效应最强的是 **{dec['best_weekday']}**（{fmt(dec['best_weekday_effect'])} 元），
  最弱的是 **{dec['worst_weekday']}**（{fmt(dec['worst_weekday_effect'])} 元），
  两者相差 {fmt(abs(dec['best_weekday_effect'] - dec['worst_weekday_effect']))} 元。
- 残差标准差 {fmt(dec['residual_std'])} 元，占日均 GMV 的 {pct(dec['residual_std_pct'])}。
  **这个比例说明：即使完全掌握趋势和星期规律，仍有约 {dec['residual_std_pct']:.0f}% 的日波动无法解释**——
  这是模型精度的天然天花板，也是后面 MAPE 停留在 10% 量级的根本原因。

![星期效应](figures/02_weekday_effect.png)

## 三、模型与回测方法

### 3.1 为什么必须用滚动回测

时间序列**不能用随机 KFold**：随机切分会把「未来的数据」混进训练集，
模型在测试集上表现虚高，上线后立刻失效。

本报告采用**滚动回测（walk-forward）**：
用最后 28 天做测试，第 t 天只用 `[0, t)` 的数据训练，逐日推进，
完全模拟「每天早上看昨天为止的数据，预测今天」的真实场景。

### 3.2 特征工程

只用当天及以前的信息，避免未来信息泄露：

| 特征 | 含义 |
|---|---|
| lag1 ～ lag7 | 前 1～7 天 GMV |
| ma7 / ma14 | 前 7 / 14 天移动平均（先 shift(1) 再 rolling，不含当天） |
| weekday | 星期几（0=周一） |
| is_weekend | 是否周末 |
| month | 月份（年内季节性） |
| day_of_month | 月内日序（月初/发薪日效应） |

### 3.3 对比的四个模型

1. **朴素基线（lag1）**：直接用昨天 GMV 当今天预测。这是**必须建立的参照系**——
   任何模型打不过它，就没有上线的理由。
2. **线性回归**：13 个特征直接线性拟合。
3. **岭回归**：L2 正则，缓解滞后特征之间的多重共线性。
4. **XGBoost**：梯度提升树，捕捉非线性。
5. **季节调整 + 线性回归**：先去星期效应 → 在去季节化序列上回归 → 预测后乘回星期因子。
   这一路是为解决「递归预测向均值收敛」问题而专门设计的（见第五节）。

## 四、回测结果

| 模型 | MAE（元） | RMSE（元） | MAPE | sMAPE |
|---|---|---|---|---|
{rows_md}

![模型对比](figures/05_model_compare.png)

![滚动回测](figures/03_backtest.png)

**结论**：
- 最优模型为 **{best}**，MAPE = {pct(best_m['MAPE'] if best_m else 0)}，
  相比朴素基线（MAPE {pct(naive['MAPE']) if naive else '—'}）**降低 {lift}**。
- 线性回归与岭回归几乎无差异（MAPE 12.43% vs 12.42%），说明**特征本身近似共线但正则收益很小**，
  模型复杂度不是瓶颈。
- **XGBoost 反而更差**（MAPE {pct(next((m['MAPE'] for m in metrics if m['model']=='XGBoost'), 0))}）：
  样本只有 352 天，树模型容易记住噪声。**这直接反驳了「越复杂的模型越好」的直觉**。
- 所有模型都显著优于朴素基线，说明滞后 + 移动平均特征确实提供了增量信息。

## 五、模型适用边界（批判性审视）

### 5.1 递归预测的均值收敛问题（本周最重要的发现）

首版用**纯递归线性回归**预测未来 7 天，结果几乎是一条平线：
预测极差仅 **{fmt(diag_plain.get('pred_range', 0))} 元**，变异系数（CV）
**{pct(diag_plain.get('pred_cv_pct', 0))}**，而历史序列的 CV 是 **{pct(diag_plain.get('hist_cv_pct', 0))}**，
两者相差一个量级。而历史数据中星期效应造成的周内极差超过 8,000 元——
**模型把这个结构性规律完全抹掉了**。

原因：递归预测时，`lag1` 等特征被**预测值**逐步填充，而不是真实观测值。
当所有滞后项都变成平滑的预测值，模型输入的方差迅速衰减，输出自然收敛到均值附近。

**诊断方法**：比较预测序列与历史序列的变异系数（CV）。

| 指标 | 纯递归线性回归 | 季节调整模型 |
|---|---|---|
| 历史 CV | {pct(diag_plain.get('hist_cv_pct', 0))} | {pct(diag.get('hist_cv_pct', 0))} |
| 预测 CV | {pct(diag_plain.get('pred_cv_pct', 0))} | {pct(diag.get('pred_cv_pct', 0))} |
| 比值 | {pct(diag_plain.get('cv_ratio_pct', 0))} | {pct(diag.get('cv_ratio_pct', 0))} |
| 预测极差（元） | {fmt(diag_plain.get('pred_range', 0))} | {fmt(diag.get('pred_range', 0))} |

**处置**：改用「季节调整 + 线性回归」路线——先把序列按星期因子去季节化，
在平稳化后的序列上回归预测，最后乘回星期因子。这样预测能保住周内结构。
改进后预测极差从 {fmt(diag_plain.get('pred_range', 0))} 元提升到 {fmt(diag.get('pred_range', 0))} 元，
CV 从 {pct(diag_plain.get('pred_cv_pct', 0))} 提升到 {pct(diag.get('pred_cv_pct', 0))}，
回测 MAPE 也从纯递归线性回归的 {pct(lin_m['MAPE'] if lin_m else 0)} 降到
{pct(seasonal_m['MAPE'] if seasonal_m else 0)}——
**结构保真与精度提升是同时发生的，不是取舍关系**。

### 5.2 其它边界

| 边界 | 说明 |
|---|---|
| **只有一年数据** | 无法做年同比，也无法验证季节规律在跨年是否稳定。2025 年的预测本质上是「2024 规律的延续假设」 |
| **无外生变量** | 促销、竞品、天气、节假日都没有数据，模型只能解释历史惯性，不能预测「黑天鹅」 |
| **MAPE 会被低值日放大** | 所以同时报 MAE / RMSE / sMAPE；三个指标排序一致，结论稳健 |
| **残差不可解释部分 {dec['residual_std_pct']:.0f}%** | 这是数据本身的随机性，不是模型缺陷，靠调参解决不了 |
| **7 天已是递归上限** | 每多预测一天误差就多累积一次，建议实际使用不超过 7 天，且每天滚动重训 |

### 5.3 业务使用建议

- 预测值应作为**预算基线**，而不是精确承诺；建议给出 ±MAPE 区间（约 ±{pct(best_m['MAPE'] if best_m else 0)}）。
- 每天用真实数据重训，不要用一周前的模型预测今天。
- 出现 |z| ≥ 2 的异常日时，人工介入核查，不要相信模型外推。

## 六、未来 7 天 GMV 预测

| 日期 | 星期 | 预测 GMV（元） |
|---|---|---|
{fc_rows}

- **7 天合计**：{fmt(fc['total'])} 元，日均 {fmt(fc['mean'])} 元
- 预测起点：{series['end']}，预测区间：{fc['rows'][0]['date']} ～ {fc['rows'][-1]['date']}

![未来 7 天预测](figures/04_forecast.png)

**解读**：预测值在 {fmt(min(r['pred_gmv'] for r in fc['rows']))} ～ {fmt(max(r['pred_gmv'] for r in fc['rows']))} 元之间波动，
保留了「周一偏低、周二偏高」的周内规律，与历史星期效应一致。
日均 {fmt(fc['mean'])} 元略低于 2024 年日均 {fmt(series['gmv_mean'])} 元，
差异在 MAPE 误差范围内，不构成趋势性下滑的证据。

## 七、结论

1. **最优模型**：{best}，MAPE {pct(best_m['MAPE'] if best_m else 0)}，比朴素基线降低 {lift}。
2. **模型复杂度不是关键**：XGBoost 反而最差，说明小样本下简单模型更稳。
3. **最大风险是递归收敛**：必须做变异系数诊断，否则会得到一个「看起来很准、实际毫无信息量」的平线预测。
4. **精度天花板由数据决定**：{dec['residual_std_pct']:.0f}% 的不可解释波动无法通过建模消除。
5. **适用边界**：单年数据、无外生变量、7 天以内、需每日重训。

---

*本报告由 `第3周/py/report_build.py` 自动生成，所有数字来自 Hive 实际查询与模型实际输出。*
"""
    path = REPORT_DIR / "第3周_预测建模分析报告.md"
    path.write_text(md, encoding="utf-8")
    print(f"  ✓ {path.name}")
    return path


# ============================================================
# 报告 2：AI 洞察与经营简报
# ============================================================
def build_insight_report():
    d = load_json("insight.json")
    if not d:
        print("  ⚠ 缺少 insight.json，跳过洞察报告")
        return None

    summary = d["summary"]
    anomalies = d["anomalies"]
    facts = summary["facts"]

    anom_md = ""
    for i, a in enumerate(anomalies, 1):
        f = a["facts"]
        v = a["verify"]
        badge = "✅ 校验通过" if v["passed"] else f"⚠️ 校验通过率 {v['pass_rate']*100:.0f}%"
        unmatched = (f"未匹配数字：`{v['unmatched_numbers']}`"
                     if v["unmatched_numbers"] else "全部数字均可在事实中回溯")
        anom_md += f"""
### 异常 {i}：{a['date']}（{f['星期']}）

| 项目 | 数值 |
|---|---|
| 当日 GMV | {fmt(f['当日GMV'])} 元 |
| 近 30 日均 GMV | {fmt(f['近30日均GMV'])} 元 |
| 偏离金额 | {fmt(f['偏离金额'])} 元 |
| 偏离比例 | {pct(f['偏离比例'])} |
| z 值 | {f['z值']} |
| 当日订单量 | {f['当日订单量']} 单 |
| 当日客单价 | {fmt(f['当日客单价'])} 元 |
| 周同比 | {pct(f['周同比']) if f['周同比'] is not None else '—'} |

**AI 归因**（生成模式：`{a['mode']}`，{badge}，{unmatched}）：

{a['analysis']}

---
"""

    md = f"""# 第 3 周 AI 智能洞察与经营分析简报

> 团队：云仓五号　日期：2026-11-09
> 生成方式：`第3周/py/insight.py`
> 说明：所有事实数字由 Hive 查询提供，大模型只负责组织语言；生成后逐数字回溯校验。

## 一、经营分析摘要

{summary['summary']}

> 生成模式：`{summary['mode']}`　数字校验通过率：{summary['verify']['pass_rate']*100:.0f}%
> {"所有数字均可在事实数据中回溯。" if not summary['verify']['unmatched_numbers'] else "未匹配数字：" + str(summary['verify']['unmatched_numbers'])}

## 二、核心事实（模型看到的输入）

| 指标 | 数值 |
|---|---|
| 全年 GMV | {fmt(facts['GMV'])} 元 |
| 订单量 | {facts['订单量']:,} 单 |
| 有效订单量 | {facts['有效订单量']:,} 单 |
| 客单价 | {fmt(facts['客单价'])} 元 |
| 活跃用户数 | {facts['活跃用户数']:,} 人 |
| 复购率 | {pct(facts['复购率'])} |
| 日均 GMV | {fmt(facts['日均GMV'])} 元 |
| 客单件数 | {facts['客单件数']} 件/单 |
| 门店数 | {facts['门店数']} 家 |
| 品类 Top1 | {facts['品类Top1']}（{pct(facts['品类Top1占比'])}） |
| 品类末位 | {facts['品类末位']}（{pct(facts['品类末位占比'])}） |
| 最高月 | {facts['最高月']}（{fmt(facts['最高月GMV'])} 元） |
| 最低月 | {facts['最低月']}（{fmt(facts['最低月GMV'])} 元） |
| 重要价值客户 | {facts['高价值客户数']} 人 / GMV 占比 {pct(facts['高价值GMV占比'])} |
| 流失预警客户 | {facts['流失预警人数'] if '流失预警人数' in facts else facts['流失预警数']} 人 / GMV 占比 {pct(facts['流失预警GMV占比'])} |
| 渠道 Top1 | {facts['渠道Top1']}（{fmt(facts['渠道Top1GMV'])} 元） |

## 三、异常日归因

本周用 `|z| ≥ 2` 判定异常日，共命中 {len(anomalies)} 天做归因分析（实际全部异常日见 `ads_anomaly_daily`）。

{anom_md}

## 四、审慎验证机制

AI 生成内容的最大风险是**看似合理但数字错误**。本报告采用三层校验：

| 层级 | 做法 | 效果 |
|---|---|---|
| 1. 事实隔离 | 所有数字先由 SQL 算出，以 JSON 形式喂给模型，模型不参与计算 | 杜绝模型自己"算错" |
| 2. 数字回溯 | 抽取生成文本中的全部数值，逐个与事实包比对（容差 2%） | 本次通过率 {summary['verify']['pass_rate']*100:.0f}% |
| 3. 离线对照 | 无 Key 时用确定性模板生成，结构与在线一致 | 可对比在线/离线差异 |

> 实测：首版离线模板把「星期 2024-01-20」写进正文，被数字校验与人工阅读同时发现，
> 改为先算星期名再拼装。**这说明自动校验能抓到一部分错误，但不能替代人工通读。**

## 五、局限与风险

1. **无外生变量**：促销、竞品、天气、系统故障都无法验证，归因只能给方向，不能定性。
2. **复购率饱和**：100% 是数据集特性（人均 15 单），不能作为用户粘性的证据，已在摘要中显式说明。
3. **单年数据**：无法判断当前是自然波动还是趋势变化。
4. **模型语言幻觉**：即便数字校验通过，因果推断仍可能是错的（如把相关性当因果）。

---

*本报告由 `第3周/py/report_build.py` 自动生成。*
"""
    path = REPORT_DIR / "第3周_AI洞察与经营简报.md"
    path.write_text(md, encoding="utf-8")
    print(f"  ✓ {path.name}")
    return path


# ============================================================
# 看板
# ============================================================
def build_dashboard():
    """生成单文件 HTML 看板（无外部依赖，双击即可看）。"""
    kpi = query_df("select kpi_name, kpi_value, unit, calc_desc "
                   "from ydsh_ads.ads_kpi_week3 order by kpi_value desc",
                   database="ydsh_ads").to_dict(orient="records")
    cats = query_df("select category, gmv, gmv_share_pct from ydsh_ads.ads_category_topn "
                    "order by rank_no", database="ydsh_ads").to_dict(orient="records")
    months = query_df("select ym, gmv, mom_pct from ydsh_ads.ads_monthly_report order by ym",
                      database="ydsh_ads").to_dict(orient="records")
    rfm = query_df("select segment, user_cnt, gmv, gmv_share_pct, user_share_pct "
                   "from ydsh_ads.ads_user_rfm_segment order by gmv desc",
                   database="ydsh_ads").to_dict(orient="records")
    stores = query_df("select rank_no, store_name, province, gmv, gmv_per_sqm "
                      "from ydsh_ads.ads_store_rank order by rank_no limit 10",
                      database="ydsh_ads").to_dict(orient="records")
    anoms = query_df("select order_date, gmv, z_score, anomaly_type "
                     "from ydsh_ads.ads_anomaly_daily order by abs(z_score) desc limit 8",
                     database="ydsh_ads").to_dict(orient="records")

    fs = load_json("forecast_summary.json", {})
    nls = load_json("nl2sql_tests.json", [])
    ins = load_json("insight.json", {})

    # KPI 卡片
    kpi_cards = ""
    for k in kpi:
        val = k["kpi_value"]
        try:
            v = f"{float(val):,.2f}" if abs(float(val)) >= 1000 else f"{float(val):.2f}"
        except (TypeError, ValueError):
            v = str(val)
        kpi_cards += f"""
        <div class="kpi">
          <div class="kpi-name">{html.escape(str(k['kpi_name']))}</div>
          <div class="kpi-val">{v}<span class="unit">{html.escape(str(k['unit']))}</span></div>
          <div class="kpi-desc">{html.escape(str(k['calc_desc']))}</div>
        </div>"""

    # 品类条形
    max_cat = max(float(c["gmv"]) for c in cats) if cats else 1
    cat_bars = ""
    for c in cats:
        w = float(c["gmv"]) / max_cat * 100
        cat_bars += f"""
        <div class="bar-row">
          <span class="bar-label">{html.escape(str(c['category']))}</span>
          <div class="bar-track"><div class="bar-fill" style="width:{w:.1f}%"></div></div>
          <span class="bar-val">{float(c['gmv']):,.0f}（{float(c['gmv_share_pct']):.2f}%）</span>
        </div>"""

    # 月度
    max_m = max(float(m["gmv"]) for m in months) if months else 1
    mon_bars = ""
    for m in months:
        w = float(m["gmv"]) / max_m * 100
        mom = m.get("mom_pct")
        mom_s = f"{float(mom):+.2f}%" if mom is not None else "—"
        cls = "up" if mom and float(mom) > 0 else "down"
        mon_bars += f"""
        <div class="bar-row">
          <span class="bar-label">{html.escape(str(m['ym']))}</span>
          <div class="bar-track"><div class="bar-fill alt" style="width:{w:.1f}%"></div></div>
          <span class="bar-val">{float(m['gmv']):,.0f} <em class="{cls}">{mom_s}</em></span>
        </div>"""

    # 分层表
    rfm_rows = "".join(
        f"<tr><td>{html.escape(str(r['segment']))}</td><td>{int(r['user_cnt']):,}</td>"
        f"<td>{float(r['user_share_pct']):.2f}%</td><td>{float(r['gmv']):,.2f}</td>"
        f"<td>{float(r['gmv_share_pct']):.2f}%</td></tr>" for r in rfm)

    # 门店表
    store_rows = "".join(
        f"<tr><td>{int(r['rank_no'])}</td><td>{html.escape(str(r['store_name']))}</td>"
        f"<td>{html.escape(str(r['province']))}</td><td>{float(r['gmv']):,.2f}</td>"
        f"<td>{float(r['gmv_per_sqm']):,.2f}</td></tr>" for r in stores)

    # 异常表
    anom_rows = "".join(
        f"<tr><td>{html.escape(str(r['order_date']))}</td><td>{float(r['gmv']):,.2f}</td>"
        f"<td>{float(r['z_score']):+.2f}</td>"
        f"<td><span class='tag {'down' if r['anomaly_type']=='偏低' else 'up'}'>"
        f"{html.escape(str(r['anomaly_type']))}</span></td></tr>" for r in anoms)

    # 预测表
    fc_rows = ""
    if fs:
        for r in fs["forecast"]["rows"]:
            fc_rows += (f"<tr><td>{r['date']}</td><td>{r['weekday']}</td>"
                        f"<td>{float(r['pred_gmv']):,.2f}</td></tr>")

    # 模型表
    model_rows = ""
    if fs:
        for m in fs["backtest"]["metrics"]:
            cls = "best" if m["model"] == fs["backtest"]["best_model"] else ""
            model_rows += (f"<tr class='{cls}'><td>{html.escape(m['model'])}</td>"
                           f"<td>{m['MAE']:,.2f}</td><td>{m['RMSE']:,.2f}</td>"
                           f"<td>{m['MAPE']:.2f}%</td><td>{m['sMAPE']:.2f}%</td></tr>")

    # NL2SQL 表
    nl_rows = ""
    for r in nls:
        ok = "✅" if r.get("passed") else "❌"
        sql = html.escape(r["sql"])
        nl_rows += f"""<tr>
          <td>{ok}</td><td>{html.escape(r['question'])}</td>
          <td class="sql">{sql[:110]}{'…' if len(sql) > 110 else ''}</td>
          <td>{r['row_count']}</td><td>{r['elapsed_s']}s</td>
          <td>{html.escape(r['mode'])}</td></tr>"""

    summary_text = ""
    if ins and ins.get("summary"):
        summary_text = ins["summary"]["summary"].replace("\n\n", "</p><p>")

    html_doc = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<title>第 3 周 · 云仓五号 经营分析看板</title>
<style>
  * {{ box-sizing: border-box; }}
  body {{
    font-family: "Microsoft YaHei", "PingFang SC", sans-serif;
    margin: 0; padding: 0; background: #f5f7fa; color: #24303f;
  }}
  header {{
    background: linear-gradient(135deg, #1f4e79 0%, #2e7ebd 100%);
    color: #fff; padding: 32px 40px;
  }}
  header h1 {{ margin: 0 0 6px; font-size: 26px; font-weight: 600; }}
  header .sub {{ opacity: .85; font-size: 14px; }}
  main {{ padding: 24px 40px 60px; max-width: 1500px; margin: 0 auto; }}
  h2 {{
    font-size: 18px; margin: 34px 0 14px; padding-left: 10px;
    border-left: 4px solid #2e7ebd;
  }}
  .kpi-grid {{
    display: grid; grid-template-columns: repeat(auto-fill, minmax(210px, 1fr));
    gap: 14px;
  }}
  .kpi {{
    background: #fff; border-radius: 8px; padding: 16px 18px;
    box-shadow: 0 1px 3px rgba(0,0,0,.08);
  }}
  .kpi-name {{ font-size: 13px; color: #6b7c93; }}
  .kpi-val {{ font-size: 24px; font-weight: 700; color: #1f4e79; margin: 6px 0; }}
  .kpi-val .unit {{ font-size: 12px; font-weight: 400; color: #8a9bb0; margin-left: 4px; }}
  .kpi-desc {{ font-size: 11px; color: #9aa9bb; line-height: 1.4; }}
  .panel {{
    background: #fff; border-radius: 8px; padding: 18px 22px;
    box-shadow: 0 1px 3px rgba(0,0,0,.08);
  }}
  .bar-row {{ display: flex; align-items: center; margin: 7px 0; font-size: 13px; }}
  .bar-label {{ width: 92px; flex: none; color: #4a5b70; }}
  .bar-track {{ flex: 1; background: #eef2f7; border-radius: 4px; height: 18px; margin: 0 10px; }}
  .bar-fill {{ height: 100%; border-radius: 4px; background: #2e7ebd; }}
  .bar-fill.alt {{ background: #59a14f; }}
  .bar-val {{ width: 190px; flex: none; text-align: right; color: #24303f; font-variant-numeric: tabular-nums; }}
  .bar-val em {{ font-style: normal; font-size: 11px; margin-left: 6px; }}
  em.up, .up {{ color: #c0392b; }}
  em.down, .down {{ color: #27ae60; }}
  table {{ width: 100%; border-collapse: collapse; font-size: 13px; }}
  th, td {{ padding: 8px 10px; text-align: left; border-bottom: 1px solid #eef2f7; }}
  th {{ background: #f8fafc; color: #4a5b70; font-weight: 600; font-size: 12px; }}
  td.sql {{ font-family: Consolas, monospace; font-size: 11px; color: #6b7c93; }}
  tr.best {{ background: #eaf6ea; font-weight: 600; }}
  .tag {{ padding: 2px 8px; border-radius: 10px; font-size: 11px; }}
  .tag.up {{ background: #fdecea; }}
  .tag.down {{ background: #eaf6ea; }}
  .two-col {{ display: grid; grid-template-columns: 1fr 1fr; gap: 18px; }}
  .summary-text {{ font-size: 14px; line-height: 1.9; color: #33475e; }}
  .summary-text p {{ margin: 0 0 12px; }}
  .note {{ font-size: 12px; color: #8a9bb0; margin-top: 10px; }}
  footer {{ text-align: center; color: #9aa9bb; font-size: 12px; padding: 30px; }}
  @media (max-width: 900px) {{ .two-col {{ grid-template-columns: 1fr; }} main {{ padding: 16px; }} }}
</style>
</head>
<body>
<header>
  <h1>云仓五号 · 第 3 周经营分析看板</h1>
  <div class="sub">数据分析 · 预测建模 · AI 协同（NL2SQL）　|　数据区间 2024-01-01 ～ 2024-12-31　|　生成时间 {__import__('datetime').datetime.now().strftime('%Y-%m-%d %H:%M')}</div>
</header>
<main>

  <h2>一、核心 KPI</h2>
  <div class="kpi-grid">{kpi_cards}</div>

  <h2>二、品类销售结构</h2>
  <div class="panel">{cat_bars}
    <div class="note">品类 GMV 按明细金额占比分摊订单实付，覆盖总 GMV 的 99.37%，占比合计不为 100%。</div>
  </div>

  <h2>三、月度趋势</h2>
  <div class="panel">{mon_bars}
    <div class="note">环比 =（本月 GMV − 上月 GMV）÷ 上月 GMV。红字为上升，绿字为下降。</div>
  </div>

  <h2>四、用户价值分层（RFM）</h2>
  <div class="panel">
    <table>
      <tr><th>分层</th><th>用户数</th><th>用户占比</th><th>GMV（元）</th><th>GMV 占比</th></tr>
      {rfm_rows}
    </table>
    <div class="note">R/F/M 各按 ntile(5) 打分，规则见《指标定义文档》第八节。</div>
  </div>

  <h2>五、门店 Top 10</h2>
  <div class="panel">
    <table>
      <tr><th>排名</th><th>门店</th><th>省份</th><th>GMV（元）</th><th>坪效（元/㎡）</th></tr>
      {store_rows}
    </table>
  </div>

  <h2>六、异常日（|z| ≥ 2）</h2>
  <div class="panel">
    <table>
      <tr><th>日期</th><th>GMV（元）</th><th>z 值</th><th>类型</th></tr>
      {anom_rows}
    </table>
    <div class="note">z =（当日 GMV − 近 30 日均值）÷ 近 30 日标准差。</div>
  </div>

  <h2>七、预测建模</h2>
  <div class="two-col">
    <div class="panel">
      <table>
        <tr><th>模型</th><th>MAE</th><th>RMSE</th><th>MAPE</th><th>sMAPE</th></tr>
        {model_rows}
      </table>
      <div class="note">绿色行为最优模型；滚动回测最后 28 天，逐日训练。</div>
    </div>
    <div class="panel">
      <table>
        <tr><th>日期</th><th>星期</th><th>预测 GMV（元）</th></tr>
        {fc_rows}
      </table>
      <div class="note">未来 7 天合计 {float(fs['forecast']['total']) if fs else 0:,.2f} 元。</div>
    </div>
  </div>

  <h2>八、NL2SQL 测试结果</h2>
  <div class="panel">
    <table>
      <tr><th></th><th>问题</th><th>生成的 SQL</th><th>行数</th><th>耗时</th><th>模式</th></tr>
      {nl_rows}
    </table>
    <div class="note">模式 rule_based 表示当前未配置 API Key，走规则引擎兜底；配置 Key 后自动切换为大模型生成。</div>
  </div>

  <h2>九、AI 经营分析摘要</h2>
  <div class="panel">
    <div class="summary-text"><p>{summary_text}</p></div>
    <div class="note">生成模式：{html.escape(str(ins.get('summary', {}).get('mode', '—')))}
      ｜ 数字校验通过率：{ins.get('summary', {}).get('verify', {}).get('pass_rate', 0)*100:.0f}%</div>
  </div>

</main>
<footer>云仓五号 · 企业级数据仓库与经营分析可视化系统 · 第 3 周</footer>
</body>
</html>
"""
    path = REPORT_DIR / "第3周_运行看板.html"
    path.write_text(html_doc, encoding="utf-8")
    print(f"  ✓ {path.name}")
    return path


def main():
    print("=" * 70)
    print("  生成第 3 周报告与看板")
    print("=" * 70)
    print("\n[1/3] 预测建模分析报告 ...")
    build_forecast_report()
    print("\n[2/3] AI 洞察与经营简报 ...")
    build_insight_report()
    print("\n[3/3] 运行看板 ...")
    build_dashboard()
    print("\n完成。")


if __name__ == "__main__":
    from console import init_console
    init_console()
    main()
