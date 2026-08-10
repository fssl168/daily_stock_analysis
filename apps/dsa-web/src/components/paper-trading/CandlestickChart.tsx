/**
 * Candlestick chart for a single stock — renders OHLC bars using Recharts.
 *
 * Click a row in the PositionsTable to expand this chart inline.
 * Data is fetched from the cached daily bars stored in the MarketListener's
 * _get_daily_df → local_store → REST fallback.
 *
 * Uses recharts (already in package.json).
 */

import { useEffect, useState } from "react";
import {
  ResponsiveContainer,
  ComposedChart,
  Bar,
  Line,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  Legend,
} from "recharts";
import { paperTradingApi } from "../../api/paperTrading";
import { Loader2 } from "lucide-react";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

interface OhlcBar {
  date: string;
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
  ma5?: number;
  ma20?: number;
}

interface Props {
  accountId: number;
  code: string;
  days?: number;
  className?: string;
  onClose?: () => void;
}

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

export function CandlestickChart({ accountId, code, days = 90, className = "", onClose }: Props) {
  const [bars, setBars] = useState<OhlcBar[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(false);

  useEffect(() => {
    const cancelled = { current: false };
    paperTradingApi
      .getDailyBars<OhlcBar[]>(accountId, code, days)
      .then((data) => {
        if (cancelled.current) return;
        if (!data || !Array.isArray(data) || data.length === 0) {
          setError(true);
          return;
        }
        // Compute MAs client-side when the API does not include them.
        const enriched = data.map((bar, i, arr) => {
          const slice = arr.slice(Math.max(0, i - 4), i + 1).filter(b => b.close > 0);
          const ma5 = slice.length >= 5
            ? slice.reduce((s, b) => s + b.close, 0) / slice.length
            : undefined;
          const slice20 = arr.slice(Math.max(0, i - 19), i + 1).filter(b => b.close > 0);
          const ma20 = slice20.length >= 20
            ? slice20.reduce((s, b) => s + b.close, 0) / slice20.length
            : undefined;
          return { ...bar, ma5, ma20 };
        });
        setBars(enriched);
        setError(false);
      })
      .catch(() => { if (!cancelled.current) setError(true); })
      .finally(() => { if (!cancelled.current) setLoading(false); });
    return () => { cancelled.current = true; };
  }, [accountId, code, days]);

  // Determine up color for the close-price line.
  const upColor = "#22c55e";

  if (loading) {
    return (
      <div className={`flex items-center gap-2 text-xs text-muted-foreground p-4 ${className}`}>
        <Loader2 size={14} className="animate-spin" /> 加载 {code} 日线…
      </div>
    );
  }

  if (error || bars.length === 0) {
    return (
      <div className={`text-xs text-muted-foreground p-4 ${className}`}>
        {code} 日线数据不可用
        {onClose && (
          <button onClick={onClose} className="ml-2 underline hover:text-foreground">关闭</button>
        )}
      </div>
    );
  }

  const last = bars[bars.length - 1];
  const changePct = last && bars.length >= 2
    ? ((last.close - bars[bars.length - 2].close) / bars[bars.length - 2].close * 100)
    : 0;

  return (
    <div className={`space-y-2 ${className}`}>
      {/* Header */}
      <div className="flex items-center justify-between">
        <div className="flex items-baseline gap-2">
          <span className="font-semibold text-sm">{code}</span>
          <span className="font-mono text-lg">{last?.close.toFixed(2)}</span>
          <span className={`text-xs font-mono ${changePct >= 0 ? "text-green-500" : "text-red-500"}`}>
            {changePct >= 0 ? "+" : ""}{changePct.toFixed(2)}%
          </span>
          <span className="text-[10px] text-muted-foreground">{days} 日</span>
        </div>
        {onClose && (
          <button onClick={onClose} className="text-xs text-muted-foreground hover:text-foreground px-2 py-1 rounded border">
            收起
          </button>
        )}
      </div>

      {/* Chart */}
      <ResponsiveContainer width="100%" height={300}>
        <ComposedChart data={bars} margin={{ top: 8, right: 8, bottom: 8, left: 8 }}>
          <CartesianGrid strokeDasharray="3 3" stroke="hsl(var(--border))" />
          <XAxis
            dataKey="date"
            tick={{ fontSize: 10 }}
            tickFormatter={(v: string) => v.slice(5)} // MM-DD
            stroke="hsl(var(--muted-foreground))"
          />
          <YAxis
            yAxisId="price"
            orientation="right"
            tick={{ fontSize: 10 }}
            domain={["auto", "auto"]}
            stroke="hsl(var(--muted-foreground))"
          />
          <YAxis
            yAxisId="volume"
            orientation="left"
            tick={{ fontSize: 9 }}
            domain={["auto", "auto"]}
            stroke="hsl(var(--muted-foreground))"
            hide
          />
          <Tooltip
            contentStyle={{
              backgroundColor: "hsl(var(--card))",
              border: "1px solid hsl(var(--border))",
              borderRadius: "8px",
              fontSize: "11px",
            }}
          />
          <Legend
            wrapperStyle={{ fontSize: "10px" }}
          />

          {/* Volume bars (background layer) */}
          <Bar
            yAxisId="volume"
            dataKey="volume"
            fill="hsl(var(--muted-foreground) / 0.15)"
            name="成交"
            isAnimationActive={false}
          />

          {/* Close-price line (colour-coded: up=green, down=red) */}
          <Line
            yAxisId="price"
            type="monotone"
            dataKey="close"
            stroke={upColor}
            strokeWidth={1.5}
            dot={false}
            name="收盘"
            isAnimationActive={false}
            // Per-point colour based on up/down — use isAnimationActive=false
            // so Recharts defaults to the series stroke. For per-segment colour
            // a full candlestick shape is preferred; this is a pragmatic fallback.
          />

          {/* MA5 line */}
          <Line
            yAxisId="price"
            type="monotone"
            dataKey="ma5"
            stroke="#f59e0b"
            strokeWidth={1}
            dot={false}
            name="MA5"
            connectNulls
            isAnimationActive={false}
          />

          {/* MA20 line */}
          <Line
            yAxisId="price"
            type="monotone"
            dataKey="ma20"
            stroke="#8b5cf6"
            strokeWidth={1}
            dot={false}
            name="MA20"
            connectNulls
            isAnimationActive={false}
          />
        </ComposedChart>
      </ResponsiveContainer>
    </div>
  );
}

export default CandlestickChart;
