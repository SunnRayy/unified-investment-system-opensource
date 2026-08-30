import React, { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { OperationsAPI } from '../../src/services/api';
import type {
  PipelinePhase,
  PipelineStatusResponse,
  PipelineStepResult,
  SourceFreshness,
} from '../../src/services/api';
import { fmtCNY } from '../../src/utils/formatMoney';

// Contract: docs/api-specs/operations-pipeline.md (Sections B–E)

const fmtDuration = (ms: number): string =>
  ms < 1000 ? `${ms}ms` : `${(ms / 1000).toFixed(1)}s`;

const fmtTimestamp = (iso: string): string => {
  // "2026-06-10T14:03:22" → "2026-06-10 14:03" (naive local — never convert as UTC)
  const m = iso.match(/^(\d{4}-\d{2}-\d{2})[T ](\d{2}:\d{2})/);
  return m ? `${m[1]} ${m[2]}` : iso;
};

const fmtShortDate = (iso: string): string => {
  const m = iso.match(/^(\d{4})-(\d{2})-(\d{2})/);
  if (!m) return iso;
  const months = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
  return `${months[Number(m[2]) - 1]} ${Number(m[3])}`;
};

const integrityTone: Record<string, string> = {
  ok: 'border-green-200 bg-green-50 text-green-700',
  degraded: 'border-yellow-200 bg-yellow-50 text-yellow-700',
  failed: 'border-red-200 bg-red-50 text-red-700',
};

const stalenessTone: Record<SourceFreshness['staleness'], string> = {
  fresh: 'border-green-200 bg-green-50 text-green-700',
  aging: 'border-yellow-200 bg-yellow-50 text-yellow-700',
  stale: 'border-red-200 bg-red-50 text-red-700',
};

const stepDotTone = (step: PipelineStepResult | undefined): string => {
  if (!step) return 'bg-gray-300';
  return step.status === 'ok' ? 'bg-green-500' : 'bg-red-500';
};

const PhaseNode: React.FC<{
  phase: PipelinePhase;
  step: PipelineStepResult | undefined;
  stepsTracked: boolean;
}> = ({ phase, step, stepsTracked }) => {
  const { t } = useTranslation('operations');
  const writesLine = t('pipelinePanel.tooltip.writes', { tables: phase.tables_written.join(', ') || '—' });
  const tooltip = step?.error
    ? `${phase.description}\n${t('pipelinePanel.tooltip.error', { error: step.error })}`
    : stepsTracked
      ? `${phase.description}\n${writesLine}`
      : `${phase.description}\n${writesLine}\n${t('pipelinePanel.tooltip.notTracked')}`;
  return (
    <div className="flex min-w-0 flex-1 flex-col items-center gap-1" title={tooltip}>
      <span className="text-xs font-semibold text-gray-700">{phase.phase_id}</span>
      <span className={`h-2.5 w-2.5 rounded-full ${stepDotTone(step)}`} />
      <span className="font-mono text-[10px] text-gray-500">
        {step ? fmtDuration(step.duration_ms) : '—'}
      </span>
      <span className="max-w-full truncate text-[10px] text-gray-400">{phase.name}</span>
    </div>
  );
};

export const PipelinePanel: React.FC = () => {
  const { t } = useTranslation('operations');
  const [data, setData] = useState<PipelineStatusResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let mounted = true;
    (async () => {
      try {
        const response = await OperationsAPI.getPipelineStatus();
        if (mounted) setData(response);
      } catch (err) {
        console.error(err);
        if (mounted) setError(t('pipelinePanel.loadError'));
      } finally {
        if (mounted) setLoading(false);
      }
    })();
    return () => {
      mounted = false;
    };
  }, [t]);

  if (loading) {
    return (
      <div className="rounded-lg border border-gray-200 bg-white p-6 text-sm text-gray-600 shadow-sm">
        {t('pipelinePanel.loading')}
      </div>
    );
  }
  if (error || !data) {
    return (
      <div className="rounded-lg border border-red-200 bg-red-50 p-4 text-sm text-red-700">
        {error ?? t('pipelinePanel.loadError')}
      </div>
    );
  }

  const { phases, last_run: lastRun, sources } = data;
  const stepsByPhase = new Map<string, PipelineStepResult>(
    (lastRun?.steps ?? []).map((s) => [s.phase_id, s]),
  );
  const stepsTracked = (lastRun?.steps?.length ?? 0) > 0;
  const nwPct = lastRun?.net_worth_change_pct;

  return (
    <div className="space-y-4">
      <div className="rounded-lg border border-gray-200 bg-white shadow-sm">
        <div className="flex flex-wrap items-center justify-between gap-3 border-b border-gray-200 px-6 py-3">
          <h2 className="text-sm font-bold text-gray-900">{t('pipelinePanel.title')}</h2>
          {lastRun ? (
            <div className="flex flex-wrap items-center gap-3 text-sm">
              <span className="text-gray-500">{t('pipelinePanel.lastSync', { timestamp: fmtTimestamp(lastRun.timestamp) })}</span>
              <span className={`rounded-full border px-2.5 py-0.5 text-xs font-semibold ${integrityTone[lastRun.integrity_status]}`}>
                {lastRun.integrity_result} {lastRun.integrity_status}
              </span>
              <span className={`font-mono text-xs ${nwPct == null ? 'text-gray-400' : nwPct >= 0 ? 'text-green-700' : 'text-red-700'}`}>
                {t('pipelinePanel.nwDelta')} {nwPct == null ? '—' : `${nwPct >= 0 ? '+' : ''}${nwPct.toFixed(2)}%`}
              </span>
              {lastRun.warning_count > 0 && (
                <span className="text-xs font-semibold text-yellow-700">⚠ {lastRun.warning_count}</span>
              )}
            </div>
          ) : (
            <span className="text-sm text-gray-500">{t('pipelinePanel.noSyncRecorded')}</span>
          )}
        </div>
        <div className="px-6 py-4">
          <div className="flex items-start gap-1">
            {phases.map((phase, idx) => (
              <React.Fragment key={phase.phase_id}>
                {idx > 0 && <div className="mt-[22px] h-px w-3 shrink-0 bg-gray-300 sm:w-5" />}
                <PhaseNode
                  phase={phase}
                  step={stepsByPhase.get(phase.phase_id)}
                  stepsTracked={stepsTracked}
                />
              </React.Fragment>
            ))}
          </div>
          {lastRun && !stepsTracked && (
            <p className="mt-2 text-xs text-gray-400">{t('pipelinePanel.stepsNotTracked')}</p>
          )}
        </div>
      </div>

      <div className="rounded-lg border border-gray-200 bg-white shadow-sm">
        <div className="border-b border-gray-200 px-6 py-3">
          <h2 className="text-sm font-bold text-gray-900">{t('pipelinePanel.sourceFreshness')}</h2>
        </div>
        <div className="grid grid-cols-1 gap-3 px-6 py-4 sm:grid-cols-2 xl:grid-cols-4">
          {sources.map((source) => (
            <div key={source.source_system} className="rounded-lg border border-gray-200 p-3">
              <div className="flex items-center justify-between gap-2">
                <span className="truncate text-sm font-semibold text-gray-900">{source.display_name}</span>
                <span className={`rounded-full border px-2 py-0.5 text-[10px] font-semibold ${stalenessTone[source.staleness]}`}>
                  {source.staleness}
                </span>
              </div>
              <p className="mt-1 font-mono text-xs text-gray-600">
                {source.latest_snapshot} · {source.snapshot_age_days === 0 ? t('pipelinePanel.today') : t('pipelinePanel.daysAgo', { days: source.snapshot_age_days })}
              </p>
              <p className="mt-1 text-xs text-gray-500">
                {source.active_assets} {source.active_assets === 1 ? t('pipelinePanel.asset') : t('pipelinePanel.assets')} · {fmtCNY(source.total_value_cny)}
              </p>
              <p className="mt-1 text-xs text-gray-400">
                {source.last_price_refresh
                  ? t('pipelinePanel.pricesRefreshed', { refreshed: source.price_refreshed_assets, active: source.active_assets, date: fmtShortDate(source.last_price_refresh) })
                  : t('pipelinePanel.pricesFileValues')}
              </p>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
};
