-- 03_dwd_order.sql —— 订单表重复值检测与去重
-- 检测：ods_order 共 15600 行、15000 个不同 order_id → 多出 600 行
--   其中 586 个重复订单号、涉及 1186 行；
--   整行完全重复 394 组 / 400 行，其余 200 行为"主键重复但内容不同"。
-- 策略（依据任务书 子任务7 + 部署手册 步骤9）：
--   ROW_NUMBER() OVER (PARTITION BY order_id ORDER BY order_time DESC, pay_amount DESC)
--   同一订单号保留【下单时间最晚】的一条（时间相同则取金额较大的），rn=1 保留。

-- 1) 先看重复分布（诊断）
select count(*) as dup_groups, sum(c) as dup_rows, sum(c-1) as extra_rows
from (select order_id, count(*) c from ydsh_ods.ods_order group by order_id having count(*)>1) t;

-- 2) 去重建表
drop table if exists ydsh_ods.dwd_order;

create table ydsh_ods.dwd_order as
select order_id, user_id, store_id, order_date, order_time, pay_amount,
       discount_amount, pay_type, order_status, channel
from (
    select order_id, user_id, store_id, order_date, order_time, pay_amount,
           discount_amount, pay_type, order_status, channel,
           row_number() over (
               partition by order_id
               order by order_time desc, pay_amount desc
           ) as rn
    from ydsh_ods.ods_order
) t
where rn = 1;

-- 3) 校验：应为 15000 行，且 order_id 无重复；前后差 600 与诊断一致
select count(*) as total_rows,
       count(distinct order_id) as distinct_oid,
       count(*) - count(distinct order_id) as dup_left
from ydsh_ods.dwd_order;
