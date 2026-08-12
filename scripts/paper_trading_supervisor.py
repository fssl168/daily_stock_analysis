#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""MarketListener 进程级自愈守护（方案 2）.

监控行情监听器子进程，异常退出自动重启（指数退避），干净退出（code=0 / 收到
SIGINT）不重启。配合系统计划任务（开机自启）即可实现"常驻 + 自愈"。

用法:
  python scripts/paper_trading_supervisor.py [--account 2] [--max-restarts 0]
  0 = 无限重启；配合 Windows 任务计划 / systemd 开机自启即可常驻。
"""
from __future__ import annotations

import argparse
import logging
import subprocess
import sys
import time
from pathlib import Path

log = logging.getLogger("supervisor")


def main() -> int:
    parser = argparse.ArgumentParser(description="MarketListener 自愈守护")
    parser.add_argument("--account", type=int, default=2, help="纸面账户 ID")
    parser.add_argument("--max-restarts", type=int, default=0,
                        help="最大重启次数（0=无限）")
    parser.add_argument("--python", default=sys.executable, help="Python 解释器")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
    )

    root = Path(__file__).resolve().parent.parent
    script = root / "paper_trading" / "run_listener.py"
    if not script.exists():
        log.error("未找到 %s", script)
        return 2

    restarts = 0
    backoff = 1.0
    log.info("MarketListener 自愈守护启动 (account=%s, max_restarts=%s)",
             args.account, args.max_restarts or "∞")

    while True:
        log.info("启动 MarketListener 子进程 (account=%s)", args.account)
        proc = subprocess.Popen(
            [args.python, str(script), str(args.account)],
            cwd=root,
        )
        try:
            code = proc.wait()
        except KeyboardInterrupt:
            log.info("收到退出信号，终止 listener 子进程")
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except Exception:
                proc.kill()
            return 0

        if code == 0:
            log.info("listener 干净退出 (code=0)，守护退出")
            return 0

        restarts += 1
        if args.max_restarts and restarts > args.max_restarts:
            log.error("达到最大重启次数 %s，放弃", args.max_restarts)
            return 1

        log.warning("listener 异常退出 (code=%s)，%.1fs 后重启（第 %s 次）",
                    code, backoff, restarts)
        time.sleep(backoff)
        backoff = min(backoff * 2, 30.0)


if __name__ == "__main__":
    sys.exit(main())
