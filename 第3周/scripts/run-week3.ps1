<#
  第 3 周 一键运行（桌面双击即可）
  作用：启动 Docker → 等 Hive → 跑 SQL（DWS/ADS 扩展）→ 跑 Python（预测/NL2SQL/洞察）
        → 生成报告与 docx → 打开看板

  参数：
    -SkipRun   只打开结果看板（秒开，不重跑）
    -ShowOnly  只重新打印终端结果表（不重跑、不开看板）
    -NoLLM     强制离线模式（不调用大模型 API）
    -NoPause   结束后不等待回车

  说明：结果表格由脚本自己渲染（按显示宽度对齐，中文占 2 列），
        不直接使用 beeline 的表格输出，避免中文导致边框错位。
#>
param(
    [switch]$SkipRun,
    [switch]$ShowOnly,
    [switch]$NoLLM,
    [switch]$NoPause
)

$ErrorActionPreference = 'Continue'

# 控制台统一 UTF-8：beeline / Python 输出 UTF-8，不设置会被按 GBK 解码成乱码
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$OutputEncoding = [System.Text.Encoding]::UTF8
$env:PYTHONIOENCODING = 'utf-8'
try { chcp 65001 > $null } catch { }

$Root      = Split-Path -Parent $PSScriptRoot          # 第3周 目录
$ProjRoot  = Split-Path -Parent $Root                  # 项目根目录
$Compose   = Join-Path $ProjRoot '第1周\docker-compose.yml'
$Week1Dir  = Join-Path $ProjRoot '第1周'
$Dashboard = Join-Path $Root 'reports\第3周_运行看板.html'
$Jdbc      = 'jdbc:hive2://localhost:10000'
$Ctr       = 'hive-server'
$script:fail = 0

# Python 定位：本项目依赖 pandas/sklearn/pyhive，系统 python 往往没装。
# 优先级：$env:PYTHON → 随项目记录的运行时 → PATH 上的 python（需能 import pandas）
function Resolve-Python {
    $cands = @()

    if ($env:PYTHON) { $cands += $env:PYTHON }

    # 1) 本机 DSH 运行时（前几周一直在用，依赖齐全）
    $cands += 'C:\Users\wei\.dsh\dsh-runtimes\dsh-primary-runtime\dependencies\python\python.exe'

    # 2) PATH 上的 python / python3
    foreach ($n in @('python', 'python3')) {
        $c = Get-Command $n -ErrorAction SilentlyContinue
        if ($c) { $cands += $c.Source }
    }

    # 3) py 启动器（注意：本机 py 是 3.8，没装依赖，放最后）
    $pyL = Get-Command 'py' -ErrorAction SilentlyContinue
    if ($pyL) { $cands += $pyL.Source }

    foreach ($c in $cands) {
        if (-not $c) { continue }
        if ($c -notmatch '\.exe$') {
            # py 启动器要带 -3
            $out = & $c -3 -c "import pandas,sklearn,pyhive" 2>&1
            if ($LASTEXITCODE -eq 0) { return @($c, '-3') }
            continue
        }
        if (-not (Test-Path $c)) { continue }
        & $c -c "import pandas,sklearn,pyhive" 2>&1 | Out-Null
        if ($LASTEXITCODE -eq 0) { return @($c, '') }
    }
    return @('', '')
}

$pyRes   = Resolve-Python
$PyExe   = $pyRes[0]
$PyLead  = $pyRes[1]

# 统一的调用方式：& $PyExe @PyLead <script> ...
function Invoke-Py {
    param([Parameter(ValueFromRemainingArguments = $true)][string[]]$PyArgs)
    if ($PyLead) { & $PyExe $PyLead @PyArgs } else { & $PyExe @PyArgs }
}

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

