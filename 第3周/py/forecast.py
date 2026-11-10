# -*- coding: utf-8 -*-
"""
forecast.py —— 时间序列分析与预测建模（任务书子任务 2）

流程：
  1. 从 Hive 读取每日 GMV / 订单量序列（366 天）
  2. 趋势分解（STL 风格：趋势 / 星期 / 残差）
  3. 特征工程（滞后、移动平均、星期、月份、节假日）
  4. 三个模型对比：朴素基线 / 线性回归 / XGBoost
  5. 滚动回测（时间序列交叉验证，不能用随机 KFold）
  6. 预测未来 7 天 GMV，输出 MAE / MAPE / RMSE
  7. 落盘：CSV + 图表 + JSON 指标

关键设计（对应任务书"批判性审视模型适用边界"）：
  - 基线必须先建：如果模型打不过"昨天=今天"，就没有上线的理由
  - 回测必须按时间切分：随机切分会把未来信息泄露进训练集
  - MAPE 在低值日会被放大：同时报 MAE / RMSE / sMAPE
  - 2024 数据只有一年，无法做年同比，也无法验证季节性在跨年的稳定性
"""
import json
import sys
import warnings

import numpy as np
import pandas as pd

from config import DATA_DIR, FIG_DIR, FORECAST_DAYS, REPORT_DIR
from hive_client import query_df

warnings.filterwarnings("ignore")

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib import font_manager

from sklearn.linear_model import LinearRegression, Ridge
from sklearn.metrics import mean_absolute_error, mean_squared_error
from sklearn.preprocessing import StandardScaler

# ---------- 中文字体 ----------
def setup_cn_font():
    from config import CN_FONTS
    available = {f.name for f in font_manager.fontManager.ttflist}
    for name in CN_FONTS:
        if name in available:
            plt.rcParams["font.sans-serif"] = [name]
            plt.rcParams["axes.unicode_minus"] = False
            return name
    plt.rcParams["axes.unicode_minus"] = False
    return None


CN_FONT = setup_cn_font()


# ============================================================
# 1. 取数
# ============================================================
def load_series():
    """从 ADS 日报表读取每日序列（已在 Hive 侧算好 GMV / 订单量 / 买家数）。"""
    sql = """
    select order_date, gmv, order_cnt, valid_order_cnt, aov, buyer_cnt
    from ydsh_ads.ads_sales_daily_report
    order by order_date
    """
    df = query_df(sql, database="ydsh_ads")
    df["order_date"] = pd.to_datetime(df["order_date"])
    df = df.sort_values("order_date").set_index("order_date")
    for c in ["gmv", "order_cnt", "valid_order_cnt", "aov", "buyer_cnt"]:
        df[c] = pd.to_numeric(df[c], errors="coerce")
    return df


# ============================================================
# 2. 趋势分解（不用 statsmodels，手写可解释版本）
# ============================================================
def decompose(df):
    """
    加法分解：value = trend + seasonal(weekday) + residual
    - trend：居中 7 日移动平均（去星期效应）
    - seasonal：各星期的平均残差
    """
    s = df["gmv"].copy()
    trend = s.rolling(7, center=True, min_periods=4).mean()
    detrended = s - trend
    weekday = s.index.dayofweek
    seasonal_map = detrended.groupby(weekday).mean()
    seasonal = pd.Series(weekday, index=s.index).map(seasonal_map)
    residual = s - trend - seasonal
    return pd.DataFrame({
        "gmv": s,
        "trend": trend,
        "seasonal": seasonal,
        "residual": residual,
    })


# ============================================================
# 3. 特征工程
# ============================================================
def build_features(df):
    """
    只用「当天及以前」的信息构造特征，避免未来信息泄露：
      lag1..lag7  : 前 1~7 天 GMV
      ma7 / ma14  : 前 7 / 14 天移动平均（shift(1) 后再 rolling，不含当天）
      weekday     : 星期几（0=周一）
      is_weekend  : 周末标记
      month       : 月份（捕捉年内季节性）
      day_of_month: 月内日序（捕捉发薪日/月初效应）
    """
    d = df.copy()
    for k in range(1, 8):
        d[f"lag{k}"] = d["gmv"].shift(k)
    d["ma7"] = d["gmv"].shift(1).rolling(7).mean()
    d["ma14"] = d["gmv"].shift(1).rolling(14).mean()
    d["weekday"] = d.index.dayofweek
    d["is_weekend"] = (d["weekday"] >= 5).astype(int)
    d["month"] = d.index.month
    d["day_of_month"] = d.index.day
    return d


