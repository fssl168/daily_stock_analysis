/**
 * Paper trading API type definitions
 * Mirrors api/v1/schemas/paper_trading.py
 */

// ============ Account ============

export interface AccountSnapshotResponse {
  accountId: number;
  name: string;
  initialCapital: number;
  cash: number;
  frozenCash: number;
  totalMarketValue: number;
  netValue: number;
  returnPct: number;
  positionCount: number;
  status: string;
}

export interface AccountCreateRequest {
  name?: string;
  initialCapital?: number;
  resetIfExists?: boolean;
}

export interface AccountUpdateRequest {
  name?: string;
  initialCapital?: number;
}

export interface AccountListItem {
  accountId: number;
  name: string;
  initialCapital: number;
  cash: number;
  frozenCash: number;
  totalMarketValue: number;
  netValue: number;
  returnPct: number;
  positionCount: number;
  status: string;
}

export interface AccountListResponse {
  accounts: AccountListItem[];
  total: number;
}

// ============ Orders ============

export interface OrderCreateRequest {
  accountId: number;
  code: string;
  side: 'buy' | 'sell';
  quantity: number;
  orderType?: 'market' | 'limit';
  limitPrice?: number;
  name?: string;
  strategyName?: string;
  reason?: string;
}

export interface OrderCancelRequest {
  signalId: number;
  reason?: string;
}

export interface OrderModifyRequest {
  signalId: number;
  newLimitPrice?: number;
  newQuantity?: number;
  reason?: string;
}

export interface TradeResultResponse {
  signalId: number;
  orderId?: number;
  side: string;
  code: string;
  status: string;
  fillPrice?: number;
  fillQuantity?: number;
  fee?: number;
  reason: string;
  riskDecisions: Record<string, unknown>[];
  agentReview?: Record<string, unknown>;
}

export interface OrderItem {
  id: number;
  accountId: number;
  code: string;
  name?: string;
  side: string;
  orderType: string;
  price?: number;
  quantity: number;
  filledQuantity: number;
  filledPriceAvg: number;
  status: string;
  strategyName?: string;
  signalId?: number;
  reason?: string;
  rejectReason?: string;
  createdAt?: string;
  filledAt?: string;
}

export interface OrderListResponse {
  accountId: number;
  total: number;
  items: OrderItem[];
}

export interface BatchOrderItem {
  code: string;
  side: 'buy' | 'sell';
  quantity: number;
  orderType?: 'market' | 'limit';
  limitPrice?: number;
  name?: string;
  strategyName?: string;
  reason?: string;
}

export interface BatchOrderCreateRequest {
  accountId: number;
  orders: BatchOrderItem[];
}

export interface BatchOrderResponse {
  accountId: number;
  total: number;
  results: TradeResultResponse[];
}

export interface ConditionalOrderCreateRequest {
  accountId: number;
  code: string;
  side: 'buy' | 'sell';
  quantity: number;
  orderType: 'stop_loss' | 'take_profit' | 'oco_primary' | 'oco_secondary';
  triggerPrice: number;
  limitPrice?: number;
  linkedOrderId?: number;
  name?: string;
  strategyName?: string;
  reason?: string;
}

export interface ConditionalOrderItem extends OrderItem {
  triggerPrice?: number;
  linkedOrderId?: number;
  triggeredAt?: string;
}

export interface OrderListFilterParams {
  status?: string;
  side?: 'buy' | 'sell';
  code?: string;
  fromDate?: string;
  toDate?: string;
  limit?: number;
  offset?: number;
}

// ============ Positions / Trades / Signals ============

export interface PositionItem {
  accountId: number;
  code: string;
  name?: string;
  quantity: number;
  availableQuantity: number;
  avgCost: number;
  lastPrice: number;
  stopLoss?: number;
  takeProfit?: number;
  takeProfit2?: number;
  sltpReasoning?: string;
  floatingPnl: number;
  floatingPnlPct: number;
}

export interface PositionListResponse {
  accountId: number;
  positions: PositionItem[];
  totalMarketValue: number;
}

export interface TradeItem {
  id: number;
  orderId: number;
  accountId: number;
  code: string;
  name?: string;
  side: string;
  fillPrice: number;
  fillQuantity: number;
  fee: number;
  realizedPnl?: number;
  tradedAt: string;
}

export interface TradeListResponse {
  accountId: number;
  total: number;
  items: TradeItem[];
}

export interface SignalItem {
  id: number;
  accountId: number;
  code: string;
  name?: string;
  side: string;
  triggerPrice: number;
  suggestedQuantity?: number;
  strategyName?: string;
  ruleName?: string;
  reason?: string;
  status: string;
  agentConfirmed?: boolean;
  agentReason?: string;
  reviewedAt?: string;
  createdAt: string;
}

export interface SignalListResponse {
  accountId: number;
  total: number;
  items: SignalItem[];
}

// ============ Reflection notes ============

