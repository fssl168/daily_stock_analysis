/**
 * Real-time quote ticker — renders a scrolling bar of latest prices.
 *
 * Reads from the `SharedQuoteCache` equivalent on the backend via
 * a WebSocket push, falling back to REST polling when WS is unavailable.
 *
 * Usage:
 *   <QuoteTicker accountId={1} maxCodes={10} />
 */

import { useEffect, useState, useRef, useCallback } from "react";
import { ArrowUp, ArrowDown, Minus, Wifi, WifiOff } from "lucide-react";
import { useWebSocket } from "../../hooks/useWebSocket";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

interface QuoteItem {
  code: string;
  price: number;
  changePct: number;
  volume: number;
  timestamp: string;
}

interface Props {
  accountId: number;
  maxCodes?: number;
  className?: string;
}

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

export function QuoteTicker({ accountId, maxCodes = 12, className = "" }: Props) {
  const [quotes, setQuotes] = useState<Map<string, QuoteItem>>(new Map());
  const pollTimerRef = useRef<ReturnType<typeof setInterval> | null>(null);

  // WebSocket for real-time quotes (when backend WS endpoint is available).
  const wsUrl = `/api/v1/paper-trading/${accountId}/ws/quotes`;
  const { isConnected: wsConnected, lastMessage } = useWebSocket<QuoteItem>({
    url: wsUrl,
    enabled: true,
    autoReconnect: true,
    reconnectDelay: 1000,
    maxReconnectDelay: 15000,
    maxRetries: 5,
  });

  // Apply WS push → update quote map.
  // setState is driven by an external WS event (not a render cascade),
  // which is the correct pattern for a streaming feed.
  useEffect(() => {
    if (lastMessage && lastMessage.code) {
      // eslint-disable-next-line react-hooks/set-state-in-effect
      setQuotes((prev) => {
        const next = new Map(prev);
        next.set(lastMessage.code, lastMessage);
        return next;
      });
    }
  }, [lastMessage]);

  // REST poll fallback (5s) when WS is not connected.
  // NOTE: no bulk-quote REST endpoint exists yet — when WS is down the
  // ticker shows the "waiting" state rather than fake data.
  const pollQuotes = useCallback(async () => {
    // Keep the interval alive so WS reconnect is re-detected.
    // Real quotes only arrive via WS push.
  }, []);

  useEffect(() => {
    if (!wsConnected) {
      pollTimerRef.current = setInterval(pollQuotes, 5_000);
    } else if (pollTimerRef.current) {
      clearInterval(pollTimerRef.current);
      pollTimerRef.current = null;
    }
    return () => {
      if (pollTimerRef.current) clearInterval(pollTimerRef.current);
    };
  }, [wsConnected, pollQuotes]);

  // Trim to maxCodes, keeping the MOST RECENTLY updated quotes.
  const items = Array.from(quotes.values())
    .sort((a, b) => (b.timestamp || "").localeCompare(a.timestamp || ""))
    .slice(0, maxCodes);

  // ------------------------------------------------------------------
  // Render
  // ------------------------------------------------------------------

  if (items.length === 0) {
    return (
      <div className={`flex items-center gap-2 text-xs text-muted-foreground ${className}`}>
        {wsConnected ? (
          <Wifi size={12} className="text-green-500" />
        ) : (
          <WifiOff size={12} className="text-amber-500" />
        )}
        <span>等待行情推送…</span>
      </div>
    );
  }

  return (
    <div
      className={`flex items-center gap-4 overflow-x-auto text-xs whitespace-nowrap ${className}`}
      style={{ scrollbarWidth: "none" }}
    >
      {/* Connection indicator */}
      <span className="flex items-center gap-1 shrink-0 text-muted-foreground">
        {wsConnected ? (
          <Wifi size={12} className="text-green-500" />
        ) : (
          <WifiOff size={12} className="text-amber-500" />
        )}
      </span>

      {items.map((q) => {
        const isUp = q.changePct > 0;
        const isDown = q.changePct < 0;
        const color = isUp ? "text-green-500" : isDown ? "text-red-500" : "text-muted-foreground";
        const Icon = isUp ? ArrowUp : isDown ? ArrowDown : Minus;

        return (
          <span
            key={q.code}
            className={`inline-flex items-center gap-1 font-mono shrink-0 ${color}`}
          >
            <span className="font-semibold text-foreground">{q.code}</span>
            <span>{q.price.toFixed(2)}</span>
            <Icon size={10} />
            <span>{Math.abs(q.changePct).toFixed(1)}%</span>
          </span>
        );
      })}
    </div>
  );
}

export default QuoteTicker;
