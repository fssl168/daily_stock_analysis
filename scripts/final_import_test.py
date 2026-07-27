import sys
sys.path.insert(0, 'D:/leanpython/daily_stock_analysis')

print("Testing imports after all fixes...")
print()

# Test 1: market_listener
try:
    from paper_trading.market_listener import MarketListener, is_market_open_now, build_default_listener
    print("[✓] market_listener imports OK")
    print(f"   is_market_open_now('cn') = {is_market_open_now('cn')}")
except Exception as e:
    print(f"[✗] market_listener import FAILED: {e}")

# Test 2: risk_order_adapter
try:
    from paper_trading.risk_order_adapter import RiskOrderAdapter, OrderCommand
    print("[✓] risk_order_adapter imports OK")
except Exception as e:
    print(f"[✗] risk_order_adapter import FAILED: {e}")

# Test 3: portfolio_manager_agent (most critical - was causing errors before)
try:
    from src.agent.portfolio_manager_agent import PortfolioManagerAgent
    print("[✓] portfolio_manager_agent imports OK")
except SyntaxError as se:
    print(f"[✗] portfolio_manager_agent SYNTAX ERROR: {se}")
except Exception as e:
    print(f"[✗] portfolio_manager_agent import FAILED: {type(e).__name__}: {e}")

# Test 4: trading_engine (to check R2 hook didn't break it)
try:
    from paper_trading.trading_engine import TradingEngine, TradeResult
    print("[✓] trading_engine imports OK")
except Exception as e:
    print(f"[✗] trading_engine import FAILED: {e}")

print()
print("All module import tests completed.")