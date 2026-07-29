import React, { useState, useEffect, useCallback, useMemo } from 'react';
import { paperTradingApi } from '../api/paperTrading';
import { Card, Badge } from '../components/common';
import { useWatchlist } from '../hooks/useWatchlist';
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

const STATUS_LABEL_MAP: Record<string, string> = {
  executed: '已执行',
  filled: '已成交',
  completed: '已完成',
  rejected: '已拒绝',
  cancelled: '已撤单',
  pending: '待成交',
  submitted: '已提交',
  conditional: '条件单',
};

function statusBadge(status: string) {
  const label = STATUS_LABEL_MAP[status] ?? status;
  switch (status) {
    case 'executed':
    case 'filled':
    case 'completed':
      return <Badge variant="success">{label}</Badge>;
    case 'rejected':
    case 'cancelled':
      return <Badge variant="danger">{label}</Badge>;
    case 'pending':
    case 'submitted':
      return <Badge variant="warning">{label}</Badge>;
    default:
      return <Badge variant="default">{label}</Badge>;
  }
}

function sideBadge(side: string) {
  const label = side === 'buy' ? '买入' : side === 'sell' ? '卖出' : side;
  return (
    <Badge
      variant={side === 'buy' ? 'success' : side === 'sell' ? 'danger' : 'default'}
      className="uppercase"
    >
      {label}
    </Badge>
  );
}

function orderTypeLabel(type: string) {
  const map: Record<string, string> = {
    market: '市价',
    limit: '限价',
    conditional: '条件',
    stop_loss: '止损',
    take_profit: '止盈',
  };
  return map[type] ?? type;
}

function actionLabel(action: string) {
  const map: Record<string, string> = {
    buy: '买入',
    sell: '卖出',
    hold: '持有',
  };
  return map[action] ?? action;
}

