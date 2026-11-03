-- ============================================================
-- 40_eda.sql —— 子任务 7：探索性数据分析（1 课时）
-- 依据任务书 子任务 7 / 部署手册 步骤 8
-- 全部基于 DWD 明细层（部分直接读 DWS/ADS 做交叉验证）
-- 内容：核心 KPI、月度趋势、品类/区域/渠道分布、时段分布、会员贡献、
--       窗口函数（用户累计消费与排名）、行为漏斗、复购分布
-- 说明：金额统一 cast 成 decimal(18,2) 输出，避免 beeline 用科学计数法显示 double
-- ============================================================

use ydsh_dwd;

-- ------------------------------------------------------------
-- 1. 核心 KPI（总订单、有效订单、GMV、客单价、买家数）
-- ------------------------------------------------------------
select count(*) as orders,
       sum(is_valid_amount) as valid_orders,
       cast(round(sum(if(is_valid_amount = 1, pay_amount, 0)), 2) as decimal(18, 2)) as gmv,
       cast(round(sum(if(is_valid_amount = 1, pay_amount, 0)) / sum(is_valid_amount), 2) as decimal(18, 2)) as aov,
       count(distinct user_id) as buyers
from dwd_order;

-- ------------------------------------------------------------
-- 2. 月度趋势
-- ------------------------------------------------------------
select substr(order_date, 1, 7) as order_month,
       count(*) as orders,
       sum(is_valid_amount) as valid_orders,
       cast(round(sum(if(is_valid_amount = 1, pay_amount, 0)), 2) as decimal(18, 2)) as gmv,
       cast(round(sum(if(is_valid_amount = 1, pay_amount, 0)) / sum(is_valid_amount), 2) as decimal(18, 2)) as aov,
       count(distinct if(is_valid_amount = 1, user_id, null)) as buyers
from dwd_order
group by substr(order_date, 1, 7)
order by order_month;

-- ------------------------------------------------------------
-- 3. 品类销售排行（读 ADS 结果，验证与 DWS 一致）
-- ------------------------------------------------------------
select rank_no, category, order_cnt, item_qty,
       cast(item_sales as decimal(18, 2)) as item_sales,
       cast(gmv as decimal(18, 2)) as gmv,
       gmv_share_pct
from ydsh_ads.ads_category_rank
order by rank_no;

-- ------------------------------------------------------------
-- 4. 省份 TOP 与区域分布
-- ------------------------------------------------------------
select rank_no, province,
       cast(gmv as decimal(18, 2)) as gmv,
       buyer_cnt, order_cnt,
       cast(aov as decimal(18, 2)) as aov,
       share_pct
from ydsh_ads.ads_region_rank
order by rank_no limit 8;

select region, order_cnt,
       cast(gmv as decimal(18, 2)) as gmv,
       cast(aov as decimal(18, 2)) as aov
from ydsh_dws.dws_region_sales
order by gmv desc limit 10;

-- ------------------------------------------------------------
-- 5. 渠道 × 支付方式
-- ------------------------------------------------------------
select channel, order_cnt,
       cast(gmv as decimal(18, 2)) as gmv,
       share_pct
from ydsh_ads.ads_channel_share
order by gmv desc;

select channel, pay_type, order_cnt, cast(gmv as decimal(18, 2)) as gmv
from ydsh_dws.dws_channel_pay
order by channel, gmv desc;

-- ------------------------------------------------------------
-- 6. 下单时段分布（找峰值时段）
-- ------------------------------------------------------------
select order_hour,
       count(*) as orders,
       cast(round(sum(if(is_valid_amount = 1, pay_amount, 0)), 2) as decimal(18, 2)) as gmv
from dwd_order
group by order_hour
order by order_hour;

-- ------------------------------------------------------------
-- 7. 会员等级 / 性别 / 年龄段的消费贡献
-- ------------------------------------------------------------
select u.member_level,
       count(*) as orders,
       count(distinct u.user_id) as users,
       cast(round(sum(o.pay_amount), 2) as decimal(18, 2)) as gmv,
       cast(round(avg(o.pay_amount), 2) as decimal(18, 2)) as aov
from dwd_order o
join dim_user u on o.user_id = u.user_id
where o.is_valid_amount = 1
group by u.member_level
order by gmv desc;

select u.gender,
       count(*) as orders,
       count(distinct u.user_id) as users,
       cast(round(sum(o.pay_amount), 2) as decimal(18, 2)) as gmv
from dwd_order o
join dim_user u on o.user_id = u.user_id
where o.is_valid_amount = 1
group by u.gender
order by gmv desc;

select u.age_band,
       count(*) as orders,
       cast(round(sum(o.pay_amount), 2) as decimal(18, 2)) as gmv,
       cast(round(avg(o.pay_amount), 2) as decimal(18, 2)) as aov
from dwd_order o
join dim_user u on o.user_id = u.user_id
where o.is_valid_amount = 1
group by u.age_band
order by gmv desc;

-- ------------------------------------------------------------
-- 8. 窗口函数：用户累计消费与单笔排名
-- ------------------------------------------------------------
select user_id, order_id, order_date,
       cast(pay_amount as decimal(18, 2)) as pay_amount,
       cast(sum(pay_amount) over (
           partition by user_id order by order_date, order_id
           rows between unbounded preceding and current row) as decimal(18, 2)) as cum_amount,
       row_number() over (partition by user_id order by pay_amount desc) as amt_rank_in_user
from dwd_order
where is_valid_amount = 1
order by user_id, order_date
limit 15;

select user_id, order_cnt,
       cast(total_gmv as decimal(18, 2)) as total_gmv,
       cast(avg_gmv as decimal(18, 2)) as avg_gmv,
       first_order_date, last_order_date, gmv_rank, value_segment
from ydsh_dws.dws_user_consume
order by gmv_rank limit 10;

-- ------------------------------------------------------------
-- 9. 用户行为漏斗（pv → cart → buy）
-- ------------------------------------------------------------
select behavior_type, behavior_name,
       count(*) as pv_cnt,
       count(distinct user_id) as uv,
       count(distinct session_id) as sessions
from dwd_user_behavior
group by behavior_type, behavior_name
order by pv_cnt desc;

-- ------------------------------------------------------------
-- 10. 复购分布（按用户有效订单数分层）
-- ------------------------------------------------------------
select seg, count(*) as users,
       cast(round(sum(gmv), 2) as decimal(18, 2)) as gmv,
       round(sum(gmv) * 100.0 / sum(sum(gmv)) over (), 2) as gmv_share_pct
from (
    select user_id, cnt, gmv,
           case when cnt = 1 then '1 单'
                when cnt <= 3 then '2-3 单'
                when cnt <= 6 then '4-6 单'
                else '7 单以上' end as seg
    from (
        select user_id, count(*) as cnt, sum(pay_amount) as gmv
        from dwd_order
        where is_valid_amount = 1
        group by user_id
    ) t
) s
group by seg
order by gmv desc;

-- ------------------------------------------------------------
-- 11. 用户价值分层
-- ------------------------------------------------------------
select value_segment, user_cnt,
       cast(gmv as decimal(18, 2)) as gmv,
       share_pct,
       cast(avg_gmv as decimal(18, 2)) as avg_gmv
from ydsh_ads.ads_user_segment
order by gmv desc;
