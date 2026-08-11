import React from 'react';
import { PageHeader } from '../components/common';
import {
  EventStatsOverview,
  EventStreamPanel,
  HealthTrendPanel,
  MetaIntrospectionPanel,
  MetaObservationsPanel,
  RegressionPanel,
  RepairEffectivenessPanel,
} from '../components/observability';

/**
 * 可观测性面板 — L1/L2/L3/L4 全主动观察数据前端。
 *
 * 聚合 7 个组件：
 * - EventStreamPanel（实时事件流，WS + REST 降级）
 * - EventStatsOverview（事件统计）
 * - MetaIntrospectionPanel（L4 内省 + 触发反思）
 * - MetaObservationsPanel（L4 系统观察）
 * - RepairEffectivenessPanel（L3 修复效果）
 * - RegressionPanel（配置回归）
 * - HealthTrendPanel（健康趋势）
 */
const ObservabilityPage: React.FC = () => {
  return (
    <div className="mx-auto max-w-7xl space-y-4 px-4 py-6">
      <PageHeader
        title="系统可观测性"
        description="L1/L2/L3/L4 全主动观察 · 事件流 / 元认知 / 修复效果 / 健康趋势"
      />

      {/* 顶部：事件流 + 统计 */}
      <div className="grid gap-4 lg:grid-cols-3">
        <div className="lg:col-span-2">
          <EventStreamPanel />
        </div>
        <div className="space-y-4">
          <EventStatsOverview />
          <HealthTrendPanel />
        </div>
      </div>

      {/* 中部：L4 元认知 */}
      <div className="grid gap-4 lg:grid-cols-2">
        <MetaIntrospectionPanel />
        <MetaObservationsPanel />
      </div>

      {/* 底部：L3 修复 */}
      <div className="grid gap-4 lg:grid-cols-2">
        <RepairEffectivenessPanel />
        <RegressionPanel />
      </div>
    </div>
  );
};

export default ObservabilityPage;
