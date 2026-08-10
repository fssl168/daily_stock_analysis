import apiClient from './index';
import { toCamelCase } from './utils';
import type {
  AccountCreateRequest,
  AccountListResponse,
  AccountSnapshotResponse,
  AccountUpdateRequest,
  BacktestPaperComparisonRequest,
  BacktestPaperComparisonResponse,
  BatchOrderCreateRequest,
  BatchOrderResponse,
  BattlePlanGenerateRequest,
  BattlePlanItem,
  BattlePlanMarkdownResponse,
  BreakerStatusResponse,
  ConditionalOrderCreateRequest,
  ConditionalOrderItem,
  DailyReflectionRequest,
  DailyReportResponse,
  DrawdownItem,
  ListenerControlResponse,
  ListenerStartRequest,
  ListenerStatusResponse,
  NetValueCurveResponse,
  OrderCreateRequest,
  OrderItem,
  OrderListFilterParams,
  OrderListResponse,
  OrderModifyRequest,
  PaperTradingScenario,
  PerformanceMetricsResponse,
  PMDecisionExecuteResponse,
  PMDecisionIgnoreRequest,
  PMDecisionItem,
  PMDecisionListResponse,
  PMDecisionTriggerRequest,
  PositionListResponse,
  ReflectionListResponse,
  ReflectionNoteItem,
  RiskMetricsResponse,
  SignalListResponse,
  TradeListResponse,
  TradeResultResponse,
  LatencyReport,
  DriftReportItem,
  ExtremeMarketAlertItem,
} from '../types/paperTrading';

// ============ API ============

