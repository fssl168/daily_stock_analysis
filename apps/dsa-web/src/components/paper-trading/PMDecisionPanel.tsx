import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { paperTradingApi } from '../../api/paperTrading';
import { Badge, Card, EmptyState, InlineAlert, Loading } from '../common';
import { useUiLanguage } from '../../contexts/UiLanguageContext';
import type { AccountListItem, PMDecisionItem } from '../../types/paperTrading';

function formatDateTime(value?: string | null, language?: string): string {
  if (!value) return '--';
  try {
    return new Date(value).toLocaleString(language === 'en' ? 'en-US' : 'zh-CN', {
      month: 'short',
      day: 'numeric',
      hour: '2-digit',
      minute: '2-digit',
    });
  } catch {
    return value;
  }
}

function statusBadgeVariant(status: string): React.ComponentProps<typeof Badge>['variant'] {
  switch (status) {
    case 'executed':
    case 'completed':
      return 'success';
    case 'rejected':
    case 'skipped':
      return 'danger';
    case 'pending':
      return 'warning';
    default:
      return 'default';
  }
}

interface PMDecisionPanelProps {
  stockCode: string;
}

export const PMDecisionPanel: React.FC<PMDecisionPanelProps> = ({ stockCode }) => {
  const { t, language } = useUiLanguage();
  const [accounts, setAccounts] = useState<AccountListItem[]>([]);
  const [decisions, setDecisions] = useState<PMDecisionItem[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const accountId = useMemo(() => {
    const active = accounts.find((a) => a.status === 'active');
    return active?.accountId ?? accounts[0]?.accountId ?? 1;
  }, [accounts]);

  const load = useCallback(async () => {
    if (!stockCode) return;
    setLoading(true);
    setError(null);
    try {
      const [accountsRes, decisionsRes] = await Promise.all([
        paperTradingApi.getAccounts().catch(() => ({ accounts: [], total: 0 })),
        paperTradingApi.listPMDecisions(accountId, { limit: 200 }),
      ]);
      setAccounts(accountsRes.accounts || []);
      const code = stockCode.toLowerCase();
      const filtered = (decisionsRes.items || []).filter(
        (d) => d.code?.toLowerCase() === code
      );
      setDecisions(filtered);
    } catch (err) {
      setError(err instanceof Error ? err.message : t('report.pmDecisions.loadError'));
    } finally {
      setLoading(false);
    }
  }, [accountId, stockCode, t]);

  useEffect(() => {
    load();
  }, [load]);

  const latest = decisions[0];

  return (
    <Card variant="gradient" padding="md">
      <div className="flex items-center justify-between">
        <span className="label-uppercase">{t('report.pmDecisions.title')}</span>
        {latest && (
          <Badge variant={statusBadgeVariant(latest.status)}>{latest.status}</Badge>
        )}
      </div>

      {loading && (
        <div className="mt-4">
          <Loading />
        </div>
      )}

      {error && !loading && (
        <InlineAlert variant="danger" message={error} className="mt-3" />
      )}

      {!loading && !error && decisions.length === 0 && (
        <div className="mt-3">
          <EmptyState title={t('report.pmDecisions.empty')} />
        </div>
      )}

      {latest && !loading && (
        <div className="mt-3 space-y-3">
          <div className="grid grid-cols-2 gap-3">
            <div>
              <p className="text-xxs text-muted uppercase">{t('report.pmDecisions.latestAction')}</p>
              <p className="text-sm font-mono font-semibold text-white">{latest.action.toUpperCase()}</p>
            </div>
            <div>
              <p className="text-xxs text-muted uppercase">{t('report.pmDecisions.confidence')}</p>
              <p className="text-sm font-mono font-semibold text-white">{(latest.confidence * 100).toFixed(1)}%</p>
            </div>
          </div>

          <div>
            <p className="text-xxs text-muted uppercase">{t('report.pmDecisions.reason')}</p>
            <p className="text-xs text-secondary leading-relaxed">{latest.reason || '--'}</p>
          </div>

          <div className="pt-3 border-t border-white/5 flex items-center justify-between text-xs text-muted">
            <span>{formatDateTime(latest.createdAt, language)}</span>
            <span>{t('report.pmDecisions.account')}: #{accountId}</span>
          </div>
        </div>
      )}
    </Card>
  );
};