FEATURES = ["lag1", "lag2", "lag3", "lag4", "lag5", "lag6", "lag7",
            "ma7", "ma14", "weekday", "is_weekend", "month", "day_of_month"]


# ============================================================
# 4. 评估指标
# ============================================================
def mape(y_true, y_pred):
    y_true = np.asarray(y_true, dtype=float)
    y_pred = np.asarray(y_pred, dtype=float)
    mask = y_true != 0
    return float(np.mean(np.abs((y_true[mask] - y_pred[mask]) / y_true[mask])) * 100)


def smape(y_true, y_pred):
    """对称 MAPE：分母用 (|y|+|ŷ|)/2，低值日不会被放大。"""
    y_true = np.asarray(y_true, dtype=float)
    y_pred = np.asarray(y_pred, dtype=float)
    denom = (np.abs(y_true) + np.abs(y_pred)) / 2
    mask = denom != 0
    return float(np.mean(np.abs(y_true[mask] - y_pred[mask]) / denom[mask]) * 100)


def evaluate(y_true, y_pred, name):
    return {
        "model": name,
        "MAE": round(float(mean_absolute_error(y_true, y_pred)), 2),
        "RMSE": round(float(np.sqrt(mean_squared_error(y_true, y_pred))), 2),
        "MAPE": round(mape(y_true, y_pred), 2),
        "sMAPE": round(smape(y_true, y_pred), 2),
    }


# ============================================================
# 5. 滚动回测（时间序列专用，绝不用随机 KFold）
# ============================================================
def rolling_backtest(data, model_factory, n_test=28, name="model"):
    """
    用最后 n_test 天做滚动回测：
    第 t 天用 [0, t) 的数据训练，预测第 t 天，逐日推进。
    这样完全模拟"每天早上看昨天为止的数据，预测今天"的真实场景。
    """
    feat = data.dropna(subset=FEATURES + ["gmv"])
    if len(feat) <= n_test:
        raise ValueError(f"样本不足：{len(feat)} <= {n_test}")

    split = len(feat) - n_test
    preds, trues, dates = [], [], []

    for i in range(split, len(feat)):
        train = feat.iloc[:i]
        test = feat.iloc[i:i + 1]
        model = model_factory()
        model.fit(train[FEATURES], train["gmv"])
        pred = float(model.predict(test[FEATURES])[0])
        preds.append(pred)
        trues.append(float(test["gmv"].iloc[0]))
        dates.append(feat.index[i])

    return preds, trues, dates


def naive_baseline(data, n_test=28):
    """朴素基线：用 lag1（昨天 GMV）当今天的预测。"""
    feat = data.dropna(subset=["lag1", "gmv"])
    test = feat.iloc[-n_test:]
    return list(test["lag1"]), list(test["gmv"]), list(test.index)


# ============================================================
# 5b. 季节调整模型（去星期效应 → 预测 → 还原）
# ============================================================
def seasonal_adjust_predict(data, n_test=28):
    """
    经典"季节调整 + 预测 + 还原"路线：
      1. 用训练段的星期均值算出星期指数（相对整体均值的偏离）
      2. 把 GMV 除以（1 + 星期指数/均值）做去季节化
      3. 在去季节化序列上训练线性回归（lag1 + ma7）
      4. 预测后再乘回星期因子

    为什么要单独做这一路：普通递归预测在 lag 全被预测值填充时会向均值收敛，
    星期效应被稀释（见 diagnose_flatness）。这一路强制保留周内结构。
    """
    feat = data.dropna(subset=["lag1", "ma7", "gmv"])
    split = len(feat) - n_test
    preds, trues, dates = [], [], []

    for i in range(split, len(feat)):
        train = feat.iloc[:i]
        test = feat.iloc[i:i + 1]

        # 训练段星期指数（相对训练段均值的偏离比例）
        base = train["gmv"].mean()
        wk_factor = train.groupby(train.index.dayofweek)["gmv"].mean() / base
        wk_factor = wk_factor.reindex(range(7)).fillna(1.0)

        wd_train = train.index.dayofweek
        adj_train = train["gmv"] / wk_factor.reindex(wd_train).values

        X_tr = np.column_stack([adj_train.shift(1).values,
                                adj_train.shift(1).rolling(7).mean().values])
        mask = ~np.isnan(X_tr).any(axis=1)
        m = LinearRegression().fit(X_tr[mask], adj_train.values[mask])

        # 用去季节化的历史预测当天
        hist_adj = adj_train.values
        x_row = np.array([[hist_adj[-1], np.mean(hist_adj[-7:])]])
        pred_adj = float(m.predict(x_row)[0])
        wd_test = test.index[0].dayofweek
        pred = pred_adj * float(wk_factor.iloc[wd_test])

        preds.append(pred)
        trues.append(float(test["gmv"].iloc[0]))
        dates.append(test.index[0])

    return preds, trues, dates


