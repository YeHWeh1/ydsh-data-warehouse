-- ============================================================
-- 01_load_dim.sql —— 子任务 6（上）：DWD 维度表加载
-- 依据任务书 子任务 6 / 部署手册 步骤 6
-- 内容：dim_date（时间维度）、dim_user、dim_product、dim_store
-- 编码映射：dim_user 通过 dim_member_level 把等级中文名映射为编码，
--           gender 编码通过 dim_enum_map 关联得到（体现"编码表关联"）
-- ============================================================

use ydsh_dwd;

-- ------------------------------------------------------------
-- 1. 时间维度：2024-01-01 ~ 2024-12-31，共 366 天（闰年）
--    Hive 无 sequence 函数，用 split(space(n),'') + posexplode 生成序号
-- ------------------------------------------------------------
insert overwrite table dim_date
select date_format(dt, 'yyyy-MM-dd') as date_id,
       year(dt) as year,
       quarter(dt) as quarter,
       month(dt) as month,
       day(dt) as day,
       weekofyear(dt) as week_of_year,
       dayofweek(dt) as day_of_week,
       case dayofweek(dt) when 1 then '周日' when 2 then '周一' when 3 then '周二'
            when 4 then '周三' when 5 then '周四' when 6 then '周五' else '周六' end as weekday_name,
       case when dayofweek(dt) in (1, 7) then 1 else 0 end as is_weekend,
       date_format(dt, 'yyyy-MM') as year_month
from (
    select date_add('2024-01-01', i) as dt
    from (select posexplode(split(space(365), '')) as (i, v)) s
) t;

-- ------------------------------------------------------------
-- 2. 用户维度：维度表承担"中文名 → 编码"的转换，事实表只存编码
-- ------------------------------------------------------------
insert overwrite table dim_user
select u.user_id,
       u.user_name,
       u.gender,
       g.std_code as gender_code,
       u.age,
       u.age_band,
       u.city,
       u.province,
       u.region,
       u.register_date,
       u.register_month,
       coalesce(l.member_code, 'UNKNOWN') as member_level_code,
       u.member_level,
       u.phone,
       u.email,
       u.email_domain,
       u.age_filled,
       u.gender_filled,
       u.member_level_filled,
       u.email_missing
from cln_user_full u
left join (select std_value, std_code from dim_enum_map where field_name = 'gender'
           group by std_value, std_code) g
       on u.gender = g.std_value
left join dim_member_level l
       on u.member_level = l.member_name;

-- ------------------------------------------------------------
-- 3. 商品维度
-- ------------------------------------------------------------
insert overwrite table dim_product
select product_id,
       product_name,
       category,
       sub_category,
       brand,
       price,
       price_flag,
       stock,
       status,
       launch_date,
       launch_filled
from cln_product;

-- ------------------------------------------------------------
-- 4. 门店维度
-- ------------------------------------------------------------
insert overwrite table dim_store
select store_id,
       store_name,
       province,
       city,
       district,
       region,
       address,
       open_date,
       area,
       manager
from cln_store_full;

-- ------------------------------------------------------------
-- 5. 校验
-- ------------------------------------------------------------
select 'dim_date' as tbl, count(*) as cnt, count(distinct date_id) as keys from dim_date
union all
select 'dim_user', count(*), count(distinct user_id) from dim_user;

-- 维度表编码完整性：不应出现编码为空
select sum(if(gender_code is null, 1, 0)) as gender_code_null,
       sum(if(member_level_code is null, 1, 0)) as member_code_null,
       count(distinct member_level_code) as member_code_cnt,
       count(distinct gender_code) as gender_code_cnt
from dim_user;

-- 商品/门店维度行数
select 'dim_product' as tbl, count(*) as cnt, count(distinct category) as cat_cnt,
       sum(if(price is null, 1, 0)) as price_null
from dim_product
union all
select 'dim_store', count(*), count(distinct province), sum(if(area is null, 1, 0)) from dim_store;
