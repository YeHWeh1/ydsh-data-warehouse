-- ============================================================
-- 11_anomaly_format.sql —— 子任务 2：异常值与格式标准化（2 课时）
-- 依据任务书 子任务 2 / 部署手册 步骤 2
-- 处理对象：
--   ods_user.age          空值 / <1 / >120
--   ods_order.pay_amount  负金额 310 条、超大金额 312 条
--   ods_order.discount_amount 负优惠 769 条
--   ods_product.price     负价格 20 条、超大价格 16 条
--   ods_order_item.quantity  -2（332 条）、0（386 条）、99（363 条）
--   ods_order_item.unit_price 负单价 798 条
--   日期：register_date / order_date 三种写法 → yyyy-MM-dd
--   手机号：去 "-" → 11 位纯数字
--   行为时间：yyyy/MM/dd HH:mm → yyyy-MM-dd HH:mm:ss
--
-- 阈值策略（重要）：
--   部署手册建议用 P99 定金额阈值，但本数据集被注入了 291 条百万级超大金额，
--   pay_amount 的 P99 被脏数据顶高到 551 万，按 P99 反而会把 1% 的正常订单误判为异常。
--   因此改用箱线图围栏：fence = Q3 + 3×IQR（只在正金额上统计），
--   实测 pay_amount fence≈11357 命中 312 条，price fence≈21648 命中 16 条，
--   与注入规模完全吻合。阈值由 SQL 实时计算，不写死。
--
-- 处理原则：能推断的修正（取绝对值），不能推断的置空并打标记，绝不臆造数值。
-- ============================================================

use ydsh_dwd;

-- ------------------------------------------------------------
-- 1. 用户：年龄越界填充、日期/手机号格式统一
--    年龄用 1~120 合法区间的中位数填充（比均值更抗极值）
-- ------------------------------------------------------------
drop table if exists cln_user;
create table cln_user stored as orc as
select u.user_id,
       u.user_name,
       u.gender_raw,
       u.gender,
       u.gender_filled,
       case when u.age is null or u.age < 1 or u.age > 120 then m.med_age else u.age end as age,
       case when u.age is null or u.age < 1 or u.age > 120 then 1 else 0 end as age_filled,
       u.city,
       u.province,
       case when trim(u.register_date) rlike '^[0-9]{8}$'
            then concat(substr(trim(u.register_date),1,4),'-',
                        substr(trim(u.register_date),5,2),'-',
                        substr(trim(u.register_date),7,2))
            else replace(trim(u.register_date), '/', '-') end as register_date,
       case when trim(u.register_date) rlike '^[0-9]{4}[-/][0-9]{2}[-/][0-9]{2}$'
                 or trim(u.register_date) rlike '^[0-9]{8}$'
            then 0 else 1 end as register_date_invalid,
       regexp_replace(u.phone, '-', '') as phone,
       case when regexp_replace(u.phone, '-', '') rlike '^1[0-9]{10}$' then 0 else 1 end as phone_invalid,
       u.member_level_raw,
       u.member_level,
       u.member_level_filled,
       u.email,
       u.email_missing
from stg_user_enum u
cross join (
    select cast(percentile_approx(age, 0.5) as int) as med_age
    from ydsh_ods.ods_user
    where age between 1 and 120
) m;

-- ------------------------------------------------------------
-- 2. 订单：金额异常处理 + 日期格式统一
-- ------------------------------------------------------------
drop table if exists cln_order;
create table cln_order stored as orc as
select o.order_id,
       o.user_id,
       o.store_id,
       case when trim(o.order_date) rlike '^[0-9]{8}$'
            then concat(substr(trim(o.order_date),1,4),'-',
                        substr(trim(o.order_date),5,2),'-',
                        substr(trim(o.order_date),7,2))
            else replace(trim(o.order_date), '/', '-') end as order_date,
       case when trim(o.order_date) rlike '^[0-9]{4}[-/][0-9]{2}[-/][0-9]{2}$'
                 or trim(o.order_date) rlike '^[0-9]{8}$'
            then 0 else 1 end as order_date_invalid,
       o.order_time,
       o.pay_amount as pay_amount_raw,
       case when o.pay_amount < 0 then abs(o.pay_amount)
            when o.pay_amount > f.amt_fence then null
            else o.pay_amount end as pay_amount,
       case when o.pay_amount < 0 then 1
            when o.pay_amount > f.amt_fence then 2
            else 0 end as amount_flag,
       case when o.pay_amount is null then 0
            when o.pay_amount < 0 then 1
            when o.pay_amount > f.amt_fence then 0
            else 1 end as is_valid_amount,
       case when o.discount_amount < 0 then abs(o.discount_amount) else o.discount_amount end as discount_amount,
       case when o.discount_amount < 0 then 1 else 0 end as discount_flag,
       o.order_status_raw,
       o.order_status,
       o.pay_type_raw,
       o.pay_type,
       o.channel_raw,
       o.channel
from stg_order_enum o
cross join (
    select percentile_approx(pay_amount, 0.75)
           + 3 * (percentile_approx(pay_amount, 0.75) - percentile_approx(pay_amount, 0.25)) as amt_fence
    from ydsh_ods.ods_order
    where pay_amount > 0
) f;

