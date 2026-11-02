-- ============================================================
-- 00_ddl.sql —— 第 2 周：四层数仓 DDL（DWD 维度表 + 事实表 + DWS/ADS 库）
-- 分层：ydsh_ods（贴源层，第 1 周已建）→ ydsh_dwd（明细层）→ ydsh_dws（汇总层）→ ydsh_ads（应用层）
-- 存储：维度表 / 事实表统一 stored as orc
-- 优化：dwd_order、dwd_order_item 按 order_date 分区（分区裁剪）
--       dwd_user_behavior 按 user_id 分 8 桶（分桶表 + Map 端桶连接）
-- 执行顺序：本脚本 → 10_enum_standardize → 11_anomaly_format → 12_split_merge
--           → 01_load_dim → 02_load_fact → 13_quality_compare
-- ============================================================

create database if not exists ydsh_dwd comment 'DWD 明细层：清洗、标准化、维度关联后的明细事实与维度';
create database if not exists ydsh_dws comment 'DWS 汇总层：按主题轻度汇总';
create database if not exists ydsh_ads comment 'ADS 应用层：面向指标与报表';

use ydsh_dwd;

-- ------------------------------------------------------------
-- 1. 时间维度表
-- ------------------------------------------------------------
drop table if exists dim_date;
create table dim_date (
  date_id      string comment '日期 yyyy-MM-dd（主键）',
  year         int    comment '年',
  quarter      int    comment '季度',
  month        int    comment '月',
  day          int    comment '日',
  week_of_year int    comment '年内第几周',
  day_of_week  int    comment '1=周日 2=周一 ... 7=周六',
  weekday_name string comment '星期中文名',
  is_weekend   int    comment '1=周末',
  year_month   string comment '年月 yyyy-MM'
) stored as orc;

-- ------------------------------------------------------------
-- 2. 枚举映射表（清洗规则的可查询化：字段 → 原始取值 → 规范值/编码）
-- ------------------------------------------------------------
drop table if exists dim_enum_map;
create table dim_enum_map (
  field_name  string comment '字段名',
  raw_value   string comment '原始取值（统一转小写去空格后登记）',
  std_value   string comment '规范值',
  std_code    string comment '规范编码',
  is_standard int    comment '1=原始即为规范值，0=脏取值'
) stored as orc;

-- ------------------------------------------------------------
-- 3. 编码映射维度表（子任务 3：会员等级 / 支付方式）
-- ------------------------------------------------------------
drop table if exists dim_member_level;
create table dim_member_level (
  member_code string comment '等级编码',
  member_name string comment '等级中文名',
  level_rank  int    comment '等级序号，越大越高'
) stored as orc;

drop table if exists dim_pay_type;
create table dim_pay_type (
  pay_code  string comment '支付方式编码',
  pay_name  string comment '支付方式中文名',
  is_online int    comment '1=线上支付，0=线下/货到付款'
) stored as orc;

-- ------------------------------------------------------------
-- 4. 用户维度表
-- ------------------------------------------------------------
drop table if exists dim_user;
create table dim_user (
  user_id              string comment '用户编号（主键）',
  user_name            string comment '姓名',
  gender               string comment '性别（规范值：男/女/未知）',
  gender_code          string comment '性别编码 M/F/U',
  age                  int    comment '年龄（1~120）',
  age_band             string comment '年龄段',
  city                 string comment '城市',
  province             string comment '省份',
  region               string comment '省份-城市（字段合并）',
  register_date        string comment '注册日期 yyyy-MM-dd',
  register_month       string comment '注册年月 yyyy-MM',
  member_level_code    string comment '会员等级编码',
  member_level         string comment '会员等级（规范中文）',
  phone                string comment '手机号（11 位纯数字）',
  email                string comment '邮箱（保留空值，缺失可追溯）',
  email_domain         string comment '邮箱域名（字段拆分）',
  age_filled           int    comment '1=年龄为空或越界，已用中位数填充',
  gender_filled        int    comment '1=性别为空，已规范为未知',
  member_level_filled  int    comment '1=等级为空，已规范为未知',
  email_missing        int    comment '1=邮箱缺失'
) stored as orc;

