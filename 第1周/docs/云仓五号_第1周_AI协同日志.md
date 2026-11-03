# 云仓五号 · 第 1 周 AI 协同日志

- 团队：云仓五号　成员：刘祎炫、张业彬、闫洪伟、柴传兴、邓克超　填写日期：2026-10-30

---

## 一、使用的 AI 工具 / 模型

- 工具名称：DeepSeek Harness（桌面端，接入 deepseek 系列模型）　模型版本：deepseek-flash
- 辅助：Windows PowerShell 本地命令行、Docker Desktop 图形界面（用于结果核验）
- 说明：本周所有 AI 产出均在本地环境实际执行验证，未经执行验证的内容一律未采用。

---

## 二、AI 协同记录表

| 序号 | 提问 / 任务描述 | AI 产出摘要 | 人工修改与验证 | 是否采纳 |
|------|----------------|-------------|----------------|----------|
| 1 | 请分析《第 1 周任务书》和《部署操作手册》，指出照着做会踩的坑 | 指出手册中 docker-compose 缺少 `container_name`、未配置 `DB_DRIVER`、无数据卷挂载、缺 `schematool` 初始化、镜像缺 MySQL 驱动等 5 处缺陷 | 按 AI 建议重写 compose 后实际 `up -d`，metastore 仍报 `ClassNotFoundException`，据此追加第 5 条（挂载 JDBC 驱动）才通过 | 是 |
| 2 | 帮我写一个能跑通 Hive + MySQL + Metastore 的 docker-compose | 给出三服务编排，含 healthcheck、depends_on 条件、SERVICE_OPTS 连接串 | 人工核对端口：本机 MySQL80 已占用 3306，改为 `3307:3306`；验证 `docker ps` 三容器均 Up | 是（修改后采纳） |
| 3 | `docker compose up` 报 `failed to resolve reference ... dial tcp 108.160.166.61:443` 怎么解决 | 判断为 Docker Hub 被污染，建议配 `registry-mirrors` 与代理 | 实测 daocloud、1ms、1panel 等镜像站只代理 manifest，层文件 307 跳转到境外 CDN 仍失败；最终改用 Clash 本地代理（7897）直连官方仓库，速度由 0.53 MB/s 提升到约 20 MB/s | 部分采纳（镜像站方案被实测否定） |
| 4 | HiveServer2 容器 Up 但 beeline 报 Connection refused | 指导用 `kill -3` 导出线程栈，发现主线程卡在 `HiveServer2.startHiveServer2` 重试 Metastore；进一步查 `/proc/net/tcp` 发现 Metastore 只绑 IPv6 | 人工确认容器间走 IPv4（172.18.x），加 `-Djava.net.preferIPv4Stack=true` 后恢复。此结论与 AI 初判（网络不通）不同，以实测为准 | 是（结论经修正） |
| 5 | 容器重建后查询报 `Input path does not exist: /opt/hive/data/warehouse/...` | 判断数据目录未挂卷，属容器可写层，重建即丢 | 人工加 `hive-data` 命名卷，重新执行建表与导入脚本，6 张表行数复核一致（114,809 行） | 是 |
| 6 | 写用户表缺失值处理 SQL：age 用均值填充、email 保留并标记 | 给出 CTAS 写法，用 `cross join` 子查询取 1~120 区间均值（41），空值与越界值统一替换，并加 4 个缺失标记列 | 人工执行后发现任务书要求的是「各表缺失字段」，我们漏了 gender(84)、member_level(14)；补加两个标记列后重跑，`sum(gender_missing)=84` 与扫描一致 | 是（人工补漏） |
| 7 | 订单表去重怎么写 | 给出 `ROW_NUMBER() OVER(PARTITION BY order_id ORDER BY order_time DESC, pay_amount DESC)` 取 `rn=1` | 人工验证：15,600 → 15,000，且 `count(*)` 与 `count(distinct order_id)` 相等；与扫描阶段发现的 600 条重复完全吻合 | 是 |
| 8 | 生成数据质量预评估表（Excel）与一键运行脚本 | 产出含 7 个 sheet 的 xlsx；产出 PowerShell 脚本（启动→等待就绪→执行清洗→打印结果） | 脚本首版中文别名导致 Hive 报 `ParseException`，改为英文别名；bat 内含中文导致 cmd 解析崩溃闪退，改为纯英文 bat 后正常 | 是（经两轮修正） |
| 9 | 帮我把项目提交到 GitHub，注意不要上传密钥 | 生成 SSH 密钥（配 443 端口绕开封锁）、写 `.gitignore`、扫描敏感信息、提交并推送 | 人工核对 `git ls-files` 15 个文件、确认无 token / `.env` / CSV；推送后核对远程 commit 哈希一致 | 是 |

> 说明：以上 9 条均为本周真实发生的协作过程。第 3、4、6、8 条是 AI 方案被**实测否定或补充**的典型例子，说明 AI 产出必须落地验证。

---

## 三、AI 产出成果清单

