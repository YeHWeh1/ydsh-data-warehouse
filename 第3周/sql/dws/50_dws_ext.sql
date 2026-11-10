-- ============================================================
-- 50_dws_ext.sql —— 第 3 周 DWS 汇总层扩展
-- 上游：ydsh_dwd（明细事实 + 维度）
-- 下游：ydsh_ads（第 3 周 ADS 扩展）
-- 口径：见 docs/第3周_指标定义文档.md（唯一口径来源）
--       金额类一律 is_valid_amount = 1
-- 说明：第 2 周已有 dws_sales_daily / dws_category_sales / dws_region_sales
--       / dws_channel_pay / dws_user_consume / dws_store_sales，
--       本脚本只做「第 3 周新增的多维扩展」，不覆盖已有表。
-- ============================================================

create database if not exists ydsh_dws;
use ydsh_dws;

-- ------------------------------------------------------------
-- 1. 日粒度宽表（在日汇总基础上补：客单件数、折扣率、新老客）
--    口径：订单数/买家数按全量，金额按有效订单
-- ------------------------------------------------------------
drop table if exists dws_sales_day_ext;
create table dws_sales_day_ext stored as orc as
select o.order_date,
       count(*) as order_cnt,
       sum(if(o.is_valid_amount = 1, 1, 0)) as valid_order_cnt,
       round(sum(if(o.is_valid_amount = 1, o.pay_amount, 0)), 2) as gmv,
       case when sum(if(o.is_valid_amount = 1, 1, 0)) = 0 then null
            else round(sum(if(o.is_valid_amount = 1, o.pay_amount, 0))
                       / sum(if(o.is_valid_amount = 1, 1, 0)), 2) end as aov,
       count(distinct o.user_id) as buyer_cnt,
       nvl(sum(if(o.is_valid_amount = 1, i.qty, 0)), 0) as item_qty,
       case when sum(if(o.is_valid_amount = 1, 1, 0)) = 0 then null
            else round(nvl(sum(if(o.is_valid_amount = 1, i.qty, 0)), 0) * 1.0
                       / sum(if(o.is_valid_amount = 1, 1, 0)), 2) end as items_per_order,
       -- 优惠率：优惠金额 / (实付 + 优惠)，只在有效金额单上算
       case when sum(if(o.is_valid_amount = 1, o.pay_amount + nvl(o.discount_amount, 0), 0)) = 0 then null
            else round(sum(if(o.is_valid_amount = 1, nvl(o.discount_amount, 0), 0)) * 100.0
                       / sum(if(o.is_valid_amount = 1, o.pay_amount + nvl(o.discount_amount, 0), 0)), 2)
       end as discount_rate_pct,
       -- 新客：当天为全历史首次下单；老客：当天非首次
       sum(if(o.is_valid_amount = 1 and o.order_date = u.first_order_date, 1, 0)) as new_cust_orders,
       sum(if(o.is_valid_amount = 1 and o.order_date > u.first_order_date, 1, 0)) as old_cust_orders
from (
    select order_id, order_date, user_id, pay_amount, discount_amount, is_valid_amount
    from ydsh_dwd.dwd_order
) o
left join (
    select order_id, sum(quantity) as qty
    from ydsh_dwd.dwd_order_item
    group by order_id
) i on o.order_id = i.order_id
join (
    select user_id, min(order_date) as first_order_date
    from ydsh_dwd.dwd_order
    where is_valid_amount = 1
    group by user_id
) u on o.user_id = u.user_id
group by o.order_date
order by o.order_date;

-- ------------------------------------------------------------
-- 2. 周汇总（周起点=周一，相对 2024-01-01 取模，跨年不裂周）
-- ------------------------------------------------------------
drop table if exists dws_sales_week;
create table dws_sales_week stored as orc as
select week_start,
       date_add(week_start, 6) as week_end,
       count(distinct order_date) as active_days,
       count(*) as order_cnt,
       sum(if(is_valid_amount = 1, 1, 0)) as valid_order_cnt,
       round(sum(if(is_valid_amount = 1, pay_amount, 0)), 2) as gmv,
       case when sum(if(is_valid_amount = 1, 1, 0)) = 0 then null
            else round(sum(if(is_valid_amount = 1, pay_amount, 0))
                       / sum(if(is_valid_amount = 1, 1, 0)), 2) end as aov,
       count(distinct user_id) as buyer_cnt,
       round(sum(if(is_valid_amount = 1, pay_amount, 0)) / count(distinct order_date), 2) as avg_daily_gmv
from (
    select order_date, user_id, pay_amount, is_valid_amount,
           date_sub(order_date, pmod(datediff(order_date, '2024-01-01'), 7)) as week_start
    from ydsh_dwd.dwd_order
) t
group by week_start
order by week_start;

-- ------------------------------------------------------------
-- 3. 月汇总
-- ------------------------------------------------------------
drop table if exists dws_sales_month;
create table dws_sales_month stored as orc as
select ym,
       count(distinct order_date) as active_days,
       count(*) as order_cnt,
       sum(if(is_valid_amount = 1, 1, 0)) as valid_order_cnt,
       round(sum(if(is_valid_amount = 1, pay_amount, 0)), 2) as gmv,
       case when sum(if(is_valid_amount = 1, 1, 0)) = 0 then null
            else round(sum(if(is_valid_amount = 1, pay_amount, 0))
                       / sum(if(is_valid_amount = 1, 1, 0)), 2) end as aov,
       count(distinct user_id) as buyer_cnt,
       round(sum(if(is_valid_amount = 1, pay_amount, 0)) / count(distinct order_date), 2) as avg_daily_gmv
