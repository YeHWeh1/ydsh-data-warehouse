# 第 1 周 · 环境搭建 · 数据导入与清洗（一）

> 对应任务书：`01_第1周任务书.docx`、`01_第1周部署操作手册.docx`
> 团队：云仓五号
> 本目录是可独立运行的一周：环境定义、ODS 建表导入、质量预评估、首轮清洗。

## 一、本周目标

1. 用 Docker 搭起 Hive 4.0.0 + MySQL Metastore + HiveServer2 的可复现环境；
2. 把 6 个 CSV 原样导入 `ydsh_ods`（贴源层，共 114,809 行）；
3. 做五维度数据质量预评估（完整性 / 唯一性 / 有效性 / 一致性 / 准确性）；
4. 完成缺失值与重复值两类清洗（`age` 填充、订单去重），异常值只标记不改值；
5. 产出环境笔记、质量预评估表、清洗脚本与答辩材料。

## 二、目录结构

```
第1周\
├── docker-compose.yml        # 3 个服务：mysql / hive-metastore / hive-server
├── lib\
│   └── mysql-connector-j-8.4.0.jar   # 官方镜像缺的 MySQL 驱动
├── 模拟数据\                  # 6 个 CSV（不入版本库，需自行放入）
├── sql\
│   ├── ods\
│   │   ├── create_ods.sql    # 建库建表（6 张 ods_ 表）
│   │   └── load_ods.sql      # 导入数据 + 行数校验
│   └── clean\
│       ├── 00_quality_scan.sql   # 五维度扫描（只读）
│       ├── 01_dwd_user.sql       # 缺失值填充 + 缺失标记
│       ├── 02_dwd_product.sql    # 上架日期按类目中位数补全
│       ├── 03_dwd_order.sql      # 订单去重（ROW_NUMBER）
│       ├── 04_dwd_other.sql      # 门店表原样下沉
│       └── 05_dwd_extra.sql      # 明细/行为表异常标记
├── reports\
│   ├── 数据质量预评估表.xlsx     # 7 个工作表
│   └── 质量预评估表.md           # 同内容 Markdown
├── docs\
│   ├── 环境搭建笔记.md           # 9 个环境问题的现象、排查与解决（+ 第 2 周补充的问题 10）
│   ├── 云仓五号_第1周_AI协同日志.md
│   ├── 答辩手册.md               # 演示流程、代码讲解、问答
│   └── screenshots\             # 8 张环境关键截图
├── scripts\
│   └── run-all.ps1              # 一键：起环境 → 等就绪 → 清洗 → 展示
└── 一键运行.bat                  # 双击入口（转调 run-all.ps1）
```

## 三、技术栈与环境

| 组件 | 版本 | 用途 |
|---|---|---|
| Docker Desktop | 4.96.3（Engine 29.9.2，WSL2 后端） | 容器运行环境 |
| Docker Compose | v5.6.1 | 多容器编排 |
| Apache Hive | 4.0.0（Tez 0.10.3） | 数据仓库，含 Metastore + HiveServer2 |
| MySQL | 8.0.46 | Hive 元数据库 |
| Git | 2.56.0 | 版本控制 |

服务端口（宿主机）：`10000` HiveServer2、`9083` Metastore、`3307` MySQL。

> 项目名固定为 `dshproject`（compose 文件里的 `name:`），所以目录搬家后数据卷
> `dshproject_hive-data` / `dshproject_mysql-data` 仍然复用，不会"丢数据"。

## 四、快速开始

```powershell
# 方式一：双击
一键运行.bat

# 方式二：命令行
docker compose up -d
powershell -File scripts/run-all.ps1
```

前置条件：`模拟数据\` 下放好 6 个 CSV（`ods_user.csv`、`ods_product.csv`、`ods_store.csv`、
`ods_order.csv`、`ods_order_item.csv`、`ods_user_behavior.csv`），容器内通过 `/data` 只读挂载。

## 五、本周成果

| 指标 | 结果 |
|---|---|
| 导入表数 | 6 张 ODS 原始表 + 6 张 DWD 清洗表 |
| 数据总量 | 114,809 行 |
| 缺失值处理 | `age` 填充 78 条（51 空值 + 27 越界）；`email`/`gender`/`member_level` 共 291 条加缺失标记 |
| 日期补全 | `launch_date` 按二级类目中位数补全 11 条 |
| 重复值处理 | 订单 15,600 → 15,000（去重 600 条，保留最新下单） |
| 异常标记 | 数量异常 718 + 363 条；枚举写法异常 3,672 条 |

详细数据见 [reports/质量预评估表.md](reports/质量预评估表.md)。

## 六、交付物清单

| # | 交付物 | 位置 |
|---|---|---|
| 1 | 可复现的容器环境 | `docker-compose.yml` + `lib/` + 环境笔记 |
| 2 | ODS 建表与导入脚本 | `sql/ods/` |
| 3 | 五维度质量预评估 | `reports/数据质量预评估表.xlsx`、`质量预评估表.md` |
| 4 | 首轮清洗脚本（缺失值/重复值） | `sql/clean/00~05` |
| 5 | 一键运行脚本 | `scripts/run-all.ps1`、`一键运行.bat` |
| 6 | 答辩材料 | `docs/答辩手册.md` |

## 七、说明

- **ODS 原表不做任何修改**，便于回溯；清洗结果写入 `ydsh_ods.dwd_*`。
- **清洗用 CTAS**：Hive 非事务表不支持 UPDATE，脚本可重复执行。
- **异常值只标记不改值**：负金额、负数量、枚举混用等需要业务规则确认，留到第 2 周统一规范化。
- 第 1 周的 `dwd_*` 与第 2 周的 `ydsh_dwd.dim_*` 是两套产物：前者是"标记式清洗"，
  后者是"规范化 + 星型建模"，可对照查看。
