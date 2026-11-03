@echo off
title DSH Project - Week1 Run
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\run-all.ps1"
echo.
pause
