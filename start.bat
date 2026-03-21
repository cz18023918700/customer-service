@echo off
chcp 65001 >nul
set PYTHONIOENCODING=utf-8
title 静享时空 AI 客服
cd /d %~dp0

echo ================================
echo   静享时空 AI 客服系统
echo   http://localhost:8900
echo   管理后台: http://localhost:8900/admin
echo ================================
echo.

python main.py

pause
