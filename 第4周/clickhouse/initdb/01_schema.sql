-- ============================================================
-- ClickHouse 初始化建表（docker-entrypoint-initdb.d，仅首次启动执行）
--
-- 定位：Hive 负责重计算（T+1 批量），ClickHouse 负责「大屏秒级查询」。
--       数据由 app/sync_clickhouse.py 从 Hive 的 ADS 表同步过来，
--       表结构与 ADS 表一一对应，字段名不改，前端不需要做映射。
--
-- 引擎选择：全部用 MergeTree + ORDER BY 业务主键
--           （不是 ReplacingMergeTree，因为同步是 truncate + insert，不需要去重）
-- ============================================================

create database if not exists ydsh_ads;

-- ------------------------------------------------------------
-- 1. 大屏 KPI 卡片（1 行）
-- ------------------------------------------------------------
create table if not exists ydsh_ads.screen_kpi
(
    total_gmv   Float64,
    order_cnt   UInt64,
    aov         Float64,
    buyer_cnt   UInt64,
    avg_pay     Float64
)
engine = MergeTree
order by tuple();

-- ------------------------------------------------------------
-- 2. 销售日报（趋势折线）
-- ------------------------------------------------------------
create table if not exists ydsh_ads.sales_daily
(
    order_date       String,
    order_cnt        UInt64,
    valid_order_cnt  UInt64,
    gmv              Float64,
    aov              Float64,
    buyer_cnt        UInt64,
    item_qty         UInt64,
    dod_pct          Nullable(Float64),
    wow_pct          Nullable(Float64),
    ma7              Nullable(Float64),
    ma30             Nullable(Float64)
)
engine = MergeTree
order by order_date;

-- ------------------------------------------------------------
-- 3. 品类排行（柱状图）
-- ------------------------------------------------------------
create table if not exists ydsh_ads.category_topn
(
    rank_no       UInt32,
    category      String,
    order_cnt     UInt64,
    item_qty      UInt64,
    gmv           Float64,
    gmv_share_pct Float64,
    aov           Float64
)
engine = MergeTree
order by rank_no;

-- ------------------------------------------------------------
-- 4. 区域排行（地图）
-- ------------------------------------------------------------
create table if not exists ydsh_ads.region_rank
(
    rank_no   UInt32,
    province  String,
    gmv       Float64,
    buyer_cnt UInt64,
    order_cnt UInt64,
    aov       Float64,
    share_pct Float64
)
engine = MergeTree
order by rank_no;

-- ------------------------------------------------------------
-- 5. 支付方式占比（饼图）
-- ------------------------------------------------------------
create table if not exists ydsh_ads.paytype_share
(
    pay_type        String,
    order_cnt       UInt64,
    gmv             Float64,
    order_share_pct Float64,
    gmv_share_pct   Float64
)
engine = MergeTree
order by pay_type;

-- ------------------------------------------------------------
-- 6. 时段 × 品类 热力图（24 × 8）
-- ------------------------------------------------------------
create table if not exists ydsh_ads.hour_category_heat
(
    order_hour UInt8,
    category   String,
    order_cnt  UInt64,
    item_qty   UInt64,
    gmv        Float64
)
engine = MergeTree
order by (order_hour, category);

-- ------------------------------------------------------------
-- 7. 品类月度结构（堆叠面积）
-- ------------------------------------------------------------
create table if not exists ydsh_ads.category_month_pivot
(
    ym             String,
    cat_yundong    Float64,
    cat_shuma      Float64,
    cat_fushi      Float64,
    cat_shipin     Float64,
    cat_meizhuang  Float64,
    cat_jiaju      Float64,
    cat_muying     Float64,
    cat_tushu      Float64,
    total_gmv      Float64
)
engine = MergeTree
order by ym;

-- ------------------------------------------------------------
-- 8. 门店排行（TopN 表）
-- ------------------------------------------------------------
create table if not exists ydsh_ads.store_rank
(
    rank_no     UInt32,
    store_id    String,
    store_name  String,
    province    String,
    city        String,
    area        UInt32,
    order_cnt   UInt64,
    buyer_cnt   UInt64,
    gmv         Float64,
    aov         Float64,
    gmv_per_sqm Float64,
    share_pct   Float64
)
engine = MergeTree
order by rank_no;

-- ------------------------------------------------------------
-- 9. 异常日（散点/标注）
-- ------------------------------------------------------------
create table if not exists ydsh_ads.anomaly_daily
(
    order_date   String,
    gmv          Float64,
    ma30         Float64,
    sd30         Float64,
    z_score      Float64,
    anomaly_type String,
    wow_pct      Nullable(Float64),
    order_cnt    UInt64,
    aov          Float64
)
engine = MergeTree
order by order_date;

-- ------------------------------------------------------------
-- 10. 渠道月度结构（多系列折线）
-- ------------------------------------------------------------
create table if not exists ydsh_ads.channel_month_share
(
    ym        String,
    channel   String,
    order_cnt UInt64,
    gmv       Float64,
    buyer_cnt UInt64,
    share_pct Float64
)
engine = MergeTree
order by (ym, channel);

-- ------------------------------------------------------------
-- 11. 同步水位表：记录每张表最后一次同步时间与行数，供健康检查
-- ------------------------------------------------------------
create table if not exists ydsh_ads.sync_meta
(
    table_name   String,
    row_count    UInt64,
    synced_at    DateTime
)
engine = ReplacingMergeTree(synced_at)
order by table_name;
