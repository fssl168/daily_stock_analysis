import sys

with open('src/agent/portfolio_manager_agent.py', 'r', encoding='utf-8') as f:
    lines = f.readlines()

# Find the function end - next def after register_paper_trading_tools that is at top level (no indent)
start_idx = None
for i, line in enumerate(lines):
    if line.strip().startswith('def register_paper_trading_tools'):
        start_idx = i
        break

if start_idx is None:
    print('Function not found')
    sys.exit(1)

# Find the line containing 'logger.info("paper_trading_tools")' or similar which is near the end of the function
insert_idx = None
for i in range(start_idx, len(lines)):
    if 'paper_trading_tools' in lines[i] and 'logger' in lines[i]:
        insert_idx = i
        break

if insert_idx is None:
    # Fallback: find the last registry.register block and add after it
    for i in range(len(lines)-1, -1, -1):
        if 'registry.register(ToolDefinition' in lines[i] and 'paper_trading_get_recent_reflections' in lines[i-1]:
            insert_idx = i + 1
            break

if insert_idx is None:
    print('Could not find insertion point')
    sys.exit(1)

print(f'Inserting at line {insert_idx+1}')

# New tool code to add
new_code = '''\n    # ---- Compute SLTP (smart stop-loss/take-profit) ----\n    def _handle_compute_sltp(**kwargs) -> dict:\n        """Compute the three-line exit plan for a position (P1-A).\"\"\"\n        try:\n            from paper_trading.sltp_calculator import build_sltp_calculator\n            calc = build_sltp_calculator(data_provider=None)\n            entry_price = float(kwargs.get(\"entry_price\", 0.0))\n            code = str(kwargs.get(\"code\", \"\")).strip()\n            if entry_price <= 0 or not code:\n                return {\"error\": \"entry_price and code required\"}\n            result = calc.compute(code=code, entry_price=entry_price)\n            return {\n                \"stop_loss\": result.stop_loss,\n                \"take_profit_1\": result.take_profit_1,\n                \"take_profit_2\": result.take_profit_2,\n                \"entry_price\": result.entry_price,\n            }\n        except Exception as exc:\n            return {\"error\": f\"{type(exc).__name__}: {exc}\"}\n\n    registry.register(ToolDefinition(\n        name=\"paper_trading_compute_sltp\",\n        description=\"Compute the smart stop-loss/take-profit three-line exit plan.\",\n        parameters=[\n            ToolParameter(name=\"code\", type=\"string\", description=\"Stock code\", required=True),\n            ToolParameter(name=\"entry_price\", type=\"number\", description=\"Entry price\", required=True),\n        ],\n        handler=_handle_compute_sltp,\n        category=\"data\",    ))\n'''

lines.insert(insert_idx, new_code)

with open('src/agent/portfolio_manager_agent.py', 'w', encoding='utf-8') as f:
    f.writelines(lines)

print('SUCCESS: Added SLTP tool registration')
