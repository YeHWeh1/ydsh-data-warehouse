<#
  第 2 周 一键运行（桌面双击即可）
  作用：启动 Docker → 等 Hive 就绪 → 按依赖顺序跑完 10 个脚本 → 打印结果 → 打开看板
  参数：
    -SkipRun   只打开结果看板（秒开，不重跑）
    -ShowOnly  只重新打印终端结果表（不重跑、不开看板）
    -NoPause   结束后不等待回车
  说明：结果表格由脚本自己渲染（按显示宽度对齐，中文占 2 列），
        不直接使用 beeline 的表格输出，避免中文导致边框错位
#>
param(
    [switch]$SkipRun,
    [switch]$ShowOnly,
    [switch]$NoPause
)

$ErrorActionPreference = 'Continue'

# 控制台统一 UTF-8：beeline 输出 UTF-8，不设置会被按 GBK 解码成"寮傚父鍊?"
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$OutputEncoding = [System.Text.Encoding]::UTF8
try { chcp 65001 > $null } catch { }

$Root      = Split-Path -Parent $PSScriptRoot
$Dashboard = Join-Path $Root 'reports\第2周_运行看板.html'
$Jdbc      = 'jdbc:hive2://localhost:10000'
$Ctr       = 'hive-server'
$script:fail = 0

# ---------------- 基础输出 ----------------
function Title($t) {
    Write-Host ""
    Write-Host ('=' * 70) -ForegroundColor DarkCyan
    Write-Host ('  ' + $t) -ForegroundColor White
    Write-Host ('=' * 70) -ForegroundColor DarkCyan
}
function Step($t) { Write-Host ""; Write-Host ('>> ' + $t) -ForegroundColor Cyan }
function Ok($t)   { Write-Host ('   OK   ' + $t) -ForegroundColor Green }
function Bad($t)  { Write-Host ('   失败  ' + $t) -ForegroundColor Red; $script:fail++ }
function Info($t) { Write-Host ('   ' + $t) -ForegroundColor Gray }

# ---------------- 宽度感知的表格渲染 ----------------
# 中文字符在等宽字体里占 2 列，beeline 按字符数补齐会错位，这里按显示宽度算
function Get-Width([string]$s) {
    $w = 0
    foreach ($c in $s.ToCharArray()) {
        $cp = [int]$c
        if (($cp -ge 0x1100 -and $cp -le 0x115F) -or
            ($cp -ge 0x2E80 -and $cp -le 0xA4CF) -or
            ($cp -ge 0xAC00 -and $cp -le 0xD7A3) -or
            ($cp -ge 0xF900 -and $cp -le 0xFAFF) -or
            ($cp -ge 0xFE30 -and $cp -le 0xFE6F) -or
            ($cp -ge 0xFF00 -and $cp -le 0xFF60) -or
            ($cp -ge 0xFFE0 -and $cp -le 0xFFE6)) { $w += 2 } else { $w += 1 }
    }
    return $w
}

function Trim-Width([string]$s, [int]$max) {
    if ((Get-Width $s) -le $max) { return $s }
    $w = 0; $out = ''
    foreach ($c in $s.ToCharArray()) {
        $cw = if ((Get-Width ([string]$c)) -eq 2) { 2 } else { 1 }
        if ($w + $cw -gt ($max - 2)) { break }
        $out += $c; $w += $cw
    }
    return ($out + '..')
}

function Write-Row([string[]]$cells, [int[]]$widths, [switch]$Header) {
    $line = '|'
    for ($c = 0; $c -lt $cells.Count; $c++) {
        $v = [string]$cells[$c]
        $pad = $widths[$c] - (Get-Width $v)
        $isNum = (-not $Header) -and ($v -match '^-?\d+(\.\d+)?$')
        if ($isNum) { $line += ' ' + (' ' * $pad) + $v + ' |' }
        else        { $line += ' ' + $v + (' ' * $pad) + ' |' }
    }
    Write-Host $line
}

function Render-Table([string[]]$rawLines, [int]$maxCol = 60) {
    $rows = @()
    foreach ($l in $rawLines) {
        if ($null -eq $l) { continue }
        $cells = @($l -split '\|')
        # 跳过空行 / 全为 NULL 的行（describe formatted 里的占位行）
        $meaningful = @($cells | Where-Object { $_.Trim() -ne '' -and $_.Trim() -ne 'NULL' })
        if ($meaningful.Count -eq 0) { continue }
        $rows += , $cells
    }
    if ($rows.Count -eq 0) { Info '(无数据)'; return }

    $cols = 0
    foreach ($r in $rows) { if ($r.Count -gt $cols) { $cols = $r.Count } }

    $norm = @()
    foreach ($r in $rows) {
        $cells = @($r)
        while ($cells.Count -lt $cols) { $cells += '' }
        $t = @()
        foreach ($c in $cells) { $t += (Trim-Width $c $maxCol) }
        $norm += , $t
    }

    $widths = @()
    for ($c = 0; $c -lt $cols; $c++) {
        $mx = 0
        foreach ($r in $norm) { $x = Get-Width $r[$c]; if ($x -gt $mx) { $mx = $x } }
        $widths += $mx
    }

    $sep = '+' + (($widths | ForEach-Object { '-' * ($_ + 2) }) -join '+') + '+'
    Write-Host $sep
    for ($i = 0; $i -lt $norm.Count; $i++) {
        Write-Row $norm[$i] $widths -Header:($i -eq 0)
        if ($i -eq 0) { Write-Host $sep }
    }
    Write-Host $sep
}

