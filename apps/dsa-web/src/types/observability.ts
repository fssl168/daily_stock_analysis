/**
 * Observability API type definitions
 * Mirrors api/v1/endpoints/observability.py (L1/L2/L3/L4 passive observation)
 */

// ============ System Events ============

export type SystemEventType =
  // L3 module restart
  | 'module_restarted'
  | 'module_restart_failed'
  | 'module_health_changed'
  // L3 config rollback
  | 'config_snapshot_created'
  | 'config_rollback_executed'
  | 'config_regression_detected'
  // L3 graceful degradation
  | 'degradation_transition'
  | 'capability_disabled'
  | 'capability_restored'
  // L4 meta-cognition
  | 'reflection_completed'
  | 'bias_detected'
  | 'circularity_detected'
  | 'outcome_deviation'
  // system
  | 'system_startup'
  | 'system_shutdown'
  | 'health_check_completed'
  // L1 infrastructure
  | 'data_source_fallback'
  | 'data_fetch_failed'
  | 'data_quality_alert'
  | 'circuit_open'
  | 'circuit_closed'
  | 'config_changed'
  | 'clock_degraded'
  | 'latency_summary'
  | 'llm_backend_switched'
  | 'llm_usage'
  | 'storage_error'
  // L2 business execution
  | 'pipeline_started'
  | 'pipeline_completed'
  | 'pipeline_failed'
  | 'market_review_completed'
  | 'backtest_started'
  | 'backtest_completed'
  | 'agent_tool_call'
  | 'agent_tool_result'
  | 'agent_loop_detected'
  | 'no_trade_decision'
  | 'notification_sent'
  | 'notification_failed'
  | 'service_error';

export type EventSeverity = 'debug' | 'info' | 'warning' | 'error' | 'critical';

export interface SystemEvent {
  event_id: string;
  event_type: SystemEventType;
  severity: EventSeverity;
  source: string;
  timestamp: string;
  payload_redacted: Record<string, unknown>;
  correlation_id: string | null;
}

export interface EventListResponse {
  items: SystemEvent[];
  total: number;
  page: number;
  page_size: number;
}

export interface EventCorrelationResponse {
  correlation_id: string;
  items: SystemEvent[];
  count: number;
}

// ============ Event Stats ============

export interface EventStatsResponse {
  bus: {
    total_events?: number;
    logged_events?: number;
    subscription_count?: number;
    event_types?: number;
    type_distribution?: Record<string, number>;
    source_distribution?: Record<string, number>;
    severity_distribution?: Record<string, number>;
  };
  l4_meta_observer: {
    total_events_observed?: number;
    event_counts?: Record<string, number>;
    last_seen?: Record<string, string>;
  };
  l3_config_observer: {
    regression_events?: number;
  };
}

// ============ L4 Meta-Cognition ============

export interface IntrospectionReport {
  report_id?: string;
  timestamp?: string;
  summary?: string;
  conclusions?: unknown;
  recommendations?: unknown;
  bias_findings?: unknown;
  [key: string]: unknown;
}

export interface IntrospectionResponse {
  report: IntrospectionReport | null;
}

export interface ReflectResponse {
  ok: boolean;
  report: IntrospectionReport;
  note: string;
}

export interface MetaObservation {
  type: string;
  timestamp: string;
  [key: string]: unknown;
}

export interface MetaObservationsResponse {
  items: MetaObservation[];
  count: number;
}

export interface MetaStatsResponse {
  stats: Record<string, unknown>;
}

// ============ L3 Repair Effectiveness ============

export interface RepairEffectivenessEntry {
  entry_id?: string;
  action_type?: string;
  target?: string;
  timestamp?: string;
  [key: string]: unknown;
}

export interface RepairsResponse {
  items: RepairEffectivenessEntry[];
  count: number;
}

export interface RepairEffectivenessResponse {
  report: Record<string, unknown>;
  window_hours: number;
}

export interface RegressionsResponse {
  items: Array<{
    timestamp?: string;
    snapshot_id?: string;
    signals?: string[];
    severity?: string;
    [key: string]: unknown;
  }>;
  count: number;
}

// ============ Health Trend ============

export interface HealthTrendItem {
  timestamp: string;
  unhealthy_count: number;
  unhealthy_components: string[];
  severity: EventSeverity;
}

export interface HealthTrendResponse {
  items: HealthTrendItem[];
  count: number;
}

// ============ WS ============

export interface EventStreamMessage {
  events: SystemEvent[];
}
