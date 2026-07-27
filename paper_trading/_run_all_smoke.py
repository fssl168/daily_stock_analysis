# -*- coding: utf-8 -*-
"""Run all paper_trading smoke tests and print a summary."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

TESTS = [
    "paper_trading/_smoke_p2a.py",
    "paper_trading/_smoke_p2b.py",
    "paper_trading/_smoke_p3c_pm_agent.py",
    "paper_trading/_smoke_p3c_cancel_modify.py",
    "paper_trading/_smoke_p3c_battle_plan.py",
]


def main() -> int:
    results = []
    for t in TESTS:
        print(f"\n=== Running {t} ===")
        rc = subprocess.call([sys.executable, t], cwd=str(ROOT))
        results.append((t, rc))
    print("\n=== SUMMARY ===")
    all_ok = True
    for t, rc in results:
        status = "PASS" if rc == 0 else f"FAIL(rc={rc})"
        print(f"  {status:12s} {t}")
        if rc != 0:
            all_ok = False
    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
