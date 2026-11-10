-- ============================================================
-- 60_ads_ext.sql —— 第 3 周 ADS 应用层扩展
-- 上游：ydsh_dws（第 2 周 6 张 + 第 3 周 7 张扩展表）
-- 口径：见 docs/第3周_指标定义文档.md
-- 产物：面向业务场景的输出表（销售日报 / 品类 TopN / 用户分层 /
--       异常日 / KPI 总览 / 周月趋势），供看板、预测、NL2SQL 使用
-- ============================================================

create database if not exists ydsh_ads;
use ydsh_ads;

-- ------------------------------------------------------------
-- 1. 销售日报（业务方每天看的那张表）
--    含 DoD 环比、WoW 周同比、近 7 日均值、是否异常日
-- ------------------------------------------------------------
drop table if exists ads_sales_daily_report;
create table ads_sales_daily_report stored as orc as
select order_date,
       order_cnt,
       valid_order_cnt,
       gmv,
       aov,
       buyer_cnt,
       item_qty,
       items_per_order,
       discount_rate_pct,
       new_cust_orders,
       old_cust_orders,
       -- 日环比 DoD
       round((gmv - lag(gmv) over (order by order_date)) * 100.0
             / nullif(lag(gmv) over (order by order_date), 0), 2) as dod_pct,
       -- 周同比 WoW（7 天前同日，消除星期效应）
       round((gmv - lag(gmv, 7) over (order by order_date)) * 100.0
             / nullif(lag(gmv, 7) over (order by order_date), 0), 2) as wow_pct,
       -- 近 7 日移动平均
       round(avg(gmv) over (order by order_date
                            rows between 6 preceding and current row), 2) as ma7,
       -- 近 30 日均值与标准差（用于异常判定）
       round(avg(gmv) over (order by order_date
                            rows between 29 preceding and current row), 2) as ma30,
       round(stddev_pop(gmv) over (order by order_date
                                   rows between 29 preceding and current row), 2) as sd30
from ydsh_dws.dws_sales_day_ext
order by order_date;

-- ------------------------------------------------------------
-- 2. 异常日明细（|z| >= 2，口径见指标定义文档第七节）
-- ------------------------------------------------------------
drop table if exists ads_anomaly_daily;
create table ads_anomaly_daily stored as orc as
select order_date,
       gmv,
       ma30,
       sd30,
       round((gmv - ma30) / nullif(sd30, 0), 2) as z_score,
       case when (gmv - ma30) / nullif(sd30, 0) <= -2 then '偏低'
            when (gmv - ma30) / nullif(sd30, 0) >=  2 then '偏高'
            else 'normal' end as anomaly_type,
       wow_pct,
       order_cnt,
       aov
from ads_sales_daily_report
where ma30 is not null and sd30 is not null and sd30 > 0
  and abs((gmv - ma30) / sd30) >= 2
order by abs((gmv - ma30) / sd30) desc;

-- ------------------------------------------------------------
-- 3. 品类 TopN（全期 + 周维度演化）
-- ------------------------------------------------------------
drop table if exists ads_category_topn;
create table ads_category_topn stored as orc as
select rank_no,
       category,
       order_cnt,
       item_qty,
       gmv,
       gmv_share_pct,
       aov,
       case when rank_no <= 3 then 'Top3'
            when rank_no <= 5 then 'Top5'
            else 'Other' end as topn_group
from (
    select row_number() over (order by gmv desc) as rank_no,
           category,
           order_cnt,
           item_qty,
           gmv,
           gmv_share_pct,
           case when order_cnt = 0 then null else round(gmv / order_cnt, 2) end as aov
    from ydsh_dws.dws_category_sales
) t;

-- ------------------------------------------------------------
-- 4. 品类周趋势（宽表：每周一行，8 个品类各一列，供折线图）
--    注意：Hive 列名不支持中文，用拼音缩写 + 注释说明
--    yundong=运动户外 shuma=数码家电 fushi=服饰鞋包 shipin=食品生鲜
--    meizhuang=美妆个护 jiaju=家居生活 muying=母婴玩具 tushu=图书文娱
-- ------------------------------------------------------------
drop table if exists ads_category_week_pivot;
create table ads_category_week_pivot (
  week_start   string  comment '周起点（周一）',
  cat_yundong  double  comment '运动户外 GMV',
  cat_shuma    double  comment '数码家电 GMV',
  cat_fushi    double  comment '服饰鞋包 GMV',
  cat_shipin   double  comment '食品生鲜 GMV',
  cat_meizhuang double comment '美妆个护 GMV',
  cat_jiaju    double  comment '家居生活 GMV',
  cat_muying   double  comment '母婴玩具 GMV',
  cat_tushu    double  comment '图书文娱 GMV',
  total_gmv    double  comment '当周合计 GMV'
) stored as orc;

insert into table ads_category_week_pivot
select week_start,
       round(max(case when category = '运动户外' then gmv else 0 end), 2) as cat_yundong,
       round(max(case when category = '数码家电' then gmv else 0 end), 2) as cat_shuma,
       round(max(case when category = '服饰鞋包' then gmv else 0 end), 2) as cat_fushi,
       round(max(case when category = '食品生鲜' then gmv else 0 end), 2) as cat_shipin,
       round(max(case when category = '美妆个护' then gmv else 0 end), 2) as cat_meizhuang,
       round(max(case when category = '家居生活' then gmv else 0 end), 2) as cat_jiaju,
       round(max(case when category = '母婴玩具' then gmv else 0 end), 2) as cat_muying,
       round(max(case when category = '图书文娱' then gmv else 0 end), 2) as cat_tushu,
       round(sum(gmv), 2) as total_gmv
from ydsh_dws.dws_category_week
group by week_start
order by week_start;

