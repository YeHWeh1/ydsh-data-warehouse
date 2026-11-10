# -*- coding: utf-8 -*-
"""
build_kpi_xlsx.py —— 生成《第3周_KPI指标结果表.xlsx》（任务书交付物 2）

包含 6 个 sheet：
  1. 核心KPI      10 项核心指标 + 口径说明
  2. 品类销售     8 个品类的 GMV / 占比 / 订单数 / 客单价
  3. 用户分层     RFM 5 个分层
  4. 月度趋势     12 个月 GMV / 环比
  5. 门店排行     60 家门店 Top20
  6. 异常日       |z| >= 2 的异常日
"""
import sys

sys.stdout.reconfigure(encoding="utf-8")

from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

from config import DOC_DIR, REPORT_DIR
from hive_client import query_df

# ---------- 样式 ----------
HEAD_FILL = PatternFill("solid", fgColor="1F4E79")
HEAD_FONT = Font(name="微软雅黑", size=11, bold=True, color="FFFFFF")
BODY_FONT = Font(name="微软雅黑", size=10)
TITLE_FONT = Font(name="微软雅黑", size=14, bold=True, color="1F4E79")
NOTE_FONT = Font(name="微软雅黑", size=9, italic=True, color="808080")
THIN = Side(style="thin", color="D9D9D9")
BORDER = Border(left=THIN, right=THIN, top=THIN, bottom=THIN)
CENTER = Alignment(horizontal="center", vertical="center")
LEFT = Alignment(horizontal="left", vertical="center", wrap_text=True)


def write_sheet(wb, name, title, headers, rows, widths, note=None, num_fmt=None):
    """通用写表：标题行 + 表头 + 数据 + 备注。"""
    ws = wb.create_sheet(name)

    # 标题
    ws.merge_cells(start_row=1, start_column=1, end_row=1, end_column=len(headers))
    c = ws.cell(row=1, column=1, value=title)
    c.font = TITLE_FONT
    c.alignment = CENTER
    ws.row_dimensions[1].height = 28

    # 表头
    for j, h in enumerate(headers, 1):
        c = ws.cell(row=2, column=j, value=h)
        c.fill = HEAD_FILL
        c.font = HEAD_FONT
        c.alignment = CENTER
        c.border = BORDER
    ws.row_dimensions[2].height = 22

    # 数据
    for i, row in enumerate(rows, 3):
        for j, v in enumerate(row, 1):
            c = ws.cell(row=i, column=j, value=v)
            c.font = BODY_FONT
            c.border = BORDER
            c.alignment = LEFT if j == len(headers) else CENTER
            if num_fmt and j in num_fmt and isinstance(v, (int, float)):
                c.number_format = num_fmt[j]
        ws.row_dimensions[i].height = 18

    # 列宽
    for j, w in enumerate(widths, 1):
        ws.column_dimensions[get_column_letter(j)].width = w

    # 备注
    if note:
        r = len(rows) + 4
        ws.merge_cells(start_row=r, start_column=1, end_row=r, end_column=len(headers))
        c = ws.cell(row=r, column=1, value=note)
        c.font = NOTE_FONT
        c.alignment = LEFT

    ws.freeze_panes = "A3"
    return ws


