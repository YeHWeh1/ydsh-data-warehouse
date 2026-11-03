-- ============================================================
-- load_ods.sql —— ODS 层数据导入与行数校验
-- 前置：已执行 create_ods.sql 建好 6 张表
-- 说明：/data 为容器内挂载点，对应宿主机 E:\dsh project\模拟数据
-- 执行：docker exec hive-server beeline -u "jdbc:hive2://localhost:10000" -f /opt/hive/data/load_ods.sql
-- ============================================================

use ydsh_ods;

-- 1) 用户信息表（预期 1,000 行）
load data local inpath '/data/ods_user.csv' into table ods_user;

-- 2) 商品信息表（预期 600 行）
load data local inpath '/data/ods_product.csv' into table ods_product;

-- 3) 门店信息表（预期 60 行）
load data local inpath '/data/ods_store.csv' into table ods_store;

-- 4) 订单主表（预期 15,600 行）
load data local inpath '/data/ods_order.csv' into table ods_order;

-- 5) 订单明细表（预期 37,549 行）
load data local inpath '/data/ods_order_item.csv' into table ods_order_item;

-- 6) 用户行为日志表（预期 60,000 行）
load data local inpath '/data/ods_user_behavior.csv' into table ods_user_behavior;

-- ============================================================
-- 行数校验：与《模拟数据字典》一致才算导入成功
-- 期望：1000 / 600 / 60 / 15600 / 37549 / 60000，合计 114,809
-- ============================================================
select 'ods_user' as tbl, count(*) as cnt from ods_user
union all select 'ods_product',         count(*) from ods_product
union all select 'ods_store',           count(*) from ods_store
union all select 'ods_order',           count(*) from ods_order
union all select 'ods_order_item',      count(*) from ods_order_item
union all select 'ods_user_behavior',   count(*) from ods_user_behavior;
