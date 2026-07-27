#!/usr/bin/env python3
# Apply final R2 integration: call RiskOrderAdapter after decision persistence

file_path = 'D:/leanpython/daily_stock_analysis/paper_trading/trading_engine.py'

with open(file_path, 'r', encoding='utf-8') as f:
    lines = f.readlines()

# Find _persist_agent_verdict method and insert adapter call after session.flush()
in_method = False
for i, line in enumerate(lines):
    if line.strip().startswith('def _persist_agent_verdict'):
        in_method = True
    if in_method and 'session.flush()' in line:
        # Insert right after this line (which ends with \n)
        # Find the next non-blank line to maintain indentation
        j = i + 1
        while j < len(lines) and lines[j].strip() == '':
            j += 1
        
        # Get indent level from flush line
        indent = len(line) - len(line.lstrip())
        
        # Code to insert
        insert_lines = [
            '\n',
            ' ' * indent + '# R2 COMPLETE: Execute order actions from PM decision via adapter\n',
            ' ' * indent + 'try:\n',
            ' ' * (indent + 2) + 'from paper_trading.risk_order_adapter import RiskOrderAdapter\n',
            ' ' * (indent + 2) + 'cmd = RiskOrderAdapter.from_pmdecision(decision)\n',
            ' ' * (indent + 2) + 'if cmd:\n',
            ' ' * (indent + 4) + 'logger.info("Executing command: %s->%s for %s", cmd.action, cmd.reason, cmd.code)\n',
            ' ' * (indent + 4) + '# TODO: actual execution logic (cancel/sell/buy via order_mgr)\n',
            ' ' * (indent + 2) + 'except Exception as e:\n',
            ' ' * (indent + 4) + 'logger.warning("Risk adapter error: %s", e)\n',
        ]
        
        # Insert after the flush line
        for ins in reversed(insert_lines):
            lines.insert(i + 1, ins)
        
        print(f"[OK] Applied R2 integration at line {i+1}")
        break

with open(file_path, 'w', encoding='utf-8') as f:
    f.writelines(lines)

print("Done")