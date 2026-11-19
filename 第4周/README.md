# 第 4 周 · 可视化实现（Superset + ECharts）· 项目部署（Docker）

> 对应任务书：`04_第4周任务书.docx`、`04_第4周部署操作手册.docx`
> 团队：云仓五号
> 依赖：**必须先跑过 [第1周](../第1周/) / [第2周](../第2周/) / [第3周](../第3周/)**（四层数仓与 ADS 指标表由前三周提供）

## 一、本周目标

把前三周沉淀的数据与模型，转成管理层看得懂的图表，并部署成一套可稳定运行、密钥安全、查询够快的系统。

| # | 子任务 | 本周产出 | 位置 |
|---|---|---|---|
| 1 | Superset 连接数据源与仪表板 | Hive + ClickHouse 双数据源、可视化数据集 | `superset/`、`docker-compose.yml` |
| 2 | ECharts 复杂图表与数据大屏 | 8 类图表 + scale 自适应大屏 | `web/` |
| 3 | Streamlit 对话式问答 | NL2SQL 闭环 + ECharts 渲染 | `app/app_streamlit.py` |
| 4 | Docker Compose 一键部署 | 6 服务编排 + 健康检查 | `docker-compose.yml`、`scripts/deploy.ps1` |
| 5 | 密钥安全 / 查询优化 / 压测 | `.env` 注入、分区裁剪、压测记录 | `.env.example`、`docs/` |

## 二、目录结构

```
第4周/
├── docker-compose.yml           # 一键编排（include 复用第 1 周 mysql/hive）
├── .env.example                 # 环境变量模板（复制为 .env）
├── 一键运行_第4周.bat            # 双击入口（默认：启动+打开页面）
├── 一键部署.bat                  # 双击入口（等价于 /full 完整部署）
├── app/                         # Python 应用镜像
│   ├── Dockerfile
│   ├── requirements.txt
│   ├── entrypoint.sh            # 同时拉起 API(8000) 与 Streamlit(8501)
│   ├── main.py                  # FastAPI 数据接口（11 个端点）
│   ├── db.py                    # 数据访问层（ClickHouse 优先，Hive 兜底）
│   ├── app_streamlit.py         # 对话式问答（NL2SQL + ECharts）
│   └── sync_clickhouse.py       # Hive ADS → ClickHouse 同步
├── web/                         # ECharts 数据大屏
│   ├── index.html
│   ├── css/screen.css
│   ├── js/{api,charts,screen}.js
│   └── vendor/                  # echarts.min.js + china.json（本地化，断网可用）
├── clickhouse/initdb/01_schema.sql   # ClickHouse 建表（首次启动自动执行）
├── superset/
│   ├── superset_config.py       # 运行时配置（含 results backend）
│   └── bootstrap.sh             # 自动建管理员 + 注册数据源
├── sql/ads/70_ads_week4.sql     # 第 4 周新增 4 张可视化 ADS 表
├── scripts/
│   ├── deploy.ps1               # 一键部署主流程
│   ├── gen_env.py               # 生成 .env（随机口令）
│   ├── sync_clickhouse.py  → 见 app/
│   ├── smoke_test.py            # 30 项冒烟测试
│   ├── verify_superset.py       # Superset 数据源真实查询验证
│   └── verify_screen.js         # 大屏渲染验证（CDP）
└── reports/                     # 冒烟测试结果等
```

## 三、架构说明

```
                    ┌──────────────┐
   用户浏览器 ──────▶│  ECharts 大屏 │  :8000/
                    └──────┬───────┘
                           │ JSON
                    ┌──────▼───────┐        ┌──────────────┐
                    │  FastAPI     │───────▶│ ClickHouse   │ 毫秒级
                    │  :8000       │  优先   │ :8123        │
                    └──────┬───────┘        └──────▲───────┘
                           │ 降级                  │ 同步
                    ┌──────▼───────┐        ┌──────┴───────┐
                    │  Hive 数仓    │───────▶│sync_clickhouse│
                    │  :10000      │        └──────────────┘
                    └──────────────┘
                    ┌──────────────┐
                    │  Superset    │──▶ Hive + ClickHouse（双数据源）
                    │  :8088       │
                    └──────────────┘
                    ┌──────────────┐
                    │  Streamlit   │──▶ NL2SQL（复用第 3 周）──▶ Hive
                    │  :8501       │
                    └──────────────┘
```

