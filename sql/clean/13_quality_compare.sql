-- ============================================================
-- 13_quality_compare.sql —— 子任务 4：数据质量对比报告（1 课时）
-- 依据任务书 子任务 4 / 部署手册 步骤 4
-- 口径：清洗前 = ODS 原始数据（订单类先去重，与 DWD 行数对齐，避免"去重"污染异常值统计）
--       清洗后 = DWD 明细层
--       修复率 = (清洗前 - 清洗后) / 清洗前
-- 覆盖五类脏数据：缺失值、重复值、异常值、枚举不规范、格式不一致
-- 产物：qc_config（阈值）、qc_before_*（清洗前标记）、qc_metric（对比结果集）
-- ============================================================

use ydsh_dwd;

-- ------------------------------------------------------------
-- 1. 阈值配置：金额/价格/数量均用 Q3+3×IQR 箱线图围栏，由数据实时计算
-- ------------------------------------------------------------
drop table if exists qc_config;
create table qc_config (cfg_key string, cfg_value double) stored as orc;

insert into table qc_config
select 'amt_fence',
       percentile_approx(pay_amount, 0.75)
       + 3 * (percentile_approx(pay_amount, 0.75) - percentile_approx(pay_amount, 0.25))
from ydsh_ods.ods_order where pay_amount > 0;

insert into table qc_config
select 'price_fence',
       percentile_approx(price, 0.75)
       + 3 * (percentile_approx(price, 0.75) - percentile_approx(price, 0.25))
from ydsh_ods.ods_product where price > 0;

insert into table qc_config
select 'qty_fence',
       percentile_approx(quantity, 0.75)
       + 3 * (percentile_approx(quantity, 0.75) - percentile_approx(quantity, 0.25))
from ydsh_ods.ods_order_item where quantity > 0;

select * from qc_config;

-- ------------------------------------------------------------
-- 2. 清洗前标记表（订单先去重，口径与 DWD 一致：15000 行）
-- ------------------------------------------------------------
drop table if exists qc_before_order;
create table qc_before_order stored as orc as
select t.order_id, t.pay_amount, t.order_date, t.order_status, t.pay_type, t.channel,
       case when t.pay_amount < 0 then 1 else 0 end as neg_flag,
       case when t.pay_amount > f.amt_fence then 1 else 0 end as huge_flag,
       case when trim(t.order_date) rlike '^[0-9]{4}-[0-9]{2}-[0-9]{2}$' then 0 else 1 end as bad_date_flag
from (
    select o.*,
           row_number() over (
               partition by order_id
               order by concat(replace(order_date, '/', '-'), ' ', order_time) desc,
                        pay_amount desc nulls last, store_id desc, channel desc
           ) as rn
    from ydsh_ods.ods_order o
) t
cross join (select cfg_value as amt_fence from qc_config where cfg_key = 'amt_fence') f
where t.rn = 1;

drop table if exists qc_before_product;
create table qc_before_product stored as orc as
select p.product_id, p.price,
       case when p.price < 0 then 1 else 0 end as neg_flag,
       case when p.price > f.price_fence then 1 else 0 end as huge_flag
from ydsh_ods.ods_product p
cross join (select cfg_value as price_fence from qc_config where cfg_key = 'price_fence') f;

drop table if exists qc_before_item;
create table qc_before_item stored as orc as
select i.item_id, i.quantity,
       case when i.quantity <= 0 then 1 else 0 end as nonpos_flag,
       case when i.quantity > f.qty_fence then 1 else 0 end as huge_flag
from ydsh_ods.ods_order_item i
cross join (select cfg_value as qty_fence from qc_config where cfg_key = 'qty_fence') f;

-- ------------------------------------------------------------
-- 3. 对比结果表（逐条 insert，避免单个 DAG 顶点过多触发 Tez 计数器上限）
-- ------------------------------------------------------------
drop table if exists qc_metric;
create table qc_metric (
  metric_class string comment '脏数据类型',
  metric_name  string comment '指标',
  before_cnt   bigint comment '清洗前（ODS/去重口径）',
  after_cnt    bigint comment '清洗后（DWD）'
) stored as orc;