def diagnose_flatness(fc, df):
    """
    诊断：预测序列是否退化为"平线"。
    比较预测的变异系数(CV)与历史 CV；若预测 CV < 历史 CV 的 10%，
    说明模型没有捕捉到波动，实际只是在预测均值。
    """
    hist_cv = float(df["gmv"].std() / df["gmv"].mean() * 100)
    pred_cv = float(fc["pred_gmv"].std() / fc["pred_gmv"].mean() * 100)
    ratio = pred_cv / hist_cv * 100 if hist_cv else 0.0
    return {
        "hist_cv_pct": round(hist_cv, 2),
        "pred_cv_pct": round(pred_cv, 2),
        "cv_ratio_pct": round(ratio, 2),
        "is_flat": ratio < 10,
        "pred_range": round(float(fc["pred_gmv"].max() - fc["pred_gmv"].min()), 2),
    }


# ============================================================
# 6. 未来 7 天预测（递归多步）
# ============================================================
def forecast_future(data, model, horizon=FORECAST_DAYS):
    """
    递归预测：每预测一天，把预测值填回序列，再预测下一天。
    必须提醒：步长越长误差累积越快，7 天后误差通常显著放大。
    """
    series = data["gmv"].copy()
    feat_row = data.iloc[[-1]].copy()   # 拿最后一行做模板（含 month 等日历特征）
    last_date = series.index[-1]
    out = []

    for step in range(1, horizon + 1):
        cur_date = last_date + pd.Timedelta(days=step)
        vals = list(series.values)
        row = {
            "lag1": vals[-1], "lag2": vals[-2], "lag3": vals[-3],
            "lag4": vals[-4], "lag5": vals[-5], "lag6": vals[-6], "lag7": vals[-7],
            "ma7": float(np.mean(vals[-7:])),
            "ma14": float(np.mean(vals[-14:])),
            "weekday": cur_date.dayofweek,
            "is_weekend": int(cur_date.dayofweek >= 5),
            "month": cur_date.month,
            "day_of_month": cur_date.day,
        }
        X = pd.DataFrame([row])[FEATURES]
        pred = float(model.predict(X)[0])
        pred = max(pred, 0.0)
        out.append({"date": cur_date, "pred_gmv": round(pred, 2), "step": step})
        series = pd.concat([series, pd.Series([pred], index=[cur_date])])

    return pd.DataFrame(out)


def forecast_seasonal(data, horizon=FORECAST_DAYS):
    """
    季节调整版未来预测：
      1. 用全量数据算星期指数
      2. 去季节化后训练线性回归（lag1 + ma7）
      3. 递归预测去季节化值，再乘回当天星期因子
    相比纯递归，它能保住"周一低、周二高"的周内结构。
    """
    s = data["gmv"]
    base = s.mean()
    wk_factor = s.groupby(s.index.dayofweek).mean() / base
    wk_factor = wk_factor.reindex(range(7)).fillna(1.0)

    adj = s / wk_factor.reindex(s.index.dayofweek).values
    X = np.column_stack([adj.shift(1).values,
                         adj.shift(1).rolling(7).mean().values])
    y = adj.values
    mask = ~np.isnan(X).any(axis=1)
    model = LinearRegression().fit(X[mask], y[mask])

    hist = list(adj.values)
    last_date = s.index[-1]
    out = []
    for step in range(1, horizon + 1):
        cur_date = last_date + pd.Timedelta(days=step)
        x_row = np.array([[hist[-1], np.mean(hist[-7:])]])
        pred_adj = float(model.predict(x_row)[0])
        wd = cur_date.dayofweek
        pred = max(pred_adj * float(wk_factor.iloc[wd]), 0.0)
        out.append({"date": cur_date, "pred_gmv": round(pred, 2), "step": step})
        hist.append(pred_adj)
    return pd.DataFrame(out)


