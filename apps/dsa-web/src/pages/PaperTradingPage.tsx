import type React from 'react';
import { useState, useEffect, useCallback, useMemo } from 'react';
import { paperTradingApi } from '../api/paperTrading';
import { Card, Badge } from '../components/common';
import type {
  AccountSnapshotResponse,
  BattlePlanItem,
  NetValuePoint,
  OrderItem,
  PMDecisionItem,
  PositionItem,
  ReflectionNoteItem,
  SignalItem,
  TradeItem,
  ListenerStatusResponse,
  TradeResultResponse,
} from '../types/paperTrading';

type TabKey = 'positions' | 'orders' | 'trades' | 'signals' | 'decisions' | 'reflections' | 'battle-plans';

// ============ Helpers ============

function formatNumber(value?: number | null, digits = 2): string {
  if (value == null) return '--';
  return value.toFixed(digits);
}

function formatPct(value?: number | null): string {
  if (value == null) return '--';
  return `${value >= 0 ? '+' : ''}${value.toFixed(2)}%`;
}

function statusBadge(status: string) {
  switch (status) {
    case 'executed':
    case 'filled':
    case 'completed':
      return <Badge variant="success">{status}</Badge>;
    case 'rejected':
    case 'cancelled':
      return <Badge variant="danger">{status}</Badge>;
    case 'pending':
    case 'submitted':
      return <Badge variant="warning">{status}</Badge>;
    default:
      return <Badge variant="default">{status}</Badge>;
  }
}

function sideBadge(side: string) {
  return (
    <Badge
      variant={side === 'buy' ? 'success' : side === 'sell' ? 'danger' : 'default'}
      className="uppercase"
    >
      {side}
    </Badge>
  );
}

function formatDateTime(value?: string | null): string {
  if (!value) return '--';
  try {
    return new Date(value).toLocaleString('zh-CN', {
      month: 'short',
      day: 'numeric',
      hour: '2-digit',
      minute: '2-digit',
    });
  } catch {
    return value;
  }
}

// ============ Net Value Sparkline ============

