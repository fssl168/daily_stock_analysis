#!/usr/bin/env python3
# Apply P1-A changes to the restored market_listener.py file

file_path = 'D:/leanpython/daily_stock_analysis/paper_trading/market_listener.py'

with open(file_path, 'r', encoding='utf-8') as f:
    content = f.read()

# Check if _check_dynamic_sltp already exists (from previous rebuild)
if '_check_dynamic_sltp' in content:
    print("Method already present - skipping duplicate")
else:
    # Find the best insertion point - after _maybe_run_daily_reflection method
    # Look for the line after _maybe_run_daily_reflection ends
    method_end_pattern = r'def _maybe_run_daily_reflection\(self, today: date\) -> None:[\s\S]*?(?=\n\s*def |\Z)'
    
    # Simpler: find _tick_market method and insert _check_dynamic_sltp before it,
    # or add it at the end of the class before any factory function
    
    # First, let's find where we should insert - right before the closing of the class
    # or after the last method
    lines = content.split('\n')
    
    # Find the last method definition before the factory function
    insert_idx = None
    for i, line in enumerate(lines):
        if line.strip().startswith('def build_default_listener'):
            # Insert just before this function, inside the class
            # Find the line before this which is part of the class (starts with spaces but not def)
            for j in range(i-1, -1, -1):
                if lines[j].strip() and not lines[j].strip().startswith('def ') and not lines[j].strip().startswith('@'):
                    insert_idx = j + 1  # insert after this method/body
                    break
            if insert_idx is not None:
                break
    
    if insert_idx is None:
        # Fallback: insert at the end of the class before any top-level code
        insert_idx = len(lines)
    
    # The new method to insert (P1-A)
    new_method = '''
    # -----------------------------------------------------------------------
    # P1-A: Dynamic SL/TP check hook
    # -----------------------------------------------------------------------

    def _check_dynamic_sltp(self, market: str) -> None:
        """Check positions for dynamic SL/TP adjustment.

        When a position has profit exceeding sltp_dynamic_threshold_pct,
        recalculate stop loss using fresh SLTP data and update position.
        """
        from paper_trading.sltp_calculator import build_sltp_calculator

        if self.trading_engine is None:
            return

        acct_id = self.default_account_id
        if acct_id <= 0:
            return

        try:
            positions = self.trading_engine.position_mgr.list_positions(acct_id)
            for pos in positions:
                if pos.stop_loss is None:
                    continue

                latest = self._get_latest_price_for_code(pos.code, market)
                if latest is None or latest <= 0:
                    continue

                avg_cost = getattr(pos, "avg_cost", 0)
                if avg_cost <= 0:
                    continue

                profit_ratio = (latest - avg_cost) / avg_cost
                threshold = self.sltp_dynamic_threshold_pct / 100.0

                if profit_ratio >= threshold:
                    try:
                        calc = build_sltp_calculator(data_provider=None)
                        result = calc.compute(code=pos.code, entry_price=avg_cost)
                        new_stop_loss = result.stop_loss

                        if new_stop_loss > pos.stop_loss:
                            self.trading_engine.position_mgr.update_stop_loss_take_profit(
                                account_id=acct_id,
                                code=pos.code,
                                stop_loss=new_stop_loss,
                                take_profit=getattr(pos, "take_profit", None),
                            )
                            logger.info(
                                "Dynamic SL updated: %s cost=%.4f current=%.4f SL=%f->%f profit=%+.1f%%",
                                pos.code, avg_cost, latest, pos.stop_loss, new_stop_loss, profit_ratio * 100,
                            )
                    except Exception as e:
                        logger.debug("SLTP calculation failed for %s: %s", pos.code, e)
        except Exception as exc:
            logger.error("Dynamic SL/TP check failed: %s", exc)

    def _get_latest_price_for_code(self, code: str, market: str) -> Optional[float]:
        """Helper to get latest price for a specific code."""
        return self._get_latest_price(code, market)
'''
    lines.insert(insert_idx, new_method)
    fixes_applied.append("Added _check_dynamic_sltp method to MarketListener")

# Ensure sltp_dynamic_threshold_pct attribute exists in __init__
init_pattern = r'def __init__\(self.*?\n.*?self\.running\s*=\s*False'
# Look for existing assignment to self.sltp_dynamic_threshold_pct
if 'self.sltp_dynamic_threshold_pct' not in content:
    # Find the __init__ method body and insert the attribute there
    lines_with_init = []
    in_init = False
    init_inserted = False
    for i, line in enumerate(lines):
        if 'def __init__' in line and 'MarketListener' in lines[i-1] if i > 0 else True:
            in_init = True
        if in_init and line.strip().startswith('self._running = False'):
            lines_with_init.append(line)
            lines_with_init.append('        # P1-A: Dynamic SL/TP threshold percentage (default 20%)\n')
            lines_with_init.append('        self.sltp_dynamic_threshold_pct = 20.0\n')
            init_inserted = True
            continue
        lines_with_init.append(line)
    
    if init_inserted:
        lines = lines_with_init
        fixes_applied.append("Added sltp_dynamic_threshold_pct to __init__")
    else:
        fixes_applied.append("Could not add attribute to __init__ - seeking alternative location")

content = '\n'.join(lines)

# Also add the threshold parameter reading in build_default_listener
build_marker = 'def build_default_listener('
if build_marker in content:
    # Find the line where default_account_id is assigned and add config reading after it
    lines = content.split('\n')
    new_lines = []
    i = 0
    while i < len(lines):
        line = lines[i]
        new_lines.append(line)
        if 'listener.sltp_dynamic_threshold_pct =' in line or ('sltp_dynamic_threshold_pct' in line and '=' in line and 'config' in content[max(0,i-5):i]):
            # This is where we set it - ensure it reads from config
            pass
        i += 1
    
    # Simple approach: add the config reading line before the return statement
    # Find "return listener" and insert before it
    for i, line in enumerate(lines):
        if line.strip().startswith('return listener'):
            # Insert before return
            lines.insert(i, '    listener.sltp_dynamic_threshold_pct = float(getattr(config, "paper_trading_sltp_dynamic_threshold_pct", 20.0))\n')
            fixes_applied.append("Added config-based threshold setting in build_default_listener")
            break

content = '\n'.join(lines)

with open(file_path, 'w', encoding='utf-8') as f:
    f.write(content)

print("\n=== MarketListener P1-A Updates Applied ===")
for f in fixes_applied:
    print(f"  - {f}")