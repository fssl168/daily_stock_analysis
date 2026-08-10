/**
 * Strategy lifecycle panel — activate / deactivate / review strategy pipeline.
 *
 * Shows each strategy's current state in the DRAFT→BACKTEST→PAPER→REVIEW→LIVE pipeline.
 */

import { useEffect, useState } from "react";
import { Play, Pause, RotateCcw, ChevronRight } from "lucide-react";
import { paperTradingApi } from "../../api/paperTrading";
import { Badge } from "../common/Badge";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

type LifecycleState = "DRAFT" | "BACKTEST" | "PAPER" | "REVIEW" | "LIVE" | "PAUSED" | "RETIRED";

interface StrategyLifecycleItem {
  name: string;
  state: LifecycleState;
  sharpeRatio?: number;
  winRate?: number;
  approvalCount: number;
}

interface Props {
  accountId: number;
  className?: string;
  /** Optional transition handler. When absent, action buttons are disabled with a tooltip. */
  onTransition?: (name: string, newState: LifecycleState) => Promise<void> | void;
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

const STATE_ORDER: LifecycleState[] = ["DRAFT", "BACKTEST", "PAPER", "REVIEW", "LIVE", "PAUSED", "RETIRED"];

const STATE_COLOR: Record<LifecycleState, "default" | "info" | "warning" | "success" | "danger"> = {
  DRAFT: "default",
  BACKTEST: "info",
  PAPER: "warning",
  REVIEW: "warning",
  LIVE: "success",
  PAUSED: "default",
  RETIRED: "danger",
};

const POLL_MS = 30_000;

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

export function StrategyLifecyclePanel({ accountId, className = "", onTransition }: Props) {
  const [strategies, setStrategies] = useState<StrategyLifecycleItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [transitioning, setTransitioning] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    const fetch = () => {
      paperTradingApi
        .getStrategies<StrategyLifecycleItem[]>(accountId)
        .then((r) => { if (!cancelled) setStrategies(r ?? []); })
        .catch(() => {})
        .finally(() => { if (!cancelled) setLoading(false); });
    };
    fetch();
    const timer = setInterval(fetch, POLL_MS);
    return () => { cancelled = true; clearInterval(timer); };
  }, [accountId]);

  if (loading && strategies.length === 0) {
    return <div className={`text-xs text-muted-foreground ${className}`}>策略数据加载中…</div>;
  }
  if (strategies.length === 0) {
    return <div className={`text-xs text-muted-foreground ${className}`}>暂无策略配置</div>;
  }

  return (
    <div className={`space-y-2 ${className}`}>
      {strategies.map((s) => {
        const stateIdx = STATE_ORDER.indexOf(s.state);

        return (
          <div key={s.name} className="rounded-md border p-3 space-y-2">
            {/* Header */}
            <div className="flex items-center gap-2">
              <span className="font-semibold text-sm truncate flex-1">{s.name}</span>
              <Badge variant={STATE_COLOR[s.state]}>{s.state}</Badge>
            </div>

            {/* Metrics */}
            <div className="flex items-center gap-4 text-[11px] text-muted-foreground">
              {s.sharpeRatio != null && (
                <span>Sharpe: <span className="font-mono text-foreground">{s.sharpeRatio.toFixed(2)}</span></span>
              )}
              {s.winRate != null && (
                <span>胜率: <span className="font-mono text-foreground">{(s.winRate * 100).toFixed(1)}%</span></span>
              )}
              <span>审批: <span className="font-mono text-foreground">{s.approvalCount}</span></span>
            </div>

            {/* State pipeline */}
            <div className="flex items-center gap-1 text-[10px]">
              {STATE_ORDER.map((st, i) => {
                const isCurrent = s.state === st;
                const isPast = i <= stateIdx;

                return (
                  <div key={st} className="flex items-center gap-1">
                    <span
                      className={`px-1.5 py-0.5 rounded ${
                        isCurrent
                          ? "bg-primary text-primary-foreground font-semibold"
                          : isPast
                          ? "bg-muted text-muted-foreground"
                          : "text-muted-foreground/50"
                      }`}
                    >
                      {st}
                    </span>
                    {i < STATE_ORDER.length - 1 && (
                      <ChevronRight size={10} className="text-muted-foreground/50" />
                    )}
                  </div>
                );
              })}
            </div>

            {/* Actions */}
            {s.state === "LIVE" && (
              <button
                disabled={!onTransition || transitioning === s.name}
                onClick={() => {
                  setTransitioning(s.name);
                  Promise.resolve(onTransition?.(s.name, "PAUSED")).finally(() => setTransitioning(null));
                }}
                className="flex items-center gap-1 text-xs text-amber-600 hover:text-amber-700 disabled:opacity-50"
                title={onTransition ? "暂停策略" : "后端未接入状态流转 API"}
              >
                <Pause size={12} /> {transitioning === s.name ? "切换中…" : "暂停"}
              </button>
            )}
            {s.state === "PAUSED" && (
              <button
                disabled={!onTransition || transitioning === s.name}
                onClick={() => {
                  setTransitioning(s.name);
                  Promise.resolve(onTransition?.(s.name, "LIVE")).finally(() => setTransitioning(null));
                }}
                className="flex items-center gap-1 text-xs text-green-600 hover:text-green-700 disabled:opacity-50"
                title={onTransition ? "恢复策略" : "后端未接入状态流转 API"}
              >
                <Play size={12} /> {transitioning === s.name ? "切换中…" : "恢复"}
              </button>
            )}
            {s.state === "RETIRED" && (
              <button
                disabled={!onTransition || transitioning === s.name}
                onClick={() => {
                  setTransitioning(s.name);
                  Promise.resolve(onTransition?.(s.name, "DRAFT")).finally(() => setTransitioning(null));
                }}
                className="flex items-center gap-1 text-xs text-muted-foreground hover:text-foreground disabled:opacity-50"
                title={onTransition ? "重新起草" : "后端未接入状态流转 API"}
              >
                <RotateCcw size={12} /> {transitioning === s.name ? "切换中…" : "重新起草"}
              </button>
            )}
          </div>
        );
      })}
    </div>
  );
}

export default StrategyLifecyclePanel;
