<#
  第 2 周 一键运行（桌面双击即可）
  作用：启动 Docker → 等 Hive 就绪 → 按依赖顺序跑完 10 个脚本 → 自动打开结果看板
  参数：
    -SkipRun   不重跑数据，只打开已有的结果看板（秒开）
    -NoPause   结束后不等待回车
#>
param(
    [switch]$SkipRun,
    [switch]$NoPause
)

$ErrorActionPreference = 'Continue'

# 控制台统一 UTF-8：beeline 输出 UTF-8，不设置会被按 GBK 解码成"寮傚父鍊?"
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$OutputEncoding = [System.Text.Encoding]::UTF8
try { chcp 65001 > $null } catch { }

$Root      = Split-Path -Parent $PSScriptRoot      # 项目根目录（由脚本位置推出来，不写死）
$Dashboard = Join-Path $Root 'reports\第2周_运行看板.html'
$Jdbc      = 'jdbc:hive2://localhost:10000'
$Ctr       = 'hive-server'
$script:fail = 0

function Title($t) {
    Write-Host ""
    Write-Host ('=' * 66) -ForegroundColor DarkCyan
    Write-Host ('  ' + $t) -ForegroundColor White
    Write-Host ('=' * 66) -ForegroundColor DarkCyan
}
function Step($t) { Write-Host ""; Write-Host ('>> ' + $t) -ForegroundColor Cyan }
function Ok($t)   { Write-Host ('   OK   ' + $t) -ForegroundColor Green }
function Bad($t)  { Write-Host ('   失败  ' + $t) -ForegroundColor Red; $script:fail++ }
function Info($t) { Write-Host ('   ' + $t) -ForegroundColor Gray }

# 只打印结果表格，过滤 beeline 日志噪音
function RunSql([string]$sql) {
    $out = docker exec $Ctr beeline -u $Jdbc -e $sql 2>&1 | Out-String
    $lines = $out -split "`r?`n"
    $lines | Where-Object { $_ -match '^\s*\|' -or $_ -match '^\s*\+--' } |
        ForEach-Object { Write-Host $_ }
    $err = $lines | Where-Object { $_ -match 'Error: |FAILED: ' } | Select-Object -First 2
    if ($err) { $err | ForEach-Object { Bad $_ } }
}

function Open-Dashboard {
    if (Test-Path $Dashboard) {
        Write-Host ''
        Write-Host '   正在打开结果看板 ...' -ForegroundColor Yellow
        Start-Process $Dashboard
        Ok ('看板已打开：' + $Dashboard)
    } else {
        Bad ('看板文件不存在：' + $Dashboard)
        Info '先完整跑一次（不加 -SkipRun），或检查 reports 目录'
    }
}

# ============================================================
Title '云购生活数仓 · 第2周 一键运行'
Write-Host ('   项目目录：' + $Root) -ForegroundColor Gray

# ---------- 只看看板 ----------
if ($SkipRun) {
    Step '跳过数据加工，直接打开结果看板'
    Open-Dashboard
    if (-not $NoPause) { Write-Host ''; Read-Host '按回车退出' }
    exit
}

# ---------- 1. Docker ----------
Step '1/6 检查 Docker'
$dockerOk = $false
for ($i = 1; $i -le 8; $i++) {
    docker info *> $null
    if ($LASTEXITCODE -eq 0) { $dockerOk = $true; break }
    if ($i -eq 1) {
        Write-Host '   Docker 未运行，尝试启动 Docker Desktop ...' -ForegroundColor Yellow
        $exe = "$env:LOCALAPPDATA\Programs\DockerDesktop\Docker Desktop.exe"
        if (Test-Path $exe) { Start-Process $exe } else { Info '未找到 Docker Desktop，请手动打开' }
    }
    Start-Sleep 15
    Write-Host ('   等待中 (' + ($i * 15) + ' 秒) ...')
}
if (-not $dockerOk) {
    Bad 'Docker 无法启动，请手动打开 Docker Desktop 后重试'
    if (-not $NoPause) { Read-Host '按回车退出' }
    exit 1
}
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
if (-not $ready) {
    Bad 'HiveServer2 未就绪，请查看：docker logs --tail 30 hive-server'
    if (-not $NoPause) { Read-Host '按回车退出' }
    exit 1
}
Ok 'HiveServer2 就绪'

