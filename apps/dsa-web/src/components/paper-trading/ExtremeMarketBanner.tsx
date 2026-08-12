/**
 * Extreme market banner — full-width alert bar when VIX-like volatility spikes.
 *
 * Polls GET /api/v1/paper-trading/{id}/extreme-market every 15s.
 * When active, renders a prominent red banner at the top of the paper-trading page.
 */

import { useEffect, useState } from "react";
import { AlertTriangle, X } from "lucide-react";
import { paperTradingApi } from "../../api/paperTrading";
import { requestQueue } from "../../utils/requestQueue";
import type { ExtremeMarketAlertItem } from "../../types/paperTrading";

interface Props {
  accountId: number;
  className?: string;
}

const POLL_MS = 15_000;

export function ExtremeMarketBanner({ accountId, className = "" }: Props) {
  const [alert, setAlert] = useState<ExtremeMarketAlertItem | null>(null);
  const [dismissed, setDismissed] = useState(false);

  useEffect(() => {
    let cancelled = false;
    const fetch = () => {
      requestQueue
        .enqueue(() => paperTradingApi.getExtremeMarket(accountId))
        .then((data) => {
          if (!cancelled) {
            if (data.isActive) {
              setAlert(data);
              setDismissed(false);
            } else {
              setAlert(null);
            }
          }
        })
        .catch(() => { /* endpoint not ready — silent */ });
    };
    fetch();
    const timer = setInterval(fetch, POLL_MS);
    return () => { cancelled = true; clearInterval(timer); };
  }, [accountId]);

  if (!alert || dismissed) return null;

  return (
    <div
      className={`flex items-center gap-3 px-4 py-2 rounded-lg border bg-red-50 border-red-300 dark:bg-red-950 dark:border-red-700 text-red-800 dark:text-red-200 text-sm ${className}`}
      role="alert"
    >
      <AlertTriangle size={16} className="shrink-0 text-red-600 dark:text-red-400" />
      <div className="flex-1 min-w-0">
        <span className="font-semibold">
          极端行情 ({alert.market.toUpperCase()})
        </span>
        {" "}— 当前波动率 {alert.currentVol.toFixed(1)}% 为历史均值 {alert.historicalVol.toFixed(1)}% 的 {alert.ratio.toFixed(1)} 倍
      </div>
      {alert.actions.length > 0 && (
        <div className="hidden sm:flex items-center gap-1 text-xs opacity-80">
          {alert.actions.map((a, i) => (
            <span key={i} className="bg-red-200 dark:bg-red-800 px-1.5 py-0.5 rounded">
              {a}
            </span>
          ))}
        </div>
      )}
      <button
        onClick={() => setDismissed(true)}
        className="shrink-0 p-0.5 rounded hover:bg-red-200 dark:hover:bg-red-800"
        aria-label="关闭"
      >
        <X size={14} />
      </button>
    </div>
  );
}

export default ExtremeMarketBanner;
