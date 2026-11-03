<#
  在桌面创建"第 2 周"快捷方式（项目目录移动后重新运行一次即可）
  用法：右键 -> 使用 PowerShell 运行；或 powershell -File scripts/create-desktop-shortcut.ps1
#>

$ErrorActionPreference = 'Stop'

$Root      = Split-Path -Parent $PSScriptRoot
$Script    = Join-Path $Root 'scripts\run-week2.ps1'
$Dashboard = Join-Path $Root 'reports\第2周_运行看板.html'
$Desktop   = [Environment]::GetFolderPath('Desktop')
$PS        = Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'

if (-not (Test-Path $Script)) { throw ('找不到脚本：' + $Script) }

$ws = New-Object -ComObject WScript.Shell

# 1) 一键运行：跑完自动打开看板
$a = $ws.CreateShortcut((Join-Path $Desktop '第2周 一键运行.lnk'))
$a.TargetPath       = $PS
$a.Arguments        = '-NoProfile -ExecutionPolicy Bypass -File "' + $Script + '"'
$a.WorkingDirectory = $Root
$a.IconLocation     = 'C:\Windows\System32\shell32.dll,137'
$a.Description      = '云仓五号第2周：启动环境 → 跑完四层数仓 → 自动打开结果看板'
$a.Save()

# 2) 查看结果：不重跑，直接打开看板
$b = $ws.CreateShortcut((Join-Path $Desktop '第2周 查看结果.lnk'))
$b.TargetPath       = $PS
$b.Arguments        = '-NoProfile -ExecutionPolicy Bypass -File "' + $Script + '" -SkipRun'
$b.WorkingDirectory = $Root
$b.IconLocation     = 'C:\Windows\System32\shell32.dll,23'
$b.Description      = '直接打开第2周结果看板（不重跑数据）'
$b.Save()

# 3) 终端结果：不重跑，在控制台重新打印已对齐的结果表（窗口停留）
$c = $ws.CreateShortcut((Join-Path $Desktop '第2周 终端结果.lnk'))
$c.TargetPath       = $PS
$c.Arguments        = '-NoProfile -ExecutionPolicy Bypass -File "' + $Script + '" -ShowOnly'
$c.WorkingDirectory = $Root
$c.IconLocation     = 'C:\Windows\System32\shell32.dll,16'
$c.Description      = '在终端重新打印第2周结果表（不重跑数据）'
$c.Save()

Write-Host ''
Write-Host '已在桌面创建：' -ForegroundColor Green
Write-Host ('  第2周 一键运行.lnk   -> ' + $Script)
Write-Host ('  第2周 查看结果.lnk   -> ' + $Dashboard)
Write-Host '  第2周 终端结果.lnk   -> 同脚本 -ShowOnly'
Write-Host ''
Write-Host '若快捷方式打不开，请检查上面的路径是否存在。' -ForegroundColor Gray
