import { useEffect, useState } from "react";
import { Activity } from "lucide-react";
import apiClient from "../../api";
import { Card } from "../common/Card";
import { Badge } from "../common/Badge";

interface HealthResponse {
  status: string;
  version?: string;
  uptime?: number;
}

export function HealthDashboard({ className = "" }: { className?: string }) {
  const [health, setHealth] = useState<HealthResponse | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;
    const fetch = () => {
      apiClient
        .get<HealthResponse>("/api/v1/health")
        .then((r) => { if (!cancelled) setHealth(r.data); })
        .catch(() => {})
        .finally(() => { if (!cancelled) setLoading(false); });
    };
    fetch();
    const t = setInterval(fetch, 60_000);
    return () => { cancelled = true; clearInterval(t); };
  }, []);

  if (loading) return null;

  const ok = health?.status === "ok";

  return (
    <Card className={`p-3 ${className}`}>
      <div className="flex items-center gap-2 text-sm">
        <Activity size={16} className={ok ? "text-green-500" : "text-red-500"} />
        <span className="font-medium">系统状态</span>
        <Badge variant={ok ? "success" : "danger"}>
          {ok ? "正常" : health?.status ?? "未知"}
        </Badge>
      </div>
    </Card>
  );
}
