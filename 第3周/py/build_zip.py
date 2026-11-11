# -*- coding: utf-8 -*-
"""
build_zip.py —— 生成教学平台提交用的压缩包

沿用前两周惯例（Git + 打包双渠道提交）：
  - 根目录放：交付文档（docx / xlsx / md）
  - 子目录放：代码（sql/、py/、scripts/）

排除：模拟数据、__pycache__、.env、临时文件、图表 PNG（体积大且非必需）
"""
import sys
import zipfile
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

from config import DOC_DIR, PY_DIR, REPORT_DIR, WEEK_DIR

OUT = WEEK_DIR / "第3周_闫洪伟.zip"

# (源路径, 压缩包内路径)
FILES = [
    # ---- 任务书 6 项交付物 ----
    (REPORT_DIR / "第3周_预测建模分析报告.docx", "第3周_预测建模分析报告.docx"),
    (REPORT_DIR / "第3周_AI洞察与经营简报.docx", "第3周_AI洞察与经营简报.docx"),
    (DOC_DIR / "第3周_KPI指标结果表.xlsx", "第3周_KPI指标结果表.xlsx"),
    (DOC_DIR / "第3周_指标定义文档.md", "第3周_指标定义文档.md"),
    (DOC_DIR / "第3周_AI应用反思.md", "第3周_AI应用反思.md"),
    (DOC_DIR / "第3周_Git协同记录.md", "第3周_Git协同记录.md"),
    (DOC_DIR / "第3周_AI协同日志.md", "第3周_AI协同日志.md"),
    (DOC_DIR / "第3周_答辩手册.md", "第3周_答辩手册.md"),
    (DOC_DIR / "第3周_项目周记.docx", "第3周_项目周记.docx"),
    (DOC_DIR / "第3周_项目周记.md", "第3周_项目周记.md"),
    # ---- 报告 Markdown 源 ----
    (REPORT_DIR / "第3周_预测建模分析报告.md", "第3周_预测建模分析报告.md"),
    (REPORT_DIR / "第3周_AI洞察与经营简报.md", "第3周_AI洞察与经营简报.md"),
    (REPORT_DIR / "第3周_运行记录.md", "第3周_运行记录.md"),
    (WEEK_DIR / "README.md", "README.md"),
    # ---- SQL ----
    (WEEK_DIR / "sql/dws/50_dws_ext.sql", "sql/dws/50_dws_ext.sql"),
    (WEEK_DIR / "sql/ads/60_ads_ext.sql", "sql/ads/60_ads_ext.sql"),
    # ---- Python 代码 ----
    (PY_DIR / "config.py", "py/config.py"),
    (PY_DIR / "console.py", "py/console.py"),
    (PY_DIR / "hive_client.py", "py/hive_client.py"),
    (PY_DIR / "schema_extract.py", "py/schema_extract.py"),
    (PY_DIR / "forecast.py", "py/forecast.py"),
    (PY_DIR / "llm_client.py", "py/llm_client.py"),
    (PY_DIR / "nl2sql.py", "py/nl2sql.py"),
    (PY_DIR / "insight.py", "py/insight.py"),
    (PY_DIR / "report_build.py", "py/report_build.py"),
    (PY_DIR / "build_kpi_xlsx.py", "py/build_kpi_xlsx.py"),
    (PY_DIR / "run_week3.py", "py/run_week3.py"),
    # ---- 脚本 ----
    (WEEK_DIR / "scripts/run-week3.ps1", "scripts/run-week3.ps1"),
    (WEEK_DIR / "scripts/md2docx.py", "scripts/md2docx.py"),
    (WEEK_DIR / "一键运行_第3周.bat", "一键运行_第3周.bat"),
    # ---- 图表（保留，docx 里已嵌入，但单独一份便于查看）----
    (REPORT_DIR / "figures/01_gmv_series.png", "figures/01_gmv_series.png"),
    (REPORT_DIR / "figures/02_weekday_effect.png", "figures/02_weekday_effect.png"),
    (REPORT_DIR / "figures/03_backtest.png", "figures/03_backtest.png"),
    (REPORT_DIR / "figures/04_forecast.png", "figures/04_forecast.png"),
    (REPORT_DIR / "figures/05_model_compare.png", "figures/05_model_compare.png"),
    # ---- 运行看板 ----
    (REPORT_DIR / "第3周_运行看板.html", "第3周_运行看板.html"),
    # ---- 中间数据（供复核）----
    (REPORT_DIR / "data/model_metrics.csv", "data/model_metrics.csv"),
    (REPORT_DIR / "data/forecast_7d.csv", "data/forecast_7d.csv"),
    (REPORT_DIR / "data/nl2sql_tests.json", "data/nl2sql_tests.json"),
]


def main():
    print("=" * 70)
    print("  打包第 3 周提交物")
    print("=" * 70)

    missing = []
    added = 0
    total_bytes = 0

    with zipfile.ZipFile(OUT, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
        for src, arc in FILES:
            if not src.exists():
                missing.append(str(src))
                continue
            zf.write(src, arc)
            added += 1
            total_bytes += src.stat().st_size

    print(f"\n  打包文件数：{added}")
    print(f"  原始体积：{total_bytes / 1024 / 1024:.2f} MB")
    print(f"  压缩后：  {OUT.stat().st_size / 1024 / 1024:.2f} MB")
    print(f"  输出：    {OUT}")

    if missing:
        print(f"\n  ⚠ 缺失 {len(missing)} 个文件：")
        for m in missing:
            print(f"    - {m}")
    else:
        print("\n  ✓ 全部文件齐全")

    # 校验：重新打开确认可读
    with zipfile.ZipFile(OUT) as zf:
        bad = zf.testzip()
        names = zf.namelist()
    print(f"  ✓ 压缩包校验通过（{len(names)} 个条目）" if bad is None else f"  ✗ 损坏：{bad}")

    return 0 if not missing else 1


if __name__ == "__main__":
    sys.exit(main())
