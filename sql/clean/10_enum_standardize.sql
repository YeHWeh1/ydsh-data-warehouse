-- ============================================================
-- 10_enum_standardize.sql —— 子任务 1：枚举字段标准化（2 课时）
-- 依据任务书 子任务 1 / 部署手册 步骤 1
-- 目标：把 gender / member_level / order_status / pay_type / channel / behavior_type
--       六类枚举字段的脏取值（中英文、大小写、数字编码、空值）收敛为规范值域
-- 做法：1) 用 select distinct 列出全部原始取值，登记到 dim_enum_map（映射表可查询、可校验）
--       2) 用 case when + lower(trim()) 做映射，避免大小写分支爆炸
--       3) 产出 stg_*_enum 过渡表，供后续异常值/格式处理使用
-- 编码规范：用户/订单类统一中文标准值；行为日志保留埋点英文编码（pv/cart/buy/fav），
--           另存中文名，兼顾行业惯例与报表可读性
-- ============================================================

use ydsh_dwd;

-- ------------------------------------------------------------
-- 1. 枚举映射表：登记 6 个字段的全部原始取值
--    raw_value 一律按 lower(trim()) 登记，与清洗脚本的匹配口径保持一致
-- ------------------------------------------------------------
-- 用 insert overwrite 保证脚本可重复执行（重跑不会累积重复行）
insert overwrite table dim_enum_map values
-- gender（原始 8 种：男/女/未知/M/F/male/female/空）
('gender','男','男','M',1),
('gender','女','女','F',1),
('gender','未知','未知','U',1),
('gender','m','男','M',0),
('gender','male','男','M',0),
('gender','f','女','F',0),
('gender','female','女','F',0),
('gender','','未知','U',0),
-- member_level（原始 12 种）
('member_level','金卡会员','金卡会员','GOLD',1),
('member_level','银卡会员','银卡会员','SILVER',1),
('member_level','普通会员','普通会员','NORMAL',1),
('member_level','钻石会员','钻石会员','DIAMOND',1),
('member_level','金卡','金卡会员','GOLD',0),
('member_level','gold','金卡会员','GOLD',0),
('member_level','银卡','银卡会员','SILVER',0),
('member_level','silver','银卡会员','SILVER',0),
('member_level','普通','普通会员','NORMAL',0),
('member_level','normal','普通会员','NORMAL',0),
('member_level','钻石','钻石会员','DIAMOND',0),
('member_level','','未知','UNKNOWN',0),
-- order_status（原始 12 种，含数字编码 1~4 与英文 cancelled）
('order_status','已完成','已完成','DONE',1),
('order_status','已支付','已支付','PAID',1),
('order_status','待支付','待支付','PENDING',1),
('order_status','已取消','已取消','CANCELLED',1),
('order_status','退款中','退款中','REFUNDING',1),
('order_status','完成','已完成','DONE',0),
('order_status','已付款','已支付','PAID',0),
('order_status','cancelled','已取消','CANCELLED',0),
('order_status','1','待支付','PENDING',0),
('order_status','2','已支付','PAID',0),
('order_status','3','已完成','DONE',0),
('order_status','4','已取消','CANCELLED',0),
-- pay_type（原始 7 种）
('pay_type','微信支付','微信支付','WECHAT',1),
('pay_type','支付宝','支付宝','ALIPAY',1),
('pay_type','银行卡','银行卡','CARD',1),
('pay_type','货到付款','货到付款','COD',1),
('pay_type','微信','微信支付','WECHAT',0),
('pay_type','wechat','微信支付','WECHAT',0),
('pay_type','alipay','支付宝','ALIPAY',0),
-- channel（原始 6 种写法，按 lower(trim()) 收敛为 5 个登记项）
('channel','app','APP','APP',0),
('channel','pc官网','PC官网','PC',1),
('channel','门店','门店','STORE',1),
('channel','小程序','小程序','MINI',1),
('channel','pc','PC官网','PC',0),
-- behavior_type（原始 10 种，含中文写法与大小写混用）
('behavior_type','pv','pv','PV',1),
('behavior_type','cart','cart','CART',1),
('behavior_type','buy','buy','BUY',1),
('behavior_type','fav','fav','FAV',1),
('behavior_type','浏览','pv','PV',0),
('behavior_type','加购','cart','CART',0),
('behavior_type','购买','buy','BUY',0),
('behavior_type','收藏','fav','FAV',0);

