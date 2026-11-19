# ============================================================
# 第 4 周 一键部署脚本
#
#   powershell -ExecutionPolicy Bypass -File 第4周\scripts\deploy.ps1
#
# 流程（对应任务书子任务 4 与手册步骤 5）：
#   1. 检查 Docker 与 .env（缺 .env 自动生成随机口令）
#   2. 拉起全部服务（含第 1 周的 mysql/hive，用 include 复用）
#   3. 等 Hive 真正可用，建第 4 周 ADS 表
#   4. 同步 ADS → ClickHouse
#   5. 冒烟测试
#   6. 打印访问地址
#
# 参数：
#   -SkipBuild   不重建 app 镜像（只改代码时用）
#   -SkipSync    跳过 ClickHouse 同步（数据没变时用）
#   -SkipTest    跳过冒烟测试
# ============================================================
param(
    [switch]$SkipBuild,
    [switch]$SkipSync,
    [switch]$SkipTest
)

$ErrorActionPreference = 'Continue'

[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$OutputEncoding = [System.Text.Encoding]::UTF8
$env:PYTHONIOENCODING = 'utf-8'
try { chcp 65001 > $null } catch { }

$Week4Dir  = Split-Path -Parent $PSScriptRoot          # 第4周
$ProjRoot  = Split-Path -Parent $Week4Dir              # 项目根
$Compose   = Join-Path $Week4Dir 'docker-compose.yml'
$EnvFile   = Join-Path $Week4Dir '.env'
$script:fail = 0

function Title($t) {
    Write-Host ""
    Write-Host ('=' * 64) -ForegroundColor DarkCyan
    Write-Host ('  ' + $t) -ForegroundColor White
    Write-Host ('=' * 64) -ForegroundColor DarkCyan
}
function Step($t) { Write-Host ""; Write-Host ('>> ' + $t) -ForegroundColor Cyan }
function Ok($t)   { Write-Host ('   OK   ' + $t) -ForegroundColor Green }
function Bad($t)  { Write-Host ('   失败  ' + $t) -ForegroundColor Red; $script:fail++ }
function Info($t) { Write-Host ('   ' + $t) -ForegroundColor Gray }

# Python 定位：本项目依赖 pyhive/pandas，系统 python 往往没有
function Resolve-Python {
    $cands = @()
    if ($env:PYTHON) { $cands += $env:PYTHON }
    $cands += 'C:\Users\wei\.dsh\dsh-runtimes\dsh-primary-runtime\dependencies\python\python.exe'
    foreach ($n in @('python', 'python3')) {
        $c = Get-Command $n -ErrorAction SilentlyContinue
        if ($c) { $cands += $c.Source }
    }
    foreach ($c in $cands) {
        if (-not $c -or -not (Test-Path $c)) { continue }
        & $c -c "import pandas, pyhive" 2>$null
        if ($LASTEXITCODE -eq 0) { return $c }
    }
    return $null
}

Title '云购生活数仓 · 第 4 周 一键部署'

# ---------- 1. Docker ----------
Step '1/6 检查 Docker'
docker info *> $null
if ($LASTEXITCODE -ne 0) {
    Info 'Docker 未运行，尝试启动 Docker Desktop ...'
    $exe = "$env:LOCALAPPDATA\Programs\DockerDesktop\Docker Desktop.exe"
    if (Test-Path $exe) { Start-Process $exe }
    for ($i = 1; $i -le 30; $i++) {
        Start-Sleep -Seconds 5
        docker info *> $null
        if ($LASTEXITCODE -eq 0) { break }
    }
}
docker info *> $null
if ($LASTEXITCODE -ne 0) {
    Bad 'Docker 未就绪，请手动启动 Docker Desktop 后重试'
    exit 1
}
Ok 'Docker 就绪'

# ---------- 2. .env ----------
Step '2/6 检查 .env（敏感信息只放这里）'
if (-not (Test-Path $EnvFile)) {
    Info '.env 不存在，生成随机口令 ...'
    $py = Resolve-Python
    if ($py) {
        & $py (Join-Path $Week4Dir 'scripts\gen_env.py')
    } else {
        Bad '找不到可用的 Python，无法生成 .env'
        exit 1
    }
}
if (Test-Path $EnvFile) {
    # 只检查关键项是否有值，不回显内容
    $envText = Get-Content $EnvFile -Raw
    foreach ($k in @('CLICKHOUSE_PASSWORD', 'SUPERSET_SECRET_KEY', 'SUPERSET_ADMIN_PASSWORD')) {
        if ($envText -notmatch "(?m)^$k=.+") {
            Bad "$k 为空，请编辑 第4周\.env 填写"
        }
    }
    Ok '.env 就绪（口令不回显）'
} else {
    Bad '.env 缺失'
    exit 1
}

# ---------- 3. 起服务 ----------
Step '3/6 启动全部服务'
if ($SkipBuild) {
    docker compose -f $Compose up -d 2>&1 | Out-Null
} else {
    docker compose -f $Compose up -d --build 2>&1 | Out-Null
}
if ($LASTEXITCODE -ne 0) { Bad 'compose up 失败' } else { Ok '容器已启动' }

# 等 Hive 可用（HiveServer2 冷启动约 60~120 秒）
Info '等待 HiveServer2 就绪（最长 4 分钟）...'
$hiveReady = $false
for ($i = 1; $i -le 48; $i++) {
    Start-Sleep -Seconds 5
    $h = docker inspect hive-server --format '{{if .State.Health}}{{.State.Health.Status}}{{end}}' 2>$null
    if ($h -eq 'healthy') { $hiveReady = $true; break }
}
if ($hiveReady) { Ok 'HiveServer2 就绪' } else { Bad 'HiveServer2 未在预期时间内就绪' }

# 等 Superset 可用（它要装驱动、建管理员、升级元数据库、注册数据源，首次约 2~4 分钟）
Info '等待 Superset 就绪（最长 6 分钟）...'
$spReady = $false
for ($i = 1; $i -le 72; $i++) {
    $s = docker inspect superset --format '{{if .State.Health}}{{.State.Health.Status}}{{end}}' 2>$null
    if ($s -eq 'healthy') { $spReady = $true; break }
    Start-Sleep -Seconds 5
}
if ($spReady) { Ok 'Superset 就绪' } else { Bad 'Superset 未在预期时间内就绪（冒烟测试会失败）' }

# ---------- 4. 建表 ----------
Step '4/6 建第 4 周 ADS 表'
$sqlFile = Join-Path $Week4Dir 'sql\ads\70_ads_week4.sql'
$py = Resolve-Python
$runner = Join-Path $PSScriptRoot 'hive_run_sql.py'
if ((Test-Path $sqlFile) -and $py -and (Test-Path $runner)) {
    # 用 Python 执行：Get-Content -Raw 会把 BOM 写进管道，beeline 报
    # "character '﻿' not supported here"，建表全失败
    & $py $runner $sqlFile 2>&1 | ForEach-Object { Write-Host ('   ' + $_) }
    if ($LASTEXITCODE -eq 0) { Ok 'ADS 表已建好（幂等，可重复执行）' }
    else { Bad '建表失败，请查看上一步输出' }
} else {
    Bad "缺少 $sqlFile 或 Python/SQL 执行器"
}

# ---------- 5. 同步 ClickHouse ----------
Step '5/6 同步 ADS → ClickHouse'
if ($SkipSync) {
    Info '已跳过（-SkipSync）'
} else {
    $env:MSYS_NO_PATHCONV = '1'
    docker exec dsh-app python /app/sync_clickhouse.py 2>&1 | ForEach-Object {
        if ($_ -match '同步完成|失败：') { Write-Host ('   ' + $_) }
    }
    if ($LASTEXITCODE -eq 0) { Ok '同步完成' } else { Bad '同步失败' }
}

# ---------- 6. 搭建 Superset 图表 ----------
Step '6/7 搭建 Superset 数据集与图表'
$py = Resolve-Python
if ($py) {
    # 把 .env 的口令注入当前进程（脚本要用它登录 Superset）
    Get-Content $EnvFile | ForEach-Object {
        if ($_ -match '^\s*([A-Z_]+)\s*=\s*(.*)$') {
            [Environment]::SetEnvironmentVariable($matches[1], $matches[2], 'Process')
        }
    }
    $setup = Join-Path $Week4Dir 'scripts\setup_dashboard.py'
    if (Test-Path $setup) {
        & $py $setup 2>&1 | ForEach-Object { Write-Host ('   ' + $_) }
        if ($LASTEXITCODE -eq 0) { Ok '5 个图表与仪表板已就绪（幂等，可重复执行）' }
        else { Bad '图表搭建失败' }
    } else {
        Bad '缺少 setup_dashboard.py'
    }

    # 筛选器：任务书要求「配置筛选器与联动」，联动随图表建立，
    # 筛选器需要单独写进仪表板元数据
    $filters = Join-Path $Week4Dir 'scripts\add_filters.py'
    if (Test-Path $filters) {
        & $py $filters 2>&1 | ForEach-Object { Write-Host ('   ' + $_) }
        if ($LASTEXITCODE -eq 0) { Ok '筛选器已配置（日期范围 + 品类）' }
        else { Bad '筛选器配置失败' }
    } else {
        Bad '缺少 add_filters.py'
    }
} else {
    Bad '找不到可用的 Python，无法搭建图表'
}

# ---------- 7. 冒烟测试 ----------
Step '7/7 冒烟测试'
if ($SkipTest) {
    Info '已跳过（-SkipTest）'
} else {
    if ($py) {
        & $py (Join-Path $Week4Dir 'scripts\smoke_test.py')
        if ($LASTEXITCODE -ne 0) { Bad '冒烟测试存在失败项' }
    } else {
        Bad '找不到可用的 Python，无法跑冒烟测试'
    }
}

# ---------- 汇总 ----------
Title '部署完成'
Write-Host '  访问地址：' -ForegroundColor White
Write-Host '    ECharts 数据大屏   http://127.0.0.1:8000/' -ForegroundColor Green
Write-Host '    数据接口文档        http://127.0.0.1:8000/docs' -ForegroundColor Green
Write-Host '    Superset 仪表板     http://127.0.0.1:8088/  （账号见 .env）' -ForegroundColor Green
Write-Host '    Streamlit 智能问答  http://127.0.0.1:8501/' -ForegroundColor Green
Write-Host ''
Write-Host '  常用命令：' -ForegroundColor White
Write-Host "    docker compose -f `"$Compose`" ps" -ForegroundColor Gray
Write-Host "    docker compose -f `"$Compose`" logs -f app" -ForegroundColor Gray
Write-Host "    docker compose -f `"$Compose`" down" -ForegroundColor Gray
Write-Host ''

if ($script:fail -gt 0) {
    Write-Host "  存在 $script:fail 项失败，请查看上面的红色输出" -ForegroundColor Red
    exit 1
}
Write-Host '  全部完成' -ForegroundColor Green
exit 0
