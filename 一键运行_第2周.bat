@echo off
title DSH Project - Week2 Run
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts/run-week2.ps1"
echo.
pause
