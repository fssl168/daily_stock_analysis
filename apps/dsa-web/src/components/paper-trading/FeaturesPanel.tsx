/**
 * Feature pipeline panel — view computed features and trigger recalculation.
 *
 * Shows the most recent feature dataframe as a sortable table.
 */

import { useEffect, useRef, useState } from "react";
import { Brain, RefreshCw } from "lucide-react";
import { paperTradingApi } from "../../api/paperTrading";
import { requestQueue } from "../../utils/requestQueue";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

interface FeatureRow {
  code: string;
  date: string;
  smaCrossover: number;
  rsi: number;
  volumeSpike: number;
  maAlignment: number;
  bidAskImbalance: number;
}

interface FeatureSnapshot {
  asOf: string;
  features: FeatureRow[];
  skippedCodes: string[];
}

interface Props {
  accountId: number;
  className?: string;
}

const POLL_MS = 300_000; // 5 min — features change slowly

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

export function FeaturesPanel({ accountId, className = "" }: Props) {
  const [snapshot, setSnapshot] = useState<FeatureSnapshot | null>(null);
  const [loading, setLoading] = useState(true);
  const [recomputing, setRecomputing] = useState(false);

  useEffect(() => {
    let cancelled = false;
    const fetch = () => {
      requestQueue
        .enqueue(() => paperTradingApi.getFeatures<FeatureSnapshot>(accountId))
        .then((r) => { if (!cancelled) setSnapshot(r); })
        .catch(() => {})
        .finally(() => { if (!cancelled) setLoading(false); });
    };
    fetch();
    const timer = setInterval(fetch, POLL_MS);
    return () => { cancelled = true; clearInterval(timer); };
  }, [accountId]);

  const recomputeTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => () => {
    if (recomputeTimerRef.current) clearTimeout(recomputeTimerRef.current);
  }, []);

  const handleRecompute = () => {
    setRecomputing(true);
    requestQueue
      .enqueue(() => paperTradingApi.recomputeFeatures(accountId))
      .then(() => {
        recomputeTimerRef.current = setTimeout(() => {
          requestQueue
            .enqueue(() => paperTradingApi.getFeatures<FeatureSnapshot>(accountId))
            .then((r) => {
              setSnapshot(r);
              setRecomputing(false);
            })
            .catch(() => setRecomputing(false));
        }, 3_000);
      })
      .catch(() => setRecomputing(false));
  };

  if (loading && !snapshot) {
    return <div className={`text-xs text-muted-foreground flex items-center gap-2 ${className}`}><Brain size={14} /> 特征数据加载中…</div>;
  }
  if (!snapshot) return <div className={`text-xs text-muted-foreground ${className}`}>暂无特征数据</div>;

  const { features, skippedCodes, asOf } = snapshot;

  return (
    <div className={`space-y-3 ${className}`}>
      {/* Header */}
      <div className="flex items-center justify-between">
        <span className="text-xs text-muted-foreground">
          快照日期: <span className="font-mono text-foreground">{asOf}</span>
          {skippedCodes.length > 0 && (
            <span className="ml-2 text-amber-500">跳过了 {skippedCodes.length} 个代码（数据不足）</span>
          )}
        </span>
        <button
          onClick={handleRecompute}
          disabled={recomputing}
          className="flex items-center gap-1 text-xs px-2 py-1 rounded border hover:bg-muted disabled:opacity-50"
        >
          <RefreshCw size={12} className={recomputing ? "animate-spin" : ""} />
          {recomputing ? "计算中…" : "重新计算"}
        </button>
      </div>

      {/* Table */}
      {features.length > 0 ? (
        <div className="overflow-x-auto">
          <table className="w-full text-[11px]">
            <thead>
              <tr className="border-b text-muted-foreground">
                <th className="text-left py-1 pr-2">代码</th>
                <th className="text-left py-1 px-1">日期</th>
                <th className="text-right py-1 px-1">SMA穿越</th>
                <th className="text-right py-1 px-1">RSI</th>
                <th className="text-right py-1 px-1">量能</th>
                <th className="text-right py-1 px-1">多头</th>
                <th className="text-right py-1 pl-1">不平衡</th>
              </tr>
            </thead>
            <tbody>
              {features.slice(0, 40).map((f, i) => (
                <tr key={`${f.code}-${f.date}-${i}`} className="border-b border-border/20">
                  <td className="py-0.5 pr-2 font-mono font-semibold">{f.code}</td>
                  <td className="py-0.5 px-1 text-muted-foreground">{f.date}</td>
                  <td className={`py-0.5 px-1 text-right font-mono ${f.smaCrossover === 1 ? "text-green-500" : "text-muted-foreground"}`}>
                    {f.smaCrossover === 1 ? "✓" : ""}
                  </td>
                  <td className={`py-0.5 px-1 text-right font-mono ${f.rsi > 70 ? "text-red-500" : f.rsi < 30 ? "text-green-500" : ""}`}>
                    {f.rsi.toFixed(1)}
                  </td>
                  <td className={`py-0.5 px-1 text-right font-mono ${f.volumeSpike === 1 ? "text-amber-500" : "text-muted-foreground"}`}>
                    {f.volumeSpike === 1 ? "◆" : ""}
                  </td>
                  <td className={`py-0.5 px-1 text-right font-mono ${f.maAlignment === 1 ? "text-green-500" : "text-red-500"}`}>
                    {f.maAlignment === 1 ? "⇧" : "⇩"}
                  </td>
                  <td className={`py-0.5 pl-1 text-right font-mono ${f.bidAskImbalance > 0 ? "text-green-500" : f.bidAskImbalance < 0 ? "text-red-500" : "text-muted-foreground"}`}>
                    {f.bidAskImbalance.toFixed(3)}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          {features.length > 40 && (
            <div className="text-[10px] text-muted-foreground mt-1">
              仅显示前 40 行（共 {features.length} 行）
            </div>
          )}
        </div>
      ) : (
        <div className="text-xs text-muted-foreground">无特征数据（可能所有代码数据不足）</div>
      )}
    </div>
  );
}

export default FeaturesPanel;