-- ------------------------------------------------------------
-- 5. 商品维度表
-- ------------------------------------------------------------
drop table if exists dim_product;
create table dim_product (
  product_id    string comment '商品编号（主键）',
  product_name  string comment '商品名称',
  category      string comment '一级类目（已去首尾空格）',
  sub_category  string comment '二级类目',
  brand         string comment '品牌',
  price         double comment '售价（异常已置空，可追溯）',
  price_flag    int    comment '0=正常 1=负价格修正 2=超大价格置空',
  stock         int    comment '库存',
  status        string comment '商品状态',
  launch_date   string comment '上架日期 yyyy-MM-dd',
  launch_filled int    comment '1=上架日期缺失已按类目中位数补全'
) stored as orc;

-- ------------------------------------------------------------
-- 6. 门店维度表
-- ------------------------------------------------------------
drop table if exists dim_store;
create table dim_store (
  store_id   string comment '门店编号（主键）',
  store_name string comment '门店名称',
  province   string comment '省份',
  city       string comment '城市',
  district   string comment '区县',
  region     string comment '省份-城市',
  address    string comment '详细地址',
  open_date  string comment '开业日期',
  area       int    comment '营业面积',
  manager    string comment '店长'
) stored as orc;

-- ------------------------------------------------------------
-- 7. 订单事实表（分区表：按 order_date 动态分区）
-- ------------------------------------------------------------
drop table if exists dwd_order;
create table dwd_order (
  order_id          string comment '订单编号（主键）',
  user_id           string comment '用户编号（外键 → dim_user）',
  store_id          string comment '门店编号（外键 → dim_store）',
  order_timestamp   string comment '下单完整时间 yyyy-MM-dd HH:mm:ss（日期+时间合并）',
  order_hour        int    comment '下单小时（时间字段拆分）',
  pay_amount        double comment '实付金额（负值已取绝对值，超大值已置空）',
  discount_amount   double comment '优惠金额',
  net_amount        double comment '净额 = 实付 - 优惠',
  pay_type_code     string comment '支付方式编码（关联 dim_pay_type）',
  pay_type          string comment '支付方式中文名',
  order_status_code string comment '订单状态编码',
  order_status      string comment '订单状态中文名',
  channel_code      string comment '渠道编码',
  channel           string comment '下单渠道',
  amount_flag       int    comment '0=正常 1=负金额已修正 2=超大金额已置空',
  discount_flag     int    comment '1=负优惠金额已修正',
  is_valid_amount   int    comment '1=金额可参与统计'
) partitioned by (order_date string comment '下单日期分区键')
stored as orc;

-- ------------------------------------------------------------
-- 8. 订单明细事实表（分区表）
-- ------------------------------------------------------------
drop table if exists dwd_order_item;
create table dwd_order_item (
  item_id     string comment '明细编号（主键）',
  order_id    string comment '订单编号（外键 → dwd_order）',
  product_id  string comment '商品编号（外键 → dim_product）',
  quantity    int    comment '购买数量（0 与超大值已置空）',
  unit_price  double comment '商品单价（负值已修正）',
  discount    double comment '折扣率',
  item_amount double comment '明细金额 = 数量 × 单价 × (1-折扣)',
  qty_flag    int    comment '0=正常 1=负数量修正 2=零数量置空 3=超大数量置空'
) partitioned by (order_date string comment '下单日期分区键')
stored as orc;

-- ------------------------------------------------------------
-- 9. 用户行为事实表（分桶表：按 user_id 分 8 桶）
-- ------------------------------------------------------------
drop table if exists dwd_user_behavior;
create table dwd_user_behavior (
  behavior_id   string comment '行为编号（主键）',
  user_id       string comment '用户编号（分桶键）',
  product_id    string comment '商品编号',
  behavior_type string comment '行为类型编码 pv/cart/buy/fav',
  behavior_name string comment '行为类型中文名',
  behavior_time string comment '行为时间 yyyy-MM-dd HH:mm:ss',
  behavior_date string comment '行为日期',
  behavior_hour int    comment '行为小时',
  session_id    string comment '会话编号'
) clustered by (user_id) into 8 buckets
stored as orc;

-- 校验：应列出 7 张维度表 + 3 张事实表
show tables in ydsh_dwd;
