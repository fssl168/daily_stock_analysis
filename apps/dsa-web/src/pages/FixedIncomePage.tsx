import React, { useCallback, useEffect, useState } from 'react';
import { Card, Badge } from '../components/common';
import { fixedIncomeApi } from '../api/fixedIncome';
import type { BondDuration, CreditSpread, RepoRate, YieldCurve } from '../api/fixedIncome';

const FixedIncomePage: React.FC = () => {
  const [curve, setCurve] = useState<YieldCurve | null>(null);
  const [repo, setRepo] = useState<RepoRate[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // duration inputs
  const [coupon, setCoupon] = useState('5');
  const [years, setYears] = useState('10');
  const [yRate, setYRate] = useState('3.5');
  const [duration, setDuration] = useState<BondDuration | null>(null);

  // spread inputs
  const [corpYield, setCorpYield] = useState('4.5');
  const [treasuryYield, setTreasuryYield] = useState('3.1');
  const [spread, setSpread] = useState<CreditSpread | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    // Repo rates are fast (stub-backed); load them independently so a slow
    // online yield-curve fetch never blocks the page.
    void fixedIncomeApi.getRepoRates().then(setRepo).catch(() => setRepo([]));
    try {
      setCurve(await fixedIncomeApi.getCurve());
    } catch (e) {
      setError(e instanceof Error ? e.message : '曲线加载失败');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const calcDuration = async () => {
    setError(null);
    try {
      const res = await fixedIncomeApi.getDuration(
        parseFloat(coupon) || 0,
        parseFloat(years) || 0,
        parseFloat(yRate) || 0,
      );
      setDuration(res);
    } catch (e) {
      setError(e instanceof Error ? e.message : '计算失败');
    }
  };

  const calcSpread = async () => {
    setError(null);
    try {
      const res = await fixedIncomeApi.getSpread(
        parseFloat(corpYield) || 0,
        parseFloat(treasuryYield) || 0,
      );
      setSpread(res);
    } catch (e) {
      setError(e instanceof Error ? e.message : '计算失败');
    }
  };

  return (
    <div className="min-h-screen p-4 max-w-6xl mx-auto">
      <header className="mb-4">
        <h1 className="text-lg font-semibold text-white" data-testid="fi-title">固收分析</h1>
        <p className="text-xs text-muted">国债收益率曲线 · 久期凸性 · 信用利差 · 回购利率</p>
      </header>
      {error && <p className="mb-3 text-xs text-danger">{error}</p>}

      {/* Treasury yield curve */}
      <Card variant="gradient" padding="md" className="mb-4">
        <div className="flex items-center justify-between">
          <span className="label-uppercase">收益率曲线</span>
          {curve && (
            <div className="flex items-center gap-2">
              <span className="text-xs text-muted">{curve.date} · {curve.source}</span>
              {curve.usedFallback && <Badge variant="warning">stub</Badge>}
            </div>
          )}
        </div>
        <div className="mt-3 overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="bg-elevated text-left">
                <th className="px-3 py-2 text-xs text-secondary uppercase">期限</th>
                <th className="px-3 py-2 text-xs text-secondary uppercase text-right">收益率 %</th>
              </tr>
            </thead>
            <tbody>
              {curve?.points.map((p) => (
                <tr key={p.tenor} className="border-t border-white/5">
                  <td className="px-3 py-1.5 text-xs text-white">{p.tenor}</td>
                  <td className="px-3 py-1.5 text-xs text-right font-mono text-cyan">
                    {p.yieldRate.toFixed(4)}
                  </td>
                </tr>
              ))}
              {!curve && !loading && (
                <tr><td className="px-3 py-2 text-xs text-muted">暂无曲线数据</td></tr>
              )}
            </tbody>
          </table>
        </div>
      </Card>

      {/* Duration / convexity */}
      <Card variant="gradient" padding="md" className="mb-4">
        <span className="label-uppercase">久期 / 凸性</span>
        <div className="mt-3 flex flex-wrap items-end gap-2">
          <label className="text-xs text-muted">
            票息%{' '}
            <input
              className="input-terminal text-xs py-1.5 w-20"
              value={coupon}
              onChange={(e) => setCoupon(e.target.value)}
              type="number" step="0.1" min="0"
            />
          </label>
          <label className="text-xs text-muted">
            年限{' '}
            <input
              className="input-terminal text-xs py-1.5 w-20"
              value={years}
              onChange={(e) => setYears(e.target.value)}
              type="number" step="1" min="1"
            />
          </label>
          <label className="text-xs text-muted">
            收益率%{' '}
            <input
              className="input-terminal text-xs py-1.5 w-20"
              value={yRate}
              onChange={(e) => setYRate(e.target.value)}
              type="number" step="0.1" min="0"
            />
          </label>
          <button type="button" onClick={calcDuration} className="btn-primary text-xs py-2 px-3">
            计算
          </button>
        </div>
        {duration && (
          <div className="mt-3 grid grid-cols-2 sm:grid-cols-4 gap-3" data-testid="fi-duration-result">
            <div>
              <p className="text-xxs text-muted uppercase">价格</p>
              <p className="text-sm font-mono text-white">{duration.bondPrice.toFixed(2)}</p>
            </div>
            <div>
              <p className="text-xxs text-muted uppercase">Macaulay</p>
              <p className="text-sm font-mono text-white">{duration.macaulayDuration.toFixed(4)}</p>
            </div>
            <div>
              <p className="text-xxs text-muted uppercase">修正久期</p>
              <p className="text-sm font-mono text-white">{duration.modifiedDuration.toFixed(4)}</p>
            </div>
            <div>
              <p className="text-xxs text-muted uppercase">凸性</p>
              <p className="text-sm font-mono text-white">{duration.convexity.toFixed(4)}</p>
            </div>
          </div>
        )}
      </Card>

      {/* Credit spread */}
      <Card variant="gradient" padding="md" className="mb-4">
        <span className="label-uppercase">信用利差</span>
        <div className="mt-3 flex flex-wrap items-end gap-2">
          <label className="text-xs text-muted">
            信用债%{' '}
            <input
              className="input-terminal text-xs py-1.5 w-20"
              value={corpYield}
              onChange={(e) => setCorpYield(e.target.value)}
              type="number" step="0.1" min="0"
            />
          </label>
          <label className="text-xs text-muted">
            国债%{' '}
            <input
              className="input-terminal text-xs py-1.5 w-20"
              value={treasuryYield}
              onChange={(e) => setTreasuryYield(e.target.value)}
              type="number" step="0.1" min="0"
            />
          </label>
          <button type="button" onClick={calcSpread} className="btn-primary text-xs py-2 px-3">
            计算
          </button>
        </div>
        {spread && (
          <div className="mt-3 grid grid-cols-2 gap-3 max-w-xs" data-testid="fi-spread-result">
            <div>
              <p className="text-xxs text-muted uppercase">利差 bps</p>
              <p className="text-sm font-mono text-cyan">{spread.spreadBps.toFixed(2)}</p>
            </div>
            <div>
              <p className="text-xxs text-muted uppercase">利差 %</p>
              <p className="text-sm font-mono text-white">{spread.spreadPct.toFixed(4)}</p>
            </div>
          </div>
        )}
      </Card>

      {/* Repo rates */}
      <Card variant="gradient" padding="md">
        <span className="label-uppercase">回购参考利率</span>
        <div className="mt-3 overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="bg-elevated text-left">
                <th className="px-3 py-2 text-xs text-secondary uppercase">代码</th>
                <th className="px-3 py-2 text-xs text-secondary uppercase">名称</th>
                <th className="px-3 py-2 text-xs text-secondary uppercase text-right">利率 %</th>
              </tr>
            </thead>
            <tbody>
              {repo.map((r) => (
                <tr key={r.code} className="border-t border-white/5">
                  <td className="px-3 py-1.5 text-xs font-mono text-cyan">{r.code}</td>
                  <td className="px-3 py-1.5 text-xs text-white">{r.name}</td>
                  <td className="px-3 py-1.5 text-xs text-right font-mono">{r.rate.toFixed(2)}</td>
                </tr>
              ))}
              {repo.length === 0 && !loading && (
                <tr><td className="px-3 py-2 text-xs text-muted">暂无回购数据</td></tr>
              )}
            </tbody>
          </table>
        </div>
      </Card>
    </div>
  );
};

export default FixedIncomePage;