**为什么 Hive + ClickHouse 双引擎**：Hive 负责 T+1 重计算（数据口径的唯一来源），ClickHouse 负责大屏秒级查询。同样的聚合查询，Hive 十几秒，ClickHouse 约 12 毫秒。`db.py` 里做了自动降级：ClickHouse 不可用时透明切到 Hive，保证演示不中断。

## 四、快速开始

### 4.1 日常使用（推荐）

```powershell
# 双击
一键运行_第4周.bat

# 或命令行
powershell -File scripts/run-week4.ps1 -Mode start
```

做四件事：检查 Docker → 起 6 个服务并等健康 → 快速探活 → 打开三个结果页面。
**约 1 分钟**，不重建数据，适合每天打开来看。

### 4.2 三种模式

| 命令 | 用途 | 耗时 |
|---|---|---|
| `一键运行_第4周.bat` | 启动服务 + 打开页面（日常） | ~1 分钟 |
| `一键运行_第4周.bat /full` | 完整部署：建表 → 同步 → 图表 → 筛选器 → 冒烟测试 | 5~10 分钟 |
| `一键运行_第4周.bat /test` | 只跑冒烟测试（37 项），不动服务 | ~1 分钟 |

> `/full` 适合**首次运行**或**数据重建后**（比如删过数据卷、重跑过前 3 周流水线）。
> 日常开关机用默认模式即可。

### 4.3 首次部署（命令行方式）

```powershell
cd 第4周
copy .env.example .env      # 首次：填写 CLICKHOUSE_PASSWORD / SUPERSET_SECRET_KEY / SUPERSET_ADMIN_PASSWORD
powershell -File scripts/deploy.ps1
```

`deploy.ps1` 与 `run-week4.ps1 -Mode full` 等价，保留它是因为前几周都用 `deploy.ps1` 命名。

### 4.4 自动完成的事

检查 Docker → 生成/校验 `.env` → 起 6 个服务 → 等 Hive 就绪 → 建 ADS 表 →
同步 ClickHouse → 搭建 Superset 图表 → 配置筛选器 → 冒烟测试 → 打印地址。

> **hive-server 重启问题**：如果服务没起来，脚本会自动重建 hive-server
> （清理容器内 `/tmp` 残留的 pid 文件）。这是 Hive 镜像的已知问题，手动处理也一样。

**访问地址**：

| 服务 | 地址 | 说明 |
|---|---|---|
| ECharts 数据大屏 | http://127.0.0.1:8000/ | 8 类图表，1920×1080 自适应 |
| 数据接口文档 | http://127.0.0.1:8000/docs | FastAPI 自动生成的 Swagger |
| Superset 仪表板 | http://127.0.0.1:8088/ | 账号见 `.env` |
| Streamlit 智能问答 | http://127.0.0.1:8501/ | NL2SQL 对话闭环 |

## 五、本周新增的 4 张 ADS 表

前三周的 ADS 表缺 3 个可视化必需的维度，本周补上（`sql/ads/70_ads_week4.sql`）：

| 表 | 行数 | 为什么需要 |
|---|---|---|
| `ads_hour_category_heat` | 192 | 任务书要求「热力图（时段×品类）」，前序表无 `order_hour` 维度 |
| `ads_screen_kpi` | 1 | 大屏顶部 4 个 KPI 卡片，单行多列一次取完，避免多次请求 |
| `ads_category_month_pivot` | 12 | 品类月度结构演化，前序只有周粒度 |
| `ads_paytype_share` | 4 | 任务书要求「支付方式占比饼图」，前序只有渠道维度 |

## 六、密钥安全（子任务 5）

| 措施 | 落地方式 |
|---|---|
| 不硬编码 | `docker-compose.yml` 里只写 `${VAR}`，无任何真实口令 |
| 不落盘 | 密钥只存在于 `.env`（已 `.gitignore`）与容器环境变量 |
| 强制校验 | compose 用 `${VAR:?提示}` 语法，缺失直接拒绝启动 |
| 随机生成 | `scripts/gen_env.py` 用 `secrets.token_urlsafe()` 生成强口令 |
| 不回显 | 所有脚本只打印口令长度，不打印明文；日志里 `llm_client` 有 `sanitize()` 打码 |
| 权限最小化 | app 容器以非 root（uid 1000）运行；ClickHouse 用专用账号而非 default |

## 七、查询优化（子任务 5）

