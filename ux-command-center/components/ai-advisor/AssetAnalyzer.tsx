import React, { useEffect, useRef, useState, Fragment } from 'react';
import { useTranslation } from 'react-i18next';
import {
  type AnalysisHistoryItem,
  type AnalysisResult,
  type AnalyzableAssetSearchResult,
  analyzeAsset,
  getAnalysisById,
  getAnalysisHistory,
  searchAnalyzableAssets,
} from '../../src/services/api';
import { TechnicalDashboard } from './TechnicalDashboard';

/** Lightweight inline markdown renderer for AI analysis output. */
function renderInlineMarkdown(text: string): React.ReactNode {
  const parts = text.split(/(\*\*[^*]+\*\*)/g);
  return parts.map((part, i) => {
    if (part.startsWith('**') && part.endsWith('**')) {
      return <strong key={i} className="font-semibold text-slate-800 dark:text-slate-100">{part.slice(2, -2)}</strong>;
    }
    return <Fragment key={i}>{part}</Fragment>;
  });
}

function renderMarkdown(markdown: string): React.ReactNode {
  const lines = markdown.split('\n');
  const nodes: React.ReactNode[] = [];
  let i = 0;
  while (i < lines.length) {
    const line = lines[i];
    if (line.startsWith('# ')) {
      nodes.push(<h1 key={i} className="text-base font-bold text-slate-800 dark:text-slate-100 mt-4 mb-1">{line.slice(2)}</h1>);
    } else if (line.startsWith('## ')) {
      nodes.push(<h2 key={i} className="text-sm font-semibold text-slate-700 dark:text-slate-200 mt-3 mb-1 border-b border-slate-100 dark:border-slate-700 pb-0.5">{line.slice(3)}</h2>);
    } else if (line.startsWith('### ')) {
      nodes.push(<h3 key={i} className="text-xs font-semibold text-slate-600 dark:text-slate-300 mt-2 mb-0.5 uppercase tracking-wide">{line.slice(4)}</h3>);
    } else if (line.startsWith('- ') || line.startsWith('* ')) {
      nodes.push(
        <div key={i} className="flex items-start gap-1.5 text-sm text-slate-700 dark:text-slate-300 leading-relaxed">
          <span className="text-slate-400 shrink-0 mt-0.5">•</span>
          <span>{renderInlineMarkdown(line.slice(2))}</span>
        </div>
      );
    } else if (line.trim() === '') {
      nodes.push(<div key={i} className="h-2" />);
    } else {
      nodes.push(<p key={i} className="text-sm text-slate-700 dark:text-slate-300 leading-relaxed">{renderInlineMarkdown(line)}</p>);
    }
    i++;
  }
  return nodes;
}

