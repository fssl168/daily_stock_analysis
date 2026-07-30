import React, { useCallback, useEffect, useState } from 'react';
import { backtestApi } from '../../api/backtest';
import { paperTradingApi } from '../../api/paperTrading';
import { Badge, Button, Card, EmptyState, InlineAlert, Loading } from '../common';
import { useUiLanguage } from '../../contexts/UiLanguageContext';
import type {
  BacktestPaperComparisonRequest,
  BacktestPaperComparisonResponse,
  PaperTradingScenario,
} from '../../types/paperTrading';
import type { PerformanceMetrics } from '../../types/backtest';

function formatNumber(value?: number | null, digits = 2): string {
  if (value == null) return '--';
  return value.toFixed(digits);
}

function formatPct(value?: number | null): string {
  if (value == null) return '--';
  return `${value >= 0 ? '+' : ''}${value.toFixed(2)}%`;
}

function summaryNumber(summary: Record<string, unknown>, key: string): number | undefined {
  const value = summary[key];
  if (value == null) return undefined;
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : undefined;
}

interface ComparisonMetricRowProps {
  label: string;
  backtest?: number | null;
  paper?: number | null;
  isPct?: boolean;
}

const ComparisonMetricRow: React.FC<ComparisonMetricRowProps> = ({
  label,
  backtest,
  paper,
  isPct,
}) => {
  const render = (value?: number | null) => (value == null ? '--' : isPct ? formatPct(value) : formatNumber(value));
  const delta = backtest != null && paper != null ? paper - backtest : null;
  const deltaClass = delta == null ? 'text-muted' : delta >= 0 ? 'text-emerald-400' : 'text-red-400';

  return (
    <div className="grid grid-cols-4 gap-2 py-2 text-xs border-b border-white/5 last:border-0">
      <span className="text-secondary">{label}</span>
      <span className="font-mono text-right text-white">{render(backtest)}</span>
      <span className="font-mono text-right text-white">{render(paper)}</span>
      <span className={`font-mono text-right ${deltaClass}`}>{delta != null ? (isPct ? formatPct(delta) : formatNumber(delta)) : '--'}</span>
    </div>
  );
};

