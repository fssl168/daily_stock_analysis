/**
 * Scrolling event log feed — real-time signal → risk → breaker → order → trade timeline.
 *
 * Subscribes to the WS event stream and renders a chronologically ordered,
 * color-coded event log.  Each event shows its type, associated code/order,
 * timestamp, and a brief message.
 */

import { useEffect, useState, useRef } from "react";
import {
  Activity,
  Shield,
  Zap,
  ShoppingCart,
  CheckCircle,
  XCircle,
  Clock,
} from "lucide-react";
import { useWebSocket } from "../../hooks/useWebSocket";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

type EventType =
  | "signal_generated"
  | "risk_check_passed"
  | "risk_check_failed"
  | "agent_review_passed"
  | "agent_review_vetoed"
  | "breaker_check_passed"
  | "breaker_rejected"
  | "order_created"
  | "order_filled"
  | "order_canceled"
  | "order_rejected"
  | "sl_tp_triggered"
  | "position_closed"
  | "extreme_market_activated"
  | "extreme_market_deactivated";

interface WsEvent {
  eventId: string;
  eventType: EventType;
  code?: string;
  orderId?: number;
  side?: string;
  price?: number;
  quantity?: number;
  strategyName?: string;
  reason?: string;
  timestamp: string;
}

interface Props {
  accountId: number;
  maxEvents?: number;
  className?: string;
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

const EVENT_META: Record<
  EventType,
  {
    icon: typeof Activity;
    label: string;
    color: string; // tailwind text class
  }
> = {
  signal_generated: { icon: Zap, label: "信号", color: "text-blue-500" },
  risk_check_passed: { icon: Shield, label: "风控通过", color: "text-green-500" },
  risk_check_failed: { icon: Shield, label: "风控拒绝", color: "text-red-500" },
  agent_review_passed: { icon: Activity, label: "AI通过", color: "text-green-500" },
  agent_review_vetoed: { icon: Activity, label: "AI否决", color: "text-red-500" },
  breaker_check_passed: { icon: Shield, label: "熔断通过", color: "text-green-500" },
  breaker_rejected: { icon: Shield, label: "熔断拒绝", color: "text-red-500" },
  order_created: { icon: Clock, label: "委托创建", color: "text-amber-500" },
  order_filled: { icon: CheckCircle, label: "成交", color: "text-green-500" },
  order_canceled: { icon: XCircle, label: "撤单", color: "text-gray-500" },
  order_rejected: { icon: XCircle, label: "拒绝", color: "text-red-500" },
  sl_tp_triggered: { icon: ShoppingCart, label: "止损/盈", color: "text-purple-500" },
  position_closed: { icon: CheckCircle, label: "平仓", color: "text-indigo-500" },
  extreme_market_activated: { icon: Activity, label: "极端行情启动", color: "text-red-500" },
  extreme_market_deactivated: { icon: Activity, label: "极端行情解除", color: "text-green-500" },
};

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

export function EventLogFeed({ accountId, maxEvents = 50, className = "" }: Props) {
  const [events, setEvents] = useState<WsEvent[]>([]);
  const containerRef = useRef<HTMLDivElement>(null);

  const wsUrl = `/api/v1/paper-trading/${accountId}/ws/events`;
  const { lastMessage, isConnected } = useWebSocket<WsEvent>({
    url: wsUrl,
    enabled: true,
    autoReconnect: true,
    maxRetries: 5,
  });

  // Push incoming events.
  useEffect(() => {
    if (!lastMessage || !(lastMessage as unknown as Record<string, unknown>).eventType) return;
    const evt = lastMessage as WsEvent;
    // setState is driven by an external WS event — correct streaming pattern.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setEvents((prev) => {
      const next = [evt, ...prev];
      return next.slice(0, maxEvents);
    });
  }, [lastMessage, maxEvents]);

  // Auto-scroll to top when new events arrive.
  useEffect(() => {
    const el = containerRef.current;
    if (el) el.scrollTop = 0;
  }, [events.length]);

  const formatTime = (ts: string) => {
    try {
      return new Date(ts).toLocaleTimeString();
    } catch {
      return ts;
    }
  };

  if (events.length === 0) {
    return (
      <div className={`text-xs text-muted-foreground ${className}`}>
        {isConnected ? "等待事件…" : "事件流未连接"}
      </div>
    );
  }

  return (
    <div
      ref={containerRef}
      className={`overflow-y-auto max-h-64 space-y-1 font-mono text-xs ${className}`}
    >
      {events.map((evt) => {
        const meta = EVENT_META[evt.eventType] ?? {
          icon: Activity,
          label: evt.eventType,
          color: "text-muted-foreground",
        };
        const Icon = meta.icon;

        return (
          <div
            key={evt.eventId || `${evt.timestamp}-${evt.eventType}-${evt.orderId ?? evt.code ?? ''}`}
            className="flex items-center gap-1.5 py-0.5 border-b border-border/30"
          >
            <Icon size={12} className={`shrink-0 ${meta.color}`} />
            <span className={`shrink-0 font-semibold ${meta.color}`}>
              {meta.label}
            </span>
            {evt.code && (
              <span className="text-foreground font-semibold">{evt.code}</span>
            )}
            {evt.side && (
              <span
                className={
                  evt.side === "buy" ? "text-red-500" : "text-green-500"
                }
              >
                {evt.side === "buy" ? "B" : "S"}
              </span>
            )}
            {evt.price != null && (
              <span className="text-foreground">{evt.price.toFixed(2)}</span>
            )}
            {evt.quantity != null && (
              <span className="text-muted-foreground">×{evt.quantity}</span>
            )}
            {evt.strategyName && (
              <span className="text-muted-foreground truncate">
                [{evt.strategyName}]
              </span>
            )}
            <span className="ml-auto text-muted-foreground shrink-0">
              {formatTime(evt.timestamp)}
            </span>
          </div>
        );
      })}
    </div>
  );
}

export default EventLogFeed;
