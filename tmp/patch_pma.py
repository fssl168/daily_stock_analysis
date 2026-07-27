import sys

with open('src/agent/portfolio_manager_agent.py', 'r', encoding='utf-8') as f:
    lines = f.readlines()

# Find where reflections line is (the one that calls _fetch_reflections_summary)
reflines_idx = None
for i, line in enumerate(lines):
    if '_fetch_reflections_summary' in line and 'def _fetch_reflections_summary' not in line:
        reflines_idx = i
        break

if reflines_idx is None:
    print("Could not find reflections call line")
    sys.exit(1)

# Find net_value line after the block (this is around line 330-350)
netval_idx = None
for i in range(reflines_idx + 1, len(lines)):
    if 'net_value = float(snapshot.get("net_value"' in lines[i]:
        netval_idx = i
        break

if netval_idx is None:
    print("Could not find net_value line")
    sys.exit(1)

print(f"Found reflections at line {reflines_idx+1}, net_value at line {netval_idx+1}")

# Replace from reflines_idx to netval_idx with new code including injection of _inject_reflections
new_block = '''        snapshot = self._fetch_account_snapshot(account_id)
        positions = self._fetch_positions_summary(account_id)
        # Inject reflections via _inject_reflections (P0-E memory loop).
        reflections = self._inject_reflections(account_id)

        net_value = float(snapshot.get("net_value", 1.0)) if snapshot else 1.0
'''
lines[reflines_idx:netval_idx] = [new_block]

# Now insert the _inject_reflections method before _fetch_account_snapshot
fetchacc_idx = None
for i, line in enumerate(lines):
    if line.strip().startswith('def _fetch_account_snapshot'):
        fetchacc_idx = i
        break

if fetchacc_idx is None:
    print("Could not find _fetch_account_snapshot")
    sys.exit(1)

inject_method = '''\n    def _inject_reflections(self, account_id):\n        \"\"\"Inject reflection memory into decision context (P0-E gap fill).\"\"\"\n        if self.reflection_engine is None:\n            return \"(复盘系统未启用)\"\n        try:\n            acct_id = int(account_id) if account_id else self.account_id\n            recent = self.reflection_engine.get_recent_notes(limit=3, account_id=acct_id)\n            code_reflections = []\n            if self.trading_engine is not None:\n                rows = self.trading_engine.position_mgr.list_positions(acct_id)\n                held_codes = [r.code for r in rows if r.code][:5]\n                for code in held_codes:\n                    notes_for_code = self.reflection_engine.get_relevant_notes(code=code, limit=1, account_id=acct_id)\n                    code_reflections.extend(notes_for_code)\n            seen_ids = set()\n            merged = []\n            for n in list(recent) + list(code_reflections):\n                rid = getattr(n, \"row_id\", None)\n                if rid is not None and rid in seen_ids:\n                    continue\n                if rid is not None:\n                    seen_ids.add(rid)\n                merged.append(n)\n            if not merged:\n                return \"(暂无复盘笔记)\"\n            lines_list = []\n            for n in merged:\n                ts = getattr(n, \"created_at\", None)\n                ts_str = ts.strftime(\"%Y-%m-%d %H:%M\") if ts else \"?\"\n                scope = getattr(n, \"scope\", \"?\")\n                code = getattr(n, \"code\", None) or \"\"\n                code_tag = \"[{}] \".format(code) if code else \"\"\n                takeaway = getattr(n, \"takeaway\", \"\") or getattr(n, \"summary\", \"\") or \"(无 takeaway)\"\n                lines_list.append(\"- [{ts}][{scope}] {code_tag}{takeaway}\".format(ts=ts_str, scope=scope, code_tag=code_tag, takeaway=takeaway))\n            return chr(10).join(lines_list)\n        except Exception as exc:\n            return \"(复盘笔记查询失败: {})\".format(exc)\n'''
lines.insert(fetchacc_idx, inject_method)

with open('src/agent/portfolio_manager_agent.py', 'w', encoding='utf-8') as f:
    f.writelines(lines)

print("SUCCESS: Patched file")
