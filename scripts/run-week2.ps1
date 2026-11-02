# ============================================================
#  第2周 一键运行脚本
#  作用：启动环境 → 等待就绪 → 按依赖顺序执行四层数仓加工 → 展示关键结果
#  用法：双击 "一键运行_第2周.bat"，或右键此脚本 -> 使用 PowerShell 运行
#  顺序：00_ddl → 10 枚举 → 11 异常/格式 → 12 拆分合并 → 01 维度 → 02 事实
#        → 13 质量对比 → 20 DWS → 30 ADS → 40 探索性分析
# ============================================================

$ErrorActionPreference = 'Continue'

$Root = 'E:\dsh project'
$Jdbc = 'jdbc:hive2://localhost:10000'
$Ctr  = 'hive-server'

function Title($t) {
    Write-Host ""
    Write-Host ('=' * 66) -ForegroundColor DarkCyan
    Write-Host ('  ' + $t) -ForegroundColor White
    Write-Host ('=' * 66) -ForegroundColor DarkCyan
}
function Step($t) { Write-Host ""; Write-Host ('>> ' + $t) -ForegroundColor Cyan }
function Ok($t)   { Write-Host ('   OK   ' + $t) -ForegroundColor Green }
function Bad($t)  { Write-Host ('   失败  ' + $t) -ForegroundColor Red }

# 只打印结果表格，过滤 beeline 日志噪音
function RunSql([string]$sql) {
    $out = docker exec $Ctr beeline -u $Jdbc -e $sql 2>&1 | Out-String
    $lines = $out -split "`r?`n"
    $lines | Where-Object { $_ -match '^\s*\|' -or $_ -match '^\s*\+--' } |
        ForEach-Object { Write-Host $_ }
    $err = $lines | Where-Object { $_ -match 'Error: |FAILED: ' } | Select-Object -First 2
    if ($err) { $err | ForEach-Object { Bad $_ } }
}

Title '云购生活数仓 · 第2周 一键运行'

# ---------- 1. Docker ----------
Step '1/6 检查 Docker'
$dockerOk = $false
for ($i = 1; $i -le 8; $i++) {
    docker info *> $null
    if ($LASTEXITCODE -eq 0) { $dockerOk = $true; break }
    if ($i -eq 1) {
        Write-Host '   Docker 未运行，尝试启动 Docker Desktop ...'
        $exe = "$env:LOCALAPPDATA\Programs\DockerDesktop\Docker Desktop.exe"
        if (Test-Path $exe) { Start-Process $exe }
    }
    Start-Sleep 15
    Write-Host ('   等待中 (' + ($i * 15) + ' 秒) ...')
}
if (-not $dockerOk) { Bad 'Docker 无法启动，请手动打开 Docker Desktop 后重试'; Read-Host '按回车退出'; exit 1 }
Ok 'Docker 就绪'

# ---------- 2. 启动容器 ----------
Step '2/6 启动容器'
Set-Location $Root
docker compose up -d 2>&1 | Out-Null
docker ps --format '   {{.Names}}  {{.Status}}'
Ok '容器已启动'

# ---------- 3. 等待 Hive ----------
Step '3/6 等待 HiveServer2 就绪（最多 3 分钟）'
$ready = $false
for ($round = 1; $round -le 2; $round++) {
    for ($i = 1; $i -le 18; $i++) {
        $o = docker exec $Ctr beeline -u $Jdbc -e 'select 1;' 2>&1 | Out-String
        if ($o -match '\|\s+1\s+\|') { $ready = $true; break }
        Start-Sleep 5
    }
    if ($ready) { break }
    if ($round -eq 1) {
        Write-Host '   仍未就绪，重建 hive-server 再等一轮 ...' -ForegroundColor Yellow
        docker compose up -d --force-recreate hive-server 2>&1 | Out-Null
        Start-Sleep 10
    }
}
if (-not $ready) { Bad 'HiveServer2 未就绪，请查看：docker logs --tail 30 hive-server'; Read-Host '按回车退出'; exit 1 }
Ok 'HiveServer2 就绪'

