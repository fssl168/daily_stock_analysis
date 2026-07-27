#!/usr/bin/env python3
"""Simple update script for market_listener.py to add dynamic SL/TP check."""

import re

file_path = 'D:/leanpython/daily_stock_analysis/paper_trading/market_listener.py'

with open(file_path, 'r', encoding='utf-8') as f:
    lines = f.readlines()

# Find the line number for _tick_market method
tick_method_line = None
for i, line in enumerate(lines):
    if line.strip().startswith('def _tick_market'):
        tick_method_line = i
        break

if tick_method_line is None:
    print("ERROR: Could not find _tick_market method")
    exit(1)

# The new method to insert (before _tick_market)
new_method_lines = [
    '\n',
    '    def _check_dynamic_sltp(self, market: str) -> None:\n',
    '        """Check positions for dynamic SL/TP adjustment (P1-A).\n',
    '        \n',
    '        When a position has >20% profit, move stop loss up to protect gains.\n',
    '        """\n',
    '        if self.trading_engine is None:\n',
    '            return\n',
    '        try:\n',
    '            acct_id = getattr(self, "default_account_id", 0)\n',
    '            if acct_id <= 0:\n',
    '                return\n',
    '            \n',
    '            # Get current positions\n',
    '            positions = self.trading_engine.position_mgr.list_positions(acct_id)\n',
    '            for pos in positions:\n',
    '                if pos.stop_loss is None:\n',
    '                    continue\n',
    '                \n',
    '                # Get latest price\n',
    '                latest = self._get_latest_price_for_code(pos.code, market)\n',
    '                if latest is None or latest <= 0:\n',
    '                    continue\n',
    '                \n',
    '                # Check if profit > 20%\n',
    '                if pos.avg_cost > 0 and latest > pos.avg_cost * 1.20:\n',
    '                    # Compute new SL using SLTP calculator\n',
    '                    try:\n',
    '                        from paper_trading.sltp_calculator import build_sltp_calculator\n',
    '                        calc = build_sltp_calculator(data_provider=None)\n',
    '                        result = calc.compute(code=pos.code, entry_price=pos.avg_cost)\n',
    '                        new_stop_loss = result.stop_loss\n',
    '                        \n',
    '                        # Update if new SL is higher (better protection)\n',
    '                        if new_stop_loss > pos.stop_loss:\n',
    '                            self.trading_engine.position_mgr.update_stop_loss_take_profit(\n',
    '                                account_id=acct_id,\n',
    '                                code=pos.code,\n',
    '                                stop_loss=new_stop_loss,\n',
    '                                take_profit=pos.take_profit\n',
    '                            )\n',
    '                            logger.info("Dynamic SL updated for %s: %f->%f", pos.code, pos.stop_loss, new_stop_loss)\n',
    '                    except Exception as e:\n',
    '                        logger.debug("SLTP calculation failed: %s", e)\n',
    '        except Exception as e:\n',
    '            logger.error("Dynamic SL check error: %s", e)\n',
    '\n',
]

# Insert before _tick_market
lines.insert(tick_method_line, ''.join(new_method_lines))

# Also need to call this method from _tick_market - find the body of _tick_market
# and add a call there
in_tick_method = False
tick_method_end = None
for i in range(tick_method_line, len(lines)):
    if lines[i].strip().startswith('def _tick_market'):
        in_tick_method = True
    if in_tick_method and i > tick_method_line:
        # Check if this line is at indent level 4 (meaning end of method)
        # or if we see another def/class at top level
        if lines[i].strip().startswith('def ') and i > tick_method_line + 5:
            tick_method_end = i
            break
        if lines[i].strip().startswith('class ') and i > tick_method_line + 5:
            tick_method_end = i
            break

if tick_method_end is None:
    # Find by indentation - look for lines that start with less indentation than the method body
    base_indent = len(lines[tick_method_line]) - len(lines[tick_method_line].lstrip())
    for i in range(tick_method_line + 1, len(lines)):
        if lines[i].strip():  # non-empty line
            indent = len(lines[i]) - len(lines[i].lstrip())
            if indent <= base_indent and i > tick_method_line + 10:  # must be after some method body
                tick_method_end = i
                break
    if tick_method_end is None:
        tick_method_end = len(lines)

# Now find where to insert the call inside _tick_body - ideally after getting prices
call_insert = tick_method_end
for i in range(tick_method_line, min(tick_method_end, tick_method_line + 50)):
    if '_fetch_latest_prices' in lines[i] or 'latest_prices' in lines[i]:
        # Insert right after this block
        call_insert = i + 1
        break

# Insert the call: self._check_dynamic_sltp(market)
call_line = '            self._check_dynamic_sltp(market)\n'
lines.insert(call_insert, call_line)

with open(file_path, 'w', encoding='utf-8') as f:
    f.writelines(lines)

print("[OK] Updated market_listener.py:")
print("  - Added _check_dynamic_sltp method")
print("  - Added call in _tick_market method")