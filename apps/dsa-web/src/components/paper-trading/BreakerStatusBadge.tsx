import { useEffect, useState } from "react";
import { AlertTriangle, AlertCircle, AlertOctagon, ShieldCheck } from "lucide-react";
import { paperTradingApi } from "../../api/paperTrading";
import { requestQueue } from "../../utils/requestQueue";
import { Badge } from "../common/Badge";
import type { BreakerStatusResponse } from "../../types/paperTrading";

const LEVEL_META: Record<
  BreakerStatusResponse["level"],
  { icon: typeof ShieldCheck; label: string; variant: "success" | "warning" | "danger" }
> = {
  normal: { icon: ShieldCheck, label: "正常", variant: "success" },
  soft: { icon: AlertTriangle, label: "软熔断", variant: "warning" },
  hard: { icon: AlertOctagon, label: "硬熔断", variant: "danger" },
  liquidate: { icon: AlertCircle, label: "强制平仓", variant: "danger" },
};

interface Props {
  accountId: number;
  className?: string;
}

export function BreakerStatusBadge({ accountId, className = "" }: Props) {
  const [status, setStatus] = useState<BreakerStatusResponse | null>(null);
  const [error, setError] = useState(false);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    const cancelled = { current: false };
    const fetch = () => {
      requestQueue
        .enqueue(() => paperTradingApi.getBreakerStatus(accountId))
        .then((s) => { if (!cancelled.current) { setStatus(s); setError(false); } })
        .catch(() => { if (!cancelled.current) setError(true); })
        .finally(() => { if (!cancelled.current) setLoading(false); });
    };
    fetch();
    const timer = setInterval(fetch, 30_000);
    return () => { cancelled.current = true; clearInterval(timer); };
  }, [accountId]);

  if (loading && !status) {
    return <span className={`text-xs text-muted-foreground ${className}`}>熔断检测中…</span>;
  }

  if (error && !status) {
    return <span className={`text-xs text-muted-foreground ${className}`}>熔断状态不可用</span>;
  }

  if (!status) return null;

  const meta = LEVEL_META[status.level] ?? LEVEL_META.normal;
  const Icon = meta.icon;

  return (
    <div className={`inline-flex items-center gap-2 ${className}`}>
      <Icon
        size={16}
        className={
          meta.variant === "danger"
            ? "text-red-500"
            : meta.variant === "warning"
            ? "text-amber-500"
            : "text-green-500"
        }
      />
      <Badge variant={meta.variant}>{meta.label}</Badge>
      {status.level !== "normal" && status.triggeredAt && (
        <span className="text-xs text-muted-foreground">
          {new Date(status.triggeredAt).toLocaleTimeString()}
        </span>
      )}
      {!status.canTrade && (
        <span className="text-xs text-red-500 font-semibold">交易停止</span>
      )}
      {status.level === "soft" && !status.canOpenNew && (
        <span className="text-xs text-amber-500">禁止开仓</span>
      )}
    </div>
  );
}
