-- ============================================================
-- create_ods.sql —— ODS 层建库与建表（只建结构，不导数据）
-- 库：ydsh_ods
-- 约定：逗号分隔；跳过 CSV 表头；ODS 层字段以 string 为主（贴源层不做类型清洗）
-- 执行：docker exec hive-server beeline -u "jdbc:hive2://localhost:10000" -f /opt/hive/data/create_ods.sql
-- ============================================================

create database if not exists ydsh_ods;

-- 1) 用户信息表
create table if not exists ydsh_ods.ods_user (
  user_id       string comment '用户编号（主键）',
  user_name     string comment '用户姓名',
  gender        string comment '性别',
  age           int    comment '年龄',
  city          string comment '城市',
  province      string comment '省份',
  register_date string comment '注册日期',
  member_level  string comment '会员等级',
  phone         string comment '手机号',
  email         string comment '邮箱'
) row format delimited fields terminated by ','
tblproperties('skip.header.line.count'='1');

-- 2) 商品信息表
create table if not exists ydsh_ods.ods_product (
  product_id   string comment '商品编号（主键）',
  product_name string comment '商品名称',
  category     string comment '一级类目',
  sub_category string comment '二级类目',
  brand        string comment '品牌',
  price        double comment '售价',
  cost         double comment '成本价',
  stock        int    comment '库存',
  status       string comment '状态',
  launch_date  string comment '上架日期'
) row format delimited fields terminated by ','
tblproperties('skip.header.line.count'='1');

-- 3) 门店信息表
create table if not exists ydsh_ods.ods_store (
  store_id   string comment '门店编号（主键）',
  store_name string comment '门店名称',
  province   string comment '省份',
  city       string comment '城市',
  district   string comment '区县',
  address    string comment '详细地址',
  open_date  string comment '开业日期',
  area       int    comment '营业面积',
  manager    string comment '店长'
) row format delimited fields terminated by ','
tblproperties('skip.header.line.count'='1');

-- 4) 订单主表
create table if not exists ydsh_ods.ods_order (
  order_id        string comment '订单编号',
  user_id         string comment '用户编号',
  store_id        string comment '门店编号',
  order_date      string comment '下单日期',
  order_time      string comment '下单时间',
  pay_amount      double comment '实付金额',
  discount_amount double comment '优惠金额',
  pay_type        string comment '支付方式',
  order_status    string comment '订单状态',
  channel         string comment '下单渠道'
) row format delimited fields terminated by ','
tblproperties('skip.header.line.count'='1');

-- 5) 订单明细表
create table if not exists ydsh_ods.ods_order_item (
  item_id    string comment '明细编号（主键）',
  order_id   string comment '所属订单编号',
  product_id string comment '商品编号',
  quantity   int    comment '购买数量',
  unit_price double comment '商品单价',
  discount   double comment '折扣率'
) row format delimited fields terminated by ','
tblproperties('skip.header.line.count'='1');

-- 6) 用户行为日志表
create table if not exists ydsh_ods.ods_user_behavior (
  behavior_id   string comment '行为编号（主键）',
  user_id       string comment '用户编号',
  product_id    string comment '商品编号',
  behavior_type string comment '行为类型',
  behavior_time string comment '行为时间',
  session_id    string comment '会话编号'
) row format delimited fields terminated by ','
tblproperties('skip.header.line.count'='1');

-- 校验：应列出 6 张 ods_ 表
show tables in ydsh_ods;
