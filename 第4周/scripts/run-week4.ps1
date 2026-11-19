# ============================================================
#  第 4 周 一键运行脚本
#
#  用法：
#    一键运行_第4周.bat                 启动服务并打开页面（日常用）
#    一键运行_第4周.bat /full           完整部署（首次/重建后）
#    一键运行_第4周.bat /test           只跑冒烟测试
#
#  三种模式的区别：
#    start  只确保容器起来 + 打开页面，最快（约 1 分钟）
#    full   建 ADS 表 → 同步 ClickHouse → 搭建图表 → 配置筛选器 → 冒烟测试
#    test   只跑冒烟测试，不动服务
#
#  参数：
#    -Mode <start|full|test>   运行模式
#    -NoPause                  结束后不等回车（被 bat 调用时用）
# ============================================================
param(
    [ValidateSet('start', 'full', 'test')]
    [string]$Mode = 'start',
    [switch]$NoPause
)

$ErrorActionPreference = 'Continue'

# 控制台统一 UTF-8：否则中文与 ✓/✗ 会乱码
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$OutputEncoding = [System.Text.Encoding]::UTF8
$env:PYTHONIOENCODING = 'utf-8'
try { chcp 65001 > $null } catch { }

$Week4Dir = Split-Path -Parent $PSScriptRoot          # 第4周
$ProjRoot = Split-Path -Parent $Week4Dir              # 项目根
$Compose  = Join-Path $Week4Dir 'docker-compose.yml'
$EnvFile  = Join-Path $Week4Dir '.env'
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

# Python 定位：本项目依赖 pyhive/pandas，系统 python 往往没装
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

# 把 .env 的值注入当前进程（不打印内容）
function Import-EnvFile {
    if (-not (Test-Path $EnvFile)) { return }
    Get-Content $EnvFile | ForEach-Object {
        if ($_ -match '^\s*([A-Z_]+)\s*=\s*(.*)$') {
            [Environment]::SetEnvironmentVariable($matches[1], $matches[2], 'Process')
        }
    }
}

function Open-Pages {
    $urls = @(
        @{ u = 'http://127.0.0.1:8000/';                    n = 'ECharts 数据大屏' },
        @{ u = 'http://127.0.0.1:8088/superset/dashboard/1/'; n = 'Superset 仪表板' },
        @{ u = 'http://127.0.0.1:8501/';                    n = 'Streamlit 智能问答' }
    )
    foreach ($x in $urls) {
        try {
            Start-Process $x.u
            Info ('已打开：' + $x.n + '  ' + $x.u)
            Start-Sleep -Milliseconds 600
        } catch {
            Info ('打开失败：' + $x.u)
        }
    }
}

Title ('云购生活数仓 · 第4周 一键运行（模式：' + $Mode + '）')

# ---------- 1. Docker ----------
Step '1/5 检查 Docker'
docker info *> $null
if ($LASTEXITCODE -ne 0) {
    Info 'Docker 未运行，尝试启动 Docker Desktop ...'
    $exe = "$env:LOCALAPPDATA\Programs\DockerDesktop\Docker Desktop.exe"
    if (Test-Path $exe) { Start-Process $exe }
    for ($i = 1; $i -le 40; $i++) {
        Start-Sleep -Seconds 5
        docker info *> $null
        if ($LASTEXITCODE -eq 0) { break }
    }
}
docker info *> $null
if ($LASTEXITCODE -ne 0) {
    Bad 'Docker 未就绪，请手动启动 Docker Desktop 后重试'
    if (-not $NoPause) { Read-Host '按回车退出' }
    exit 1
}
Ok 'Docker 就绪'

# ---------- 2. .env ----------
Step '2/5 检查 .env'
if (-not (Test-Path $EnvFile)) {
    Info '.env 不存在，生成随机口令 ...'
    $py0 = Resolve-Python
    if ($py0) { & $py0 (Join-Path $Week4Dir 'scripts\gen_env.py') }
}
if (Test-Path $EnvFile) {
    Import-EnvFile
    Ok '.env 就绪（口令不回显）'
} else {
    Bad '.env 缺失，无法启动（需要 CLICKHOUSE_PASSWORD 等）'
    if (-not $NoPause) { Read-Host '按回车退出' }
    exit 1
}

# ---------- 3. 启动服务 ----------
Step '3/5 启动 6 个服务'
if ($Mode -eq 'test') {
    Info '测试模式，跳过启动'
} else {
    docker compose -f $Compose up -d 2>&1 | Out-Null
    if ($LASTEXITCODE -ne 0) { Bad 'compose up 失败' } else { Ok '容器已启动' }
}

# 等关键服务健康
Info '等待服务就绪（HiveServer2 冷启动约 1~2 分钟）...'
$targets = @('hive-server', 'clickhouse', 'dsh-app', 'superset')
$ready = $false
for ($i = 1; $i -le 60; $i++) {
    $okCnt = 0
    foreach ($n in $targets) {
        $s = docker inspect $n --format '{{if .State.Health}}{{.State.Health.Status}}{{end}}' 2>$null
        if ($s -eq 'healthy') { $okCnt++ }
    }
    if ($okCnt -ge $targets.Count) { $ready = $true; break }
    Start-Sleep -Seconds 5
}

if ($ready) {
    Ok '全部服务健康'
} else {
    # hive-server 常见问题：/tmp 残留 pid 文件导致反复重启
    Info '有服务未就绪，尝试重建 hive-server（清理残留 pid）...'
    docker rm -f hive-server *> $null
    docker compose -f $Compose up -d hive-server 2>&1 | Out-Null
    for ($i = 1; $i -le 40; $i++) {
        Start-Sleep -Seconds 6
        $s = docker inspect hive-server --format '{{if .State.Health}}{{.State.Health.Status}}{{end}}' 2>$null
        if ($s -eq 'healthy') { $ready = $true; break }
    }
    if ($ready) { Ok '重建后服务健康' } else { Bad '服务仍未全部就绪' }
}