# ---------- 4. 按顺序执行四层加工 ----------
Step '4/6 执行第2周脚本（10 个，按依赖顺序）'
$scripts = @(
    @{ n = '00'; file = 'sql/dwd/00_ddl.sql';               desc = 'DWD/ADS 建库建表' },
    @{ n = '10'; file = 'sql/clean/10_enum_standardize.sql'; desc = '枚举字段标准化' },
    @{ n = '11'; file = 'sql/clean/11_anomaly_format.sql';   desc = '异常值与格式标准化' },
    @{ n = '12'; file = 'sql/clean/12_split_merge.sql';      desc = '字段拆分合并 + 去重' },
    @{ n = '01'; file = 'sql/dwd/01_load_dim.sql';           desc = '维度表加载' },
    @{ n = '02'; file = 'sql/dwd/02_load_fact.sql';          desc = '事实表加载（分区/分桶）' },
    @{ n = '13'; file = 'sql/clean/13_quality_compare.sql';  desc = '质量对比报告取数' },
    @{ n = '20'; file = 'sql/dws/20_dws.sql';                desc = 'DWS 汇总层' },
    @{ n = '30'; file = 'sql/ads/30_ads.sql';                desc = 'ADS 应用层' },
    @{ n = '40'; file = 'sql/analysis/40_eda.sql';           desc = '探索性分析' }
)
foreach ($s in $scripts) {
    $src = Join-Path $Root $s.file
    if (-not (Test-Path $src)) { Bad ('找不到 ' + $s.file); continue }
    docker cp $src "$Ctr`:/opt/hive/data/run.sql" | Out-Null
    $out = docker exec $Ctr beeline -u $Jdbc -f '/opt/hive/data/run.sql' 2>&1 | Out-String
    if ($out -match 'Error: |FAILED: ') {
        Bad ($s.n + ' ' + $s.desc)
        ($out -split "`r?`n" | Where-Object { $_ -match 'Error: |FAILED: ' } | Select-Object -First 2) |
            ForEach-Object { Write-Host ('      ' + $_) }
    } else {
        Ok ($s.n + '  ' + $s.desc)
    }
}

# ---------- 5. 结果展示 ----------
Step '5/6 结果展示'

Write-Host ''
Write-Host '  [A] 四层表清单' -ForegroundColor Yellow
RunSql 'show tables in ydsh_dwd;'
RunSql 'show tables in ydsh_dws;'
RunSql 'show tables in ydsh_ads;'

Write-Host ''
Write-Host '  [B] 事实表行数（应为 15000 / 37549 / 60000）' -ForegroundColor Yellow
RunSql "select 'dwd_order' as tbl, count(*) as cnt from ydsh_dwd.dwd_order
union all select 'dwd_order_item', count(*) from ydsh_dwd.dwd_order_item
union all select 'dwd_user_behavior', count(*) from ydsh_dwd.dwd_user_behavior"

Write-Host ''
Write-Host '  [C] 分区与分桶' -ForegroundColor Yellow
RunSql 'select count(*) as partitions from (select order_date from ydsh_dwd.dwd_order group by order_date) t;'
RunSql 'describe formatted ydsh_dwd.dwd_user_behavior;'

Write-Host ''
Write-Host '  [D] 数据质量修复率（五类脏数据）' -ForegroundColor Yellow
RunSql "select metric_class, metric_name, before_cnt, after_cnt,
       case when before_cnt = 0 then null
            else round((before_cnt - after_cnt) * 100.0 / before_cnt, 1) end as fix_rate_pct
from ydsh_dwd.qc_metric order by metric_class, metric_name"

Write-Host ''
Write-Host '  [E] 核心指标' -ForegroundColor Yellow
RunSql 'select kpi_name, kpi_value, unit from ydsh_ads.ads_kpi_overview'
RunSql 'select rank_no, category, gmv, gmv_share_pct from ydsh_ads.ads_category_rank order by rank_no'
RunSql 'select rank_no, province, gmv, share_pct from ydsh_ads.ads_region_rank order by rank_no limit 5'

# ---------- 6. 完成 ----------
Title '全部完成'
Write-Host '  四层：ydsh_ods → ydsh_dwd → ydsh_dws → ydsh_ads' -ForegroundColor Green
Write-Host '  维度 7 张 / 事实 3 张 / 汇总 5 张 / 指标 5 张' -ForegroundColor Green
Write-Host '  报告：reports/第2周_数据质量对比报告.md、reports/第2周_探索性分析简报.md' -ForegroundColor Green
Write-Host ''
Write-Host '  手动进 Hive 看看：' -ForegroundColor Gray
Write-Host '  docker exec -it hive-server beeline -u "jdbc:hive2://localhost:10000"' -ForegroundColor Gray
Write-Host ''
Read-Host '按回车退出'
