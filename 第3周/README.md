# 第 3 周 · 数据分析 · 预测建模 · AI 协同（NL2SQL）

> 对应任务书：`03_第3周任务书.docx`、`03_第3周部署操作手册.docx`
> 团队：云仓五号
> 依赖：**必须先跑过 [第1周](../第1周/) 与 [第2周](../第2周/)**（环境、ODS、DWD/DWS/ADS 基础表由前两周提供）

## 一、本周目标（6 个子任务）

| # | 子任务 | 产出 |
|---|---|---|
| 1 | DWS / ADS 层建设与 KPI 计算 | 7 张 DWS 扩展表 + 9 张 ADS 扩展表 + 指标定义文档 |
| 2 | 时间序列分析与预测建模 | 5 个模型对比、滚动回测、未来 7 天 GMV 预测、预测建模分析报告 |
| 3 | 大模型 API 接入与密钥管理 | `.env` 密钥管理、最小调用脚本、离线兜底 |
| 4 | NL2SQL 构建 | Schema 自动抽取 + LangChain 风格链路 + Prompt 设计 + 8 条测试 |
| 5 | AI 智能洞察与报告生成 | 异常归因、经营分析摘要、数字回溯校验、AI 应用反思 |
| 6 | Git 团队协同实战 | 分支 / PR / Code Review / Issue 记录 |

## 二、目录结构

```
第3周/
├── py/                          # Python 流水线（本周核心）
│   ├── config.py                # 配置与口径常量（唯一来源）
│   ├── console.py               # 控制台 UTF-8 兜底
│   ├── hive_client.py           # Hive 查询封装
│   ├── schema_extract.py        # 子任务4：Schema 自动抽取
│   ├── forecast.py              # 子任务2：时间序列与预测建模
│   ├── llm_client.py            # 子任务3：大模型接入与密钥管理
│   ├── nl2sql.py                # 子任务4：NL2SQL 链路
│   ├── insight.py               # 子任务5：AI 洞察与报告生成
│   ├── report_build.py          # 报告与看板生成
│   └── run_week3.py             # Python 侧一键执行
├── sql/
│   ├── dws/50_dws_ext.sql       # 子任务1：DWS 汇总层扩展
│   └── ads/60_ads_ext.sql       # 子任务1：ADS 应用层扩展
├── reports/
│   ├── 第3周_预测建模分析报告.md / .docx
│   ├── 第3周_AI洞察与经营简报.md / .docx
│   ├── 第3周_运行看板.html       # 浏览器打开的可视化看板
│   ├── 第3周_运行记录.md         # 终端真实输出
│   ├── figures/                 # 5 张图表（PNG）
│   └── data/                    # 中间产物（JSON / CSV）
├── docs/
│   ├── 第3周_指标定义文档.md     # 口径唯一来源（必读）
│   ├── 第3周_AI应用反思.md       # 子任务5 要求
│   ├── 第3周_AI协同日志.md
│   ├── 第3周_答辩手册.md
│   └── screenshots/
├── scripts/
│   ├── run-week3.ps1            # 一键：Docker → Hive → SQL → Python → docx → 看板
│   └── md2docx.py               # Markdown → Word
└── 一键运行_第3周.bat            # 双击入口
```

## 三、快速开始

### 3.1 双击（推荐）

| 双击 | 作用 |
|---|---|
| **一键运行_第3周.bat** | 起环境 → 等 Hive → 跑 SQL → 跑 Python → 生成 docx → 打开看板 |
| `scripts\run-week3.ps1 -SkipRun` | 不重跑，直接打开看板 |
| `scripts\run-week3.ps1 -ShowOnly` | 不重跑，只打印终端结果表 |
| `scripts\run-week3.ps1 -NoLLM` | 强制离线模式（不调用大模型） |

> **闪退排查**：`.bat` 结束时一定会 `pause`，正常不会闪退。
> 如果仍然一闪而过，在 `cmd` 里手动执行 `一键运行_第3周.bat` 就能看到完整报错。
> 脚本第 0 步会先定位 Python，找不到会明确提示而不是静默退出。

### 3.2 只跑 Python（Hive 已在运行）

```powershell
cd 第3周\py
python run_week3.py            # 全流程
python run_week3.py --no-llm   # 强制离线
python run_week3.py --only 2   # 只跑预测建模
```

> 注意：这里的 `python` 指**装了依赖的那个 Python**。本机系统自带的 `python` / `py` 是 3.8 且没装 pandas，
> 一键脚本会自动去找带依赖的解释器；手动执行时请用完整路径，或先设 `set PYTHON=<你的 python.exe>`。

### 3.3 配置大模型（可选）

在项目根目录 `.env`（已被 `.gitignore` 排除）填入任一 Key：

```ini
DEEPSEEK_API_KEY=sk-xxxxxxxx
# 或 DASHSCOPE_API_KEY=sk-xxxxxxxx
# 或 OPENAI_API_KEY=sk-xxxxxxxx
LLM_PROVIDER=deepseek
```

