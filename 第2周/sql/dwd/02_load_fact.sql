-- ============================================================
-- 02_load_fact.sql —— 子任务 6（下）：DWD 事实表加载（分区表 / 分桶表）
-- 依据任务书 子任务 6 / 部署手册 步骤 7
-- 内容：dwd_order（按 order_date 动态分区）
--       dwd_order_item（按 order_date 动态分区）
--       dwd_user_behavior（按 user_id 分 8 桶）
-- 说明：动态分区需开启 nonstrict；本机 Tez 对动态分区数有上限，
--       366 个日期分区需把 max.dynamic.partitions(.pernode) 调到 2000
-- ============================================================

use ydsh_dwd;

set hive.exec.dynamic.partition=true;
set hive.exec.dynamic.partition.mode=nonstrict;
set hive.exec.max.dynamic.partitions=2000;
set hive.exec.max.dynamic.partitions.pernode=2000;
set hive.enforce.bucketing=true;
set hive.exec.max.created.files=200000;

-- ------------------------------------------------------------
-- 1. 订单事实表：分区表 + 支付方式编码表关联
--    分区字段 order_date 必须放在 select 最后一列
-- ------------------------------------------------------------
insert overwrite table dwd_order partition (order_date)
select o.order_id,
       o.user_id,
       o.store_id,
       o.order_timestamp,
       o.order_hour,
       o.pay_amount,
       o.discount_amount,
       o.net_amount,
       p.pay_code as pay_type_code,
       p.pay_name as pay_type,
       s.std_code as order_status_code,
       o.order_status,
       c.std_code as channel_code,
       o.channel,
       o.amount_flag,
       o.discount_flag,
       o.is_valid_amount,
       o.order_date
from cln_order_full o
left join dim_pay_type p
       on o.pay_type = p.pay_name
left join (select std_value, std_code from dim_enum_map where field_name = 'order_status'
           group by std_value, std_code) s
       on o.order_status = s.std_value
left join (select std_value, std_code from dim_enum_map where field_name = 'channel'
           group by std_value, std_code) c
       on o.channel = c.std_value;

-- ------------------------------------------------------------
-- 2. 订单明细事实表：通过订单表拿到分区日期
-- ------------------------------------------------------------
insert overwrite table dwd_order_item partition (order_date)
select i.item_id,
       i.order_id,
       i.product_id,
       i.quantity,
       i.unit_price,
       i.discount,
       i.item_amount,
       i.qty_flag,
       o.order_date
from cln_item i
join cln_order_full o on i.order_id = o.order_id;

-- ------------------------------------------------------------
-- 3. 用户行为事实表：分桶表（按 user_id 分 8 桶）
-- ------------------------------------------------------------
insert overwrite table dwd_user_behavior
select behavior_id,
       user_id,
       product_id,
       behavior_type,
       behavior_name,
       behavior_time,
       substr(behavior_time, 1, 10) as behavior_date,
       cast(substr(behavior_time, 12, 2) as int) as behavior_hour,
       session_id
from cln_behavior;

-- ------------------------------------------------------------
-- 4. 校验
-- ------------------------------------------------------------
-- 4.1 事实表行数
select 'dwd_order' as tbl, count(*) as cnt from dwd_order
union all
select 'dwd_order_item', count(*) from dwd_order_item
union all
select 'dwd_user_behavior', count(*) from dwd_user_behavior;

-- 4.2 分区与分桶
show partitions dwd_order;
show partitions dwd_order_item;

describe formatted dwd_user_behavior;

-- 4.3 金额可用性与异常残留
select count(*) as total,
       sum(is_valid_amount) as valid_amount,
       sum(if(amount_flag = 1, 1, 0)) as neg_fixed,
       sum(if(amount_flag = 2, 1, 0)) as huge_nulled,
       sum(if(pay_amount < 0, 1, 0)) as neg_left,
       sum(if(pay_type_code is null, 1, 0)) as pay_code_null,
       count(distinct order_status) as status_cnt,
       count(distinct channel) as channel_cnt
from dwd_order;