# ---------------- 结果展示 ----------------
function Show-Results {
    Step '结果展示'

    Write-Host ''
    Write-Host '  [A] 第3周 DWS 扩展表行数' -ForegroundColor Yellow
    RunSql "select 'dws_sales_day_ext' as tbl, count(*) as cnt from ydsh_dws.dws_sales_day_ext
union all select 'dws_sales_week', count(*) from ydsh_dws.dws_sales_week
union all select 'dws_sales_month', count(*) from ydsh_dws.dws_sales_month
union all select 'dws_user_rfm', count(*) from ydsh_dws.dws_user_rfm"

    Write-Host ''
    Write-Host '  [B] 第3周 ADS 扩展表行数' -ForegroundColor Yellow
    RunSql "select 'ads_sales_daily_report' as tbl, count(*) as cnt from ydsh_ads.ads_sales_daily_report
union all select 'ads_category_topn', count(*) from ydsh_ads.ads_category_topn
union all select 'ads_anomaly_daily', count(*) from ydsh_ads.ads_anomaly_daily
union all select 'ads_user_rfm_segment', count(*) from ydsh_ads.ads_user_rfm_segment"

    Write-Host ''
    Write-Host '  [C] 核心 KPI' -ForegroundColor Yellow
    RunSql "select kpi_name, cast(kpi_value as decimal(18,2)) as kpi_value, unit
from ydsh_ads.ads_kpi_week3 order by kpi_name"

    Write-Host ''
    Write-Host '  [D] 品类 TopN' -ForegroundColor Yellow
    RunSql 'select rank_no, category, round(gmv,2) as gmv, gmv_share_pct
from ydsh_ads.ads_category_topn order by rank_no'

    Write-Host ''
    Write-Host '  [E] 用户 RFM 分层' -ForegroundColor Yellow
    RunSql 'select segment, user_cnt, user_share_pct, round(gmv,2) as gmv, gmv_share_pct
from ydsh_ads.ads_user_rfm_segment order by gmv desc'

    Write-Host ''
    Write-Host '  [F] 异常日（|z| >= 2）' -ForegroundColor Yellow
    RunSql 'select order_date, round(gmv,2) as gmv, z_score, anomaly_type
from ydsh_ads.ads_anomaly_daily order by abs(z_score) desc limit 10'
}

function Open-Dashboard {
    if (Test-Path $Dashboard) {
        Write-Host ''
        Write-Host '   正在打开结果看板 ...' -ForegroundColor Yellow
        Start-Process $Dashboard
        Ok ('看板已打开：' + $Dashboard)
    } else {
        Bad ('看板文件不存在：' + $Dashboard)
        Info '先完整跑一次（不加 -SkipRun）'
    }
}

# ============================================================
Title '云购生活数仓 · 第3周 一键运行'
Write-Host ('   本周目录：' + $Root) -ForegroundColor Gray
Write-Host ('   依赖目录：' + $Week1Dir + '（环境 + ODS + DWD 由前两周提供）') -ForegroundColor Gray

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

# ---------- 0. Python 定位 ----------
Step '0/8 定位 Python（需要 pandas / scikit-learn / pyhive）'
if (-not $PyExe) {
    Bad '未找到可用的 Python（需要已安装 pandas、scikit-learn、pyhive）'
    Info '解决办法（任选其一）：'
    Info '  1) 设置环境变量 PYTHON 指向你的 python.exe，再双击本脚本'
    Info '  2) 用 pip 安装依赖：pip install pandas scikit-learn pyhive matplotlib openai python-docx xgboost thrift_sasl'
    Info '  3) 只想看结果：scripts\run-week3.ps1 -SkipRun'
    if (-not $NoPause) { Read-Host '按回车退出' }
    exit 1
}
Ok ('Python: ' + $PyExe + $(if ($PyLead) { ' ' + $PyLead } else { '' }))

# ---------- 1. Docker ----------
Step '1/8 检查 Docker'
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
Step '2/8 启动容器（环境在第1周目录）'
Set-Location $Root
if (Test-Path $Compose) {
    docker compose -f $Compose up -d 2>&1 | Out-Null
} else {
    Info ('未找到 ' + $Compose + '，假设容器已在运行')
}
docker ps --format '   {{.Names}}  {{.Status}}'
Ok '容器已启动'

# ---------- 3. 等待 Hive ----------
Step '3/8 等待 HiveServer2 就绪（最多 3 分钟）'
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
        docker exec $Ctr rm -rf /tmp/hive 2>$null
        docker compose -f $Compose up -d --force-recreate hive-server 2>&1 | Out-Null
        Start-Sleep 10
    }
}
if (-not $ready) {
    Bad 'HiveServer2 未就绪，请查看：docker logs --tail 30 hive-server'
    if (-not $NoPause) { Read-Host '按回车退出' }
    exit 1
}
Ok 'HiveServer2 就绪'

