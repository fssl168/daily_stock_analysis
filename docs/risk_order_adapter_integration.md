# Risk Order Adapter Integration Guide

## Overview

The `risk_order_adapter.py` module provides the mapping from `AgentReviewResult` / `RiskDecision` to actionable order commands, addressing the P0-C gap of connecting risk review verdicts to actual order actions (cancel/modify/sell).

## Usage Pattern

### Basic Integration in PortfolioManagerAgent

```python
# In src/agent/portfolio_manager_agent.py, after make_decision() returns:
decision = self.make_decision(...)

# Optionally check for recent agent review results that might trigger order actions
if hasattr(self, 'risk_reviewer'):  # if configured
    from paper_trading.risk_order_adapter import RiskOrderAdapter
    # Assume review_result is stored somewhere or can be fetched
    # review_result = self.get_latest_review_result(decision.code)
    # cmd = RiskOrderAdapter.from_agent_review(review_result)
    # if cmd and cmd.action != "hold":
    #     self.trading_engine.order_mgr.execute(cmd)  # would need to implement execute
```

### Integration in MarketListener (Alternative)

The market listener could periodically check for pending reviews and apply corresponding actions:

```python
# In paper_trading/market_listener.py, add to _tick_market or a separate timer hook:
def _apply_pending_review_actions(self, market: str) -> None:
    """Check for recent agent review results and execute corresponding order commands."""
    if not self.trading_engine:
        return
    
    # Fetch latest unresolved review result (implementation depends on storage)
    # review_result = self.trading_engine.review_store.get_latest_pending()
    # if review_result:
    #     from paper_trading.risk_order_adapter import RiskOrderAdapter
    #     cmd = RiskOrderAdapter.from_agent_review(review_result)
    #     if cmd:
    #         self.execute_order_command(cmd, account_id=self.default_account_id)
```

## Command Types Mapped

| AgentReviewResult.action | RiskOrderAdapter.Action | Effect |
|--------------------------|------------------------|--------|
| "reject" | cancel | Cancel all pending orders for the stock |
| "sell" / "reduce" | sell | Sell position at current market (or new limit order) |
| "approve" | None | No action (signal approved as-is) |
| Any with stop_loss/take_profit set | update SL/TP | Could modify existing orders' SLTP |

## TODO Items for Production-Ready Integration

1. [ ] Add `reviewer` field to `PortfolioManagerAgent` configuration to pass `AgentRiskReviewer` instance
2. [ ] Store `AgentReviewResult` in database (similar to `PaperDecision`) for lookup
3. [ ] Add hook in `TradingEngine.submit_signal()` to invoke adapter after review
4. [ ] Add unit tests for adapter's mapping logic

## Status

**File created:** `paper_trading/risk_order_adapter.py`  
**Documentation:** `docs/risk_order_adapter_integration.md`  
**Integration status:** Not yet wired into execution flow (P0-C partial completion)  
**Recommendation:** Complete integration in next sprint to achieve full auto-cancel/auto-modify capability.
