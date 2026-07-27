import sys
sys.path.insert(0, 'D:/leanpython/daily_stock_analysis')

try:
    from src.agent.portfolio_manager_agent import PortfolioManagerAgent
    print("✅ PortfolioManagerAgent imported OK")
except Exception as e:
    print(f"❌ PortfolioManagerAgent import failed: {e}")

try:
    from paper_trading.market_listener import MarketListener, is_market_open_now, build_default_listener
    print("✅ market_listener imports OK")
    print(f"is_market_open_now('cn') = {is_market_open_now('cn')}")
except Exception as e:
    print(f"❌ market_listener import failed: {e}")

try:
    from paper_trading.risk_order_adapter import RiskOrderAdapter, OrderCommand
    print("✅ risk_order_adapter imported OK")
except Exception as e:
    print(f"❌ risk_order_adapter import failed: {e}")