const NetValueSparkline: React.FC<{ data: NetValuePoint[]; width?: number; height?: number }> = ({
  data,
  width = 320,
  height = 120,
}) => {
  if (data.length < 2) {
    return (
      <div className="flex items-center justify-center h-full text-xs text-muted">
        No net value data yet
      </div>
    );
  }

  const values = data.map(d => d.netValue);
  const min = Math.min(...values);
  const max = Math.max(...values);
  const range = max - min || 1;
  const padding = 6;
  const chartWidth = width - padding * 2;
  const chartHeight = height - padding * 2;

  const points = data.map((d, i) => {
    const x = padding + (i / (data.length - 1)) * chartWidth;
    const y = padding + chartHeight - ((d.netValue - min) / range) * chartHeight;
    return `${x},${y}`;
  }).join(' ');

  const areaPoints = `${padding},${height - padding} ${points} ${width - padding},${height - padding}`;
  const baseLine = data[0]?.netValue ?? 1;
  const current = data[data.length - 1]?.netValue ?? baseLine;
  const isUp = current >= baseLine;

  return (
    <svg width={width} height={height} className="overflow-visible">
      <defs>
        <linearGradient id="netValueGradient" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stopColor={isUp ? '#00ff88' : '#ff4466'} stopOpacity="0.3" />
          <stop offset="100%" stopColor={isUp ? '#00ff88' : '#ff4466'} stopOpacity="0" />
        </linearGradient>
      </defs>
      <polygon points={areaPoints} fill="url(#netValueGradient)" />
      <polyline
        points={points}
        fill="none"
        stroke={isUp ? '#00ff88' : '#ff4466'}
        strokeWidth={2}
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
};

// ============ Order Form ============

const OrderForm: React.FC<{ accountId: number; onSubmitted: () => void }> = ({ accountId, onSubmitted }) => {
  const [code, setCode] = useState('');
  const [side, setSide] = useState<'buy' | 'sell'>('buy');
  const [quantity, setQuantity] = useState('');
  const [orderType, setOrderType] = useState<'market' | 'limit'>('market');
  const [limitPrice, setLimitPrice] = useState('');
  const [loading, setLoading] = useState(false);
  const [result, setResult] = useState<TradeResultResponse | null>(null);
  const [error, setError] = useState<string | null>(null);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setLoading(true);
    setResult(null);
    setError(null);
    try {
      const qty = parseFloat(quantity);
      if (!code || Number.isNaN(qty) || qty <= 0) {
        throw new Error('Please enter a valid code and quantity');
      }
      const res = await paperTradingApi.submitOrder({
        accountId,
        code: code.toUpperCase(),
        side,
        quantity: qty,
        orderType,
        limitPrice: orderType === 'limit' && limitPrice ? parseFloat(limitPrice) : undefined,
        reason: 'manual order from WebUI',
      });
      setResult(res);
      onSubmitted();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Order failed');
    } finally {
      setLoading(false);
    }
  };

  return (
    <Card variant="gradient" padding="md">
      <span className="label-uppercase">Manual Order</span>
      <form onSubmit={handleSubmit} className="mt-3 space-y-3">
        <div className="grid grid-cols-2 gap-3">
          <input
            type="text"
            value={code}
            onChange={(e) => setCode(e.target.value.toUpperCase())}
            placeholder="Code"
            className="input-terminal"
            data-testid="order-code-input"
          />
          <select
            value={side}
            onChange={(e) => setSide(e.target.value as 'buy' | 'sell')}
            className="input-terminal bg-elevated"
            data-testid="order-side-select"
          >
            <option value="buy">Buy</option>
            <option value="sell">Sell</option>
          </select>
        </div>
        <div className="grid grid-cols-2 gap-3">
          <input
            type="number"
            value={quantity}
            onChange={(e) => setQuantity(e.target.value)}
            placeholder="Quantity"
            min={0.01}
            step={0.01}
            className="input-terminal"
            data-testid="order-quantity-input"
          />
          <select
            value={orderType}
            onChange={(e) => setOrderType(e.target.value as 'market' | 'limit')}
            className="input-terminal bg-elevated"
            data-testid="order-type-select"
          >
            <option value="market">Market</option>
            <option value="limit">Limit</option>
          </select>
        </div>
        {orderType === 'limit' && (
          <input
            type="number"
            value={limitPrice}
            onChange={(e) => setLimitPrice(e.target.value)}
            placeholder="Limit price"
            min={0.01}
            step={0.01}
            className="input-terminal"
            data-testid="order-limit-price-input"
          />
        )}
        <button
          type="submit"
          disabled={loading}
          className="btn-primary w-full flex items-center justify-center gap-2"
          data-testid="order-submit-button"
        >
          {loading ? 'Submitting...' : 'Submit Order'}
        </button>
      </form>
      {result && (
        <div className="mt-3 p-2 rounded-lg bg-emerald-500/10 border border-emerald-500/20 text-xs">
          <span className="text-emerald-400">{result.status.toUpperCase()}</span>
          {' '}
          <span className="text-secondary">{result.code}</span>
          {result.fillPrice != null && (
            <span className="text-secondary"> @ {formatNumber(result.fillPrice)}</span>
          )}
          {result.fillQuantity != null && (
            <span className="text-secondary"> x {formatNumber(result.fillQuantity)}</span>
          )}
        </div>
      )}
      {error && (
        <p className="mt-3 text-xs text-danger">{error}</p>
      )}
    </Card>
  );
};

// ============ Listener Control ============

