@echo off
chcp 65001 > nul
REM ============================================================
REM  第 4 周 一键部署（双击运行）
REM  详细流程见 scripts\deploy.ps1 头部注释
REM ============================================================
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\deploy.ps1" %*
set RC=%ERRORLEVEL%
echo.
if not "%RC%"=="0" (
    echo 部署未全部成功，退出码 %RC%
) else (
    echo 部署完成
)
pause
exit /b %RC%
