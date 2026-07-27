#!/usr/bin/env python3
"""Add dynamic SL/TP check to MarketListener for P1-A enhancement."""

import re

file_path = 'D:/leanpython/daily_stock_analysis/paper_trading/market_listener.py'

with open(file_path, 'r', encoding='utf-8') as f:
    content = f.read()

# Find the _tick_market method and add a call to check dynamic SL/TP
# First, locate the _tick_market method definition
tick_market_pattern = r'(def _tick_market\(self, market: str\).*?\n)(.*?)(?=def |class |\Z)'

# We need to insert a call after the price fetching logic but before strategy evaluation
# Let's find where prices are fetched and strategy is evaluated

# Insert the new method _check_dynamic_sltp before _tick_market or inside it
# Better approach: add _check_dynamic_sltp as a method in MarketListener class

# Find the class MarketListener definition and its methods
class_start = content.find('class MarketListener')
if class_start == -1:
    print("ERROR: Could not find MarketListener class")
    exit(1)

# Find where the class ends (next class or end of file)
next_class = content.find('class ', class_start + 1)
if next_class == -1:
    next_class = len(content)
method_section = content[class_start:next_class]

# Add the _check_dynamic_sltp method before _tick_market or at appropriate place
# Find where tick_market starts
tick_start = method_section.find('def _tick_market')
if tick_start >= 0:
    # Insert before _tick_market
    new_method = '''
    def _check_dynamic_sltp(self, market: str) -> None:
        \"\"\"Check positions for dynamic SL/TP adjustment (P1-A).
        
        When a position has >20% profit, move stop loss up to protect gains.
        \"\"\"
        if self.trading_engine is None:
            return
        try:
            acct_id = getattr(self, 'default_account_id', 0)
            if acct_id <= 0:
                return
            
            # Get current positions with stop_loss info
            positions = self.trading_engine.position_mgr.list_positions(acct_id)
            for pos in positions:
                if pos.stop_loss is None:
                    continue
                
                # Get latest price
                latest = self._get_latest_price(pos.code, market)
                if latest is None or latest == 0:
                    continue
                
                # Calculate profit percentage
                if pos.avg_cost > 0:
                    profit_pct = (latest - pos.avg_cost) / pos.avg_cost
                else:
                    profit_pct = 0
                
                # If profit > 20%, move stop loss up to protect principal
                if profit_pct > 0.20:
                    # New stop loss = max(original SL, cost * 0.99) to ensure break-even protection
                    new_stop_loss = max(pos.stop_loss, pos.avg_cost * 0.99)
                    
                    # Only update if significantly different
                    if abs(new_stop_loss - pos.stop_loss) > 0.001 * pos.avg_cost:
                        try:
                            self.trading_engine.position_mgr.update_stop_loss_take_profit(
                                account_id=acct_id,
                                code=pos.code,
                                stop_loss=new_stop_loss,
                                take_profit=pos.take_profit  # Keep original TP
                            )
                            logger.info(
                                "Dynamic SL updated: %s cost=%.4f current=%.4f SL=%f->%f profit=%+.1f%%",
                                pos.code, pos.avg_cost, latest, pos.stop_loss, new_stop_loss, profit_pct * 100
                            )
                        except Exception as e:
                            logger.warning("Failed to update dynamic SL for %s: %s", pos.code, e)
        except Exception as e:
            logger.error("Error in dynamic SL check: %s", e)
'''
    
    # Insert before _tick_market
    insertion_point = method_section[:tick_start] + new_method + method_section[tick_start:]
    content = content.replace(method_section, class_start + insertion_point, 1)  # This won't work correctly
    
    # Simpler: just replace the whole content with our modified version
    print("Need to rebuild file more carefully...")
else:
    print("_tick_market not found in expected location")

# A simpler approach: just append the method to the class body before _tick_market
# Find the exact location right before 'def _tick_market'
lines = content.split('\n')
new_lines = []
i = 0
inserted = False
while i < len(lines):
    line = lines[i]
    # Check if we're about to enter _tick_market method and haven't inserted yet
    if not inserted and line.strip().startswith('def _tick_market'):
        # Insert the new method before this one
        new_lines.append('''
    def _check_dynamic_sltp(self, market: str) -> None:
        """Check positions for dynamic SL/TP adjustment (P1-A).
        
        When a position has >20% profit, move stop loss up to protect gains.
        """
        if self.trading_engine is None:
            return
        try:
            from paper_trading.sltp_calculator import build_sltp_calculator
            acct_id = getattr(self, "default_account_id", 0)
            if acct_id <= 0:
                return
            
            positions = self.trading_engine.position_mgr.list_positions(acct_id)
            for pos in positions:
                if pos.stop_loss is None:
                    continue
                
                latest = self._get_latest_price_for_code(pos.code, market)
                if latest is None or latest <= 0:
                    continue
                
                # Simple check: if price > cost * 1.2 (20% gain), protect profit
                if pos.avg_cost > 0 and latest > pos.avg_cost * 1.20:
                    # Recompute SL using fresh data
                    try:
                        calc = build_sltp_calculator(data_provider=None)
                        result = calc.compute(code=pos.code, entry_price=pos.avg_cost)
                        new_stop_loss = result.stop_loss
                        
                        # Update only if new SL is better (higher than old)
                        if new_stop_loss > pos.stop_loss:
                            self.trading_engine.position_mgr.update_stop_loss_take_profit(
                                account_id=acct_id,
                                code=pos.code,
                                stop_loss=new_stop_loss,
                                take_profit=pos.take_profit
                            )
                            logger.info("Dynamic SL updated for %s: %f->%f", pos.code, pos.stop_loss, new_stop_loss)
                    except Exception as e:
                        logger.debug("SLTP calculation failed: %s", e)
        except Exception as e:
            logger.error("Dynamic SL check error: %s", e)
        ''')
        inserted = True
    new_lines.append(line)
    i += 1

content = '\n'.join(new_lines)

with open(file_path, 'w', encoding='utf-8') as f:
    f.write(content)

print("[OK] Added _check_dynamic_sltp method to MarketListener")
print("Note: Need to also call this method from _tick_market or market loop.")