export const BacktestComparisonPanel: React.FC<{ accountId: number }> = ({ accountId }) => {
  const { t } = useUiLanguage();
  const [strategyName, setStrategyName] = useState('default');
  const [scenario, setScenario] = useState<PaperTradingScenario | null>(null);
  const [backtestMetrics, setBacktestMetrics] = useState<PerformanceMetrics | null>(null);
  const [result, setResult] = useState<BacktestPaperComparisonResponse | null>(null);
  const [loadingScenario, setLoadingScenario] = useState(false);
  const [loadingBacktest, setLoadingBacktest] = useState(false);
  const [comparing, setComparing] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const loadScenario = useCallback(async () => {
    setLoadingScenario(true);
    try {
      const data = await paperTradingApi.getBacktestScenario(accountId, strategyName);
      setScenario(data);
    } catch (err) {
      setError(err instanceof Error ? err.message : t('paperTrading.backtestComparison.loadError'));
    } finally {
      setLoadingScenario(false);
    }
  }, [accountId, strategyName, t]);

  const loadBacktest = useCallback(async () => {
    setLoadingBacktest(true);
    try {
      const data = await backtestApi.getOverallPerformance();
      setBacktestMetrics(data);
    } catch {
      // Non-fatal: the comparison endpoint can fetch the latest summary itself.
      setBacktestMetrics(null);
    } finally {
      setLoadingBacktest(false);
    }
  }, []);

  useEffect(() => {
    loadScenario();
    loadBacktest();
  }, [loadScenario, loadBacktest]);

  const handleCompare = async () => {
    setComparing(true);
    setError(null);
    try {
      const request: BacktestPaperComparisonRequest = {
        strategyName,
        persistReflection: true,
      };
      const data = await paperTradingApi.compareWithBacktest(accountId, request);
      setResult(data);
    } catch (err) {
      setError(err instanceof Error ? err.message : t('paperTrading.backtestComparison.loadError'));
    } finally {
      setComparing(false);
    }
  };

  const isLoading = loadingScenario || loadingBacktest;
  const hasBacktestData = Boolean(backtestMetrics) || Boolean(result?.backtestSummary);

  const btSummary = result?.backtestSummary as Record<string, unknown> | undefined;
  const btTotalReturn = summaryNumber(btSummary ?? {}, 'avgStockReturnPct') ?? backtestMetrics?.avgStockReturnPct;
  const btWinRate = summaryNumber(btSummary ?? {}, 'winRatePct') ?? backtestMetrics?.winRatePct;
  const btSample = summaryNumber(btSummary ?? {}, 'completedCount') ?? backtestMetrics?.completedCount;

  return (
    <div className="space-y-4">
      <Card variant="gradient" padding="md">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div className="flex items-center gap-3">
            <span className="label-uppercase">{t('paperTrading.backtestComparison.title')}</span>
            <select
              value={strategyName}
              onChange={(e) => setStrategyName(e.target.value)}
              className="input-terminal text-xs py-1.5"
              aria-label={t('paperTrading.backtestComparison.strategyLabel')}
            >
              <option value="default">default</option>
            </select>
          </div>
          <Button
            variant="primary"
            size="sm"
            onClick={handleCompare}
            disabled={comparing || isLoading}
            data-testid="run-backtest-comparison-button"
          >
            {comparing ? t('paperTrading.backtestComparison.running') : t('paperTrading.backtestComparison.run')}
          </Button>
        </div>

        {error && (
          <InlineAlert
            variant="danger"
            message={error}
            className="mt-3"
          />
        )}

        {isLoading && (
          <div className="mt-4">
            <Loading />
          </div>
        )}

        {!hasBacktestData && !isLoading && (
          <div className="mt-4">
            <EmptyState
              title={t('paperTrading.backtestComparison.noBacktestData')}
            />
          </div>
        )}
      </Card>

      {scenario && !isLoading && (
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
          <Card variant="gradient" padding="md">
            <span className="label-uppercase">{t('paperTrading.backtestComparison.paper')}</span>
            <div className="mt-3 grid grid-cols-2 gap-3">
              <div>
                <p className="text-xxs text-muted uppercase">{t('paperTrading.backtestComparison.totalReturn')}</p>
                <p className={`text-sm font-mono font-semibold ${scenario.totalReturnPct >= 0 ? 'text-emerald-400' : 'text-red-400'}`}>
                  {formatPct(scenario.totalReturnPct)}
                </p>
              </div>
              <div>
                <p className="text-xxs text-muted uppercase">{t('paperTrading.backtestComparison.winRate')}</p>
                <p className="text-sm font-mono font-semibold text-white">{formatPct(scenario.winRate)}</p>
              </div>
              <div>
                <p className="text-xxs text-muted uppercase">{t('paperTrading.backtestComparison.maxDrawdown')}</p>
                <p className="text-sm font-mono font-semibold text-red-400">{formatPct(scenario.maxDrawdownPct)}</p>
              </div>
              <div>
                <p className="text-xxs text-muted uppercase">{t('paperTrading.backtestComparison.sampleSize')}</p>
                <p className="text-sm font-mono font-semibold text-white">{scenario.tradeCount}</p>
              </div>
            </div>
            {scenario.startDate && scenario.endDate && (
              <p className="mt-3 text-xs text-muted">
                {scenario.startDate} → {scenario.endDate}
              </p>
            )}
          </Card>

          <Card variant="gradient" padding="md">
            <span className="label-uppercase">{t('paperTrading.backtestComparison.backtest')}</span>
            {hasBacktestData ? (
              <div className="mt-3 grid grid-cols-2 gap-3">
                <div>
                  <p className="text-xxs text-muted uppercase">{t('paperTrading.backtestComparison.totalReturn')}</p>
                  <p className="text-sm font-mono font-semibold text-white">{formatPct(btTotalReturn)}</p>
                </div>
                <div>
                  <p className="text-xxs text-muted uppercase">{t('paperTrading.backtestComparison.winRate')}</p>
                  <p className="text-sm font-mono font-semibold text-white">{formatPct(btWinRate)}</p>
                </div>
                <div>
                  <p className="text-xxs text-muted uppercase">{t('paperTrading.backtestComparison.sampleSize')}</p>
                  <p className="text-sm font-mono font-semibold text-white">{btSample ?? 0}</p>
                </div>
              </div>
            ) : (
              <p className="mt-3 text-xs text-muted">{t('paperTrading.backtestComparison.noBacktestData')}</p>
            )}
          </Card>
        </div>
      )}

      {result && (
        <Card variant="gradient" padding="md">
          <div className="flex items-center justify-between">
            <span className="label-uppercase">{t('paperTrading.backtestComparison.delta')}</span>
            {result.reflectionPersisted && (
              <Badge variant="success">{t('paperTrading.backtestComparison.reflectionPersisted')}</Badge>
            )}
          </div>

          <div className="mt-3">
            <div className="grid grid-cols-4 gap-2 py-2 text-xs font-medium text-secondary uppercase border-b border-white/10">
              <span />
              <span className="text-right">{t('paperTrading.backtestComparison.backtest')}</span>
              <span className="text-right">{t('paperTrading.backtestComparison.paper')}</span>
              <span className="text-right">{t('paperTrading.backtestComparison.delta')}</span>
            </div>
            <ComparisonMetricRow
              label={t('paperTrading.backtestComparison.winRate')}
              backtest={result.metrics.winRatePct.backtest}
              paper={result.metrics.winRatePct.paper}
              isPct
            />
            <ComparisonMetricRow
              label={t('paperTrading.backtestComparison.totalReturn')}
              backtest={result.metrics.totalReturnPct.backtest}
              paper={result.metrics.totalReturnPct.paper}
              isPct
            />
            <ComparisonMetricRow
              label={t('paperTrading.backtestComparison.maxDrawdown')}
              backtest={summaryNumber(result.metrics.maxDrawdownPct, 'backtest')}
              paper={summaryNumber(result.metrics.maxDrawdownPct, 'paper')}
              isPct
            />
          </div>

          {result.metrics.sampleSize && (
            <div className="mt-3 pt-3 border-t border-white/5 text-xs">
              <span className="text-muted uppercase">{t('paperTrading.backtestComparison.sampleSize')}</span>
              <p className="font-mono text-white mt-1">
                {t('paperTrading.backtestComparison.backtest')}: {result.metrics.sampleSize.backtestCompleted} / {result.metrics.sampleSize.backtestLongSignals} &middot; {t('paperTrading.backtestComparison.paper')}: {result.metrics.sampleSize.paperTrades}
              </p>
            </div>
          )}

          {result.interpretation && (
            <div className="mt-3 pt-3 border-t border-white/5">
              <p className="text-xxs text-muted uppercase mb-1">{t('paperTrading.backtestComparison.interpretation')}</p>
              <p className="text-xs text-secondary leading-relaxed">{result.interpretation}</p>
            </div>
          )}
        </Card>
      )}

      {!result && !isLoading && scenario && hasBacktestData && (
        <EmptyState
          title={t('paperTrading.backtestComparison.empty')}
        />
      )}
    </div>
  );
};
