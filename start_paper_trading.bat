@echo off
chcp 65001 >nul
title DSA Paper Trading Server (8000)
cd /d D:\leanpython\daily_stock_analysis
echo [DSA] 启动纸面交易完整服务 (port 8000) ...
echo [DSA] Python: .venv\Scripts\python.exe
set PYTHONPATH=
.venv\Scripts\python.exe server.py
pause
