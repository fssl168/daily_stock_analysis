with open('D:/leanpython/daily_stock_analysis/src/agent/portfolio_manager_agent.py', 'r', encoding='utf-8') as f:
    content = f.read()

# Find _handle_modify_order and show its structure
import re
match = re.search(r'def _handle_modify_order\(\*\*kwargs\) -> dict:[\s\S]*?except Exception as exc:', content, re.MULTILINE)
if match:
    print(match.group()[:800])
else:
    print("Could not find full function")
    # Just show lines around the area
    lines = content.split('\n')
    for i, line in enumerate(lines):
        if '_handle_modify_order' in line:
            print(f"Line {i+1}: {line}")
            for j in range(i, min(i+30, len(lines))):
                print(f"{j+1}: {repr(lines[j])}")
            break