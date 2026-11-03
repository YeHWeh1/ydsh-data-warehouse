-- 04_dwd_other.sql —— 门店表下沉（本周无需清洗）
--
-- 依据：第 1 周任务书子任务 5 要求「对 6 张表进行五维度扫描」，
--       子任务 6 针对「各表缺失字段」。
--
-- 全列扫描结论（sql/clean/00_quality_scan.sql 可复现）：
--   ods_store 9 个字段空值均为 0；store_id 60/60 无重复；
--   area 取值 121~783 全部合法；open_date 60 条全为 yyyy-MM-dd 格式。
--   → 无缺失值、无重复值、无异常取值，本周没有任何需要处理的对象。
--
-- 另两张表（ods_order_item、ods_user_behavior）虽然同样没有缺失和重复，
-- 但存在数量异常与枚举写法混乱，其标记处理见 05_dwd_extra.sql。
--
-- 本表原样下沉，保证 DWD 层 6 张表齐全，供后续建维度表使用。

drop table if exists ydsh_ods.dwd_store;
create table ydsh_ods.dwd_store as
select store_id, store_name, province, city, district,
       address, open_date, area, manager
from ydsh_ods.ods_store;

-- 校验：行数应与 ODS 一致（60）
select 'dwd_store' as t, count(*) as c from ydsh_ods.dwd_store;
