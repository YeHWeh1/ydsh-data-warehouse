-- 02_dwd_product.sql —— 商品表缺失值处理
-- 策略（依据任务书 子任务6 "上架日期按规则补全"）：
--   规则：用【同一二级类目】内已有 launch_date 的中位数日期补全；
--         若该类目也没有有效日期，则补 1970-01-01 并标记。
--   同时新增 launch_date_filled 标记（1=补全过）

drop table if exists ydsh_ods.dwd_product;

create table ydsh_ods.dwd_product as
select p.product_id,
       p.product_name,
       p.category,
       p.sub_category,
       p.brand,
       p.price,
       p.cost,
       p.stock,
       p.status,
       case when p.launch_date is null or p.launch_date = ''
            then coalesce(d.med_date, '1970-01-01')
            else p.launch_date
       end as launch_date,
       case when p.launch_date is null or p.launch_date = '' then 1 else 0 end as launch_date_filled
from ydsh_ods.ods_product p
left join (
    select sub_category,
           from_unixtime(
               cast(percentile(cast(unix_timestamp(launch_date,'yyyy-MM-dd') as bigint), 0.5) as bigint),
               'yyyy-MM-dd'
           ) as med_date
    from ydsh_ods.ods_product
    where launch_date is not null and launch_date <> ''
    group by sub_category
) d on p.sub_category = d.sub_category;

-- 校验：应返回 600 行，补全 11 条，无空日期
select count(*) as total,
       sum(launch_date_filled) as filled_cnt,
       sum(case when launch_date is null or launch_date = '' then 1 else 0 end) as still_null
from ydsh_ods.dwd_product;