from (
    select order_date, user_id, pay_amount, is_valid_amount,
           substr(order_date, 1, 7) as ym
    from ydsh_dwd.dwd_order
) t
group by ym
order by ym;

-- ------------------------------------------------------------
-- 4. 周 × 品类 汇总（用于品类趋势与 TopN 演化）
-- ------------------------------------------------------------
drop table if exists dws_category_week;
create table dws_category_week stored as orc as
select week_start,
       category,
       count(distinct order_id) as order_cnt,
       sum(quantity) as item_qty,
       round(sum(alloc_gmv), 2) as gmv
from (
    select date_sub(o.order_date, pmod(datediff(o.order_date, '2024-01-01'), 7)) as week_start,
           o.order_id,
           p.category,
           o.quantity,
           case when order_item_total = 0 then 0
                else o.pay_amount * o.item_amount / order_item_total end as alloc_gmv
    from (
        select oi.order_id, oi.product_id, oi.quantity, oi.item_amount,
               o.pay_amount, o.order_date,
               sum(oi.item_amount) over (partition by oi.order_id) as order_item_total
        from ydsh_dwd.dwd_order_item oi
        join ydsh_dwd.dwd_order o on oi.order_id = o.order_id
        where o.is_valid_amount = 1 and oi.item_amount is not null
    ) o
    join ydsh_dwd.dim_product p on o.product_id = p.product_id
) t
group by week_start, category
order by week_start, gmv desc;

-- ------------------------------------------------------------
-- 5. 渠道 × 月 汇总（渠道结构变化）
-- ------------------------------------------------------------
drop table if exists dws_channel_month;
create table dws_channel_month stored as orc as
select substr(order_date, 1, 7) as ym,
       channel,
       count(*) as order_cnt,
       round(sum(pay_amount), 2) as gmv,
       count(distinct user_id) as buyer_cnt
from ydsh_dwd.dwd_order
where is_valid_amount = 1
group by substr(order_date, 1, 7), channel
order by ym, gmv desc;

-- ------------------------------------------------------------
-- 6. 门店 × 月 汇总（门店经营趋势，含坪效）
-- ------------------------------------------------------------
drop table if exists dws_store_month;
create table dws_store_month stored as orc as
select substr(o.order_date, 1, 7) as ym,
       o.store_id,
       s.store_name,
       s.province,
       s.city,
       s.area,
       count(*) as order_cnt,
       count(distinct o.user_id) as buyer_cnt,
       round(sum(o.pay_amount), 2) as gmv,
       round(avg(o.pay_amount), 2) as aov,
       case when s.area is null or s.area = 0 then null
            else round(sum(o.pay_amount) / s.area, 2) end as gmv_per_sqm
from ydsh_dwd.dwd_order o
join ydsh_dwd.dim_store s on o.store_id = s.store_id
where o.is_valid_amount = 1
group by substr(o.order_date, 1, 7), o.store_id, s.store_name, s.province, s.city, s.area
order by ym, gmv desc;

-- ------------------------------------------------------------
-- 7. 用户 RFM 分层明细（本周新增，替代第 2 周的四分位分层）
--    R/F/M 各 ntile(5) 打分；分层规则见指标定义文档第八节
-- ------------------------------------------------------------
drop table if exists dws_user_rfm;
create table dws_user_rfm stored as orc as
select user_id,
       recency_days,
       freq,
       monetary,
       r_score,
       f_score,
       m_score,
       concat(cast(r_score as string), cast(f_score as string), cast(m_score as string)) as rfm_code,
       case
         when r_score >= 4 and f_score >= 4 and m_score >= 4 then '重要价值客户'
         when r_score <  4 and f_score >= 4 and m_score >= 4 then '重要保持客户'
         when r_score >= 4 and f_score <  4 and m_score >= 4 then '重要发展客户'
         when r_score <= 2 and f_score <= 2 then '流失预警'
         else '一般客户'
       end as segment
from (
    select user_id,
           recency_days,
           freq,
           monetary,
           6 - ntile(5) over (order by recency_days asc)  as r_score,
           ntile(5) over (order by freq asc)              as f_score,
           ntile(5) over (order by monetary asc)          as m_score
    from (
        select user_id,
               datediff('2024-12-31', max(order_date)) as recency_days,
               count(*) as freq,
               round(sum(pay_amount), 2) as monetary
        from ydsh_dwd.dwd_order
        where is_valid_amount = 1
        group by user_id
    ) t1
) t2;

-- ------------------------------------------------------------
-- 8. 校验（拆两条，避免 Tez 计数器上限）
-- ------------------------------------------------------------
select 'dws_sales_day_ext' as tbl, count(*) as cnt from dws_sales_day_ext
union all
select 'dws_sales_week', count(*) from dws_sales_week
union all
select 'dws_sales_month', count(*) from dws_sales_month
union all
select 'dws_category_week', count(*) from dws_category_week;

select 'dws_channel_month' as tbl, count(*) as cnt from dws_channel_month
union all
select 'dws_store_month', count(*) from dws_store_month
union all
select 'dws_user_rfm', count(*) from dws_user_rfm;
