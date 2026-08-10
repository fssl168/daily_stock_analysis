/**
 * Strategy drift panel — shows drift detection results per strategy.
 *
 * Each strategy row displays: rolling Sharpe trend, consecutive losing days,
 * and a recommended action in a coloured badge.
 */

import { useEffect, useState } from "react";
import { TrendingDown, TrendingUp, Minus } from "lucide-react";
import { paperTradingApi } from "../../api/paperTrading";
import { Badge } from "../common/Badge";
import type { DriftReportItem } from "../../types/paperTrading";

interface Props {
  accountId: number;
  className?: string;
}

const POLL_MS = 60_000; // drift changes slowly

const ACTION_LABEL: Record<DriftReportItem["recommendedAction"], string> = {
  keep: "保持",
  reduce_weight: "降权",
  pause: "暂停",
  retire: "退役",
};

const ACTION_VARIANT: Record<DriftReportItem["recommendedAction"], "success" | "warning" | "danger"> = {
  keep: "success",
  reduce_weight: "warning",
  pause: "warning",
  retire: "danger",
};

export function DriftPanel({ accountId, className = "" }: Props) {
  const [reports, setReports] = useState<DriftReportItem[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;
    const fetch = () => {
      paperTradingApi
        .getDrift(accountId)
        .then((r) => { if (!cancelled) setReports(r ?? []); })
        .catch(() => {})
        .finally(() => { if (!cancelled) setLoading(false); });
    };
    fetch();
    const timer = setInterval(fetch, POLL_MS);
    return () => { cancelled = true; clearInterval(timer); };
  }, [accountId]);

  if (loading && reports.length === 0) {
    return <div className={`text-xs text-muted-foreground ${className}`}>漂移数据加载中…</div>;
  }
  if (reports.length === 0) return <div className={`text-xs text-muted-foreground ${className}`}>暂无策略或数据不足</div>;

  return (
    <div className={`space-y-2 ${className}`}>
      {reports.map((r) => {
        const trendIcon = r.sharpeTrend > 0.01 ? (
          <TrendingUp size={14} className="text-green-500" />
        ) : r.sharpeTrend < -0.01 ? (
          <TrendingDown size={14} className="text-red-500" />
        ) : (
          <Minus size={14} className="text-muted-foreground" />
        );

        return (
          <div key={r.strategyName} className="flex items-center gap-3 rounded-md border px-3 py-2 text-xs">
            <span className="font-semibold w-32 truncate">{r.strategyName}</span>
            <span className="flex items-center gap-1 text-muted-foreground">
              {trendIcon}
              <span className="font-mono">{r.sharpeTrend >= 0 ? "+" : ""}{r.sharpeTrend.toFixed(3)}</span>
            </span>
            <span className={`font-mono ${r.consecutiveLosingDays >= 10 ? "text-red-500" : "text-muted-foreground"}`}>
              连亏 {r.consecutiveLosingDays}d
            </span>
            <Badge variant={ACTION_VARIANT[r.recommendedAction]}>
              {ACTION_LABEL[r.recommendedAction]}
            </Badge>
          </div>
        );
      })}
    </div>
  );
}

export default DriftPanel;
