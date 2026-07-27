with open('D:/leanpython/daily_stock_analysis/src/agent/portfolio_manager_agent.py', 'r', encoding='utf-8') as f:
    lines = f.readlines()

# Find all functions before _handle_modify_order to check their structure
print("Functions before line 896 (_handle_modify_order):")
for i in range(max(0, 800), 896):
    line = lines[i]
    if line.strip().startswith('def ') and not line.strip().startswith('def _'):
        # This might be a top-level function or method within a class
        pass
    
# Look for the class definition that contains this method
in_class = False
class_start = None
for i in range(max(0, 850), -1, -1):
    if lines[i].strip().startswith('class '):
        in_class = True
        class_start = i
        break

if in_class:
    print(f"\nClass starts at line {class_start+1}: {lines[class_start].strip()}")
    # Check indentation of methods inside this class
    for i in range(class_start, min(len(lines), 1000)):
        if lines[i].strip().startswith('def ') and i > class_start:
            # Method found - check its indentation level
            indent = len(lines[i]) - len(lines[i].lstrip())
            print(f"  Line {i+1}: def with indent={indent}")
            if '_handle_modify_order' in lines[i]:
                print("    *** THIS IS OUR METHOD ***")
                # Show next few lines to see structure
                for j in range(i, min(i+30, len(lines))):
                    lj = len(lines[j]) - len(lines[j].lstrip())
                    print(f"      {j+1}: indent={lj:2} {lines[j].rstrip()}[:200]")
                break
else:
    print("No enclosing class found near _handle_modify_order")

# Also check the function immediately preceding _handle_modify_order
prev_line_idx = None
for i in range(890, 896):
    if lines[i].strip().startswith('def '):
        prev_line_idx = i
        break

if prev_line_idx is not None:
    print(f"\nFunction before _handle_modify_order at line {prev_line_idx+1}: {lines[prev_line_idx].strip()}")
    # Show its end
    base_indent = len(lines[prev_line_idx]) - len(lines[prev_line_idx].lstrip())
    for j in range(prev_line_idx+1, min(len(lines), prev_line_idx+50)):
        if lines[j].strip() and (len(lines[j]) - len(lines[j].lstrip())) <= base_indent:
            print(f"  Function ends around line {j+1}")
            break