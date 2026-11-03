-- 05_dwd_extra.sql —— 后三张表的补充处理（标记式，不修改原值）
--
-- 背景：任务书子任务 6 原文为「定位各表缺失字段（如 ...）」，
--       "各表"指 6 张表，"如"为举例，故后三张表同样需要处理。
--       经全字段扫描：
--         ods_store          空值 0、主键重复 0、面积 121~783 合法、日期格式统一 → 无可处理项
--         ods_order_item     空值 0、主键重复 0，但 quantity 取值 -2 ~ 99，存在数量异常
--         ods_user_behavior  空值 0、主键重复 0，但 behavior_type 存在 10 种写法
--
-- 处理原则（与 dwd_user 一致）：只新增标记列，不修改任何原始取值，
-- 保证数据可追溯；真正的取值规范化留待后续周次按业务规则统一处理。

drop table if exists ydsh_ods.dwd_order_item;

create table ydsh_ods.dwd_order_item as
select item_id,
       order_id,
       product_id,
       quantity,
       unit_price,
       discount,
       case when quantity is null or quantity <= 0 then 1 else 0 end as quantity_invalid,
       case when quantity > 50 then 1 else 0 end as quantity_outlier
from ydsh_ods.ods_order_item;

drop table if exists ydsh_ods.dwd_user_behavior;

create table ydsh_ods.dwd_user_behavior as
select behavior_id,
       user_id,
       product_id,
       behavior_type,
       behavior_time,
       session_id,
       case when behavior_type in ('pv','cart','fav','buy') then 0 else 1 end as behavior_type_variant
from ydsh_ods.ods_user_behavior;

-- ods_store：无缺失、无重复、无异常取值，保持原样下沉（见 04_dwd_other.sql）

-- 校验：行数应与 ODS 一致；异常标记数量应与扫描结果一致
--       quantity_invalid 预期 718、quantity_outlier 预期 363、
--       behavior_type_variant 预期 3672（60000 - 56328 规范写法）
select 'dwd_order_item' as t, count(*) as c,
       sum(quantity_invalid) as invalid_cnt,
       sum(quantity_outlier) as outlier_cnt
from ydsh_ods.dwd_order_item
union all
select 'dwd_user_behavior', count(*), sum(behavior_type_variant), 0
from ydsh_ods.dwd_user_behavior;
