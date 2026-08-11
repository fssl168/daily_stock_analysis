import React, { useCallback, useEffect, useState } from 'react';
import { observabilityApi } from '../../api/observability';
import { Card, EmptyState, InlineAlert } from '../common';
import type {
  EventStatsResponse,
  HealthTrendResponse,
  IntrospectionResponse,
  MetaObservationsResponse,
  ReflectResponse,
  RegressionsResponse,
  RepairEffectivenessResponse,
} from '../../types/observability';

// ============ 通用加载骨架 ============

function Skeleton({ lines = 3 }: { lines?: number }) {
  return (
    <div className="space-y-2 py-1">
      {Array.from({ length: lines }).map((_, i) => (
        <div key={i} className="h-8 animate-pulse rounded-md bg-surface" />
      ))}
    </div>
  );
}

// ============ EventStatsOverview ============

/** 事件类型/来源/严重度分布概览。 */
export const EventStatsOverview: React.FC = () => {
  const [stats, setStats] = useState<EventStatsResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      setStats(await observabilityApi.getEventStats());
    } catch (e) {
      setError(e instanceof Error ? e.message : '统计加载失败');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { void load(); }, [load]);

  const typeDist = stats?.bus?.type_distribution ?? {};
  const topTypes = Object.entries(typeDist).sort((a, b) => b[1] - a[1]).slice(0, 8);

  return (
    <Card title="事件统计" subtitle="L1/L2/L3/L4 事件分布">
      {error && <InlineAlert variant="danger" title="加载失败" message={error} />}
      {loading && !stats ? <Skeleton lines={4} /> : (
        stats ? (
          <div className="space-y-2">
            <div className="grid grid-cols-3 gap-2 text-center">
              <div className="rounded-xl bg-elevated p-2">
                <div className="text-lg font-semibold">{stats.bus?.total_events ?? 0}</div>
                <div className="text-xs text-muted-foreground">事件总数</div>
              </div>
              <div className="rounded-xl bg-elevated p-2">
                <div className="text-lg font-semibold">{Object.keys(typeDist).length}</div>
                <div className="text-xs text-muted-foreground">事件类型</div>
              </div>
              <div className="rounded-xl bg-elevated p-2">
                <div className="text-lg font-semibold">{stats.l3_config_observer?.regression_events ?? 0}</div>
                <div className="text-xs text-muted-foreground">配置回归</div>
              </div>
            </div>
            <div className="mt-3">
              <div className="mb-1 text-xs font-medium text-muted-foreground">TOP 事件类型</div>
              <ul className="space-y-1">
                {topTypes.map(([type, count]) => (
                  <li key={type} className="flex items-center justify-between text-sm">
                    <span className="truncate">{type}</span>
                    <span className="ml-2 text-xs text-muted-foreground">{count}</span>
                  </li>
                ))}
              </ul>
            </div>
          </div>
        ) : <EmptyState title="暂无统计" />
      )}
    </Card>
  );
};

// ============ MetaIntrospectionPanel ============

/** 最新内省报告 + 触发反思（dry_run）。 */
export const MetaIntrospectionPanel: React.FC = () => {
  const [report, setReport] = useState<IntrospectionResponse['report']>(null);
  const [loading, setLoading] = useState(true);
  const [reflecting, setReflecting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const resp = await observabilityApi.getIntrospection();
      setReport(resp.report);
    } catch (e) {
      setError(e instanceof Error ? e.message : '内省报告加载失败');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { void load(); }, [load]);

  const triggerReflect = async () => {
    if (reflecting) return;
    setReflecting(true);
    setError(null);
    try {
      const resp: ReflectResponse = await observabilityApi.triggerReflect();
      setReport(resp.report);
    } catch (e) {
      setError(e instanceof Error ? e.message : '反思触发失败');
    } finally {
      setReflecting(false);
    }
  };

  return (
    <Card title="L4 内省报告" subtitle="元认知 · dry-run 观察模式">
      <div className="mb-2 flex items-center justify-between">
        <button
          type="button"
          className="btn-primary"
          onClick={() => void triggerReflect()}
          disabled={reflecting}
        >
          {reflecting ? '反思中...' : '触发反思'}
        </button>
        <span className="text-xs text-muted-foreground">仅产出报告，不干预策略</span>
      </div>
      {error && <InlineAlert variant="danger" title="操作失败" message={error} />}
      {loading && !report ? <Skeleton lines={5} /> : (
        report ? (
          <div className="max-h-64 space-y-2 overflow-y-auto rounded-xl bg-elevated p-3">
            {report.summary ? <p className="text-sm font-medium">{report.summary}</p> : null}
            {report.timestamp ? (
              <p className="text-xs text-muted-foreground">
                生成于 {new Date(report.timestamp).toLocaleString()}
              </p>
            ) : null}
            {report.report_id ? (
              <p className="text-xs text-muted-foreground">ID: {report.report_id}</p>
            ) : null}
            {report.bias_findings ? (
              <pre className="mt-2 whitespace-pre-wrap text-xs">
                {typeof report.bias_findings === 'string'
                  ? report.bias_findings
                  : JSON.stringify(report.bias_findings, null, 2)}
              </pre>
            ) : null}
          </div>
        ) : <EmptyState title="尚无内省报告" description="点击「触发反思」生成第一份报告" />
      )}
    </Card>
  );
};

// ============ MetaObservationsPanel ============