export const AssetAnalyzer: React.FC = () => {
  const { t } = useTranslation('aiAdvisor');
  const [query, setQuery] = useState('');
  const [searchResults, setSearchResults] = useState<AnalyzableAssetSearchResult[]>([]);
  const [selectedAsset, setSelectedAsset] = useState<AnalyzableAssetSearchResult | null>(null);
  const [analyzing, setAnalyzing] = useState(false);
  const [result, setResult] = useState<AnalysisResult | null>(null);
  const [historyItems, setHistoryItems] = useState<AnalysisHistoryItem[]>([]);
  const [historyLoading, setHistoryLoading] = useState(false);
  const [recentHistory, setRecentHistory] = useState<AnalysisHistoryItem[]>([]);
  const [recentLoading, setRecentLoading] = useState(true);
  const [loadingHistoryId, setLoadingHistoryId] = useState<number | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [errorIsWarning, setErrorIsWarning] = useState(false);
  const [dropdownOpen, setDropdownOpen] = useState(false);

  const debounceRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const abortRef = useRef<AbortController | null>(null);
  const isMountedRef = useRef(true);

  // Track mount state for async guard
  useEffect(() => {
    isMountedRef.current = true;
    return () => { isMountedRef.current = false; };
  }, []);

  // Load global recent history on mount
  useEffect(() => {
    getAnalysisHistory(undefined, 20)
      .then((items) => { if (isMountedRef.current) setRecentHistory(items); })
      .catch(() => { /* non-fatal */ })
      .finally(() => { if (isMountedRef.current) setRecentLoading(false); });
  }, []);

  // Debounced search — skip when asset already selected (prevents re-opening dropdown after selection)
  useEffect(() => {
    if (debounceRef.current) clearTimeout(debounceRef.current);
    if (query.length < 2 || selectedAsset) {
      setSearchResults([]);
      setDropdownOpen(false);
      return;
    }
    debounceRef.current = setTimeout(async () => {
      if (abortRef.current) abortRef.current.abort();
      abortRef.current = new AbortController();
      try {
        const results = await searchAnalyzableAssets(query, abortRef.current.signal);
        if (!isMountedRef.current) return;
        setSearchResults(results);
        setDropdownOpen(results.length > 0);
      } catch (e) {
        if (e instanceof DOMException && e.name === 'AbortError') return;
        if (!isMountedRef.current) return;
        setSearchResults([]);
        setError(e instanceof Error ? e.message : t('assetAnalyzer.errors.searchFailed'));
      }
    }, 300);

    return () => {
      if (debounceRef.current) clearTimeout(debounceRef.current);
      abortRef.current?.abort();
    };
  }, [query, selectedAsset]);

  const loadHistory = async (code: string) => {
    setHistoryLoading(true);
    try {
      const items = await getAnalysisHistory(code, 10);
      if (!isMountedRef.current) return;
      setHistoryItems(items);
    } catch {
      if (!isMountedRef.current) return;
      setHistoryItems([]);
      setError(t('assetAnalyzer.errors.loadHistoryFailed'));
    } finally {
      if (!isMountedRef.current) return;
      setHistoryLoading(false);
    }
  };

  const handleSelectAsset = (asset: AnalyzableAssetSearchResult) => {
    setSelectedAsset(asset);
    setQuery(asset.name ? `${asset.name} (${asset.code})` : asset.code);
    setSearchResults([]);
    setDropdownOpen(false);
    setResult(null);
    setError(null);
    setErrorIsWarning(false);
  };

  const handleAnalyze = async () => {
    if (!selectedAsset || analyzing) return;
    setAnalyzing(true);
    setError(null);
    setErrorIsWarning(false);
    try {
      const analysisResult = await analyzeAsset(selectedAsset.code);
      if (!isMountedRef.current) return;
      setResult(analysisResult);
      await loadHistory(selectedAsset.code);
      // Refresh global recent list so new analysis appears immediately
      getAnalysisHistory(undefined, 20).then((items) => {
        if (isMountedRef.current) setRecentHistory(items);
      }).catch(() => { /* non-fatal */ });
    } catch (e) {
      if (!isMountedRef.current) return;
      const msg = e instanceof Error ? e.message : t('assetAnalyzer.errors.analysisFailed');
      const isTimeout = msg.toLowerCase().includes('timed out') || msg.includes('504');
      if (isTimeout) {
        setErrorIsWarning(true);
        setError(t('assetAnalyzer.errors.stillRunning'));
        // Poll history up to 4 times (every 20s) so the result appears automatically when ready.
        const code = selectedAsset?.code;
        if (code) {
          let attempts = 0;
          const poll = setInterval(async () => {
            attempts++;
            try {
              const items = await getAnalysisHistory(code, 5);
              if (!isMountedRef.current) { clearInterval(poll); return; }
              if (items.length > 0) {
                setRecentHistory(prev => {
                  const ids = new Set(prev.map(x => x.id));
                  const fresh = items.filter(x => !ids.has(x.id));
                  return fresh.length > 0 ? [...fresh, ...prev] : prev;
                });
                setError(t('assetAnalyzer.errors.analysisComplete'));
                clearInterval(poll);
              }
            } catch { /* non-fatal */ }
            if (attempts >= 4) clearInterval(poll);
          }, 20000);
        }
      } else {
        setError(msg);
      }
    } finally {
      if (!isMountedRef.current) return;
      setAnalyzing(false);
    }
  };

  const handleLoadHistoryItem = async (item: AnalysisHistoryItem) => {
    if (loadingHistoryId != null) return;
    setLoadingHistoryId(item.id);
    setError(null);
    try {
      const full = await getAnalysisById(item.id);
      if (!isMountedRef.current) return;
      setResult(full);
      setSelectedAsset({ code: item.asset_code, name: item.asset_name ?? null, in_portfolio: false, position_pct: null });
      setQuery(item.asset_name ? `${item.asset_name} (${item.asset_code})` : item.asset_code);
    } catch (e) {
      if (!isMountedRef.current) return;
      setError(e instanceof Error ? e.message : t('assetAnalyzer.errors.loadAnalysisFailed'));
    } finally {
      if (isMountedRef.current) setLoadingHistoryId(null);
    }
  };

  const SIGNAL_COLORS: Record<string, string> = {
    BUY: 'bg-emerald-100 text-emerald-700 dark:bg-emerald-900/30 dark:text-emerald-400',
    STRONG_BUY: 'bg-emerald-100 text-emerald-700 dark:bg-emerald-900/30 dark:text-emerald-400',
    SELL: 'bg-red-100 text-red-700 dark:bg-red-900/30 dark:text-red-400',
    STRONG_SELL: 'bg-red-100 text-red-700 dark:bg-red-900/30 dark:text-red-400',
    HOLD: 'bg-slate-100 text-slate-600 dark:bg-slate-800 dark:text-slate-300',
    WAIT: 'bg-amber-100 text-amber-700 dark:bg-amber-900/30 dark:text-amber-400',
    TRIM: 'bg-orange-100 text-orange-700 dark:bg-orange-900/30 dark:text-orange-400',
  };
  const DEFAULT_SIGNAL_COLOR = 'bg-slate-100 text-slate-600 dark:bg-slate-800 dark:text-slate-300';

  return (
    <div className="space-y-4 w-full">
      <div className="rounded-xl border border-slate-200 dark:border-border-dark bg-white dark:bg-card-dark p-4">
        <h2 className="text-sm font-semibold text-slate-800 dark:text-slate-100 mb-3">
          {t('assetAnalyzer.title')}
        </h2>

        {/* Search + Analyze row */}
        <div className="flex gap-2">
          <div className="relative flex-1">
            <input
              type="text"
              value={query}
              onChange={(e) => {
                setQuery(e.target.value);
                setSelectedAsset(null);
                setResult(null);
              }}
              placeholder={t('assetAnalyzer.searchPlaceholder')}
              className="w-full rounded-lg border border-slate-200 dark:border-border-dark bg-slate-50 dark:bg-slate-900 px-3 py-2 text-sm text-slate-800 dark:text-slate-100 placeholder-slate-400 focus:outline-none focus:ring-2 focus:ring-primary/50"
            />
            {dropdownOpen && searchResults.length > 0 && (
              <div className="absolute top-full mt-1 left-0 right-0 z-20 bg-white dark:bg-card-dark border border-slate-200 dark:border-border-dark rounded-lg shadow-lg overflow-hidden">
                {searchResults.map((asset) => (
                  <button
                    key={asset.code}
                    type="button"
                    onClick={() => handleSelectAsset(asset)}
                    className="w-full text-left px-3 py-2 hover:bg-slate-50 dark:hover:bg-slate-800 transition-colors flex items-center justify-between"
                  >
                    <div>
                      <span className="text-sm text-slate-800 dark:text-slate-100 font-medium">
                        {asset.name ?? asset.code}
                      </span>
                      <span className="text-xs text-slate-400 ml-2">{asset.code}</span>
                    </div>
                    {asset.in_portfolio && (
                      <span className="text-[10px] px-1.5 py-0.5 rounded bg-primary/10 text-primary font-medium">
                        {t('assetAnalyzer.inPortfolio')}
                        {asset.position_pct != null
                          ? ` ${(asset.position_pct * 100).toFixed(1)}%`
                          : ''}
                      </span>
                    )}
                  </button>
                ))}
              </div>
            )}
          </div>
          <button
            type="button"
            onClick={handleAnalyze}
            disabled={!selectedAsset || analyzing}
            className="flex items-center gap-2 px-4 py-2 bg-primary text-white rounded-lg text-sm font-medium hover:bg-primary/90 disabled:opacity-50 transition-colors whitespace-nowrap"
          >
            {analyzing ? (
              <>
                <svg className="animate-spin h-4 w-4" viewBox="0 0 24 24" fill="none">
                  <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
                  <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8v8H4z" />
                </svg>
                {t('assetAnalyzer.analyzing')}
              </>
            ) : (
              <>
                <span className="material-symbols-outlined !text-[16px]">analytics</span>
                {t('assetAnalyzer.analyze')}
              </>
            )}
          </button>
        </div>

        {/* Error */}
        {error && (
          <div className={`mt-3 flex items-center gap-2 p-3 rounded-lg text-sm border ${errorIsWarning ? 'bg-amber-50 dark:bg-amber-900/20 border-amber-200 dark:border-amber-700 text-amber-700 dark:text-amber-400' : 'bg-red-50 dark:bg-red-900/20 border-red-200 dark:border-red-800 text-red-600 dark:text-red-400'}`}>
            <span className="material-symbols-outlined !text-[16px]">{errorIsWarning ? 'schedule' : 'error'}</span>
            {error}
          </div>
        )}
      </div>

      {/* Recent history panel — shown when no active result */}
      {!result && (
        <div className="rounded-xl border border-slate-200 dark:border-border-dark bg-white dark:bg-card-dark p-4">
          <h3 className="text-xs font-semibold text-slate-500 dark:text-slate-400 uppercase tracking-wide mb-3">
            {t('assetAnalyzer.recentAnalyses')}
          </h3>
          {recentLoading ? (
            <p className="text-xs text-slate-400">{t('assetAnalyzer.loadingEllipsis')}</p>
          ) : recentHistory.length === 0 ? (
            <p className="text-xs text-slate-400">{t('assetAnalyzer.noPreviousAnalyses')}</p>
          ) : (
            <div className="divide-y divide-slate-100 dark:divide-slate-700">
              {recentHistory.map((item) => {
                const signalKey = (item.timing_signal ?? '').toUpperCase();
                const signalColor = SIGNAL_COLORS[signalKey] ?? DEFAULT_SIGNAL_COLOR;
                return (
                  <button
                    key={item.id}
                    type="button"
                    onClick={() => handleLoadHistoryItem(item)}
                    disabled={loadingHistoryId != null}
                    className="w-full text-left flex items-center gap-3 py-2.5 hover:bg-slate-50 dark:hover:bg-slate-800/50 transition-colors px-1 rounded disabled:opacity-50"
                  >
                    <div className="flex-1 min-w-0">
                      <span className="text-sm font-medium text-slate-800 dark:text-slate-100 truncate block">
                        {item.asset_name ?? item.asset_code}
                      </span>
                      <span className="text-xs text-slate-400">
                        {item.asset_name ? `${item.asset_code} · ` : ''}{new Date(item.created_at).toLocaleString()}
                      </span>
                    </div>
                    {item.timing_signal && (
                      <span className={`text-[10px] px-2 py-0.5 rounded-full font-semibold shrink-0 ${signalColor}`}>
                        {item.timing_signal}
                      </span>
                    )}
                    {item.confidence != null && (
                      <span className="text-xs text-slate-400 shrink-0">
                        {(item.confidence * 100).toFixed(0)}%
                      </span>
                    )}
                    {loadingHistoryId === item.id && (
                      <svg className="animate-spin h-3.5 w-3.5 text-primary shrink-0" viewBox="0 0 24 24" fill="none">
                        <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
                        <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8v8H4z" />
                      </svg>
                    )}
                  </button>
                );
              })}
            </div>
          )}
        </div>
      )}

      {/* Analysis Result */}
      {result && (
        <div className="space-y-4">
          <div className="flex items-center gap-2">
            <h3 className="text-sm font-semibold text-slate-800 dark:text-slate-100">
              {result.asset_name ?? result.asset_code}
            </h3>
            <span className="text-xs text-slate-400">{result.asset_code}</span>
            <span className="ml-auto text-xs text-slate-400">
              {new Date(result.created_at).toLocaleString()}
              {result.model_used ? ` · ${result.model_used}` : ''}
            </span>
          </div>

          {/* Valuation card — shown when LLM produced valuation-aware output */}
          {result.llm_analysis && (result.llm_analysis as Record<string, unknown>).valuation_judgment && (
            <div className="rounded-xl border border-slate-200 dark:border-border-dark bg-white dark:bg-card-dark p-4">
              <h4 className="text-xs font-semibold text-slate-600 dark:text-slate-400 mb-3 uppercase tracking-wide flex items-center gap-1.5">
                <span className="material-symbols-outlined !text-[14px] text-primary">candlestick_chart</span>
                {t('assetAnalyzer.valuationJudgment')}
              </h4>
              <div className="flex flex-wrap items-start gap-3">
                {/* Operation signal badge */}
                {(() => {
                  const sig = String((result.llm_analysis as Record<string, unknown>).operation_signal ?? 'wait').toUpperCase();
                  const color = SIGNAL_COLORS[sig] ?? DEFAULT_SIGNAL_COLOR;
                  const confidence = (result.llm_analysis as Record<string, unknown>).confidence as number | undefined;
                  return (
                    <div className="flex flex-col items-center gap-0.5">
                      <span className={`px-3 py-1 rounded-full text-sm font-bold ${color}`}>{sig}</span>
                      {confidence != null && (
                        <span className="text-[10px] text-slate-400 font-mono">{t('assetAnalyzer.confPct', { pct: (confidence * 100).toFixed(0) })}</span>
                      )}
                    </div>
                  );
                })()}
                <div className="flex-1 min-w-0">
                  {/* Rule bucket */}
                  {(result.llm_analysis as Record<string, unknown>).rule_bucket && (
                    <div className="mb-1.5">
                      <span className="inline-block px-2 py-0.5 rounded text-[10px] font-mono bg-slate-100 dark:bg-slate-800 text-slate-500 uppercase tracking-wider">
                        {String((result.llm_analysis as Record<string, unknown>).rule_bucket)}
                      </span>
                    </div>
                  )}
                  {/* Valuation judgment */}
                  <p className="text-sm text-slate-700 dark:text-slate-300 leading-relaxed">
                    {String((result.llm_analysis as Record<string, unknown>).valuation_judgment)}
                  </p>
                  {/* Validity period */}
                  {(result.llm_analysis as Record<string, unknown>).validity_period && (
                    <p className="text-xs text-slate-400 font-mono mt-1">
                      {t('assetAnalyzer.validityPeriod', { value: String((result.llm_analysis as Record<string, unknown>).validity_period) })}
                    </p>
                  )}
                  {/* Falsification conditions */}
                  {Array.isArray((result.llm_analysis as Record<string, unknown>).falsification_conditions) &&
                    ((result.llm_analysis as Record<string, unknown>).falsification_conditions as string[]).length > 0 && (
                    <div className="mt-2">
                      <p className="text-[10px] font-semibold text-slate-400 uppercase tracking-wider mb-1">{t('assetAnalyzer.falsificationConditions')}</p>
                      {((result.llm_analysis as Record<string, unknown>).falsification_conditions as string[]).map((c, i) => (
                        <div key={i} className="flex items-start gap-1.5 text-xs text-slate-500">
                          <span className="text-slate-300 shrink-0 mt-0.5">⚠</span>
                          <span>{c}</span>
                        </div>
                      ))}
                    </div>
                  )}
                </div>
              </div>
            </div>
          )}

          <div className="rounded-xl border border-slate-200 dark:border-border-dark bg-white dark:bg-card-dark p-4">
            <h4 className="text-xs font-semibold text-slate-500 dark:text-slate-500 mb-3 uppercase tracking-wide flex items-center gap-1.5">
              <span className="material-symbols-outlined !text-[14px] text-slate-400">show_chart</span>
              {t('assetAnalyzer.technicalIndicators')}
            </h4>
            <TechnicalDashboard signals={result.technical_signals} />
          </div>

          {result.llm_analysis_markdown && (
            <div className="rounded-xl border border-slate-200 dark:border-border-dark bg-white dark:bg-card-dark p-4">
              <h4 className="text-xs font-semibold text-slate-600 dark:text-slate-400 mb-3 uppercase tracking-wide">
                {t('assetAnalyzer.aiAnalysis')}
              </h4>
              <div className="space-y-0.5">
                {renderMarkdown(result.llm_analysis_markdown)}
              </div>
            </div>
          )}
        </div>
      )}

      {/* History */}
      {selectedAsset && (historyLoading || historyItems.length > 0) && (
        <div className="rounded-xl border border-slate-200 dark:border-border-dark bg-white dark:bg-card-dark p-4">
          <h4 className="text-xs font-semibold text-slate-600 dark:text-slate-400 mb-3 uppercase tracking-wide">
            {t('assetAnalyzer.analysisHistory')}
          </h4>
          {historyLoading ? (
            <p className="text-sm text-slate-400">{t('assetAnalyzer.loadingHistory')}</p>
          ) : (
            <div className="space-y-2">
              {historyItems.map((item) => (
                <div
                  key={item.id}
                  className="flex items-center justify-between py-2 border-b border-slate-100 dark:border-slate-700 last:border-0"
                >
                  <div className="flex items-center gap-3">
                    <span className="text-xs text-slate-500 dark:text-slate-400 font-mono">
                      {new Date(item.created_at).toLocaleDateString()}
                    </span>
                    {item.timing_signal && (
                      <span className="px-2 py-0.5 rounded-full text-[10px] font-medium bg-slate-100 text-slate-700 dark:bg-slate-800 dark:text-slate-300 uppercase">
                        {item.timing_signal}
                      </span>
                    )}
                  </div>
                  <div className="flex items-center gap-2">
                    {item.confidence != null && (
                      <span className="text-xs text-slate-400">
                        {t('assetAnalyzer.confPct', { pct: (item.confidence * 100).toFixed(0) })}
                      </span>
                    )}
                    {item.model_used && (
                      <span className="text-[10px] text-slate-400">{item.model_used}</span>
                    )}
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  );
};