docker ps --format '   {{.Names}}  {{.Status}}'

# ---------- 4. 完整部署（仅 /full） ----------
if ($Mode -eq 'full') {
    $py = Resolve-Python
    if (-not $py) {
        Bad '找不到可用的 Python，无法执行完整部署'
    } else {
        Step '4/5 建表 + 同步数据 + 搭建图表 + 配置筛选器'

        # 4.1 建第 4 周 ADS 表
        Info '[1/4] 建 ADS 表 ...'
        $sqlFile = Join-Path $Week4Dir 'sql\ads\70_ads_week4.sql'
        $runner  = Join-Path $Week4Dir 'scripts\hive_run_sql.py'
        if ((Test-Path $sqlFile) -and (Test-Path $runner)) {
            & $py $runner $sqlFile 2>&1 | ForEach-Object { Write-Host ('      ' + $_) }
            if ($LASTEXITCODE -eq 0) { Ok 'ADS 表就绪' } else { Bad 'ADS 建表失败' }
        } else { Bad '缺少建表 SQL 或执行器' }

        # 4.2 同步 ClickHouse
        Info '[2/4] 同步 ADS → ClickHouse ...'
        $env:MSYS_NO_PATHCONV = '1'
        docker exec dsh-app python /app/sync_clickhouse.py 2>&1 |
            ForEach-Object { if ($_ -match '同步完成|失败：') { Write-Host ('      ' + $_) } }
        if ($LASTEXITCODE -eq 0) { Ok '数据同步完成' } else { Bad '同步失败' }

        # 4.3 搭建图表
        Info '[3/4] 搭建 Superset 图表 ...'
        $setup = Join-Path $Week4Dir 'scripts\setup_dashboard.py'
        if (Test-Path $setup) {
            & $py $setup 2>&1 | ForEach-Object { Write-Host ('      ' + $_) }
            if ($LASTEXITCODE -eq 0) { Ok '图表就绪' } else { Bad '图表搭建失败' }
        } else { Bad '缺少 setup_dashboard.py' }

        # 4.4 配置筛选器
        Info '[4/4] 配置筛选器 ...'
        $filters = Join-Path $Week4Dir 'scripts\add_filters.py'
        if (Test-Path $filters) {
            & $py $filters 2>&1 | ForEach-Object { Write-Host ('      ' + $_) }
            if ($LASTEXITCODE -eq 0) { Ok '筛选器就绪' } else { Bad '筛选器配置失败' }
        } else { Bad '缺少 add_filters.py' }
    }
} else {
    Step '4/5 跳过完整部署（需要时用 /full）'
    Info '日常启动不需要重建数据，直接打开页面即可'
}

# ---------- 5. 冒烟测试 ----------
Step '5/5 冒烟测试'
if ($Mode -eq 'start') {
    # 启动模式只做快速探活，不跑全套（省时间）
    $live = $false
    for ($i = 1; $i -le 12; $i++) {
        try {
            $r = Invoke-WebRequest -Uri 'http://127.0.0.1:8000/api/live' -TimeoutSec 5 -UseBasicParsing
            if ($r.StatusCode -eq 200) { $live = $true; break }
        } catch { }
        Start-Sleep -Seconds 3
    }
    if ($live) { Ok '接口已响应' } else { Bad '接口未响应' }
} else {
    $py = Resolve-Python
    if ($py) {
        & $py (Join-Path $Week4Dir 'scripts\smoke_test.py')
        if ($LASTEXITCODE -ne 0) { Bad '冒烟测试存在失败项' }
    } else {
        Bad '找不到可用的 Python，无法跑冒烟测试'
    }
}

# ---------- 打开页面 ----------
if ($Mode -ne 'test') {
    Step '打开结果页面'
    Open-Pages
}

# ---------- 汇总 ----------
Title '完成'
Write-Host '  访问地址：' -ForegroundColor White
Write-Host '    ECharts 数据大屏   http://127.0.0.1:8000/' -ForegroundColor Green
Write-Host '    数据接口文档        http://127.0.0.1:8000/docs' -ForegroundColor Green
Write-Host '    Superset 仪表板     http://127.0.0.1:8088/superset/dashboard/1/' -ForegroundColor Green
Write-Host '    Streamlit 智能问答  http://127.0.0.1:8501/' -ForegroundColor Green
Write-Host ''
Write-Host '  登录账号：' -ForegroundColor White
Write-Host ('    Superset：' + $(if ($env:SUPERSET_ADMIN_USER) { $env:SUPERSET_ADMIN_USER } else { 'admin' }) +
            ' / ' + $(if ($env:SUPERSET_ADMIN_PASSWORD) { $env:SUPERSET_ADMIN_PASSWORD } else { '（见 .env）' })) -ForegroundColor Gray
Write-Host ''
Write-Host '  常用命令：' -ForegroundColor White
Write-Host "    docker compose -f `"$Compose`" ps" -ForegroundColor Gray
Write-Host "    docker compose -f `"$Compose`" logs -f app" -ForegroundColor Gray
Write-Host "    docker compose -f `"$Compose`" down" -ForegroundColor Gray
Write-Host ''

if ($script:fail -gt 0) {
    Write-Host "  存在 $script:fail 项失败，请查看上面的红色输出" -ForegroundColor Red
    if (-not $NoPause) { Read-Host '按回车退出' }
    exit 1
}
Write-Host '  全部完成' -ForegroundColor Green
if (-not $NoPause) { Read-Host '按回车退出' }
exit 0
