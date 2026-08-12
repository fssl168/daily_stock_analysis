@echo off
chcp 65001 >nul
title DSA Paper Trading Listener
cd /d D:\leanpython\daily_stock_analysis
echo [DSA] 启动纸面交易行情监听器 (account 2) ...
set PYTHONPATH=
.venv\Scripts\python.exe paper_trading\run_listener.py
pause
