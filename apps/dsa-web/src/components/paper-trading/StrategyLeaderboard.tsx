/**
 * Strategy leaderboard — multi-strategy performance comparison.
 *
 * Sorted by Sharpe ratio (desc). Shows current SignalFusion weight
 * and drift-adjusted status.
 */

import { useEffect, useState } from "react";
import { Trophy, BarChart3 } from "lucide-react";
import { paperTradingApi } from "../../api/paperTrading";
import { requestQueue } from "../../utils/requestQueue";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

interface StrategyPerformance {
  name: string;
  sharpeRatio: number;
  winRate: number;
  maxDrawdownPct: number;
  calmarRatio?: number;
  avgDailyReturnPct: number;
  currentWeight: number;
  status: "active" | "reduced" | "paused" | "retired";
  tradeCount: number;
}

interface Props {
  accountId: number;
  className?: string;
}

const POLL_MS = 60_000;

const STATUS_COLOR: Record<StrategyPerformance["status"], string> = {
  active: "text-green-500",
  reduced: "text-amber-500",
  paused: "text-orange-500",
  retired: "text-red-500",
};

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

export function StrategyLeaderboard({ accountId, className = "" }: Props) {
  const [strategies, setStrategies] = useState<StrategyPerformance[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;
    const fetch = () => {
      requestQueue
        .enqueue(() => paperTradingApi.getStrategyPerformance<StrategyPerformance[]>(accountId))
        .then((list) => {
          if (!cancelled) {
            const sorted = [...(list ?? [])].sort((a, b) => b.sharpeRatio - a.sharpeRatio);
            setStrategies(sorted);
          }
        })
        .catch(() => {})
        .finally(() => { if (!cancelled) setLoading(false); });
    };
    fetch();
    const timer = setInterval(fetch, POLL_MS);
    return () => { cancelled = true; clearInterval(timer); };
  }, [accountId]);

  if (loading && strategies.length === 0) {
    return <div className={`text-xs text-muted-foreground flex items-center gap-2 ${className}`}><BarChart3 size={14} /> 排行榜加载中…</div>;
  }
  if (strategies.length === 0) {
    return <div className={`text-xs text-muted-foreground ${className}`}>暂无策略数据</div>;
  }

  return (
    <div className={`overflow-x-auto ${className}`}>
      <table className="w-full text-xs">
        <thead>
          <tr className="border-b text-muted-foreground">
            <th className="text-left py-1 pr-2">#</th>
            <th className="text-left py-1 pr-4">策略</th>
            <th className="text-right py-1 px-2">Sharpe</th>
            <th className="text-right py-1 px-2">胜率</th>
            <th className="text-right py-1 px-2">最大回撤</th>
            <th className="text-right py-1 px-2">日均收益</th>
            <th className="text-right py-1 px-2">权重</th>
            <th className="text-right py-1 pl-2">笔数</th>
          </tr>
        </thead>
        <tbody>
          {strategies.map((s, idx) => (
            <tr
              key={s.name}
              className={`border-b border-border/30 ${idx % 2 === 0 ? "bg-muted/30" : ""}`}
            >
              <td className="py-1 pr-2 text-muted-foreground">
                {idx === 0 ? <Trophy size={12} className="text-amber-500 inline" /> : idx + 1}
              </td>
              <td className="py-1 pr-4 font-semibold truncate max-w-[120px]">
                <span className={STATUS_COLOR[s.status]} aria-label={s.status}>{s.name}</span>
              </td>
              <td className={`py-1 px-2 text-right font-mono tabular-nums ${s.sharpeRatio > 1 ? "text-green-500" : s.sharpeRatio < 0 ? "text-red-500" : ""}`}>
                {s.sharpeRatio.toFixed(2)}
              </td>
              <td className="py-1 px-2 text-right font-mono tabular-nums">
                {(s.winRate * 100).toFixed(0)}%
              </td>
              <td className={`py-1 px-2 text-right font-mono tabular-nums ${s.maxDrawdownPct > 20 ? "text-red-500" : ""}`}>
                -{s.maxDrawdownPct.toFixed(1)}%
              </td>
              <td className={`py-1 px-2 text-right font-mono tabular-nums ${s.avgDailyReturnPct >= 0 ? "text-green-500" : "text-red-500"}`}>
                {s.avgDailyReturnPct >= 0 ? "+" : ""}{s.avgDailyReturnPct.toFixed(2)}%
              </td>
              <td className={`py-1 px-2 text-right font-mono tabular-nums ${s.currentWeight === 0 ? "text-red-500 line-through" : s.currentWeight < 1 ? "text-amber-500" : ""}`}>
                {s.currentWeight.toFixed(2)}
              </td>
              <td className="py-1 pl-2 text-right text-muted-foreground">{s.tradeCount}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export default StrategyLeaderboard;
