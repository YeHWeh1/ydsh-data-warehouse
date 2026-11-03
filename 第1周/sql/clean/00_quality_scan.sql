-- 00_quality_scan.sql —— 数据质量五维度扫描（只读，可重复执行）
-- 用法：docker exec -it hive-server beeline -u jdbc:hive2://localhost:10000 -e "$(cat 00_quality_scan.sql)"
--       或逐段粘贴执行
use ydsh_ods;

-- ============ 1. 完整性：空值统计 ============
select 'ods_user' as tbl, count(*) as total,
       sum(case when age is null then 1 else 0 end) as age_null,
       sum(case when email is null or email='' then 1 else 0 end) as email_null,
       sum(case when gender is null or gender='' then 1 else 0 end) as gender_null
from ods_user;

select 'ods_product' as tbl, count(*) as total,
       sum(case when launch_date is null or launch_date='' then 1 else 0 end) as launch_null
from ods_product;

select 'ods_order' as tbl, count(*) as total,
       sum(case when pay_amount is null then 1 else 0 end) as pay_null
from ods_order;

select 'ods_order_item' as tbl, count(*) as total,
       sum(case when quantity is null then 1 else 0 end) as qty_null
from ods_order_item;

select 'ods_user_behavior' as tbl, count(*) as total,
       sum(case when behavior_type is null or behavior_type='' then 1 else 0 end) as bt_null,
       sum(case when session_id is null or session_id='' then 1 else 0 end) as sess_null
from ods_user_behavior;

-- ============ 2. 唯一性：主键重复 ============
select count(*) as dup_groups, sum(c) as dup_rows, sum(c-1) as extra_rows
from (select order_id, count(*) c from ods_order group by order_id having count(*)>1) t;

-- 整行完全重复 vs 主键重复内容不同
select count(*) as exact_dup_groups, sum(c-1) as exact_extra_rows
from (select order_id,user_id,store_id,order_date,order_time,pay_amount,
             discount_amount,pay_type,order_status,channel,count(*) c
      from ods_order
      group by order_id,user_id,store_id,order_date,order_time,pay_amount,
               discount_amount,pay_type,order_status,channel
      having count(*)>1) t;

-- ============ 3. 有效性：枚举取值 ============
select 'gender' as dim, gender as val, count(*) c from ods_user group by gender;
select 'member_level' as dim, member_level as val, count(*) c from ods_user group by member_level;
select 'order_status' as dim, order_status as val, count(*) c from ods_order group by order_status;
select 'pay_type' as dim, pay_type as val, count(*) c from ods_order group by pay_type;
select 'behavior_type' as dim, behavior_type as val, count(*) c from ods_user_behavior group by behavior_type;

-- ============ 4. 一致性：格式分布 ============
select 'register_date' as dim, length(register_date) len, substr(register_date,5,1) ch, count(*) c
from ods_user group by length(register_date), substr(register_date,5,1);

select 'order_date' as dim, length(order_date) len, substr(order_date,5,1) ch, count(*) c
from ods_order group by length(order_date), substr(order_date,5,1);

select 'phone' as dim, case when phone like '%-%' then 'dash' else 'plain' end fmt, count(*) c
from ods_user group by case when phone like '%-%' then 'dash' else 'plain' end;

-- ============ 5. 准确性：取值合理性 ============
select 'age' as dim,
       sum(case when age < 1 or age > 120 then 1 else 0 end) as bad_cnt,
       min(age) as min_v, max(age) as max_v
from ods_user;

select 'pay_amount' as dim,
       sum(case when pay_amount < 0 then 1 else 0 end) as neg_cnt,
       sum(case when pay_amount > 1000000 then 1 else 0 end) as huge_cnt
from ods_order;

select 'price' as dim,
       sum(case when price < 0 then 1 else 0 end) as neg_cnt,
       sum(case when price > 100000 then 1 else 0 end) as huge_cnt
from ods_product;

select 'quantity' as dim,
       sum(case when quantity <= 0 then 1 else 0 end) as bad_cnt
from ods_order_item;
