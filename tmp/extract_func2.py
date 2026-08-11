import sys

with open('src/agent/portfolio_manager_agent.py', 'r', encoding='utf-8') as f:
    lines = f.readlines()

# Find start line
start_line = None
for i, line in enumerate(lines):
    if 'register_paper_trading_tools' in line and line.strip().startswith('def '):
        start_line = i
        break

if start_line is None:
    print('Function not found')
    sys.exit(1)

# Get the indentation of the function definition (spaces before 'def')
def_indent = len(lines[start_line]) - len(lines[start_line].lstrip())
print(f'Def indent: {def_indent}')

# Find end - next line with same or less indent that starts a top-level construct
end = None
for i in range(start_line + 1, len(lines)):
    stripped = lines[i].lstrip()
    if stripped and not stripped.startswith(' ') and not stripped.startswith('\t'):
        # No leading whitespace, likely module level? Too strict.
        pass
    # Count leading spaces
    leading = len(lines[i]) - len(lines[i].lstrip())
    if leading <= def_indent and stripped and (stripped.startswith('def ') or stripped.startswith('class ') or stripped.startswith('#')):
        # This is a new top-level item
        # But we need to make sure it's actually after the function body ends
        # Check if previous line was blank or just ended a block
        end = i
        break

if end is None:
    # Fallback: find by counting braces in the content
    content = ''.join(lines)
    start_pos = content.find('def register_paper_trading_tools')
    # Simple brace counting from start
    brace = 0
    pos = start_pos
    while pos < len(content):
        if content[pos] == '(':
            brace += 1
        elif content[pos] == ')':
            brace -= 1
        if brace == 0 and pos > start_pos:
            break
        pos += 1
    # Now find the next newline after this position
    end_line = content.count('\n', 0, pos) + 1
    end = end_line

print(f'End line: {end}')
print(f'Functions spans lines {start_line+1} to {end}')
for j in range(start_line, min(end, len(lines))):
    sys.stdout.write(lines[j])