/** L4 系统观察历史。 */
export const MetaObservationsPanel: React.FC = () => {
  const [data, setData] = useState<MetaObservationsResponse>({ items: [], count: 0 });
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    observabilityApi.getMetaObservations({ limit: 50 })
      .then(setData)
      .catch((e) => setError(e instanceof Error ? e.message : '观察加载失败'))
      .finally(() => setLoading(false));
  }, []);

  return (
    <Card title="L4 系统观察" subtitle={`${data.count} 条记录`}>
      {error && <InlineAlert variant="danger" title="加载失败" message={error} />}
      {loading ? <Skeleton lines={4} /> : data.items.length === 0 ? (
        <EmptyState title="暂无系统观察" description="降级/回滚/重启等事件会记录在这里" />
      ) : (
        <ul className="max-h-56 space-y-1.5 overflow-y-auto">
          {data.items.map((obs, i) => (
            <li key={`${obs.timestamp}-${i}`} className="rounded-lg bg-elevated p-2 text-xs">
              <span className="font-medium text-cyan">{obs.type}</span>
              <span className="ml-2 text-muted-foreground">
                {new Date(obs.timestamp).toLocaleString()}
              </span>
            </li>
          ))}
        </ul>
      )}
    </Card>
  );
};

// ============ RepairEffectivenessPanel ============

/** 修复记录 + 效果分析。 */
export const RepairEffectivenessPanel: React.FC = () => {
  const [report, setReport] = useState<RepairEffectivenessResponse['report']>({});
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    observabilityApi.getRepairEffectiveness({ window_hours: 24 })
      .then((r) => setReport(r.report))
      .catch((e) => setError(e instanceof Error ? e.message : '修复效果加载失败'))
      .finally(() => setLoading(false));
  }, []);

  return (
    <Card title="L3 修复效果" subtitle="24h 窗口分析">
      {error && <InlineAlert variant="danger" title="加载失败" message={error} />}
      {loading ? <Skeleton lines={4} /> : (
        Object.keys(report).length === 0 ? (
          <EmptyState title="暂无修复记录" description="自动修复动作的结果会展示在这里" />
        ) : (
          <pre className="max-h-56 overflow-y-auto whitespace-pre-wrap rounded-xl bg-elevated p-3 text-xs">
            {JSON.stringify(report, null, 2)}
          </pre>
        )
      )}
    </Card>
  );
};

// ============ RegressionPanel ============

/** 配置回归观察记录。 */
export const RegressionPanel: React.FC = () => {
  const [items, setItems] = useState<RegressionsResponse['items']>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    observabilityApi.getRegressions()
      .then((r) => setItems(r.items))
      .catch((e) => setError(e instanceof Error ? e.message : '回归记录加载失败'))
      .finally(() => setLoading(false));
  }, []);

  return (
    <Card title="配置回归" subtitle="observe-only · 不自动回滚">
      {error && <InlineAlert variant="danger" title="加载失败" message={error} />}
      {loading ? <Skeleton lines={3} /> : items.length === 0 ? (
        <EmptyState title="无配置回归" />
      ) : (
        <ul className="max-h-48 space-y-1.5 overflow-y-auto">
          {items.map((it, i) => (
            <li key={`${it.timestamp}-${i}`} className="rounded-lg bg-elevated p-2 text-xs">
              <div className="flex items-center justify-between">
                <span className="font-medium text-warning">{it.severity}</span>
                <span className="text-muted-foreground">
                  {it.timestamp ? new Date(it.timestamp).toLocaleString() : ''}
                </span>
              </div>
              {it.signals ? <div className="mt-1 text-muted-foreground">{it.signals.join(', ')}</div> : null}
            </li>
          ))}
        </ul>
      )}
    </Card>
  );
};

// ============ HealthTrendPanel ============

/** 健康检查历史趋势（Sparkline 简化版）。 */
export const HealthTrendPanel: React.FC = () => {
  const [data, setData] = useState<HealthTrendResponse>({ items: [], count: 0 });
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    observabilityApi.getHealthTrend({ limit: 100 })
      .then(setData)
      .catch((e) => setError(e instanceof Error ? e.message : '健康趋势加载失败'))
      .finally(() => setLoading(false));
  }, []);

  const unhealthy = data.items.filter((i) => i.unhealthy_count > 0).length;
  const maxUnhealthy = Math.max(1, ...data.items.map((i) => i.unhealthy_count));

  return (
    <Card title="健康趋势" subtitle={`${data.count} 次检查 · ${unhealthy} 次异常`}>
      {error && <InlineAlert variant="danger" title="加载失败" message={error} />}
      {loading ? <Skeleton lines={3} /> : data.items.length === 0 ? (
        <EmptyState title="无健康数据" />
      ) : (
        <div className="space-y-2">
          <div className="flex h-16 items-end gap-0.5 overflow-hidden">
            {data.items.slice(-60).map((item, i) => (
              <div
                key={i}
                className="flex-1 rounded-t"
                style={{
                  height: `${Math.max(8, (item.unhealthy_count / maxUnhealthy) * 100)}%`,
                  backgroundColor: item.unhealthy_count > 0 ? 'var(--color-danger, #ef4444)' : 'var(--color-success, #22c55e)',
                  opacity: 0.8,
                }}
                title={`${item.timestamp} unhealthy=${item.unhealthy_count}`}
              />
            ))}
          </div>
          <div className="flex items-center justify-between text-xs text-muted-foreground">
            <span>最近 60 次检查</span>
            <span>红色=异常</span>
          </div>
        </div>
      )}
    </Card>
  );
};
