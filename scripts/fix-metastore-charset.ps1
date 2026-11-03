<#
  修复 Hive 元数据库（MySQL）字符集导致的"表/字段注释变问号"问题
  现象：desc dwd_order 时 comment 显示 ????，但数据本身正常
  原因：Hive 4.0.0 的 metastore 建表脚本把 COLUMNS_V2.COMMENT 等列建成 latin1，
        MySQL 写入中文时按 latin1 存储会静默替换为 '?'（不可逆）
  处理：把相关列改为 utf8mb4，然后重建表（重跑 00_ddl + 01/02 加载）
  用法：powershell -File scripts/fix-metastore-charset.ps1
#>

$ErrorActionPreference = 'Stop'

$Ctr   = 'meta-mysql'
$Root  = Split-Path -Parent $PSScriptRoot
$Check = @"
select TABLE_NAME, COLUMN_NAME, CHARACTER_SET_NAME
from information_schema.COLUMNS
where TABLE_SCHEMA='metastore'
  and ((TABLE_NAME='COLUMNS_V2' and COLUMN_NAME='COMMENT')
    or (TABLE_NAME='TABLE_PARAMS' and COLUMN_NAME='PARAM_VALUE'));
"@

function Invoke-MetaSql([string]$sql) {
    $sql | docker exec -i $Ctr sh -c 'mysql -uroot -proot'
}

Write-Host '当前字符集：' -ForegroundColor Cyan
Invoke-MetaSql $Check

$hasLatin1 = (Invoke-MetaSql $Check 2>&1 | Out-String) -match 'latin1'
if (-not $hasLatin1) {
    Write-Host ''
    Write-Host '已经是 utf8mb4，无需修复。' -ForegroundColor Green
    exit 0
}

Write-Host ''
Write-Host '正在把 metastore 注释列改为 utf8mb4 ...' -ForegroundColor Yellow
Invoke-MetaSql @"
ALTER TABLE metastore.COLUMNS_V2
  MODIFY COMMENT varchar(4000) CHARACTER SET utf8mb4 COLLATE utf8mb4_bin;
ALTER TABLE metastore.TABLE_PARAMS
  MODIFY PARAM_VALUE mediumtext CHARACTER SET utf8mb4 COLLATE utf8mb4_bin;
"@

Write-Host ''
Write-Host '修复后的字符集：' -ForegroundColor Cyan
Invoke-MetaSql $Check

Write-Host ''
Write-Host '注意：已存成 ? 的旧注释无法恢复，需要重跑 DDL 重建表。' -ForegroundColor Yellow
Write-Host ('  双击 ' + (Join-Path $Root '一键运行_第2周.bat') + ' 即可重建（约 3-4 分钟）。')
Write-Host ''
