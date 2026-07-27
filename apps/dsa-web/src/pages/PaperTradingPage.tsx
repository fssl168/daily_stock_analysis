import React, { useState, useEffect, useCallback, useMemo } from 'react';
import { paperTradingApi } from '../api/paperTrading';
import { Card, Badge } from '../components/common';
import type {
  AccountSnapshotResponse,
  BatchOrderResponse,
  BattlePlanItem,
  DailyReportResponse,
  DrawdownItem,
  NetValuePoint,
  OrderItem,
  PMDecisionItem,
  PerformanceMetricsResponse,
  PositionItem,
  ReflectionNoteItem,
  RiskMetricsResponse,
  SignalItem,
  TradeItem,
  ListenerStatusResponse,
  TradeResultResponse,
} from '../types/paperTrading';

type TabKey = 'positions' | 'orders' | 'trades' | 'signals' | 'decisions' | 'reflections' | 'battle-plans' | 'daily-report';

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

// ============ Performance Card ============

const DrawdownSparkline: React.FC<{ data: DrawdownItem[]; width?: number; height?: number }> = ({
  data,
  width = 280,
  height = 60,
}) => {
  if (data.length < 2) {
    return <div className="text-xs text-muted">No drawdown data</div>;
  }

  const values = data.map(d => d.drawdownPct);
  const min = Math.min(...values, 0);
  const max = 0;
  const range = max - min || 1;
  const padding = 4;
  const chartWidth = width - padding * 2;
  const chartHeight = height - padding * 2;

  const points = data.map((d, i) => {
    const x = padding + (i / (data.length - 1)) * chartWidth;
    const y = padding + ((max - d.drawdownPct) / range) * chartHeight;
    return `${x},${y}`;
  }).join(' ');

  const areaPoints = `${padding},${padding} ${points} ${width - padding},${padding}`;

  return (
    <svg width={width} height={height} className="overflow-visible">
      <defs>
        <linearGradient id="drawdownGradient" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stopColor="#ff4466" stopOpacity="0" />
          <stop offset="100%" stopColor="#ff4466" stopOpacity="0.3" />
        </linearGradient>
      </defs>
      <polygon points={areaPoints} fill="url(#drawdownGradient)" />
      <polyline
        points={points}
        fill="none"
        stroke="#ff4466"
        strokeWidth={1.5}
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
};

// ============ Performance Card ============

const PerformanceCard: React.FC<{ accountId: number }> = ({ accountId }) => {
  const [metrics, setMetrics] = useState<PerformanceMetricsResponse | null>(null);
  const [risk, setRisk] = useState<RiskMetricsResponse | null>(null);
  const [drawdown, setDrawdown] = useState<DrawdownItem[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const [m, r, dd] = await Promise.all([
        paperTradingApi.getPerformanceMetrics(accountId),
        paperTradingApi.getRiskMetrics(accountId),
        paperTradingApi.getDrawdownCurve(accountId).catch(() => [] as DrawdownItem[]),
      ]);
      setMetrics(m);
      setRisk(r);
      setDrawdown(dd);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load performance');
    } finally {
      setLoading(false);
    }
  }, [accountId]);

  useEffect(() => {
    load();
  }, [load]);

  return (
    <Card variant="gradient" padding="md">
      <div className="flex items-center justify-between">
        <span className="label-uppercase">Performance</span>
        <button
          type="button"
          onClick={load}
          disabled={loading}
          className="text-xs text-cyan hover:text-cyan/80 disabled:opacity-50"
          data-testid="refresh-performance-button"
        >
          {loading ? 'Loading...' : 'Refresh'}
        </button>
      </div>
      {metrics ? (
        <div className="mt-3 grid grid-cols-2 gap-3">
          <div>
            <p className="text-xxs text-muted uppercase">Total Return</p>
            <p className={`text-sm font-mono font-semibold ${metrics.totalReturnPct >= 0 ? 'text-emerald-400' : 'text-red-400'}`}>
              {formatPct(metrics.totalReturnPct)}
            </p>
          </div>
          <div>
            <p className="text-xxs text-muted uppercase">Sharpe Ratio</p>
            <p className="text-sm font-mono font-semibold text-white" data-testid="sharpe-ratio-value">
              {formatNumber(metrics.sharpeRatio ?? 0, 2)}
            </p>
          </div>
          <div>
            <p className="text-xxs text-muted uppercase">Max Drawdown</p>
            <p className="text-sm font-mono font-semibold text-red-400" data-testid="max-drawdown-value">
              {formatPct(metrics.maxDrawdownPct)}
            </p>
          </div>
          <div>
            <p className="text-xxs text-muted uppercase">Win Rate</p>
            <p className="text-sm font-mono font-semibold text-white" data-testid="win-rate-value">
              {metrics.winRate.toFixed(2)}%
            </p>
          </div>
        </div>
      ) : (
        <p className="mt-3 text-xs text-muted">Loading performance...</p>
      )}
      {drawdown.length >= 2 && (
        <div className="mt-3" data-testid="drawdown-chart">
          <p className="text-xxs text-muted uppercase mb-1">Drawdown Curve</p>
          <DrawdownSparkline data={drawdown} />
        </div>
      )}
      {risk && (
        <div className="mt-3 pt-3 border-t border-white/5 grid grid-cols-2 gap-3">
          <div>
            <p className="text-xxs text-muted uppercase">Concentration</p>
            <p className="text-sm font-mono font-semibold text-white">
              {risk.maxSingleStockConcentrationPct.toFixed(2)}%
            </p>
          </div>
          <div>
            <p className="text-xxs text-muted uppercase">Drawdown</p>
            <p className="text-sm font-mono font-semibold text-white">
              {risk.currentDrawdownPct.toFixed(2)}%
            </p>
          </div>
        </div>
      )}
      {error && <p className="mt-2 text-xs text-danger">{error}</p>}
    </Card>
  );
};

