/**
 * Multi-market status dashboard — shows connection health per exchange.
 *
 * Polls listener status for each market (CN/HK/US) and displays
 * live session state with colour-coded cards.
 */

import { useEffect, useState } from "react";
import { WifiOff, Sun, Moon, Clock } from "lucide-react";
import { paperTradingApi } from "../../api/paperTrading";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

interface MarketStatus {
  market: string;
  label: string;
  connected: boolean;
  isSessionOpen: boolean;
  lastTickAt?: string;
  watchedCodesCount: number;
}

interface Props {
  accountId: number;
  className?: string;
}

const POLL_MS = 10_000;

const MARKET_LABELS: Record<string, string> = {
  cn: "A股",
  hk: "港股",
  us: "美股",
  jp: "日股",
  kr: "韩股",
};

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

export function MarketStatusDashboard({ accountId, className = "" }: Props) {
  const [statuses, setStatuses] = useState<MarketStatus[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;
    const fetch = async () => {
      try {
        const s = await paperTradingApi.getListenerStatus();
        if (cancelled || !s) return;
        const markets = s.markets;
        const connected = s.running;
        const lastTick = s.lastSettleDate;
        const count = s.watchedCodesCount;

        const items: MarketStatus[] = (markets ?? ["cn"]).map((m) => ({
          market: m,
          label: MARKET_LABELS[m] ?? m.toUpperCase(),
          connected: connected ?? false,
          isSessionOpen: isBusinessHours(m),
          lastTickAt: lastTick,
          watchedCodesCount: count ?? 0,
        }));
        if (!cancelled) setStatuses(items);
      } catch {
        // listener not running — show disconnected
        if (!cancelled) {
          setStatuses([
            { market: "cn", label: "A股", connected: false, isSessionOpen: false, watchedCodesCount: 0 },
            { market: "hk", label: "港股", connected: false, isSessionOpen: false, watchedCodesCount: 0 },
            { market: "us", label: "美股", connected: false, isSessionOpen: false, watchedCodesCount: 0 },
          ]);
        }
      } finally {
        if (!cancelled) setLoading(false);
      }
    };
    fetch();
    const timer = setInterval(fetch, POLL_MS);
    return () => { cancelled = true; clearInterval(timer); };
  }, [accountId]);

  // Render helper.
  const statusColor = (connected: boolean, open: boolean): string => {
    if (!connected) return "border-red-300 bg-red-50 dark:border-red-700 dark:bg-red-950";
    if (!open) return "border-amber-300 bg-amber-50 dark:border-amber-700 dark:bg-amber-950";
    return "border-green-300 bg-green-50 dark:border-green-700 dark:bg-green-950";
  };

  const statusIcon = (connected: boolean, open: boolean) => {
    if (!connected) return <WifiOff size={14} className="text-red-500" />;
    if (!open) return <Moon size={14} className="text-amber-500" />;
    return <Sun size={14} className="text-green-500" />;
  };

  const statusText = (connected: boolean, open: boolean): string => {
    if (!connected) return "未连接";
    if (!open) return "已休市";
    return "交易中";
  };

  if (loading) {
    return <div className={`text-xs text-muted-foreground flex items-center gap-2 ${className}`}><Clock size={14} /> 加载市场状态…</div>;
  }

  return (
    <div className={`flex flex-wrap gap-3 ${className}`}>
      {statuses.map((s) => (
        <div
          key={s.market}
          className={`flex items-center gap-2 px-3 py-2 rounded-lg border text-xs ${statusColor(s.connected, s.isSessionOpen)}`}
        >
          {statusIcon(s.connected, s.isSessionOpen)}
          <span className="font-semibold">{s.label}</span>
          <span className="text-muted-foreground">{statusText(s.connected, s.isSessionOpen)}</span>
          {s.connected && (
            <span className="text-[10px] text-muted-foreground ml-1">{s.watchedCodesCount} 只</span>
          )}
        </div>
      ))}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Helper: rough business-hours check (client-side approximation)
// ---------------------------------------------------------------------------

function isBusinessHours(market: string): boolean {
  const now = new Date();
  const utcH = now.getUTCHours();
  const utcM = now.getUTCMinutes();
  const utcDay = now.getUTCDay();
  if (utcDay === 0 || utcDay === 6) return false;

  const t = utcH + utcM / 60;
  switch (market) {
    case "cn":
    case "hk":
      return (t >= 1.5 && t <= 3.5) || (t >= 5 && t <= 7); // UTC+8: 9:30-11:30, 13:00-15:00
    case "us":
      return t >= 13.5 && t <= 20; // UTC-4: 9:30-16:00 EDT
    default:
      return true;
  }
}

export default MarketStatusDashboard;
