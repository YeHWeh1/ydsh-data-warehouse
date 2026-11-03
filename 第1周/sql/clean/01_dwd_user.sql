-- 01_dwd_user.sql —— 用户表缺失值处理
-- 策略（依据任务书 子任务6「定位各表缺失字段」+ 部署手册 步骤8）：
--   age          : 先取 1~120 区间的均值（排除 200、-3 等异常值对均值的污染），
--                  再把【空值】和【越界值】统一替换为该均值；打 age_filled 标记
--   email        : 保留空值，新增 email_missing 标记（1=缺失）
--   gender       : 保留空值，新增 gender_missing 标记（不臆造取值）
--   member_level : 保留空值，新增 member_level_missing 标记
-- 说明：Hive 表不支持 UPDATE，采用 CTAS 生成新表，ODS 原表不做任何修改。

drop table if exists ydsh_ods.dwd_user;

create table ydsh_ods.dwd_user as
select u.user_id,
       u.user_name,
       u.gender,
       case when u.age is null or u.age < 1 or u.age > 120
            then m.avg_age
            else u.age
       end as age,
       u.city,
       u.province,
       u.register_date,
       u.member_level,
       u.phone,
       u.email,
       case when u.age is null or u.age < 1 or u.age > 120 then 1 else 0 end as age_filled,
       case when u.email is null or u.email = '' then 1 else 0 end as email_missing,
       case when u.gender is null or u.gender = '' then 1 else 0 end as gender_missing,
       case when u.member_level is null or u.member_level = '' then 1 else 0 end as member_level_missing
from ydsh_ods.ods_user u
cross join (
    select cast(round(avg(age)) as int) as avg_age
    from ydsh_ods.ods_user
    where age between 1 and 120          -- 只用合法区间算均值
) m;

-- 校验：1000 行；age 无空值、区间 [18,65]；四个缺失标记与扫描结果一致
--       期望：filled=78  email=193  gender=84  member_level=14
select count(*) as total,
       sum(age_filled) as age_filled_cnt,
       sum(email_missing) as email_missing_cnt,
       sum(gender_missing) as gender_missing_cnt,
       sum(member_level_missing) as member_level_missing_cnt,
       min(age) as min_age,
       max(age) as max_age,
       sum(case when age is null then 1 else 0 end) as age_null
from ydsh_ods.dwd_user;