def main():
    print("=" * 70)
    print("  生成 KPI 指标结果表")
    print("=" * 70)

    wb = Workbook()
    wb.remove(wb.active)   # 删掉默认 sheet

    # ---- 1. 核心 KPI ----
    print("\n[1/6] 核心 KPI ...")
    kpi = query_df(
        "select kpi_name, kpi_value, unit, calc_desc "
        "from ydsh_ads.ads_kpi_week3 order by kpi_value desc",
        database="ydsh_ads")
    rows = []
    for _, r in kpi.iterrows():
        v = float(r["kpi_value"])
        rows.append([r["kpi_name"], round(v, 2), r["unit"], r["calc_desc"]])
    write_sheet(
        wb, "核心KPI", "第 3 周 核心 KPI 指标结果表",
        ["指标名称", "指标值", "单位", "口径说明"], rows,
        [16, 18, 10, 52],
        note="数据来源：ydsh_ads.ads_kpi_week3　|　统计区间：2024-01-01 ～ 2024-12-31　|　"
             "金额口径：仅统计 is_valid_amount = 1 的订单（14,696 单）",
        num_fmt={2: "#,##0.00"})

    # ---- 2. 品类销售 ----
    print("[2/6] 品类销售 ...")
    cats = query_df(
        "select rank_no, category, order_cnt, item_qty, gmv, gmv_share_pct, aov "
        "from ydsh_ads.ads_category_topn order by rank_no",
        database="ydsh_ads")
    rows = [[int(r["rank_no"]), r["category"], int(r["order_cnt"]), int(r["item_qty"]),
             round(float(r["gmv"]), 2), round(float(r["gmv_share_pct"]), 2),
             round(float(r["aov"]), 2)] for _, r in cats.iterrows()]
    write_sheet(
        wb, "品类销售", "品类销售结果（按 GMV 降序）",
        ["排名", "品类", "订单数", "件数", "GMV（元）", "占比（%）", "客单价（元）"], rows,
        [8, 14, 12, 12, 18, 12, 14],
        note="品类 GMV 按明细金额占比分摊订单实付，覆盖总 GMV 的 99.37%（94 单明细不可用无法归类）",
        num_fmt={5: "#,##0.00", 7: "#,##0.00"})

    # ---- 3. 用户分层 ----
    print("[3/6] 用户分层 ...")
    rfm = query_df(
        "select segment, user_cnt, user_share_pct, gmv, gmv_share_pct, "
        "avg_monetary, avg_freq, avg_recency_days "
        "from ydsh_ads.ads_user_rfm_segment order by gmv desc",
        database="ydsh_ads")
    rows = [[r["segment"], int(r["user_cnt"]), round(float(r["user_share_pct"]), 2),
             round(float(r["gmv"]), 2), round(float(r["gmv_share_pct"]), 2),
             round(float(r["avg_monetary"]), 2), round(float(r["avg_freq"]), 2),
             round(float(r["avg_recency_days"]), 1)] for _, r in rfm.iterrows()]
    write_sheet(
        wb, "用户分层", "用户价值分层（RFM）",
        ["分层", "用户数", "用户占比（%）", "GMV（元）", "GMV占比（%）",
         "人均消费（元）", "人均频次", "平均最近购买（天）"], rows,
        [16, 10, 14, 18, 14, 16, 12, 18],
        note="R/F/M 各按 ntile(5) 打分，规则见《指标定义文档》第八节；"
             "复购率 100% 为本数据集饱和所致，不代表用户粘性强",
        num_fmt={4: "#,##0.00", 6: "#,##0.00"})

    # ---- 4. 月度趋势 ----
    print("[4/6] 月度趋势 ...")
    mon = query_df(
        "select ym, active_days, order_cnt, gmv, aov, buyer_cnt, avg_daily_gmv, mom_pct "
        "from ydsh_ads.ads_monthly_report order by ym",
        database="ydsh_ads")
    rows = [[r["ym"], int(r["active_days"]), int(r["order_cnt"]),
             round(float(r["gmv"]), 2), round(float(r["aov"]), 2), int(r["buyer_cnt"]),
             round(float(r["avg_daily_gmv"]), 2),
             (round(float(r["mom_pct"]), 2) if r["mom_pct"] is not None and str(r["mom_pct"]) != "nan" else None)]
            for _, r in mon.iterrows()]
    write_sheet(
        wb, "月度趋势", "月度经营趋势",
        ["月份", "活跃天数", "订单量", "GMV（元）", "客单价（元）", "买家数",
         "日均GMV（元）", "环比（%）"], rows,
        [12, 12, 12, 18, 14, 12, 16, 12],
        note="环比 =（本月 GMV − 上月 GMV）÷ 上月 GMV；2024-01 无上月数据，环比为空",
        num_fmt={4: "#,##0.00", 5: "#,##0.00", 7: "#,##0.00"})

    # ---- 5. 门店排行 ----
    print("[5/6] 门店排行 ...")
    st = query_df(
        "select rank_no, store_name, province, city, order_cnt, gmv, aov, gmv_per_sqm "
        "from ydsh_ads.ads_store_rank order by rank_no limit 20",
        database="ydsh_ads")
    rows = [[int(r["rank_no"]), r["store_name"], r["province"], r["city"],
             int(r["order_cnt"]), round(float(r["gmv"]), 2),
             round(float(r["aov"]), 2), round(float(r["gmv_per_sqm"]), 2)]
            for _, r in st.iterrows()]
    write_sheet(
        wb, "门店排行", "门店销售排行（Top 20 / 共 60 家）",
        ["排名", "门店名称", "省份", "城市", "订单数", "GMV（元）", "客单价（元）", "坪效（元/㎡）"], rows,
        [8, 22, 12, 12, 10, 18, 14, 16],
        note="坪效 = 门店 GMV ÷ 营业面积",
        num_fmt={6: "#,##0.00", 7: "#,##0.00", 8: "#,##0.00"})

    # ---- 6. 异常日 ----
    print("[6/6] 异常日 ...")
    an = query_df(
        "select order_date, gmv, ma30, sd30, z_score, anomaly_type, order_cnt, aov "
        "from ydsh_ads.ads_anomaly_daily order by abs(z_score) desc",
        database="ydsh_ads")
    rows = [[r["order_date"], round(float(r["gmv"]), 2), round(float(r["ma30"]), 2),
             round(float(r["sd30"]), 2), round(float(r["z_score"]), 2),
             r["anomaly_type"], int(r["order_cnt"]), round(float(r["aov"]), 2)]
            for _, r in an.iterrows()]
    write_sheet(
        wb, "异常日", "GMV 异常日明细（|z| ≥ 2）",
        ["日期", "当日GMV（元）", "近30日均值（元）", "近30日标准差", "z 值",
         "类型", "订单量", "客单价（元）"], rows,
        [14, 16, 18, 16, 10, 10, 10, 14],
        note="z =（当日 GMV − 近 30 日均值）÷ 近 30 日标准差；|z| ≥ 2 判定为异常",
        num_fmt={2: "#,##0.00", 3: "#,##0.00", 4: "#,##0.00", 8: "#,##0.00"})

    # ---- 保存 ----
    out = DOC_DIR / "第3周_KPI指标结果表.xlsx"
    wb.save(out)
    print(f"\n✓ 已生成：{out}")
    print(f"  共 6 个 sheet，{len(wb.sheetnames)} 个工作表")
    return out


if __name__ == "__main__":
    main()
