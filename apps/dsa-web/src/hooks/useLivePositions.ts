/**
 * Real-time PnL streaming hook — computes floating PnL from live quotes.
 *
 * Takes base positions (cost basis) from a REST fetch, then applies
 * WebSocket price updates to compute current unrealized PnL in real time.
 */

import { useEffect, useState } from "react";
import { useWebSocket } from "./useWebSocket";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export interface PositionBase {
  code: string;
  name?: string;
  quantity: number;
  availableQuantity: number;
  avgCost: number;
  stopLoss?: number;
  takeProfit?: number;
}

export interface PositionLive extends PositionBase {
  lastPrice: number;
  marketValue: number;
  floatingPnl: number;
  floatingPnlPct: number;
  changePct: number;
}

interface PriceTick {
  code: string;
  price: number;
  changePct: number;
}

interface UseLivePositionsOptions {
  accountId: number;
  /** Base positions (from REST). When null, nothing to stream. */
  positions: PositionBase[] | null;
  enabled?: boolean;
}

// ---------------------------------------------------------------------------
// Hook
// ---------------------------------------------------------------------------

export function useLivePositions({
  accountId,
  positions,
  enabled = true,
}: UseLivePositionsOptions): PositionLive[] {
  const [prices, setPrices] = useState<Map<string, PriceTick>>(new Map());

  const wsUrl = `/api/v1/paper-trading/${accountId}/ws/quotes`;
  const { lastMessage } = useWebSocket<PriceTick>({
    url: wsUrl,
    enabled,
    autoReconnect: true,
  });

  // Cache latest WS pushes into a price map.
  useEffect(() => {
    if (!lastMessage || !(lastMessage as unknown as Record<string, unknown>).code) return;
    const tick = lastMessage as PriceTick;
    // setState is driven by an external WS event — correct streaming pattern.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setPrices((prev) => {
      const next = new Map(prev);
      next.set(tick.code, tick);
      return next;
    });
  }, [lastMessage]);

  // Merge base positions with live prices.
  if (!positions || positions.length === 0) return [];

  return positions.map((pos): PositionLive => {
    const tick = prices.get(pos.code);
    const price = tick?.price ?? pos.avgCost;
    const marketValue = price * pos.quantity;
    const pnl = (price - pos.avgCost) * pos.quantity;
    const pnlPct = pos.avgCost > 0 ? ((price / pos.avgCost) - 1) * 100 : 0;

    return {
      ...pos,
      lastPrice: price,
      marketValue,
      floatingPnl: pnl,
      floatingPnlPct: pnlPct,
      changePct: tick?.changePct ?? 0,
    };
  });
}

export default useLivePositions;