export const paperTradingApi = {
  /**
   * List all paper trading accounts with snapshot summaries.
   */
  getAccounts: async (): Promise<AccountListResponse> => {
    const response = await apiClient.get<Record<string, unknown>>('/api/v1/paper-trading/accounts');
    return toCamelCase<AccountListResponse>(response.data);
  },

  /**
   * Create or reset a paper trading account.
   */
  createAccount: async (params: AccountCreateRequest = {}): Promise<AccountSnapshotResponse> => {
    const response = await apiClient.post<Record<string, unknown>>(
      '/api/v1/paper-trading/accounts',
      {
        name: params.name ?? 'default',
        initial_capital: params.initialCapital ?? 1000,
        reset_if_exists: params.resetIfExists ?? false,
      }
    );
    return toCamelCase<AccountSnapshotResponse>(response.data);
  },

  /**
   * Update account metadata (name / initial capital).
   */
  updateAccount: async (accountId: number, params: AccountUpdateRequest): Promise<AccountSnapshotResponse> => {
    const response = await apiClient.put<Record<string, unknown>>(
      `/api/v1/paper-trading/accounts/${accountId}`,
      {
        name: params.name,
        initial_capital: params.initialCapital,
      }
    );
    return toCamelCase<AccountSnapshotResponse>(response.data);
  },

  /**
   * Delete a paper trading account and all its data.
   */
  deleteAccount: async (accountId: number): Promise<void> => {
    await apiClient.delete(`/api/v1/paper-trading/accounts/${accountId}`);
  },

  /**
   * Get account snapshot.
   */
  getAccountSnapshot: async (accountId: number): Promise<AccountSnapshotResponse> => {
    const response = await apiClient.get<Record<string, unknown>>(
      `/api/v1/paper-trading/accounts/${accountId}`
    );
    return toCamelCase<AccountSnapshotResponse>(response.data);
  },

  /**
   * Get net value curve.
   */
  getNetValueCurve: async (accountId: number, limit = 90): Promise<NetValueCurveResponse> => {
    const response = await apiClient.get<Record<string, unknown>>(
      `/api/v1/paper-trading/accounts/${accountId}/net-value`,
      { params: { limit } }
    );
    return toCamelCase<NetValueCurveResponse>(response.data);
  },

  /**
   * Get account performance metrics.
   */
  getPerformanceMetrics: async (accountId: number, params: {
    startDate?: string;
    endDate?: string;
  } = {}): Promise<PerformanceMetricsResponse> => {
    const response = await apiClient.get<Record<string, unknown>>(
      `/api/v1/paper-trading/accounts/${accountId}/performance`,
      { params: { start_date: params.startDate, end_date: params.endDate } }
    );
    return toCamelCase<PerformanceMetricsResponse>(response.data);
  },

  /**
   * Get account drawdown curve.
   */
  getDrawdownCurve: async (accountId: number, params: {
    startDate?: string;
    endDate?: string;
  } = {}): Promise<DrawdownItem[]> => {
    const response = await apiClient.get<Record<string, unknown>[]>(
      `/api/v1/paper-trading/accounts/${accountId}/drawdown`,
      { params: { start_date: params.startDate, end_date: params.endDate } }
    );
    return response.data.map(item => toCamelCase<DrawdownItem>(item));
  },

  /**
   * Get current risk metrics.
   */
  getRiskMetrics: async (accountId: number): Promise<RiskMetricsResponse> => {
    const response = await apiClient.get<Record<string, unknown>>(
      `/api/v1/paper-trading/accounts/${accountId}/risk-metrics`
    );
    return toCamelCase<RiskMetricsResponse>(response.data);
  },

  /**
   * Submit a manual order.
   */
  submitOrder: async (params: OrderCreateRequest): Promise<TradeResultResponse> => {
    const response = await apiClient.post<Record<string, unknown>>(
      '/api/v1/paper-trading/orders',
      {
        account_id: params.accountId,
        code: params.code,
        side: params.side,
        quantity: params.quantity,
        order_type: params.orderType ?? 'market',
        limit_price: params.limitPrice,
        name: params.name,
        strategy_name: params.strategyName,
        reason: params.reason,
      }
    );
    return toCamelCase<TradeResultResponse>(response.data);
  },

  /**
   * Submit a batch of orders.
   */
  submitBatchOrders: async (params: BatchOrderCreateRequest): Promise<BatchOrderResponse> => {
    const response = await apiClient.post<Record<string, unknown>>(
      '/api/v1/paper-trading/orders/batch',
      {
        account_id: params.accountId,
        orders: params.orders.map(order => ({
          code: order.code,
          side: order.side,
          quantity: order.quantity,
          order_type: order.orderType ?? 'market',
          limit_price: order.limitPrice,
          name: order.name,
          strategy_name: order.strategyName,
          reason: order.reason,
        })),
      }
    );
    return toCamelCase<BatchOrderResponse>(response.data);
  },

  /**
   * Create a conditional order (stop-loss / take-profit / OCO).
   */
  createConditionalOrder: async (params: ConditionalOrderCreateRequest): Promise<ConditionalOrderItem> => {
    const response = await apiClient.post<Record<string, unknown>>(
      '/api/v1/paper-trading/orders/conditional',
      {
        account_id: params.accountId,
        code: params.code,
        side: params.side,
        quantity: params.quantity,
        order_type: params.orderType,
        trigger_price: params.triggerPrice,
        limit_price: params.limitPrice,
        linked_order_id: params.linkedOrderId,
        name: params.name,
        strategy_name: params.strategyName,
        reason: params.reason,
      }
    );
    return toCamelCase<ConditionalOrderItem>(response.data);
  },

  /**
   * Cancel a pending signal and its order.
   */
  cancelSignal: async (signalId: number, reason?: string): Promise<TradeResultResponse> => {
    const response = await apiClient.post<Record<string, unknown>>(
      `/api/v1/paper-trading/signals/${signalId}/cancel`,
      { signal_id: signalId, reason }
    );
    return toCamelCase<TradeResultResponse>(response.data);
  },

  /**
   * Modify a pending limit order's price/quantity by signal id.
   */
  modifySignal: async (signalId: number, params: Omit<OrderModifyRequest, 'signalId'>): Promise<TradeResultResponse> => {
    const response = await apiClient.post<Record<string, unknown>>(
      `/api/v1/paper-trading/signals/${signalId}/modify`,
      {
        signal_id: signalId,
        new_limit_price: params.newLimitPrice,
        new_quantity: params.newQuantity,
        reason: params.reason,
      }
    );
    return toCamelCase<TradeResultResponse>(response.data);
  },

  /**
   * Cancel a pending order by order id.
   */
  cancelOrder: async (orderId: number, reason?: string): Promise<TradeResultResponse> => {
    const response = await apiClient.post<Record<string, unknown>>(
      `/api/v1/paper-trading/orders/${orderId}/cancel`,
      { signal_id: 0, reason }
    );
    return toCamelCase<TradeResultResponse>(response.data);
  },

  /**
   * Modify a pending limit order by order id.
   */
  modifyOrder: async (orderId: number, params: Omit<OrderModifyRequest, 'signalId'>): Promise<TradeResultResponse> => {
    const response = await apiClient.post<Record<string, unknown>>(
      `/api/v1/paper-trading/orders/${orderId}/modify`,
      {
        signal_id: 0,
        new_limit_price: params.newLimitPrice,
        new_quantity: params.newQuantity,
        reason: params.reason,
      }
    );
    return toCamelCase<TradeResultResponse>(response.data);
  },

  /**
   * List orders for an account.
   */
  listOrders: async (accountId: number, params: OrderListFilterParams = {}): Promise<OrderListResponse> => {
    const response = await apiClient.get<Record<string, unknown>>(
      `/api/v1/paper-trading/accounts/${accountId}/orders`,
      {
        params: {
          status: params.status,
          side: params.side,
          code: params.code,
          from_date: params.fromDate,
          to_date: params.toDate,
          limit: params.limit ?? 100,
          offset: params.offset ?? 0,
        },
      }
    );
    const data = toCamelCase<OrderListResponse>(response.data);
    return {
      ...data,
      items: (data.items || []).map(item => toCamelCase<OrderItem>(item)),
    };
  },

  /**
   * List filled trades for an account.
   */
  listTrades: async (accountId: number, params: { code?: string; limit?: number } = {}): Promise<TradeListResponse> => {
    const response = await apiClient.get<Record<string, unknown>>(
      `/api/v1/paper-trading/accounts/${accountId}/trades`,
      { params: { code: params.code, limit: params.limit ?? 100 } }
    );
    return toCamelCase<TradeListResponse>(response.data);
  },

  /**
   * List open positions.
   */
  listPositions: async (accountId: number, includeZero = false): Promise<PositionListResponse> => {
    const response = await apiClient.get<Record<string, unknown>>(
      `/api/v1/paper-trading/accounts/${accountId}/positions`,
      { params: { include_zero: includeZero } }
    );
    return toCamelCase<PositionListResponse>(response.data);
  },

  /**
   * List signals (audit trail).
   */
  listSignals: async (accountId: number, params: {
    status?: string;
    code?: string;
    limit?: number;
  } = {}): Promise<SignalListResponse> => {
    const response = await apiClient.get<Record<string, unknown>>(
      `/api/v1/paper-trading/accounts/${accountId}/signals`,
      { params: { status: params.status, code: params.code, limit: params.limit ?? 100 } }
    );
    return toCamelCase<SignalListResponse>(response.data);
  },

  /**
   * List reflection notes.
   */
  listReflections: async (accountId: number, params: {
    scope?: string;
    code?: string;
    limit?: number;
  } = {}): Promise<ReflectionListResponse> => {
    const response = await apiClient.get<Record<string, unknown>>(
      `/api/v1/paper-trading/accounts/${accountId}/reflections`,
      { params: { scope: params.scope, code: params.code, limit: params.limit ?? 50 } }
    );
    return toCamelCase<ReflectionListResponse>(response.data);
  },

  /**
   * Trigger a daily reflection manually.
   */
  triggerDailyReflection: async (params: DailyReflectionRequest): Promise<ReflectionNoteItem> => {
    const response = await apiClient.post<Record<string, unknown>>(
      `/api/v1/paper-trading/accounts/${params.accountId}/reflections/daily`,
      {
        account_id: params.accountId,
        review_date: params.reviewDate,
      }
    );
    return toCamelCase<ReflectionNoteItem>(response.data);
  },

  /**
   * Generate the next-trading-day battle plan.
   */
  generateBattlePlan: async (params: BattlePlanGenerateRequest): Promise<BattlePlanItem> => {
    const response = await apiClient.post<Record<string, unknown>>(
      `/api/v1/paper-trading/accounts/${params.accountId}/battle-plans/generate`,
      {
        account_id: params.accountId,
        target_date: params.targetDate,
        watched_codes: params.watchedCodes,
      }
    );
    return toCamelCase<BattlePlanItem>(response.data);
  },

  /**
   * List recent battle plans.
   */
  listBattlePlans: async (accountId: number, limit = 10): Promise<BattlePlanItem[]> => {
    const response = await apiClient.get<Record<string, unknown>[]>(
      `/api/v1/paper-trading/accounts/${accountId}/battle-plans`,
      { params: { limit } }
    );
    return response.data.map(item => toCamelCase<BattlePlanItem>(item));
  },

  /**
   * Get a battle plan by id.
   */
  getBattlePlan: async (planId: number): Promise<BattlePlanItem> => {
    const response = await apiClient.get<Record<string, unknown>>(
      `/api/v1/paper-trading/battle-plans/${planId}`
    );
    return toCamelCase<BattlePlanItem>(response.data);
  },

  /**
   * Render a battle plan as Markdown.
   */
  getBattlePlanMarkdown: async (planId: number): Promise<BattlePlanMarkdownResponse> => {
    const response = await apiClient.get<Record<string, unknown>>(
      `/api/v1/paper-trading/battle-plans/${planId}/markdown`
    );
    return toCamelCase<BattlePlanMarkdownResponse>(response.data);
  },

  /**
   * Trigger one PM agent decision cycle.
   */
  triggerPMDecision: async (params: PMDecisionTriggerRequest): Promise<PMDecisionItem> => {
    const response = await apiClient.post<Record<string, unknown>>(
      `/api/v1/paper-trading/accounts/${params.accountId}/pm-decisions/trigger`,
      {
        account_id: params.accountId,
        extra_context: params.extraContext,
      }
    );
    return toCamelCase<PMDecisionItem>(response.data);
  },

  /**
   * List PM agent decisions.
   */
  listPMDecisions: async (accountId: number, params: {
    action?: string;
    limit?: number;
  } = {}): Promise<PMDecisionListResponse> => {
    const response = await apiClient.get<Record<string, unknown>>(
      `/api/v1/paper-trading/accounts/${accountId}/pm-decisions`,
      { params: { action: params.action, limit: params.limit ?? 50 } }
    );
    return toCamelCase<PMDecisionListResponse>(response.data);
  },

  /**
   * Execute a pending PM decision (buy/sell).
   */
  executePMDecision: async (
    accountId: number,
    decisionId: number
  ): Promise<PMDecisionExecuteResponse> => {
    const response = await apiClient.post<Record<string, unknown>>(
      `/api/v1/paper-trading/accounts/${accountId}/pm-decisions/${decisionId}/execute`
    );
    return toCamelCase<PMDecisionExecuteResponse>(response.data);
  },

  /**
   * Ignore / skip a pending PM decision.
   */
  ignorePMDecision: async (
    accountId: number,
    decisionId: number,
    params: PMDecisionIgnoreRequest = {}
  ): Promise<void> => {
    await apiClient.post(
      `/api/v1/paper-trading/accounts/${accountId}/pm-decisions/${decisionId}/ignore`,
      { reason: params.reason }
    );
  },

  /**
   * Get MarketListener status.
   */
  getListenerStatus: async (): Promise<ListenerStatusResponse> => {
    const response = await apiClient.get<Record<string, unknown>>(
      '/api/v1/paper-trading/listener/status'
    );
    return toCamelCase<ListenerStatusResponse>(response.data);
  },

  /**
   * Start the MarketListener.
   */
  startListener: async (params: ListenerStartRequest): Promise<ListenerControlResponse> => {
    const response = await apiClient.post<Record<string, unknown>>(
      '/api/v1/paper-trading/listener/start',
      {
        account_id: params.accountId,
        watched_codes: params.watchedCodes,
        markets: params.markets,
        tick_interval_seconds: params.tickIntervalSeconds,
        enable_strategies: params.enableStrategies ?? true,
        enable_agent_review: params.enableAgentReview ?? false,
        enable_daily_reflection: params.enableDailyReflection ?? true,
        enable_battle_plan: params.enableBattlePlan ?? true,
        pm_decision_interval_seconds: params.pmDecisionIntervalSeconds,
      }
    );
    return toCamelCase<ListenerControlResponse>(response.data);
  },

  /**
   * Stop the MarketListener.
   */
  stopListener: async (): Promise<ListenerControlResponse> => {
    const response = await apiClient.post<Record<string, unknown>>(
      '/api/v1/paper-trading/listener/stop'
    );
    return toCamelCase<ListenerControlResponse>(response.data);
  },

  /**
   * Generate a daily trading report (P2-A).
   */
  generateDailyReport: async (accountId: number, save = true): Promise<DailyReportResponse> => {
    const response = await apiClient.post<Record<string, unknown>>(
      `/api/v1/paper-trading/accounts/${accountId}/daily-report/generate`,
      undefined,
      { params: { save } }
    );
    return toCamelCase<DailyReportResponse>(response.data);
  },

  /**
   * Retrieve a saved daily report by date (P2-A).
   */
  getDailyReport: async (accountId: number, reportDate: string): Promise<DailyReportResponse> => {
    const response = await apiClient.get<Record<string, unknown>>(
      `/api/v1/paper-trading/accounts/${accountId}/daily-report/${reportDate}`
    );
    return toCamelCase<DailyReportResponse>(response.data);
  },

  /**
   * Generate a backtest-like scenario from paper-trading history (P3-F).
   */
  getBacktestScenario: async (
    accountId: number,
    strategyName = 'default'
  ): Promise<PaperTradingScenario> => {
    const response = await apiClient.get<Record<string, unknown>>(
      `/api/v1/paper-trading/accounts/${accountId}/backtest-scenario`,
      { params: { strategy_name: strategyName } }
    );
    return toCamelCase<PaperTradingScenario>(response.data);
  },

  /**
   * Compare backtest engine output with the paper-trading account record (P3-F).
   */
  compareWithBacktest: async (
    accountId: number,
    params: BacktestPaperComparisonRequest
  ): Promise<BacktestPaperComparisonResponse> => {
    const body: Record<string, unknown> = {
      strategy_name: params.strategyName,
      persist_reflection: params.persistReflection ?? true,
    };
    if (params.backtestSummary) {
      body.backtest_summary = params.backtestSummary;
    }
    const response = await apiClient.post<Record<string, unknown>>(
      `/api/v1/paper-trading/accounts/${accountId}/backtest-comparison`,
      body
    );
    return toCamelCase<BacktestPaperComparisonResponse>(response.data);
  },

  /** Get circuit breaker status for an account. */
  getBreakerStatus: async (accountId: number): Promise<BreakerStatusResponse> => {
    const response = await apiClient.get<Record<string, unknown>>(
      `/api/v1/paper-trading/accounts/${accountId}/breaker/status`
    );
    return toCamelCase<BreakerStatusResponse>(response.data);
  },

  // ---------------------------------------------------------------------------
  // Realtime-quant API surface (added during frontend alignment)
  // Backend endpoints may not all exist yet — these stubs call the documented
  // routes and degrade to empty defaults on failure.
  // ---------------------------------------------------------------------------

  /** Get tick-level latency statistics (p50/p95/p99). */
  getLatency: async (accountId: number): Promise<LatencyReport> => {
    try {
      const response = await apiClient.get<Record<string, unknown>>(
        `/api/v1/paper-trading/accounts/${accountId}/latency`
      );
      return toCamelCase<LatencyReport>(response.data);
    } catch {
      return {
        tickTotalMs: { p50: 0, p95: 0, p99: 0 },
        steps: [],
      };
    }
  },

  /** Get strategy drift reports. */
  getDrift: async (accountId: number): Promise<DriftReportItem[]> => {
    try {
      const response = await apiClient.get<Record<string, unknown>>(
        `/api/v1/paper-trading/accounts/${accountId}/drift`
      );
      const data = response.data;
      const items = Array.isArray(data) ? data : (data as Record<string, unknown>).items;
      return (items ?? []) as unknown as DriftReportItem[];
    } catch {
      return [];
    }
  },

  /** Get extreme market alert status. */
  getExtremeMarket: async (accountId: number): Promise<ExtremeMarketAlertItem> => {
    try {
      const response = await apiClient.get<Record<string, unknown>>(
        `/api/v1/paper-trading/accounts/${accountId}/extreme-market`
      );
      return toCamelCase<ExtremeMarketAlertItem>(response.data);
    } catch {
      return {
        market: "cn",
        isActive: false,
        currentVol: 0,
        historicalVol: 0,
        ratio: 0,
        actions: [],
        detectedAt: "",
      };
    }
  },

  /** Get all strategies with lifecycle + performance. */
  getStrategies: async <T = unknown[]>(accountId: number): Promise<T> => {
    try {
      const response = await apiClient.get<Record<string, unknown>>(
        `/api/v1/paper-trading/accounts/${accountId}/strategies`
      );
      const data = response.data;
      const items = Array.isArray(data) ? data : (data as Record<string, unknown>).items;
      return (items ?? []) as T;
    } catch {
      return [] as T;
    }
  },

  /** Get strategy performance leaderboard. */
  getStrategyPerformance: async <T = unknown[]>(accountId: number): Promise<T> => {
    try {
      const response = await apiClient.get<Record<string, unknown>>(
        `/api/v1/paper-trading/accounts/${accountId}/strategies/performance`
      );
      const data = response.data;
      const items = Array.isArray(data) ? data : (data as Record<string, unknown>).items;
      return (items ?? []) as T;
    } catch {
      return [] as T;
    }
  },

  /** Get latest feature-pipeline snapshot. */
  getFeatures: async <T = unknown>(accountId: number): Promise<T> => {
    try {
      const response = await apiClient.get<Record<string, unknown>>(
        `/api/v1/paper-trading/accounts/${accountId}/features`
      );
      return response.data as T;
    } catch {
      return { as_of: "", features: [], skipped_codes: [] } as T;
    }
  },

  /** Trigger feature pipeline recompute. */
  recomputeFeatures: async (accountId: number): Promise<void> => {
    try {
      await apiClient.post(
        `/api/v1/paper-trading/accounts/${accountId}/features/recompute`
      );
    } catch {
      // silent — feature pipeline not yet available
    }
  },

  /** Get daily OHLC bars for a stock (for candlestick chart). */
  getDailyBars: async <T = unknown[]>(accountId: number, code: string, days = 90): Promise<T> => {
    try {
      const response = await apiClient.get<Record<string, unknown>>(
        `/api/v1/paper-trading/accounts/${accountId}/daily-bars/${code}?days=${days}`
      );
      const data = response.data;
      const items = Array.isArray(data) ? data : (data as Record<string, unknown>).items;
      return (items ?? []) as T;
    } catch {
      return [] as T;
    }
  },
};

