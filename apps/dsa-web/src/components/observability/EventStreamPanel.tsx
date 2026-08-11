import React, { useCallback, useEffect, useState } from 'react';
import { observabilityApi } from '../../api/observability';
import { useWebSocket } from '../../hooks/useWebSocket';
import { Badge, Card, EmptyState, InlineAlert } from '../common';
import type { EventStreamMessage, SystemEvent, SystemEventType } from '../../types/observability';

// ============ Severity badge color ============

function severityVariant(severity: string): 'default' | 'success' | 'warning' | 'danger' {
  switch (severity) {
    case 'critical':
    case 'error':
      return 'danger';
    case 'warning':
      return 'warning';
    case 'info':
      return 'default';
    default:
      return 'success';
  }
}

const TYPE_LABELS: Partial<Record<SystemEventType, string>> = {
  pipeline_started: '管线启动',
  pipeline_completed: '管线完成',
  pipeline_failed: '管线失败',
  data_source_fallback: '数据源降级',
  data_fetch_failed: '取数失败',
  config_changed: '配置变更',
  config_regression_detected: '配置回归',
  module_restarted: '模块重启',
  module_restart_failed: '模块重启失败',
  degradation_transition: '降级切换',
  agent_tool_call: 'Agent 工具调用',
  agent_tool_result: 'Agent 工具结果',
  notification_sent: '通知已发送',
  notification_failed: '通知失败',
  health_check_completed: '健康检查',
  reflection_completed: '反思完成',
  bias_detected: '检测到偏差',
  no_trade_decision: '无交易决策',
  system_startup: '系统启动',
  system_shutdown: '系统关闭',
};

function typeLabel(et: string): string {
  return TYPE_LABELS[et as SystemEventType] ?? et;
}

// ============ Component ============

interface EventStreamPanelProps {
  initialLimit?: number;
}

/**
 * 实时事件流面板（L1/L2/L3/L4 统一）。
 * WS 实时推送 + REST 分页历史浏览；WS 不可用时降级为 REST 轮询。
 */
export const EventStreamPanel: React.FC<EventStreamPanelProps> = ({
  initialLimit = 20,
}) => {
  const [events, setEvents] = useState<SystemEvent[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [pageSize] = useState(initialLimit);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [filterType, setFilterType] = useState<string>('');
  const [pollTimer, setPollTimer] = useState<number | null>(null);

  // WS 实时流
  const wsUrl = '/api/v1/observability/ws/events';
  const { isConnected: wsConnected, lastMessage } = useWebSocket<EventStreamMessage>({
    url: wsUrl,
    enabled: true,
  });

  // 从 WS 消息提取事件，去重后合并到顶部
  useEffect(() => {
    if (lastMessage && Array.isArray(lastMessage.events)) {
      const incoming = lastMessage.events as SystemEvent[];
      if (incoming.length > 0) {
        setEvents((prev) => {
          const known = new Set(prev.map((e) => e.event_id));
          const fresh = incoming.filter((e) => !known.has(e.event_id));
          return [...fresh, ...prev].slice(0, 200); // 保留最近 200 条
        });
        setTotal((t) => t + incoming.length);
      }
    }
  }, [lastMessage]);

  // REST 分页历史（WS 断开时轮询）
  const loadHistory = useCallback(async (targetPage: number) => {
    setLoading(true);
    setError(null);
    try {
      const resp = await observabilityApi.getEvents({
        page: targetPage,
        page_size: pageSize,
        ...(filterType ? { event_type: filterType } : {}),
      });
      setEvents(resp.items);
      setTotal(resp.total);
      setPage(resp.page);
    } catch (e) {
      setError(e instanceof Error ? e.message : '事件加载失败');
    } finally {
      setLoading(false);
    }
  }, [pageSize, filterType]);

  useEffect(() => {
    if (!wsConnected) {
      void loadHistory(page);
    }
  }, [wsConnected, page, loadHistory]);

  // WS 断开时轮询兜底（5s）
  useEffect(() => {
    if (wsConnected) {
      if (pollTimer) window.clearInterval(pollTimer);
      setPollTimer(null);
      return;
    }
    if (!pollTimer) {
      const t = window.setInterval(() => {
        if (!wsConnected) void loadHistory(1);
      }, 5000);
      setPollTimer(t);
    }
    return () => {
      if (pollTimer) window.clearInterval(pollTimer);
    };
  }, [wsConnected, pollTimer, loadHistory]);

  const totalPages = Math.max(1, Math.ceil(total / pageSize));

  return (
    <Card
      title="系统事件流"
      subtitle={wsConnected ? '实时推送' : '轮询模式（WS 不可用）'}
    >
      {/* 类型过滤 */}
      <div className="mb-3 flex flex-wrap items-center gap-2">
        <select
          className="input-sm rounded-md border border-border bg-surface px-2 py-1 text-sm"
          value={filterType}
          onChange={(e) => {
            setFilterType(e.target.value);
            void loadHistory(1);
          }}
          aria-label="事件类型过滤"
        >
          <option value="">全部类型</option>
          {Object.entries(TYPE_LABELS).map(([value, label]) => (
            <option key={value} value={value}>{label}</option>
          ))}
        </select>
        <span className={`badge ${wsConnected ? 'badge-success' : 'badge-warning'}`}>
          {wsConnected ? 'WS 已连接' : 'WS 断开'}
        </span>
        <span className="ml-auto text-xs text-muted-foreground">共 {total} 条</span>
      </div>

      {error && <InlineAlert variant="danger" title="加载失败" message={error} />}

      {loading && events.length === 0 ? (
        <div className="space-y-2 py-2">
          {[0, 1, 2].map((i) => (
            <div key={i} className="h-10 animate-pulse rounded-md bg-surface" />
          ))}
        </div>
      ) : events.length === 0 ? (
        <EmptyState title="暂无事件" description="系统运行后事件将实时显示在这里" />
      ) : (
        <ul className="max-h-[28rem] divide-y divide-border overflow-y-auto">
          {events.map((e) => (
            <li key={e.event_id} className="flex items-start gap-2 py-2">
              <Badge variant={severityVariant(e.severity)}>{e.severity}</Badge>
              <div className="min-w-0 flex-1">
                <div className="flex items-center gap-2">
                  <span className="text-sm font-medium">{typeLabel(e.event_type)}</span>
                  <span className="text-xs text-muted-foreground">{e.source}</span>
                </div>
                <div className="truncate text-xs text-muted-foreground">
                  {new Date(e.timestamp).toLocaleTimeString()} · {e.event_id}
                </div>
              </div>
            </li>
          ))}
        </ul>
      )}

      {/* 分页 */}
      {totalPages > 1 && (
        <div className="mt-3 flex items-center justify-between">
          <button
            type="button"
            className="btn-secondary"
            disabled={page <= 1}
            onClick={() => void loadHistory(page - 1)}
          >
            上一页
          </button>
          <span className="text-xs text-muted-foreground">{page} / {totalPages}</span>
          <button
            type="button"
            className="btn-secondary"
            disabled={page >= totalPages}
            onClick={() => void loadHistory(page + 1)}
          >
            下一页
          </button>
        </div>
      )}
    </Card>
  );
};
