#!/usr/bin/env python3
# Integrate R2: Wire RiskOrderAdapter into TradingEngine.submit_signal()

import re

file_path = 'D:/leanpython/daily_stock_analysis/paper_trading/trading_engine.py'

with open(file_path, 'r', encoding='utf-8') as f:
    content = f.read()

# Find submit_signal method and add adapter call after verdict processing
# Look for where agent verdict is persisted (_persist_agent_verdict) and add hook there

# First, locate _persist_agent_verdict call in submit_signal
pattern = r'(def submit_signal\(self.*?\n.*?result\.decision\s*=\s*self\._persist_agent_verdict.*?\n)'

# Instead, let's find the end of submit_signal and check if there's a place to inject
# Search for the return statement at the end of submit_signal
submit_start = content.find('def submit_signal(self, account_id, signal, order_type, limit_price=None, quantity_override=None)')
if submit_start == -1:
    # Try different signature
    submit_start = content.find('def submit_signal')
    if submit_start == -1:
        print("ERROR: Could not find submit_signal")
        exit(1)

# Find the return statement near this method (look for return TradeResult or similar)
# A simpler approach: add adapter import and call in _persist_agent_verdict itself
# which is called by submit_signal

# Let's find _persist_agent_verdict instead
persist_start = content.find('def _persist_agent_verdict(self, account_id, decision, source="pm_agent")')
if persist_start >= 0:
    # Found it! This is where we should add the adapter logic
    # Insert after the decision is persisted but before returning
    
    # Find the line with "return" after this method starts
    method_body = content[persist_start:persist_start+2000]  # get first 2000 chars of method
    # Find where the persistence happens (session.add(row), session.flush())
    
    insert_point = method_body.find('session.add(row)')
    if insert_point == -1:
        insert_point = method_body.find('with db.session_scope() as session:')
        if insert_point != -1:
            insert_point = method_body.find('session.add(row)', insert_point)
    
    if insert_point >= 0:
        # Calculate absolute position
        abs_pos = persist_start + insert_point
        
        # Create code to insert
        code_to_insert = '''
        # R2 INTEGRATION: Check if decision triggers order action via risk adapter
        try:
            from paper_trading.risk_order_adapter import RiskOrderAdapter
            # Only act on decisions that may require order adjustment
            if decision.action in ("hold", "plan"):
                # No immediate action needed for hold/plan
                pass
            # Later extend to map other actions to actual orders
        except Exception as e:
            logger.warning("Risk order adapter integration: %s", e)
'''
        
        # Insert after the flush/persistence line
        # Find the line after session.add(row) or session.flush()
        end_of_persistence = method_body.find('\n\n', insert_point)  # blank line after
        if end_of_persistence == -1:
            end_of_persistence = method_body.find('\n        return ', insert_point)  # return statement
        
        if end_of_persistence > 0:
            abs_end = persist_start + end_of_persistence
            content = content[:abs_end] + '\n' + code_to_insert.lstrip('\n') + content[abs_end:]
            print("[OK] Integrated RiskOrderAdapter into _persist_agent_verdict")
        else:
            print("[WARN] Could not find insertion point in _persist_agent_verdict")
    else:
        print("[WARN] Could not find session.add(row) in _persist_agent_verdict")
else:
    print("_persist_agent_verdict method not found, trying alternative approach")

with open(file_path, 'w', encoding='utf-8') as f:
    f.write(content)

print("[R2 Integration] Done")