-- ============================================================
-- 12_split_merge.sql —— 子任务 3：字段拆分合并（1 课时）
-- 依据任务书 子任务 3 / 部署手册 步骤 3
-- 合并：order_date + order_time → order_timestamp（完整下单时间）
--       province + city         → region（省份-城市，便于区域聚合）
-- 拆分：order_time → order_hour（下单小时，用于时段分析）
--       register_date → register_month（注册年月，用于月度留存分析）
--       email → email_domain（邮箱域名，用于用户来源分析）
-- 顺带完成：订单去重（沿用第 1 周规则，保证 DWD 事实表一单一行）
-- 产物：cln_user_full / cln_order_full / cln_store_full，作为 DWD 加载的输入
-- ============================================================

use ydsh_dwd;

-- ------------------------------------------------------------
-- 1. 用户：region 合并 + 年龄分段 + 注册年月 + 邮箱域名拆分
-- ------------------------------------------------------------
drop table if exists cln_user_full;
create table cln_user_full stored as orc as
select user_id,
       user_name,
       gender,
       gender_filled,
       age,
       case when age < 25 then '18-24'
            when age < 35 then '25-34'
            when age < 45 then '35-44'
            when age < 55 then '45-54'
            else '55+' end as age_band,
       age_filled,
       city,
       province,
       concat(province, '-', city) as region,
       register_date,
       substr(register_date, 1, 7) as register_month,
       member_level,
       member_level_filled,
       phone,
       email,
       case when email is null or trim(email) = '' then null
            else split(email, '@')[1] end as email_domain,
       email_missing
from cln_user;

-- ------------------------------------------------------------
-- 2. 订单：日期+时间合并、时间拆分、金额净额、去重
--    去重规则（与第 1 周一致）：同一 order_id 保留下单时间最晚的一条，
--    时间相同取金额较大者；仍相同按 store_id / channel 保证结果确定。
-- ------------------------------------------------------------
drop table if exists cln_order_full;
create table cln_order_full stored as orc as
select order_id, user_id, store_id,
       order_timestamp, order_hour, order_date, order_month,
       pay_amount, discount_amount, net_amount,
       amount_flag, is_valid_amount, discount_flag,
       order_status, pay_type, channel,
       order_status_raw, pay_type_raw, channel_raw
from (
    select t.*,
           row_number() over (
               partition by order_id
               order by order_timestamp desc, pay_amount desc nulls last,
                        store_id desc, channel desc
           ) as rn
    from (
        select order_id,
               user_id,
               store_id,
               concat(order_date, ' ', order_time) as order_timestamp,
               cast(substr(order_time, 1, 2) as int) as order_hour,
               order_date,
               substr(order_date, 1, 7) as order_month,
               pay_amount,
               discount_amount,
               case when pay_amount is null then null
                    else pay_amount - discount_amount end as net_amount,
               amount_flag,
               is_valid_amount,
               discount_flag,
               order_status,
               pay_type,
               channel,
               order_status_raw,
               pay_type_raw,
               channel_raw
        from cln_order
    ) t
) x
where rn = 1;

-- ------------------------------------------------------------
-- 3. 门店：region 合并
-- ------------------------------------------------------------
drop table if exists cln_store_full;
create table cln_store_full stored as orc as
select store_id,
       store_name,
       province,
       city,
       district,
       concat(province, '-', city) as region,
       address,
       open_date,
       area,
       manager
from ydsh_ods.ods_store;

-- ------------------------------------------------------------
-- 4. 校验：时间戳格式、去重结果、字段拆分结果
-- ------------------------------------------------------------
-- 4.1 订单时间戳 + 去重
select count(*) as total,
       count(distinct order_id) as distinct_oid,
       sum(if(length(order_timestamp) = 19, 1, 0)) as ok_len19,
       min(order_timestamp) as min_ts,
       max(order_timestamp) as max_ts,
       count(distinct order_date) as date_cnt,
       count(distinct order_hour) as hour_cnt
from cln_order_full;

-- 4.2 用户区域合并与邮箱域名拆分
select count(*) as total,
       count(distinct region) as region_cnt,
       count(distinct province) as province_cnt,
       count(distinct email_domain) as domain_cnt,
       sum(if(email_domain is null, 1, 0)) as no_domain,
       count(distinct age_band) as band_cnt,
       count(distinct register_month) as reg_month_cnt
from cln_user_full;

-- 4.3 拆分结果抽样
select order_id, order_timestamp, order_hour, order_month from cln_order_full limit 3;
select user_id, province, city, region, email, email_domain, age, age_band from cln_user_full limit 3;