export interface ReflectionNoteItem {
  id: number;
  accountId: number;
  scope: string;
  subject: string;
  summary: string;
  takeaway: string;
  lessons: string[];
  tags: string;
  mood: string;
  tradeId?: number;
  orderId?: number;
  code?: string;
  createdAt: string;
}

export interface ReflectionListResponse {
  accountId: number;
  total: number;
  items: ReflectionNoteItem[];
}

export interface DailyReflectionRequest {
  accountId: number;
  reviewDate?: string;
}

// ============ Battle plan ============

export interface HoldingPlanItem {
  code: string;
  name: string;
  currentPrice: number;
  strongScenario: string;
  neutralScenario: string;
  weakScenario: string;
  actionConditions: string[];
  stopLoss?: number;
  takeProfit1?: number;
  takeProfit2?: number;
}

export interface CandidatePlanItem {
  code: string;
  name: string;
  auctionCondition: string;
  intradayTrigger: string;
  positionRatio: number;
  stopLoss?: number;
  takeProfit1?: number;
  takeProfit2?: number;
  technicalScore: number;
}

export interface BattlePlanItem {
  planId: number;
  accountId: number;
  date: string;
  holdingsPlans: HoldingPlanItem[];
  candidates: CandidatePlanItem[];
  marketReview: string;
  sentimentScore: number;
  mainTheme: string;
  usedFallback: boolean;
  createdAt?: string;
}

export interface BattlePlanGenerateRequest {
  accountId: number;
  targetDate?: string;
  watchedCodes?: string[];
}

export interface BattlePlanMarkdownResponse {
  planId: number;
  date: string;
  markdown: string;
}

// ============ PM decisions ============

export interface PMDecisionItem {
  id: number;
  accountId: number;
  action: string;
  code?: string;
  name?: string;
  params: Record<string, unknown>;
  reason: string;
  confidence: number;
  elapsedSeconds: number;
  usedFallback: boolean;
  error?: string;
  status: string;
  signalId?: number;
  orderId?: number;
  createdAt: string;
}

export interface PMDecisionListResponse {
  accountId: number;
  total: number;
  items: PMDecisionItem[];
}

export interface PMDecisionTriggerRequest {
  accountId: number;
  extraContext?: Record<string, unknown>;
}

export interface PMDecisionExecuteResponse {
  decisionId: number;
  accountId: number;
  signalId: number;
  orderId?: number;
  side: string;
  code: string;
  status: string;
  fillPrice?: number;
  fillQuantity?: number;
  fee?: number;
  reason: string;
}

export interface PMDecisionIgnoreRequest {
  reason?: string;
}

// ============ Performance / Risk metrics (Phase 2) ============

export interface PerformanceMetricsResponse {
  accountId: number;
  startDate?: string;
  endDate?: string;
  totalReturnPct: number;
  annualizedReturnPct: number;
  sharpeRatio?: number;
  maxDrawdownPct: number;
  maxDrawdownStartDate?: string;
  maxDrawdownEndDate?: string;
  volatilityAnnualized?: number;
  winRate: number;
  profitFactor?: number;
  avgWin: number;
  avgLoss: number;
  calmarRatio?: number;
  tradeCount: number;
  winCount: number;
  lossCount: number;
}

export interface DrawdownItem {
  date: string;
  netValue: number;
  peakNetValue: number;
  drawdownPct: number;
}

export interface RiskMetricsResponse {
  accountId: number;
  maxSingleStockConcentrationPct: number;
  maxOpenPositionsLimit: number;
  currentOpenPositions: number;
  maxPctPerStockLimit: number;
  maxCashPerBuyLimit: number;
  maxDailyLossLimit: number;
  currentDrawdownPct: number;
}

// ============ Net value curve ============

export interface NetValuePoint {
  date: string;
  netValue: number;
  cash: number;
  marketValue: number;
  returnPct: number;
}

export interface NetValueCurveResponse {
  accountId: number;
  points: NetValuePoint[];
}

// ============ Listener status ============

export interface ListenerStatusResponse {
  running: boolean;
  accountId?: number;
  watchedCodesCount: number;
  strategiesCount: number;
  markets: string[];
  lastSettleDate?: string;
  lastBattlePlanDate?: string;
  lastDailyReflectionDate?: string;
  lastPmDecisionAt?: Record<string, string>;
}

export interface ListenerStartRequest {
  accountId: number;
  watchedCodes?: string[];
  markets?: string[];
  tickIntervalSeconds?: number;
  enableStrategies?: boolean;
  enableAgentReview?: boolean;
  enableDailyReflection?: boolean;
  enableBattlePlan?: boolean;
  pmDecisionIntervalSeconds?: number;
}

export interface ListenerControlResponse {
  running: boolean;
  message: string;
}

// ============ Daily report (P2-A) ============

export interface DailyReportResponse {
  date: string;
  markdown?: string;
  reportPath?: string;
  voicePath?: string;
  usedFallback?: boolean;
  error?: string;
}