-- 3.1 缺失值
insert into table qc_metric
select '缺失值', 'user.age 空值/越界', b.c, a.c
from (select count(*) as c from ydsh_ods.ods_user where age is null or age < 1 or age > 120) b
cross join (select count(*) as c from dim_user where age is null or age < 1 or age > 120) a;

insert into table qc_metric
select '缺失值', 'user.gender 空值', b.c, a.c
from (select count(*) as c from ydsh_ods.ods_user where gender is null or trim(gender) = '') b
cross join (select count(*) as c from dim_user where gender is null or trim(gender) = '') a;

insert into table qc_metric
select '缺失值', 'user.member_level 空值', b.c, a.c
from (select count(*) as c from ydsh_ods.ods_user where member_level is null or trim(member_level) = '') b
cross join (select count(*) as c from dim_user where member_level is null or trim(member_level) = '') a;

insert into table qc_metric
select '缺失值', 'user.email 空值', b.c, a.c
from (select count(*) as c from ydsh_ods.ods_user where email is null or trim(email) = '') b
cross join (select count(*) as c from dim_user where email is null or trim(email) = '') a;

insert into table qc_metric
select '缺失值', 'product.launch_date 空值', b.c, a.c
from (select count(*) as c from ydsh_ods.ods_product where launch_date is null or trim(launch_date) = '') b
cross join (select count(*) as c from dim_product where launch_date is null or trim(launch_date) = '') a;

-- 3.2 重复值
insert into table qc_metric
select '重复值', 'order 重复行数', b.c, a.c
from (select count(*) - count(distinct order_id) as c from ydsh_ods.ods_order) b
cross join (select count(*) - count(distinct order_id) as c from dwd_order) a;

-- 3.3 异常值
insert into table qc_metric
select '异常值', 'order.pay_amount 负金额', b.c, a.c
from (select sum(neg_flag) as c from qc_before_order) b
cross join (select count(*) as c from dwd_order where pay_amount < 0) a;

insert into table qc_metric
select '异常值', 'order.pay_amount 超大金额', b.c, a.c
from (select sum(huge_flag) as c from qc_before_order) b
cross join (select count(*) as c from dwd_order o
            cross join (select cfg_value as f from qc_config where cfg_key = 'amt_fence') c2
            where o.pay_amount > c2.f) a;

insert into table qc_metric
select '异常值', 'product.price 负价格', b.c, a.c
from (select sum(neg_flag) as c from qc_before_product) b
cross join (select count(*) as c from dim_product where price < 0) a;

insert into table qc_metric
select '异常值', 'product.price 超大价格', b.c, a.c
from (select sum(huge_flag) as c from qc_before_product) b
cross join (select count(*) as c from dim_product p
            cross join (select cfg_value as f from qc_config where cfg_key = 'price_fence') c2
            where p.price > c2.f) a;

insert into table qc_metric
select '异常值', 'item.quantity <= 0', b.c, a.c
from (select sum(nonpos_flag) as c from qc_before_item) b
cross join (select count(*) as c from dwd_order_item where quantity <= 0) a;

insert into table qc_metric
select '异常值', 'item.quantity 超大', b.c, a.c
from (select sum(huge_flag) as c from qc_before_item) b
cross join (select count(*) as c from dwd_order_item i
            cross join (select cfg_value as f from qc_config where cfg_key = 'qty_fence') c2
            where i.quantity > c2.f) a;

-- 3.4 枚举不规范
insert into table qc_metric
select '枚举不规范', 'user.gender', b.c, a.c
from (select count(*) as c from ydsh_ods.ods_user
      where lower(trim(gender)) not in ('男', '女', '未知')) b
cross join (select count(*) as c from dim_user
            where lower(trim(gender)) not in ('男', '女', '未知')) a;

