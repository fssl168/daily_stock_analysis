#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""启动耗时诊断脚本：分阶段计时，定位 2 分钟卡在哪。

在**后端启动问题的环境**运行：
    python scripts/diagnose_startup.py
输出每个阶段的耗时，据此定位瓶颈（import / 调度服务 / 配置服务 /
EventBus / 数据库 / 数据源）。
"""
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)


def step(label, start):
    now = time.time()
    print(f"  {label}: {now - start:7.1f}s")
    return now


t0 = time.time()
print(f"[0] 开始 (PID={os.getpid()})")

import server  # noqa: E402
t1 = step("import server (全部模块)", t0)

from src.config import setup_env  # noqa: E402
setup_env()
t2 = step("setup_env", t1)

from src.services.runtime_scheduler import RuntimeSchedulerService  # noqa: E402
t3 = step("RuntimeSchedulerService import", t2)
svc = RuntimeSchedulerService(
    owns_schedule=True, force_enabled=False,
    run_immediately_in_background=True, schedule_args_overrides=None,
)
t4 = step("RuntimeSchedulerService 实例化", t3)
svc.reconcile_from_config(run_immediately=False)
t5 = step("reconcile_from_config (调度配置/DB)", t4)

from src.services.system_config_service import SystemConfigService  # noqa: E402
t6 = step("SystemConfigService import", t5)
scs = SystemConfigService(runtime_scheduler=svc)
t7 = step("SystemConfigService 实例化", t6)

from src.services.bootstrap_event_bus import bootstrap_event_bus  # noqa: E402
t8 = step("bootstrap_event_bus import", t7)
bus = bootstrap_event_bus()
t9 = step("bootstrap_event_bus init (含 load_from_disk)", t8)

from src.storage import DatabaseManager  # noqa: E402
t10 = step("DatabaseManager import", t9)
db = DatabaseManager()
t11 = step("DatabaseManager init (连接 DB)", t10)

from data_provider import DataFetcherManager  # noqa: E402
t12 = step("DataFetcherManager import", t11)
try:
    dm = DataFetcherManager()
    t13 = step("DataFetcherManager init (数据源预热)", t12)
except Exception as e:  # noqa: BLE001
    t13 = time.time()
    print(f"  DataFetcherManager init ERR: {str(e)[:100]} ({t13 - t12:.1f}s)")

print(f"\n总耗时: {t13 - t0:.1f}s")
print("说明：若 reconcile_from_config / DatabaseManager init / DataFetcherManager init 耗时占比大，"
      "瓶颈在数据库 IO 或数据源预热；若 import server 占比大，瓶颈在模块加载。")
