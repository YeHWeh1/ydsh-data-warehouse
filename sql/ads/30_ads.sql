-- ============================================================
-- 30_ads.sql —— 四层架构中的 ADS 应用层（面向指标与报表）
-- 上游：ydsh_dws（汇总层），少量全局 KPI 直接取 DWD 口径
-- 产物：答辩演示 / 看板用的 5 张指标表
-- 说明：金额口径统一 is_valid_amount=1；占比用窗口函数在结果集内计算
-- ============================================================

create database if not exists ydsh_ads;
use ydsh_ads;

-- ------------------------------------------------------------
-- 1. 核心 KPI 总览（指标名 / 值 / 单位 / 口径说明）
-- ------------------------------------------------------------
drop table if exists ads_kpi_overview;
create table ads_kpi_overview (
  kpi_name  string,
  kpi_value double,
  unit      string,
  calc_desc string
) stored as orc;

insert into table ads_kpi_overview
select '总订单量', count(*), '单', 'dwd_order 行数（已去重）' from ydsh_dwd.dwd_order;

insert into table ads_kpi_overview
select '有效订单量', sum(is_valid_amount), '单', '金额非空且未超 IQR 围栏' from ydsh_dwd.dwd_order;

insert into table ads_kpi_overview
select '总 GMV', round(sum(if(is_valid_amount = 1, pay_amount, 0)), 2), '元', '有效订单实付金额之和'
from ydsh_dwd.dwd_order;

insert into table ads_kpi_overview
select '客单价 AOV',
       round(sum(if(is_valid_amount = 1, pay_amount, 0)) / sum(is_valid_amount), 2), '元', 'GMV / 有效订单量'
from ydsh_dwd.dwd_order;

insert into table ads_kpi_overview
select '下单用户数', count(distinct user_id), '人', 'dwd_order 中去重用户数' from ydsh_dwd.dwd_order;

insert into table ads_kpi_overview
select '注册用户数', count(*), '人', 'dim_user 行数' from ydsh_dwd.dim_user;

insert into table ads_kpi_overview
select '复购用户数', count(*), '人', '有效订单数 >= 2 的用户'
from (select user_id from ydsh_dwd.dwd_order
      where is_valid_amount = 1
      group by user_id having count(*) >= 2) t;

-- ------------------------------------------------------------
-- 2. 品类销售排行
-- ------------------------------------------------------------
drop table if exists ads_category_rank;
create table ads_category_rank stored as orc as
select row_number() over (order by gmv desc) as rank_no,
       category,
       order_cnt,
       item_qty,
       item_sales,
       gmv,
       gmv_share_pct
from ydsh_dws.dws_category_sales;

-- ------------------------------------------------------------
-- 3. 区域（省份）销售排行
-- ------------------------------------------------------------
drop table if exists ads_region_rank;
create table ads_region_rank stored as orc as
select row_number() over (order by gmv desc) as rank_no,
       province,
       gmv,
       buyer_cnt,
       order_cnt,
       case when order_cnt = 0 then null else round(gmv / order_cnt, 2) end as aov,
       round(gmv * 100.0 / sum(gmv) over (), 2) as share_pct
from (
    select province,
           sum(gmv) as gmv,
           sum(buyer_cnt) as buyer_cnt,
           sum(order_cnt) as order_cnt
    from ydsh_dws.dws_region_sales
    group by province
) t;

-- ------------------------------------------------------------
-- 4. 渠道销售占比
-- ------------------------------------------------------------
drop table if exists ads_channel_share;
create table ads_channel_share stored as orc as
select channel,
       order_cnt,
       gmv,
       round(gmv * 100.0 / sum(gmv) over (), 2) as share_pct
from (
    select channel, sum(order_cnt) as order_cnt, round(sum(gmv), 2) as gmv
    from ydsh_dws.dws_channel_pay
    group by channel
) t;

-- ------------------------------------------------------------
-- 5. 用户价值分层（按累计消费四分位）
-- ------------------------------------------------------------
drop table if exists ads_user_segment;
create table ads_user_segment stored as orc as
select value_segment,
       count(*) as user_cnt,
       round(sum(total_gmv), 2) as gmv,
       round(sum(total_gmv) * 100.0 / sum(sum(total_gmv)) over (), 2) as share_pct,
       round(avg(total_gmv), 2) as avg_gmv
from ydsh_dws.dws_user_consume
group by value_segment;

-- ------------------------------------------------------------
-- 6. 校验
-- ------------------------------------------------------------
select 'ads_kpi_overview' as tbl, count(*) as cnt from ads_kpi_overview
union all
select 'ads_category_rank', count(*) from ads_category_rank
union all
select 'ads_region_rank', count(*) from ads_region_rank
union all
select 'ads_channel_share', count(*) from ads_channel_share
union all
select 'ads_user_segment', count(*) from ads_user_segment;
