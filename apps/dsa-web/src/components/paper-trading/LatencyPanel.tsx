/**
 * Latency monitoring panel — tick-level p50/p95/p99 metrics.
 *
 * Reads from GET /api/v1/paper-trading/{id}/latency (polled every 5s).
 * Falls back to a static placeholder when the endpoint is not yet available.
 */

import { useEffect, useState } from "react";
import { Gauge, Timer, AlertCircle } from "lucide-react";
import { paperTradingApi } from "../../api/paperTrading";
import type { LatencyReport } from "../../types/paperTrading";

interface Props {
  accountId: number;
  className?: string;
}

const POLL_MS = 5_000;

export function LatencyPanel({ accountId, className = "" }: Props) {
  const [report, setReport] = useState<LatencyReport | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(false);

  useEffect(() => {
    let cancelled = false;
    const fetch = () => {
      paperTradingApi
        .getLatency(accountId)
        .then((r) => { if (!cancelled) { setReport(r); setError(false); } })
        .catch(() => { if (!cancelled) setError(true); })
        .finally(() => { if (!cancelled) setLoading(false); });
    };
    fetch();
    const timer = setInterval(fetch, POLL_MS);
    return () => { cancelled = true; clearInterval(timer); };
  }, [accountId]);

  // ---- Loading / error states ----
  if (loading && !report) {
    return <div className={`text-xs text-muted-foreground flex items-center gap-2 ${className}`}><Timer size={14} /> 延迟数据加载中…</div>;
  }
  if (error && !report) {
    return <div className={`text-xs text-muted-foreground flex items-center gap-2 ${className}`}><Timer size={14} /> 延迟数据不可用（端点未就绪）</div>;
  }
  if (!report) return null;

  const { tickTotalMs, steps } = report;
  const totalWarn = tickTotalMs.p95 > 1000;

  return (
    <div className={`space-y-3 ${className}`}>
      {/* Header card */}
      <div className="flex items-center gap-3">
        <Gauge size={18} className={totalWarn ? "text-amber-500" : "text-green-500"} />
        <div className="flex items-baseline gap-2">
          <span className="text-sm font-semibold">全链路延迟</span>
          <span className="text-xs text-muted-foreground">
            p50: <span className="font-mono font-semibold text-foreground">{tickTotalMs.p50.toFixed(0)}ms</span>
          </span>
          <span className="text-xs text-muted-foreground">
            p95: <span className={`font-mono font-semibold ${totalWarn ? "text-amber-500" : "text-foreground"}`}>{tickTotalMs.p95.toFixed(0)}ms</span>
          </span>
          <span className="text-xs text-muted-foreground">
            p99: <span className="font-mono font-semibold text-foreground">{tickTotalMs.p99.toFixed(0)}ms</span>
          </span>
          {totalWarn && (
            <span title="p95 > 1000ms">
              <AlertCircle size={14} className="text-amber-500" />
            </span>
          )}
        </div>
      </div>

      {/* Step breakdown */}
      {steps.length > 0 && (
        <div className="grid grid-cols-2 gap-2">
          {steps.map((step) => (
            <div key={step.name} className="flex items-center justify-between rounded-md border px-2 py-1 text-xs">
              <span className="text-muted-foreground truncate">{step.name}</span>
              <span className="font-mono tabular-nums ml-2">
                <span className="font-semibold">{step.p50Ms.toFixed(0)}</span>
                <span className="text-muted-foreground"> / </span>
                <span className={step.p95Ms > 500 ? "text-amber-500 font-semibold" : ""}>{step.p95Ms.toFixed(0)}</span>
                <span className="text-muted-foreground"> ms</span>
              </span>
            </div>
          ))}
        </div>
      )}

      <div className="text-[10px] text-muted-foreground">{POLL_MS / 1000}s 轮询（生产环境建议切换为 WS 推送）</div>
    </div>
  );
}

export default LatencyPanel;