insert into table qc_metric
select '枚举不规范', 'user.member_level', b.c, a.c
from (select count(*) as c from ydsh_ods.ods_user
      where lower(trim(member_level)) not in ('金卡会员', '银卡会员', '普通会员', '钻石会员', '未知')) b
cross join (select count(*) as c from dim_user
            where lower(trim(member_level)) not in ('金卡会员', '银卡会员', '普通会员', '钻石会员', '未知')) a;

insert into table qc_metric
select '枚举不规范', 'order.order_status', b.c, a.c
from (select count(*) as c from qc_before_order
      where lower(trim(order_status)) not in ('已完成', '已支付', '待支付', '已取消', '退款中')) b
cross join (select count(*) as c from dwd_order
            where lower(trim(order_status)) not in ('已完成', '已支付', '待支付', '已取消', '退款中')) a;

insert into table qc_metric
select '枚举不规范', 'order.pay_type', b.c, a.c
from (select count(*) as c from qc_before_order
      where lower(trim(pay_type)) not in ('微信支付', '支付宝', '银行卡', '货到付款')) b
cross join (select count(*) as c from dwd_order
            where lower(trim(pay_type)) not in ('微信支付', '支付宝', '银行卡', '货到付款')) a;

insert into table qc_metric
select '枚举不规范', 'order.channel', b.c, a.c
from (select count(*) as c from qc_before_order
      where lower(trim(channel)) not in ('app', 'pc官网', '门店', '小程序')) b
cross join (select count(*) as c from dwd_order
            where lower(trim(channel)) not in ('app', 'pc官网', '门店', '小程序')) a;

insert into table qc_metric
select '枚举不规范', 'behavior.behavior_type', b.c, a.c
from (select count(*) as c from ydsh_ods.ods_user_behavior
      where lower(trim(behavior_type)) not in ('pv', 'cart', 'buy', 'fav')) b
cross join (select count(*) as c from dwd_user_behavior
            where lower(trim(behavior_type)) not in ('pv', 'cart', 'buy', 'fav')) a;

-- 3.5 格式不一致
insert into table qc_metric
select '格式不一致', 'user.register_date', b.c, a.c
from (select count(*) as c from ydsh_ods.ods_user
      where not (trim(register_date) rlike '^[0-9]{4}-[0-9]{2}-[0-9]{2}$')) b
cross join (select count(*) as c from dim_user
            where not (trim(register_date) rlike '^[0-9]{4}-[0-9]{2}-[0-9]{2}$')) a;

insert into table qc_metric
select '格式不一致', 'order.order_date', b.c, a.c
from (select sum(bad_date_flag) as c from qc_before_order) b
cross join (select count(*) as c from dwd_order
            where not (trim(order_date) rlike '^[0-9]{4}-[0-9]{2}-[0-9]{2}$')) a;

insert into table qc_metric
select '格式不一致', 'user.phone', b.c, a.c
from (select count(*) as c from ydsh_ods.ods_user where phone like '%-%') b
cross join (select count(*) as c from dim_user where phone like '%-%') a;

insert into table qc_metric
select '格式不一致', 'behavior.behavior_time', b.c, a.c
from (select count(*) as c from ydsh_ods.ods_user_behavior
      where not (trim(behavior_time) rlike '^[0-9]{4}-[0-9]{2}-[0-9]{2} [0-9]{2}:[0-9]{2}:[0-9]{2}$')) b
cross join (select count(*) as c from dwd_user_behavior
            where not (trim(behavior_time) rlike '^[0-9]{4}-[0-9]{2}-[0-9]{2} [0-9]{2}:[0-9]{2}:[0-9]{2}$')) a;

-- ------------------------------------------------------------
-- 4. 输出：修复率（Hive 无 UPDATE，修复率在查询时计算）
-- ------------------------------------------------------------
select metric_class,
       metric_name,
       before_cnt,
       after_cnt,
       case when before_cnt = 0 then null
            else round((before_cnt - after_cnt) * 100.0 / before_cnt, 1) end as fix_rate_pct
from qc_metric
order by metric_class, metric_name;
