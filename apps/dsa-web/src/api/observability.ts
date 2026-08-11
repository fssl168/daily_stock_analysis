import apiClient from './index';
import type {
  AdjustmentHistoryResponse,
  EventCorrelationResponse,
  EventListResponse,
  EventStatsResponse,
  HealthTrendResponse,
  IntrospectionResponse,
  MetaObservationsResponse,
  MetaStatsResponse,
  ReflectResponse,
  RegressionsResponse,
  RepairEffectivenessResponse,
  RepairsResponse,
} from '../types/observability';

/**
 * Observability API — L1/L2/L3/L4 passive observation data.
 *
 * 注意：事件 payload 保持 snake_case 原样（不经过 toCamelCase），
 * 因为事件类型/字段本身就是 snake_case，且与后端契约一一对应。
 */
export const observabilityApi = {
  /** 最近事件（分页/过滤） */
  getEvents: async (params?: {
    event_type?: string;
    source?: string;
    min_severity?: string;
    page?: number;
    page_size?: number;
  }): Promise<EventListResponse> => {
    const { data } = await apiClient.get('/observability/events', { params });
    return data as EventListResponse;
  },

  /** 事件统计（类型/来源/严重度分布） */
  getEventStats: async (): Promise<EventStatsResponse> => {
    const { data } = await apiClient.get('/observability/events/stats');
    return data as EventStatsResponse;
  },

  /** 按 correlation_id 追踪事件链 */
  getEventCorrelation: async (cid: string): Promise<EventCorrelationResponse> => {
    const { data } = await apiClient.get(`/observability/events/correlation/${encodeURIComponent(cid)}`);
    return data as EventCorrelationResponse;
  },

  /** L4 系统观察历史 */
  getMetaObservations: async (params?: { limit?: number; observation_type?: string }): Promise<MetaObservationsResponse> => {
    const { data } = await apiClient.get('/observability/meta/observations', { params });
    return data as MetaObservationsResponse;
  },

  /** 最新内省报告 */
  getIntrospection: async (): Promise<IntrospectionResponse> => {
    const { data } = await apiClient.get('/observability/meta/introspection');
    return data as IntrospectionResponse;
  },

  /** L4 元认知统计 */
  getMetaStats: async (): Promise<MetaStatsResponse> => {
    const { data } = await apiClient.get('/observability/meta/stats');
    return data as MetaStatsResponse;
  },

  /** 触发一次反思（dry_run，仅产出报告） */
  triggerReflect: async (): Promise<ReflectResponse> => {
    const { data } = await apiClient.post('/observability/meta/reflect');
    return data as ReflectResponse;
  },

  /** 调整历史（L4 干预） */
  getAdjustmentHistory: async (): Promise<AdjustmentHistoryResponse> => {
    const { data } = await apiClient.get('/observability/adjustments');
    return data as AdjustmentHistoryResponse;
  },

  /** 应用一条调整提案（人工确认） */
  applyAdjustment: async (paramName: string, paramValue: unknown, reason?: string): Promise<{ ok: boolean; param_name: string }> => {
    const { data } = await apiClient.post('/observability/adjustments/apply', {
      param_name: paramName,
      param_value: paramValue,
      reason,
    });
    return data as { ok: boolean; param_name: string };
  },

  /** 拒绝一条调整提案（人工确认） */
  rejectAdjustment: async (paramName: string, paramValue?: unknown): Promise<{ ok: boolean; param_name: string }> => {
    const { data } = await apiClient.post('/observability/adjustments/reject', {
      param_name: paramName,
      param_value: paramValue,
    });
    return data as { ok: boolean; param_name: string };
  },

  /** 修复记录列表 */
  getRepairs: async (params?: { target?: string; action_type?: string; limit?: number }): Promise<RepairsResponse> => {
    const { data } = await apiClient.get('/observability/repairs', { params });
    return data as RepairsResponse;
  },

  /** 修复效果分析报告 */
  getRepairEffectiveness: async (params?: { window_hours?: number }): Promise<RepairEffectivenessResponse> => {
    const { data } = await apiClient.get('/observability/repairs/effectiveness', { params });
    return data as RepairEffectivenessResponse;
  },

  /** 配置回归观察记录 */
  getRegressions: async (): Promise<RegressionsResponse> => {
    const { data } = await apiClient.get('/observability/regressions');
    return data as RegressionsResponse;
  },

  /** 健康检查历史趋势 */
  getHealthTrend: async (params?: { limit?: number }): Promise<HealthTrendResponse> => {
    const { data } = await apiClient.get('/observability/health/trend', { params });
    return data as HealthTrendResponse;
  },
};
