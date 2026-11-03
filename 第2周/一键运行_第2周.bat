@echo off
chcp 65001 >nul
title Week2 Data Warehouse - One Click Run
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts/run-week2.ps1"
echo.
pause