function moodLabel(mood: string) {
  const map: Record<string, string> = {
    good: '良好',
    bad: '不佳',
    neutral: '中性',
    happy: '良好',
    sad: '不佳',
  };
  return map[mood] ?? mood;
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
        暂无净值数据
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
    return <div className="text-xs text-muted">暂无回撤数据</div>;
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
      setError(err instanceof Error ? err.message : '加载绩效失败');
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
        <span className="label-uppercase">绩效</span>
        <button
          type="button"
          onClick={load}
          disabled={loading}
          className="text-xs text-cyan hover:text-cyan/80 disabled:opacity-50"
          data-testid="refresh-performance-button"
        >
          {loading ? '加载中...' : '刷新'}
        </button>
      </div>
      {metrics ? (
        <div className="mt-3 grid grid-cols-2 gap-3">
          <div>
            <p className="text-xxs text-muted uppercase">总收益</p>
            <p className={`text-sm font-mono font-semibold ${metrics.totalReturnPct >= 0 ? 'text-emerald-400' : 'text-red-400'}`}>
              {formatPct(metrics.totalReturnPct)}
            </p>
          </div>
          <div>
            <p className="text-xxs text-muted uppercase">夏普比率</p>
            <p className="text-sm font-mono font-semibold text-white" data-testid="sharpe-ratio-value">
              {formatNumber(metrics.sharpeRatio ?? 0, 2)}
            </p>
          </div>
          <div>
            <p className="text-xxs text-muted uppercase">最大回撤</p>
            <p className="text-sm font-mono font-semibold text-red-400" data-testid="max-drawdown-value">
              {formatPct(metrics.maxDrawdownPct)}
            </p>
          </div>
          <div>
            <p className="text-xxs text-muted uppercase">胜率</p>
            <p className="text-sm font-mono font-semibold text-white" data-testid="win-rate-value">
              {metrics.winRate.toFixed(2)}%
            </p>
          </div>
        </div>
      ) : (
        <p className="mt-3 text-xs text-muted">绩效加载中...</p>
      )}
      {drawdown.length >= 2 && (
        <div className="mt-3" data-testid="drawdown-chart">
          <p className="text-xxs text-muted uppercase mb-1">回撤曲线</p>
          <DrawdownSparkline data={drawdown} />
        </div>
      )}
      {risk && (
        <div className="mt-3 pt-3 border-t border-white/5 grid grid-cols-2 gap-3">
          <div>
            <p className="text-xxs text-muted uppercase">集中度</p>
            <p className="text-sm font-mono font-semibold text-white">
              {risk.maxSingleStockConcentrationPct.toFixed(2)}%
            </p>
          </div>
          <div>
            <p className="text-xxs text-muted uppercase">回撤</p>
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
        throw new Error('请输入有效的代码和数量');
      }
      const res = await paperTradingApi.submitOrder({
        accountId,
        code: code.toUpperCase(),
        side,
        quantity: qty,
        orderType,
        limitPrice: orderType === 'limit' && limitPrice ? parseFloat(limitPrice) : undefined,
        reason: 'WebUI 手动下单',
      });
      setResult(res);
      onSubmitted();
    } catch (err) {
      setError(err instanceof Error ? err.message : '下单失败');
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
        throw new Error('请输入有效的代码、数量和触发价');
      }
      const res = await paperTradingApi.createConditionalOrder({
        accountId,
        code: code.toUpperCase(),
        side,
        quantity: qty,
        orderType: conditionalType,
        triggerPrice: trigger,
        limitPrice: orderType === 'limit' && limitPrice ? parseFloat(limitPrice) : undefined,
        reason: 'WebUI 条件单',
      });
      setConditionalResult({ id: res.id, status: res.status });
      onSubmitted();
    } catch (err) {
      setError(err instanceof Error ? err.message : '条件单创建失败');
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
            throw new Error(`的数量无效${row.code}`);
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
        throw new Error('请至少添加一笔有效订单');
      }
      const res = await paperTradingApi.submitBatchOrders({ accountId, orders });
      setBatchResult(res);
      onSubmitted();
    } catch (err) {
      setError(err instanceof Error ? err.message : '批量下单失败');
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
          <span className="text-emerald-400">{STATUS_LABEL_MAP[result.status] ?? result.status}</span>
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
          <p className="text-emerald-400">批量提交 ({batchResult.total})</p>
          {batchResult.results.map((r: TradeResultResponse, i: number) => (
            <p key={i} className="text-secondary">
              {r.code}: {STATUS_LABEL_MAP[r.status] ?? r.status}
              {r.fillPrice != null && ` @ ${formatNumber(r.fillPrice)}`}
            </p>
          ))}
        </div>
      );
    }
    if (conditionalResult) {
      return (
        <div className="mt-3 p-2 rounded-lg bg-emerald-500/10 border border-emerald-500/20 text-xs">
          <span className="text-emerald-400">条件单已创建</span>
          {' '}
          <span className="text-secondary">#{conditionalResult.id}</span>
        </div>
      );
    }
    return null;
  };

  return (
    <Card variant="gradient" padding="md">
      <span className="label-uppercase">订单</span>
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
            {m === 'single' ? '单笔' : m === 'batch' ? '批量' : '条件'}
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
              placeholder="代码"
              className="input-terminal"
              data-testid="order-code-input"
            />
            <select
              value={side}
              onChange={(e) => setSide(e.target.value as 'buy' | 'sell')}
              className="input-terminal bg-elevated"
              data-testid="order-side-select"
            >
              <option value="buy">买入</option>
              <option value="sell">卖出</option>
            </select>
          </div>
          <div className="grid grid-cols-2 gap-3">
            <input
              type="number"
              value={quantity}
              onChange={(e) => setQuantity(e.target.value)}
              placeholder="数量"
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
              <option value="market">市价</option>
              <option value="limit">限价</option>
            </select>
          </div>
          {orderType === 'limit' && (
            <input
              type="number"
              value={limitPrice}
              onChange={(e) => setLimitPrice(e.target.value)}
              placeholder="限价"
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
            {loading ? '提交中...' : '提交订单'}
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
              placeholder="代码"
              className="input-terminal"
              data-testid="conditional-code-input"
            />
            <select
              value={side}
              onChange={(e) => setSide(e.target.value as 'buy' | 'sell')}
              className="input-terminal bg-elevated"
              data-testid="conditional-side-select"
            >
              <option value="buy">买入</option>
              <option value="sell">卖出</option>
            </select>
          </div>
          <div className="grid grid-cols-2 gap-3">
            <input
              type="number"
              value={quantity}
              onChange={(e) => setQuantity(e.target.value)}
              placeholder="数量"
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
              <option value="stop_loss">止损</option>
              <option value="take_profit">止盈</option>
            </select>
          </div>
          <input
            type="number"
            value={triggerPrice}
            onChange={(e) => setTriggerPrice(e.target.value)}
            placeholder="触发价"
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
              <option value="market">市价</option>
              <option value="limit">限价</option>
            </select>
            {orderType === 'limit' && (
              <input
                type="number"
                value={limitPrice}
                onChange={(e) => setLimitPrice(e.target.value)}
                placeholder="限价"
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
            {loading ? '提交中...' : '创建条件单'}
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
                      删除
                    </button>
                  )}
                </div>
                <div className="grid grid-cols-2 gap-2">
                  <input
                    type="text"
                    value={row.code}
                    onChange={(e) => updateBatchRow(row.id, 'code', e.target.value.toUpperCase())}
                    placeholder="代码"
                    className="input-terminal text-xs py-1.5"
                    data-testid={`batch-code-input-${index}`}
                  />
                  <select
                    value={row.side}
                    onChange={(e) => updateBatchRow(row.id, 'side', e.target.value)}
                    className="input-terminal bg-elevated text-xs py-1.5"
                    data-testid={`batch-side-select-${index}`}
                  >
                    <option value="buy">买入</option>
                    <option value="sell">卖出</option>
                  </select>
                </div>
                <div className="grid grid-cols-2 gap-2">
                  <input
                    type="number"
                    value={row.quantity}
                    onChange={(e) => updateBatchRow(row.id, 'quantity', e.target.value)}
                    placeholder="数量"
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
                    <option value="market">市价</option>
                    <option value="limit">限价</option>
                  </select>
                </div>
                {row.orderType === 'limit' && (
                  <input
                    type="number"
                    value={row.limitPrice}
                    onChange={(e) => updateBatchRow(row.id, 'limitPrice', e.target.value)}
                    placeholder="限价"
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
              + 添加行
            </button>
            <button
              type="submit"
              disabled={loading}
              className="flex-1 btn-primary text-xs py-2"
              data-testid="batch-submit-button"
            >
              {loading ? '提交中...' : '批量提交'}
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
  const { watchlistCodes, isLoading: watchlistLoading } = useWatchlist();

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
      await paperTradingApi.startListener({
        accountId: 1,
        watchedCodes: watchlistCodes,
      });
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
        <span className="label-uppercase">行情监听</span>
        <Badge variant={status?.running ? 'success' : 'default'}>
          {status?.running ? '运行中' : '已停止'}
        </Badge>
      </div>
      <div className="mt-2 text-xs text-secondary space-y-1">
        <p>账户：{status?.accountId ?? '--'}</p>
        <p>监控：{status?.watchedCodesCount ?? 0} 只代码{watchlistLoading ? '（加载自选股...）' : null}</p>
        <p>市场：{status?.markets?.join(', ') || '--'}</p>
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
            停止
          </button>
        ) : (
          <button
            type="button"
            onClick={handleStart}
            disabled={loading || watchlistLoading || watchlistCodes.length === 0}
            className="btn-primary flex-1 text-xs py-2"
            data-testid="listener-start-button"
          >
            启动
          </button>
        )}
      </div>
    </Card>
  );
};

// ============ Data Table Components ============

const PositionsTable: React.FC<{ positions: PositionItem[] }> = ({ positions }) => {
  if (positions.length === 0) {
    return <EmptyState message="暂无持仓" />;
  }
  return (
    <div className="overflow-x-auto rounded-xl border border-white/5">
      <table className="w-full text-sm">
        <thead>
          <tr className="bg-elevated text-left">
            <th className="px-3 py-2.5 text-xs font-medium text-secondary uppercase">代码</th>
            <th className="px-3 py-2.5 text-xs font-medium text-secondary uppercase text-right">数量</th>
            <th className="px-3 py-2.5 text-xs font-medium text-secondary uppercase text-right">持仓成本</th>
            <th className="px-3 py-2.5 text-xs font-medium text-secondary uppercase text-right">最新价</th>
            <th className="px-3 py-2.5 text-xs font-medium text-secondary uppercase text-right">止损/止盈1/止盈2</th>
            <th className="px-3 py-2.5 text-xs font-medium text-secondary uppercase text-right">盈亏</th>
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
      await paperTradingApi.cancelOrder(orderId, 'WebUI 撤单');
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
        reason: 'WebUI 改单',
      };
      if (modifyPrice) {
        const price = parseFloat(modifyPrice);
        if (Number.isNaN(price) || price <= 0) {
          throw new Error('限价无效');
        }
        params.newLimitPrice = price;
      }
      if (modifyQty) {
        const qty = parseFloat(modifyQty);
        if (Number.isNaN(qty) || qty <= 0) {
          throw new Error('数量无效');
        }
        params.newQuantity = qty;
      }
      if (!params.newLimitPrice && !params.newQuantity) {
        throw new Error('请输入新的限价或数量');
      }
      await paperTradingApi.modifyOrder(orderId, params);
      setModifyId(null);
      setModifyPrice('');
      setModifyQty('');
      onRefresh();
    } catch (err) {
      setModifyError(err instanceof Error ? err.message : '改单失败');
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
          <option value="">全部状态</option>
          <option value="pending">待成交</option>
          <option value="filled">已成交</option>
          <option value="cancelled">已撤单</option>
          <option value="rejected">已拒绝</option>
          <option value="conditional">条件单</option>
        </select>
        <select
          value={filters.side}
          onChange={(e) => onFiltersChange({ ...filters, side: e.target.value })}
          className="input-terminal bg-elevated text-xs py-1.5"
          data-testid="orders-filter-side"
        >
          <option value="">全部方向</option>
          <option value="buy">买入</option>
          <option value="sell">卖出</option>
        </select>
        <input
          type="text"
          value={filters.code}
          onChange={(e) => onFiltersChange({ ...filters, code: e.target.value.toUpperCase() })}
          placeholder="筛选代码"
          className="input-terminal text-xs py-1.5 flex-1 min-w-[120px]"
          data-testid="orders-filter-code"
        />
        <span className="text-xs text-muted" data-testid="orders-filter-count">
          {filteredOrders.length} / {orders.length}
        </span>
      </div>

      {filteredOrders.length === 0 ? (
        <EmptyState message="没有符合筛选条件的订单" />
      ) : (
        <div className="overflow-x-auto rounded-xl border border-white/5" data-testid="orders-table">
          <table className="w-full text-sm">
            <thead>
              <tr className="bg-elevated text-left">
                <th className="px-3 py-2.5 text-xs font-medium text-secondary uppercase">编号</th>
                <th className="px-3 py-2.5 text-xs font-medium text-secondary uppercase">代码</th>
                <th className="px-3 py-2.5 text-xs font-medium text-secondary uppercase">方向</th>
                <th className="px-3 py-2.5 text-xs font-medium text-secondary uppercase">类型</th>
                <th className="px-3 py-2.5 text-xs font-medium text-secondary uppercase text-right">数量</th>
                <th className="px-3 py-2.5 text-xs font-medium text-secondary uppercase text-right">已成交</th>
                <th className="px-3 py-2.5 text-xs font-medium text-secondary uppercase">状态</th>
                <th className="px-3 py-2.5 text-xs font-medium text-secondary uppercase">创建时间</th>
                <th className="px-3 py-2.5 text-xs font-medium text-secondary uppercase">操作</th>
              </tr>
            </thead>
            <tbody>
              {filteredOrders.map((o) => (
                <React.Fragment key={o.id}>
                  <tr className="border-t border-white/5 hover:bg-hover transition-colors">
                    <td className="px-3 py-2 text-xs text-muted">{o.id}</td>
                    <td className="px-3 py-2 font-mono text-cyan text-xs">{o.code}</td>
                    <td className="px-3 py-2">{sideBadge(o.side)}</td>
                    <td className="px-3 py-2 text-xs text-secondary">{orderTypeLabel(o.orderType)}</td>
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
                            {actingId === o.id && modifyId !== o.id ? '...' : '撤单'}
                          </button>
                          {o.orderType === 'limit' && (
                            <button
                              type="button"
                              onClick={() => startModify(o)}
                              disabled={actingId === o.id}
                              className="text-xs text-cyan hover:text-cyan/80 disabled:opacity-50"
                              data-testid={`order-modify-${o.id}`}
                            >
                              改单
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
                          <span className="text-xs text-muted">改单 {o.code}:</span>
                          <input
                            type="number"
                            value={modifyPrice}
                            onChange={(e) => setModifyPrice(e.target.value)}
                            placeholder="新限价"
                            min={0.01}
                            step={0.01}
                            className="input-terminal text-xs py-1.5 w-36"
                            data-testid="order-modify-price-input"
                          />
                          <input
                            type="number"
                            value={modifyQty}
                            onChange={(e) => setModifyQty(e.target.value)}
                            placeholder="新数量"
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
                            {actingId === o.id ? '保存中...' : '保存'}
                          </button>
                          <button
                            type="button"
                            onClick={() => { setModifyId(null); setModifyError(null); }}
                            className="btn-secondary text-xs py-1.5 px-3"
                            data-testid="order-modify-cancel"
                          >
                            取消
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
    return <EmptyState message="暂无成交" />;
  }
  return (
    <div className="overflow-x-auto rounded-xl border border-white/5" data-testid="trades-table">
      <table className="w-full text-sm">
        <thead>
          <tr className="bg-elevated text-left">
            <th className="px-3 py-2.5 text-xs font-medium text-secondary uppercase">代码</th>
            <th className="px-3 py-2.5 text-xs font-medium text-secondary uppercase">方向</th>
            <th className="px-3 py-2.5 text-xs font-medium text-secondary uppercase text-right">成交价</th>
            <th className="px-3 py-2.5 text-xs font-medium text-secondary uppercase text-right">数量</th>
            <th className="px-3 py-2.5 text-xs font-medium text-secondary uppercase text-right">手续费</th>
            <th className="px-3 py-2.5 text-xs font-medium text-secondary uppercase">成交时间</th>
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
    return <EmptyState message="暂无信号" />;
  }

  const handleCancel = async (signalId: number) => {
    setActingId(signalId);
    try {
      await paperTradingApi.cancelSignal(signalId, 'WebUI 撤单');
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
        reason: 'WebUI 改单',
      };
      if (modifyPrice) {
        const price = parseFloat(modifyPrice);
        if (Number.isNaN(price) || price <= 0) {
          throw new Error('限价无效');
        }
        params.newLimitPrice = price;
      }
      if (modifyQty) {
        const qty = parseFloat(modifyQty);
        if (Number.isNaN(qty) || qty <= 0) {
          throw new Error('数量无效');
        }
        params.newQuantity = qty;
      }
      if (!params.newLimitPrice && !params.newQuantity) {
        throw new Error('请输入新的限价或数量');
      }
      await paperTradingApi.modifySignal(signalId, params);
      setModifyId(null);
      setModifyPrice('');
      setModifyQty('');
      onRefresh();
    } catch (err) {
      setModifyError(err instanceof Error ? err.message : '改单失败');
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
            <th className="px-3 py-2.5 text-xs font-medium text-secondary uppercase">代码</th>
            <th className="px-3 py-2.5 text-xs font-medium text-secondary uppercase">方向</th>
            <th className="px-3 py-2.5 text-xs font-medium text-secondary uppercase text-right">触发价</th>
            <th className="px-3 py-2.5 text-xs font-medium text-secondary uppercase">策略</th>
            <th className="px-3 py-2.5 text-xs font-medium text-secondary uppercase">状态</th>
            <th className="px-3 py-2.5 text-xs font-medium text-secondary uppercase">智能体</th>
            <th className="px-3 py-2.5 text-xs font-medium text-secondary uppercase">创建时间</th>
            <th className="px-3 py-2.5 text-xs font-medium text-secondary uppercase">操作</th>
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
                  {s.agentConfirmed == null ? '--' : s.agentConfirmed ? '已确认' : '已否决'}
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
                        {actingId === s.id && modifyId !== s.id ? '...' : '撤单'}
                      </button>
                      <button
                        type="button"
                        onClick={() => startModify(s)}
                        disabled={actingId === s.id}
                        className="text-xs text-cyan hover:text-cyan/80 disabled:opacity-50"
                        data-testid={`signal-modify-${s.id}`}
                      >
                        改单
                      </button>
                    </div>
                  )}
                </td>
              </tr>
              {modifyId === s.id && (
                <tr className="border-t border-white/5 bg-elevated/50">
                  <td colSpan={8} className="px-3 py-3">
                    <div className="flex flex-wrap items-center gap-3" data-testid={`signal-modify-form-${s.id}`}>
                      <span className="text-xs text-muted">改单 {s.code}:</span>
                      <input
                        type="number"
                        value={modifyPrice}
                        onChange={(e) => setModifyPrice(e.target.value)}
                        placeholder="新限价"
                        min={0.01}
                        step={0.01}
                        className="input-terminal text-xs py-1.5 w-36"
                        data-testid="signal-modify-price-input"
                      />
                      <input
                        type="number"
                        value={modifyQty}
                        onChange={(e) => setModifyQty(e.target.value)}
                        placeholder="新数量"
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
                        {actingId === s.id ? '保存中...' : '保存'}
                      </button>
                      <button
                        type="button"
                        onClick={() => { setModifyId(null); setModifyError(null); }}
                        className="btn-secondary text-xs py-1.5 px-3"
                        data-testid="signal-modify-cancel"
                      >
                        取消
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
    return <EmptyState message="暂无 PM 决策" />;
  }
  return (
    <div className="space-y-2">
      {decisions.map((d) => (
        <div key={d.id} className="p-3 rounded-xl bg-elevated border border-white/5">
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-2">
              <Badge variant={d.action === 'buy' ? 'success' : d.action === 'sell' ? 'danger' : 'info'}>
                {actionLabel(d.action)}
              </Badge>
              {d.code && <span className="font-mono text-cyan text-xs">{d.code}</span>}
            </div>
            <span className="text-xs text-muted">{formatDateTime(d.createdAt)}</span>
          </div>
          <p className="mt-2 text-xs text-secondary">{d.reason || '未提供理由'}</p>
          <div className="mt-2 flex items-center gap-3 text-xs text-muted">
            <span>置信度：{(d.confidence * 100).toFixed(0)}%</span>
            {d.usedFallback && <Badge variant="warning">降级</Badge>}
          </div>
        </div>
      ))}
    </div>
  );
};

const ReflectionsList: React.FC<{ reflections: ReflectionNoteItem[] }> = ({ reflections }) => {
  if (reflections.length === 0) {
    return <EmptyState message="暂无复盘笔记" />;
  }
  return (
    <div className="space-y-3">
      {reflections.map((r) => (
        <div key={r.id} className="p-3 rounded-xl bg-elevated border border-white/5">
          <div className="flex items-center justify-between">
            <span className="text-xs font-medium text-white">{r.subject || '复盘'}</span>
            <Badge variant={r.mood === 'good' ? 'success' : r.mood === 'bad' ? 'danger' : 'default'}>
              {moodLabel(r.mood)}
            </Badge>
          </div>
          <p className="mt-1 text-xs text-secondary">{r.summary}</p>
          {r.takeaway && (
            <p className="mt-2 text-xs text-cyan">心得：{r.takeaway}</p>
          )}
          {r.lessons.length > 0 && (
            <ul className="mt-2 space-y-1">
              {r.lessons.map((lesson, i) => (
                <li key={i} className="text-xs text-muted list-disc list-inside">{lesson}</li>
              ))}
            </ul>
          )}
          <div className="mt-2 text-xs text-muted">
            {r.code && <span className="mr-2">代码：{r.code}</span>}
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
    return <EmptyState message="暂无作战卡" />;
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
      setMarkdown('Markdown 加载失败');
    } finally {
      setLoadingMd(false);
    }
  };

  return (
    <div className="space-y-3">
      {plans.map((p) => (
        <div key={p.planId} className="p-3 rounded-xl bg-elevated border border-white/5">
          <div className="flex items-center justify-between">
            <span className="text-xs font-medium text-white">作战卡 {p.date}</span>
            <div className="flex items-center gap-2">
              <button
                type="button"
                onClick={() => toggleMarkdown(p.planId)}
                className="text-xs text-cyan hover:text-cyan/80"
                data-testid={`battle-plan-md-${p.planId}`}
              >
                {expandedId === p.planId ? '隐藏原文' : '查看原文'}
              </button>
              <Badge variant={p.usedFallback ? 'warning' : 'success'}>
                {p.usedFallback ? '降级' : 'AI'}
              </Badge>
            </div>
          </div>
          <p className="mt-1 text-xs text-secondary">{p.marketReview || '无市场回顾'}</p>
          {p.mainTheme && (
            <p className="mt-1 text-xs text-cyan">主题：{p.mainTheme}</p>
          )}
          <div className="mt-2 grid grid-cols-2 gap-2">
            <div>
              <span className="text-xxs text-muted uppercase">持仓计划</span>
              <p className="text-xs text-secondary">{p.holdingsPlans.map(h => h.code).join(', ') || '--'}</p>
            </div>
            <div>
              <span className="text-xxs text-muted uppercase">候选标的</span>
              <p className="text-xs text-secondary">{p.candidates.map(c => c.code).join(', ') || '--'}</p>
            </div>
          </div>
          {expandedId === p.planId && (
            <div className="mt-3 pt-3 border-t border-white/5" data-testid={`battle-plan-md-content-${p.planId}`}>
              {loadingMd ? (
                <p className="text-xs text-muted">Markdown 加载中...</p>
              ) : (
                <pre className="text-xs text-secondary whitespace-pre-wrap font-mono max-h-96 overflow-y-auto">{markdown || '暂无 Markdown'}</pre>
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
      setError(err instanceof Error ? err.message : '生成日报失败');
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
      setError(err instanceof Error ? err.message : '加载日报失败');
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
          {loading ? '加载中...' : '加载日报'}
        </button>
        <button
          type="button"
          onClick={handleGenerate}
          disabled={loading}
          className="btn-primary text-xs py-1.5 px-3"
          data-testid="daily-report-generate-button"
        >
          {loading ? '生成中...' : '生成今日日报'}
        </button>
      </div>

      {error && (
        <p className="text-xs text-danger" data-testid="daily-report-error">{error}</p>
      )}

      {report && (
        <div className="p-3 rounded-xl bg-elevated border border-white/5" data-testid="daily-report-content">
          <div className="flex items-center justify-between mb-2">
            <span className="text-xs font-medium text-white">日报 - {report.date}</span>
            <div className="flex items-center gap-2">
              {report.usedFallback && <Badge variant="warning">降级</Badge>}
              {report.reportPath && (
                <Badge variant="info">已保存</Badge>
              )}
            </div>
          </div>
          {report.markdown ? (
            <pre className="text-xs text-secondary whitespace-pre-wrap font-mono max-h-[60vh] overflow-y-auto" data-testid="daily-report-markdown">
              {report.markdown}
            </pre>
          ) : (
            <p className="text-xs text-muted">暂无 Markdown 内容</p>
          )}
        </div>
      )}

      {!report && !error && !loading && (
        <EmptyState message="尚未加载日报，请生成或加载。" />
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
      setError(err instanceof Error ? err.message : '加载纸面交易数据失败');
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
      setError(err instanceof Error ? err.message : '创建账户失败');
    }
  };

  const handleTriggerPm = async () => {
    setTriggeringPm(true);
    try {
      await paperTradingApi.triggerPMDecision({ accountId });
      await loadAll();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'PM 决策失败');
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
      setError(err instanceof Error ? err.message : '生成作战卡失败');
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
      setError(err instanceof Error ? err.message : '触发复盘失败');
    } finally {
      setTriggeringReflection(false);
    }
  };

  const tabs: { key: TabKey; label: string; count?: number }[] = useMemo(() => [
    { key: 'positions', label: '持仓', count: positions.length },
    { key: 'orders', label: '订单', count: orders.length },
    { key: 'trades', label: '成交', count: trades.length },
    { key: 'signals', label: '信号', count: signals.length },
    { key: 'decisions', label: 'PM 决策', count: decisions.length },
    { key: 'reflections', label: '复盘', count: reflections.length },
    { key: 'battle-plans', label: '作战卡', count: battlePlans.length },
    { key: 'daily-report', label: '日报' },
  ], [positions.length, orders.length, trades.length, signals.length, decisions.length, reflections.length, battlePlans.length]);

  return (
    <div className="min-h-screen flex flex-col">
      {/* Header */}
      <header className="flex-shrink-0 px-4 py-3 border-b border-white/5" data-testid="paper-trading-header">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-3">
            <h1 className="text-lg font-semibold text-white" data-testid="paper-trading-title">纸面交易</h1>
            <div className="flex items-center gap-2">
              <input
                type="number"
                value={accountInput}
                onChange={(e) => setAccountInput(e.target.value)}
                placeholder="账户 ID"
                className="input-terminal w-24 text-xs py-2"
                data-testid="account-id-input"
              />
              <button
                type="button"
                onClick={handleAccountSwitch}
                className="btn-secondary text-xs py-2 px-3"
                data-testid="account-switch-button"
              >
                切换
              </button>
              <button
                type="button"
                onClick={handleCreateAccount}
                className="btn-secondary text-xs py-2 px-3"
                data-testid="account-reset-button"
              >
                重置默认
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
              {triggeringPm ? 'PM 思考中...' : '触发 PM'}
            </button>
            <button
              type="button"
              onClick={handleTriggerReflection}
              disabled={triggeringReflection}
              className="btn-secondary text-xs py-2 px-3"
              data-testid="trigger-reflection-button"
            >
              {triggeringReflection ? '复盘中...' : '触发复盘'}
            </button>
            <button
              type="button"
              onClick={handleGeneratePlan}
              disabled={generatingPlan}
              className="btn-secondary text-xs py-2 px-3"
              data-testid="generate-plan-button"
            >
              {generatingPlan ? '生成中...' : '生成作战卡'}
            </button>
            <button
              type="button"
              onClick={loadAll}
              disabled={loading}
              className="btn-primary text-xs py-2 px-3"
              data-testid="refresh-button"
            >
              {loading ? '加载中...' : '刷新'}
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
            <span className="label-uppercase">账户 #{accountId}</span>
            {snapshot ? (
              <div className="mt-3 grid grid-cols-2 gap-3">
                <div>
                  <p className="text-xxs text-muted uppercase">净值</p>
                  <p className="text-lg font-mono font-semibold text-white">{formatNumber(snapshot.netValue)}</p>
                </div>
                <div>
                  <p className="text-xxs text-muted uppercase">收益</p>
                  <p className={`text-lg font-mono font-semibold ${snapshot.returnPct >= 0 ? 'text-emerald-400' : 'text-red-400'}`}>
                    {formatPct(snapshot.returnPct)}
                  </p>
                </div>
                <div>
                  <p className="text-xxs text-muted uppercase">现金</p>
                  <p className="text-sm font-mono text-secondary">{formatNumber(snapshot.cash)}</p>
                </div>
                <div>
                  <p className="text-xxs text-muted uppercase">持仓</p>
                  <p className="text-sm font-mono text-secondary">{snapshot.positionCount}</p>
                </div>
              </div>
            ) : (
              <p className="mt-3 text-xs text-muted">账户加载中...</p>
            )}
          </Card>

          {/* Net value curve */}
          <Card variant="gradient" padding="md">
            <span className="label-uppercase">净值曲线</span>
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
