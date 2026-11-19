-- ============================================================
-- 70_ads_week4.sql —— 第 4 周 ADS 层补充（可视化专用）
--
-- 为什么需要这张表：
--   任务书子任务 2 要求「热力图（时段×品类）」，但前 3 周的 ADS 表里
--   没有任何一张带 order_hour 维度的汇总表，Superset 与 ECharts 都取不到数。
--   这里补一张，口径与其它 ADS 表保持一致（金额只算 is_valid_amount=1）。
--
-- 执行：docker exec -i hive-server beeline -u jdbc:hive2://localhost:10000 -f /opt/hive/data/70_ads_week4.sql
-- 幂等：drop + create，可重复执行
-- ============================================================

create database if not exists ydsh_ads;
use ydsh_ads;

-- ------------------------------------------------------------
-- 1. 时段 × 品类 热力表（24 小时 × 8 品类 = 192 行）
--    同时给出订单量与 GMV 两个度量，热力图可切换
-- ------------------------------------------------------------
drop table if exists ads_hour_category_heat;
create table ads_hour_category_heat stored as orc as
select o.order_hour                              as order_hour,
       p.category                                as category,
       count(distinct o.order_id)                as order_cnt,
       sum(i.quantity)                           as item_qty,
       round(sum(if(o.is_valid_amount = 1, i.item_amount, 0)), 2) as gmv
from ydsh_dwd.dwd_order o
join ydsh_dwd.dwd_order_item i on o.order_id = i.order_id
join ydsh_dwd.dim_product   p on i.product_id = p.product_id
group by o.order_hour, p.category
order by o.order_hour, p.category;

-- ------------------------------------------------------------
-- 2. 大屏顶部 KPI 卡片（单行多列，前端一次取完，避免多次请求）
--    列名与前端字段一一对应，不做二次计算
-- ------------------------------------------------------------
drop table if exists ads_screen_kpi;
create table ads_screen_kpi stored as orc as
select round(sum(if(is_valid_amount = 1, pay_amount, 0)), 2) as total_gmv,
       count(*)                                             as order_cnt,
       round(sum(if(is_valid_amount = 1, pay_amount, 0))
             / sum(is_valid_amount), 2)                     as aov,
       count(distinct user_id)                              as buyer_cnt,
       round(avg(if(is_valid_amount = 1, pay_amount, 0)), 2) as avg_pay
from ydsh_dwd.dwd_order;

-- ------------------------------------------------------------
-- 3. 品类 × 月份 结构演化（堆叠面积图用）
--    已有 ads_category_week_pivot 是周粒度，大屏月度视图需要月粒度
-- ------------------------------------------------------------
drop table if exists ads_category_month_pivot;
create table ads_category_month_pivot stored as orc as
select ym,
       round(sum(if(category = '运动户外', gmv, 0)), 2) as cat_yundong,
       round(sum(if(category = '数码家电', gmv, 0)), 2) as cat_shuma,
       round(sum(if(category = '服饰鞋包', gmv, 0)), 2) as cat_fushi,
       round(sum(if(category = '食品生鲜', gmv, 0)), 2) as cat_shipin,
       round(sum(if(category = '美妆个护', gmv, 0)), 2) as cat_meizhuang,
       round(sum(if(category = '家居生活', gmv, 0)), 2) as cat_jiaju,
       round(sum(if(category = '母婴玩具', gmv, 0)), 2) as cat_muying,
       round(sum(if(category = '图书文娱', gmv, 0)), 2) as cat_tushu,
       round(sum(gmv), 2)                                          as total_gmv
from (
    select substr(o.order_date, 1, 7) as ym,
           p.category                 as category,
           sum(i.item_amount)         as gmv
    from ydsh_dwd.dwd_order o
    join ydsh_dwd.dwd_order_item i on o.order_id = i.order_id
    join ydsh_dwd.dim_product   p on i.product_id = p.product_id
    where o.is_valid_amount = 1
    group by substr(o.order_date, 1, 7), p.category
) t
group by ym
order by ym;

-- ------------------------------------------------------------
-- 4. 支付方式占比（任务书明确要求的饼图，前 3 周只有渠道，没有支付方式）
-- ------------------------------------------------------------
drop table if exists ads_paytype_share;
create table ads_paytype_share stored as orc as
select pay_type,
       count(*)                            as order_cnt,
       round(sum(pay_amount), 2)           as gmv,
       round(count(*) * 100.0 / sum(count(*)) over (), 2) as order_share_pct,
       round(sum(pay_amount) * 100.0 / sum(sum(pay_amount)) over (), 2) as gmv_share_pct
from ydsh_dwd.dwd_order
where is_valid_amount = 1
group by pay_type
order by gmv desc;

-- ------------------------------------------------------------
-- 5. 校验
-- ------------------------------------------------------------
select 'ads_hour_category_heat'   as tbl, count(*) as cnt from ads_hour_category_heat
union all
select 'ads_screen_kpi',          count(*) from ads_screen_kpi
union all
select 'ads_category_month_pivot',count(*) from ads_category_month_pivot
union all
select 'ads_paytype_share',       count(*) from ads_paytype_share;
