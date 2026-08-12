/**
 * Risk alert toast notification for real-time risk events.
 *
 * Displays VaR breaches, liquidity warnings, and market anomalies
 * as dismissible toasts that auto-expire after 8 seconds.
 */

import { useEffect, useState, useCallback } from "react";
import { AlertTriangle, TrendingDown, Droplets, X } from "lucide-react";
import { useWebSocket } from "../../hooks/useWebSocket";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export interface RiskAlertEvent {
  alertType: "var_breach" | "liquidity_warning" | "market_anomaly";
  message: string;
  detail?: string;
  level: "warning" | "danger";
  timestamp: string;
}

interface RiskAlertToastItem {
  id: number;
  event: RiskAlertEvent;
  createdAt: number;
}

interface Props {
  accountId: number;
  maxVisible?: number;
}

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

let toastIdCounter = 0;

export function RiskAlertToast({ accountId, maxVisible = 3 }: Props) {
  const [toasts, setToasts] = useState<RiskAlertToastItem[]>([]);

  // Subscribe to WS event stream.
  const wsUrl = `/api/v1/paper-trading/${accountId}/ws/events`;
  const { lastMessage } = useWebSocket<RiskAlertEvent>({
    url: wsUrl,
    enabled: true,
    autoReconnect: true,
    maxRetries: 5,
  });

  // On WS event → push a toast.
  useEffect(() => {
    if (!lastMessage || typeof lastMessage !== "object") return;
    const evt = lastMessage as unknown as Record<string, unknown>;
    if (!evt.alertType || evt.alertType === "none") return;

    const alert: RiskAlertEvent = {
      alertType: evt.alertType as RiskAlertEvent["alertType"],
      message: String(evt.message ?? ""),
      detail: String(evt.detail ?? ""),
      level: (evt.level as "warning" | "danger") ?? "warning",
      timestamp: String(evt.timestamp ?? new Date().toISOString()),
    };

    const id = ++toastIdCounter;
    setToasts((prev) => {
      const next = [{ id, event: alert, createdAt: Date.now() }, ...prev];
      return next.slice(0, maxVisible);
    });
  }, [lastMessage, maxVisible]);

  // Auto-dismiss after 8s.
  const dismiss = useCallback((id: number) => {
    setToasts((prev) => prev.filter((t) => t.id !== id));
  }, []);

  useEffect(() => {
    if (toasts.length === 0) return;
    const timer = setInterval(() => {
      const now = Date.now();
      setToasts((prev) => prev.filter((t) => now - t.createdAt < 8_000));
    }, 1_000);
    return () => clearInterval(timer);
  }, [toasts.length]);

  if (toasts.length === 0) return null;

  const iconMap: Record<string, typeof AlertTriangle> = {
    var_breach: TrendingDown,
    liquidity_warning: Droplets,
    market_anomaly: AlertTriangle,
  };

  return (
    <div className="fixed bottom-4 right-4 z-50 flex flex-col gap-2 max-w-sm">
      {toasts.map((t) => {
        const Icon = iconMap[t.event.alertType] ?? AlertTriangle;
        const bg =
          t.event.level === "danger"
            ? "bg-red-50 border-red-300 dark:bg-red-950 dark:border-red-700"
            : "bg-amber-50 border-amber-300 dark:bg-amber-950 dark:border-amber-700";
        const text =
          t.event.level === "danger"
            ? "text-red-800 dark:text-red-200"
            : "text-amber-800 dark:text-amber-200";

        return (
          <div
            key={t.id}
            className={`flex items-start gap-2 p-3 rounded-lg border shadow-lg text-sm ${bg} ${text} animate-in slide-in-from-right`}
          >
            <Icon size={16} className="shrink-0 mt-0.5" />
            <div className="flex-1 min-w-0">
              <p className="font-semibold truncate">{t.event.message}</p>
              {t.event.detail && (
                <p className="text-xs opacity-75 mt-0.5">{t.event.detail}</p>
              )}
            </div>
            <button
              onClick={() => dismiss(t.id)}
              className="shrink-0 p-0.5 rounded hover:bg-black/10 dark:hover:bg-white/10"
              aria-label="关闭"
            >
              <X size={14} />
            </button>
          </div>
        );
      })}
    </div>
  );
}

export default RiskAlertToast;