**不配置也能跑通全流程**——所有环节都有确定性兜底：

| 环节 | 有 Key | 无 Key |
|---|---|---|
| NL2SQL | 大模型生成 SQL | 规则引擎生成等价 SQL（模式标记 `rule_based`） |
| 异常归因 | 大模型组织语言 | 结构化模板 |
| 经营摘要 | 大模型撰写 | 结构化模板 |

两种模式结构一致、可对比，答辩时可现场说明差异。

## 四、本周核心结果

### 4.1 指标（与第 2 周口径完全对齐）

| 指标 | 数值 |
|---|---|
| 总 GMV | 3,702.79 万元 |
| 订单量 / 有效订单量 | 15,000 / 14,696 单 |
| 客单价 AOV | 2,519.59 元 |
| 活跃用户数 | 1,000 人 |
| 客单件数 | 7.37 件/单 |
| 日均 GMV | 101,169.01 元 |
| 门店数 | 60 家 |

### 4.2 预测建模

| 模型 | MAE（元） | MAPE | 排名 |
|---|---|---|---|
| **季节调整 + 线性回归** | **11,940.79** | **11.54%** | 1 |
| 岭回归 | 12,899.27 | 12.42% | 2 |
| 线性回归 | 12,914.72 | 12.43% | 3 |
| XGBoost | 15,355.87 | 15.39% | 4 |
| 朴素基线（lag1） | 19,849.59 | 19.88% | 5 |

- 最优模型比朴素基线 **MAPE 降低 42.0%**
- **XGBoost 反而最差**：352 天样本下树模型容易记噪声，复杂不等于更好
- **关键发现**：纯递归预测会向均值收敛（预测 CV 0.95% vs 历史 18.46%），
  改用季节调整路线后 CV 提升到 3.05%，且 MAPE 同步改善
- 未来 7 天预测合计 **709,609.39 元**

### 4.3 NL2SQL

8 条测试用例 **8/8 通过**，覆盖跨表 join、排序、聚合、占比、异常检测等场景。
三层防护：Prompt 约束 → 静态校验（只读 + 表白名单）→ 执行兜底。

### 4.4 数据规模

| 层 | 第 2 周 | 第 3 周新增 | 合计 |
|---|---|---|---|
| DWS | 6 张 | 7 张 | 13 张 |
| ADS | 6 张 | 9 张 | 15 张 |
| Python 脚本 | — | 9 个 | 9 个 |
| 图表 | — | 5 张 | 5 张 |

## 五、交付物清单（对照任务书）

| # | 交付物 | 位置 |
|---|---|---|
| 1 | DWS / ADS 建模与 ETL 脚本 | [`sql/dws/50_dws_ext.sql`](sql/dws/50_dws_ext.sql)、[`sql/ads/60_ads_ext.sql`](sql/ads/60_ads_ext.sql) |
| 2 | KPI 指标结果表 | [`docs/第3周_KPI指标结果表.xlsx`](docs/第3周_KPI指标结果表.xlsx)（6 个 sheet）+ `ydsh_ads.ads_kpi_week3` + [`docs/第3周_指标定义文档.md`](docs/第3周_指标定义文档.md) |
| 3 | 预测建模分析报告 | [`reports/第3周_预测建模分析报告.md`](reports/第3周_预测建模分析报告.md) / `.docx` |
| 4 | NL2SQL 接口代码 | [`py/nl2sql.py`](py/nl2sql.py) + [`py/schema_extract.py`](py/schema_extract.py) |
| 5 | AI 智能洞察 / 反思文档 | [`reports/第3周_AI洞察与经营简报.md`](reports/第3周_AI洞察与经营简报.md) + [`docs/第3周_AI应用反思.md`](docs/第3周_AI应用反思.md) |
| 6 | PR / Code Review / Issue 记录 | [`docs/第3周_Git协同记录.md`](docs/第3周_Git协同记录.md)（含 PR [#1](https://github.com/YeHWeh1/ydsh-data-warehouse/pull/1) 真实记录） |

## 六、说明

- **口径先于代码**：本周所有指标先写进《指标定义文档》，再写 SQL。第 2 周的复购率 100% 就是没做这步的后果。
- **模型先建基线**：没有朴素基线的模型评估没有意义，因为你不知道模型到底有没有超过"昨天=今天"。
- **回测必须按时间切分**：随机 KFold 会把未来信息泄露进训练集，评估结果虚高。
- **AI 输出必须回溯校验**：`insight.py` 会把生成文本里的每个数字拿去和事实比对，通过率低于 80% 会标红。
- **Hive 列名不支持中文**：`ads_category_week_pivot` 的品类列用拼音缩写，注释里写中文。
- **Tez 计数器上限 120**：多表 union 的校验查询要拆成两条，否则报 `Too many counters`。
