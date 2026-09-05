import React, { useCallback, useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { SettingsAPI, SourceHealthEntry, SourceHealthResponse } from '../../src/services/api';
import { formatCNY } from '../../src/utils/format';

function relativeTime(t: (key: string, opts?: Record<string, unknown>) => string, isoString: string | null): string {
  if (!isoString) return t('sourceHealthDashboard.never');
  const diff = Date.now() - new Date(isoString).getTime();
  if (diff < 60000) return t('sourceHealthDashboard.justNow');
  if (diff < 3600000) return t('sourceHealthDashboard.minutesAgo', { count: Math.floor(diff / 60000) });
  if (diff < 86400000) return t('sourceHealthDashboard.hoursAgo', { count: Math.floor(diff / 3600000) });
  return t('sourceHealthDashboard.daysAgo', { count: Math.floor(diff / 86400000) });
}

function statusMeta(t: (key: string) => string): Record<
  SourceHealthEntry['status'],
  { label: string; dotClass: string; badgeClass: string }
> {
  return {
    ok: {
      label: t('sourceHealthDashboard.status.ok'),
      dotClass: 'bg-emerald-500',
      badgeClass: 'bg-emerald-50 dark:bg-emerald-900/20 text-emerald-700 dark:text-emerald-400',
    },
    pending_sync: {
      label: t('sourceHealthDashboard.status.pendingSync'),
      dotClass: 'bg-blue-500',
      badgeClass: 'bg-blue-50 dark:bg-blue-900/20 text-blue-700 dark:text-blue-400',
    },
    stale: {
      label: t('sourceHealthDashboard.status.stale'),
      dotClass: 'bg-amber-500',
      badgeClass: 'bg-amber-50 dark:bg-amber-900/20 text-amber-700 dark:text-amber-400',
    },
    missing: {
      label: t('sourceHealthDashboard.status.missing'),
      dotClass: 'bg-red-500',
      badgeClass: 'bg-red-50 dark:bg-red-900/20 text-red-700 dark:text-red-400',
    },
    never_synced: {
      label: t('sourceHealthDashboard.status.neverSynced'),
      dotClass: 'bg-red-500',
      badgeClass: 'bg-red-50 dark:bg-red-900/20 text-red-700 dark:text-red-400',
    },
    unknown: {
      label: t('sourceHealthDashboard.status.unknown'),
      dotClass: 'bg-red-500',
      badgeClass: 'bg-red-50 dark:bg-red-900/20 text-red-700 dark:text-red-400',
    },
  };
}

interface SourceHealthDashboardProps {
  refreshTrigger?: number;
  onReaderClick?: (readerKey: string) => void;
  /** key→label lookup built from the /sources payload; falls back to entry.reader when absent */
  labelLookup?: Record<string, string>;
}

export const SourceHealthDashboard: React.FC<SourceHealthDashboardProps> = ({ refreshTrigger, onReaderClick, labelLookup }) => {
  const { t } = useTranslation('system');
  const [health, setHealth] = useState<SourceHealthResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [collapsed, setCollapsed] = useState(false);

  const loadHealth = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const data = await SettingsAPI.getSourceHealth();
      setHealth(data);
    } catch {
      setError(t('sourceHealthDashboard.loadError'));
    } finally {
      setLoading(false);
    }
  }, [t]);

  useEffect(() => {
    void loadHealth();
    const intervalId = window.setInterval(() => {
      void loadHealth();
    }, 60000);
    return () => window.clearInterval(intervalId);
  }, [loadHealth, refreshTrigger]);

  const attentionCount = health ? health.sources.filter(s => s.status !== 'ok').length : 0;
  const overallStatusText = health
    ? health.all_healthy
      ? t('sourceHealthDashboard.allHealthy', { count: health.sources.length })
      : t('sourceHealthDashboard.needAttention', { count: attentionCount })
    : t('sourceHealthDashboard.checking');
  const overallStatusClass = health?.all_healthy
    ? 'text-emerald-600 dark:text-emerald-400'
    : 'text-amber-600 dark:text-amber-400';

  return (
    <section className="border border-slate-200 dark:border-border-dark rounded-xl bg-white dark:bg-card-dark mb-4">
      <div className="flex items-center gap-3 px-4 py-3">
        <span className="material-symbols-outlined !text-[20px] text-slate-400 dark:text-slate-500">
          monitoring
        </span>
        <div className="flex-1 min-w-0">
          <div className="text-sm font-medium text-slate-800 dark:text-slate-100">{t('sourceHealthDashboard.title')}</div>
          <div className={`text-xs mt-0.5 ${overallStatusClass}`}>{overallStatusText}</div>
        </div>

        <button
          onClick={() => { void loadHealth(); }}
          disabled={loading}
          className="flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium rounded-lg border border-slate-200 dark:border-border-dark text-slate-600 dark:text-slate-400 hover:bg-slate-50 dark:hover:bg-card-dark disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
          aria-label={t('sourceHealthDashboard.refreshAria')}
        >
          {loading ? (
            <span className="material-symbols-outlined !text-[14px] animate-spin">progress_activity</span>
          ) : (
            <span className="material-symbols-outlined !text-[14px]">refresh</span>
          )}
          {loading ? t('sourceHealthDashboard.refreshingEllipsis') : t('sourceHealthDashboard.refresh')}
        </button>

        <button
          onClick={() => setCollapsed(prev => !prev)}
          className="p-1.5 rounded-md text-slate-500 hover:bg-slate-100 dark:hover:bg-surface-dark transition-colors"
          aria-label={collapsed ? t('sourceHealthDashboard.expandAria') : t('sourceHealthDashboard.collapseAria')}
        >
          <span className={`material-symbols-outlined !text-[18px] transition-transform ${collapsed ? '' : 'rotate-180'}`}>
            expand_more
          </span>
        </button>
      </div>

      {error && (
        <div className="px-4 pb-3">
          <div className="flex items-center gap-1.5 px-3 py-2 rounded-lg text-xs bg-red-50 dark:bg-red-900/20 border border-red-200 dark:border-red-800 text-red-700 dark:text-red-400">
            <span className="material-symbols-outlined !text-[14px]">error</span>
            {error}
          </div>
        </div>
      )}

      {!collapsed && (
        <div className="px-4 pb-4">
          {health && health.sources.length > 0 ? (
            <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
              {health.sources.map(entry => {
                // Label from caller-supplied lookup (built from /sources payload); fallback to reader key
                const label = labelLookup?.[entry.reader] ?? entry.reader;
                const meta = statusMeta(t);
                const status = meta[entry.status] ?? meta.unknown;
                const lastSyncText = entry.last_sync_at ? relativeTime(t, entry.last_sync_at) : t('sourceHealthDashboard.neverSynced');
                const rowCount = entry.row_count ?? '—';
                const netValue: React.ReactNode = entry.net_value_cny != null ? formatCNY(entry.net_value_cny) : '—';

                return (
                  <article
                    key={entry.reader}
                    className={`border border-slate-200 dark:border-border-dark/70 rounded-lg bg-slate-50/50 dark:bg-surface-dark/50 px-3 py-3${onReaderClick ? ' health-row-clickable cursor-pointer hover:bg-slate-100/80 dark:hover:bg-surface-dark transition-colors' : ''}`}
                    role={onReaderClick ? 'button' : undefined}
                    tabIndex={onReaderClick ? 0 : undefined}
                    onClick={() => onReaderClick?.(entry.reader)}
                    onKeyDown={(e) => {
                      if (onReaderClick && (e.key === 'Enter' || e.key === ' ')) {
                        e.preventDefault();
                        onReaderClick(entry.reader);
                      }
                    }}
                  >
                    <div className="flex items-start justify-between gap-2">
                      <div className="flex items-center gap-1.5 min-w-0">
                        <span className="material-symbols-outlined !text-[16px] text-slate-400 dark:text-slate-500 flex-shrink-0">
                          monitoring
                        </span>
                        <span className="text-xs font-semibold text-slate-800 dark:text-slate-100 truncate">
                          {label}
                        </span>
                      </div>
                      <span className={`inline-flex items-center gap-1 px-2 py-0.5 rounded text-[10px] font-medium ${status.badgeClass}`}>
                        <span className={`w-1.5 h-1.5 rounded-full ${status.dotClass}`} />
                        {status.label}
                      </span>
                    </div>

                    <div className="mt-2 space-y-1 text-xs text-slate-500 dark:text-slate-400">
                      <div className="flex items-center justify-between gap-3">
                        <span>{t('sourceHealthDashboard.lastSync')}</span>
                        <span className="font-medium text-slate-700 dark:text-slate-300">{lastSyncText}</span>
                      </div>
                      <div className="flex items-center justify-between gap-3">
                        <span>{t('sourceHealthDashboard.rows')}</span>
                        <span className="font-medium text-slate-700 dark:text-slate-300">{rowCount}</span>
                      </div>
                      <div className="flex items-center justify-between gap-3">
                        <span>{t('sourceHealthDashboard.netValue')}</span>
                        <span className="font-medium text-slate-700 dark:text-slate-300">{netValue}</span>
                      </div>
                    </div>

                    {entry.file_stale && (
                      <div className="mt-2 inline-flex items-center gap-1 text-[11px] text-amber-700 dark:text-amber-400">
                        <span className="material-symbols-outlined !text-[12px]">warning</span>
                        {entry.file_modified
                          ? t('sourceHealthDashboard.fileStaleWithModified', { relative: relativeTime(t, entry.file_modified) })
                          : t('sourceHealthDashboard.fileStale')}
                      </div>
                    )}
                  </article>
                );
              })}
            </div>
          ) : (
            <div className="text-xs text-slate-500 dark:text-slate-400">
              {t('sourceHealthDashboard.noData')}
            </div>
          )}
        </div>
      )}
    </section>
  );
};