# ============================================================
# 7. 绘图
# ============================================================
def plot_all(df, dec, fc, bt_df, metrics_df):
    # 图 1：GMV 原始序列 + 7 日趋势
    fig, ax = plt.subplots(figsize=(12, 4.5))
    ax.plot(df.index, df["gmv"], lw=0.9, color="#4C78A8", label="每日 GMV")
    ax.plot(dec["trend"].index, dec["trend"], lw=2, color="#E45756", label="7 日趋势")
    ax.set_title("2024 年每日 GMV 与趋势", fontsize=13)
    ax.set_ylabel("GMV（元）")
    ax.legend()
    ax.grid(alpha=0.3)
    fig.tight_layout()
    fig.savefig(FIG_DIR / "01_gmv_series.png", dpi=150)
    plt.close(fig)

    # 图 2：星期效应
    wk = df.groupby(df.index.dayofweek)["gmv"].mean()
    names = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]
    fig, ax = plt.subplots(figsize=(8, 4))
    bars = ax.bar(names, wk.values, color="#72B7B2")
    ax.set_title("星期效应：各星期平均 GMV", fontsize=13)
    ax.set_ylabel("平均 GMV（元）")
    for b, v in zip(bars, wk.values):
        ax.text(b.get_x() + b.get_width() / 2, v, f"{v:,.0f}",
                ha="center", va="bottom", fontsize=9)
    ax.grid(axis="y", alpha=0.3)
    fig.tight_layout()
    fig.savefig(FIG_DIR / "02_weekday_effect.png", dpi=150)
    plt.close(fig)

    # 图 3：回测对比
    fig, ax = plt.subplots(figsize=(12, 4.5))
    ax.plot(bt_df["date"], bt_df["actual"], marker="o", ms=3, lw=1.2,
            color="#4C78A8", label="真实值")
    ax.plot(bt_df["date"], bt_df["pred_linreg"], marker="s", ms=3, lw=1.2,
            color="#E45756", label="线性回归")
    ax.plot(bt_df["date"], bt_df["pred_seasonal"], marker="D", ms=3, lw=1.4,
            color="#B279A2", label="季节调整模型")
    ax.plot(bt_df["date"], bt_df["pred_naive"], marker="^", ms=3, lw=1.2,
            color="#54A24B", alpha=0.8, label="朴素基线(lag1)")
    ax.set_title("滚动回测：最后 28 天预测 vs 真实", fontsize=13)
    ax.set_ylabel("GMV（元）")
    ax.legend()
    ax.grid(alpha=0.3)
    fig.tight_layout()
    fig.savefig(FIG_DIR / "03_backtest.png", dpi=150)
    plt.close(fig)

    # 图 4：未来 7 天预测
    fig, ax = plt.subplots(figsize=(12, 4.5))
    hist = df["gmv"].iloc[-60:]
    ax.plot(hist.index, hist.values, lw=1.2, color="#4C78A8", label="历史 60 天")
    ax.plot(fc["date"], fc["pred_gmv"], marker="o", lw=2,
            color="#F58518", label="未来 7 天预测")
    ax.axvline(df.index[-1], color="gray", ls="--", lw=1)
    ax.set_title("未来 7 天 GMV 预测", fontsize=13)
    ax.set_ylabel("GMV（元）")
    ax.legend()
    ax.grid(alpha=0.3)
    fig.tight_layout()
    fig.savefig(FIG_DIR / "04_forecast.png", dpi=150)
    plt.close(fig)

    # 图 5：模型对比柱状图
    fig, ax = plt.subplots(figsize=(9, 4))
    x = np.arange(len(metrics_df))
    w = 0.28
    ax.bar(x - w, metrics_df["MAE"], w, label="MAE（元）", color="#4C78A8")
    ax.bar(x, metrics_df["MAPE"], w, label="MAPE（%）", color="#E45756")
    ax.bar(x + w, metrics_df["sMAPE"], w, label="sMAPE（%）", color="#54A24B")
    ax.set_xticks(x)
    ax.set_xticklabels(metrics_df["model"], fontsize=9)
    ax.set_title("模型效果对比（越低越好）", fontsize=13)
    ax.legend()
    ax.grid(axis="y", alpha=0.3)
    fig.tight_layout()
    fig.savefig(FIG_DIR / "05_model_compare.png", dpi=150)
    plt.close(fig)