# ---------------- 查询封装 ----------------
function Invoke-Beeline([string]$sql) {
    $out = docker exec $Ctr beeline -u $Jdbc --silent=true --outputformat=dsv -e $sql 2>&1 | Out-String
    return ($out -split "`r?`n")
}

function RunSql([string]$sql, [int]$maxCol = 60) {
    $lines = Invoke-Beeline $sql
    $err = $lines | Where-Object { $_ -match '^Error: |^FAILED: ' } | Select-Object -First 2
    if ($err) { $err | ForEach-Object { Bad ($_.Trim()) }; return }
    $data = @($lines | Where-Object {
        $t = $_.Trim()
        $t -ne '' -and
        $t -notmatch 'SLF4J|log4j|Binder\.class|jar:file:' -and
        $t -notmatch '^\[WARN\]' -and
        $t -notmatch '^No such file' -and
        $t -notmatch '^docker :' -and
        $t -notmatch '^(所在位置|\+ |    \+ |CategoryInfo|FullyQualifiedErrorId|RemoteException)'
    })
    Render-Table $data $maxCol
}

# 从 describe formatted 里提取关键行，拼成 "Key: Value" / "Key: [value]"
function Show-TableDetail([string]$table, [string[]]$keys) {
    $lines = Invoke-Beeline ("describe formatted " + $table + ";")
    foreach ($l in $lines) {
        if ($l -notmatch '\|') { continue }
        $cells = @(($l -split '\|') | ForEach-Object { $_.Trim() } | Where-Object {
            $_ -ne '' -and $_ -ne 'NULL'
        })
        if ($cells.Count -lt 2) { continue }
        $k = $cells[0]
        $v = ($cells[1..($cells.Count - 1)] -join ' ')
        $joined = if ($k.EndsWith(':')) { $k + ' ' + $v } else { $k + ': ' + $v }
        foreach ($want in $keys) {
            if ($joined -match ('^' + [regex]::Escape($want))) {
                Write-Host ('   ' + $joined)
            }
        }
    }
}

# ---------------- 结果展示 ----------------
function Show-Results {
    Step '结果展示'

    Write-Host ''
    Write-Host '  [A] 事实表行数（应为 15000 / 37549 / 60000）' -ForegroundColor Yellow
    RunSql "select 'dwd_order' as tbl, count(*) as cnt from ydsh_dwd.dwd_order
union all select 'dwd_order_item', count(*) from ydsh_dwd.dwd_order_item
union all select 'dwd_user_behavior', count(*) from ydsh_dwd.dwd_user_behavior"

    Write-Host ''
    Write-Host '  [B] 订单事实表结构（中文注释）' -ForegroundColor Yellow
    RunSql 'describe ydsh_dwd.dwd_order;' 52

    Write-Host ''
    Write-Host '  [C] 分区与分桶' -ForegroundColor Yellow
    RunSql 'select count(*) as partitions from (select order_date from ydsh_dwd.dwd_order group by order_date) t;'
    Show-TableDetail 'ydsh_dwd.dwd_user_behavior' @('Num Buckets', 'Bucket Columns', 'numFiles', 'numRows')

    Write-Host ''
    Write-Host '  [D] 数据质量修复率（五类脏数据，22 项）' -ForegroundColor Yellow
    RunSql "select metric_class, metric_name, before_cnt, after_cnt,
       case when before_cnt = 0 then null
            else round((before_cnt - after_cnt) * 100.0 / before_cnt, 1) end as fix_rate_pct
from ydsh_dwd.qc_metric order by metric_class, metric_name"

    Write-Host ''
    Write-Host '  [E] 核心指标' -ForegroundColor Yellow
    RunSql "select kpi_name, cast(kpi_value as decimal(18,2)) as kpi_value, unit
from ydsh_ads.ads_kpi_overview order by kpi_name"
    RunSql 'select rank_no, category, gmv, gmv_share_pct from ydsh_ads.ads_category_rank order by rank_no'
    RunSql 'select rank_no, province, gmv, share_pct from ydsh_ads.ads_region_rank order by rank_no limit 5'
}

# ---------------- 打开看板 ----------------
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

if ($SkipRun) {
    Step '跳过数据加工，直接打开结果看板'
    Open-Dashboard
    if (-not $NoPause) { Write-Host ''; Read-Host '按回车退出' }
    exit
}

if ($ShowOnly) {
    Show-Results
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
        Ok (('{0}  {1}' -f $s.n, $s.desc).PadRight(44) + ('({0,3:N0}s)' -f $sw.Elapsed.TotalSeconds))
    }
}
$total.Stop()
Write-Host ''
Info ('10 个脚本总耗时：{0:N0} 秒' -f $total.Elapsed.TotalSeconds)

# ---------- 5. 结果展示 ----------
Show-Results

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