-- ------------------------------------------------------------
-- 2. 编码映射维度表（子任务 3 要求：会员等级 / 支付方式）
-- ------------------------------------------------------------
insert overwrite table dim_member_level values
('NORMAL','普通会员',1),
('SILVER','银卡会员',2),
('GOLD','金卡会员',3),
('DIAMOND','钻石会员',4),
('UNKNOWN','未知',0);

insert overwrite table dim_pay_type values
('WECHAT','微信支付',1),
('ALIPAY','支付宝',1),
('CARD','银行卡',1),
('COD','货到付款',0);

-- ------------------------------------------------------------
-- 3. 用户：gender / member_level 标准化
--    空值统一收敛为「未知」，同时保留 *_filled 标记，做到不臆造、可追溯
-- ------------------------------------------------------------
drop table if exists stg_user_enum;
create table stg_user_enum stored as orc as
select user_id,
       user_name,
       trim(gender) as gender_raw,
       case when lower(trim(gender)) in ('男','m','male') then '男'
            when lower(trim(gender)) in ('女','f','female') then '女'
            else '未知' end as gender,
       case when gender is null or trim(gender) = '' then 1 else 0 end as gender_filled,
       age, city, province, register_date, phone, email,
       trim(member_level) as member_level_raw,
       case when lower(trim(member_level)) in ('金卡会员','金卡','gold') then '金卡会员'
            when lower(trim(member_level)) in ('银卡会员','银卡','silver') then '银卡会员'
            when lower(trim(member_level)) in ('普通会员','普通','normal') then '普通会员'
            when lower(trim(member_level)) in ('钻石会员','钻石','diamond') then '钻石会员'
            else '未知' end as member_level,
       case when member_level is null or trim(member_level) = '' then 1 else 0 end as member_level_filled,
       case when email is null or trim(email) = '' then 1 else 0 end as email_missing
from ydsh_ods.ods_user;

-- ------------------------------------------------------------
-- 4. 订单：order_status / pay_type / channel 标准化
-- ------------------------------------------------------------
drop table if exists stg_order_enum;
create table stg_order_enum stored as orc as
select order_id, user_id, store_id,
       order_date, order_time, pay_amount, discount_amount,
       trim(order_status) as order_status_raw,
       case when lower(trim(order_status)) in ('已完成','完成','3') then '已完成'
            when lower(trim(order_status)) in ('已支付','已付款','2') then '已支付'
            when lower(trim(order_status)) in ('待支付','1') then '待支付'
            when lower(trim(order_status)) in ('已取消','cancelled','4') then '已取消'
            when lower(trim(order_status)) in ('退款中','refunding') then '退款中'
            else '未知' end as order_status,
       trim(pay_type) as pay_type_raw,
       case when lower(trim(pay_type)) in ('微信支付','微信','wechat') then '微信支付'
            when lower(trim(pay_type)) in ('支付宝','alipay') then '支付宝'
            when lower(trim(pay_type)) in ('银行卡','card') then '银行卡'
            when lower(trim(pay_type)) in ('货到付款','cod') then '货到付款'
            else '未知' end as pay_type,
       trim(channel) as channel_raw,
       case when lower(trim(channel)) = 'app' then 'APP'
            when lower(trim(channel)) in ('pc官网','pc') then 'PC官网'
            when trim(channel) = '门店' then '门店'
            when trim(channel) = '小程序' then '小程序'
            else '未知' end as channel
from ydsh_ods.ods_order;

