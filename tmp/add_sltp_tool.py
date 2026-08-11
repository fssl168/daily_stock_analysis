import sys

with open('src/agent/portfolio_manager_agent.py', 'r', encoding='utf-8') as f:
    lines = f.readlines()

# Find the end of register_paper_trading_tools function (just before "def build_portfolio_manager_agent")
target_line = 'def build_portfolio_manager_agent'
end_idx = None
for i, line in enumerate(lines):
    if target_line in line and line.strip().startswith('def '):
        end_idx = i
        break

if end_idx is None:
    print('Could not find function end marker')
    sys.exit(1)

print(f'Function ends at line {end_idx+1}')

# The new SLTP tool code to insert before the logger.info statement at the end of the function
# Find the logger.log line near the end of the function
logger_idx = None
# First find the start of the function to know where it begins
start_line = None
for i, line in enumerate(lines):
    if 'register_paper_trading_tools' in line and line.strip().startswith('def '):
        start_line = i
        break
if start_line is None:
    print('Could not find function start')
    sys.exit(1)
logger_idx = None
for i in range(end_idx - 20, end_idx):
    if 'logger.info' in lines[i] and 'Registered' in lines[i]:
        logger_idx = i
        break

if logger_idx is None:
    print('Could not find logger.info line')
    # Fallback: insert before the end of function
    logger_idx = end_idx - 1

# Build the new tool handler and registration
sltp_tool_code = '''\n    # ---- Compute SLTP (smart stop-loss/take-profit) ----\n    def _handle_compute_sltp(**kwargs) -> dict:\n        \"\"\"Compute the three-line exit plan for a position (P1-A gap fill).\"\"\"\n        try:\n            from paper_trading.sltp_calculator import build_sltp_calculator\n            calc = build_sltp_calculator(data_provider=None)\n            # We need entry_price and code; fallback to current market price if needed\n            entry_price = float(kwargs.get(\"entry_price\", 0.0))\n            code = str(kwargs.get(\"code\", \"\")).strip()\n            if entry_price <= 0 or not code:\n                return {\"error\": \"entry_price and code required\"}\n            result = calc.compute(code=code, entry_price=entry_price)\n            return {\n                \"stop_loss\": result.stop_loss,\n                \"take_profit_1\": result.take_profit_1,\n                \"take_profit_2\": result.take_profit_2,\n                \"entry_price\": result.entry_price,\n            }\n        except Exception as exc:\n            return {\"error\": f\"{type(exc).__name__}: {exc}\"}\n\n    registry.register(ToolDefinition(\n        name=\"paper_trading_compute_sltp\",\n        description=\"Compute the smart stop-loss/take-profit three-line exit plan for a stock.\",\n        parameters=[\n            ToolParameter(name=\"code\", type=\"string\", description=\"Stock code (e.g., 600519)\", required=True),\n            ToolParameter(name=\"entry_price\", type=\"number\", description=\"Entry price for calculating SL/TP\", required=True),\n        ],\n        handler=_handle_compute_sltp,\n        category=\"data\",    ))\n'''

# Insert before the logger.info line
lines.insert(logger_idx, sltp_tool_code)

# Also update the docstring to include the new tool
doc_start = None
for i in range(start_line, end_idx):
    if '\"\"\"Register' in lines[i]:
        doc_start = i
        break
if doc_start is None:
    print('Could not find docstring')
else:
    # Find the line with \"Tools registered:\" list
    list_start = None
    for i in range(doc_start, end_idx):
        if 'Tools registered:' in lines[i]:
            list_start = i
            break
    if list_start is None:
        list_start = doc_start + 10  # fallback
    # Insert paper_trading_compute_sltp into the list
    # Find the line with the last tool entry
    last_tool_line = None
    for i in range(list_start, end_idx):
        if 'paper_trading_get_recent_reflections' in lines[i]:
            last_tool_line = i
            break
    if last_tool_line is None:
        last_tool_line = list_start + 10

# Actually simpler: just modify the docstring by replacing the Tools registered block
content = ''.join(lines)
# Replace the comment listing tools
old_tools = '''    Tools are closures over the TradingEngine + account_id, so each tool
call automatically targets the PM agent's account. Re-registering a
tool replaces the handler (idempotent).

    Tools registered:
    - paper_trading_get_account_snapshot
    - paper_trading_get_positions
    - paper_trading_get_open_orders
    - paper_trading_place_order
    - paper_trading_cancel_order
    - paper_trading_modify_order
    - paper_trading_get_recent_reflections (no-op if reflection_engine is None)
'''
new_tools = '''    Tools are closures over the TradingEngine + account_id, so each tool
call automatically targets the PM agent's account. Re-registering a
tool replaces the handler (idempotent).

    Tools registered:
    - paper_trading_get_account_snapshot
    - paper_trading_get_positions
    - paper_trading_get_open_orders
    - paper_trading_place_order
    - paper_trading_cancel_order
    - paper_trading_modify_order
    - paper_trading_get_recent_reflections (no-op if reflection_engine is None)
    - paper_trading_compute_sltp (new: compute three-line SL/TP plan)
'''

if old_tools in content:
    content = content.replace(old_tools, new_tools)
    lines = content.split('\n')
    # Write back
    with open('src/agent/portfolio_manager_agent.py', 'w', encoding='utf-8') as f:
        f.writelines(lines)
    print('Updated docstring')
else:
    print('Old tools text not found, skipping docstring update')

# Now write the modified lines with the inserted tool
with open('src/agent/portfolio_manager_agent.py', 'w', encoding='utf-8') as f:
    f.writelines(lines)

print('Added paper_trading_compute_sltp tool to register_paper_trading_tools')