const ListenerControl: React.FC<{ onStatusChange: () => void }> = ({ onStatusChange }) => {
  const [status, setStatus] = useState<ListenerStatusResponse | null>(null);
  const [loading, setLoading] = useState(false);

  const fetchStatus = useCallback(async () => {
    try {
      const s = await paperTradingApi.getListenerStatus();
      setStatus(s);
    } catch {
      setStatus({ running: false, watchedCodesCount: 0, strategiesCount: 0, markets: [] });
    }
  }, []);

  useEffect(() => {
    fetchStatus();
    const timer = setInterval(fetchStatus, 5000);
    return () => clearInterval(timer);
  }, [fetchStatus]);

  const handleStart = async () => {
    setLoading(true);
    try {
      await paperTradingApi.startListener({ accountId: 1 });
      await fetchStatus();
      onStatusChange();
    } finally {
      setLoading(false);
    }
  };

  const handleStop = async () => {
    setLoading(true);
    try {
      await paperTradingApi.stopListener();
      await fetchStatus();
      onStatusChange();
    } finally {
      setLoading(false);
    }
  };

  return (
    <Card variant="gradient" padding="md" className="mt-3">
      <div className="flex items-center justify-between">
        <span className="label-uppercase">Market Listener</span>
        <Badge variant={status?.running ? 'success' : 'default'}>
          {status?.running ? 'RUNNING' : 'STOPPED'}
        </Badge>
      </div>
      <div className="mt-2 text-xs text-secondary space-y-1">
        <p>Account: {status?.accountId ?? '--'}</p>
        <p>Watched: {status?.watchedCodesCount ?? 0} codes</p>
        <p>Markets: {status?.markets?.join(', ') || '--'}</p>
      </div>
      <div className="mt-3 flex gap-2">
        {status?.running ? (
          <button
            type="button"
            onClick={handleStop}
            disabled={loading}
            className="btn-secondary flex-1 text-xs py-2"
            data-testid="listener-stop-button"
          >
            Stop
          </button>
        ) : (
          <button
            type="button"
            onClick={handleStart}
            disabled={loading}
            className="btn-primary flex-1 text-xs py-2"
            data-testid="listener-start-button"
          >
            Start
          </button>
        )}
      </div>
    </Card>
  );
};

// ============ Data Table Components ============