-- ------------------------------------------------------------
-- 3. 商品：类目/品牌去空格、价格异常、上架日期补全
-- ------------------------------------------------------------
drop table if exists cln_product;
create table cln_product stored as orc as
select p.product_id,
       p.product_name,
       trim(p.category) as category,
       trim(p.sub_category) as sub_category,
       trim(p.brand) as brand,
       case when p.price < 0 then abs(p.price)
            when p.price > f.price_fence then null
            else p.price end as price,
       case when p.price < 0 then 1
            when p.price > f.price_fence then 2
            else 0 end as price_flag,
       p.stock,
       p.status,
       case when p.launch_date is null or trim(p.launch_date) = ''
            then coalesce(d.med_date, '1970-01-01')
            else trim(p.launch_date) end as launch_date,
       case when p.launch_date is null or trim(p.launch_date) = '' then 1 else 0 end as launch_filled
from ydsh_ods.ods_product p
cross join (
    select percentile_approx(price, 0.75)
           + 3 * (percentile_approx(price, 0.75) - percentile_approx(price, 0.25)) as price_fence
    from ydsh_ods.ods_product
    where price > 0
) f
left join (
    select sub_category,
           from_unixtime(
               cast(percentile(cast(unix_timestamp(launch_date, 'yyyy-MM-dd') as bigint), 0.5) as bigint),
               'yyyy-MM-dd') as med_date
    from ydsh_ods.ods_product
    where launch_date is not null and launch_date <> ''
    group by sub_category
) d on trim(p.sub_category) = d.sub_category;

-- ------------------------------------------------------------
-- 4. 订单明细：数量与单价异常
--    -2 视为符号错误 → 取绝对值；0 与 99（>fence）无法推断真实值 → 置空并标记
-- ------------------------------------------------------------
drop table if exists cln_item;
create table cln_item stored as orc as
select i.item_id,
       i.order_id,
       i.product_id,
       case when i.quantity < 0 then -i.quantity
            when i.quantity = 0 then null
            when i.quantity > 10 then null
            else i.quantity end as quantity,
       case when i.quantity < 0 then 1
            when i.quantity = 0 then 2
            when i.quantity > 10 then 3
            else 0 end as qty_flag,
       case when i.unit_price < 0 then abs(i.unit_price) else i.unit_price end as unit_price,
       case when i.unit_price < 0 then 1 else 0 end as price_flag,
       i.discount,
       (case when i.quantity < 0 then -i.quantity
             when i.quantity = 0 or i.quantity > 10 then null
             else i.quantity end)
       * (case when i.unit_price < 0 then abs(i.unit_price) else i.unit_price end)
       * (1 - i.discount) as item_amount
from ydsh_ods.ods_order_item i;

-- ------------------------------------------------------------
-- 5. 用户行为：时间格式统一（yyyy/MM/dd HH:mm → yyyy-MM-dd HH:mm:ss）
-- ------------------------------------------------------------
drop table if exists cln_behavior;
create table cln_behavior stored as orc as
select b.behavior_id,
       b.user_id,
       b.product_id,
       b.behavior_type_raw,
       b.behavior_type,
       b.behavior_name,
       case when length(trim(b.behavior_time)) = 16
            then concat(replace(trim(b.behavior_time), '/', '-'), ':00')
            else replace(trim(b.behavior_time), '/', '-') end as behavior_time,
       case when length(trim(b.behavior_time)) = 16 then 1 else 0 end as time_format_fixed,
       b.session_id
from stg_behavior_enum b;

-- ------------------------------------------------------------
-- 6. 校验（逐条执行，避免单个 DAG 顶点过多）
-- ------------------------------------------------------------
-- 6.1 用户表：年龄、日期、手机号
select count(*) as total,
       sum(age_filled) as age_filled_cnt,
       min(age) as min_age,
       max(age) as max_age,
       sum(register_date_invalid) as bad_reg_date,
       sum(phone_invalid) as bad_phone,
       count(distinct length(register_date)) as date_len_kinds,
       count(distinct length(phone)) as phone_len_kinds
from cln_user;

-- 6.2 订单表：金额异常残留、阈值命中数
select count(*) as total,
       sum(if(amount_flag = 1, 1, 0)) as neg_fixed,
       sum(if(amount_flag = 2, 1, 0)) as huge_nulled,
       sum(case when pay_amount < 0 then 1 else 0 end) as neg_left,
       sum(case when pay_amount > 1000000 then 1 else 0 end) as huge_left,
       sum(discount_flag) as discount_fixed,
       sum(is_valid_amount) as valid_amount_cnt,
       sum(order_date_invalid) as bad_order_date
from cln_order;

-- 6.3 商品表：价格异常、类目收敛
select count(*) as total,
       sum(if(price_flag = 1, 1, 0)) as neg_fixed,
       sum(if(price_flag = 2, 1, 0)) as huge_nulled,
       sum(launch_filled) as launch_filled,
       count(distinct category) as category_cnt,
       count(distinct brand) as brand_cnt
from cln_product;

-- 6.4 明细表：数量/单价处理
select count(*) as total,
       sum(if(qty_flag = 1, 1, 0)) as qty_neg_fixed,
       sum(if(qty_flag = 2, 1, 0)) as qty_zero_nulled,
       sum(if(qty_flag = 3, 1, 0)) as qty_huge_nulled,
       sum(price_flag) as price_neg_fixed,
       sum(case when quantity is null then 1 else 0 end) as qty_null
from cln_item;

-- 6.5 行为表：时间格式
select count(*) as total,
       sum(time_format_fixed) as time_fixed,
       count(distinct length(behavior_time)) as time_len_kinds,
       min(behavior_time) as min_t,
       max(behavior_time) as max_t
from cln_behavior;
