@echo off
chcp 65001 >nul
setlocal
title Week3 Data Warehouse - One Click Run
cd /d "%~dp0"

echo ============================================================
echo   Week 3  One Click Run
echo ============================================================
echo.

if not exist "%~dp0scripts\run-week3.ps1" (
    echo [ERROR] scripts\run-week3.ps1 not found.
    echo Make sure this file is inside the 第3周 folder.
    echo.
    pause
    exit /b 1
)

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\run-week3.ps1" -NoPause
set RC=%ERRORLEVEL%

echo.
if not "%RC%"=="0" (
    echo [FAILED] exit code = %RC%
    echo Check the red messages above.
) else (
    echo [DONE] All steps finished.
)
echo.
pause
endlocal