const PositionsTable: React.FC<{ positions: PositionItem[] }> = ({ positions }) => {
  if (positions.length === 0) {
    return <EmptyState message="No open positions" />;
  }
  return (
    <div className="overflow-x-auto rounded-xl border border-white/5">
      <table className="w-full text-sm">
        <thead>
          <tr className="bg-elevated text-left">
            <th className="px-3 py-2.5 text-xs font-medium text-secondary uppercase">Code</th>
            <th className="px-3 py-2.5 text-xs font-medium text-secondary uppercase text-right">Quantity</th>
            <th className="px-3 py-2.5 text-xs font-medium text-secondary uppercase text-right">Avg Cost</th>
            <th className="px-3 py-2.5 text-xs font-medium text-secondary uppercase text-right">Last Price</th>
            <th className="px-3 py-2.5 text-xs font-medium text-secondary uppercase text-right">SL / TP1 / TP2</th>
            <th className="px-3 py-2.5 text-xs font-medium text-secondary uppercase text-right">PnL</th>
          </tr>
        </thead>
        <tbody>
          {positions.map((p) => (
            <tr key={p.code} className="border-t border-white/5 hover:bg-hover transition-colors">
              <td className="px-3 py-2 font-mono text-cyan text-xs">{p.code}</td>
              <td className="px-3 py-2 text-xs text-right text-white">{formatNumber(p.quantity)}</td>
              <td className="px-3 py-2 text-xs text-right text-secondary">{formatNumber(p.avgCost)}</td>
              <td className="px-3 py-2 text-xs text-right text-secondary">{formatNumber(p.lastPrice)}</td>
              <td className="px-3 py-2 text-xs text-right text-secondary">
                {formatNumber(p.stopLoss)} / {formatNumber(p.takeProfit)} / {formatNumber(p.takeProfit2)}
              </td>
              <td className="px-3 py-2 text-xs text-right">
                <span className={p.floatingPnl >= 0 ? 'text-emerald-400' : 'text-red-400'}>
                  {formatNumber(p.floatingPnl)} ({formatPct(p.floatingPnlPct)})
                </span>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
};

const OrdersTable: React.FC<{ orders: OrderItem[]; onRefresh: () => void }> = ({ orders, onRefresh }) => {
  const [actingId, setActingId] = useState<number | null>(null);

  const handleCancel = async (orderId: number) => {
    setActingId(orderId);
    try {
      await paperTradingApi.cancelOrder(orderId, 'cancelled from WebUI');
      onRefresh();
    } finally {
      setActingId(null);
    }
  };

  if (orders.length === 0) {
    return <EmptyState message="No orders" />;
  }

  return (
    <div className="overflow-x-auto rounded-xl border border-white/5" data-testid="orders-table">
      <table className="w-full text-sm">
        <thead>
          <tr className="bg-elevated text-left">
            <th className="px-3 py-2.5 text-xs font-medium text-secondary uppercase">ID</th>
            <th className="px-3 py-2.5 text-xs font-medium text-secondary uppercase">Code</th>
            <th className="px-3 py-2.5 text-xs font-medium text-secondary uppercase">Side</th>
            <th className="px-3 py-2.5 text-xs font-medium text-secondary uppercase">Type</th>
            <th className="px-3 py-2.5 text-xs font-medium text-secondary uppercase text-right">Qty</th>
            <th className="px-3 py-2.5 text-xs font-medium text-secondary uppercase text-right">Filled</th>
            <th className="px-3 py-2.5 text-xs font-medium text-secondary uppercase">Status</th>
            <th className="px-3 py-2.5 text-xs font-medium text-secondary uppercase">Created</th>
            <th className="px-3 py-2.5 text-xs font-medium text-secondary uppercase">Action</th>
          </tr>
        </thead>
        <tbody>
          {orders.map((o) => (
            <tr key={o.id} className="border-t border-white/5 hover:bg-hover transition-colors">
              <td className="px-3 py-2 text-xs text-muted">{o.id}</td>
              <td className="px-3 py-2 font-mono text-cyan text-xs">{o.code}</td>
              <td className="px-3 py-2">{sideBadge(o.side)}</td>
              <td className="px-3 py-2 text-xs text-secondary">{o.orderType}</td>
              <td className="px-3 py-2 text-xs text-right text-white">{formatNumber(o.quantity)}</td>
              <td className="px-3 py-2 text-xs text-right text-secondary">{formatNumber(o.filledQuantity)}</td>
              <td className="px-3 py-2">{statusBadge(o.status)}</td>
              <td className="px-3 py-2 text-xs text-muted">{formatDateTime(o.createdAt)}</td>
              <td className="px-3 py-2">
                {o.status === 'pending' && (
                  <button
                    type="button"
                    onClick={() => handleCancel(o.id)}
                    disabled={actingId === o.id}
                    className="text-xs text-danger hover:text-red-300 disabled:opacity-50"
                  >
                    {actingId === o.id ? '...' : 'Cancel'}
                  </button>
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
};

const TradesTable: React.FC<{ trades: TradeItem[] }> = ({ trades }) => {
  if (trades.length === 0) {
    return <EmptyState message="No filled trades" />;
  }
  return (
    <div className="overflow-x-auto rounded-xl border border-white/5" data-testid="trades-table">
      <table className="w-full text-sm">
        <thead>
          <tr className="bg-elevated text-left">
            <th className="px-3 py-2.5 text-xs font-medium text-secondary uppercase">Code</th>
            <th className="px-3 py-2.5 text-xs font-medium text-secondary uppercase">Side</th>
            <th className="px-3 py-2.5 text-xs font-medium text-secondary uppercase text-right">Fill Price</th>
            <th className="px-3 py-2.5 text-xs font-medium text-secondary uppercase text-right">Quantity</th>
            <th className="px-3 py-2.5 text-xs font-medium text-secondary uppercase text-right">Fee</th>
            <th className="px-3 py-2.5 text-xs font-medium text-secondary uppercase">Traded At</th>
          </tr>
        </thead>
        <tbody>
          {trades.map((t) => (
            <tr key={t.id} className="border-t border-white/5 hover:bg-hover transition-colors">
              <td className="px-3 py-2 font-mono text-cyan text-xs">{t.code}</td>
              <td className="px-3 py-2">{sideBadge(t.side)}</td>
              <td className="px-3 py-2 text-xs text-right text-white">{formatNumber(t.fillPrice)}</td>
              <td className="px-3 py-2 text-xs text-right text-secondary">{formatNumber(t.fillQuantity)}</td>
              <td className="px-3 py-2 text-xs text-right text-secondary">{formatNumber(t.fee)}</td>
              <td className="px-3 py-2 text-xs text-muted">{formatDateTime(t.tradedAt)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
};

const SignalsTable: React.FC<{ signals: SignalItem[] }> = ({ signals }) => {
  if (signals.length === 0) {
    return <EmptyState message="No signals" />;
  }
  return (
    <div className="overflow-x-auto rounded-xl border border-white/5" data-testid="signals-table">
      <table className="w-full text-sm">
        <thead>
          <tr className="bg-elevated text-left">
            <th className="px-3 py-2.5 text-xs font-medium text-secondary uppercase">Code</th>
            <th className="px-3 py-2.5 text-xs font-medium text-secondary uppercase">Side</th>
            <th className="px-3 py-2.5 text-xs font-medium text-secondary uppercase text-right">Trigger</th>
            <th className="px-3 py-2.5 text-xs font-medium text-secondary uppercase">Strategy</th>
            <th className="px-3 py-2.5 text-xs font-medium text-secondary uppercase">Status</th>
            <th className="px-3 py-2.5 text-xs font-medium text-secondary uppercase">Agent</th>
            <th className="px-3 py-2.5 text-xs font-medium text-secondary uppercase">Created</th>
          </tr>
        </thead>
        <tbody>
          {signals.map((s) => (
            <tr key={s.id} className="border-t border-white/5 hover:bg-hover transition-colors">
              <td className="px-3 py-2 font-mono text-cyan text-xs">{s.code}</td>
              <td className="px-3 py-2">{sideBadge(s.side)}</td>
              <td className="px-3 py-2 text-xs text-right text-white">{formatNumber(s.triggerPrice)}</td>
              <td className="px-3 py-2 text-xs text-secondary">{s.strategyName || '--'}</td>
              <td className="px-3 py-2">{statusBadge(s.status)}</td>
              <td className="px-3 py-2 text-xs text-secondary">
                {s.agentConfirmed == null ? '--' : s.agentConfirmed ? 'Confirmed' : 'Vetoed'}
              </td>
              <td className="px-3 py-2 text-xs text-muted">{formatDateTime(s.createdAt)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
};

const DecisionsList: React.FC<{ decisions: PMDecisionItem[] }> = ({ decisions }) => {
  if (decisions.length === 0) {
    return <EmptyState message="No PM decisions" />;
  }
  return (
    <div className="space-y-2">
      {decisions.map((d) => (
        <div key={d.id} className="p-3 rounded-xl bg-elevated border border-white/5">
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-2">
              <Badge variant={d.action === 'buy' ? 'success' : d.action === 'sell' ? 'danger' : 'info'}>
                {d.action.toUpperCase()}
              </Badge>
              {d.code && <span className="font-mono text-cyan text-xs">{d.code}</span>}
            </div>
            <span className="text-xs text-muted">{formatDateTime(d.createdAt)}</span>
          </div>
          <p className="mt-2 text-xs text-secondary">{d.reason || 'No reason provided'}</p>
          <div className="mt-2 flex items-center gap-3 text-xs text-muted">
            <span>confidence: {(d.confidence * 100).toFixed(0)}%</span>
            {d.usedFallback && <Badge variant="warning">fallback</Badge>}
          </div>
        </div>
      ))}
    </div>
  );
};

const ReflectionsList: React.FC<{ reflections: ReflectionNoteItem[] }> = ({ reflections }) => {
  if (reflections.length === 0) {
    return <EmptyState message="No reflection notes" />;
  }
  return (
    <div className="space-y-3">
      {reflections.map((r) => (
        <div key={r.id} className="p-3 rounded-xl bg-elevated border border-white/5">
          <div className="flex items-center justify-between">
            <span className="text-xs font-medium text-white">{r.subject || 'Reflection'}</span>
            <Badge variant={r.mood === 'good' ? 'success' : r.mood === 'bad' ? 'danger' : 'default'}>
              {r.mood}
            </Badge>
          </div>
          <p className="mt-1 text-xs text-secondary">{r.summary}</p>
          {r.takeaway && (
            <p className="mt-2 text-xs text-cyan">Takeaway: {r.takeaway}</p>
          )}
          {r.lessons.length > 0 && (
            <ul className="mt-2 space-y-1">
              {r.lessons.map((lesson, i) => (
                <li key={i} className="text-xs text-muted list-disc list-inside">{lesson}</li>
              ))}
            </ul>
          )}
          <div className="mt-2 text-xs text-muted">
            {r.code && <span className="mr-2">Code: {r.code}</span>}
            <span>{formatDateTime(r.createdAt)}</span>
          </div>
        </div>
      ))}
    </div>
  );
};

const BattlePlansList: React.FC<{ plans: BattlePlanItem[] }> = ({ plans }) => {
  if (plans.length === 0) {
    return <EmptyState message="No battle plans" />;
  }
  return (
    <div className="space-y-3">
      {plans.map((p) => (
        <div key={p.planId} className="p-3 rounded-xl bg-elevated border border-white/5">
          <div className="flex items-center justify-between">
            <span className="text-xs font-medium text-white">Battle Plan {p.date}</span>
            <Badge variant={p.usedFallback ? 'warning' : 'success'}>
              {p.usedFallback ? 'fallback' : 'AI'}
            </Badge>
          </div>
          <p className="mt-1 text-xs text-secondary">{p.marketReview || 'No market review'}</p>
          {p.mainTheme && (
            <p className="mt-1 text-xs text-cyan">Theme: {p.mainTheme}</p>
          )}
          <div className="mt-2 grid grid-cols-2 gap-2">
            <div>
              <span className="text-xxs text-muted uppercase">Holdings</span>
              <p className="text-xs text-secondary">{p.holdingsPlans.map(h => h.code).join(', ') || '--'}</p>
            </div>
            <div>
              <span className="text-xxs text-muted uppercase">Candidates</span>
              <p className="text-xs text-secondary">{p.candidates.map(c => c.code).join(', ') || '--'}</p>
            </div>
          </div>
        </div>
      ))}
    </div>
  );
};

const EmptyState: React.FC<{ message: string }> = ({ message }) => (
  <div className="flex flex-col items-center justify-center h-48 text-center rounded-xl border border-white/5 bg-elevated/30">
    <div className="w-10 h-10 mb-2 rounded-xl bg-elevated flex items-center justify-center">
      <svg className="w-5 h-5 text-muted" fill="none" stroke="currentColor" viewBox="0 0 24 24">
        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M9 5H7a2 2 0 00-2 2v12a2 2 0 002 2h10a2 2 0 002-2V7a2 2 0 00-2-2h-2M9 5a2 2 0 002 2h2a2 2 0 002-2M9 5a2 2 0 012-2h2a2 2 0 012 2" />
      </svg>
    </div>
    <p className="text-xs text-muted">{message}</p>
  </div>
);

// ============ Main Page ============

const PaperTradingPage: React.FC = () => {
  const [accountId, setAccountId] = useState<number>(1);
  const [accountInput, setAccountInput] = useState('1');
  const [snapshot, setSnapshot] = useState<AccountSnapshotResponse | null>(null);
  const [netValue, setNetValue] = useState<NetValuePoint[]>([]);
  const [positions, setPositions] = useState<PositionItem[]>([]);
  const [orders, setOrders] = useState<OrderItem[]>([]);
  const [trades, setTrades] = useState<TradeItem[]>([]);
  const [signals, setSignals] = useState<SignalItem[]>([]);
  const [decisions, setDecisions] = useState<PMDecisionItem[]>([]);
  const [reflections, setReflections] = useState<ReflectionNoteItem[]>([]);
  const [battlePlans, setBattlePlans] = useState<BattlePlanItem[]>([]);
  const [activeTab, setActiveTab] = useState<TabKey>('positions');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [triggeringPm, setTriggeringPm] = useState(false);
  const [generatingPlan, setGeneratingPlan] = useState(false);

  const loadAll = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const [
        snap,
        nv,
        pos,
        ord,
        trd,
        sig,
        dec,
        ref,
        plans,
      ] = await Promise.all([
        paperTradingApi.getAccountSnapshot(accountId),
        paperTradingApi.getNetValueCurve(accountId, 90),
        paperTradingApi.listPositions(accountId),
        paperTradingApi.listOrders(accountId, { limit: 100 }),
        paperTradingApi.listTrades(accountId, { limit: 100 }),
        paperTradingApi.listSignals(accountId, { limit: 100 }),
        paperTradingApi.listPMDecisions(accountId, { limit: 50 }),
        paperTradingApi.listReflections(accountId, { limit: 50 }),
        paperTradingApi.listBattlePlans(accountId, 10),
      ]);
      setSnapshot(snap);
      setNetValue(nv.points || []);
      setPositions(pos.positions || []);
      setOrders(ord.items || []);
      setTrades(trd.items || []);
      setSignals(sig.items || []);
      setDecisions(dec.items || []);
      setReflections(ref.items || []);
      setBattlePlans(plans);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load paper trading data');
    } finally {
      setLoading(false);
    }
  }, [accountId]);

  useEffect(() => {
    loadAll();
  }, [loadAll]);

  const handleAccountSwitch = () => {
    const id = parseInt(accountInput, 10);
    if (!Number.isNaN(id) && id > 0) {
      setAccountId(id);
    }
  };

  const handleCreateAccount = async () => {
    try {
      const snap = await paperTradingApi.createAccount({
        name: 'default',
        initialCapital: 1000,
        resetIfExists: true,
      });
      setAccountId(snap.accountId);
      setAccountInput(String(snap.accountId));
      loadAll();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to create account');
    }
  };

  const handleTriggerPm = async () => {
    setTriggeringPm(true);
    try {
      await paperTradingApi.triggerPMDecision({ accountId });
      await loadAll();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'PM decision failed');
    } finally {
      setTriggeringPm(false);
    }
  };

  const handleGeneratePlan = async () => {
    setGeneratingPlan(true);
    try {
      await paperTradingApi.generateBattlePlan({ accountId });
      await loadAll();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Battle plan generation failed');
    } finally {
      setGeneratingPlan(false);
    }
  };

  const tabs: { key: TabKey; label: string; count?: number }[] = useMemo(() => [
    { key: 'positions', label: 'Positions', count: positions.length },
    { key: 'orders', label: 'Orders', count: orders.length },
    { key: 'trades', label: 'Trades', count: trades.length },
    { key: 'signals', label: 'Signals', count: signals.length },
    { key: 'decisions', label: 'Decisions', count: decisions.length },
    { key: 'reflections', label: 'Reflections', count: reflections.length },
    { key: 'battle-plans', label: 'Battle Plans', count: battlePlans.length },
  ], [positions.length, orders.length, trades.length, signals.length, decisions.length, reflections.length, battlePlans.length]);

  return (
    <div className="min-h-screen flex flex-col">
      {/* Header */}
      <header className="flex-shrink-0 px-4 py-3 border-b border-white/5" data-testid="paper-trading-header">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-3">
            <h1 className="text-lg font-semibold text-white" data-testid="paper-trading-title">Paper Trading</h1>
            <div className="flex items-center gap-2">
              <input
                type="number"
                value={accountInput}
                onChange={(e) => setAccountInput(e.target.value)}
                placeholder="Account ID"
                className="input-terminal w-24 text-xs py-2"
                data-testid="account-id-input"
              />
              <button
                type="button"
                onClick={handleAccountSwitch}
                className="btn-secondary text-xs py-2 px-3"
                data-testid="account-switch-button"
              >
                Switch
              </button>
              <button
                type="button"
                onClick={handleCreateAccount}
                className="btn-secondary text-xs py-2 px-3"
                data-testid="account-reset-button"
              >
                Reset Default
              </button>
            </div>
          </div>
          <div className="flex items-center gap-2">
            <button
              type="button"
              onClick={handleTriggerPm}
              disabled={triggeringPm}
              className="btn-secondary text-xs py-2 px-3"
              data-testid="trigger-pm-button"
            >
              {triggeringPm ? 'PM Thinking...' : 'Trigger PM'}
            </button>
            <button
              type="button"
              onClick={handleGeneratePlan}
              disabled={generatingPlan}
              className="btn-secondary text-xs py-2 px-3"
              data-testid="generate-plan-button"
            >
              {generatingPlan ? 'Generating...' : 'Generate Plan'}
            </button>
            <button
              type="button"
              onClick={loadAll}
              disabled={loading}
              className="btn-primary text-xs py-2 px-3"
              data-testid="refresh-button"
            >
              {loading ? 'Loading...' : 'Refresh'}
            </button>
          </div>
        </div>
        {error && (
          <p className="mt-2 text-xs text-danger" data-testid="error-message">{error}</p>
        )}
      </header>

      {/* Main content */}
      <main className="flex-1 flex overflow-hidden p-3 gap-3">
        {/* Left sidebar */}
        <div className="flex flex-col gap-3 w-80 flex-shrink-0 overflow-y-auto">
          {/* Account summary */}
          <Card variant="gradient" padding="md">
            <span className="label-uppercase">Account #{accountId}</span>
            {snapshot ? (
              <div className="mt-3 grid grid-cols-2 gap-3">
                <div>
                  <p className="text-xxs text-muted uppercase">Net Value</p>
                  <p className="text-lg font-mono font-semibold text-white">{formatNumber(snapshot.netValue)}</p>
                </div>
                <div>
                  <p className="text-xxs text-muted uppercase">Return</p>
                  <p className={`text-lg font-mono font-semibold ${snapshot.returnPct >= 0 ? 'text-emerald-400' : 'text-red-400'}`}>
                    {formatPct(snapshot.returnPct)}
                  </p>
                </div>
                <div>
                  <p className="text-xxs text-muted uppercase">Cash</p>
                  <p className="text-sm font-mono text-secondary">{formatNumber(snapshot.cash)}</p>
                </div>
                <div>
                  <p className="text-xxs text-muted uppercase">Positions</p>
                  <p className="text-sm font-mono text-secondary">{snapshot.positionCount}</p>
                </div>
              </div>
            ) : (
              <p className="mt-3 text-xs text-muted">Loading account...</p>
            )}
          </Card>

          {/* Net value curve */}
          <Card variant="gradient" padding="md">
            <span className="label-uppercase">Net Value Curve</span>
            <div className="mt-3">
              <NetValueSparkline data={netValue} />
            </div>
          </Card>

          {/* Order form */}
          <OrderForm accountId={accountId} onSubmitted={loadAll} />

          {/* Listener control */}
          <ListenerControl onStatusChange={loadAll} />
        </div>

        {/* Right content */}
        <section className="flex-1 flex flex-col overflow-hidden">
          {/* Tabs */}
          <div className="flex items-center gap-1 overflow-x-auto pb-2 border-b border-white/5" data-testid="paper-trading-tabs">
            {tabs.map((tab) => (
              <button
                key={tab.key}
                type="button"
                onClick={() => setActiveTab(tab.key)}
                data-testid={`tab-${tab.key}`}
                className={`
                  px-3 py-1.5 rounded-lg text-xs font-medium whitespace-nowrap transition-colors
                  ${activeTab === tab.key
                    ? 'bg-cyan/10 text-cyan border border-cyan/30'
                    : 'text-muted hover:text-secondary hover:bg-white/5 border border-transparent'
                  }
                `}
              >
                {tab.label}
                {tab.count != null && tab.count > 0 && (
                  <span className="ml-1.5 px-1.5 py-0.5 rounded-full bg-white/10 text-white text-xxs">
                    {tab.count}
                  </span>
                )}
              </button>
            ))}
          </div>

          {/* Tab content */}
          <div className="flex-1 overflow-y-auto pt-3">
            {activeTab === 'positions' && <PositionsTable positions={positions} />}
            {activeTab === 'orders' && <OrdersTable orders={orders} onRefresh={loadAll} />}
            {activeTab === 'trades' && <TradesTable trades={trades} />}
            {activeTab === 'signals' && <SignalsTable signals={signals} />}
            {activeTab === 'decisions' && <DecisionsList decisions={decisions} />}
            {activeTab === 'reflections' && <ReflectionsList reflections={reflections} />}
            {activeTab === 'battle-plans' && <BattlePlansList plans={battlePlans} />}
          </div>
        </section>
      </main>
    </div>
  );
};

export default PaperTradingPage;
