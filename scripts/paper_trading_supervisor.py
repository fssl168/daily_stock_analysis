#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""MarketListener 进程级自愈守护（方案 2）+ WebSocket push 行情源.

监控行情监听器子进程，异常退出自动重启（指数退避 1s→30s，封顶 30s），
干净退出（code=0 / 收到 SIGINT）不重启。配合系统计划任务（开机自启）即可
实现"常驻 + 自愈"。

行情源（方案 3）:
  - 启动子进程时注入 ``PAPER_TRADING_WS_QUOTE_URL``（优先 ``--ws-url``，
    否则读项目根 ``.env`` 的同名变量），listener 即启用 WebSocket push
    （push 优先、轮询兜底）。
  - 未配置时保持轮询模式（listener 正常降级，不报错）。

用法:
  python scripts/paper_trading_supervisor.py [--account 2] [--max-restarts 0] [--ws-url <wss://...>]
  0 = 无限重启；配合 Windows 任务计划 / systemd 开机自启即可常驻。
"""
from __future__ import annotations

import argparse
import logging
import os
import subprocess
import sys
import time
from pathlib import Path

log = logging.getLogger("supervisor")


def _load_dotenv(root: Path) -> dict:
    """Minimal KEY=VALUE .env parser (no third-party dependency)."""
    env = {}
    path = root / ".env"
    if not path.exists():
        return env
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        env[key.strip()] = value.strip().strip('"').strip("'")
    return env


def main() -> int:
    parser = argparse.ArgumentParser(description="MarketListener 自愈守护")
    parser.add_argument("--account", type=int, default=2, help="纸面账户 ID")
    parser.add_argument("--max-restarts", type=int, default=0,
                        help="最大重启次数（0=无限）")
    parser.add_argument("--python", default=sys.executable, help="Python 解释器")
    parser.add_argument(
        "--ws-url", default="",
        help="行情 WebSocket URL（如 Longbridge wss://...）。优先于 .env 的 "
             "PAPER_TRADING_WS_QUOTE_URL；不传则读 .env。留空=轮询",
    )
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

    # 行情源：--ws-url > .env 的 PAPER_TRADING_WS_QUOTE_URL > 轮询
    dotenv = _load_dotenv(root)
    ws_url = (args.ws_url or dotenv.get("PAPER_TRADING_WS_QUOTE_URL", "")).strip()
    child_env = dict(os.environ)
    if ws_url:
        child_env["PAPER_TRADING_WS_QUOTE_URL"] = ws_url
        log.info("行情源: WebSocket push (%s)", ws_url[:80])
    else:
        child_env.pop("PAPER_TRADING_WS_QUOTE_URL", None)
        log.info("未配置 PAPER_TRADING_WS_QUOTE_URL，行情走轮询（push 优先/轮询兜底）")

    restarts = 0
    backoff = 1.0  # 指数退避：1s → 2 → 4 → … → 30s 封顶
    log.info("MarketListener 自愈守护启动 (account=%s, max_restarts=%s)",
             args.account, args.max_restarts or "∞")

    while True:
        log.info("启动 MarketListener 子进程 (account=%s)", args.account)
        proc = subprocess.Popen(
            [args.python, str(script), str(args.account)],
            cwd=root,
            env=child_env,
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
