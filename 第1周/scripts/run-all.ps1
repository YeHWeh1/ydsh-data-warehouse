# ============================================================
#  第1周 一键运行脚本
#  作用：启动环境 → 等待就绪 → 执行数据清洗 → 展示结果
#  用法：双击 "一键运行.bat"，或右键此脚本 -> 使用 PowerShell 运行
# ============================================================

$ErrorActionPreference = 'Continue'

# 控制台 UTF-8，避免 beeline 输出的中文按 GBK 解码成乱码
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$OutputEncoding = [System.Text.Encoding]::UTF8
try { chcp 65001 > $null } catch { }

# 本周目录（scripts 的上级），docker-compose.yml 与 sql 都在这里
$Root = Split-Path -Parent $PSScriptRoot
$Jdbc = 'jdbc:hive2://localhost:10000'
$Ctr  = 'hive-server'

function Title($t) {
    Write-Host ""
    Write-Host ('=' * 62) -ForegroundColor DarkCyan
    Write-Host ('  ' + $t) -ForegroundColor White
    Write-Host ('=' * 62) -ForegroundColor DarkCyan
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
    $err = $lines | Where-Object { $_ -match 'Error:|FAILED:' } | Select-Object -First 2
    if ($err) { $err | ForEach-Object { Bad $_ } }
}

Title '云购生活数仓 · 第1周 一键运行'

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

# ---------- 4. 执行清洗 ----------
Step '4/6 执行数据清洗'
$scripts = @(
    @{ file = '01_dwd_user.sql';    tables = 'dwd_user' },
    @{ file = '02_dwd_product.sql'; tables = 'dwd_product' },
    @{ file = '03_dwd_order.sql';   tables = 'dwd_order' },
    @{ file = '04_dwd_other.sql';   tables = 'dwd_store' },
    @{ file = '05_dwd_extra.sql';   tables = 'dwd_order_item + dwd_user_behavior' }
)
foreach ($s in $scripts) {
    $src = Join-Path $Root ("sql\clean\" + $s.file)
    if (-not (Test-Path $src)) { Bad ("找不到 " + $s.file); continue }
    docker cp $src "$Ctr`:/opt/hive/data/run.sql" | Out-Null
    $out = docker exec $Ctr beeline -u $Jdbc -f '/opt/hive/data/run.sql' 2>&1 | Out-String
    if ($out -match 'Error:|FAILED:') {
        Bad ($s.file + '  ->  ' + $s.tables)
        ($out -split "`r?`n" | Where-Object { $_ -match 'Error:|FAILED:' } | Select-Object -First 2) |
            ForEach-Object { Write-Host ('      ' + $_) }
    } else {
        Ok ($s.file + '  ->  ' + $s.tables)
    }
}

# ---------- 5. 结果展示 ----------
Step '5/6 结果展示'

Write-Host ''
Write-Host '  [A] 当前所有表（6 张 ODS + 6 张 DWD）' -ForegroundColor Yellow
RunSql 'use ydsh_ods; show tables;'

Write-Host ''
Write-Host '  [B] 清洗前：ODS 层的原始数据（故意埋了脏数据）' -ForegroundColor Yellow
Write-Host '      age 的极值 —— 正常人不可能是 -3 或 200'
RunSql 'select min(age) as min_age, max(age) as max_age from ydsh_ods.ods_user;'
Write-Host '      订单行数 vs 不同订单号 —— 两者不相等说明有重复'
RunSql 'select count(*) as total_rows, count(distinct order_id) as distinct_oid from ydsh_ods.ods_order;'

Write-Host ''
Write-Host '  [C] 清洗后：DWD 层的干净数据' -ForegroundColor Yellow
Write-Host '      age 收敛到 18~65，78 条被填充'
RunSql 'select min(age) as min_age, max(age) as max_age, sum(age_filled) as filled_cnt from ydsh_ods.dwd_user;'
Write-Host '      行数 = 不同订单号，重复已消除'
RunSql 'select count(*) as total_rows, count(distinct order_id) as distinct_oid from ydsh_ods.dwd_order;'

Write-Host ''
Write-Host '  [D] 六张 DWD 表行数' -ForegroundColor Yellow
RunSql "select 'dwd_user' as tbl, count(*) as cnt from ydsh_ods.dwd_user
union all select 'dwd_product', count(*) from ydsh_ods.dwd_product
union all select 'dwd_store', count(*) from ydsh_ods.dwd_store
union all select 'dwd_order', count(*) from ydsh_ods.dwd_order
union all select 'dwd_order_item', count(*) from ydsh_ods.dwd_order_item
union all select 'dwd_user_behavior', count(*) from ydsh_ods.dwd_user_behavior"

Write-Host ''
Write-Host '  [E] 异常标记（只标记，不改原值）' -ForegroundColor Yellow
RunSql 'select sum(quantity_invalid) as qty_invalid, sum(quantity_outlier) as qty_outlier from ydsh_ods.dwd_order_item'
RunSql 'select sum(behavior_type_variant) as enum_variant from ydsh_ods.dwd_user_behavior'
RunSql 'select sum(email_missing) as email_missing, sum(gender_missing) as gender_missing, sum(member_level_missing) as level_missing from ydsh_ods.dwd_user'

# ---------- 6. 完成 ----------
Title '全部完成'
Write-Host '  环境：3 个容器运行中（mysql / metastore / hive-server）' -ForegroundColor Green
Write-Host '  数据：6 张 ODS 原表 + 6 张 DWD 清洗表' -ForegroundColor Green
Write-Host ''
Write-Host '  想手动进 Hive 看看，执行：' -ForegroundColor Gray
Write-Host '  docker exec -it hive-server beeline -u "jdbc:hive2://localhost:10000"' -ForegroundColor Gray
Write-Host ''
Read-Host '按回车退出'