| 优化点 | 做法 | 效果 |
|---|---|---|
| 引擎分层 | Hive 重算 → ClickHouse 服务查询 | 接口响应 12ms（Hive 同查询约 10s+） |
| 分区裁剪 | 查询一律带 `order_date` 条件 | 见 `docs/第4周_压力测试记录.md` |
| 预聚合 | 大屏只查 ADS 汇总表，不碰 DWD 明细 | 避免全表扫描 |
| 结果行数上限 | `QUERY_MAX_ROWS=500`、`SQL_MAX_ROW=50000` | 防止误查拖垮服务 |
| 连接复用 | ClickHouse 客户端短连接 + 5s 超时 | 失败快速降级，不阻塞 |

## 八、交付物清单

| # | 交付物 | 位置 | 状态 |
|---|---|---|---|
| 1 | Superset 仪表板 | http://127.0.0.1:8088/superset/dashboard/1/ | ✅ 5 图表 + 双数据源，渲染 0 报错 |
| 2 | ECharts 数据大屏 | `web/` | ✅ 8 类图表，渲染验证通过 |
| 3 | Streamlit 问答应用 | `app/app_streamlit.py` | ✅ NL2SQL 闭环 |
| 4 | docker-compose.yml | `第4周/docker-compose.yml` | ✅ 6 服务一键启动 |
| 5 | 部署手册 / API 配置指南 | `docs/第4周_部署手册.md` | ✅ |
| 6 | 压力测试记录 | `docs/第4周_压力测试记录.md` | ✅ |

## 九、Superset 仪表板自动化说明

`scripts/setup_dashboard.py` 用 API 自动建好 5 个数据集、5 个图表和 1 个仪表板，**不需要手工点 UI**。

搭建过程踩到的 5 个坑（都已修好，记下来避免重犯）：

| 坑 | 现象 | 根因 | 解法 |
|---|---|---|---|
| 仪表板整页「意外错误」 | 只有空白框 | `ROOT.children[0]` 是 HEADER 且 HEADER 无 children | **不要加 HEADER_ID**，ROOT 直接挂 GRID |
| 图表全空 | 容器在但没画 | `dashboard_slices` 关联表为空 | 用 SQLAlchemy 直连插入关联 |
| 图表框内「意外错误」 | `Cannot read properties of undefined` | `query_context` 为 NULL | 建图表后单独 PUT `query_context` |
| 柱状图/地图报错 | 走旧接口 `explore_json` 返回 400 | `viz_type` 用了旧版 `bar`/`country_map` | 改用 `echarts_timeseries_bar` |
| 柱状图 400 | 后端校验失败 | `x_axis` 与 `groupby` 同时写同一列，生成重复 columns | 只写 `x_axis`，`groupby` 留空 |

**筛选器**由 `scripts/add_filters.py` 写入 `json_metadata.native_filter_configuration`，共 2 个：

| 筛选器 | 类型 | 作用范围 |
|---|---|---|
| 日期范围 | `time_range` | `sales_daily.order_date`，控制销售趋势时间窗 |
| 品类 | `filter_select`（多选） | `category_topn.category`，筛选品类相关图表 |

> 为什么直接改元数据而不调 API：Superset 4.x 的 dashboard PUT 对
> `native_filter_configuration` 校验极严，字段缺一个就 400；而它的 schema 本身是
> `List[Dict]`，直接写元数据最稳，前端读的是同一份。

## 十、已知限制

1. **Superset 地图图表**：`country_map` 属旧版插件，4.1.2 里走废弃接口会 400，因此仪表板用「省份排行柱状图」替代；真正的中国地图热力图由 ECharts 大屏承担（`web/js/charts.js` 的 `map()`）。
2. **Superset 截图需特殊处理**：图表用 canvas 渲染，`Page.captureScreenshot` 截出来是纯白页（彩色像素 <0.5%）。已用 `scripts/capture_superset_canvas.js` 把 canvas 像素导出后重新拼图解决，成品见 `docs/screenshots/02_Superset仪表板.png`。
3. **大屏每 60 秒自动刷新**：现场演示若要固定画面，改 `web/js/screen.js` 的 `REFRESH_MS`。
4. **ClickHouse 同步是手动的**：数据更新后需重跑 `docker exec dsh-app python /app/sync_clickhouse.py`（或 `deploy.ps1`）。
5. **`.env` 需自行填写**：仓库不提供真实口令，首次部署必须复制模板并填写，否则 compose 会拒绝启动（刻意的安全设计）。
