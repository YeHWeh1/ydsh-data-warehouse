@echo off
chcp 65001 >nul
setlocal
title 云购生活数仓 - 第4周 一键运行

REM ============================================================
REM  第 4 周 一键运行（双击即可）
REM
REM  做四件事：
REM    1. 启动 Docker（没开就自动开）
REM    2. 拉起 6 个服务并等它们健康
REM    3. 打开三个结果页面（大屏 / Superset / Streamlit）
REM    4. 打印访问地址与账号
REM
REM  参数（可选）：
REM    /full   完整部署：建表 + 同步数据 + 搭建图表 + 冒烟测试
REM            首次运行或数据重建后用这个
REM    /test   只跑冒烟测试
REM ============================================================

cd /d "%~dp0"

echo ============================================================
echo   云购生活数仓 . 第4周 一键运行
echo ============================================================
echo.

if not exist "%~dp0scripts\run-week4.ps1" (
    echo [ERROR] 找不到 scripts\run-week4.ps1
    echo         请确认本文件位于「第4周」目录内
    echo.
    pause
    exit /b 1
)

REM 解析参数
set "MODE=start"
if /i "%~1"=="/full" set "MODE=full"
if /i "%~1"=="/test" set "MODE=test"

echo   模式：%MODE%
echo.

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\run-week4.ps1" -Mode %MODE% -NoPause
set RC=%ERRORLEVEL%

echo.
if not "%RC%"=="0" (
    echo [FAILED] 退出码 %RC%
    echo          请查看上面的红色提示
) else (
    echo [DONE] 全部完成
)
echo.
pause
exit /b %RC%
