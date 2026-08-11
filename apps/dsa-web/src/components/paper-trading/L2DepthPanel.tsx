import { useCallback, useEffect, useState } from 'react';
import { paperTradingApi } from '../../api/paperTrading';
import { Card, EmptyState, InlineAlert } from '../common';

interface L2DepthLevel {
  price: number;
  volume: number;
}

interface L2DepthData {
  code: string;
  timestamp?: string;
  bids?: L2DepthLevel[];
  asks?: L2DepthLevel[];
  bid_ask_imbalance?: number;
  depth_weighted_spread?: number;
  source?: string;
}

interface Props {
  /** Stock code to show L2 depth for (e.g. '600519'). */
  code?: string;
  /** Polling interval in ms (default 10s). */
  pollMs?: number;
}

/**
 * L2 ten-level order book panel.
 *
 * Fetches depth data from the paper-trading L2 endpoint; shows an empty
 * state when L2 is not available (provider not configured / no data).
 */
export function L2DepthPanel({ code, pollMs = 10_000 }: Props) {
  const [data, setData] = useState<L2DepthData | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    if (!code) {
      setLoading(false);
      return;
    }
    setError(null);
    try {
      const resp = await paperTradingApi.getL2Depth<L2DepthData>(code);
      setData(resp);
    } catch (e) {
      setError(e instanceof Error ? e.message : "L2 数据加载失败");
    } finally {
      setLoading(false);
    }
  }, [code]);

  useEffect(() => {
    setLoading(true);
    void load();
    if (pollMs > 0) {
      const timer = window.setInterval(() => { void load(); }, pollMs);
      return () => window.clearInterval(timer);
    }
  }, [load, pollMs]);

  const hasData = data && (data.bids?.length ?? 0) > 0 && (data.asks?.length ?? 0) > 0;
  const noProvider = data?.source === "no-data" || data?.source === "error";

  return (
    <Card title="L2 深度行情" subtitle={code ? `十档盘口 · ${code}` : "未选择股票"}>
      {error && <InlineAlert variant="danger" title="加载失败" message={error} />}

      {!code ? (
        <EmptyState title="未选择股票" description="在持仓中选择一只股票查看 L2 盘口" />
      ) : loading && !data ? (
        <div className="space-y-2 py-1">
          {[0, 1, 2].map((i) => <div key={i} className="h-8 animate-pulse rounded-md bg-surface" />)}
        </div>
      ) : noProvider ? (
        <EmptyState title="L2 数据不可用" description="未配置 L2 数据源或暂无行情推送" />
      ) : !hasData ? (
        <EmptyState title="暂无盘口数据" />
      ) : (
        <div className="space-y-2">
          {/* Asks (sell side) — shown reversed so best ask is at the bottom */}
          <div className="space-y-0.5">
            {[...(data.asks ?? [])].reverse().map((lvl, i) => (
              <div key={`ask-${i}`} className="flex items-center justify-between rounded bg-danger/5 px-2 py-0.5 text-xs">
                <span className="font-mono text-danger">{lvl.price.toFixed(2)}</span>
                <span className="font-mono text-muted-foreground">{lvl.volume}</span>
              </div>
            ))}
          </div>

          {/* Spread */}
          <div className="flex items-center justify-between border-y border-border py-1 text-[11px] text-muted-foreground">
            <span>价差 {data.depth_weighted_spread?.toFixed(4) ?? "--"}</span>
            <span>失衡 {(data.bid_ask_imbalance ?? 0).toFixed(3)}</span>
          </div>

          {/* Bids (buy side) — best bid at the top */}
          <div className="space-y-0.5">
            {(data.bids ?? []).map((lvl, i) => (
              <div key={`bid-${i}`} className="flex items-center justify-between rounded bg-success/5 px-2 py-0.5 text-xs">
                <span className="font-mono text-success">{lvl.price.toFixed(2)}</span>
                <span className="font-mono text-muted-foreground">{lvl.volume}</span>
              </div>
            ))}
          </div>

          <div className="text-right text-[10px] text-muted-foreground">
            {data.timestamp ? new Date(data.timestamp).toLocaleTimeString() : ""} · {data.source ?? ""}
          </div>
        </div>
      )}
    </Card>
  );
}