# ---------- 4. 按顺序执行四层加工 ----------
Step '4/6 执行第2周脚本（10 个，按依赖顺序）'
$scripts = @(
    @{ n = '00'; file = 'sql/dwd/00_ddl.sql';                desc = 'DWD/ADS 建库建表' },
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
$total = [Diagnostics.Stopwatch]::StartNew()
foreach ($s in $scripts) {
    $src = Join-Path $Root $s.file
    if (-not (Test-Path $src)) { Bad ('找不到 ' + $s.file); continue }
    $sw = [Diagnostics.Stopwatch]::StartNew()
    docker cp $src "$Ctr`:/opt/hive/data/run.sql" | Out-Null
    $out = docker exec $Ctr beeline -u $Jdbc -f '/opt/hive/data/run.sql' 2>&1 | Out-String
    $sw.Stop()
    if ($out -match 'Error: |FAILED: ') {
        Bad ($s.n + ' ' + $s.desc)
        ($out -split "`r?`n" | Where-Object { $_ -match 'Error: |FAILED: ' } | Select-Object -First 2) |
            ForEach-Object { Write-Host ('      ' + $_) }
    } else {
        Ok (('{0}  {1}' -f $s.n, $s.desc).PadRight(46) + ('({0,3:N0}s)' -f $sw.Elapsed.TotalSeconds))
    }
}
$total.Stop()
Write-Host ''
Info ('10 个脚本总耗时：{0:N0} 秒' -f $total.Elapsed.TotalSeconds)

# ---------- 5. 结果展示 ----------
Step '5/6 结果展示'

Write-Host ''
Write-Host '  [A] 事实表行数（应为 15000 / 37549 / 60000）' -ForegroundColor Yellow
RunSql "select 'dwd_order' as tbl, count(*) as cnt from ydsh_dwd.dwd_order
union all select 'dwd_order_item', count(*) from ydsh_dwd.dwd_order_item
union all select 'dwd_user_behavior', count(*) from ydsh_dwd.dwd_user_behavior"

Write-Host ''
Write-Host '  [B] 分区数（应为 366）、分桶数（应为 8）、表结构' -ForegroundColor Yellow
RunSql 'select count(*) as partitions from (select order_date from ydsh_dwd.dwd_order group by order_date) t;'
RunSql 'describe ydsh_dwd.dwd_order;'
RunSql 'describe ydsh_dwd.dwd_user_behavior;'
$storage = docker exec $Ctr beeline -u $Jdbc -e 'describe formatted ydsh_dwd.dwd_user_behavior;' 2>&1 | Out-String
($storage -split "`r?`n") |
    Where-Object { $_ -match 'Num Buckets|Bucket Columns|numFiles|numRows|Location' } |
    ForEach-Object { Write-Host ('   ' + $_.Trim()) }

Write-Host ''
Write-Host '  [C] 数据质量修复率（五类脏数据，22 项）' -ForegroundColor Yellow
RunSql "select metric_class, metric_name, before_cnt, after_cnt,
       case when before_cnt = 0 then null
            else round((before_cnt - after_cnt) * 100.0 / before_cnt, 1) end as fix_rate_pct
from ydsh_dwd.qc_metric order by metric_class, metric_name"

Write-Host ''
Write-Host '  [D] 核心指标' -ForegroundColor Yellow
RunSql "select kpi_name, cast(kpi_value as decimal(18,2)) as kpi_value, unit
from ydsh_ads.ads_kpi_overview order by kpi_name"
RunSql 'select rank_no, category, gmv, gmv_share_pct from ydsh_ads.ads_category_rank order by rank_no'
RunSql 'select rank_no, province, gmv, share_pct from ydsh_ads.ads_region_rank order by rank_no limit 5'

# ---------- 6. 完成 ----------
Title '全部完成'
Write-Host '  四层：ydsh_ods → ydsh_dwd → ydsh_dws → ydsh_ads' -ForegroundColor Green
Write-Host '  维度 7 张 / 事实 3 张 / 汇总 5 张 / 指标 5 张' -ForegroundColor Green
if ($script:fail -eq 0) {
    Write-Host '  执行结果：全部成功' -ForegroundColor Green
} else {
    Write-Host ('  执行结果：有 ' + $script:fail + ' 处失败，请查看上方红色提示') -ForegroundColor Red
}
Write-Host ''
Write-Host '  结果文件：' -ForegroundColor Gray
Write-Host ('    ' + $Dashboard) -ForegroundColor Gray
Write-Host ('    ' + (Join-Path $Root 'reports\第2周_数据质量对比报告.md')) -ForegroundColor Gray
Write-Host ('    ' + (Join-Path $Root 'reports\第2周_探索性分析简报.md')) -ForegroundColor Gray

Open-Dashboard

if (-not $NoPause) { Write-Host ''; Read-Host '按回车退出' }