# ---------- 3.5 检查前两周的数据 ----------
Step '3.5/8 检查前两周数据是否就绪'
$chk = docker exec $Ctr beeline -u $Jdbc --silent=true --outputformat=dsv -e 'select count(*) as c from ydsh_dwd.dwd_order;' 2>&1 | Out-String
$m = [regex]::Match($chk, '(?m)^\s*(\d+)\s*$')
if ($m.Success) {
    Info ('DWD 已就绪：dwd_order 共 ' + $m.Groups[1].Value + ' 行')
} else {
    Bad 'DWD 层未就绪（找不到 ydsh_dwd.dwd_order）'
    Info ('请先运行第1周与第2周：' + $Week1Dir)
    if (-not $NoPause) { Read-Host '按回车退出' }
    exit 1
}

# ---------- 4. 执行 SQL ----------
Step '4/8 执行第3周 SQL（DWS 扩展 + ADS 扩展）'
$sqlScripts = @(
    @{ n = '50'; file = 'sql/dws/50_dws_ext.sql'; desc = 'DWS 汇总层扩展' },
    @{ n = '60'; file = 'sql/ads/60_ads_ext.sql'; desc = 'ADS 应用层扩展' }
)
$total = [Diagnostics.Stopwatch]::StartNew()
foreach ($s in $sqlScripts) {
    $src = Join-Path $Root $s.file
    if (-not (Test-Path $src)) { Bad ('找不到 ' + $s.file); continue }
    $sw = [Diagnostics.Stopwatch]::StartNew()
    docker cp $src "${Ctr}:/opt/hive/data/run.sql" | Out-Null
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
Info ('SQL 总耗时：{0:N0} 秒' -f $total.Elapsed.TotalSeconds)

# ---------- 5. Python 流水线 ----------
Step '5/8 执行 Python 流水线（预测 / NL2SQL / 洞察 / 报告）'
Push-Location (Join-Path $Root 'py')
$pyArgs = @('run_week3.py')
if ($NoLLM) { $pyArgs += '--no-llm' }
Invoke-Py @pyArgs
if ($LASTEXITCODE -ne 0) { Bad 'Python 流水线有失败步骤' } else { Ok 'Python 流水线完成' }
Pop-Location

# ---------- 6. 生成 docx ----------
Step '6/8 生成 Word 报告'
Push-Location $Root
$mdPairs = @(
    @{ md = 'reports/第3周_预测建模分析报告.md'; docx = 'reports/第3周_预测建模分析报告.docx' },
    @{ md = 'reports/第3周_AI洞察与经营简报.md'; docx = 'reports/第3周_AI洞察与经营简报.docx' }
)
foreach ($p in $mdPairs) {
    if (Test-Path $p.md) {
        Invoke-Py 'scripts/md2docx.py' $p.md $p.docx 2>&1 | Out-Null
        if (Test-Path $p.docx) { Ok ('生成 ' + (Split-Path -Leaf $p.docx)) }
        else { Bad ('生成失败：' + $p.docx) }
    } else {
        Bad ('缺少源文件：' + $p.md)
    }
}
Pop-Location

# ---------- 7. 结果展示 ----------
Step '7/8 结果展示'
Show-Results

# ---------- 完成 ----------
Title '全部完成'
Write-Host '  四层：ydsh_ods → ydsh_dwd → ydsh_dws → ydsh_ads' -ForegroundColor Green
Write-Host '  本周新增：DWS 7 张扩展 / ADS 9 张扩展 / 预测模型 / NL2SQL / AI 洞察' -ForegroundColor Green
if ($script:fail -eq 0) {
    Write-Host '  执行结果：全部成功' -ForegroundColor Green
} else {
    Write-Host ('  执行结果：有 ' + $script:fail + ' 处失败，请查看上方红色提示') -ForegroundColor Red
}
Write-Host ''
Write-Host '  结果文件：' -ForegroundColor Gray
Write-Host ('    ' + $Dashboard) -ForegroundColor Gray
Write-Host ('    ' + (Join-Path $Root 'reports\第3周_预测建模分析报告.docx')) -ForegroundColor Gray
Write-Host ('    ' + (Join-Path $Root 'reports\第3周_AI洞察与经营简报.docx')) -ForegroundColor Gray

Open-Dashboard

if (-not $NoPause) { Write-Host ''; Read-Host '按回车退出' }
