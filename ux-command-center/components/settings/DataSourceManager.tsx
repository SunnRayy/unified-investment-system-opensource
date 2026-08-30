import React, { useCallback, useEffect, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import {
  SettingsAPI,
  SourceConfig,
  SourceRegistryResponse,
  SourceHealthEntry,
  MarketDataStatusResponse,
  MarketDataProvider,
  FetchResult,
  SourceEvent,
} from '../../src/services/api';
import { formatCNY } from '../../src/utils/format';
import { ImportAdaptersPanel } from './ImportAdaptersPanel';
import { ReaderMappingsPanel } from './ReaderMappingsPanel';

// ADR-023/ADR-023 (docs/plans/2026-07-18-reader-mapping-management.md).
// Check against source.key (the settings-registry key used as the
// reader-mappings API path param), NOT source.reader (a code-module label,
// e.g. "financial_summary_reader").
//   WS-A: financial_summary (fs_column)
//   WS-B: gold, insurance, rsu (id_field_map)
//   WS-C: schwab (known_etf/symbol_norm/action_map), cn_fund (type_map)
// ibkr deliberately excluded — it shares schwab's symbol-normalization
// vocabulary (co-authority) rather than owning its own; see the muted note
// rendered on its row below instead of a "Manage assets" toggle.
const MAPPING_MANAGED_SOURCE_KEYS = new Set<string>([
  'financial_summary', 'gold', 'insurance', 'rsu', 'schwab', 'cn_fund',
]);

// Accepted upload extensions derived from format — NOT per-source-key.
// Adding a new source needs zero changes here; the backend format field drives it.
const FORMAT_ACCEPT: Record<string, string> = {
  csv: '.csv',
  flex_csv: '.csv',
  xlsx: '.xlsx,.xls',
};

// Authority chip colors per spec Section D.
// authoritative=slate, co-authority=indigo, non-authoritative=amber, historical-shadow=zinc
const AUTHORITY_CHIP: Record<string, string> = {
  authoritative:       'bg-slate-100 dark:bg-slate-700/50 text-slate-700 dark:text-slate-300 border border-slate-300 dark:border-slate-600',
  'co-authority':      'bg-indigo-100 dark:bg-indigo-900/30 text-indigo-700 dark:text-indigo-300 border border-indigo-300 dark:border-indigo-600',
  'non-authoritative': 'bg-amber-100 dark:bg-amber-900/30 text-amber-700 dark:text-amber-400 border border-amber-300 dark:border-amber-600',
  'historical-shadow': 'bg-zinc-100 dark:bg-zinc-700/50 text-zinc-600 dark:text-zinc-400 border border-zinc-300 dark:border-zinc-600',
};

type Tx = (key: string, opts?: Record<string, unknown>) => string;

function authorityLabel(t: Tx, authority: string): string {
  const map: Record<string, string> = {
    authoritative: t('dataSourceManager.authority.authoritative'),
    'co-authority': t('dataSourceManager.authority.coAuthority'),
    'non-authoritative': t('dataSourceManager.authority.nonAuthoritative'),
    'historical-shadow': t('dataSourceManager.authority.historicalShadow'),
  };
  return map[authority] ?? authority.replace(/-/g, '‑');
}

function fetcherDisplay(t: Tx): Record<string, { name: string; coverage: string }> {
  return {
    yfinance: { name: t('dataSourceManager.fetcher.yfinance.name'), coverage: t('dataSourceManager.fetcher.yfinance.coverage') },
    akshare: { name: t('dataSourceManager.fetcher.akshare.name'), coverage: t('dataSourceManager.fetcher.akshare.coverage') },
    gold: { name: t('dataSourceManager.fetcher.gold.name'), coverage: t('dataSourceManager.fetcher.gold.coverage') },
  };
}

function stalenessLabelText(t: Tx, staleness: string): string {
  const map: Record<string, string> = {
    fresh: t('dataSourceManager.stalenessLabel.fresh'),
    aging: t('dataSourceManager.stalenessLabel.aging'),
    stale: t('dataSourceManager.stalenessLabel.stale'),
    never: t('dataSourceManager.stalenessLabel.never'),
  };
  return map[staleness] ?? staleness.toUpperCase();
}

function marketDisplay(t: Tx): Record<string, string> {
  return {
    us: t('dataSourceManager.marketDisplay.us'),
    cn_fund: t('dataSourceManager.marketDisplay.cnFund'),
    gold: t('dataSourceManager.marketDisplay.gold'),
  };
}

function relativeTime(t: Tx, iso: string | null): { rel: string; abs: string } {
  if (!iso) return { rel: '–', abs: '–' };
  const date = new Date(iso);
  const diffMs = Date.now() - date.getTime();
  const diffDays = Math.floor(diffMs / 86400000);
  const diffHours = Math.floor(diffMs / 3600000);
  const diffMins = Math.floor(diffMs / 60000);

  let rel: string;
  if (diffMins < 2) rel = t('dataSourceManager.justNow');
  else if (diffMins < 60) rel = t('dataSourceManager.minutesAgo', { count: diffMins });
  else if (diffHours < 24) rel = t('dataSourceManager.hoursAgo', { count: diffHours });
  else rel = t('dataSourceManager.daysAgo', { count: diffDays });

  const abs = date.toLocaleString('en-US', {
    month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit', hour12: false,
  });
  return { rel, abs };
}

function staleDays(iso: string | null): number {
  if (!iso) return 0;
  return Math.floor((Date.now() - new Date(iso).getTime()) / 86400000);
}

function shortenPath(fullPath: string | null, fallbackDir: string | null): string {
  if (!fullPath) return '–';
  if (fallbackDir && fullPath.startsWith(fallbackDir)) {
    return '../Finance' + fullPath.slice(fallbackDir.length);
  }
  const parts = fullPath.split('/');
  return '../' + parts.slice(-2).join('/');
}

export const DataSourceManager: React.FC = () => {
  const { t } = useTranslation('system');
  const [data, setData] = useState<SourceRegistryResponse | null>(null);
  const [health, setHealth] = useState<SourceHealthEntry[]>([]);
  const [draft, setDraft] = useState<SourceConfig[] | null>(null);
  const [marketData, setMarketData] = useState<MarketDataStatusResponse | null>(null);
  const [autoRefreshEnabled, setAutoRefreshEnabled] = useState(false);
  const [autoRefreshInterval, setAutoRefreshInterval] = useState(30);
  const [loading, setLoading] = useState(true);
  const [syncRunning, setSyncRunning] = useState(false);
  const [syncLog, setSyncLog] = useState<string[]>([]);
  const [marketRefreshing, setMarketRefreshing] = useState(false);
  const [uploadStatus, setUploadStatus] = useState<Record<string, { loading: boolean; message: string; ok: boolean }>>({});
  const [fetchStatus, setFetchStatus] = useState<Record<string, { loading: boolean; message: string; ok: boolean }>>({});
  const [eventsExpanded, setEventsExpanded] = useState<Record<string, boolean>>({});
  const [eventsData, setEventsData] = useState<Record<string, { loading: boolean; events: SourceEvent[]; error: string | null }>>({});
  const fileInputRefs = useRef<Record<string, HTMLInputElement | null>>({});

  // ADR-023 / WS-A — Reader mappings panel expander (per source key)
  const [mappingsExpanded, setMappingsExpanded] = useState<Record<string, boolean>>({});

  // C5.4 — Config editor state (per source key)
  const [configExpanded, setConfigExpanded] = useState<Record<string, boolean>>({});
  const [draftDataDir, setDraftDataDir] = useState<Record<string, string>>({});
  const [draftFilePatterns, setDraftFilePatterns] = useState<Record<string, Record<string, string>>>({});
  const [configSaveStatus, setConfigSaveStatus] = useState<Record<string, { saving: boolean; message: string; ok: boolean }>>({});
  // Per-source draft for a new pattern key+value before it is added
  const [newPatternDraft, setNewPatternDraft] = useState<Record<string, { key: string; value: string }>>({});

  const handleUpload = async (sourceKey: string, file: File) => {
    // source.key IS the reader key — no mapping needed
    setUploadStatus(s => ({ ...s, [sourceKey]: { loading: true, message: '', ok: false } }));
    try {
      const result = await SettingsAPI.uploadSourceFile(sourceKey, file);
      const msg = result.warnings.length > 0
        ? t('dataSourceManager.uploadedWithWarnings', { count: result.warnings.length })
        : t('dataSourceManager.uploaded');
      setUploadStatus(s => ({ ...s, [sourceKey]: { loading: false, message: msg, ok: result.is_valid } }));
      refreshEventsFeed(sourceKey);
      setTimeout(() => load(), 800);
    } catch (e: unknown) {
      const msg = e instanceof Error ? e.message : t('dataSourceManager.uploadFailed');
      setUploadStatus(s => ({ ...s, [sourceKey]: { loading: false, message: msg, ok: false } }));
    }
  };

  const handleFetchSource = async (sourceKey: string) => {
    setFetchStatus(s => ({ ...s, [sourceKey]: { loading: true, message: '', ok: false } }));
    try {
      const result: FetchResult = await SettingsAPI.fetchSource(sourceKey);
      const msg = result.pruned.length > 0
        ? t('dataSourceManager.fetchedLinesPruned', { count: result.line_count, pruned: result.pruned.length })
        : t('dataSourceManager.fetchedLines', { count: result.line_count });
      setFetchStatus(s => ({ ...s, [sourceKey]: { loading: false, message: msg, ok: true } }));
      refreshEventsFeed(sourceKey);
      setTimeout(() => load(), 800);
    } catch (e: unknown) {
      const msg = e instanceof Error ? e.message : t('dataSourceManager.fetchFailed');
      setFetchStatus(s => ({ ...s, [sourceKey]: { loading: false, message: msg, ok: false } }));
    }
  };

  const loadEvents = async (sourceKey: string) => {
    setEventsData(s => ({ ...s, [sourceKey]: { loading: true, events: s[sourceKey]?.events ?? [], error: null } }));
    try {
      const res = await SettingsAPI.getSourceEvents(sourceKey);
      setEventsData(s => ({ ...s, [sourceKey]: { loading: false, events: res.events, error: null } }));
    } catch (e: unknown) {
      const msg = e instanceof Error ? e.message : t('dataSourceManager.failedToLoadEvents');
      setEventsData(s => ({ ...s, [sourceKey]: { loading: false, events: [], error: msg } }));
    }
  };

  // A new upload/fetch records an event: refresh the feed if it's open, else drop
  // the cache so the next expand re-fetches (fixes stale "No update history yet").
  const refreshEventsFeed = (sourceKey: string) => {
    if (eventsExpanded[sourceKey]) {
      void loadEvents(sourceKey);
    } else {
      setEventsData(s => { const n = { ...s }; delete n[sourceKey]; return n; });
    }
  };

  const handleToggleEvents = async (sourceKey: string) => {
    const nowExpanded = !eventsExpanded[sourceKey];
    setEventsExpanded(s => ({ ...s, [sourceKey]: nowExpanded }));
    if (nowExpanded && !eventsData[sourceKey]) {
      await loadEvents(sourceKey);
    }
  };

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const [resR, healthR, mdR, schedR] = await Promise.allSettled([
        SettingsAPI.getSources(),
        SettingsAPI.getSourceHealth(),
        SettingsAPI.getMarketDataStatus(),
        SettingsAPI.getRefreshSchedule(),
      ]);
      if (resR.status === 'fulfilled') {
        setData(resR.value);
        setDraft(resR.value.sources.map((s: SourceConfig) => ({ ...s })));
        // Initialize C5.4 draft editors from live data (only if not currently being edited)
        setDraftDataDir(prev => {
          const next: Record<string, string> = {};
          for (const s of resR.value.sources) {
            // preserve in-progress edits; initialize missing keys only
            next[s.key] = s.key in prev ? prev[s.key] : (s.data_dir ?? '');
          }
          return next;
        });
        setDraftFilePatterns(prev => {
          const next: Record<string, Record<string, string>> = {};
          for (const s of resR.value.sources) {
            next[s.key] = s.key in prev ? prev[s.key] : { ...s.file_patterns };
          }
          return next;
        });
      }
      if (healthR.status === 'fulfilled') setHealth(healthR.value.sources);
      if (mdR.status === 'fulfilled') setMarketData(mdR.value);
      if (schedR.status === 'fulfilled') {
        setAutoRefreshEnabled(schedR.value.enabled);
        setAutoRefreshInterval(schedR.value.interval_minutes);
      }
    } catch (e) {
      console.error(e);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { load(); }, [load]);

  const handleToggleSource = async (key: string, enabled: boolean) => {
    if (!draft) return;
    const updated = draft.map(s => s.key === key ? { ...s, enabled } : s);
    setDraft(updated);
    try {
      await SettingsAPI.updateSources({ sources: updated.map(s => ({ key: s.key, enabled: s.enabled })) });
    } catch (e) {
      console.error('Failed to update source toggle', e);
    }
  };

  const handleSaveConfig = async (key: string) => {
    setConfigSaveStatus(s => ({ ...s, [key]: { saving: true, message: '', ok: false } }));
    try {
      await SettingsAPI.updateSources({
        sources: [{
          key,
          data_dir: draftDataDir[key] ?? '',
          file_patterns: draftFilePatterns[key] ?? {},
        }],
      });
      setConfigSaveStatus(s => ({ ...s, [key]: { saving: false, message: t('dataSourceManager.saved'), ok: true } }));
      // Reload so resolved_dir, file_found, etc. reflect the updated config.
      // Re-init draft state ONLY for this key once the fresh data lands (handled in load()).
      // Clear draft for this key so load() re-reads from backend.
      setDraftDataDir(prev => { const n = { ...prev }; delete n[key]; return n; });
      setDraftFilePatterns(prev => { const n = { ...prev }; delete n[key]; return n; });
      setTimeout(() => load(), 600);
    } catch (e: unknown) {
      const msg = e instanceof Error ? e.message : t('dataSourceManager.saveFailed');
      setConfigSaveStatus(s => ({ ...s, [key]: { saving: false, message: msg, ok: false } }));
    }
  };

  const handleRevertConfig = (source: SourceConfig) => {
    setDraftDataDir(prev => ({ ...prev, [source.key]: source.data_dir ?? '' }));
    setDraftFilePatterns(prev => ({ ...prev, [source.key]: { ...source.file_patterns } }));
    setConfigSaveStatus(s => ({ ...s, [source.key]: { saving: false, message: '', ok: false } }));
  };

  const handleToggleConfig = (key: string) => {
    setConfigExpanded(prev => ({ ...prev, [key]: !prev[key] }));
  };

  const handleToggleMappings = (key: string) => {
    setMappingsExpanded(prev => ({ ...prev, [key]: !prev[key] }));
  };

  const handlePatternChange = (key: string, patKey: string, value: string) => {
    setDraftFilePatterns(prev => ({
      ...prev,
      [key]: { ...(prev[key] ?? {}), [patKey]: value },
    }));
  };

  const handleAddPattern = (sourceKey: string, patKey: string, value: string) => {
    const trimmedKey = patKey.trim();
    const trimmedValue = value.trim();
    if (!trimmedKey || !trimmedValue) return;
    const existing = draftFilePatterns[sourceKey] ?? {};
    if (trimmedKey in existing) return; // guard: skip if already exists
    setDraftFilePatterns(prev => ({
      ...prev,
      [sourceKey]: { ...(prev[sourceKey] ?? {}), [trimmedKey]: trimmedValue },
    }));
    // Clear the draft inputs for this source
    setNewPatternDraft(prev => ({ ...prev, [sourceKey]: { key: '', value: '' } }));
  };

  const handleSyncAll = async () => {
    if (syncRunning) return;
    setSyncRunning(true);
    setSyncLog([]);
    try {
      await SettingsAPI.startSync();
      // Open SSE stream — keeps Cloud Run container alive during sync (~30s)
      SettingsAPI.streamSyncLogs(
        (msg) => setSyncLog(prev => [...prev.slice(-49), msg]),
        (_success) => {
          setSyncRunning(false);
          load();
        }
      );
    } catch (e) {
      console.error(e);
      setSyncRunning(false);
    }
  };

  const handleRefreshMarketData = async () => {
    if (marketRefreshing) return;
    setMarketRefreshing(true);
    try {
      await SettingsAPI.refreshMarketData();
      const mdRes = await SettingsAPI.getMarketDataStatus();
      setMarketData(mdRes);
    } catch (e) {
      console.error(e);
    } finally {
      setMarketRefreshing(false);
    }
  };

  const handleToggleAutoRefresh = async (enabled: boolean) => {
    setAutoRefreshEnabled(enabled);
    try {
      await SettingsAPI.updateRefreshSchedule(enabled, autoRefreshInterval);
    } catch (e) {
      console.error('Failed to update auto-refresh', e);
      setAutoRefreshEnabled(!enabled);
    }
  };

  if (loading) {
    return (
      <div className="flex items-center gap-2 text-slate-500 text-sm">
        <span className="material-symbols-outlined !text-[18px] animate-spin">progress_activity</span>
        {t('dataSourceManager.loadingDataSources')}
      </div>
    );
  }

  const healthByKey = Object.fromEntries(health.map(h => [h.reader, h]));
  const staleCount = health.filter(h => h.file_stale).length;
  const totalRows = health.reduce((s, h) => s + (h.row_count ?? 0), 0);
  const totalValue = health.reduce((s, h) => s + (h.net_value_cny ?? 0), 0);
  const lastSyncAt = health[0]?.last_sync_at ?? null;
  const { rel: lastSyncRel } = relativeTime(t, lastSyncAt);
  const lastSyncAbsFormatted = lastSyncAt
    ? new Date(lastSyncAt).toLocaleString('en-US', { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false })
    : null;

  // Providers from the real API — what's actively fetching prices
  const providers: MarketDataProvider[] = marketData?.providers ?? [];
  const stalenessLabel = marketData?.staleness ?? 'never';
  const stalenessCls =
    stalenessLabel === 'fresh' ? 'text-emerald-600 dark:text-emerald-400' :
    stalenessLabel === 'aging' ? 'text-amber-600 dark:text-amber-400' :
    stalenessLabel === 'stale' ? 'text-red-600 dark:text-red-400' :
    'text-slate-400';

  return (
    <div className="w-full text-slate-800 dark:text-slate-200">
      {/* Default directory — read-only info, no edit button (requires settings.yaml) */}
      <div className="flex items-center gap-2 px-4 py-3 mb-6 bg-slate-50 dark:bg-slate-800/40 border border-slate-200 dark:border-border-dark rounded-xl text-[13px]">
        <span className="material-symbols-outlined !text-[18px] text-slate-400 shrink-0">folder_open</span>
        <span className="text-slate-500 shrink-0">{t('dataSourceManager.defaultDirectory')}</span>
        <span className="font-mono text-slate-700 dark:text-slate-300 bg-white dark:bg-slate-900 px-2 py-0.5 rounded border border-slate-200 dark:border-border-dark shadow-sm truncate max-w-sm">
          {data?.fallback_dir ?? t('dataSourceManager.defaultFallbackPath')}
        </span>
        <span className="text-slate-400 shrink-0">{t('dataSourceManager.usedByDefaultPath')}</span>
      </div>

      {/* TABLE 1: Local data files */}
      <div className="mb-6 rounded-xl border border-slate-200 dark:border-border-dark bg-white dark:bg-card-dark overflow-hidden shadow-sm">
        <div className="flex items-center justify-between px-5 py-3.5 border-b border-slate-200 dark:border-border-dark bg-slate-50/50 dark:bg-surface-dark/30">
          <div className="flex items-center gap-2.5">
            <span className="material-symbols-outlined !text-[18px] text-primary">folder_copy</span>
            <div>
              <h2 className="text-[13px] font-bold text-slate-900 dark:text-white leading-tight">{t('dataSourceManager.localDataFiles')}</h2>
              <p className="text-[11px] text-slate-500 dark:text-slate-400 mt-0.5 font-mono">
                {t('dataSourceManager.readersCsvXlsx', { count: draft?.length ?? 0 })}
              </p>
            </div>
          </div>
          <div className="flex items-center gap-2.5">
            {staleCount > 0 && (
              <span className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-md bg-amber-100 dark:bg-amber-900/30 text-amber-700 dark:text-amber-400 text-[11px] font-bold tracking-wide">
                <span className="w-1.5 h-1.5 rounded-full bg-amber-500"></span>
                {t('dataSourceManager.staleCount', { count: staleCount })}
              </span>
            )}
            <button
              onClick={handleSyncAll}
              disabled={syncRunning}
              className="flex items-center gap-1.5 px-3.5 py-1.5 rounded-lg bg-primary text-white text-[12px] font-semibold hover:bg-primary-hover transition-colors disabled:opacity-50 shadow-sm"
            >
              <span className={`material-symbols-outlined !text-[15px] ${syncRunning ? 'animate-spin' : ''}`}>
                {syncRunning ? 'progress_activity' : 'sync'}
              </span>
              {t('dataSourceManager.syncAllFiles')}
            </button>
          </div>
        </div>

        {/* Sync log (visible while sync is running) */}
        {syncRunning && syncLog.length > 0 && (
          <div className="px-5 py-2 bg-slate-950 dark:bg-black border-b border-slate-800 font-mono text-[11px] text-emerald-400 max-h-28 overflow-y-auto">
            {syncLog.map((line, i) => <div key={i}>{line}</div>)}
          </div>
        )}

        {/* Stats bar */}
        <div className="flex items-center gap-5 px-5 py-2 bg-blue-50/30 dark:bg-blue-900/10 border-b border-slate-200 dark:border-border-dark text-[11px] font-mono tracking-wider text-slate-500 uppercase">
          <span className="flex items-center gap-1.5 text-slate-600 dark:text-slate-300">
            <span className="material-symbols-outlined !text-[14px] text-emerald-500">history_toggle_off</span>
            {t('dataSourceManager.lastFullSync')}
            <span className="text-slate-900 dark:text-white font-semibold ml-1">
              {lastSyncAbsFormatted ?? t('dataSourceManager.never')}
            </span>
            {lastSyncAt && <span className="text-slate-400 font-normal">· {lastSyncRel}</span>}
          </span>
          <span className="border-l border-slate-200 dark:border-slate-700 pl-5">
            {t('dataSourceManager.rowsImported')} <span className="text-slate-900 dark:text-white font-semibold ml-1">{totalRows}</span>
          </span>
          <span className="border-l border-slate-200 dark:border-slate-700 pl-5">
            {t('dataSourceManager.netValue')} <span className="text-slate-900 dark:text-white font-semibold ml-1">
              {formatCNY(totalValue, 2)}
            </span>
          </span>
        </div>

        {/* Files table */}
        <div className="overflow-x-auto">
          <table className="w-full text-left border-collapse">
            <thead>
              <tr className="text-[10px] font-bold text-slate-400 dark:text-slate-500 uppercase tracking-wider border-b border-slate-100 dark:border-slate-800/50">
                <th className="pl-5 pr-2 py-2.5 w-44">{t('dataSourceManager.col.file')}</th>
                <th className="px-2 py-2.5">{t('dataSourceManager.col.path')}</th>
                <th className="px-2 py-2.5 w-28">{t('dataSourceManager.col.status')}</th>
                <th className="px-2 py-2.5 w-16 text-right">{t('dataSourceManager.col.rows')}</th>
                <th className="px-4 py-2.5 w-28">{t('dataSourceManager.col.lastSync')}</th>
                <th className="px-4 py-2.5 w-32">{t('dataSourceManager.col.fileModified')}</th>
                <th className="px-2 py-2.5 w-14 text-center">{t('dataSourceManager.col.on')}</th>
                <th className="px-4 py-2.5 w-20"></th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-100 dark:divide-slate-800/50">
              {draft?.map((source) => {
                const h = healthByKey[source.key];
                // Display name comes from the payload label field (registry-driven)
                const displayName = source.label || source.key;
                const shortPath = shortenPath(source.file_path, data?.fallback_dir ?? null);
                const rowCount = h?.row_count ?? 0;
                const isStale = h?.file_stale ?? false;
                const stale = staleDays(source.file_modified);
                const { rel: syncRel, abs: syncAbs } = relativeTime(t, h?.last_sync_at ?? null);
                const { rel: modRel, abs: modAbs } = relativeTime(t, source.file_modified);
                const statusLabel = isStale ? t('dataSourceManager.staleDays', { count: stale }) : t('dataSourceManager.ok');
                const statusCls = isStale
                  ? t('dataSourceManager.css.statusStale')
                  : t('dataSourceManager.css.statusOk');
                const dotCls = isStale ? 'bg-amber-500' : 'bg-emerald-500';

                const { rel: lastUpdateRel, abs: lastUpdateAbs } = relativeTime(t, source.last_update?.at ?? null);
                const lastUpdateOriginLabel = source.last_update?.origin === 'fetch' ? t('dataSourceManager.viaAutoFetch') : source.last_update?.origin === 'upload' ? t('dataSourceManager.viaUpload') : null;
                const fetchSt = fetchStatus[source.key];
                const isEventsExpanded = eventsExpanded[source.key] ?? false;
                const eventsInfo = eventsData[source.key];

                return (
                  <React.Fragment key={source.key}>
                  <tr className="hover:bg-slate-50/60 dark:hover:bg-slate-800/20 transition-colors group">
                    <td className="pl-5 pr-2 py-3">
                      <div className="flex flex-col gap-1">
                        <span className="text-[13px] font-bold text-slate-800 dark:text-slate-100 leading-tight">{displayName}</span>
                        <div className="flex items-center gap-1.5 flex-wrap">
                          <span
                            className={`inline-flex items-center px-1.5 py-0.5 rounded text-[10px] font-semibold tracking-wide uppercase ${AUTHORITY_CHIP[source.authority] ?? AUTHORITY_CHIP['non-authoritative']}`}
                            title={source.authority_note ?? undefined}
                          >
                            {authorityLabel(t, source.authority)}
                          </span>
                          <span className="inline-flex items-center px-1.5 py-0.5 rounded text-[10px] font-semibold tracking-wide uppercase bg-slate-100 dark:bg-slate-800 text-slate-500 dark:text-slate-400 border border-slate-200 dark:border-slate-700">
                            {source.format.toUpperCase()}
                          </span>
                          {MAPPING_MANAGED_SOURCE_KEYS.has(source.key) && (source.unmapped_count ?? 0) > 0 && (
                            <button
                              onClick={() => setMappingsExpanded(prev => ({ ...prev, [source.key]: true }))}
                              title={t('dataSourceManager.openAssetMappings')}
                              className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded text-[10px] font-semibold tracking-wide uppercase bg-amber-100 dark:bg-amber-900/30 text-amber-700 dark:text-amber-400 border border-amber-300 dark:border-amber-600 hover:bg-amber-200 dark:hover:bg-amber-900/50 transition-colors"
                            >
                              <span className="w-1.5 h-1.5 rounded-full bg-amber-500"></span>
                              {t('dataSourceManager.unmappedCount', { count: source.unmapped_count })}
                            </button>
                          )}
                        </div>
                        {lastUpdateOriginLabel && (
                          <span
                            className="text-[11px] text-slate-400 dark:text-slate-500"
                            title={lastUpdateAbs}
                          >
                            {lastUpdateOriginLabel} · {lastUpdateRel}
                          </span>
                        )}
                      </div>
                    </td>
                    <td className="px-2 py-3">
                      <span className="font-mono text-[11px] text-slate-400 dark:text-slate-500 truncate block max-w-[210px]" title={source.file_path ?? ''}>
                        {shortPath}
                      </span>
                    </td>
                    <td className="px-2 py-3">
                      <span className={`inline-flex items-center gap-1.5 px-2 py-0.5 rounded text-[10px] font-bold tracking-wide uppercase ${statusCls}`}>
                        <span className={`w-1.5 h-1.5 rounded-full ${dotCls}`}></span>
                        {statusLabel}
                      </span>
                    </td>
                    <td className="px-2 py-3 text-right">
                      <span className="text-[13px] font-semibold text-slate-700 dark:text-slate-200 tabular-nums">{rowCount}</span>
                    </td>
                    <td className="px-4 py-3">
                      <div className="text-[12px] font-medium text-slate-700 dark:text-slate-200">{syncRel}</div>
                      <div className="text-[11px] text-slate-400 font-mono">{syncAbs.split(',')[1]?.trim() ?? ''}</div>
                    </td>
                    <td className="px-4 py-3">
                      <div className={`text-[12px] font-medium ${isStale ? 'text-amber-600 dark:text-amber-400' : 'text-slate-700 dark:text-slate-200'}`}>{modRel}</div>
                      <div className="text-[11px] text-slate-400 font-mono">{modAbs.split(',')[0]}</div>
                    </td>
                    <td className="px-2 py-3 text-center">
                      <button
                        onClick={() => handleToggleSource(source.key, !source.enabled)}
                        className={`relative inline-flex h-5 w-9 items-center rounded-full transition-colors focus:outline-none ${source.enabled ? 'bg-primary' : 'bg-slate-300 dark:bg-slate-600'}`}
                      >
                        <span className={`inline-block h-3.5 w-3.5 transform rounded-full bg-white shadow-sm transition-transform ${source.enabled ? 'translate-x-[18px]' : 'translate-x-[2px]'}`} />
                      </button>
                    </td>
                    <td className="px-4 py-3 text-right">
                      {(() => {
                        // Accept extensions are derived from the format field — registry-driven
                        const accept = FORMAT_ACCEPT[source.format] ?? t('dataSourceManager.defaultAcceptTypes');
                        const patternCount = Object.keys(source.file_patterns ?? {}).length;
                        const multiFile = patternCount >= 2;
                        const us = uploadStatus[source.key];
                        return (
                          <div className="flex flex-col items-end gap-1.5">
                            {/* Status messages */}
                            {us && !us.loading && us.message && (
                              <span className={`text-[11px] font-medium ${us.ok ? 'text-emerald-600 dark:text-emerald-400' : 'text-red-500 dark:text-red-400'}`}>
                                {us.message}
                              </span>
                            )}
                            {fetchSt && !fetchSt.loading && fetchSt.message && (
                              <span className={`text-[11px] font-medium ${fetchSt.ok ? 'text-emerald-600 dark:text-emerald-400' : 'text-red-500 dark:text-red-400'}`}>
                                {fetchSt.message}
                              </span>
                            )}
                            <div className="flex items-center gap-1.5">
                              <input
                                ref={el => { fileInputRefs.current[source.key] = el; }}
                                type="file"
                                accept={accept}
                                multiple
                                className="hidden"
                                onChange={async e => {
                                  const files = Array.from(e.target.files ?? []);
                                  for (const f of files) await handleUpload(source.key, f);
                                  e.target.value = '';
                                }}
                              />
                              <button
                                onClick={() => fileInputRefs.current[source.key]?.click()}
                                disabled={us?.loading}
                                title={multiFile ? t('dataSourceManager.uploadNFiles', { count: patternCount }) : t('dataSourceManager.uploadNewFile')}
                                className="inline-flex items-center gap-1 px-2.5 py-1 rounded-lg border border-primary/40 bg-primary/5 dark:bg-primary/10 text-primary text-[12px] font-semibold hover:bg-primary/15 disabled:opacity-40 transition-colors shadow-sm"
                              >
                                {us?.loading
                                  ? <span className="material-symbols-outlined !text-[14px] animate-spin">progress_activity</span>
                                  : <span className="material-symbols-outlined !text-[14px]">upload_file</span>}
                                {multiFile ? t('dataSourceManager.uploadNFilesParen', { count: patternCount }) : t('dataSourceManager.upload')}
                              </button>
                              {source.can_fetch && (
                                <button
                                  onClick={() => handleFetchSource(source.key)}
                                  disabled={fetchSt?.loading}
                                  title={t('dataSourceManager.triggerLiveFetch')}
                                  className="inline-flex items-center gap-1 px-2.5 py-1 rounded-lg border border-emerald-400/50 bg-emerald-50 dark:bg-emerald-900/20 text-emerald-700 dark:text-emerald-400 text-[12px] font-semibold hover:bg-emerald-100 dark:hover:bg-emerald-900/30 disabled:opacity-40 transition-colors shadow-sm"
                                >
                                  {fetchSt?.loading
                                    ? <span className="material-symbols-outlined !text-[14px] animate-spin">progress_activity</span>
                                    : <span className="material-symbols-outlined !text-[14px]">cloud_download</span>}
                                  {t('dataSourceManager.fetchNow')}
                                </button>
                              )}
                            </div>
                            {/* Update history toggle */}
                            <button
                              onClick={() => handleToggleEvents(source.key)}
                              className="text-[11px] text-slate-400 dark:text-slate-500 hover:text-slate-600 dark:hover:text-slate-300 flex items-center gap-0.5 transition-colors"
                            >
                              <span className="material-symbols-outlined !text-[13px]">
                                {isEventsExpanded ? 'expand_less' : 'expand_more'}
                              </span>
                              {t('dataSourceManager.updateHistory')}
                            </button>
                            {/* Config editor toggle (C5.4) */}
                            <button
                              onClick={() => handleToggleConfig(source.key)}
                              className="text-[11px] text-slate-400 dark:text-slate-500 hover:text-slate-600 dark:hover:text-slate-300 flex items-center gap-0.5 transition-colors"
                            >
                              <span className="material-symbols-outlined !text-[13px]">
                                {configExpanded[source.key] ? 'expand_less' : 'expand_more'}
                              </span>
                              {t('dataSourceManager.config')}
                            </button>
                            {/* Reader mappings toggle (ADR-023/ADR-023) */}
                            {MAPPING_MANAGED_SOURCE_KEYS.has(source.key) && (
                              <button
                                onClick={() => handleToggleMappings(source.key)}
                                className="text-[11px] text-slate-400 dark:text-slate-500 hover:text-slate-600 dark:hover:text-slate-300 flex items-center gap-0.5 transition-colors"
                              >
                                <span className="material-symbols-outlined !text-[13px]">
                                  {mappingsExpanded[source.key] ? 'expand_less' : 'expand_more'}
                                </span>
                                {t('dataSourceManager.manageAssets')}
                              </button>
                            )}
                            {/* ADR-023 WS-C — ibkr shares schwab's symbol-normalization
                                vocabulary (co-authority) rather than owning its own panel. */}
                            {source.key === 'ibkr' && (
                              <span
                                className="text-[11px] text-slate-400 dark:text-slate-500 italic"
                                title={t('dataSourceManager.ibkrReuseTitle')}
                              >
                                {t('dataSourceManager.ibkrManagedUnderSchwab')}
                              </span>
                            )}
                          </div>
                        );
                      })()}
                    </td>
                  </tr>
                  {/* Expandable events feed row */}
                  {isEventsExpanded && (
                    <tr className="bg-slate-50/50 dark:bg-slate-800/10">
                      <td colSpan={8} className="px-5 py-3">
                        {eventsInfo?.loading && (
                          <div className="flex items-center gap-2 text-slate-400 text-[12px]">
                            <span className="material-symbols-outlined !text-[15px] animate-spin">progress_activity</span>
                            {t('dataSourceManager.loadingUpdateHistory')}
                          </div>
                        )}
                        {eventsInfo?.error && (
                          <span className="text-[12px] text-red-500 dark:text-red-400">{eventsInfo.error}</span>
                        )}
                        {eventsInfo && !eventsInfo.loading && !eventsInfo.error && eventsInfo.events.length === 0 && (
                          <span className="text-[12px] text-slate-400 italic">{t('dataSourceManager.noUpdateHistory')}</span>
                        )}
                        {eventsInfo && !eventsInfo.loading && eventsInfo.events.length > 0 && (
                          <div className="flex flex-col gap-1.5">
                            <div className="text-[10px] font-bold uppercase tracking-wider text-slate-400 mb-1">{t('dataSourceManager.updateHistoryHeading')}</div>
                            {eventsInfo.events.map((ev: SourceEvent) => {
                              const { rel: evRel, abs: evAbs } = relativeTime(t, ev.occurred_at);
                              const isFetch = ev.origin === 'fetch';
                              return (
                                <div key={ev.id} className="flex items-start gap-2.5 text-[12px]">
                                  <span
                                    className={`material-symbols-outlined !text-[15px] mt-0.5 shrink-0 ${isFetch ? 'text-emerald-500' : 'text-primary'}`}
                                    title={isFetch ? t('dataSourceManager.autoFetch') : t('dataSourceManager.uploadTitle')}
                                  >
                                    {isFetch ? 'cloud_download' : 'upload_file'}
                                  </span>
                                  <div className="flex flex-col gap-0.5 min-w-0">
                                    <div className="flex items-center gap-2 flex-wrap">
                                      <span className="font-mono text-slate-700 dark:text-slate-200 truncate max-w-[260px]" title={ev.filename}>
                                        {ev.filename}
                                      </span>
                                      <span
                                        className="text-slate-400 dark:text-slate-500 shrink-0"
                                        title={evAbs}
                                      >
                                        {evRel}
                                      </span>
                                      {ev.is_valid === true && (
                                        <span className="inline-flex items-center px-1.5 py-0.5 rounded text-[10px] font-bold uppercase text-emerald-700 bg-emerald-50 dark:bg-emerald-900/20 dark:text-emerald-400 border border-emerald-200 dark:border-emerald-700/40">
                                          {t('dataSourceManager.valid')}
                                        </span>
                                      )}
                                      {ev.is_valid === false && (
                                        <span className="inline-flex items-center px-1.5 py-0.5 rounded text-[10px] font-bold uppercase text-red-700 bg-red-50 dark:bg-red-900/20 dark:text-red-400 border border-red-200 dark:border-red-700/40">
                                          {t('dataSourceManager.invalid')}
                                        </span>
                                      )}
                                    </div>
                                    {ev.warnings.length > 0 && (
                                      <ul className="list-none pl-0 mt-0.5">
                                        {ev.warnings.map((w, wi) => (
                                          <li key={wi} className="text-[11px] text-amber-600 dark:text-amber-400">⚠ {w}</li>
                                        ))}
                                      </ul>
                                    )}
                                  </div>
                                </div>
                              );
                            })}
                          </div>
                        )}
                      </td>
                    </tr>
                  )}
                  {/* C5.4 — Expandable Config editor row */}
                  {configExpanded[source.key] && (() => {
                    const cfgDir = draftDataDir[source.key] ?? source.data_dir ?? '';
                    const cfgPatterns = draftFilePatterns[source.key] ?? source.file_patterns;
                    const cfgStatus = configSaveStatus[source.key];
                    const isDirDirty = cfgDir !== (source.data_dir ?? '');
                    const isPatternsDirty = JSON.stringify(cfgPatterns) !== JSON.stringify(source.file_patterns);
                    const isDirty = isDirDirty || isPatternsDirty;
                    return (
                      <tr className="bg-slate-50/50 dark:bg-slate-800/10">
                        <td colSpan={8} className="px-5 py-4">
                          <div className="max-w-xl space-y-4">
                            <div className="text-[10px] font-bold uppercase tracking-wider text-slate-400 mb-1">{t('dataSourceManager.sourceConfig')}</div>

                            {/* Identity fields (read-only) — raw API field names, shown verbatim */}
                            <div className="grid grid-cols-2 gap-x-4 gap-y-1.5 text-[11px]">
                              <div>
                                <span className="font-mono text-slate-400 dark:text-slate-500 mr-1.5">{t('dataSourceManager.field.key')}</span>
                                <span className="font-mono text-slate-600 dark:text-slate-300">{source.key}</span>
                              </div>
                              <div>
                                <span className="font-mono text-slate-400 dark:text-slate-500 mr-1.5">{t('dataSourceManager.field.reader')}</span>
                                <span className="font-mono text-slate-600 dark:text-slate-300">{source.reader}</span>
                              </div>
                              <div>
                                <span className="font-mono text-slate-400 dark:text-slate-500 mr-1.5">{t('dataSourceManager.field.format')}</span>
                                <span className="font-mono text-slate-600 dark:text-slate-300">{source.format}</span>
                              </div>
                              {source.asset_prefixes.length > 0 && (
                                <div className="col-span-2">
                                  <span className="font-mono text-slate-400 dark:text-slate-500 mr-1.5">{t('dataSourceManager.field.assetPrefixes')}</span>
                                  <span className="font-mono text-slate-600 dark:text-slate-300">{source.asset_prefixes.join(', ')}</span>
                                </div>
                              )}
                            </div>

                            {/* data_dir editable input */}
                            <div>
                              <label className="block text-[11px] font-semibold text-slate-600 dark:text-slate-400 mb-1">
                                {t('dataSourceManager.dataDirectory')}
                              </label>
                              <input
                                type="text"
                                value={cfgDir}
                                onChange={e => setDraftDataDir(prev => ({ ...prev, [source.key]: e.target.value }))}
                                placeholder={data?.fallback_dir ?? t('dataSourceManager.leaveEmptyDefault')}
                                className="w-full px-2.5 py-1.5 text-[12px] font-mono rounded-lg border border-slate-200 dark:border-border-dark bg-white dark:bg-surface-dark text-slate-800 dark:text-slate-100 placeholder-slate-400 dark:placeholder-slate-500 focus:outline-none focus:ring-2 focus:ring-primary/30 focus:border-primary"
                              />
                              {source.resolved_dir && (
                                <p className="mt-1 text-[11px] text-slate-400 dark:text-slate-500 font-mono truncate">
                                  {t('dataSourceManager.resolvedPrefix')} {source.resolved_dir}
                                  {source.fallback_active && <span className="ml-1.5 text-amber-500">{t('dataSourceManager.fallbackParen')}</span>}
                                </p>
                              )}
                            </div>

                            {/* file_patterns editable rows */}
                            <div>
                              <label className="block text-[11px] font-semibold text-slate-600 dark:text-slate-400 mb-1">
                                {t('dataSourceManager.filePatterns')}
                              </label>
                              <div className="space-y-1.5">
                                {Object.keys(cfgPatterns).map(patKey => (
                                  <div key={patKey} className="flex items-center gap-2">
                                    <span className="text-[11px] font-mono text-slate-500 dark:text-slate-400 w-20 shrink-0 truncate" title={patKey}>
                                      {patKey}
                                    </span>
                                    <input
                                      type="text"
                                      value={cfgPatterns[patKey] ?? ''}
                                      onChange={e => handlePatternChange(source.key, patKey, e.target.value)}
                                      className="flex-1 px-2 py-1 text-[12px] font-mono rounded border border-slate-200 dark:border-border-dark bg-white dark:bg-surface-dark text-slate-800 dark:text-slate-100 focus:outline-none focus:ring-2 focus:ring-primary/30 focus:border-primary"
                                    />
                                  </div>
                                ))}
                                {/* Add new pattern row */}
                                {(() => {
                                  const draft = newPatternDraft[source.key] ?? { key: '', value: '' };
                                  return (
                                    <div className="flex items-center gap-2 pt-0.5">
                                      <input
                                        type="text"
                                        value={draft.key}
                                        onChange={e => setNewPatternDraft(prev => ({ ...prev, [source.key]: { ...draft, key: e.target.value } }))}
                                        placeholder={t('dataSourceManager.keyPlaceholder')}
                                        className="w-20 shrink-0 px-2 py-1 text-[11px] font-mono rounded border border-dashed border-slate-300 dark:border-slate-600 bg-white dark:bg-surface-dark text-slate-800 dark:text-slate-100 placeholder-slate-400 dark:placeholder-slate-500 focus:outline-none focus:ring-2 focus:ring-primary/30 focus:border-primary"
                                      />
                                      <input
                                        type="text"
                                        value={draft.value}
                                        onChange={e => setNewPatternDraft(prev => ({ ...prev, [source.key]: { ...draft, value: e.target.value } }))}
                                        placeholder={t('dataSourceManager.globPatternPlaceholder')}
                                        className="flex-1 px-2 py-1 text-[12px] font-mono rounded border border-dashed border-slate-300 dark:border-slate-600 bg-white dark:bg-surface-dark text-slate-800 dark:text-slate-100 placeholder-slate-400 dark:placeholder-slate-500 focus:outline-none focus:ring-2 focus:ring-primary/30 focus:border-primary"
                                      />
                                      <button
                                        onClick={() => handleAddPattern(source.key, draft.key, draft.value)}
                                        title={t('dataSourceManager.addPattern')}
                                        className="inline-flex items-center gap-0.5 px-2 py-1 rounded-lg bg-primary text-white text-[12px] font-semibold hover:bg-primary-hover disabled:opacity-40 transition-colors shadow-sm"
                                      >
                                        <span className="material-symbols-outlined !text-[14px]">add</span>
                                      </button>
                                    </div>
                                  );
                                })()}
                              </div>
                            </div>

                            {/* Save / Revert */}
                            <div className="flex items-center gap-2.5 pt-1">
                              <button
                                onClick={() => handleSaveConfig(source.key)}
                                disabled={cfgStatus?.saving}
                                className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-primary text-white text-[12px] font-semibold hover:bg-primary-hover disabled:opacity-50 transition-colors shadow-sm"
                              >
                                {cfgStatus?.saving
                                  ? <span className="material-symbols-outlined !text-[14px] animate-spin">progress_activity</span>
                                  : <span className="material-symbols-outlined !text-[14px]">save</span>}
                                {t('dataSourceManager.save')}
                              </button>
                              {isDirty && (
                                <button
                                  onClick={() => handleRevertConfig(source)}
                                  className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg border border-slate-200 dark:border-border-dark text-slate-600 dark:text-slate-300 text-[12px] font-medium hover:bg-slate-50 dark:hover:bg-slate-800/30 transition-colors"
                                >
                                  <span className="material-symbols-outlined !text-[14px]">undo</span>
                                  {t('dataSourceManager.revert')}
                                </button>
                              )}
                              {cfgStatus && !cfgStatus.saving && cfgStatus.message && (
                                <span className={`text-[12px] font-medium ${cfgStatus.ok ? 'text-emerald-600 dark:text-emerald-400' : 'text-red-500 dark:text-red-400'}`}>
                                  {cfgStatus.message}
                                </span>
                              )}
                            </div>
                          </div>
                        </td>
                      </tr>
                    );
                  })()}
                  {/* ADR-023 / WS-A — Expandable reader-mappings panel row */}
                  {MAPPING_MANAGED_SOURCE_KEYS.has(source.key) && mappingsExpanded[source.key] && (
                    <tr className="bg-slate-50/50 dark:bg-slate-800/10">
                      <td colSpan={8} className="px-5 py-4">
                        <div className="rounded-xl border border-slate-200 dark:border-border-dark bg-white dark:bg-card-dark shadow-sm p-3">
                          <ReaderMappingsPanel reader={source.key} />
                        </div>
                      </td>
                    </tr>
                  )}
                  </React.Fragment>
                );
              })}
            </tbody>
          </table>
        </div>
      </div>

      <ImportAdaptersPanel />

      {/* TABLE 2: Market data fetchers */}
      <div className="mb-6 rounded-xl border border-slate-200 dark:border-border-dark bg-white dark:bg-card-dark overflow-hidden shadow-sm">
        <div className="flex items-center justify-between px-5 py-3.5 border-b border-slate-200 dark:border-border-dark bg-slate-50/50 dark:bg-surface-dark/30">
          <div className="flex items-center gap-2.5">
            <span className="material-symbols-outlined !text-[18px] text-primary">show_chart</span>
            <div>
              <h2 className="text-[13px] font-bold text-slate-900 dark:text-white leading-tight">{t('dataSourceManager.marketPriceFetchers')}</h2>
              <p className="text-[11px] text-slate-500 dark:text-slate-400 mt-0.5 font-mono">
                {t('dataSourceManager.builtInFetchers')}
              </p>
            </div>
          </div>
          <div className="flex items-center gap-3">
            {/* Auto-refresh toggle */}
            <div className="flex items-center gap-2 px-3 py-1.5 rounded-lg border border-slate-200 dark:border-border-dark bg-white dark:bg-card-dark text-[12px]">
              <span className="text-slate-500 dark:text-slate-400 font-medium">{t('dataSourceManager.autoRefresh')}</span>
              <button
                onClick={() => handleToggleAutoRefresh(!autoRefreshEnabled)}
                className={`relative inline-flex h-5 w-9 items-center rounded-full transition-colors focus:outline-none ${autoRefreshEnabled ? 'bg-primary' : 'bg-slate-300 dark:bg-slate-600'}`}
              >
                <span className={`inline-block h-3.5 w-3.5 transform rounded-full bg-white shadow-sm transition-transform ${autoRefreshEnabled ? 'translate-x-[18px]' : 'translate-x-[2px]'}`} />
              </button>
              {autoRefreshEnabled && (
                <span className="text-slate-400 font-mono">{t('dataSourceManager.everyNMinutes', { count: autoRefreshInterval })}</span>
              )}
            </div>
            <button
              onClick={handleRefreshMarketData}
              disabled={marketRefreshing}
              className="flex items-center gap-1.5 px-3.5 py-1.5 rounded-lg bg-primary text-white text-[12px] font-semibold hover:bg-primary-hover transition-colors disabled:opacity-50 shadow-sm"
            >
              <span className={`material-symbols-outlined !text-[15px] ${marketRefreshing ? 'animate-spin' : ''}`}>
                {marketRefreshing ? 'progress_activity' : 'cloud_sync'}
              </span>
              {t('dataSourceManager.refreshNow')}
            </button>
          </div>
        </div>

        {/* Stats bar */}
        <div className="flex items-center gap-5 px-5 py-2 bg-slate-50/30 dark:bg-surface-dark/10 border-b border-slate-200 dark:border-border-dark text-[11px] font-mono tracking-wider text-slate-500 uppercase">
          <span className="flex items-center gap-1.5 text-slate-600 dark:text-slate-300">
            <span className="material-symbols-outlined !text-[14px] text-slate-400">schedule</span>
            {t('dataSourceManager.lastPriceRefresh')}
            <span className="text-slate-900 dark:text-white font-semibold ml-1">
              {marketData?.last_refresh
                ? relativeTime(t, marketData.last_refresh.timestamp ?? null).rel
                : t('dataSourceManager.never')}
            </span>
          </span>
          <span className="border-l border-slate-200 dark:border-slate-700 pl-5">
            {t('dataSourceManager.symbolsUpdated')}{' '}
            <span className="text-slate-900 dark:text-white font-semibold ml-1">
              {marketData?.last_refresh?.refreshed ?? 0}
            </span>
          </span>
          <span className="border-l border-slate-200 dark:border-slate-700 pl-5">
            {t('dataSourceManager.quoteAge')}{' '}
            <span className={`font-semibold ml-1 ${stalenessCls}`}>
              {stalenessLabelText(t, stalenessLabel)}
            </span>
          </span>
        </div>

        <div className="overflow-x-auto">
          <table className="w-full text-left border-collapse table-fixed">
            <colgroup>
              <col className="w-[22%]" />
              <col className="w-[38%]" />
              <col className="w-[16%]" />
              <col className="w-[12%]" />
              <col className="w-[12%]" />
            </colgroup>
            <thead>
              <tr className="text-[10px] font-bold text-slate-400 dark:text-slate-500 uppercase tracking-wider border-b border-slate-100 dark:border-slate-800/50">
                <th className="pl-5 pr-2 py-2.5">{t('dataSourceManager.col.fetcher')}</th>
                <th className="px-2 py-2.5">{t('dataSourceManager.col.coverage')}</th>
                <th className="px-2 py-2.5">{t('dataSourceManager.col.status')}</th>
                <th className="px-2 py-2.5 text-center">{t('dataSourceManager.col.market')}</th>
                <th className="px-2 py-2.5 text-right">{t('dataSourceManager.col.assetsTracked')}</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-100 dark:divide-slate-800/50">
              {providers.length === 0 ? (
                <tr>
                  <td colSpan={5} className="pl-5 pr-2 py-4 text-[12px] text-slate-400 italic">
                    {t('dataSourceManager.noActiveFetchers')}
                  </td>
                </tr>
              ) : (
                providers.map((p) => {
                  const display = fetcherDisplay(t)[p.fetcher] ?? { name: p.fetcher, coverage: p.market };
                  return (
                    <tr key={p.fetcher} className="hover:bg-slate-50/60 dark:hover:bg-slate-800/20 transition-colors">
                      <td className="pl-5 pr-2 py-3">
                        <span className="text-[13px] font-bold text-slate-800 dark:text-slate-100">{display.name}</span>
                      </td>
                      <td className="px-2 py-3">
                        <span className="text-[12px] text-slate-500 dark:text-slate-400">{display.coverage}</span>
                      </td>
                      <td className="px-2 py-3">
                        <span className="inline-flex items-center gap-1.5 px-2 py-0.5 rounded text-[10px] font-bold tracking-wide uppercase text-emerald-700 bg-emerald-50 dark:bg-emerald-900/20 dark:text-emerald-400 border border-emerald-200 dark:border-emerald-700/40">
                          <span className="w-1.5 h-1.5 rounded-full bg-emerald-500"></span>
                          {t('dataSourceManager.active')}
                        </span>
                      </td>
                      <td className="px-2 py-3 text-center">
                        <span className="font-mono text-[11px] px-2 py-0.5 rounded bg-slate-100 dark:bg-slate-800 text-slate-600 dark:text-slate-400">
                          {marketDisplay(t)[p.market] ?? p.market}
                        </span>
                      </td>
                      <td className="px-2 py-3 text-right">
                        <span className="text-[13px] font-semibold text-slate-700 dark:text-slate-200 tabular-nums">{p.asset_count}</span>
                      </td>
                    </tr>
                  );
                })
              )}
            </tbody>
          </table>
        </div>

        {/* Footer note */}
        <div className="px-5 py-2.5 border-t border-slate-100 dark:border-slate-800/50 bg-slate-50/30 dark:bg-surface-dark/10">
          <p className="text-[11px] text-slate-400 dark:text-slate-500">
            {t('dataSourceManager.fetchersFooterNote')}
          </p>
        </div>
      </div>
    </div>
  );
};
