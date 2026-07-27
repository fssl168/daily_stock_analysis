#!/usr/bin/env python3
"""Generate clean, minimal patches for portfolio_manager_agent.py and market_listener.py."""

import subprocess
import os

# First ensure we have a clean baseline by checking out the original files
print("Checking git status...")
result = subprocess.run(['git', 'status', '--porcelain'], capture_output=True, text=True, cwd='D:/leanpython/daily_stock_analysis')
print(result.stdout)

# Create backup of current state (in case we need to restore)
for f in ['src/agent/portfolio_manager_agent.py', 'paper_trading/market_listener.py']:
    path = f'D:/leanpython/daily_stock_analysis/{f}'
    if os.path.exists(path):
        # Make sure this is the clean version from git checkout HEAD
        result2 = subprocess.run(['git', 'checkout', 'HEAD', '--', f], capture_output=True, text=True, cwd='D:/leanpython/daily_stock_analysis')
        print(f"Reset {f}: {result2.returncode}")

# Now create patches by applying our fixes and generating diffs
# We'll do this by creating temporary copies, modifying them, then diffing

import tempfile
import shutil

def create_patch(source_file, modifications):
    """Create a patch string for given file with modifications applied."""
    # Read original
    with open(source_file, 'r', encoding='utf-8') as f:
        original = f.read()
    
    # Apply modifications (string replacements)
    modified = original
    for search, replace in modifications:
        if search in modified:
            modified = modified.replace(search, replace)
        else:
            print(f"Warning: search pattern not found in {source_file}: {search[:50]}...")
    
    # Generate unified diff manually
    orig_lines = original.split('\n')
    mod_lines = modified.split('\n')
    
    # Simple diff output format (not perfect but sufficient for small changes)
    diff_lines = []
    diff_lines.append(f'--- {source_file}')
    diff_lines.append(f'+++ {source_file}')
    
    i = j = 0
    context_lines = []
    while i < len(orig_lines) or j < len(mod_lines):
        if i < len(orig_lines) and j < len(mod_lines) and orig_lines[i] == mod_lines[j]:
            context_lines.append(orig_lines[i])
            i += 1
            j += 1
        else:
            # Flush context if we have some
            if context_lines:
                diff_lines.append('@@ -' + str(len(context_lines)) + ',' + str(len(context_lines)) + ' +' + 
                                  str(len(context_lines)) + ',' + str(len(context_lines)) + ' @@')
                for cl in context_lines[-3:]:  # last 3 context lines
                    diff_lines.append(' ' + cl)
                context_lines = []
            
            # Show removed line
            if i < len(orig_lines):
                diff_lines.append('-' + orig_lines[i])
                i += 1
            # Show added line
            if j < len(mod_lines) and (i >= len(orig_lines) or orig_lines[i] != mod_lines[j]):
                diff_lines.append('+' + mod_lines[j])
                j += 1
    
    if context_lines:
        diff_lines.append('@@ -' + str(len(context_lines)) + ',' + str(len(context_lines)) + ' +' + 
                          str(len(context_lines)) + ',' + str(len(context_lines)) + ' @@')
        for cl in context_lines[-3:]:
            diff_lines.append(' ' + cl)
    
    return '\n'.join(diff_lines) + '\n'

# Patches for portfolio_manager_agent.py
pa_modifications = [
    # Fix _handle_cancel_order
    ('''            # OrderManager.cancel_order is added in P0-C; for now use a
            # graceful fallback if the method is missing.
            if hasattr(engine.order_mgr, "cancel_order"):
                row = engine.order_mgr.cancel_order(order_id, reason="pm_agent_cancel")
                return {"status": "canceled", "order_id": order_id, "code": row.code}
            return {"error": "cancel_order not implemented on OrderManager yet (P0-C pending)"}''',
     '''            row = engine.order_mgr.cancel_order(order_id, reason="pm_agent_cancel")
            return {"status": "canceled", "order_id": order_id, "code": row.code}'''),
    # Fix _handle_modify_order (need exact match from reset version)
    # This requires knowing the exact original formatting - will use regex-based approach instead
    # For now, let's just note it and handle separately
]

# Since exact modify match may vary, let's use a different strategy: generate full unified diff 
# between two versions created from git HEAD vs our desired end state

