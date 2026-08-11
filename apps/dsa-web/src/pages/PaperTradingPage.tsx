import React, { useState, useEffect, useCallback, useMemo } from 'react';
import { paperTradingApi } from '../api/paperTrading';
import { Card, Badge } from '../components/common';
import { BacktestComparisonPanel } from '../components/paper-trading';
import { BreakerStatusBadge } from '../components/paper-trading/BreakerStatusBadge';
import { HealthDashboard } from '../components/paper-trading/HealthDashboard';
import { QuoteTicker } from '../components/paper-trading/QuoteTicker';
import { ExtremeMarketBanner } from '../components/paper-trading/ExtremeMarketBanner';
import { RiskAlertToast } from '../components/paper-trading/RiskAlertToast';
import { LatencyPanel } from '../components/paper-trading/LatencyPanel';
import { L2DepthPanel } from '../components/paper-trading/L2DepthPanel';
import { MarketStatusDashboard } from '../components/paper-trading/MarketStatusDashboard';
import { StrategyLeaderboard } from '../components/paper-trading/StrategyLeaderboard';
import { StrategyLifecyclePanel } from '../components/paper-trading/StrategyLifecyclePanel';
import { DriftPanel } from '../components/paper-trading/DriftPanel';
import { FeaturesPanel } from '../components/paper-trading/FeaturesPanel';
import { EventLogFeed } from '../components/paper-trading/EventLogFeed';
import { useUiLanguage } from '../contexts/UiLanguageContext';
import { formatUiText } from '../i18n/uiText';
import type {
  AccountListItem,
  AccountSnapshotResponse,
  AccountUpdateRequest,
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

type TabKey = 'positions' | 'orders' | 'trades' | 'signals' | 'decisions' | 'reflections' | 'battle-plans' | 'daily-report' | 'backtest-comparison' | 'strategies' | 'features';

// ============ Helpers ============

function formatNumber(value?: number | null, digits = 2): string {
  if (value == null) return '--';
  return value.toFixed(digits);
}

function formatPct(value?: number | null): string {
  if (value == null) return '--';
  return `${value >= 0 ? '+' : ''}${value.toFixed(2)}%`;
}

function statusBadge(status: string, t: (key: import('../i18n/uiText').UiTextKey) => string) {
  const label = t(`paperTrading.status.${status}` as import('../i18n/uiText').UiTextKey) || status;
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

function sideBadge(side: string, t: (key: import('../i18n/uiText').UiTextKey) => string) {
  const label = side === 'buy'
    ? t('paperTrading.order.side.buy')
    : side === 'sell'
      ? t('paperTrading.order.side.sell')
      : side;
  return (
    <Badge
      variant={side === 'buy' ? 'success' : side === 'sell' ? 'danger' : 'default'}
    >
      {label}
    </Badge>
  );
}

function formatDateTime(value?: string | null, language?: string): string {
  if (!value) return '--';
  try {
    return new Date(value).toLocaleString(language === 'en' ? 'en-US' : 'zh-CN', {
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
  const { t } = useUiLanguage();
  if (data.length < 2) {
    return (
      <div className="flex items-center justify-center h-full text-xs text-muted">
        {t('paperTrading.noNetValueData')}
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
    return <div className="text-xs text-muted">--</div>;
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
  const { t } = useUiLanguage();
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
      setError(err instanceof Error ? err.message : t('paperTrading.performance.loadError'));
    } finally {
      setLoading(false);
    }
  }, [accountId, t]);

  useEffect(() => {
    load();
  }, [load]);

  return (
    <Card variant="gradient" padding="md">
      <div className="flex items-center justify-between">
        <span className="label-uppercase">{t('paperTrading.performance.metrics')}</span>
        <button
          type="button"
          onClick={load}
          disabled={loading}
          className="text-xs text-cyan hover:text-cyan/80 disabled:opacity-50"
          data-testid="refresh-performance-button"
        >
          {loading ? t('paperTrading.performance.loading') : t('paperTrading.performance.refresh')}
        </button>
      </div>
      {metrics ? (
        <div className="mt-3 grid grid-cols-2 gap-3">
          <div>
            <p className="text-xxs text-muted uppercase">{t('paperTrading.performance.totalReturnPct')}</p>
            <p className={`text-sm font-mono font-semibold ${metrics.totalReturnPct >= 0 ? 'text-emerald-400' : 'text-red-400'}`}>
              {formatPct(metrics.totalReturnPct)}
            </p>
          </div>
          <div>
            <p className="text-xxs text-muted uppercase">{t('paperTrading.performance.sharpeRatio')}</p>
            <p className="text-sm font-mono font-semibold text-white" data-testid="sharpe-ratio-value">
              {formatNumber(metrics.sharpeRatio ?? 0, 2)}
            </p>
          </div>
          <div>
            <p className="text-xxs text-muted uppercase">{t('paperTrading.performance.maxDrawdownPct')}</p>
            <p className="text-sm font-mono font-semibold text-red-400" data-testid="max-drawdown-value">
              {formatPct(metrics.maxDrawdownPct)}
            </p>
          </div>
          <div>
            <p className="text-xxs text-muted uppercase">{t('paperTrading.performance.winRate')}</p>
            <p className="text-sm font-mono font-semibold text-white" data-testid="win-rate-value">
              {metrics.winRate.toFixed(2)}%
            </p>
          </div>
        </div>
      ) : (
        <p className="mt-3 text-xs text-muted">{t('paperTrading.performance.loadingData')}</p>
      )}
      {drawdown.length >= 2 && (
        <div className="mt-3" data-testid="drawdown-chart">
          <p className="text-xxs text-muted uppercase mb-1">{t('paperTrading.performance.drawdownCurve')}</p>
          <DrawdownSparkline data={drawdown} />
        </div>
      )}
      {risk && (
        <div className="mt-3 pt-3 border-t border-white/5 grid grid-cols-2 gap-3">
          <div>
            <p className="text-xxs text-muted uppercase">{t('paperTrading.performance.concentration')}</p>
            <p className="text-sm font-mono font-semibold text-white">
              {risk.maxSingleStockConcentrationPct.toFixed(2)}%
            </p>
          </div>
          <div>
            <p className="text-xxs text-muted uppercase">{t('paperTrading.performance.currentDrawdown')}</p>
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
  const { t } = useUiLanguage();
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
        throw new Error(t('paperTrading.order.error.invalidSingle'));
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
      setError(err instanceof Error ? err.message : t('paperTrading.order.error.singleFailed'));
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
        throw new Error(t('paperTrading.order.error.invalidConditional'));
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
      setError(err instanceof Error ? err.message : t('paperTrading.order.error.conditionalFailed'));
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
            throw new Error(formatUiText(t('paperTrading.order.error.invalidQuantity'), { code: row.code }));
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
        throw new Error(t('paperTrading.order.error.noOrders'));
      }
      const res = await paperTradingApi.submitBatchOrders({ accountId, orders });
      setBatchResult(res);
      onSubmitted();
    } catch (err) {
      setError(err instanceof Error ? err.message : t('paperTrading.order.error.batchFailed'));
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
          <p className="text-emerald-400">{formatUiText(t('paperTrading.order.result.batchSubmitted'), { total: batchResult.total })}</p>
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
          <span className="text-emerald-400">{t('paperTrading.order.result.conditionalCreated')}</span>
          {' '}
          <span className="text-secondary">#{conditionalResult.id}</span>
        </div>
      );
    }
    return null;
  };

  return (
    <Card variant="gradient" padding="md">
      <span className="label-uppercase">{t('paperTrading.order.title')}</span>
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
            {t(`paperTrading.order.mode.${m}`)}
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
              placeholder={t('paperTrading.order.placeholder.code')}
              className="input-terminal"
              data-testid="order-code-input"
            />
            <select
              value={side}
              onChange={(e) => setSide(e.target.value as 'buy' | 'sell')}
              className="input-terminal bg-elevated"
              data-testid="order-side-select"
            >
              <option value="buy">{t('paperTrading.order.side.buy')}</option>
              <option value="sell">{t('paperTrading.order.side.sell')}</option>
            </select>
          </div>
          <div className="grid grid-cols-2 gap-3">
            <input
              type="number"
              value={quantity}
              onChange={(e) => setQuantity(e.target.value)}
              placeholder={t('paperTrading.order.placeholder.quantity')}
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
              <option value="market">{t('paperTrading.order.type.market')}</option>
              <option value="limit">{t('paperTrading.order.type.limit')}</option>
            </select>
          </div>
          {orderType === 'limit' && (
            <input
              type="number"
              value={limitPrice}
              onChange={(e) => setLimitPrice(e.target.value)}
              placeholder={t('paperTrading.order.placeholder.limitPrice')}
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
            {loading ? t('common.processing') : t('paperTrading.order.submit')}
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
              placeholder={t('paperTrading.order.placeholder.code')}
              className="input-terminal"
              data-testid="conditional-code-input"
            />
            <select
              value={side}
              onChange={(e) => setSide(e.target.value as 'buy' | 'sell')}
              className="input-terminal bg-elevated"
              data-testid="conditional-side-select"
            >
              <option value="buy">{t('paperTrading.order.side.buy')}</option>
              <option value="sell">{t('paperTrading.order.side.sell')}</option>
            </select>
          </div>
          <div className="grid grid-cols-2 gap-3">
            <input
              type="number"
              value={quantity}
              onChange={(e) => setQuantity(e.target.value)}
              placeholder={t('paperTrading.order.placeholder.quantity')}
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
              <option value="stop_loss">{t('paperTrading.order.conditionalType.stopLoss')}</option>
              <option value="take_profit">{t('paperTrading.order.conditionalType.takeProfit')}</option>
            </select>
          </div>
          <input
            type="number"
            value={triggerPrice}
            onChange={(e) => setTriggerPrice(e.target.value)}
            placeholder={t('paperTrading.order.placeholder.triggerPrice')}
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
              <option value="market">{t('paperTrading.order.type.market')}</option>
              <option value="limit">{t('paperTrading.order.type.limit')}</option>
            </select>
            {orderType === 'limit' && (
              <input
                type="number"
                value={limitPrice}
                onChange={(e) => setLimitPrice(e.target.value)}
                placeholder={t('paperTrading.order.placeholder.limitPrice')}
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
            {loading ? t('common.processing') : t('paperTrading.order.createConditional')}
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
                      {t('paperTrading.order.removeRow')}
                    </button>
                  )}
                </div>
                <div className="grid grid-cols-2 gap-2">
                  <input
                    type="text"
                    value={row.code}
                    onChange={(e) => updateBatchRow(row.id, 'code', e.target.value.toUpperCase())}
                    placeholder={t('paperTrading.order.placeholder.code')}
                    className="input-terminal text-xs py-1.5"
                    data-testid={`batch-code-input-${index}`}
                  />
                  <select
                    value={row.side}
                    onChange={(e) => updateBatchRow(row.id, 'side', e.target.value)}
                    className="input-terminal bg-elevated text-xs py-1.5"
                    data-testid={`batch-side-select-${index}`}
                  >
                    <option value="buy">{t('paperTrading.order.side.buy')}</option>
                    <option value="sell">{t('paperTrading.order.side.sell')}</option>
                  </select>
                </div>
                <div className="grid grid-cols-2 gap-2">
                  <input
                    type="number"
                    value={row.quantity}
                    onChange={(e) => updateBatchRow(row.id, 'quantity', e.target.value)}
                    placeholder={t('paperTrading.order.placeholder.quantity')}
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
                    <option value="market">{t('paperTrading.order.type.market')}</option>
                    <option value="limit">{t('paperTrading.order.type.limit')}</option>
                  </select>
                </div>
                {row.orderType === 'limit' && (
                  <input
                    type="number"
                    value={row.limitPrice}
                    onChange={(e) => updateBatchRow(row.id, 'limitPrice', e.target.value)}
                    placeholder={t('paperTrading.order.placeholder.limitPrice')}
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
              {t('paperTrading.order.addRow')}
            </button>
            <button
              type="submit"
              disabled={loading}
              className="flex-1 btn-primary text-xs py-2"
              data-testid="batch-submit-button"
            >
              {loading ? t('common.processing') : t('paperTrading.order.batchSubmit')}
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
  const { t } = useUiLanguage();
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
        <span className="label-uppercase">{t('paperTrading.listener.title')}</span>
        <Badge variant={status?.running ? 'success' : 'default'}>
          {status?.running ? t('paperTrading.listener.status.running') : t('paperTrading.listener.status.stopped')}
        </Badge>
      </div>
      <div className="mt-2 text-xs text-secondary space-y-1">
        <p>{t('paperTrading.listener.account')}: {status?.accountId ?? '--'}</p>
        <p>{t('paperTrading.listener.watched')}: {status?.watchedCodesCount ?? 0} {t('paperTrading.listener.codes')}</p>
        <p>{t('paperTrading.listener.markets')}: {status?.markets?.join(', ') || '--'}</p>
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
            {t('paperTrading.listener.stop')}
          </button>
        ) : (
          <button
            type="button"
            onClick={handleStart}
            disabled={loading}
            className="btn-primary flex-1 text-xs py-2"
            data-testid="listener-start-button"
          >
            {t('paperTrading.listener.start')}
          </button>
        )}
      </div>
    </Card>
  );
};

// ============ Data Table Components ============

const PositionsTable: React.FC<{ positions: PositionItem[] }> = ({ positions }) => {
  const { t } = useUiLanguage();
  if (positions.length === 0) {
    return <EmptyState message={t('paperTrading.positions.empty')} />;
  }
  return (
    <div className="overflow-x-auto rounded-xl border border-white/5">
      <table className="w-full text-sm">
        <thead>
          <tr className="bg-elevated text-left">
            <th className="px-3 py-2.5 text-xs font-medium text-secondary uppercase">{t('paperTrading.positions.code')}</th>
            <th className="px-3 py-2.5 text-xs font-medium text-secondary uppercase text-right">{t('paperTrading.positions.quantity')}</th>
            <th className="px-3 py-2.5 text-xs font-medium text-secondary uppercase text-right">{t('paperTrading.positions.avgCost')}</th>
            <th className="px-3 py-2.5 text-xs font-medium text-secondary uppercase text-right">{t('paperTrading.positions.lastPrice')}</th>
            <th className="px-3 py-2.5 text-xs font-medium text-secondary uppercase text-right">{t('paperTrading.positions.slTp')}</th>
            <th className="px-3 py-2.5 text-xs font-medium text-secondary uppercase text-right">{t('paperTrading.positions.pnl')}</th>
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
  const { t } = useUiLanguage();
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
          throw new Error(t('paperTrading.orders.modifyError.invalidPrice'));
        }
        params.newLimitPrice = price;
      }
      if (modifyQty) {
        const qty = parseFloat(modifyQty);
        if (Number.isNaN(qty) || qty <= 0) {
          throw new Error(t('paperTrading.orders.modifyError.invalidQuantity'));
        }
        params.newQuantity = qty;
      }
      if (!params.newLimitPrice && !params.newQuantity) {
        throw new Error(t('paperTrading.orders.modifyError.empty'));
      }
      await paperTradingApi.modifyOrder(orderId, params);
      setModifyId(null);
      setModifyPrice('');
      setModifyQty('');
      onRefresh();
    } catch (err) {
      setModifyError(err instanceof Error ? err.message : t('paperTrading.orders.modifyError.failed'));
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
          <option value="">{t('paperTrading.orders.filter.allStatus')}</option>
          <option value="pending">{t('paperTrading.orders.filter.pending')}</option>
          <option value="filled">{t('paperTrading.orders.filter.filled')}</option>
          <option value="cancelled">{t('paperTrading.orders.filter.cancelled')}</option>
          <option value="rejected">{t('paperTrading.orders.filter.rejected')}</option>
          <option value="conditional">{t('paperTrading.orders.filter.conditional')}</option>
        </select>
        <select
          value={filters.side}
          onChange={(e) => onFiltersChange({ ...filters, side: e.target.value })}
          className="input-terminal bg-elevated text-xs py-1.5"
          data-testid="orders-filter-side"
        >
          <option value="">{t('paperTrading.orders.filter.allSides')}</option>
          <option value="buy">{t('paperTrading.orders.filter.buy')}</option>
          <option value="sell">{t('paperTrading.orders.filter.sell')}</option>
        </select>
        <input
          type="text"
          value={filters.code}
          onChange={(e) => onFiltersChange({ ...filters, code: e.target.value.toUpperCase() })}
          placeholder={t('paperTrading.orders.filter.placeholder')}
          className="input-terminal text-xs py-1.5 flex-1 min-w-[120px]"
          data-testid="orders-filter-code"
        />
        <span className="text-xs text-muted" data-testid="orders-filter-count">
          {filteredOrders.length} / {orders.length}
        </span>
      </div>

      {filteredOrders.length === 0 ? (
        <EmptyState message={t('paperTrading.orders.empty')} />
      ) : (
        <div className="overflow-x-auto rounded-xl border border-white/5" data-testid="orders-table">
          <table className="w-full text-sm">
            <thead>
              <tr className="bg-elevated text-left">
                <th className="px-3 py-2.5 text-xs font-medium text-secondary uppercase">{t('paperTrading.orders.id')}</th>
                <th className="px-3 py-2.5 text-xs font-medium text-secondary uppercase">{t('paperTrading.orders.code')}</th>
                <th className="px-3 py-2.5 text-xs font-medium text-secondary uppercase">{t('paperTrading.orders.side')}</th>
                <th className="px-3 py-2.5 text-xs font-medium text-secondary uppercase">{t('paperTrading.orders.type')}</th>
                <th className="px-3 py-2.5 text-xs font-medium text-secondary uppercase text-right">{t('paperTrading.orders.quantity')}</th>
                <th className="px-3 py-2.5 text-xs font-medium text-secondary uppercase text-right">{t('paperTrading.orders.filled')}</th>
                <th className="px-3 py-2.5 text-xs font-medium text-secondary uppercase">{t('paperTrading.orders.status')}</th>
                <th className="px-3 py-2.5 text-xs font-medium text-secondary uppercase">{t('paperTrading.orders.createdAt')}</th>
                <th className="px-3 py-2.5 text-xs font-medium text-secondary uppercase">{t('paperTrading.orders.action')}</th>
              </tr>
            </thead>
            <tbody>
              {filteredOrders.map((o) => (
                <React.Fragment key={o.id}>
                  <tr className="border-t border-white/5 hover:bg-hover transition-colors">
                    <td className="px-3 py-2 text-xs text-muted">{o.id}</td>
                    <td className="px-3 py-2 font-mono text-cyan text-xs">{o.code}</td>
                    <td className="px-3 py-2">{sideBadge(o.side, t)}</td>
                    <td className="px-3 py-2 text-xs text-secondary">{t(`paperTrading.order.type.${o.orderType}` as import('../i18n/uiText').UiTextKey) || o.orderType}</td>
                    <td className="px-3 py-2 text-xs text-right text-white">{formatNumber(o.quantity)}</td>
                    <td className="px-3 py-2 text-xs text-right text-secondary">{formatNumber(o.filledQuantity)}</td>
                    <td className="px-3 py-2">{statusBadge(o.status, t)}</td>
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
                            {actingId === o.id && modifyId !== o.id ? '...' : t('paperTrading.orders.cancel')}
                          </button>
                          {o.orderType === 'limit' && (
                            <button
                              type="button"
                              onClick={() => startModify(o)}
                              disabled={actingId === o.id}
                              className="text-xs text-cyan hover:text-cyan/80 disabled:opacity-50"
                              data-testid={`order-modify-${o.id}`}
                            >
                              {t('paperTrading.orders.modify')}
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
                          <span className="text-xs text-muted">{formatUiText(t('paperTrading.orders.modifyLabel'), { code: o.code })}</span>
                          <input
                            type="number"
                            value={modifyPrice}
                            onChange={(e) => setModifyPrice(e.target.value)}
                            placeholder={t('paperTrading.orders.newLimitPrice')}
                            min={0.01}
                            step={0.01}
                            className="input-terminal text-xs py-1.5 w-36"
                            data-testid="order-modify-price-input"
                          />
                          <input
                            type="number"
                            value={modifyQty}
                            onChange={(e) => setModifyQty(e.target.value)}
                            placeholder={t('paperTrading.orders.newQuantity')}
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
                            {actingId === o.id ? t('paperTrading.orders.saving') : t('paperTrading.orders.save')}
                          </button>
                          <button
                            type="button"
                            onClick={() => { setModifyId(null); setModifyError(null); }}
                            className="btn-secondary text-xs py-1.5 px-3"
                            data-testid="order-modify-cancel"
                          >
                            {t('common.cancel')}
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
  const { t } = useUiLanguage();
  if (trades.length === 0) {
    return <EmptyState message={t('paperTrading.trades.empty')} />;
  }
  return (
    <div className="overflow-x-auto rounded-xl border border-white/5" data-testid="trades-table">
      <table className="w-full text-sm">
        <thead>
          <tr className="bg-elevated text-left">
            <th className="px-3 py-2.5 text-xs font-medium text-secondary uppercase">{t('paperTrading.trades.code')}</th>
            <th className="px-3 py-2.5 text-xs font-medium text-secondary uppercase">{t('paperTrading.trades.side')}</th>
            <th className="px-3 py-2.5 text-xs font-medium text-secondary uppercase text-right">{t('paperTrading.trades.fillPrice')}</th>
            <th className="px-3 py-2.5 text-xs font-medium text-secondary uppercase text-right">{t('paperTrading.trades.quantity')}</th>
            <th className="px-3 py-2.5 text-xs font-medium text-secondary uppercase text-right">{t('paperTrading.trades.fee')}</th>
            <th className="px-3 py-2.5 text-xs font-medium text-secondary uppercase">{t('paperTrading.trades.tradedAt')}</th>
          </tr>
        </thead>
        <tbody>
          {trades.map((trade) => (
            <tr key={trade.id} className="border-t border-white/5 hover:bg-hover transition-colors">
              <td className="px-3 py-2 font-mono text-cyan text-xs">{trade.code}</td>
              <td className="px-3 py-2">{sideBadge(trade.side, t)}</td>
              <td className="px-3 py-2 text-xs text-right text-white">{formatNumber(trade.fillPrice)}</td>
              <td className="px-3 py-2 text-xs text-right text-secondary">{formatNumber(trade.fillQuantity)}</td>
              <td className="px-3 py-2 text-xs text-right text-secondary">{formatNumber(trade.fee)}</td>
              <td className="px-3 py-2 text-xs text-muted">{formatDateTime(trade.tradedAt)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
};

const SignalsTable: React.FC<{ signals: SignalItem[]; onRefresh: () => void }> = ({ signals, onRefresh }) => {
  const { t } = useUiLanguage();
  const [actingId, setActingId] = useState<number | null>(null);
  const [modifyId, setModifyId] = useState<number | null>(null);
  const [modifyPrice, setModifyPrice] = useState('');
  const [modifyQty, setModifyQty] = useState('');
  const [modifyError, setModifyError] = useState<string | null>(null);

  if (signals.length === 0) {
    return <EmptyState message={t('paperTrading.signals.empty')} />;
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
          throw new Error(t('paperTrading.orders.modifyError.invalidPrice'));
        }
        params.newLimitPrice = price;
      }
      if (modifyQty) {
        const qty = parseFloat(modifyQty);
        if (Number.isNaN(qty) || qty <= 0) {
          throw new Error(t('paperTrading.orders.modifyError.invalidQuantity'));
        }
        params.newQuantity = qty;
      }
      if (!params.newLimitPrice && !params.newQuantity) {
        throw new Error(t('paperTrading.orders.modifyError.empty'));
      }
      await paperTradingApi.modifySignal(signalId, params);
      setModifyId(null);
      setModifyPrice('');
      setModifyQty('');
      onRefresh();
    } catch (err) {
      setModifyError(err instanceof Error ? err.message : t('paperTrading.orders.modifyError.failed'));
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
            <th className="px-3 py-2.5 text-xs font-medium text-secondary uppercase">{t('paperTrading.signals.code')}</th>
            <th className="px-3 py-2.5 text-xs font-medium text-secondary uppercase">{t('paperTrading.signals.side')}</th>
            <th className="px-3 py-2.5 text-xs font-medium text-secondary uppercase text-right">{t('paperTrading.signals.triggerPrice')}</th>
            <th className="px-3 py-2.5 text-xs font-medium text-secondary uppercase">{t('paperTrading.signals.strategy')}</th>
            <th className="px-3 py-2.5 text-xs font-medium text-secondary uppercase">{t('paperTrading.signals.status')}</th>
            <th className="px-3 py-2.5 text-xs font-medium text-secondary uppercase">{t('paperTrading.signals.agent')}</th>
            <th className="px-3 py-2.5 text-xs font-medium text-secondary uppercase">{t('paperTrading.signals.createdAt')}</th>
            <th className="px-3 py-2.5 text-xs font-medium text-secondary uppercase">{t('paperTrading.signals.action')}</th>
          </tr>
        </thead>
        <tbody>
          {signals.map((s) => (
            <React.Fragment key={s.id}>
              <tr className="border-t border-white/5 hover:bg-hover transition-colors">
                <td className="px-3 py-2 font-mono text-cyan text-xs">{s.code}</td>
                <td className="px-3 py-2">{sideBadge(s.side, t)}</td>
                <td className="px-3 py-2 text-xs text-right text-white">{formatNumber(s.triggerPrice)}</td>
                <td className="px-3 py-2 text-xs text-secondary">{s.strategyName || '--'}</td>
                <td className="px-3 py-2">{statusBadge(s.status, t)}</td>
                <td className="px-3 py-2 text-xs text-secondary">
                  {s.agentConfirmed == null ? '--' : s.agentConfirmed ? t('paperTrading.signals.agentConfirmed') : t('paperTrading.signals.agentVetoed')}
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
                        {actingId === s.id && modifyId !== s.id ? '...' : t('paperTrading.orders.cancel')}
                      </button>
                      <button
                        type="button"
                        onClick={() => startModify(s)}
                        disabled={actingId === s.id}
                        className="text-xs text-cyan hover:text-cyan/80 disabled:opacity-50"
                        data-testid={`signal-modify-${s.id}`}
                      >
                        {t('paperTrading.orders.modify')}
                      </button>
                    </div>
                  )}
                </td>
              </tr>
              {modifyId === s.id && (
                <tr className="border-t border-white/5 bg-elevated/50">
                  <td colSpan={8} className="px-3 py-3">
                    <div className="flex flex-wrap items-center gap-3" data-testid={`signal-modify-form-${s.id}`}>
                      <span className="text-xs text-muted">{formatUiText(t('paperTrading.orders.modifyLabel'), { code: s.code })}</span>
                      <input
                        type="number"
                        value={modifyPrice}
                        onChange={(e) => setModifyPrice(e.target.value)}
                        placeholder={t('paperTrading.orders.newLimitPrice')}
                        min={0.01}
                        step={0.01}
                        className="input-terminal text-xs py-1.5 w-36"
                        data-testid="signal-modify-price-input"
                      />
                      <input
                        type="number"
                        value={modifyQty}
                        onChange={(e) => setModifyQty(e.target.value)}
                        placeholder={t('paperTrading.orders.newQuantity')}
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
                        {actingId === s.id ? t('paperTrading.orders.saving') : t('paperTrading.orders.save')}
                      </button>
                      <button
                        type="button"
                        onClick={() => { setModifyId(null); setModifyError(null); }}
                        className="btn-secondary text-xs py-1.5 px-3"
                        data-testid="signal-modify-cancel"
                      >
                        {t('common.cancel')}
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

const DecisionsList: React.FC<{
  decisions: PMDecisionItem[];
  accountId: number;
  onRefresh: () => void;
}> = ({ decisions, accountId, onRefresh }) => {
  const { t } = useUiLanguage();
  const [executingId, setExecutingId] = useState<number | null>(null);
  const [ignoringId, setIgnoringId] = useState<number | null>(null);
  const [error, setError] = useState<string | null>(null);

  const handleExecute = async (decisionId: number) => {
    setExecutingId(decisionId);
    setError(null);
    try {
      await paperTradingApi.executePMDecision(accountId, decisionId);
      onRefresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : t('paperTrading.decisions.executeFailed'));
    } finally {
      setExecutingId(null);
    }
  };

  const handleIgnore = async (decisionId: number) => {
    if (!window.confirm(t('paperTrading.decisions.ignoreConfirm'))) return;
    setIgnoringId(decisionId);
    setError(null);
    try {
      await paperTradingApi.ignorePMDecision(accountId, decisionId);
      onRefresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : t('paperTrading.decisions.ignoreFailed'));
    } finally {
      setIgnoringId(null);
    }
  };

  if (decisions.length === 0) {
    return <EmptyState message={t('paperTrading.decisions.empty')} />;
  }

  return (
    <div className="space-y-2">
      {error && (
        <div className="rounded-lg bg-red-500/10 border border-red-500/20 p-2 text-xs text-red-400">
          {error}
        </div>
      )}
      {decisions.map((d) => {
        const isPending = d.status === 'pending';
        const isExecuting = executingId === d.id;
        const isIgnoring = ignoringId === d.id;
        return (
          <div key={d.id} className="p-3 rounded-xl bg-elevated border border-white/5">
            <div className="flex items-center justify-between">
              <div className="flex items-center gap-2 flex-wrap">
                <Badge variant={d.action === 'buy' ? 'success' : d.action === 'sell' ? 'danger' : 'info'}>
                  {sideBadge(d.action, t)}
                </Badge>
                {d.code && <span className="font-mono text-cyan text-xs">{d.code}</span>}
                <Badge
                  variant={
                    d.status === 'executed'
                      ? 'success'
                      : d.status === 'skipped' || d.status === 'rejected'
                        ? 'default'
                        : 'warning'
                  }
                >
                  {d.status === 'pending'
                    ? t('paperTrading.decisions.status.pending')
                    : d.status === 'executed'
                      ? t('paperTrading.decisions.status.executed')
                      : d.status === 'skipped'
                        ? t('paperTrading.decisions.status.skipped')
                        : d.status === 'rejected'
                          ? t('paperTrading.decisions.status.rejected')
                          : d.status}
                </Badge>
                {d.usedFallback && <Badge variant="warning">{t('paperTrading.battlePlans.fallback')}</Badge>}
              </div>
              <span className="text-xs text-muted">{formatDateTime(d.createdAt)}</span>
            </div>
            <p className="mt-2 text-xs text-secondary">{d.reason || t('paperTrading.decisions.noReason')}</p>
            <div className="mt-2 flex items-center justify-between">
              <div className="flex items-center gap-3 text-xs text-muted">
                <span>{t('paperTrading.decisions.confidence')}: {(d.confidence * 100).toFixed(0)}%</span>
                {Boolean(d.params?.quantity) && (
                  <span>{t('paperTrading.decisions.quantity')}: {String(d.params!.quantity)}</span>
                )}
                {Boolean(d.params?.limitPrice) && (
                  <span>{t('paperTrading.decisions.limitPrice')}: {String(d.params!.limitPrice)}</span>
                )}
                {d.signalId && (
                  <span className="text-cyan">{formatUiText(t('paperTrading.decisions.signal'), { id: d.signalId })}</span>
                )}
              </div>
              {isPending && (
                <div className="flex items-center gap-2">
                  <button
                    type="button"
                    onClick={() => handleExecute(d.id)}
                    disabled={isExecuting || isIgnoring}
                    className="btn-primary text-xs py-1 px-2 disabled:opacity-50"
                  >
                    {isExecuting ? t('paperTrading.decisions.executing') : t('paperTrading.decisions.execute')}
                  </button>
                  <button
                    type="button"
                    onClick={() => handleIgnore(d.id)}
                    disabled={isExecuting || isIgnoring}
                    className="btn-secondary text-xs py-1 px-2 disabled:opacity-50"
                  >
                    {isIgnoring ? t('paperTrading.decisions.ignoring') : t('paperTrading.decisions.ignore')}
                  </button>
                </div>
              )}
            </div>
          </div>
        );
      })}
    </div>
  );
};

const ReflectionsList: React.FC<{ reflections: ReflectionNoteItem[] }> = ({ reflections }) => {
  const { t } = useUiLanguage();
  if (reflections.length === 0) {
    return <EmptyState message={t('paperTrading.reflections.empty')} />;
  }
  return (
    <div className="space-y-3">
      {reflections.map((r) => (
        <div key={r.id} className="p-3 rounded-xl bg-elevated border border-white/5">
          <div className="flex items-center justify-between">
            <span className="text-xs font-medium text-white">{r.subject || t('paperTrading.reflections.noSubject')}</span>
            <Badge variant={r.mood === 'good' ? 'success' : r.mood === 'bad' ? 'danger' : 'default'}>
              {t(`paperTrading.reflections.mood.${r.mood}` as import('../i18n/uiText').UiTextKey) || r.mood}
            </Badge>
          </div>
          <p className="mt-1 text-xs text-secondary">{r.summary}</p>
          {r.takeaway && (
            <p className="mt-2 text-xs text-cyan">{t('paperTrading.reflections.takeaway')}: {r.takeaway}</p>
          )}
          {r.lessons.length > 0 && (
            <ul className="mt-2 space-y-1">
              {r.lessons.map((lesson, i) => (
                <li key={i} className="text-xs text-muted list-disc list-inside">{lesson}</li>
              ))}
            </ul>
          )}
          <div className="mt-2 text-xs text-muted">
            {r.code && <span className="mr-2">{formatUiText(t('paperTrading.reflections.code'), { code: r.code })}</span>}
            <span>{formatDateTime(r.createdAt)}</span>
          </div>
        </div>
      ))}
    </div>
  );
};

const BattlePlansList: React.FC<{ plans: BattlePlanItem[] }> = ({ plans }) => {
  const { t } = useUiLanguage();
  const [expandedId, setExpandedId] = useState<number | null>(null);
  const [markdown, setMarkdown] = useState<string | null>(null);
  const [loadingMd, setLoadingMd] = useState(false);

  if (plans.length === 0) {
    return <EmptyState message={t('paperTrading.battlePlans.empty')} />;
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
      setMarkdown(t('paperTrading.battlePlans.loadingMd'));
    } finally {
      setLoadingMd(false);
    }
  };

  return (
    <div className="space-y-3">
      {plans.map((p) => (
        <div key={p.planId} className="p-3 rounded-xl bg-elevated border border-white/5">
          <div className="flex items-center justify-between">
            <span className="text-xs font-medium text-white">{formatUiText(t('paperTrading.battlePlans.title'), { date: p.date })}</span>
            <div className="flex items-center gap-2">
              <button
                type="button"
                onClick={() => toggleMarkdown(p.planId)}
                className="text-xs text-cyan hover:text-cyan/80"
                data-testid={`battle-plan-md-${p.planId}`}
              >
                {expandedId === p.planId ? t('paperTrading.battlePlans.hideMd') : t('paperTrading.battlePlans.viewMd')}
              </button>
              <Badge variant={p.usedFallback ? 'warning' : 'success'}>
                {p.usedFallback ? t('paperTrading.battlePlans.fallback') : t('paperTrading.battlePlans.ai')}
              </Badge>
            </div>
          </div>
          <p className="mt-1 text-xs text-secondary">{p.marketReview || t('paperTrading.battlePlans.noReview')}</p>
          {p.mainTheme && (
            <p className="mt-1 text-xs text-cyan">{formatUiText(t('paperTrading.battlePlans.theme'), { theme: p.mainTheme })}</p>
          )}
          <div className="mt-2 grid grid-cols-2 gap-2">
            <div>
              <span className="text-xxs text-muted uppercase">{t('paperTrading.battlePlans.holdings')}</span>
              <p className="text-xs text-secondary">{p.holdingsPlans.map(h => h.code).join(', ') || '--'}</p>
            </div>
            <div>
              <span className="text-xxs text-muted uppercase">{t('paperTrading.battlePlans.candidates')}</span>
              <p className="text-xs text-secondary">{p.candidates.map(c => c.code).join(', ') || '--'}</p>
            </div>
          </div>
          {expandedId === p.planId && (
            <div className="mt-3 pt-3 border-t border-white/5" data-testid={`battle-plan-md-content-${p.planId}`}>
              {loadingMd ? (
                <p className="text-xs text-muted">{t('paperTrading.battlePlans.loadingMd')}</p>
              ) : (
                <pre className="text-xs text-secondary whitespace-pre-wrap font-mono max-h-96 overflow-y-auto">{markdown || t('paperTrading.battlePlans.noMd')}</pre>
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

// ============ Account Manager ============

type AccountManagerProps = {
  accounts: AccountListItem[];
  currentAccountId: number;
  onSwitch: (accountId: number) => void;
  onRefresh: () => void;
};

const AccountManager: React.FC<AccountManagerProps> = ({
  accounts,
  currentAccountId,
  onSwitch,
  onRefresh,
}) => {
  const { t } = useUiLanguage();
  const [open, setOpen] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [editingId, setEditingId] = useState<number | null>(null);
  const [editName, setEditName] = useState('');
  const [editCapital, setEditCapital] = useState('');
  const [createName, setCreateName] = useState('');
  const [createCapital, setCreateCapital] = useState('1000');
  const [confirmDeleteId, setConfirmDeleteId] = useState<number | null>(null);

  const currentAccount = accounts.find((a) => a.accountId === currentAccountId);

  const handleCreate = async () => {
    const name = createName.trim() || `paper-${Date.now()}`;
    const capital = parseFloat(createCapital);
    if (Number.isNaN(capital) || capital <= 0) {
      setError(t('paperTrading.accountManager.createError.invalidCapital'));
      return;
    }
    setLoading(true);
    setError(null);
    try {
      const snap = await paperTradingApi.createAccount({
        name,
        initialCapital: capital,
        resetIfExists: false,
      });
      setCreateName('');
      setCreateCapital('1000');
      onSwitch(snap.accountId);
      onRefresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : t('paperTrading.accountManager.createError.failed'));
    } finally {
      setLoading(false);
    }
  };

  const handleUpdate = async (accountId: number) => {
    const params: AccountUpdateRequest = {};
    const name = editName.trim();
    if (name) params.name = name;
    const capital = parseFloat(editCapital);
    if (!Number.isNaN(capital) && capital > 0) {
      params.initialCapital = capital;
    }
    setLoading(true);
    setError(null);
    try {
      await paperTradingApi.updateAccount(accountId, params);
      setEditingId(null);
      onRefresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : t('paperTrading.accountManager.updateError.failed'));
    } finally {
      setLoading(false);
    }
  };

  const handleReset = async (accountId: number) => {
    if (!window.confirm(t('paperTrading.accountManager.resetConfirm'))) return;
    setLoading(true);
    setError(null);
    try {
      const account = accounts.find((a) => a.accountId === accountId);
      await paperTradingApi.createAccount({
        name: account?.name ?? 'default',
        initialCapital: account?.initialCapital ?? 1000,
        resetIfExists: true,
      });
      onRefresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : t('paperTrading.accountManager.resetError.failed'));
    } finally {
      setLoading(false);
    }
  };

  const handleDelete = async (accountId: number) => {
    setLoading(true);
    setError(null);
    try {
      await paperTradingApi.deleteAccount(accountId);
      setConfirmDeleteId(null);
      if (currentAccountId === accountId && accounts.length > 1) {
        const next = accounts.find((a) => a.accountId !== accountId);
        if (next) onSwitch(next.accountId);
      }
      onRefresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : t('paperTrading.accountManager.deleteError.failed'));
    } finally {
      setLoading(false);
    }
  };

  return (
    <>
      <button
        type="button"
        onClick={() => setOpen(true)}
        className="btn-secondary text-xs py-2 px-3"
        data-testid="account-manager-button"
      >
        {t('paperTrading.accountManager.button')}: {currentAccount?.name ?? `#${currentAccountId}`}
      </button>

      {open && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4">
          <div className="w-full max-w-lg max-h-[80vh] overflow-y-auto rounded-2xl border border-white/10 bg-[#0b0f17] p-4 shadow-2xl">
            <div className="flex items-center justify-between mb-4">
              <h2 className="text-sm font-semibold text-white">{t('paperTrading.accountManager.title')}</h2>
              <button
                type="button"
                onClick={() => setOpen(false)}
                className="text-xs text-muted hover:text-white"
              >
                {t('paperTrading.accountManager.close')}
              </button>
            </div>

            {error && <p className="mb-3 text-xs text-danger">{error}</p>}

            <div className="mb-4 p-3 rounded-xl bg-elevated border border-white/5">
              <h3 className="text-xs font-medium text-white mb-2">{t('paperTrading.accountManager.createTitle')}</h3>
              <div className="flex flex-wrap items-center gap-2">
                <input
                  type="text"
                  value={createName}
                  onChange={(e) => setCreateName(e.target.value)}
                  placeholder={t('paperTrading.accountManager.namePlaceholder')}
                  className="input-terminal text-xs py-1.5 px-2 w-36"
                />
                <input
                  type="number"
                  value={createCapital}
                  onChange={(e) => setCreateCapital(e.target.value)}
                  placeholder={t('paperTrading.accountManager.capitalPlaceholder')}
                  min={1}
                  step={1}
                  className="input-terminal text-xs py-1.5 px-2 w-28"
                />
                <button
                  type="button"
                  onClick={handleCreate}
                  disabled={loading}
                  className="btn-primary text-xs py-1.5 px-3"
                >
                  {t('paperTrading.accountManager.create')}
                </button>
              </div>
            </div>

            <div className="space-y-2">
              <h3 className="text-xs font-medium text-white">{t('paperTrading.accountManager.existingTitle')}</h3>
              {accounts.length === 0 && <EmptyState message={t('paperTrading.accountManager.empty')} />}
              {accounts.map((a) => (
                <div
                  key={a.accountId}
                  className={`p-3 rounded-xl border ${
                    a.accountId === currentAccountId
                      ? 'border-cyan/30 bg-cyan/5'
                      : 'border-white/5 bg-elevated'
                  }`}
                >
                  <div className="flex items-center justify-between">
                    <div className="flex items-center gap-2">
                      <span className="font-mono text-cyan text-xs">#{a.accountId}</span>
                      {editingId === a.accountId ? (
                        <input
                          type="text"
                          value={editName}
                          onChange={(e) => setEditName(e.target.value)}
                          className="input-terminal text-xs py-1 px-2 w-32"
                        />
                      ) : (
                        <span className="text-sm font-medium text-white">{a.name}</span>
                      )}
                    </div>
                    <div className="flex items-center gap-1">
                      {a.accountId === currentAccountId ? (
                        <Badge variant="info">{t('paperTrading.accountManager.current')}</Badge>
                      ) : (
                        <button
                          type="button"
                          onClick={() => {
                            onSwitch(a.accountId);
                            setOpen(false);
                          }}
                          className="text-xs text-cyan hover:text-cyan/80 px-2 py-1"
                        >
                          {t('paperTrading.accountManager.switch')}
                        </button>
                      )}
                    </div>
                  </div>

                  <div className="mt-2 grid grid-cols-3 gap-2 text-xs text-muted">
                    <div>{t('paperTrading.accountManager.initialCapital')}: {formatNumber(a.initialCapital)}</div>
                    <div>{t('paperTrading.accountManager.cash')}: {formatNumber(a.cash)}</div>
                    <div>{t('paperTrading.accountManager.netValue')}: {formatNumber(a.netValue)}</div>
                  </div>

                  <div className="mt-2 flex items-center gap-2">
                    {editingId === a.accountId ? (
                      <>
                        <input
                          type="number"
                          value={editCapital}
                          onChange={(e) => setEditCapital(e.target.value)}
                          placeholder={t('paperTrading.accountManager.capitalPlaceholder')}
                          className="input-terminal text-xs py-1 px-2 w-28"
                        />
                        <button
                          type="button"
                          onClick={() => handleUpdate(a.accountId)}
                          disabled={loading}
                          className="text-xs text-cyan hover:text-cyan/80"
                        >
                          {t('paperTrading.accountManager.save')}
                        </button>
                        <button
                          type="button"
                          onClick={() => setEditingId(null)}
                          className="text-xs text-muted hover:text-white"
                        >
                          {t('common.cancel')}
                        </button>
                      </>
                    ) : (
                      <>
                        <button
                          type="button"
                          onClick={() => {
                            setEditingId(a.accountId);
                            setEditName(a.name);
                            setEditCapital(String(a.initialCapital));
                          }}
                          className="text-xs text-muted hover:text-white"
                        >
                          {t('paperTrading.accountManager.edit')}
                        </button>
                        <button
                          type="button"
                          onClick={() => handleReset(a.accountId)}
                          disabled={loading}
                          className="text-xs text-muted hover:text-white"
                        >
                          {t('paperTrading.accountManager.reset')}
                        </button>
                        {confirmDeleteId === a.accountId ? (
                          <>
                            <span className="text-xs text-danger">{t('paperTrading.accountManager.deleteConfirm')}</span>
                            <button
                              type="button"
                              onClick={() => handleDelete(a.accountId)}
                              disabled={loading}
                              className="text-xs text-danger hover:text-red-300"
                            >
                              {t('paperTrading.accountManager.yes')}
                            </button>
                            <button
                              type="button"
                              onClick={() => setConfirmDeleteId(null)}
                              className="text-xs text-muted hover:text-white"
                            >
                              {t('paperTrading.accountManager.no')}
                            </button>
                          </>
                        ) : (
                          <button
                            type="button"
                            onClick={() => setConfirmDeleteId(a.accountId)}
                            className="text-xs text-danger hover:text-red-300"
                          >
                            {t('paperTrading.accountManager.delete')}
                          </button>
                        )}
                      </>
                    )}
                  </div>
                </div>
              ))}
            </div>
          </div>
        </div>
      )}
    </>
  );
};

// ============ Daily Report Tab (P2-A) ============

const DailyReportTab: React.FC<{ accountId: number }> = ({ accountId }) => {
  const { t } = useUiLanguage();
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
      setError(err instanceof Error ? err.message : t('paperTrading.dailyReport.generateError'));
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
      setError(err instanceof Error ? err.message : t('paperTrading.dailyReport.loadError'));
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
          {loading ? t('paperTrading.dailyReport.loading') : t('paperTrading.dailyReport.load')}
        </button>
        <button
          type="button"
          onClick={handleGenerate}
          disabled={loading}
          className="btn-primary text-xs py-1.5 px-3"
          data-testid="daily-report-generate-button"
        >
          {loading ? t('paperTrading.dailyReport.generating') : t('paperTrading.dailyReport.generate')}
        </button>
      </div>

      {error && (
        <p className="text-xs text-danger" data-testid="daily-report-error">{error}</p>
      )}

      {report && (
        <div className="p-3 rounded-xl bg-elevated border border-white/5" data-testid="daily-report-content">
          <div className="flex items-center justify-between mb-2">
            <span className="text-xs font-medium text-white">{formatUiText(t('paperTrading.dailyReport.title'), { date: report.date })}</span>
            <div className="flex items-center gap-2">
              {report.usedFallback && <Badge variant="warning">{t('paperTrading.dailyReport.fallback')}</Badge>}
              {report.reportPath && (
                <Badge variant="info">{t('paperTrading.dailyReport.saved')}</Badge>
              )}
            </div>
          </div>
          {report.markdown ? (
            <pre className="text-xs text-secondary whitespace-pre-wrap font-mono max-h-[60vh] overflow-y-auto" data-testid="daily-report-markdown">
              {report.markdown}
            </pre>
          ) : (
            <p className="text-xs text-muted">{t('paperTrading.dailyReport.noMarkdown')}</p>
          )}
        </div>
      )}

      {!report && !error && !loading && (
        <EmptyState message={t('paperTrading.dailyReport.empty')} />
      )}
    </div>
  );
};

// ============ Main Page ============

const PaperTradingPage: React.FC = () => {
  const { t } = useUiLanguage();
  const [accountId, setAccountId] = useState<number>(1);
  const [accounts, setAccounts] = useState<AccountListItem[]>([]);
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
      const [accountsRes, snap, nv, pos, ord, trd, sig, dec, ref, plans] = await Promise.all([
        paperTradingApi.getAccounts(),
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
      setAccounts(accountsRes.accounts || []);
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
      setError(err instanceof Error ? err.message : t('paperTrading.actions.loadError'));
    } finally {
      setLoading(false);
    }
  }, [accountId, t]);

  useEffect(() => {
    loadAll();
  }, [loadAll]);

  const handleSwitchAccount = (id: number) => {
    if (id > 0) {
      setAccountId(id);
    }
  };

  const handleTriggerPm = async () => {
    setTriggeringPm(true);
    try {
      await paperTradingApi.triggerPMDecision({ accountId });
      await loadAll();
    } catch (err) {
      setError(err instanceof Error ? err.message : t('paperTrading.actions.pmError'));
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
      setError(err instanceof Error ? err.message : t('paperTrading.actions.planError'));
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
      setError(err instanceof Error ? err.message : t('paperTrading.actions.reflectionError'));
    } finally {
      setTriggeringReflection(false);
    }
  };

  const tabs: { key: TabKey; label: string; count?: number }[] = useMemo(() => [
    { key: 'positions', label: t('paperTrading.tabs.positions'), count: positions.length },
    { key: 'orders', label: t('paperTrading.tabs.orders'), count: orders.length },
    { key: 'trades', label: t('paperTrading.tabs.trades'), count: trades.length },
    { key: 'signals', label: t('paperTrading.tabs.signals'), count: signals.length },
    { key: 'decisions', label: t('paperTrading.tabs.decisions'), count: decisions.length },
    { key: 'reflections', label: t('paperTrading.tabs.reflections'), count: reflections.length },
    { key: 'battle-plans', label: t('paperTrading.tabs.battlePlans'), count: battlePlans.length },
    { key: 'daily-report', label: t('paperTrading.tabs.dailyReport') },
    { key: 'backtest-comparison', label: t('paperTrading.tabs.backtestComparison') },
    { key: 'strategies', label: '策略' },
    { key: 'features', label: '特征' },
  ], [positions.length, orders.length, trades.length, signals.length, decisions.length, reflections.length, battlePlans.length, t]);

  return (
    <div className="min-h-screen flex flex-col">
      {/* Header */}
      <header className="flex-shrink-0 px-4 py-3 border-b border-white/5" data-testid="paper-trading-header">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-3">
            <h1 className="text-lg font-semibold text-white" data-testid="paper-trading-title">{t('paperTrading.title')}</h1>
            <AccountManager
              accounts={accounts}
              currentAccountId={accountId}
              onSwitch={handleSwitchAccount}
              onRefresh={loadAll}
            />
          </div>
          <div className="flex items-center gap-2">
            <button
              type="button"
              onClick={handleTriggerPm}
              disabled={triggeringPm}
              className="btn-secondary text-xs py-2 px-3"
              data-testid="trigger-pm-button"
            >
              {triggeringPm ? t('paperTrading.actions.triggeringPm') : t('paperTrading.actions.triggerPm')}
            </button>
            <button
              type="button"
              onClick={handleTriggerReflection}
              disabled={triggeringReflection}
              className="btn-secondary text-xs py-2 px-3"
              data-testid="trigger-reflection-button"
            >
              {triggeringReflection ? t('paperTrading.actions.reflecting') : t('paperTrading.actions.triggerReflection')}
            </button>
            <button
              type="button"
              onClick={handleGeneratePlan}
              disabled={generatingPlan}
              className="btn-secondary text-xs py-2 px-3"
              data-testid="generate-plan-button"
            >
              {generatingPlan ? t('paperTrading.actions.generatingPlan') : t('paperTrading.actions.generatePlan')}
            </button>
            <button
              type="button"
              onClick={loadAll}
              disabled={loading}
              className="btn-primary text-xs py-2 px-3"
              data-testid="refresh-button"
            >
              {loading ? t('paperTrading.actions.loading') : t('paperTrading.actions.refresh')}
            </button>
          </div>
        </div>
        {error && (
          <p className="mt-2 text-xs text-danger" data-testid="error-message">{error}</p>
        )}
      </header>

      {/* Realtime strips — quote ticker + extreme-market banner */}
      <div className="flex-shrink-0 px-4 py-1.5 border-b border-white/5 space-y-1.5">
        <QuoteTicker accountId={accountId} maxCodes={12} />
        <ExtremeMarketBanner accountId={accountId} />
      </div>

      {/* Main content */}
      <main className="flex-1 flex overflow-hidden p-3 gap-3 max-sm:flex-col max-sm:p-2 max-sm:gap-2">
        {/* Left sidebar */}
        <div className="flex flex-col gap-3 w-80 flex-shrink-0 overflow-y-auto max-sm:w-full max-sm:flex-shrink max-sm:order-2">
          {/* Account summary */}
          <Card variant="gradient" padding="md">
            <span className="label-uppercase">
              {formatUiText(t('paperTrading.accountSummary'), { accountId, name: snapshot?.name ? `· ${snapshot.name}` : '' })}
            </span>
            {snapshot ? (
              <div className="mt-3 grid grid-cols-2 gap-3">
                <div>
                  <p className="text-xxs text-muted uppercase">{t('paperTrading.netValue')}</p>
                  <p className="text-lg font-mono font-semibold text-white">{formatNumber(snapshot.netValue)}</p>
                </div>
                <div>
                  <p className="text-xxs text-muted uppercase">{t('paperTrading.returnPct')}</p>
                  <p className={`text-lg font-mono font-semibold ${snapshot.returnPct >= 0 ? 'text-emerald-400' : 'text-red-400'}`}>
                    {formatPct(snapshot.returnPct)}
                  </p>
                </div>
                <div>
                  <p className="text-xxs text-muted uppercase">{t('paperTrading.cash')}</p>
                  <p className="text-sm font-mono text-secondary">{formatNumber(snapshot.cash)}</p>
                </div>
                <div>
                  <p className="text-xxs text-muted uppercase">{t('paperTrading.positionCount')}</p>
                  <p className="text-sm font-mono text-secondary">{snapshot.positionCount}</p>
                </div>
              </div>
            ) : (
              <p className="mt-3 text-xs text-muted">{t('paperTrading.loadingAccount')}</p>
            )}
          </Card>

          {/* Breaker status + System health (integration batch) */}
          <BreakerStatusBadge accountId={accountId} />
          <HealthDashboard />

          {/* Net value curve */}
          <Card variant="gradient" padding="md">
            <span className="label-uppercase">{t('paperTrading.netValueCurve')}</span>
            <div className="mt-3">
              <NetValueSparkline data={netValue} />
            </div>
          </Card>

          {/* Performance metrics */}
          <PerformanceCard accountId={accountId} />

          {/* Realtime latency + market status */}
          <Card padding="md">
            <span className="label-uppercase">实时状态</span>
            <div className="mt-2 space-y-2">
              <MarketStatusDashboard accountId={accountId} />
              <LatencyPanel accountId={accountId} />
              <L2DepthPanel code={positions?.[0]?.code} pollMs={10_000} />
            </div>
          </Card>

          {/* Order form */}
          <OrderForm accountId={accountId} onSubmitted={loadAll} />

          {/* Listener control */}
          <ListenerControl onStatusChange={loadAll} />
        </div>

        {/* Right content */}
        <section className="flex-1 flex flex-col overflow-hidden max-sm:min-h-0">
          {/* Tabs */}
          <div className="flex items-center gap-1 overflow-x-auto pb-2 border-b border-white/5 max-sm:gap-0.5" data-testid="paper-trading-tabs">
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
          <div className="flex-1 overflow-y-auto pt-3 max-sm:overflow-x-auto">
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
            {activeTab === 'decisions' && <DecisionsList decisions={decisions} accountId={accountId} onRefresh={loadAll} />}
            {activeTab === 'reflections' && <ReflectionsList reflections={reflections} />}
            {activeTab === 'battle-plans' && <BattlePlansList plans={battlePlans} />}
            {activeTab === 'daily-report' && <DailyReportTab accountId={accountId} />}
            {activeTab === 'backtest-comparison' && <BacktestComparisonPanel accountId={accountId} />}
            {activeTab === 'strategies' && (
              <div className="space-y-4">
                <StrategyLeaderboard accountId={accountId} />
                <DriftPanel accountId={accountId} />
                <StrategyLifecyclePanel
                  accountId={accountId}
                  onTransition={async (name, newState) => {
                    await paperTradingApi.transitionStrategy(name, newState);
                  }}
                />
              </div>
            )}
            {activeTab === 'features' && <FeaturesPanel accountId={accountId} />}
          </div>
        </section>
      </main>

      {/* Global risk-alert toast (fixed, overlays everything) */}
      <RiskAlertToast accountId={accountId} />

      {/* Realtime event log (fixed bottom-left) */}
      <div className="fixed bottom-3 left-3 z-40 w-80 max-h-64 hidden lg:block">
        <Card padding="md" className="backdrop-blur-sm bg-card/80">
          <span className="label-uppercase">实时事件流</span>
          <div className="mt-2">
            <EventLogFeed accountId={accountId} maxEvents={20} />
          </div>
        </Card>
      </div>
    </div>
  );
};

export default PaperTradingPage;