-- ------------------------------------------------------------
-- 5. 用户行为：behavior_type 标准化（统一为埋点英文编码 + 中文名）
-- ------------------------------------------------------------
drop table if exists stg_behavior_enum;
create table stg_behavior_enum stored as orc as
select behavior_id, user_id, product_id,
       trim(behavior_type) as behavior_type_raw,
       case when lower(trim(behavior_type)) in ('pv','浏览') then 'pv'
            when lower(trim(behavior_type)) in ('cart','加购') then 'cart'
            when lower(trim(behavior_type)) in ('buy','购买') then 'buy'
            when lower(trim(behavior_type)) in ('fav','收藏') then 'fav'
            else 'unknown' end as behavior_type,
       case when lower(trim(behavior_type)) in ('pv','浏览') then '浏览'
            when lower(trim(behavior_type)) in ('cart','加购') then '加购'
            when lower(trim(behavior_type)) in ('buy','购买') then '购买'
            when lower(trim(behavior_type)) in ('fav','收藏') then '收藏'
            else '未知' end as behavior_name,
       behavior_time, session_id
from ydsh_ods.ods_user_behavior;

-- ------------------------------------------------------------
-- 6. 校验
-- 说明：本机 Tez 的计数器上限为 120，单个 DAG 超过约 16 个顶点会报
--       "Too many counters"；因此校验拆成单条语句 + 小结果表，逐条执行。
-- ------------------------------------------------------------
-- 6.1 覆盖度：映射表是否覆盖全部原始取值（期望全部为 0）
drop table if exists qc_enum_unmapped;
create table qc_enum_unmapped (field_name string, unmapped_cnt int) stored as orc;

insert into table qc_enum_unmapped
select 'gender', count(*)
from (select distinct lower(trim(gender)) as v from ydsh_ods.ods_user) t
left join (select raw_value from dim_enum_map where field_name = 'gender') m on t.v = m.raw_value
where m.raw_value is null;

insert into table qc_enum_unmapped
select 'member_level', count(*)
from (select distinct lower(trim(member_level)) as v from ydsh_ods.ods_user) t
left join (select raw_value from dim_enum_map where field_name = 'member_level') m on t.v = m.raw_value
where m.raw_value is null;

insert into table qc_enum_unmapped
select 'order_status', count(*)
from (select distinct lower(trim(order_status)) as v from ydsh_ods.ods_order) t
left join (select raw_value from dim_enum_map where field_name = 'order_status') m on t.v = m.raw_value
where m.raw_value is null;

insert into table qc_enum_unmapped
select 'pay_type', count(*)
from (select distinct lower(trim(pay_type)) as v from ydsh_ods.ods_order) t
left join (select raw_value from dim_enum_map where field_name = 'pay_type') m on t.v = m.raw_value
where m.raw_value is null;

insert into table qc_enum_unmapped
select 'channel', count(*)
from (select distinct lower(trim(channel)) as v from ydsh_ods.ods_order) t
left join (select raw_value from dim_enum_map where field_name = 'channel') m on t.v = m.raw_value
where m.raw_value is null;

insert into table qc_enum_unmapped
select 'behavior_type', count(*)
from (select distinct lower(trim(behavior_type)) as v from ydsh_ods.ods_user_behavior) t
left join (select raw_value from dim_enum_map where field_name = 'behavior_type') m on t.v = m.raw_value
where m.raw_value is null;

select * from qc_enum_unmapped order by field_name;

-- 6.2 收敛效果：标准化后各字段取值集合（应只剩规范值域）
drop table if exists qc_enum_after;
create table qc_enum_after (field_name string, std_value string, cnt bigint) stored as orc;

insert into table qc_enum_after
select 'gender', gender, count(*) from stg_user_enum group by gender;
insert into table qc_enum_after
select 'member_level', member_level, count(*) from stg_user_enum group by member_level;
insert into table qc_enum_after
select 'order_status', order_status, count(*) from stg_order_enum group by order_status;
insert into table qc_enum_after
select 'pay_type', pay_type, count(*) from stg_order_enum group by pay_type;
insert into table qc_enum_after
select 'channel', channel, count(*) from stg_order_enum group by channel;
insert into table qc_enum_after
select 'behavior_type', behavior_type, count(*) from stg_behavior_enum group by behavior_type;

select field_name, std_value, cnt, count(*) over (partition by field_name) as value_cnt
from qc_enum_after
order by field_name, cnt desc;