// ============ Order Form ============

type OrderMode = 'single' | 'batch' | 'conditional';

interface BatchRow {
  id: number;
  code: string;
  side: 'buy' | 'sell';
  quantity: string;
  orderType: 'market' | 'limit';
  limitPrice: string;
}

const OrderForm: React.FC<{ accountId: number; onSubmitted: () => void }> = ({ accountId, onSubmitted }) => {
  const [mode, setMode] = useState<OrderMode>('single');

  // Single order state
  const [code, setCode] = useState('');
  const [side, setSide] = useState<'buy' | 'sell'>('buy');
  const [quantity, setQuantity] = useState('');
  const [orderType, setOrderType] = useState<'market' | 'limit'>('market');
  const [limitPrice, setLimitPrice] = useState('');

  // Conditional order state
  const [conditionalType, setConditionalType] = useState<'stop_loss' | 'take_profit'>('stop_loss');
  const [triggerPrice, setTriggerPrice] = useState('');

  // Batch order state
  const [batchRows, setBatchRows] = useState<BatchRow[]>([
    { id: 1, code: '', side: 'buy', quantity: '', orderType: 'market', limitPrice: '' },
  ]);

  const [loading, setLoading] = useState(false);
  const [result, setResult] = useState<TradeResultResponse | null>(null);
  const [batchResult, setBatchResult] = useState<BatchOrderResponse | null>(null);
  const [conditionalResult, setConditionalResult] = useState<{ id: number; status: string } | null>(null);
  const [error, setError] = useState<string | null>(null);

  const resetResults = () => {
    setResult(null);
    setBatchResult(null);
    setConditionalResult(null);
    setError(null);
  };

  const handleSingleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setLoading(true);
    resetResults();
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

  const handleConditionalSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setLoading(true);
    resetResults();
    try {
      const qty = parseFloat(quantity);
      const trigger = parseFloat(triggerPrice);
      if (!code || Number.isNaN(qty) || qty <= 0 || Number.isNaN(trigger) || trigger <= 0) {
        throw new Error('Please enter a valid code, quantity and trigger price');
      }
      const res = await paperTradingApi.createConditionalOrder({
        accountId,
        code: code.toUpperCase(),
        side,
        quantity: qty,
        orderType: conditionalType,
        triggerPrice: trigger,
        limitPrice: orderType === 'limit' && limitPrice ? parseFloat(limitPrice) : undefined,
        reason: 'manual conditional order from WebUI',
      });
      setConditionalResult({ id: res.id, status: res.status });
      onSubmitted();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Conditional order failed');
    } finally {
      setLoading(false);
    }
  };

  const handleBatchSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setLoading(true);
    resetResults();
    try {
      const orders = batchRows
        .filter(row => row.code && row.quantity)
        .map(row => {
          const qty = parseFloat(row.quantity);
          if (Number.isNaN(qty) || qty <= 0) {
            throw new Error(`Invalid quantity for ${row.code}`);
          }
          return {
            code: row.code.toUpperCase(),
            side: row.side,
            quantity: qty,
            orderType: row.orderType,
            limitPrice: row.orderType === 'limit' && row.limitPrice ? parseFloat(row.limitPrice) : undefined,
          };
        });
      if (orders.length === 0) {
        throw new Error('Please add at least one valid order');
      }
      const res = await paperTradingApi.submitBatchOrders({ accountId, orders });
      setBatchResult(res);
      onSubmitted();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Batch order failed');
    } finally {
      setLoading(false);
    }
  };

  const updateBatchRow = (id: number, field: keyof BatchRow, value: string) => {
    setBatchRows(rows => rows.map(row => row.id === id ? { ...row, [field]: value } : row));
  };

  const addBatchRow = () => {
    setBatchRows(rows => [
      ...rows,
      { id: Date.now(), code: '', side: 'buy', quantity: '', orderType: 'market', limitPrice: '' },
    ]);
  };

  const removeBatchRow = (id: number) => {
    setBatchRows(rows => rows.filter(row => row.id !== id));
  };

  const renderResult = () => {
    if (result) {
      return (
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
      );
    }
    if (batchResult) {
      return (
        <div className="mt-3 p-2 rounded-lg bg-emerald-500/10 border border-emerald-500/20 text-xs space-y-1">
          <p className="text-emerald-400">BATCH SUBMITTED ({batchResult.total})</p>
          {batchResult.results.map((r: TradeResultResponse, i: number) => (
            <p key={i} className="text-secondary">
              {r.code}: {r.status.toUpperCase()}
              {r.fillPrice != null && ` @ ${formatNumber(r.fillPrice)}`}
            </p>
          ))}
        </div>
      );
    }
    if (conditionalResult) {
      return (
        <div className="mt-3 p-2 rounded-lg bg-emerald-500/10 border border-emerald-500/20 text-xs">
          <span className="text-emerald-400">CONDITIONAL CREATED</span>
          {' '}
          <span className="text-secondary">#{conditionalResult.id}</span>
        </div>
      );
    }
    return null;
  };

  return (
    <Card variant="gradient" padding="md">
      <span className="label-uppercase">Orders</span>
      <div className="mt-2 flex gap-1 p-1 rounded-lg bg-elevated">
        {(['single', 'batch', 'conditional'] as OrderMode[]).map(m => (
          <button
            key={m}
            type="button"
            onClick={() => { setMode(m); resetResults(); }}
            className={`flex-1 text-xs py-1.5 rounded-md transition-colors ${
              mode === m ? 'bg-cyan/20 text-cyan' : 'text-muted hover:text-secondary'
            }`}
            data-testid={`order-mode-${m}`}
          >
            {m === 'single' ? 'Single' : m === 'batch' ? 'Batch' : 'Conditional'}
          </button>
        ))}
      </div>

      {mode === 'single' && (
        <form onSubmit={handleSingleSubmit} className="mt-3 space-y-3">
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
      )}

      {mode === 'conditional' && (
        <form onSubmit={handleConditionalSubmit} className="mt-3 space-y-3">
          <div className="grid grid-cols-2 gap-3">
            <input
              type="text"
              value={code}
              onChange={(e) => setCode(e.target.value.toUpperCase())}
              placeholder="Code"
              className="input-terminal"
              data-testid="conditional-code-input"
            />
            <select
              value={side}
              onChange={(e) => setSide(e.target.value as 'buy' | 'sell')}
              className="input-terminal bg-elevated"
              data-testid="conditional-side-select"
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
              data-testid="conditional-quantity-input"
            />
            <select
              value={conditionalType}
              onChange={(e) => setConditionalType(e.target.value as 'stop_loss' | 'take_profit')}
              className="input-terminal bg-elevated"
              data-testid="conditional-type-select"
            >
              <option value="stop_loss">Stop Loss</option>
              <option value="take_profit">Take Profit</option>
            </select>
          </div>
          <input
            type="number"
            value={triggerPrice}
            onChange={(e) => setTriggerPrice(e.target.value)}
            placeholder="Trigger price"
            min={0.01}
            step={0.01}
            className="input-terminal"
            data-testid="conditional-trigger-price-input"
          />
          <div className="grid grid-cols-2 gap-3">
            <select
              value={orderType}
              onChange={(e) => setOrderType(e.target.value as 'market' | 'limit')}
              className="input-terminal bg-elevated"
              data-testid="conditional-order-type-select"
            >
              <option value="market">Market</option>
              <option value="limit">Limit</option>
            </select>
            {orderType === 'limit' && (
              <input
                type="number"
                value={limitPrice}
                onChange={(e) => setLimitPrice(e.target.value)}
                placeholder="Limit price"
                min={0.01}
                step={0.01}
                className="input-terminal"
                data-testid="conditional-limit-price-input"
              />
            )}
          </div>
          <button
            type="submit"
            disabled={loading}
            className="btn-primary w-full flex items-center justify-center gap-2"
            data-testid="conditional-submit-button"
          >
            {loading ? 'Submitting...' : 'Create Conditional Order'}
          </button>
        </form>
      )}

      {mode === 'batch' && (
        <form onSubmit={handleBatchSubmit} className="mt-3 space-y-3">
          <div className="max-h-64 overflow-y-auto space-y-2">
            {batchRows.map((row, index) => (
              <div key={row.id} className="p-2 rounded-lg bg-elevated border border-white/5 space-y-2">
                <div className="flex items-center justify-between">
                  <span className="text-xs text-muted">#{index + 1}</span>
                  {batchRows.length > 1 && (
                    <button
                      type="button"
                      onClick={() => removeBatchRow(row.id)}
                      className="text-xs text-danger hover:text-red-300"
                    >
                      Remove
                    </button>
                  )}
                </div>
                <div className="grid grid-cols-2 gap-2">
                  <input
                    type="text"
                    value={row.code}
                    onChange={(e) => updateBatchRow(row.id, 'code', e.target.value.toUpperCase())}
                    placeholder="Code"
                    className="input-terminal text-xs py-1.5"
                    data-testid={`batch-code-input-${index}`}
                  />
                  <select
                    value={row.side}
                    onChange={(e) => updateBatchRow(row.id, 'side', e.target.value)}
                    className="input-terminal bg-elevated text-xs py-1.5"
                    data-testid={`batch-side-select-${index}`}
                  >
                    <option value="buy">Buy</option>
                    <option value="sell">Sell</option>
                  </select>
                </div>
                <div className="grid grid-cols-2 gap-2">
                  <input
                    type="number"
                    value={row.quantity}
                    onChange={(e) => updateBatchRow(row.id, 'quantity', e.target.value)}
                    placeholder="Quantity"
                    min={0.01}
                    step={0.01}
                    className="input-terminal text-xs py-1.5"
                    data-testid={`batch-quantity-input-${index}`}
                  />
                  <select
                    value={row.orderType}
                    onChange={(e) => updateBatchRow(row.id, 'orderType', e.target.value)}
                    className="input-terminal bg-elevated text-xs py-1.5"
                    data-testid={`batch-type-select-${index}`}
                  >
                    <option value="market">Market</option>
                    <option value="limit">Limit</option>
                  </select>
                </div>
                {row.orderType === 'limit' && (
                  <input
                    type="number"
                    value={row.limitPrice}
                    onChange={(e) => updateBatchRow(row.id, 'limitPrice', e.target.value)}
                    placeholder="Limit price"
                    min={0.01}
                    step={0.01}
                    className="input-terminal text-xs py-1.5 w-full"
                    data-testid={`batch-limit-price-input-${index}`}
                  />
                )}
              </div>
            ))}
          </div>
          <div className="flex gap-2">
            <button
              type="button"
              onClick={addBatchRow}
              className="flex-1 btn-secondary text-xs py-2"
              data-testid="batch-add-row-button"
            >
              + Add Row
            </button>
            <button
              type="submit"
              disabled={loading}
              className="flex-1 btn-primary text-xs py-2"
              data-testid="batch-submit-button"
            >
              {loading ? 'Submitting...' : 'Submit Batch'}
            </button>
          </div>
        </form>
      )}

      {renderResult()}
      {error && (
        <p className="mt-3 text-xs text-danger" data-testid="order-error-message">{error}</p>
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

const OrdersTable: React.FC<{
  orders: OrderItem[];
  onRefresh: () => void;
  filters: { status: string; side: string; code: string };
  onFiltersChange: (filters: { status: string; side: string; code: string }) => void;
}> = ({ orders, onRefresh, filters, onFiltersChange }) => {
  const [actingId, setActingId] = useState<number | null>(null);
  const [modifyId, setModifyId] = useState<number | null>(null);
  const [modifyPrice, setModifyPrice] = useState('');
  const [modifyQty, setModifyQty] = useState('');
  const [modifyError, setModifyError] = useState<string | null>(null);

  const handleCancel = async (orderId: number) => {
    setActingId(orderId);
    try {
      await paperTradingApi.cancelOrder(orderId, 'cancelled from WebUI');
      onRefresh();
    } finally {
      setActingId(null);
    }
  };

  const handleModify = async (orderId: number) => {
    setActingId(orderId);
    setModifyError(null);
    try {
      const params: { newLimitPrice?: number; newQuantity?: number; reason: string } = {
        reason: 'modified from WebUI',
      };
      if (modifyPrice) {
        const price = parseFloat(modifyPrice);
        if (Number.isNaN(price) || price <= 0) {
          throw new Error('Invalid limit price');
        }
        params.newLimitPrice = price;
      }
      if (modifyQty) {
        const qty = parseFloat(modifyQty);
        if (Number.isNaN(qty) || qty <= 0) {
          throw new Error('Invalid quantity');
        }
        params.newQuantity = qty;
      }
      if (!params.newLimitPrice && !params.newQuantity) {
        throw new Error('Enter a new price or quantity to modify');
      }
      await paperTradingApi.modifyOrder(orderId, params);
      setModifyId(null);
      setModifyPrice('');
      setModifyQty('');
      onRefresh();
    } catch (err) {
      setModifyError(err instanceof Error ? err.message : 'Modify failed');
    } finally {
      setActingId(null);
    }
  };

  const startModify = (order: OrderItem) => {
    setModifyId(order.id);
    setModifyPrice(order.price ? String(order.price) : '');
    setModifyQty(String(order.quantity));
    setModifyError(null);
  };

  const filteredOrders = useMemo(() => {
    return orders.filter(o => {
      if (filters.status && o.status !== filters.status) return false;
      if (filters.side && o.side !== filters.side) return false;
      if (filters.code && !o.code.toLowerCase().includes(filters.code.toLowerCase())) return false;
      return true;
    });
  }, [orders, filters]);

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center gap-2 p-2 rounded-xl bg-elevated border border-white/5" data-testid="orders-filter-bar">
        <select
          value={filters.status}
          onChange={(e) => onFiltersChange({ ...filters, status: e.target.value })}
          className="input-terminal bg-elevated text-xs py-1.5"
          data-testid="orders-filter-status"
        >
          <option value="">All Status</option>
          <option value="pending">Pending</option>
          <option value="filled">Filled</option>
          <option value="cancelled">Cancelled</option>
          <option value="rejected">Rejected</option>
          <option value="conditional">Conditional</option>
        </select>
        <select
          value={filters.side}
          onChange={(e) => onFiltersChange({ ...filters, side: e.target.value })}
          className="input-terminal bg-elevated text-xs py-1.5"
          data-testid="orders-filter-side"
        >
          <option value="">All Sides</option>
          <option value="buy">Buy</option>
          <option value="sell">Sell</option>
        </select>
        <input
          type="text"
          value={filters.code}
          onChange={(e) => onFiltersChange({ ...filters, code: e.target.value.toUpperCase() })}
          placeholder="Filter code"
          className="input-terminal text-xs py-1.5 flex-1 min-w-[120px]"
          data-testid="orders-filter-code"
        />
        <span className="text-xs text-muted" data-testid="orders-filter-count">
          {filteredOrders.length} / {orders.length}
        </span>
      </div>

      {filteredOrders.length === 0 ? (
        <EmptyState message="No orders match the filters" />
      ) : (
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
              {filteredOrders.map((o) => (
                <React.Fragment key={o.id}>
                  <tr className="border-t border-white/5 hover:bg-hover transition-colors">
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
                        <div className="flex items-center gap-2">
                          <button
                            type="button"
                            onClick={() => handleCancel(o.id)}
                            disabled={actingId === o.id}
                            className="text-xs text-danger hover:text-red-300 disabled:opacity-50"
                            data-testid={`order-cancel-${o.id}`}
                          >
                            {actingId === o.id && modifyId !== o.id ? '...' : 'Cancel'}
                          </button>
                          {o.orderType === 'limit' && (
                            <button
                              type="button"
                              onClick={() => startModify(o)}
                              disabled={actingId === o.id}
                              className="text-xs text-cyan hover:text-cyan/80 disabled:opacity-50"
                              data-testid={`order-modify-${o.id}`}
                            >
                              Modify
                            </button>
                          )}
                        </div>
                      )}
                    </td>
                  </tr>
                  {modifyId === o.id && (
                    <tr className="border-t border-white/5 bg-elevated/50">
                      <td colSpan={9} className="px-3 py-3">
                        <div className="flex flex-wrap items-center gap-3" data-testid={`order-modify-form-${o.id}`}>
                          <span className="text-xs text-muted">Modify {o.code}:</span>
                          <input
                            type="number"
                            value={modifyPrice}
                            onChange={(e) => setModifyPrice(e.target.value)}
                            placeholder="New limit price"
                            min={0.01}
                            step={0.01}
                            className="input-terminal text-xs py-1.5 w-36"
                            data-testid="order-modify-price-input"
                          />
                          <input
                            type="number"
                            value={modifyQty}
                            onChange={(e) => setModifyQty(e.target.value)}
                            placeholder="New quantity"
                            min={0.01}
                            step={0.01}
                            className="input-terminal text-xs py-1.5 w-32"
                            data-testid="order-modify-quantity-input"
                          />
                          <button
                            type="button"
                            onClick={() => handleModify(o.id)}
                            disabled={actingId === o.id}
                            className="btn-primary text-xs py-1.5 px-3"
                            data-testid="order-modify-submit"
                          >
                            {actingId === o.id ? 'Saving...' : 'Save'}
                          </button>
                          <button
                            type="button"
                            onClick={() => { setModifyId(null); setModifyError(null); }}
                            className="btn-secondary text-xs py-1.5 px-3"
                            data-testid="order-modify-cancel"
                          >
                            Cancel
                          </button>
                          {modifyError && (
                            <span className="text-xs text-danger">{modifyError}</span>
                          )}
                        </div>
                      </td>
                    </tr>
                  )}
                </React.Fragment>
              ))}
            </tbody>
          </table>
        </div>
      )}
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

const SignalsTable: React.FC<{ signals: SignalItem[]; onRefresh: () => void }> = ({ signals, onRefresh }) => {
  const [actingId, setActingId] = useState<number | null>(null);
  const [modifyId, setModifyId] = useState<number | null>(null);
  const [modifyPrice, setModifyPrice] = useState('');
  const [modifyQty, setModifyQty] = useState('');
  const [modifyError, setModifyError] = useState<string | null>(null);

  if (signals.length === 0) {
    return <EmptyState message="No signals" />;
  }

  const handleCancel = async (signalId: number) => {
    setActingId(signalId);
    try {
      await paperTradingApi.cancelSignal(signalId, 'cancelled from WebUI');
      onRefresh();
    } finally {
      setActingId(null);
    }
  };

  const handleModify = async (signalId: number) => {
    setActingId(signalId);
    setModifyError(null);
    try {
      const params: { newLimitPrice?: number; newQuantity?: number; reason: string } = {
        reason: 'modified from WebUI',
      };
      if (modifyPrice) {
        const price = parseFloat(modifyPrice);
        if (Number.isNaN(price) || price <= 0) {
          throw new Error('Invalid limit price');
        }
        params.newLimitPrice = price;
      }
      if (modifyQty) {
        const qty = parseFloat(modifyQty);
        if (Number.isNaN(qty) || qty <= 0) {
          throw new Error('Invalid quantity');
        }
        params.newQuantity = qty;
      }
      if (!params.newLimitPrice && !params.newQuantity) {
        throw new Error('Enter a new price or quantity to modify');
      }
      await paperTradingApi.modifySignal(signalId, params);
      setModifyId(null);
      setModifyPrice('');
      setModifyQty('');
      onRefresh();
    } catch (err) {
      setModifyError(err instanceof Error ? err.message : 'Modify failed');
    } finally {
      setActingId(null);
    }
  };

  const startModify = (signal: SignalItem) => {
    setModifyId(signal.id);
    setModifyPrice(signal.triggerPrice ? String(signal.triggerPrice) : '');
    setModifyQty(signal.suggestedQuantity ? String(signal.suggestedQuantity) : '');
    setModifyError(null);
  };

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
            <th className="px-3 py-2.5 text-xs font-medium text-secondary uppercase">Action</th>
          </tr>
        </thead>
        <tbody>
          {signals.map((s) => (
            <React.Fragment key={s.id}>
              <tr className="border-t border-white/5 hover:bg-hover transition-colors">
                <td className="px-3 py-2 font-mono text-cyan text-xs">{s.code}</td>
                <td className="px-3 py-2">{sideBadge(s.side)}</td>
                <td className="px-3 py-2 text-xs text-right text-white">{formatNumber(s.triggerPrice)}</td>
                <td className="px-3 py-2 text-xs text-secondary">{s.strategyName || '--'}</td>
                <td className="px-3 py-2">{statusBadge(s.status)}</td>
                <td className="px-3 py-2 text-xs text-secondary">
                  {s.agentConfirmed == null ? '--' : s.agentConfirmed ? 'Confirmed' : 'Vetoed'}
                </td>
                <td className="px-3 py-2 text-xs text-muted">{formatDateTime(s.createdAt)}</td>
                <td className="px-3 py-2">
                  {s.status === 'pending' && (
                    <div className="flex items-center gap-2">
                      <button
                        type="button"
                        onClick={() => handleCancel(s.id)}
                        disabled={actingId === s.id}
                        className="text-xs text-danger hover:text-red-300 disabled:opacity-50"
                        data-testid={`signal-cancel-${s.id}`}
                      >
                        {actingId === s.id && modifyId !== s.id ? '...' : 'Cancel'}
                      </button>
                      <button
                        type="button"
                        onClick={() => startModify(s)}
                        disabled={actingId === s.id}
                        className="text-xs text-cyan hover:text-cyan/80 disabled:opacity-50"
                        data-testid={`signal-modify-${s.id}`}
                      >
                        Modify
                      </button>
                    </div>
                  )}
                </td>
              </tr>
              {modifyId === s.id && (
                <tr className="border-t border-white/5 bg-elevated/50">
                  <td colSpan={8} className="px-3 py-3">
                    <div className="flex flex-wrap items-center gap-3" data-testid={`signal-modify-form-${s.id}`}>
                      <span className="text-xs text-muted">Modify {s.code}:</span>
                      <input
                        type="number"
                        value={modifyPrice}
                        onChange={(e) => setModifyPrice(e.target.value)}
                        placeholder="New limit price"
                        min={0.01}
                        step={0.01}
                        className="input-terminal text-xs py-1.5 w-36"
                        data-testid="signal-modify-price-input"
                      />
                      <input
                        type="number"
                        value={modifyQty}
                        onChange={(e) => setModifyQty(e.target.value)}
                        placeholder="New quantity"
                        min={0.01}
                        step={0.01}
                        className="input-terminal text-xs py-1.5 w-32"
                        data-testid="signal-modify-quantity-input"
                      />
                      <button
                        type="button"
                        onClick={() => handleModify(s.id)}
                        disabled={actingId === s.id}
                        className="btn-primary text-xs py-1.5 px-3"
                        data-testid="signal-modify-submit"
                      >
                        {actingId === s.id ? 'Saving...' : 'Save'}
                      </button>
                      <button
                        type="button"
                        onClick={() => { setModifyId(null); setModifyError(null); }}
                        className="btn-secondary text-xs py-1.5 px-3"
                        data-testid="signal-modify-cancel"
                      >
                        Cancel
                      </button>
                      {modifyError && (
                        <span className="text-xs text-danger">{modifyError}</span>
                      )}
                    </div>
                  </td>
                </tr>
              )}
            </React.Fragment>
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
  const [expandedId, setExpandedId] = useState<number | null>(null);
  const [markdown, setMarkdown] = useState<string | null>(null);
  const [loadingMd, setLoadingMd] = useState(false);

  if (plans.length === 0) {
    return <EmptyState message="No battle plans" />;
  }

  const toggleMarkdown = async (planId: number) => {
    if (expandedId === planId) {
      setExpandedId(null);
      setMarkdown(null);
      return;
    }
    setExpandedId(planId);
    setMarkdown(null);
    setLoadingMd(true);
    try {
      const res = await paperTradingApi.getBattlePlanMarkdown(planId);
      setMarkdown(res.markdown);
    } catch {
      setMarkdown('Failed to load markdown');
    } finally {
      setLoadingMd(false);
    }
  };

  return (
    <div className="space-y-3">
      {plans.map((p) => (
        <div key={p.planId} className="p-3 rounded-xl bg-elevated border border-white/5">
          <div className="flex items-center justify-between">
            <span className="text-xs font-medium text-white">Battle Plan {p.date}</span>
            <div className="flex items-center gap-2">
              <button
                type="button"
                onClick={() => toggleMarkdown(p.planId)}
                className="text-xs text-cyan hover:text-cyan/80"
                data-testid={`battle-plan-md-${p.planId}`}
              >
                {expandedId === p.planId ? 'Hide MD' : 'View MD'}
              </button>
              <Badge variant={p.usedFallback ? 'warning' : 'success'}>
                {p.usedFallback ? 'fallback' : 'AI'}
              </Badge>
            </div>
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
          {expandedId === p.planId && (
            <div className="mt-3 pt-3 border-t border-white/5" data-testid={`battle-plan-md-content-${p.planId}`}>
              {loadingMd ? (
                <p className="text-xs text-muted">Loading markdown...</p>
              ) : (
                <pre className="text-xs text-secondary whitespace-pre-wrap font-mono max-h-96 overflow-y-auto">{markdown || 'No markdown available'}</pre>
              )}
            </div>
          )}
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

// ============ Daily Report Tab (P2-A) ============

const DailyReportTab: React.FC<{ accountId: number }> = ({ accountId }) => {
  const [report, setReport] = useState<DailyReportResponse | null>(null);
  const [reportDate, setReportDate] = useState(new Date().toISOString().slice(0, 10));
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const handleGenerate = async () => {
    setLoading(true);
    setError(null);
    try {
      const res = await paperTradingApi.generateDailyReport(accountId, true);
      setReport(res);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to generate report');
    } finally {
      setLoading(false);
    }
  };

  const handleFetch = async () => {
    setLoading(true);
    setError(null);
    try {
      const res = await paperTradingApi.getDailyReport(accountId, reportDate);
      setReport(res);
      if (res.error) {
        setError(res.error);
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load report');
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center gap-2 p-3 rounded-xl bg-elevated border border-white/5">
        <input
          type="date"
          value={reportDate}
          onChange={(e) => setReportDate(e.target.value)}
          className="input-terminal text-xs py-1.5"
          data-testid="daily-report-date-input"
        />
        <button
          type="button"
          onClick={handleFetch}
          disabled={loading}
          className="btn-secondary text-xs py-1.5 px-3"
          data-testid="daily-report-fetch-button"
        >
          {loading ? 'Loading...' : 'Load Report'}
        </button>
        <button
          type="button"
          onClick={handleGenerate}
          disabled={loading}
          className="btn-primary text-xs py-1.5 px-3"
          data-testid="daily-report-generate-button"
        >
          {loading ? 'Generating...' : 'Generate Today'}
        </button>
      </div>

      {error && (
        <p className="text-xs text-danger" data-testid="daily-report-error">{error}</p>
      )}

      {report && (
        <div className="p-3 rounded-xl bg-elevated border border-white/5" data-testid="daily-report-content">
          <div className="flex items-center justify-between mb-2">
            <span className="text-xs font-medium text-white">Daily Report - {report.date}</span>
            <div className="flex items-center gap-2">
              {report.usedFallback && <Badge variant="warning">fallback</Badge>}
              {report.reportPath && (
                <Badge variant="info">saved</Badge>
              )}
            </div>
          </div>
          {report.markdown ? (
            <pre className="text-xs text-secondary whitespace-pre-wrap font-mono max-h-[60vh] overflow-y-auto" data-testid="daily-report-markdown">
              {report.markdown}
            </pre>
          ) : (
            <p className="text-xs text-muted">No markdown content available</p>
          )}
        </div>
      )}

      {!report && !error && !loading && (
        <EmptyState message="No daily report loaded. Generate or load a report." />
      )}
    </div>
  );
};

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
  const [triggeringReflection, setTriggeringReflection] = useState(false);
  const [orderFilters, setOrderFilters] = useState({ status: '', side: '', code: '' });

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

  const handleTriggerReflection = async () => {
    setTriggeringReflection(true);
    try {
      await paperTradingApi.triggerDailyReflection({ accountId });
      await loadAll();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Daily reflection failed');
    } finally {
      setTriggeringReflection(false);
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
    { key: 'daily-report', label: 'Daily Report' },
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
              onClick={handleTriggerReflection}
              disabled={triggeringReflection}
              className="btn-secondary text-xs py-2 px-3"
              data-testid="trigger-reflection-button"
            >
              {triggeringReflection ? 'Reflecting...' : 'Trigger Reflection'}
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

          {/* Performance metrics */}
          <PerformanceCard accountId={accountId} />

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
            {activeTab === 'orders' && (
              <OrdersTable
                orders={orders}
                onRefresh={loadAll}
                filters={orderFilters}
                onFiltersChange={setOrderFilters}
              />
            )}
            {activeTab === 'trades' && <TradesTable trades={trades} />}
            {activeTab === 'signals' && <SignalsTable signals={signals} onRefresh={loadAll} />}
            {activeTab === 'decisions' && <DecisionsList decisions={decisions} />}
            {activeTab === 'reflections' && <ReflectionsList reflections={reflections} />}
            {activeTab === 'battle-plans' && <BattlePlansList plans={battlePlans} />}
            {activeTab === 'daily-report' && <DailyReportTab accountId={accountId} />}
          </div>
        </section>
      </main>
    </div>
  );
};

export default PaperTradingPage;
