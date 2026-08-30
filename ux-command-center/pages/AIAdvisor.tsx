import React, { useCallback, useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import {
  type BehavioralMetric,
  type BriefHistoryItem,
  type BriefResponse,
  type ContextConfig,
  type InsightItem,
  type LLMSettings,
  computeBehavioralMetrics,
  generateBrief,
  getBriefById,
  getBriefHistory,
  getContextPreview,
  getLatestBehavioralMetrics,
  getLatestBrief,
  getLLMSettings,
  listInsights,
  renderAdvisorContext,
} from '../src/services/api';
import { BehavioralRadar } from '../components/ai-advisor/BehavioralRadar';
import { BriefSection, BRIEF_SECTION_ORDER } from '../components/ai-advisor/BriefSection';
import { ContextPanel } from '../components/ai-advisor/ContextPanel';
import { InsightManager } from '../components/ai-advisor/InsightManager';
import { LLMDebugLog } from '../components/ai-advisor/LLMDebugLog';
import { AssetAnalyzer } from '../components/ai-advisor/AssetAnalyzer';
import { MemoManager } from '../components/ai-advisor/MemoManager';
import { ReviewFlow } from '../components/ai-advisor/ReviewFlow';
import { PendingVerificationList } from '../components/ai-advisor/PendingVerificationList';
import { CrossCheckAudit } from '../components/ai-advisor/CrossCheckAudit';
import { TradeRecorder } from '../components/ai-advisor/TradeRecorder';
import { usePortfolioFilter } from '../src/context/usePortfolioFilter';
import { useLanguage } from '../src/context/useLanguage';
import { formatDate, formatTime } from '../src/utils/formatMoney';

type ActiveTab = 'brief' | 'memos' | 'analyze' | 'record-trade' | 'review' | 'insights';

const HISTORY_TIME_OPTS: Intl.DateTimeFormatOptions = { hour: '2-digit', minute: '2-digit' };

const DEFAULT_QUICK_CONTEXT: ContextConfig = {
  tiers: {
    identity: { enabled: true, detail: 'summary' },
    portfolio: { enabled: true, detail: 'summary' },
    market: { enabled: true, detail: 'summary' },
    strategy: { enabled: false, detail: 'summary', timeframe: '30d' },
    transactions: { enabled: true, detail: 'summary', timeframe: '14d' },
  },
  include_realtime: false,
  include_non_rebalanceable: false,
};

// Section order now lives beside the styling it belongs to, keyed by the stable
// ASCII section IDs (Program BIL / WS-5). It used to be a second, independent
// list of the same Chinese literals BriefSection matched on.

export const AIAdvisor: React.FC = () => {
  const { t } = useTranslation('aiAdvisor');
  const { includeNonRebalanceable } = usePortfolioFilter();
  const { lang } = useLanguage();
  const [activeTab, setActiveTab] = useState<ActiveTab>('brief');
  const [contextConfig, setContextConfig] = useState<ContextConfig>(DEFAULT_QUICK_CONTEXT);
  const [brief, setBrief] = useState<BriefResponse | null>(null);
  const [generating, setGenerating] = useState(false);
  const [previewLoading, setPreviewLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [history, setHistory] = useState<BriefHistoryItem[]>([]);
  const [showContextPanel, setShowContextPanel] = useState(true);
  const [llmSettings, setLlmSettings] = useState<LLMSettings | null>(null);
  const [loadingInitial, setLoadingInitial] = useState(true);
  const [copied, setCopied] = useState(false);
  const [metrics, setMetrics] = useState<BehavioralMetric[]>([]);
  const [computingMetrics, setComputingMetrics] = useState(false);
  const [insights, setInsights] = useState<InsightItem[]>([]);
  const [tokenEstimates, setTokenEstimates] = useState<Record<string, unknown>>({});
  const [contextDraft, setContextDraft] = useState('');
  const [previewConfigKey, setPreviewConfigKey] = useState<string | null>(null);

  const contextConfigKey = JSON.stringify(contextConfig);
  const isPreviewCurrent = Boolean(contextDraft.trim()) && previewConfigKey === contextConfigKey;

  useEffect(() => {
    setContextConfig((prev) => {
      if (prev.include_non_rebalanceable === includeNonRebalanceable) {
        return prev;
      }
      return {
        ...prev,
        include_non_rebalanceable: includeNonRebalanceable,
      };
    });
  }, [includeNonRebalanceable]);

  // Fetch token estimates whenever context config changes
  useEffect(() => {
    const enabled = Object.entries(contextConfig.tiers)
      .filter(([, v]) => v.enabled)
      .map(([k]) => k);
    if (enabled.length === 0) {
      setTokenEstimates({});
      return;
    }
    const params: Record<string, string> = { tiers: enabled.join(',') };
    // Add detail levels and timeframe for each enabled tier
    for (const [tier, cfg] of Object.entries(contextConfig.tiers)) {
      if ((cfg as any).enabled) {
        if ((cfg as any).detail) {
          params[`detail_${tier}`] = (cfg as any).detail;
        }
        if (tier === 'transactions' && (cfg as any).timeframe) {
          params.timeframe = (cfg as any).timeframe;
        }
      }
    }
    getContextPreview(params)
      .then(setTokenEstimates)
      .catch(() => {}); // non-critical
  }, [contextConfig]);

  const loadHistory = useCallback(async () => {
    try {
      const items = await getBriefHistory(10);
      setHistory(Array.isArray(items) ? items : []);
    } catch {
      // history is non-critical
    }
  }, []);

  useEffect(() => {
    const init = async () => {
      try {
        const [latestBrief, settings, metricsResult, insightsResult] = await Promise.allSettled([
          getLatestBrief(),
          getLLMSettings(),
          getLatestBehavioralMetrics(),
          listInsights(),
        ]);
        if (latestBrief.status === 'fulfilled' && latestBrief.value) {
          setBrief(latestBrief.value);
        }
        if (settings.status === 'fulfilled') {
          setLlmSettings(settings.value);
        }
        if (metricsResult.status === 'fulfilled' && Array.isArray(metricsResult.value)) {
          setMetrics(metricsResult.value);
        }
        if (insightsResult.status === 'fulfilled' && Array.isArray(insightsResult.value)) {
          setInsights(insightsResult.value);
        }
        await loadHistory();
      } finally {
        setLoadingInitial(false);
      }
    };
    init();
  }, [loadHistory]);

  const handleComputeMetrics = async () => {
    setComputingMetrics(true);
    try {
      const result = await computeBehavioralMetrics();
      setMetrics(result.metrics);
    } catch {
      // silent fail
    } finally {
      setComputingMetrics(false);
    }
  };

  const handleGenerate = async () => {
    if (!isPreviewCurrent) {
      return;
    }
    setGenerating(true);
    setError(null);
    try {
      const result = await generateBrief(contextConfig, contextDraft);
      setBrief(result);
      await loadHistory();
    } catch (e) {
      setError(e instanceof Error ? e.message : t('page.errors.generationFailed'));
    } finally {
      setGenerating(false);
    }
  };

  const handlePreviewContext = async () => {
    setPreviewLoading(true);
    setError(null);
    try {
      const result = await renderAdvisorContext('brief', contextConfig);
      setContextDraft(result.context_text);
      setPreviewConfigKey(JSON.stringify(contextConfig));
    } catch (e) {
      setError(e instanceof Error ? e.message : t('page.errors.contextPreviewFailed'));
    } finally {
      setPreviewLoading(false);
    }
  };

  const handleLoadFromHistory = async (id: number) => {
    try {
      const result = await getBriefById(id);
      setBrief(result);
    } catch {
      setError(t('page.errors.loadFromHistoryFailed'));
    }
  };

  const handleCopy = async () => {
    if (!brief?.content_markdown) return;
    try {
      await navigator.clipboard.writeText(brief.content_markdown);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } catch {
      // clipboard not available
    }
  };

  // Ordered sections present in content_json
  const orderedSections = brief
    ? BRIEF_SECTION_ORDER.filter((k) => k in brief.content_json)
    : [];

  return (
    <div className="p-6 max-w-[1600px] mx-auto w-full bg-gray-50 dark:bg-background-dark min-h-screen">
      {/* Header */}
      <header className="flex justify-between items-start mb-6">
        <div>
          <h1 className="text-2xl font-bold text-slate-900 dark:text-white tracking-tight">{t('page.title')}</h1>
          <p className="text-slate-500 dark:text-slate-400 mt-0.5 text-sm">
            {t('page.subtitle')}
          </p>
        </div>
        <div className="flex items-center gap-2">
          {llmSettings && (
            <span className="text-xs text-slate-400 px-2 py-1 rounded-md bg-slate-100 dark:bg-slate-800">
              {llmSettings.primary_model}
            </span>
          )}
          {activeTab === 'brief' && (
            <>
              <button
                type="button"
                onClick={handlePreviewContext}
                disabled={previewLoading || generating}
                className="flex items-center gap-2 px-4 py-2 rounded-lg text-sm font-medium border border-slate-200 bg-white text-slate-700 hover:bg-slate-50 disabled:opacity-50 transition-colors"
              >
                {previewLoading ? (
                  <>
                    <svg className="animate-spin h-4 w-4" viewBox="0 0 24 24" fill="none">
                      <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
                      <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8v8H4z" />
                    </svg>
                    {t('page.previewing')}
                  </>
                ) : (
                  <>
                    <span className="material-symbols-outlined !text-[16px]">preview</span>
                    {t('page.previewContext')}
                  </>
                )}
              </button>
              <button
                type="button"
                onClick={handleGenerate}
                disabled={generating || !isPreviewCurrent}
                className="flex items-center gap-2 px-4 py-2 bg-primary text-white rounded-lg text-sm font-medium hover:bg-primary/90 disabled:opacity-50 transition-colors"
              >
                {generating ? (
                  <>
                    <svg className="animate-spin h-4 w-4" viewBox="0 0 24 24" fill="none">
                      <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
                      <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8v8H4z" />
                    </svg>
                    {t('page.generating')}
                  </>
                ) : (
                  <>
                    <span className="material-symbols-outlined !text-[16px]">auto_awesome</span>
                    {t('page.generateBrief')}
                  </>
                )}
              </button>
            </>
          )}
        </div>
      </header>

      {/* Tabs */}
      <div className="flex gap-1 mb-5 border-b border-slate-200 dark:border-border-dark overflow-x-auto">
        {(['brief', 'memos', 'analyze', 'record-trade', 'review', 'insights'] as const).map((tab) => (
          <button
            key={tab}
            type="button"
            onClick={() => setActiveTab(tab)}
            className={`px-4 py-2 text-sm font-medium border-b-2 transition-colors -mb-px whitespace-nowrap ${
              activeTab === tab
                ? 'border-primary text-primary'
                : 'border-transparent text-slate-500 hover:text-slate-700 dark:hover:text-slate-300'
            }`}
          >
            {tab === 'brief' ? t('page.tabs.brief') :
             tab === 'memos' ? t('page.tabs.memos') :
             tab === 'analyze' ? t('page.tabs.analyze') :
             tab === 'record-trade' ? t('page.tabs.recordTrade') :
             tab === 'review' ? t('page.tabs.review') : t('page.tabs.insights')}
          </button>
        ))}
      </div>

      {activeTab === 'insights' ? (
        <div className="space-y-4">
          <BehavioralRadar metrics={metrics} onCompute={handleComputeMetrics} computing={computingMetrics} />
          <InsightManager insights={insights} onRefresh={() => listInsights().then(setInsights)} />
        </div>
      ) : activeTab === 'review' ? (
        <div className="space-y-6">
          <PendingVerificationList />
          <CrossCheckAudit />
          <ReviewFlow contextConfig={contextConfig} />
        </div>
      ) : activeTab === 'memos' ? (
        <MemoManager />
      ) : activeTab === 'analyze' ? (
        <AssetAnalyzer />
      ) : activeTab === 'record-trade' ? (
        <TradeRecorder />
      ) : (
        /* Brief tab — two-panel layout */
        <div className="flex gap-5 items-start">
          {/* Left panel — context config */}
          {showContextPanel && (
            <div className="w-72 flex-shrink-0">
              <div className="rounded-xl border border-slate-200 dark:border-border-dark bg-white dark:bg-card-dark p-4 sticky top-6">
                <div className="flex items-center justify-between mb-3">
                  <h2 className="text-sm font-semibold text-slate-800 dark:text-slate-100">{t('page.contextSettings')}</h2>
                  <button
                    type="button"
                    onClick={() => setShowContextPanel(false)}
                    className="text-slate-400 hover:text-slate-600 dark:hover:text-slate-300 transition-colors"
                  >
                    <span className="material-symbols-outlined !text-[18px]">close</span>
                  </button>
                </div>
                <ContextPanel config={contextConfig} onChange={setContextConfig} tokenEstimates={tokenEstimates} />

                {/* History */}
                {history.length > 0 && (
                  <div className="mt-5 pt-4 border-t border-slate-100 dark:border-slate-700">
                    <p className="text-xs text-slate-500 mb-2">{t('page.briefHistory')}</p>
                    <div className="space-y-1">
                      {history.map((item) => (
                        <button
                          key={item.id}
                          type="button"
                          onClick={() => handleLoadFromHistory(item.id)}
                          className="w-full text-left px-2 py-1.5 rounded-md hover:bg-slate-50 dark:hover:bg-slate-800 transition-colors"
                        >
                          <p className="text-xs text-slate-700 dark:text-slate-300 truncate">
                            {item.title ?? formatDate(item.created_at, lang)}
                          </p>
                          <p className="text-[10px] text-slate-400">
                            {formatTime(item.created_at, lang, HISTORY_TIME_OPTS)}
                            {item.model_used ? ` · ${item.model_used}` : ''}
                          </p>
                        </button>
                      ))}
                    </div>
                  </div>
                )}
              </div>
            </div>
          )}

          {/* Right panel — brief output */}
          <div className="flex-1 min-w-0">
            {!showContextPanel && (
              <button
                type="button"
                onClick={() => setShowContextPanel(true)}
                className="mb-4 flex items-center gap-1.5 text-xs text-slate-500 hover:text-slate-700 dark:hover:text-slate-300 transition-colors"
              >
                <span className="material-symbols-outlined !text-[14px]">settings</span>
                {t('page.showSettings')}
              </button>
            )}

            {error && (
              <div className="mb-4 flex items-center gap-2 p-3 rounded-lg bg-red-50 dark:bg-red-900/20 border border-red-200 dark:border-red-800 text-sm text-red-600 dark:text-red-400">
                <span className="material-symbols-outlined !text-[16px]">error</span>
                {error}
              </div>
            )}

            {loadingInitial ? (
              <div className="text-center py-20 text-slate-400 text-sm">{t('page.loading')}</div>
            ) : (
              <>
                <div className="mb-4 rounded-2xl border border-slate-200 bg-white p-4 shadow-sm">
                  <div className="flex items-start justify-between gap-3 mb-3">
                    <div>
                      <h2 className="text-sm font-semibold text-slate-800">{t('page.reviewedContext')}</h2>
                      <p className="text-xs text-slate-500 mt-1">
                        {t('page.reviewedContextHint')}
                      </p>
                    </div>
                    <span className={`rounded-full px-2.5 py-1 text-[11px] font-medium ${
                      isPreviewCurrent ? 'bg-emerald-50 text-emerald-700' : 'bg-amber-50 text-amber-700'
                    }`}>
                      {isPreviewCurrent ? t('page.previewReady') : t('page.previewRequired')}
                    </span>
                  </div>
                  <textarea
                    value={contextDraft}
                    onChange={(e) => setContextDraft(e.target.value)}
                    placeholder={t('page.previewPlaceholder')}
                    rows={12}
                    className="w-full rounded-xl border border-slate-200 bg-slate-50 px-3 py-3 text-sm text-slate-800 placeholder-slate-400 focus:outline-none focus:ring-2 focus:ring-primary/50 font-mono"
                  />
                  {!isPreviewCurrent && contextDraft.trim() && (
                    <p className="mt-2 text-xs text-amber-600">
                      {t('page.previewStale')}
                    </p>
                  )}
                </div>

                {!brief ? (
                  <div className="flex flex-col items-center justify-center py-20 text-center">
                    <span className="material-symbols-outlined !text-[48px] text-slate-300 dark:text-slate-600 mb-4">
                      smart_toy
                    </span>
                    <h2 className="text-lg font-semibold text-slate-700 dark:text-slate-300 mb-2">{t('page.noBriefYet')}</h2>
                    <p className="text-sm text-slate-400 mb-2">{t('page.noBriefYetHint')}</p>
                  </div>
                ) : (
                  <>
                {/* Brief header row */}
                <div className="flex items-center justify-between mb-4">
                  <h2 className="text-sm font-semibold text-slate-800 dark:text-slate-100">
                    {t('page.investmentBrief')}
                  </h2>
                  <button
                    type="button"
                    onClick={handleCopy}
                    className="flex items-center gap-1.5 text-xs text-slate-500 hover:text-slate-700 dark:hover:text-slate-300 transition-colors px-2.5 py-1 rounded-md border border-slate-200 dark:border-border-dark"
                  >
                    <span className="material-symbols-outlined !text-[14px]">
                      {copied ? 'check' : 'content_copy'}
                    </span>
                    {copied ? t('page.copied') : t('page.copyMarkdown')}
                  </button>
                </div>

                {/* Sections */}
                {orderedSections.length > 0 ? (
                  orderedSections.map((key) => (
                    <BriefSection key={key} title={key} content={brief.content_json[key]} />
                  ))
                ) : (
                  /* Fallback: render all keys */
                  Object.entries(brief.content_json).map(([key, content]) => (
                    <BriefSection key={key} title={key} content={content} />
                  ))
                )}

                {/* Footer */}
                <div className="text-xs text-slate-400 mt-3">
                  {t('page.footer', {
                    model: brief.model_used,
                    tokens: brief.usage?.total_tokens?.toLocaleString(),
                  })}{' '}
                  {new Date(brief.created_at).toLocaleString()}
                </div>

                <LLMDebugLog
                  promptText={brief.prompt_text}
                  rawResponse={brief.raw_response_text}
                  modelUsed={brief.model_used}
                  tokenCount={brief.usage?.total_tokens}
                />
                  </>
                )}
              </>
            )}
          </div>
        </div>
      )}
    </div>
  );
};
