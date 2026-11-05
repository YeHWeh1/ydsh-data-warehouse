-- ============================================================
-- 20_dws.sql —— 四层架构中的 DWS 汇总层（按主题轻度汇总）
-- 上游：ydsh_dwd（明细事实 + 维度）
-- 下游：ydsh_ads（应用层指标）
-- 粒度：日 / 品类 / 区域 / 渠道-支付 / 用户
-- 说明：汇总层用 CTAS 重建（stored as orc），便于口径迭代；
--       金额类指标统一只统计 is_valid_amount=1 的订单，避免被置空的异常金额污染
-- ============================================================

create database if not exists ydsh_dws;
use ydsh_dws;

-- ------------------------------------------------------------
-- 1. 日销售汇总：订单数、GMV、客单价、买家数、件数、明细金额
-- ------------------------------------------------------------
drop table if exists dws_sales_daily;
create table dws_sales_daily stored as orc as
select o.order_date,
       o.order_cnt,
       o.valid_order_cnt,
       o.gmv,
       case when o.valid_order_cnt = 0 then null
            else round(o.gmv / o.valid_order_cnt, 2) end as aov,
       o.buyer_cnt,
       nvl(i.item_qty, 0) as item_qty,
       nvl(i.item_amount, 0) as item_amount
from (
    select order_date,
           count(*) as order_cnt,
           sum(if(is_valid_amount = 1, 1, 0)) as valid_order_cnt,
           round(sum(if(is_valid_amount = 1, pay_amount, 0)), 2) as gmv,
           count(distinct user_id) as buyer_cnt
    from ydsh_dwd.dwd_order
    group by order_date
) o
left join (
    select order_date,
           sum(quantity) as item_qty,
           round(sum(item_amount), 2) as item_amount
    from ydsh_dwd.dwd_order_item
    group by order_date
) i on o.order_date = i.order_date;

-- ------------------------------------------------------------
-- 2. 品类销售汇总：订单数、件数、销售额、占比
--    口径：明细单价×数量与订单实付不同源，故按明细金额占比把订单实付分摊到品类；
--          品种合计覆盖 GMV 的 99.37%，剩余 94 单因明细金额全部不可用无法归类
-- ------------------------------------------------------------
drop table if exists dws_category_sales;
create table dws_category_sales stored as orc as
select category,
       count(distinct order_id) as order_cnt,
       sum(quantity) as item_qty,
       round(sum(item_amount), 2) as item_sales,
       round(sum(alloc_gmv), 2) as gmv,
       round(sum(alloc_gmv) * 100.0 / sum(sum(alloc_gmv)) over (), 2) as gmv_share_pct
from (
    select order_id, category, quantity, item_amount,
           case when order_item_total = 0 then 0
                else pay_amount * item_amount / order_item_total end as alloc_gmv
    from (
        select o.order_id,
               p.category,
               i.quantity,
               i.item_amount,
               o.pay_amount,
               sum(i.item_amount) over (partition by o.order_id) as order_item_total
        from ydsh_dwd.dwd_order_item i
        join ydsh_dwd.dwd_order o on i.order_id = o.order_id
        join ydsh_dwd.dim_product p on i.product_id = p.product_id
        where o.is_valid_amount = 1 and i.item_amount is not null
    ) t2
) t
group by category;

-- ------------------------------------------------------------
-- 3. 区域销售汇总：省份 / 城市
-- ------------------------------------------------------------
drop table if exists dws_region_sales;
create table dws_region_sales stored as orc as
select u.province,
       u.region,
       count(distinct o.user_id) as buyer_cnt,
       count(*) as order_cnt,
       round(sum(o.pay_amount), 2) as gmv,
       round(avg(o.pay_amount), 2) as aov
from ydsh_dwd.dwd_order o
join ydsh_dwd.dim_user u on o.user_id = u.user_id
where o.is_valid_amount = 1
group by u.province, u.region;

-- ------------------------------------------------------------
-- 4. 渠道 + 支付方式汇总
-- ------------------------------------------------------------
drop table if exists dws_channel_pay;
create table dws_channel_pay stored as orc as
select channel,
       pay_type,
       count(*) as order_cnt,
       round(sum(pay_amount), 2) as gmv
from ydsh_dwd.dwd_order
where is_valid_amount = 1
group by channel, pay_type;

-- ------------------------------------------------------------
-- 5. 用户消费汇总：累计消费、窗口函数排名、首末单
-- ------------------------------------------------------------
drop table if exists dws_user_consume;
create table dws_user_consume stored as orc as
select user_id,
       order_cnt,
       total_gmv,
       avg_gmv,
       first_order_date,
       last_order_date,
       gmv_rank,
       cum_gmv,
       case ntile(4) over (order by total_gmv desc)
            when 1 then '高价值'
            when 2 then '潜力'
            when 3 then '一般'
            else '低价值' end as value_segment
from (
    select user_id,
           count(*) as order_cnt,
           round(sum(pay_amount), 2) as total_gmv,
           round(avg(pay_amount), 2) as avg_gmv,
           min(order_date) as first_order_date,
           max(order_date) as last_order_date,
           row_number() over (order by sum(pay_amount) desc) as gmv_rank,
           round(sum(sum(pay_amount)) over (
                 order by sum(pay_amount) desc
                 rows between unbounded preceding and current row), 2) as cum_gmv
    from ydsh_dwd.dwd_order
    where is_valid_amount = 1
    group by user_id
) t;

-- ------------------------------------------------------------
-- 6. 门店销售汇总：门店粒度（含坪效 = GMV / 营业面积）
--    任务书子任务 7 要求"品类 / 省份 / 门店 / 渠道"四个维度的分布分析，
--    门店维度单独建汇总表，供 ADS 排行与探索性分析使用
-- ------------------------------------------------------------
drop table if exists dws_store_sales;
create table dws_store_sales stored as orc as
select o.store_id,
       s.store_name,
       s.province,
       s.city,
       s.district,
       s.area,
       s.open_date,
       count(*) as order_cnt,
       count(distinct o.user_id) as buyer_cnt,
       round(sum(o.pay_amount), 2) as gmv,
       round(avg(o.pay_amount), 2) as aov,
       case when s.area is null or s.area = 0 then null
            else round(sum(o.pay_amount) / s.area, 2) end as gmv_per_sqm
from ydsh_dwd.dwd_order o
join ydsh_dwd.dim_store s on o.store_id = s.store_id
where o.is_valid_amount = 1
group by o.store_id, s.store_name, s.province, s.city, s.district, s.area, s.open_date;

-- ------------------------------------------------------------
-- 7. 校验（拆成两条，避免单个 DAG 顶点过多触发 Tez 计数器上限）
-- ------------------------------------------------------------
select 'dws_sales_daily' as tbl, count(*) as cnt from dws_sales_daily
union all
select 'dws_category_sales', count(*) from dws_category_sales
union all
select 'dws_region_sales', count(*) from dws_region_sales;

select 'dws_channel_pay' as tbl, count(*) as cnt from dws_channel_pay
union all
select 'dws_user_consume', count(*) from dws_user_consume
union all
select 'dws_store_sales', count(*) from dws_store_sales;