-- ------------------------------------------------------------
-- 5. 用户价值分层（RFM，替代第 2 周四分位分层）
-- ------------------------------------------------------------
drop table if exists ads_user_rfm_segment;
create table ads_user_rfm_segment stored as orc as
select segment,
       count(*) as user_cnt,
       round(count(*) * 100.0 / sum(count(*)) over (), 2) as user_share_pct,
       round(sum(monetary), 2) as gmv,
       round(sum(monetary) * 100.0 / sum(sum(monetary)) over (), 2) as gmv_share_pct,
       round(avg(monetary), 2) as avg_monetary,
       round(avg(freq), 2) as avg_freq,
       round(avg(recency_days), 1) as avg_recency_days
from ydsh_dws.dws_user_rfm
group by segment
order by gmv desc;

-- ------------------------------------------------------------
-- 6. 核心 KPI 总览（第 3 周口径，含复购率饱和说明）
-- ------------------------------------------------------------
drop table if exists ads_kpi_week3;
create table ads_kpi_week3 (
  kpi_name  string,
  kpi_value double,
  unit      string,
  calc_desc string
) stored as orc;

insert into table ads_kpi_week3
select '总 GMV', round(sum(if(is_valid_amount = 1, pay_amount, 0)), 2), '元', '有效订单实付金额之和'
from ydsh_dwd.dwd_order;

insert into table ads_kpi_week3
select '订单量', count(*), '单', 'dwd_order 去重行数'
from ydsh_dwd.dwd_order;

insert into table ads_kpi_week3
select '有效订单量', sum(is_valid_amount), '单', 'is_valid_amount=1'
from ydsh_dwd.dwd_order;

insert into table ads_kpi_week3
select '客单价 AOV',
       round(sum(if(is_valid_amount = 1, pay_amount, 0)) / sum(is_valid_amount), 2),
       '元', 'GMV ÷ 有效订单量'
from ydsh_dwd.dwd_order;

insert into table ads_kpi_week3
select '活跃用户数', count(distinct user_id), '人', '有有效下单行为的去重用户'
from ydsh_dwd.dwd_order where is_valid_amount = 1;

insert into table ads_kpi_week3
select '复购用户数', count(*), '人', '有效订单数 >= 2 的用户'
from (select user_id from ydsh_dwd.dwd_order
      where is_valid_amount = 1 group by user_id having count(*) >= 2) t;

insert into table ads_kpi_week3
select '复购率',
       round(count(*) * 100.0 / (select count(distinct user_id)
                                 from ydsh_dwd.dwd_order where is_valid_amount = 1), 2),
       '%', '复购用户数 ÷ 活跃用户数（本数据集饱和，见指标定义文档第五节）'
from (select user_id from ydsh_dwd.dwd_order
      where is_valid_amount = 1 group by user_id having count(*) >= 2) t;

insert into table ads_kpi_week3
select '日均 GMV', round(avg(gmv), 2), '元', '366 天日 GMV 均值'
from ydsh_dws.dws_sales_day_ext;

insert into table ads_kpi_week3
select '客单件数', round(sum(item_qty) * 1.0 / sum(valid_order_cnt), 2), '件/单', '明细件数 ÷ 有效订单量'
from ydsh_dws.dws_sales_day_ext;

insert into table ads_kpi_week3
select '门店数', count(*), '家', 'dim_store 行数'
from ydsh_dwd.dim_store;

-- ------------------------------------------------------------
-- 7. 周报 / 月报（带环比）
-- ------------------------------------------------------------
drop table if exists ads_weekly_report;
create table ads_weekly_report stored as orc as
select week_start,
       week_end,
       active_days,
       order_cnt,
       gmv,
       aov,
       buyer_cnt,
       avg_daily_gmv,
       round((gmv - lag(gmv) over (order by week_start)) * 100.0
             / nullif(lag(gmv) over (order by week_start), 0), 2) as wow_pct
from ydsh_dws.dws_sales_week
order by week_start;

drop table if exists ads_monthly_report;
create table ads_monthly_report stored as orc as
select ym,
       active_days,
       order_cnt,
       gmv,
       aov,
       buyer_cnt,
       avg_daily_gmv,
       round((gmv - lag(gmv) over (order by ym)) * 100.0
             / nullif(lag(gmv) over (order by ym), 0), 2) as mom_pct
from ydsh_dws.dws_sales_month
order by ym;

-- ------------------------------------------------------------
-- 8. 渠道月度结构（渠道占比变化）
-- ------------------------------------------------------------
drop table if exists ads_channel_month_share;
create table ads_channel_month_share stored as orc as
select ym,
       channel,
       order_cnt,
       gmv,
       buyer_cnt,
       round(gmv * 100.0 / sum(gmv) over (partition by ym), 2) as share_pct
from ydsh_dws.dws_channel_month
order by ym, gmv desc;

-- ------------------------------------------------------------
-- 9. 校验（拆两条，避免 Tez 计数器上限）
-- ------------------------------------------------------------
select 'ads_sales_daily_report' as tbl, count(*) as cnt from ads_sales_daily_report
union all
select 'ads_anomaly_daily', count(*) from ads_anomaly_daily
union all
select 'ads_category_topn', count(*) from ads_category_topn
union all
select 'ads_category_week_pivot', count(*) from ads_category_week_pivot;

select 'ads_user_rfm_segment' as tbl, count(*) as cnt from ads_user_rfm_segment
union all
select 'ads_kpi_week3', count(*) from ads_kpi_week3
union all
select 'ads_weekly_report', count(*) from ads_weekly_report
union all
select 'ads_monthly_report', count(*) from ads_monthly_report
union all
select 'ads_channel_month_share', count(*) from ads_channel_month_share;