**由 AI 生成（经人工验证后采用）**

- `docker-compose.yml` 初版编排与三处环境参数修正（端口映射、IPv4 协议栈、JVM 参数）
- `sql/ods/create_ods.sql`、`sql/ods/load_ods.sql`（6 张表 DDL 与导入校验）
- `sql/clean/` 下 6 个脚本（质量扫描、缺失值、日期补全、去重、异常标记）
- `reports/数据质量预评估表.xlsx`（7 个 sheet 的结构与内容）
- `scripts/run-all.ps1` 一键运行脚本骨架
- `docs/环境搭建笔记.md`、`docs/答辩手册.md` 初稿

**由人工编写 / 提供**

- 团队信息、角色分工、队名与口号（`README.md` 第一部分）
- 业务判断：`age` 用均值而非中位数、`email` 保留空值不造数据、异常值只标记不改值
- 所有 SQL 的最终执行与结果核对（行数、空值数、重复数逐项比对）
- 环境排障的实际操作：启用 WSL 功能、迁移 Docker 数据盘、配置代理、抓线程栈

**人机协作完成**

- 环境搭建：AI 给排查思路，人工执行并反馈真实报错，迭代 3 轮后稳定
- 清洗方案：AI 起草 SQL，人工补漏字段、修正标记逻辑（`behavior_type_variant` 首版 0/1 写反）
- 文档：AI 起草结构，人工补充真实报错原文与验证数据

---

## 四、问题与解决

**遇到的问题**

1. `wsl --install` 报「错误的网关 (502)」，WSL 相关服务根本不存在
2. Docker 镜像拉取卡死（Hub 被污染，镜像站假代理）
3. 官方 `apache/hive:4.0.0` 镜像不含 MySQL JDBC 驱动，metastore 启动即退出
4. Metastore 只监听 IPv6，HiveServer2 卡在重试
5. 容器重建导致数据文件丢失
6. HiveServer2 的 JVM 在 GC 阶段 SIGSEGV，反复重启

**AI 是否有效解决**

大部分有效，但有三次判断需要人工纠正：

- 镜像站方案：AI 认为配了 mirror 即可，实测 4 个镜像站只代理清单不代理层，必须走代理
- 连接失败：AI 初判为网络不通，实际是 IPv6 绑定问题
- 数据丢失：AI 未提前提示「不挂卷就会丢」，是人工踩坑后反推的

**最终如何解决**

- 502：手动启用 DISM 功能 + 单独安装 WSL2 内核（16 MB）绕开 Store 下载
- 拉取：去掉 mirror，Docker Desktop 配置 HTTP 代理指向本地 7897
- 驱动：下载 `mysql-connector-j-8.4.0.jar` 挂载到 `/opt/hive/lib/`
- IPv6：两个 Hive 容器加 `-Djava.net.preferIPv4Stack=true`
- 数据丢失：加命名卷 `hive-data`，并把建表/导入/清洗脚本做成可重复执行
- JVM 崩溃：去掉整块 `/tmp` 的 tmpfs（只给 pid 目录挂），JVM 改 G1 + 关闭压缩指针

---

## 五、反思与总结

**AI 协同带来的价值**

- 排障提速：线程栈分析、`/proc/net` 查看、GC 崩溃定位这些不熟悉的排查路径，AI 能直接给出方向，节省大量试错
- 代码量产：6 个 SQL 脚本、Excel 报表、一键脚本的骨架在很短时间内完成，人工专注在验证与业务判断上
- 文档沉淀：把散落的报错整理成结构化的《环境搭建笔记》，复盘成本大幅降低

**AI 的局限与风险**

- **环境感知缺失**：AI 不知道本机装了 MySQL80、不知道 `/tmp` 被挂成 tmpfs，给出的配置必须落地验证
- **会给出「看起来对但跑不通」的方案**：镜像加速站即典型，配置语法全对，但实际被 307 重定向卡死
- **容易漏需求**：第一版清洗只覆盖任务书举例的 3 个字段，漏了实际存在的 gender、member_level
- **依赖风险**：如果全盘接受不验证，最终交付的可能是「能编译不能运行」的代码

**本次协作中能力的提升**

- 学会用「现象 → 日志 → 线程栈 → 系统文件」的固定顺序排障，而不是凭感觉改配置
- 理解了 Docker 的容器可写层与命名卷的区别，知道哪些数据必须持久化
- 掌握了 Hive CTAS、窗口函数、聚合子查询的实际写法
- 建立了「AI 给方案 → 本地执行 → 对比预期 → 修正」的验证习惯

**对 AI 协同的改进建议**

- 提问时把**真实报错原文**和**已尝试的方案**一起给出，避免 AI 重复推荐无效路径
- 让 AI 输出「可验证的判据」（如"应该看到 15600 行"），而不是只给命令
- 对涉及删除、重建的操作，先确认数据是否已持久化，再执行
- 保留 AI 的错误判断记录（本日志第 3、4 条），这些比成功案例更能说明协作的真实性