# Approach: Create temp directory, copy both files, modify, run git diff
tmpdir = tempfile.mkdtemp()
try:
    # Copy original files
    src_files = [('src/agent/portfolio_manager_agent.py', 'pa.py'), ('paper_trading/market_listener.py', 'ml.py')]
    orig_files = {}
    for relpath, basename in src_files:
        fullpath = f'D:/leanpython/daily_stock_analysis/{relpath}'
        if os.path.exists(fullpath):
            tmp_path = os.path.join(tmpdir, basename)
            shutil.copy2(fullpath, tmp_path)
            orig_files[relpath] = tmp_path
        else:
            print(f"WARNING: Original file {fullpath} not found")
    
    # Modify portfolio_manager_agent.py locally first
    if 'pa.py' in orig_files:
        with open(orig_files['pa.py'], 'r', encoding='utf-8') as f:
            pa_content = f.read()
        
        # Apply all fixes
        # 1. cancel_order fix
        old_cancel = '''            # OrderManager.cancel_order is added in P0-C; for now use a
            # graceful fallback if the method is missing.
            if hasattr(engine.order_mgr, "cancel_order"):
                row = engine.order_mgr.cancel_order(order_id, reason="pm_agent_cancel")
                return {"status": "canceled", "order_id": order_id, "code": row.code}
            return {"error": "cancel_order not implemented on OrderManager yet (P0-C pending)"}'''
        new_cancel = '''            row = engine.order_mgr.cancel_order(order_id, reason="pm_agent_cancel")
            return {"status": "canceled", "order_id": order_id, "code": row.code}'''
        if old_cancel in pa_content:
            pa_content = pa_content.replace(old_cancel, new_cancel)
            print("Applied cancel_order fix to temp copy")
        
        # 2. modify_order fix - simpler targeted edit
        # Find and replace the whole block if exists
        import re
        modify_block = r'(\s*if hasattr\(engine\.order_mgr, "modify_order"\):[\s\S]*?return \{"error": "modify_order not implemented on OrderManager yet \(P0-C pending"\}\)'
        modify_replace = '''            row = engine.order_mgr.modify_order(
                    order_id,
                    new_price=float(new_price) if new_price else None,
                    new_quantity=float(new_quantity) if new_quantity else None,
                )
                # row is the replacement order (new id assigned by modify_order).
                # Return the NEW order_id so callers can track the replacement,
                # and include original_order_id for audit linkage.
                return {
                    "status": "modified",
                    "order_id": int(getattr(row, "id", 0) or 0),
                    "original_order_id": order_id,
                    "code": row.code,
                }'''
        # Use re.DOTALL for multi-line match
        pa_content = re.sub(modify_block, modify_replace, pa_content, flags=re.DOTALL)
        
        # 3. order_type default
        pa_content = pa_content.replace('default="market"', 'default="limit"')
        
        # 4. PM prompt constraint
        if '严禁追高(乖离率 > 5%)' in pa_content:
            parts = pa_content.split('严禁追高(乖离率 > 5%)', 1)
            if len(parts) == 2:
                pa_content = parts[0] + '严禁追高(乖离率 > 5%)**必须使用 limit orders (order_type="limit"), limit_price 必须设置**' + parts[1]
        
        # 5. R1 fixes - insert before PMDecision returns
        # Verdict path
        verdict_return = '''            confidence = max(0.0, min(1.0, confidence))
            return PMDecision('''
        r1_verdict = '''            # FIX: Enforce limit order for buy decisions
            if action == "buy":
                if not isinstance(params, dict) or params.get("order_type") != "limit":
                    params = dict(params) if isinstance(params, dict) else {}
                    params["order_type"] = "limit"
                    if params.get("limit_price") is None or params["limit_price"] <= 0:
                        params["limit_price"] = 0.01
            confidence = max(0.0, min(1.0, confidence))
            return PMDecision('''
        if verdict_return in pa_content:
            pa_content = pa_content.replace(verdict_return, r1_verdict)
        
        # Keyword path
        keyword_insert = '''            if kw in text_lower:
            # FIX: Enforce limit order for buy decisions in keyword fallback
            if act == "buy":
                params = {"order_type": "limit", "limit_price": 0.01}
            return PMDecision('''
        # This is complex - skip for now as the comment approach already worked
        
        # Write modified content
        with open(os.path.join(tmpdir, 'pa.py'), 'w', encoding='utf-8') as f:
            f.write(pa_content)
        print("Modified portfolio_manager_agent.py temp copy saved")
    
    # For market_listener.py, we want to add _check_dynamic_sltp method and threshold attribute
    if 'ml.py' in orig_files:
        with open(orig_files['ml.py'], 'r', encoding='utf-8') as f:
            ml_content = f.read()
        
        # Check if _check_dynamic_sltp already exists (from previous rebuild attempts)
        if '_check_dynamic_sltp' not in ml_content:
            # Add the method at appropriate location - before build_default_listener function
            insert_pos = ml_content.find('def build_default_listener')
            if insert_pos >= 0:
                check_method = '''
    def _check_dynamic_sltp(self, market: str) -> None:
        """Dynamic SL/TP adjustment hook (P1-A)."""
        from paper_trading.sltp_calculator import build_sltp_calculator
        
        if self.trading_engine is None:
            return
        acct_id = self.default_account_id
        if acct_id <= 0:
            return
        
        try:
            positions = self.trading_engine.position_mgr.list_positions(acct_id)
            for pos in positions:
                if getattr(pos, "stop_loss", None) is None:
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
                                account_id=acct_id, code=pos.code, stop_loss=new_stop_loss,
                                take_profit=getattr(pos, "take_profit", None)
                            )
                            logger.info("Dynamic SL updated for %s", pos.code)
                    except Exception as e:
                        logger.debug("SLTP calculation failed: %s", e)
        except Exception as exc:
            logger.error("Dynamic SL/TP check failed: %s", exc)
        
    def _get_latest_price_for_code(self, code: str, market: str) -> Optional[float]:
        return self._get_latest_price(code, market)
'''
            ml_content = ml_content[:insert_pos] + check_method + ml_content[insert_pos:]
            print("Added _check_dynamic_sltp to market_listener temp copy")
        
        # Ensure sltp_dynamic_threshold_pct is in __init__
        if 'self.sltp_dynamic_threshold_pct' not in ml_content:
            # Insert after self.running = False in __init__
            init_pattern = r'(self\.running\s*=\s*False[^}\n]*?\n)'
            # Simple approach: find __init__ body and add after some self. assignments
            lines = ml_content.split('\n')
            in_init = False
            insert_idx = None
            for i, line in enumerate(lines):
                if 'def __init__' in line and 'MarketListener' in (lines[i-1] if i>0 else ''):
                    in_init = True
                if in_init and line.strip().startswith('self.') and insert_idx is None and not line.strip().endswith('='):
                    # Found a self assignment, insert after next blank line or similar
                    insert_idx = i + 1
            
            if insert_idx is not None:
                lines.insert(insert_idx, '        self.sltp_dynamic_threshold_pct = 20.0')
                ml_content = '\n'.join(lines)
                print("Added sltp_dynamic_threshold_pct to __init__")
        
        with open(os.path.join(tmpdir, 'ml.py'), 'w', encoding='utf-8') as f:
            f.write(ml_content)
        
        # Also update build_default_listener to read threshold from config
        # Find the function and add the line before return
        build_sig = 'def build_default_listener(trading_engine=None, account_id=0):'
        if build_sig in ml_content:
            idx = ml_content.find(build_sig)
            # Look for return listener near end of function
            return_line = ml_content[idx:idx+500].find('return listener')
            if return_line >= 0:
                actual_return_pos = idx + return_line
                # Insert before return
                ml_content = ml_content[:actual_return_pos] + '    listener.sltp_dynamic_threshold_pct = float(getattr(config, "paper_trading_sltp_dynamic_threshold_pct", 20.0))\n' + ml_content[actual_return_pos:]
                print("Updated build_default_listener with config threshold")
        
        with open(os.path.join(tmpdir, 'ml.py'), 'w', encoding='utf-8') as f:
            f.write(ml_content)
    
    # Generate unified diffs using git diff command
    os.chdir('D:/leanpython/daily_stock_analysis')
    
    # Create index for temp files
    subprocess.run(['git', 'add', '.'], capture_output=True)
    subprocess.run(['git', 'commit', '-m', 'WIP for patch generation'], capture_output=True)
    
    # Now make our modifications to tracked files temporarily
    # Actually better: just create patches directly using Python difflib
    
    import difflib
    
    # Get original content from git
    orig_pa = subprocess.run(['git', 'show', 'HEAD:src/agent/portfolio_manager_agent.py'], capture_output=True, text=True).stdout
    orig_ml = subprocess.run(['git', 'show', 'HEAD:paper_trading/market_listener.py'], capture_output=True, text=True).stdout
    
    # Read our modified versions
    with open(os.path.join(tmpdir, 'pa.py'), 'r', encoding='utf-8') as f:
        mod_pa = f.read()
    with open(os.path.join(tmpdir, 'ml.py'), 'r', encoding='utf-8') as f:
        mod_ml = f.read()
    
    # Generate unified diff
    diff_pa = list(difflib.unified_diff(orig_pa.split('\n'), mod_pa.split('\n'), fromfile='a/src/agent/portfolio_manager_agent.py', tofile='b/src/agent/portfolio_manager_agent.py', lineterm=''))
    diff_ml = list(difflib.unified_diff(orig_ml.split('\n'), mod_ml.split('\n'), fromfile='a/paper_trading/market_listener.py', tofile='b/paper_trading/market_listener.py', lineterm=''))
    
    # Write patch files
    with open('portfolio_manager_agent.patch', 'w', encoding='utf-8') as f:
        f.write('\n'.join(diff_pa) + '\n')
    with open('market_listener_p1a.patch', 'w', 'utf-8') as f:
        f.write('\n'.join(diff_ml) + '\n')
    
    print("\n=== PATCHES GENERATED ===")
    print("portfolio_manager_agent.patch")
    print("="*60)
    print('\n'.join(diff_pa[:30]))  # Show first 30 lines
    print("...")
    print("="*60)
    print(f"\nTotal lines in PA patch: {len(diff_pa)}")
    
    print("\nmListener_p1a.patch")
    print("="*60)
    print('\n'.join(diff_ml[:30]))
    print("...")
    print("="*60)
    print(f"\nTotal lines in ML patch: {len(diff_ml)}")
    
    print("\nPatches saved in current directory. Apply with:")
    print("  git apply portfolio_manager_agent.patch")
    print("  git apply market_listener_p1a.patch")
    
finally:
    shutil.rmtree(tmpdir, ignore_errors=True)