import React, { useEffect, useMemo, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { api, ManagementAPI, OperationsAPI, SyncHistoryDetail, SyncHistoryRun } from '../src/services/api';
import { PipelinePanel } from '../components/operations';

interface ReaderPreview {
  reader: string;
  status: string;
  holdings_count: number;
  transactions_count: number;
  warnings: string[];
  new_assets?: string[];
  conflicts?: string[];
}

type Severity = 'success' | 'info' | 'warning' | 'error';

const parseIntegrityScore = (integrityResult: string): number => {
  const ratioMatch = integrityResult.match(/(\d+)\/(\d+)/);
  if (ratioMatch) {
    const passed = Number(ratioMatch[1]);
    const total = Number(ratioMatch[2]);
    return total > 0 ? (passed / total) * 100 : 0;
  }
  const numeric = Number(integrityResult);
  return Number.isFinite(numeric) ? numeric : 0;
};

const classifyLogMessage = (msg: string): Severity => {
  const lower = msg.toLowerCase();
  if (lower.includes('passed') || lower.includes('success') || lower.includes('completed')) return 'success';
  if (lower.includes('failed') || lower.includes('error') || lower.includes('exception')) return 'error';
  if (lower.includes('exceeded') || lower.includes('mismatch') || lower.includes('warning') || lower.includes('threshold')) return 'warning';
  return 'info';
};

const severityTone: Record<Severity, string> = {
  success: 'border-green-200 bg-green-50 text-green-700',
  info: 'border-blue-200 bg-blue-50 text-blue-700',
  warning: 'border-yellow-200 bg-yellow-50 text-yellow-700',
  error: 'border-red-200 bg-red-50 text-red-700',
};

const severityRowTone: Record<Severity, string> = {
  success: 'border-green-300 bg-green-50/50',
  info: 'border-blue-300 bg-blue-50/50',
  warning: 'border-yellow-300 bg-yellow-50/50',
  error: 'border-red-300 bg-red-50/50',
};

const severityIcon: Record<Severity, string> = {
  success: 'check_circle',
  info: 'info',
  warning: 'warning',
  error: 'cancel',
};

const severityIconTone: Record<Severity, string> = {
  success: 'text-green-600',
  info: 'text-blue-600',
  warning: 'text-yellow-700',
  error: 'text-red-600',
};

const asList = (value: unknown) => (Array.isArray(value) ? value : []);

export const ImportWorkbench: React.FC = () => {
  const { t } = useTranslation(['system', 'common']);
  const warningPreviewCount = 4;
  const [runs, setRuns] = useState<SyncHistoryRun[]>([]);
  const [selectedRun, setSelectedRun] = useState<SyncHistoryDetail | null>(null);
  const [activeFilter, setActiveFilter] = useState<'meaningful' | 'all' | 'no_change' | 'failed'>('meaningful');
  const [loading, setLoading] = useState(true);
  const [previewing, setPreviewing] = useState(false);
  const [syncingNow, setSyncingNow] = useState(false);
  const [previewRows, setPreviewRows] = useState<ReaderPreview[]>([]);
  const [expandedPreviewDetails, setExpandedPreviewDetails] = useState<Record<string, boolean>>({});
  const [syncMessage, setSyncMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [showAllWarnings, setShowAllWarnings] = useState(false);
  const [showSyncLog, setShowSyncLog] = useState(false);

  const loadRuns = async (filter: 'meaningful' | 'all' | 'no_change' | 'failed' = 'meaningful') => {
    setLoading(true);
    setError(null);
    try {
      const apiFilter = filter === 'failed' ? 'all' : filter;
      const history = await OperationsAPI.getSyncHistory(20, apiFilter);
      setRuns(history.runs || []);
      if (history.runs?.[0]) {
        const detail = await OperationsAPI.getSyncHistoryDetail(history.runs[0].id);
        setSelectedRun(detail);
        setShowAllWarnings(false);
        setShowSyncLog(false);
      } else {
        setSelectedRun(null);
        setShowAllWarnings(false);
        setShowSyncLog(false);
      }
    } catch (err) {
      console.error(err);
      setError(t('importWorkbench.loadError'));
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    loadRuns(activeFilter);
  }, [activeFilter]);

  const openRun = async (runId: string) => {
    try {
      const detail = await OperationsAPI.getSyncHistoryDetail(runId);
      setSelectedRun(detail);
      setShowAllWarnings(false);
      setShowSyncLog(false);
    } catch (err) {
      console.error(err);
    }
  };

  const runPreview = async () => {
    setPreviewing(true);
    try {
      const response = await ManagementAPI.previewImports();
      setPreviewRows((response.readers || []) as ReaderPreview[]);
    } catch (err) {
      console.error(err);
    } finally {
      setPreviewing(false);
    }
  };

  const startSyncNow = async () => {
    setSyncingNow(true);
    setSyncMessage(null);
    setError(null);
    try {
      await api.startSync();
      await new Promise((resolve) => setTimeout(resolve, 3500));
      await loadRuns(activeFilter);
      setSyncMessage(t('importWorkbench.syncStartedMsg'));
    } catch (err) {
      console.error(err);
      setError(t('importWorkbench.syncStartError'));
    } finally {
      setSyncingNow(false);
    }
  };

  const filteredRuns = useMemo(() => runs.filter((run) => {
    if (activeFilter === 'failed') return run.alert || run.warning_count > 0;
    return true; // meaningful, all, no_change are filtered server-side
  }), [runs, activeFilter]);

  const selectedRunSources = asList(selectedRun?.sources_affected);
  const selectedRunReaderCounts = selectedRun?.reader_counts || {};
  const selectedRunWarnings = selectedRun?.warnings || [];
  const selectedRunInfoMessages = selectedRun?.info_messages || [];
  const selectedRunChecks = asList(selectedRun?.integrity_checks) as Array<{ passed?: boolean }>;
  const checksPassed = selectedRunChecks.filter((item) => !!item.passed).length;
  const detailIntegrityStatus = selectedRun?.integrity_status ?? (checksPassed < (selectedRunChecks.length || 1) ? 'failed' : 'ok');
  const showingAllWarnings = showAllWarnings || selectedRunWarnings.length <= warningPreviewCount;
  const visibleWarnings = showingAllWarnings ? selectedRunWarnings : selectedRunWarnings.slice(0, warningPreviewCount);

  return (
    <div className="min-h-screen bg-gray-50 p-6 font-sans md:p-8">
      <div className="mx-auto max-w-[1360px] space-y-6">
        <header className="rounded-lg border border-gray-200 bg-white shadow-sm">
          <div className="flex flex-wrap items-center justify-between gap-3 border-b border-gray-200 px-6 py-4">
            <p className="text-sm text-gray-500">{t('common:section.operations')} <span className="px-1">›</span> {t('importWorkbench.title')}</p>
          </div>

          <div className="flex flex-wrap items-start justify-between gap-4 px-6 py-5">
            <div>
              <h1 className="text-2xl font-bold text-gray-900">{t('importWorkbench.title')}</h1>
              <p className="mt-1 text-sm text-gray-600">{t('importWorkbench.subtitle')}</p>
            </div>
            <div className="flex items-center gap-2">
              <button
                onClick={runPreview}
                disabled={previewing}
                className="rounded-lg bg-blue-500 px-4 py-2 text-sm font-semibold text-white hover:bg-blue-600 disabled:opacity-60"
              >
                {previewing ? t('importWorkbench.runningEllipsis') : t('importWorkbench.runPreview')}
              </button>
              <button
                onClick={loadRuns}
                className="rounded-lg border border-gray-200 bg-white px-4 py-2 text-sm font-semibold text-gray-700 hover:bg-gray-50"
              >
                {t('importWorkbench.refreshRuns')}
              </button>
              <button
                onClick={startSyncNow}
                disabled={syncingNow}
                className="rounded-lg border border-blue-200 bg-blue-50 px-4 py-2 text-sm font-semibold text-blue-700 hover:bg-blue-100 disabled:opacity-60"
              >
                {syncingNow ? t('importWorkbench.syncInProgressEllipsis') : t('importWorkbench.syncNow')}
              </button>
            </div>
          </div>
        </header>

        {error && <div className="rounded-lg border border-red-200 bg-red-50 p-4 text-sm text-red-700">{error}</div>}
        {syncMessage && <div className="rounded-lg border border-green-200 bg-green-50 p-4 text-sm text-green-700">{syncMessage}</div>}

        <PipelinePanel />

        {loading && <div className="rounded-lg border border-gray-200 bg-white p-6 text-sm text-gray-600 shadow-sm">{t('importWorkbench.loadingSyncHistory')}</div>}

        {!loading && (
          <section className="grid grid-cols-1 gap-4 xl:grid-cols-[minmax(0,1fr)_560px]">
            <div className="overflow-hidden rounded-lg border border-gray-200 bg-white shadow-sm">
              <div className="flex flex-wrap items-center justify-between gap-3 border-b border-gray-200 px-6 py-4">
                <div className="flex items-center gap-2 rounded-lg bg-gray-100 p-1 text-sm font-semibold">
                  <button onClick={() => setActiveFilter('meaningful')} className={`rounded-md px-3 py-1.5 ${activeFilter === 'meaningful' ? 'bg-white text-blue-600 shadow-sm' : 'text-gray-600'}`}>{t('importWorkbench.filter.meaningful')}</button>
                  <button onClick={() => setActiveFilter('all')} className={`rounded-md px-3 py-1.5 ${activeFilter === 'all' ? 'bg-white text-blue-600 shadow-sm' : 'text-gray-600'}`}>{t('importWorkbench.filter.allRuns')}</button>
                  <button onClick={() => setActiveFilter('no_change')} className={`rounded-md px-3 py-1.5 ${activeFilter === 'no_change' ? 'bg-white text-blue-600 shadow-sm' : 'text-gray-600'}`}>{t('importWorkbench.filter.noChange')}</button>
                  <button onClick={() => setActiveFilter('failed')} className={`rounded-md px-3 py-1.5 ${activeFilter === 'failed' ? 'bg-white text-blue-600 shadow-sm' : 'text-gray-600'}`}>{t('importWorkbench.filter.failed')}</button>
                </div>
                <span className="text-sm text-gray-500">{t('importWorkbench.runsCount', { count: filteredRuns.length })}</span>
              </div>

              <div className="overflow-x-auto">
                <table className="w-full min-w-[720px] text-left">
                  <thead className="bg-gray-50 text-xs font-semibold uppercase tracking-[0.08em] text-gray-500">
                    <tr>
                      <th className="w-[32%] px-6 py-3 whitespace-nowrap">{t('importWorkbench.col.dateTime')}</th>
                      <th className="w-[1%] px-6 py-3 whitespace-nowrap">{t('importWorkbench.col.netWorthDelta')}</th>
                      <th className="w-[1%] px-6 py-3 whitespace-nowrap">{t('importWorkbench.col.integrity')}</th>
                      <th className="w-[1%] px-6 py-3 whitespace-nowrap">{t('importWorkbench.col.status')}</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-gray-200 text-sm text-gray-700">
                    {filteredRuns.map((run) => {
                      const integrity = parseIntegrityScore(run.integrity_result);
                      const deltaValue = run.net_worth_delta || 0;
                      const isWarning = run.alert || run.warning_count > 0;
                      const isNoChange = run.is_no_change && !isWarning;
                      // integrity_status may be absent on legacy rows — derive from score as fallback.
                      const integrityStatus = run.integrity_status ?? (integrity < 100 ? 'failed' : 'ok');
                      const integrityBarColor =
                        integrityStatus === 'failed' ? 'bg-red-500' :
                        integrityStatus === 'degraded' ? 'bg-amber-400' :
                        'bg-blue-500';
                      return (
                        <tr key={run.id} onClick={() => openRun(run.id)} className={`cursor-pointer hover:bg-gray-50 ${isNoChange ? 'opacity-60' : ''}`}>
                          <td className="w-[32%] px-6 py-3">
                            <p className="font-medium text-gray-900">{run.timestamp ? run.timestamp.slice(0, 10) : t('importWorkbench.unknown')}</p>
                            <p className="text-sm text-gray-500">{run.timestamp ? run.timestamp.slice(11, 16) : t('importWorkbench.notAvailable')}</p>
                          </td>
                          <td className="w-[1%] px-6 py-3 whitespace-nowrap">
                            <span className={`rounded-full px-2.5 py-0.5 text-xs font-semibold ${deltaValue >= 0 ? 'bg-green-100 text-green-700' : 'bg-red-100 text-red-700'}`}>
                              {deltaValue >= 0 ? '+' : ''}{deltaValue.toFixed(2)}%
                            </span>
                          </td>
                          <td className="w-[1%] px-6 py-3 whitespace-nowrap">
                            <div className="flex items-center gap-2">
                              <div className="h-2 w-20 overflow-hidden rounded-full bg-gray-200">
                                <div className={`h-full ${integrityBarColor}`} style={{ width: `${Math.max(0, Math.min(100, integrity))}%` }} />
                              </div>
                              <span className={`font-medium ${integrityStatus === 'failed' ? 'text-red-600' : integrityStatus === 'degraded' ? 'text-amber-600' : 'text-gray-700'}`}>
                                {run.integrity_result}
                                {integrityStatus === 'degraded' && <span className="ml-1 text-[10px] font-semibold uppercase tracking-wide text-amber-500">{t('importWorkbench.advisory')}</span>}
                              </span>
                            </div>
                          </td>
                          <td className="w-[1%] px-6 py-3 whitespace-nowrap">
                            {isWarning ? (
                              <span className="inline-flex items-center gap-1 rounded-full bg-red-100 px-2.5 py-0.5 text-xs font-semibold text-red-700">
                                <span className="material-symbols-outlined text-[15px]">warning</span>
                                {t('importWorkbench.status.warning')}
                              </span>
                            ) : isNoChange ? (
                              <span className="inline-flex items-center gap-1 rounded-full bg-gray-100 px-2.5 py-0.5 text-xs font-semibold text-gray-500">
                                <span className="material-symbols-outlined text-[15px]">remove_circle</span>
                                {t('importWorkbench.status.noChange')}
                              </span>
                            ) : (
                              <span className="inline-flex items-center gap-1 rounded-full bg-green-100 px-2.5 py-0.5 text-xs font-semibold text-green-700">
                                <span className="material-symbols-outlined text-[15px]">check_circle</span>
                                {t('importWorkbench.status.completed')}
                              </span>
                            )}
                          </td>
                        </tr>
                      );
                    })}

                    {filteredRuns.length === 0 && (
                      <tr>
                        <td colSpan={4} className="px-6 py-12 text-center text-sm text-gray-500">{t('importWorkbench.noRunsForFilter')}</td>
                      </tr>
                    )}
                  </tbody>
                </table>
              </div>
            </div>

            <aside className="space-y-4">
              <article className="rounded-lg border border-gray-200 bg-white p-6 shadow-sm">
                <div className="flex items-center justify-between">
                  <h2 className="text-lg font-semibold text-gray-800">{t('importWorkbench.runDetails')}</h2>
                </div>

                {!selectedRun && <p className="mt-4 text-sm text-gray-500">{t('importWorkbench.selectRunHint')}</p>}

                {selectedRun && (
                  <div className="mt-4 space-y-5">
                    <div className="rounded-lg border border-gray-200 bg-gray-50 p-4">
                      <div className="flex items-center justify-between text-sm">
                        <span className="text-gray-500">{t('importWorkbench.source')}</span>
                        <span className="font-semibold text-gray-900">{selectedRun.type || 'sync'}</span>
                      </div>
                      <div className="mt-2 flex items-center justify-between text-sm">
                        <span className="text-gray-500">{t('importWorkbench.integrity')}</span>
                        <span className={`font-semibold ${detailIntegrityStatus === 'failed' ? 'text-red-600' : detailIntegrityStatus === 'degraded' ? 'text-amber-600' : 'text-gray-800'}`}>
                          {t('importWorkbench.checksPassed', { passed: checksPassed, total: selectedRunChecks.length || 0 })}
                          {detailIntegrityStatus === 'degraded' && <span className="ml-1 text-[10px] font-semibold uppercase tracking-wide text-amber-500">{t('importWorkbench.advisory')}</span>}
                        </span>
                      </div>
                    </div>

                    {selectedRunSources.length > 0 && (
                      <div>
                        <p className="text-sm font-medium text-gray-500">{t('importWorkbench.sourcesAffected')}</p>
                        <div className="mt-2 flex flex-wrap gap-2">
                          {selectedRunSources.map((source) => (
                            <span key={String(source)} className="rounded-full bg-blue-100 px-2 py-0.5 text-xs font-semibold text-blue-700">
                              {String(source)}
                            </span>
                          ))}
                        </div>
                      </div>
                    )}

                    <div className="grid grid-cols-2 gap-2">
                      <div className="rounded-lg border border-gray-200 p-3">
                        <p className="text-sm font-medium text-gray-500">{t('importWorkbench.beforeSync')}</p>
                        <p className="mt-1 text-xl font-bold tracking-tight text-gray-900">{selectedRun.net_worth_before.toLocaleString()}</p>
                      </div>
                      <div className="rounded-lg border border-gray-200 p-3">
                        <p className="text-sm font-medium text-gray-500">{t('importWorkbench.afterSync')}</p>
                        <p className="mt-1 text-xl font-bold tracking-tight text-blue-600">{selectedRun.net_worth_after.toLocaleString()}</p>
                      </div>
                    </div>

                    {Object.keys(selectedRunReaderCounts).length > 0 && (
                      <div className="rounded-lg border border-gray-200 p-3">
                        <p className="text-sm font-medium text-gray-500">{t('importWorkbench.readerCounts')}</p>
                        <p className="mt-1 text-xs text-gray-500">{t('importWorkbench.readersReported', { count: Object.keys(selectedRunReaderCounts).length })}</p>
                        <div className="mt-2 max-h-40 overflow-auto">
                          <table className="w-full text-left text-xs">
                            <thead className="sticky top-0 bg-white text-gray-500">
                              <tr>
                                <th className="py-1 pr-2">{t('importWorkbench.col.reader')}</th>
                                <th className="py-1 text-right">{t('importWorkbench.col.read')}</th>
                                <th className="py-1 text-right">{t('importWorkbench.col.inserted')}</th>
                              </tr>
                            </thead>
                            <tbody className="divide-y divide-gray-200 text-gray-700">
                              {Object.entries(selectedRunReaderCounts).map(([reader, value]) => (
                                <tr key={reader}>
                                  <td className="py-1 pr-2">{reader}</td>
                                  <td className="py-1 text-right">{Number((value as { read?: number }).read || 0)}</td>
                                  <td className="py-1 text-right">{Number((value as { inserted?: number }).inserted || 0)}</td>
                                </tr>
                              ))}
                            </tbody>
                          </table>
                        </div>
                      </div>
                    )}

                    <div className="rounded-lg border border-gray-200 p-3">
                      <div className="flex items-center justify-between gap-3">
                        <p className="text-base font-semibold text-gray-800">{t('importWorkbench.warningLog', { count: selectedRunWarnings.length })}</p>
                        {selectedRunWarnings.length > warningPreviewCount && (
                          <button
                            onClick={() => setShowAllWarnings((prev) => !prev)}
                            className="rounded border border-gray-300 bg-white px-2 py-1 text-xs font-semibold text-gray-700 hover:bg-gray-50"
                          >
                            {showingAllWarnings ? t('importWorkbench.showLess') : t('importWorkbench.showAll', { count: selectedRunWarnings.length })}
                          </button>
                        )}
                      </div>
                      {selectedRunWarnings.length > 0 && (
                        <p className="mt-1 text-sm text-gray-500">
                          {showingAllWarnings
                            ? t('importWorkbench.showingAllEntries', { count: selectedRunWarnings.length })
                            : t('importWorkbench.showingSomeEntries', { shown: visibleWarnings.length, total: selectedRunWarnings.length })}
                        </p>
                      )}
                      <div className={`mt-3 space-y-2 ${showingAllWarnings ? 'max-h-72 overflow-y-auto pr-1' : ''}`}>
                        {selectedRunWarnings.length === 0 && (
                          <div className="rounded-lg border border-green-200 bg-green-50 px-3 py-2 text-sm text-green-700">{t('importWorkbench.noWarnings')}</div>
                        )}
                        {visibleWarnings.map((warning, idx) => {
                          const severity = classifyLogMessage(warning);
                          return (
                            <div key={`${warning}-${idx}`} className={`rounded-md border-l-4 px-3 py-2 ${severityRowTone[severity]}`}>
                              <span className="inline-flex items-start gap-1.5">
                                <span className={`material-symbols-outlined text-[15px] ${severityIconTone[severity]}`}>{severityIcon[severity]}</span>
                                <span className="text-[13px] leading-6 text-gray-800">{warning}</span>
                              </span>
                            </div>
                          );
                        })}
                      </div>
                    </div>

                    {selectedRunInfoMessages.length > 0 && (
                      <div className="rounded-lg border border-gray-200 p-3">
                        <button
                          onClick={() => setShowSyncLog((prev) => !prev)}
                          className="flex w-full items-center justify-between text-left"
                        >
                          <p className="text-base font-semibold text-gray-800">{t('importWorkbench.syncLog', { count: selectedRunInfoMessages.length })}</p>
                          <span className="material-symbols-outlined text-sm text-gray-400">{showSyncLog ? 'expand_less' : 'expand_more'}</span>
                        </button>
                        {showSyncLog && (
                          <div className="mt-3 max-h-60 space-y-1.5 overflow-y-auto pr-1">
                            {selectedRunInfoMessages.map((msg, idx) => (
                              <div key={idx} className="rounded-md border-l-4 border-blue-300 bg-blue-50 px-3 py-1.5">
                                <span className="text-[13px] leading-5 text-blue-800">{msg}</span>
                              </div>
                            ))}
                          </div>
                        )}
                      </div>
                    )}
                  </div>
                )}
              </article>
            </aside>
          </section>
        )}

        {previewRows.length > 0 && (
          <section className="rounded-lg border border-gray-200 bg-white p-6 shadow-sm">
            <h2 className="text-lg font-semibold text-gray-800">{t('importWorkbench.runPreviewSnapshot')}</h2>
            <div className="mt-4 grid grid-cols-1 gap-3 md:grid-cols-2 lg:grid-cols-3">
              {previewRows.map((reader) => {
                const previewWarnings = reader.warnings || [];
                const previewNewAssets = reader.new_assets || [];
                const previewConflicts = reader.conflicts || [];
                const key = reader.reader;
                return (
                  <div key={reader.reader} className="rounded-lg border border-gray-200 bg-gray-50 p-4">
                    <p className="text-sm font-semibold text-gray-900">{reader.reader}</p>
                    <p className="mt-1 text-sm text-gray-600">{t('importWorkbench.holdingsTransactions', { holdings: reader.holdings_count, transactions: reader.transactions_count })}</p>
                    <p className={`mt-2 text-sm font-semibold ${reader.status === 'ok' ? 'text-green-600' : 'text-yellow-600'}`}>
                      {reader.status}
                    </p>

                    <div className="mt-3 flex flex-wrap gap-2">
                      <button
                        onClick={() => setExpandedPreviewDetails((prev) => ({ ...prev, [`${key}:warnings`]: !prev[`${key}:warnings`] }))}
                        className="rounded-full bg-yellow-100 px-2 py-0.5 text-xs font-semibold text-yellow-700"
                      >
                        {t('importWorkbench.warningsCount', { count: previewWarnings.length })}
                      </button>
                      <button
                        onClick={() => setExpandedPreviewDetails((prev) => ({ ...prev, [`${key}:new`]: !prev[`${key}:new`] }))}
                        className="rounded-full bg-blue-100 px-2 py-0.5 text-xs font-semibold text-blue-700"
                      >
                        {t('importWorkbench.newAssetsCount', { count: previewNewAssets.length })}
                      </button>
                      <button
                        onClick={() => setExpandedPreviewDetails((prev) => ({ ...prev, [`${key}:conflicts`]: !prev[`${key}:conflicts`] }))}
                        className="rounded-full bg-red-100 px-2 py-0.5 text-xs font-semibold text-red-700"
                      >
                        {t('importWorkbench.conflictsCount', { count: previewConflicts.length })}
                      </button>
                    </div>

                    {expandedPreviewDetails[`${key}:warnings`] && previewWarnings.length > 0 && (
                      <ul className="mt-2 list-disc space-y-1 pl-5 text-xs text-yellow-800">
                        {previewWarnings.map((item, idx) => <li key={`${key}-w-${idx}`}>{item}</li>)}
                      </ul>
                    )}
                    {expandedPreviewDetails[`${key}:new`] && previewNewAssets.length > 0 && (
                      <ul className="mt-2 list-disc space-y-1 pl-5 text-xs text-blue-800">
                        {previewNewAssets.map((item, idx) => <li key={`${key}-n-${idx}`}>{item}</li>)}
                      </ul>
                    )}
                    {expandedPreviewDetails[`${key}:conflicts`] && previewConflicts.length > 0 && (
                      <ul className="mt-2 list-disc space-y-1 pl-5 text-xs text-red-800">
                        {previewConflicts.map((item, idx) => <li key={`${key}-c-${idx}`}>{item}</li>)}
                      </ul>
                    )}
                  </div>
                );
              })}
            </div>
          </section>
        )}
      </div>
    </div>
  );
};