# ============================================================
# 8. 主流程
# ============================================================
def main():
    print("=" * 70)
    print("  第 3 周 · 时间序列分析与预测建模")
    print("=" * 70)
    if CN_FONT:
        print(f"[字体] 使用 {CN_FONT}")
    else:
        print("[字体] 未找到中文字体，图表中文可能显示为方块")

    # ---- 取数 ----
    print("\n[1/6] 从 Hive 读取每日 GMV 序列 ...")
    df = load_series()
    print(f"      读取 {len(df)} 天：{df.index.min().date()} ～ {df.index.max().date()}")
    print(f"      GMV 合计 {df['gmv'].sum():,.2f} 元，日均 {df['gmv'].mean():,.2f} 元")

    # ---- 分解 ----
    print("\n[2/6] 趋势分解（趋势 / 星期 / 残差）...")
    dec = decompose(df)
    wk = dec.groupby(df.index.dayofweek)["seasonal"].mean()
    wk_names = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]
    best_wd = wk.idxmax()
    worst_wd = wk.idxmin()
    print(f"      星期效应最强：{wk_names[best_wd]}（+{wk.max():,.0f} 元）")
    print(f"      星期效应最弱：{wk_names[worst_wd]}（{wk.min():,.0f} 元）")
    resid_sd = dec["residual"].std()
    print(f"      残差标准差：{resid_sd:,.2f} 元（占日均 {resid_sd / df['gmv'].mean() * 100:.1f}%）")

    # ---- 特征 ----
    print("\n[3/6] 特征工程 ...")
    feat = build_features(df)
    feat_ok = feat.dropna(subset=FEATURES + ["gmv"])
    print(f"      可用样本 {len(feat_ok)} 天，特征 {len(FEATURES)} 个")

    # ---- 回测 ----
    print("\n[4/6] 滚动回测（最后 28 天，逐日训练）...")
    n_test = 28

    p_lin, t_lin, d_lin = rolling_backtest(feat_ok, LinearRegression, n_test, "线性回归")
    print("      线性回归完成")

    p_ridge, t_ridge, _ = rolling_backtest(
        feat_ok, lambda: Ridge(alpha=10.0), n_test, "岭回归")
    print("      岭回归完成")

    p_naive, t_naive, d_naive = naive_baseline(feat_ok, n_test)
    print("      朴素基线完成")

    p_seas, t_seas, _ = seasonal_adjust_predict(feat_ok, n_test)
    print("      季节调整模型完成")

    xgb_ok = False
    try:
        from xgboost import XGBRegressor
        p_xgb, t_xgb, _ = rolling_backtest(
            feat_ok,
            lambda: XGBRegressor(n_estimators=300, max_depth=3, learning_rate=0.05,
                                 subsample=0.9, colsample_bytree=0.9,
                                 random_state=42, verbosity=0),
            n_test, "XGBoost")
        xgb_ok = True
        print("      XGBoost 完成")
    except Exception as e:
        print(f"      XGBoost 跳过：{type(e).__name__}: {e}")

    metrics = [
        evaluate(t_naive, p_naive, "朴素基线(lag1)"),
        evaluate(t_lin, p_lin, "线性回归"),
        evaluate(t_ridge, p_ridge, "岭回归"),
        evaluate(t_seas, p_seas, "季节调整+线性回归"),
    ]
    if xgb_ok:
        metrics.append(evaluate(t_xgb, p_xgb, "XGBoost"))
    metrics_df = pd.DataFrame(metrics).sort_values("MAPE")

    print("\n      回测结果（按 MAPE 升序）：")
    print(metrics_df.to_string(index=False))

    # ---- 全量重训 + 未来 7 天 ----
    print("\n[5/6] 全量重训并预测未来 7 天 ...")
    best_name = metrics_df.iloc[0]["model"]

    if best_name == "季节调整+线性回归":
        fc = forecast_seasonal(df, FORECAST_DAYS)
        fc["weekday"] = fc["date"].dt.dayofweek.map(lambda i: wk_names[i])
        final_desc = "季节调整 + 线性回归"
    else:
        if best_name == "XGBoost" and xgb_ok:
            from xgboost import XGBRegressor
            final_model = XGBRegressor(n_estimators=300, max_depth=3, learning_rate=0.05,
                                       subsample=0.9, colsample_bytree=0.9,
                                       random_state=42, verbosity=0)
        elif best_name == "岭回归":
            final_model = Ridge(alpha=10.0)
        else:
            final_model = LinearRegression()
        final_model.fit(feat_ok[FEATURES], feat_ok["gmv"])
        fc = forecast_future(df, final_model, FORECAST_DAYS)
        fc["weekday"] = fc["date"].dt.dayofweek.map(lambda i: wk_names[i])
        final_desc = best_name

    flat = diagnose_flatness(fc, df)

    # 同时跑一遍「纯递归线性回归」作为对照，用于证明均值收敛问题真实存在
    plain_model = LinearRegression()
    plain_model.fit(feat_ok[FEATURES], feat_ok["gmv"])
    fc_plain = forecast_future(df, plain_model, FORECAST_DAYS)
    flat_plain = diagnose_flatness(fc_plain, df)
    print(f"\n      [对照] 纯递归线性回归预测极差 {flat_plain['pred_range']:,.2f} 元，"
          f"CV {flat_plain['pred_cv_pct']}%（历史 {flat_plain['hist_cv_pct']}%）")

    print(f"      最优模型：{final_desc}")
    print(fc[["date", "weekday", "pred_gmv"]].to_string(index=False))
    print(f"      7 天合计预测 GMV：{fc['pred_gmv'].sum():,.2f} 元")
    print(f"\n      [诊断] 历史 CV {flat['hist_cv_pct']}% vs 预测 CV {flat['pred_cv_pct']}%"
          f"（比值 {flat['cv_ratio_pct']}%）")
    if flat["is_flat"]:
        print("      ⚠ 预测序列接近平线：模型实质在预测均值，周内波动未被捕捉")
    else:
        print("      ✓ 预测序列保留了历史波动结构")
    print(f"      预测极差 {flat['pred_range']:,.2f} 元")

    # ---- 落盘 ----
    print("\n[6/6] 输出文件 ...")
    bt_df = pd.DataFrame({
        "date": d_lin,
        "actual": t_lin,
        "pred_linreg": p_lin,
        "pred_ridge": p_ridge,
        "pred_naive": p_naive,
        "pred_seasonal": p_seas,
    })
    if xgb_ok:
        bt_df["pred_xgb"] = p_xgb

    dec.reset_index().to_csv(DATA_DIR / "decompose.csv", index=False, encoding="utf-8-sig")
    bt_df.to_csv(DATA_DIR / "backtest.csv", index=False, encoding="utf-8-sig")
    fc.to_csv(DATA_DIR / "forecast_7d.csv", index=False, encoding="utf-8-sig")
    metrics_df.to_csv(DATA_DIR / "model_metrics.csv", index=False, encoding="utf-8-sig")

    plot_all(df, dec, fc, bt_df, metrics_df)

    summary = {
        "series": {
            "days": len(df),
            "start": str(df.index.min().date()),
            "end": str(df.index.max().date()),
            "gmv_total": round(float(df["gmv"].sum()), 2),
            "gmv_mean": round(float(df["gmv"].mean()), 2),
            "gmv_std": round(float(df["gmv"].std()), 2),
            "gmv_min": round(float(df["gmv"].min()), 2),
            "gmv_max": round(float(df["gmv"].max()), 2),
        },
        "decompose": {
            "residual_std": round(float(resid_sd), 2),
            "residual_std_pct": round(float(resid_sd / df["gmv"].mean() * 100), 2),
            "best_weekday": wk_names[best_wd],
            "best_weekday_effect": round(float(wk.max()), 2),
            "worst_weekday": wk_names[worst_wd],
            "worst_weekday_effect": round(float(wk.min()), 2),
            "weekday_effects": {wk_names[i]: round(float(wk[i]), 2) for i in wk.index},
        },
        "backtest": {
            "n_test": n_test,
            "metrics": metrics_df.to_dict(orient="records"),
            "best_model": best_name,
        },
        "diagnosis": flat,
        "diagnosis_plain_recursive": flat_plain,
        "forecast": {
            "horizon": FORECAST_DAYS,
            "total": round(float(fc["pred_gmv"].sum()), 2),
            "mean": round(float(fc["pred_gmv"].mean()), 2),
            "rows": [
                {"date": str(r["date"].date()), "weekday": r["weekday"],
                 "pred_gmv": float(r["pred_gmv"])}
                for _, r in fc.iterrows()
            ],
        },
        "font": CN_FONT,
        "xgboost_available": xgb_ok,
    }
    with open(DATA_DIR / "forecast_summary.json", "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)

    print(f"      图表 → {FIG_DIR}")
    print(f"      数据 → {DATA_DIR}")
    print("\n完成。")
    return summary


if __name__ == "__main__":
    from console import init_console
    init_console()
    try:
        main()
    except Exception as e:
        import traceback
        traceback.print_exc()
        sys.exit(1)
