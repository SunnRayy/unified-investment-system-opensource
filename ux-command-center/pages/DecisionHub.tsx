import React, { useEffect, useMemo, useRef, useState } from 'react';
import { Trans, useTranslation } from 'react-i18next';
import type { TFunction } from 'i18next';
import { useNavigate } from 'react-router-dom';
import {
    api,
    DecisionTimeline,
    DecisionStats,
    DecisionScorecard,
    DecisionIntelligence,
    DecisionAlert,
    ScorecardItem,
} from '../src/services/api';

type Tab = 'timeline' | 'scorecard' | 'intelligence';
type AlertFilter = 'all' | DecisionAlert['category'];
type VerificationStatus = 'pending' | 'verified' | 'unmatched' | null | undefined;

export const DecisionHub: React.FC = () => {
    const { t } = useTranslation('reports');
    const navigate = useNavigate();
    const [activeTab, setActiveTab] = useState<Tab>('timeline');
    const [timeline, setTimeline] = useState<DecisionTimeline | null>(null);
    const [stats, setStats] = useState<DecisionStats | null>(null);
    const [scorecard, setScorecard] = useState<DecisionScorecard | null>(null);
    const [intelligence, setIntelligence] = useState<DecisionIntelligence | null>(null);
    const [alerts, setAlerts] = useState<DecisionAlert[]>([]);
    const [filter, setFilter] = useState<string>('all');
    const [alertFilter, setAlertFilter] = useState<AlertFilter>('all');
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState<string | null>(null);
    const alertsRef = useRef<HTMLDivElement | null>(null);
    const timelineRef = useRef<HTMLDivElement | null>(null);

    useEffect(() => {
        const loadData = async () => {
            setError(null);
            try {
                const [tData, sData, scData, iData, aData] = await Promise.all([
                    api.getDecisionsTimeline(50, filter),
                    api.getDecisionsStats(),
                    api.getDecisionsScorecard(),
                    api.getDecisionsIntelligence(),
                    api.getDecisionAlerts(),
                ]);
                setTimeline(tData);
                setStats(sData);
                setScorecard(scData);
                setIntelligence(iData);
                setAlerts(aData.alerts);
            } catch (err) {
                console.error(err);
                setError(t('decisionHub.errors.load'));
            } finally {
                setLoading(false);
            }
        };
        loadData();
    }, [filter]);

    useEffect(() => {
        if (alertFilter !== 'all' && !alerts.some(alert => alert.category === alertFilter)) {
            setAlertFilter('all');
        }
    }, [alertFilter, alerts]);

    const getStatusColor = (status: string) => {
        switch (status) {
            case 'adopted': return 'bg-green-100 text-green-700 dark:bg-green-900/30 dark:text-green-400';
            case 'executed': return 'bg-green-100 text-green-700 dark:bg-green-900/30 dark:text-green-400';
            case 'rejected': return 'bg-red-100 text-red-700 dark:bg-red-900/30 dark:text-red-400';
            case 'observing': return 'bg-yellow-100 text-yellow-700 dark:bg-yellow-900/30 dark:text-yellow-400';
            default: return 'bg-slate-100 text-slate-700 dark:bg-slate-800 dark:text-slate-400';
        }
    };

    const getTypeIcon = (type: string) => {
        switch (type) {
            case 'insight': return 'lightbulb';
            case 'trade': return 'swap_horiz';
            case 'drift': return 'warning';
            default: return 'article';
        }
    };

    const filteredAlerts = useMemo(
        () => alertFilter === 'all'
            ? alerts
            : alerts.filter(alert => alert.category === alertFilter),
        [alertFilter, alerts]
    );

    // Counted by the backend from the same three queries the timeline runs.
    // The old sum here — insights + AI-attributed trades — omitted drift alerts
    // and every broker-imported trade, so it read 0 above a timeline listing
    // real executed orders. Fall back to the old shape only for a stats payload
    // that predates the field.
    const totalDecisions = stats?.total_decisions ?? ((stats?.total_insights || 0) + (stats?.ai_trades_total || 0));
    const pendingActionsCount = stats?.pending_actions_count ?? alerts.length;
    const driftAlertsCount = stats?.active_drift_alerts ?? alerts.filter(alert => alert.category === 'drift').length;

    const scrollToAlerts = (nextFilter: AlertFilter) => {
        setAlertFilter(nextFilter);
        alertsRef.current?.scrollIntoView({ behavior: 'smooth', block: 'start' });
    };

    const showTimeline = (nextFilter: string) => {
        setActiveTab('timeline');
        setFilter(nextFilter);
        timelineRef.current?.scrollIntoView({ behavior: 'smooth', block: 'start' });
    };

    if (loading) return <div className="p-8 text-center text-slate-500">{t('decisionHub.loading')}</div>;

    const tabs: { id: Tab; label: string; icon: string }[] = [
        { id: 'timeline', label: t('decisionHub.tabs.timeline'), icon: 'history' },
        { id: 'scorecard', label: t('decisionHub.tabs.scorecard'), icon: 'fact_check' },
        { id: 'intelligence', label: t('decisionHub.tabs.intelligence'), icon: 'insights' },
    ];

    return (
        <div data-testid="decision-page" className="p-8 max-w-[1600px] mx-auto space-y-8 bg-gray-50 dark:bg-background-dark min-h-screen">
            {error && (
                <div className="bg-red-50 border border-red-200 rounded-lg p-4 mb-4">
                    <p className="text-red-700 text-sm">{error}</p>
                </div>
            )}
            <header>
                <h1 className="text-3xl font-bold text-slate-900 dark:text-white tracking-tight">{t('decisionHub.title')}</h1>
                <p className="text-slate-500 dark:text-slate-400 mt-1">{t('decisionHub.subtitle')}</p>
            </header>

            {/* Alerts Section */}
            <div ref={alertsRef}>
                <AlertsSection
                    alerts={filteredAlerts}
                    allAlerts={alerts}
                    filter={alertFilter}
                    onFilterChange={setAlertFilter}
                />
            </div>

            {/* Summary Stats */}
            {stats && (
                <div className="grid grid-cols-1 md:grid-cols-4 gap-4">
                    <StatCard
                        title={t('decisionHub.stats.totalDecisions')}
                        value={totalDecisions}
                        icon="history"
                        onClick={() => showTimeline('all')}
                    />
                    {/* No insights means there are no adoption decisions to
                        rate. "0%" reads as "every suggestion was rejected",
                        which is a different and much worse claim. */}
                    <StatCard
                        title={t('decisionHub.stats.adoptionRate')}
                        value={stats.total_insights > 0 ? `${stats.adoption_rate}%` : '—'}
                        icon="thumb_up"
                        onClick={() => navigate('/verify')}
                    />
                    <StatCard
                        title={t('decisionHub.stats.pendingActions')}
                        value={pendingActionsCount}
                        icon="pending"
                        color="text-yellow-600"
                        onClick={() => scrollToAlerts('all')}
                    />
                    <StatCard
                        title={t('decisionHub.stats.driftAlerts')}
                        value={driftAlertsCount}
                        icon="warning"
                        color="text-orange-600"
                        onClick={() => scrollToAlerts('drift')}
                    />
                </div>
            )}

            {/* Tab Navigation */}
            <div ref={timelineRef} className="bg-white dark:bg-card-dark rounded-xl shadow-sm border border-slate-200 dark:border-border-dark overflow-hidden">
                <div className="border-b border-slate-200 dark:border-border-dark bg-slate-50 dark:bg-slate-800/50">
                    <div className="flex">
                        {tabs.map(tab => (
                            <button
                                key={tab.id}
                                onClick={() => setActiveTab(tab.id)}
                                className={`flex items-center gap-2 px-5 py-3.5 text-sm font-medium border-b-2 transition-colors ${
                                    activeTab === tab.id
                                        ? 'border-primary text-primary dark:text-primary-light bg-white dark:bg-card-dark'
                                        : 'border-transparent text-slate-500 hover:text-slate-700 dark:text-slate-400 dark:hover:text-white hover:bg-slate-100 dark:hover:bg-slate-700/50'
                                }`}
                            >
                                <span className="material-symbols-outlined text-[18px]">{tab.icon}</span>
                                {tab.label}
                            </button>
                        ))}
                    </div>
                </div>

                {/* Timeline Tab */}
                {activeTab === 'timeline' && (
                    <>
                        <div className="p-4 border-b border-slate-200 dark:border-border-dark flex items-center gap-2 bg-white dark:bg-card-dark">
                            {(['all', 'insight', 'trade', 'drift'] as const).map(f => (
                                <button
                                    key={f}
                                    onClick={() => setFilter(f)}
                                    className={`px-3 py-1.5 rounded-lg text-sm font-medium transition-colors ${
                                        filter === f
                                            ? 'bg-white shadow-sm text-primary ring-1 ring-slate-200 dark:bg-slate-700 dark:text-white dark:ring-slate-600'
                                            : 'text-slate-500 hover:text-slate-700 dark:text-slate-400 dark:hover:text-white'
                                    }`}
                                >
                                    {t(`decisionHub.timelineFilters.${f}`)}
                                </button>
                            ))}
                        </div>
                        <div className="divide-y divide-slate-100 dark:divide-slate-800">
                            {timeline?.items.map((item) => (
                                <div key={item.id} className="p-4 hover:bg-slate-50 dark:hover:bg-slate-800/30 transition-colors flex gap-4">
                                    <div className={`mt-1 size-10 rounded-full flex items-center justify-center shrink-0 ${
                                        item.type === 'insight' ? 'bg-indigo-100 text-indigo-600 dark:bg-indigo-900/30 dark:text-indigo-400' :
                                        item.type === 'trade' ? 'bg-emerald-100 text-emerald-600 dark:bg-emerald-900/30 dark:text-emerald-400' :
                                        'bg-orange-100 text-orange-600 dark:bg-orange-900/30 dark:text-orange-400'
                                    }`}>
                                        <span className="material-symbols-outlined text-[20px]">{getTypeIcon(item.type)}</span>
                                    </div>
                                    <div className="flex-1 min-w-0">
                                        <div className="flex items-center justify-between mb-1">
                                            <h3 className="text-sm font-semibold text-slate-900 dark:text-white truncate">{item.title}</h3>
                                            <span className="text-xs text-slate-400 whitespace-nowrap">{item.date}</span>
                                        </div>
                                        <p className="text-sm text-slate-600 dark:text-slate-300 line-clamp-2 mb-2">{item.content}</p>
                                        {item.type === 'trade' && (item.metadata?.linked_title || item.metadata?.reason_excerpt) && (
                                            <div className="mb-2 space-y-1">
                                                {item.metadata?.linked_title && (
                                                    <p className="text-xs text-slate-500 dark:text-slate-400">
                                                        <span className="font-medium text-slate-700 dark:text-slate-200">{t('decisionHub.linked')}</span>{' '}
                                                        {item.metadata.linked_title}
                                                    </p>
                                                )}
                                                {item.metadata?.reason_excerpt && (
                                                    <p className="text-xs text-slate-500 dark:text-slate-400">
                                                        <span className="font-medium text-slate-700 dark:text-slate-200">{t('decisionHub.reason')}</span>{' '}
                                                        {item.metadata.reason_excerpt}
                                                    </p>
                                                )}
                                            </div>
                                        )}
                                        <div className="flex items-center gap-3">
                                            <span className={`inline-flex items-center px-2 py-0.5 rounded text-xs font-medium uppercase tracking-wide ${getStatusColor(item.display_status || item.status)}`}>
                                                {item.display_status || item.status}
                                            </span>
                                            {item.type === 'trade' && (
                                                <VerificationStatusBadge status={item.verification_status} />
                                            )}
                                            <span className="text-xs text-slate-400 flex items-center gap-1">
                                                <span className="material-symbols-outlined text-[14px]">smart_toy</span>
                                                {item.display_source || item.source}
                                            </span>
                                        </div>
                                    </div>
                                </div>
                            ))}
                            {timeline?.items.length === 0 && (
                                <div className="p-12 text-center text-slate-500">{t('decisionHub.noDecisions')}</div>
                            )}
                        </div>
                    </>
                )}

                {/* Scorecard Tab */}
                {activeTab === 'scorecard' && (
                    <div className="p-6">
                        {!scorecard || scorecard.items.length === 0 ? (
                            <div className="py-16 text-center">
                                <span className="material-symbols-outlined text-5xl text-slate-300 dark:text-slate-600 block mb-3">fact_check</span>
                                <p className="text-slate-500 dark:text-slate-400 font-medium">{t('decisionHub.scorecard.empty')}</p>
                                <p className="text-sm text-slate-400 dark:text-slate-500 mt-1">
                                    <Trans
                                        t={t}
                                        i18nKey="decisionHub.scorecard.emptyHint"
                                        components={{ code: <code className="font-mono bg-slate-100 dark:bg-slate-800 px-1 rounded" /> }}
                                    />
                                </p>
                            </div>
                        ) : (
                            <div className="overflow-x-auto">
                                <table className="w-full text-sm">
                                    <thead>
                                        <tr className="border-b border-slate-200 dark:border-border-dark text-left">
                                            <th className="pb-3 pr-4 font-semibold text-slate-500 dark:text-slate-400 text-xs uppercase tracking-wide">{t('decisionHub.scorecard.cols.date')}</th>
                                            <th className="pb-3 pr-4 font-semibold text-slate-500 dark:text-slate-400 text-xs uppercase tracking-wide">{t('decisionHub.scorecard.cols.asset')}</th>
                                            <th className="pb-3 pr-4 font-semibold text-slate-500 dark:text-slate-400 text-xs uppercase tracking-wide">{t('decisionHub.scorecard.cols.action')}</th>
                                            <th className="pb-3 pr-4 font-semibold text-slate-500 dark:text-slate-400 text-xs uppercase tracking-wide">{t('decisionHub.scorecard.cols.source')}</th>
                                            <th className="pb-3 pr-4 font-semibold text-slate-500 dark:text-slate-400 text-xs uppercase tracking-wide">{t('decisionHub.scorecard.cols.link')}</th>
                                            <th className="pb-3 pr-4 font-semibold text-slate-500 dark:text-slate-400 text-xs uppercase tracking-wide">{t('decisionHub.scorecard.cols.verification')}</th>
                                            <th className="pb-3 pr-4 font-semibold text-slate-500 dark:text-slate-400 text-xs uppercase tracking-wide">{t('decisionHub.scorecard.cols.verificationNote')}</th>
                                            <th className="pb-3 pr-4 font-semibold text-slate-500 dark:text-slate-400 text-xs uppercase tracking-wide text-right">{t('decisionHub.scorecard.cols.outcomePct')}</th>
                                            <th className="pb-3 pr-4 font-semibold text-slate-500 dark:text-slate-400 text-xs uppercase tracking-wide">{t('decisionHub.scorecard.cols.verdict')}</th>
                                            <th className="pb-3 font-semibold text-slate-500 dark:text-slate-400 text-xs uppercase tracking-wide">{t('decisionHub.scorecard.cols.grade')}</th>
                                        </tr>
                                    </thead>
                                    <tbody className="divide-y divide-slate-100 dark:divide-slate-800">
                                        {scorecard.items.map(item => (
                                            <ScorecardRow key={item.id} item={item} />
                                        ))}
                                    </tbody>
                                </table>
                            </div>
                        )}
                    </div>
                )}

                {/* Intelligence Tab */}
                {activeTab === 'intelligence' && (
                    <div className="p-6 space-y-8">
                        <section>
                            <h2 className="text-base font-semibold text-slate-900 dark:text-white mb-4">{t('decisionHub.intelligence.decisionPatterns')}</h2>
                            {!intelligence ? (
                                <p className="text-sm text-slate-400 dark:text-slate-500 italic">
                                    {t('decisionHub.intelligence.noData')}
                                </p>
                            ) : (
                                <DecisionPatternsSection intelligence={intelligence} />
                            )}
                        </section>

                        <section>
                            <h2 className="text-base font-semibold text-slate-900 dark:text-white mb-4">{t('decisionHub.intelligence.growthTimeline')}</h2>
                            {!intelligence || intelligence.growth_timeline.length === 0 ? (
                                <p className="text-sm text-slate-400 dark:text-slate-500 italic">{t('decisionHub.intelligence.noGrowthLessons')}</p>
                            ) : (
                                <div className="space-y-3">
                                    {intelligence.growth_timeline.map(item => (
                                        <div key={item.id} className="rounded-xl border border-slate-200 dark:border-border-dark p-4 bg-slate-50/70 dark:bg-slate-800/30">
                                            <div className="flex items-center justify-between gap-3 mb-1">
                                                <h3 className="font-semibold text-slate-900 dark:text-white">{item.title}</h3>
                                                <span className="text-xs text-slate-400">{item.date}</span>
                                            </div>
                                            <p className="text-sm text-slate-600 dark:text-slate-300">{item.content}</p>
                                            <div className="mt-2 text-xs text-slate-400 flex items-center gap-3">
                                                <span>{item.source}</span>
                                                <span>{item.origin_ref}</span>
                                            </div>
                                        </div>
                                    ))}
                                </div>
                            )}
                        </section>

                        <section>
                            <h2 className="text-base font-semibold text-slate-900 dark:text-white mb-4">{t('decisionHub.intelligence.rawInsightSections')}</h2>
                            {!intelligence || intelligence.raw_sections.length === 0 ? (
                                <p className="text-sm text-slate-400 dark:text-slate-500 italic">{t('decisionHub.intelligence.noRawSections')}</p>
                            ) : (
                                <div className="space-y-3">
                                    {intelligence.raw_sections.map(section => (
                                        <details key={`${section.section}-${section.title}`} className="rounded-xl border border-slate-200 dark:border-border-dark bg-white dark:bg-slate-900/30">
                                            <summary className="cursor-pointer list-none px-4 py-3 flex items-center justify-between gap-3">
                                                <div>
                                                    <p className="font-semibold text-slate-900 dark:text-white">{section.section}</p>
                                                    <p className="text-xs text-slate-400">{section.title}</p>
                                                </div>
                                                <span className="text-xs text-slate-400">{t('decisionHub.intelligence.entries', { count: section.entry_count })}</span>
                                            </summary>
                                            <pre className="px-4 pb-4 text-xs whitespace-pre-wrap text-slate-600 dark:text-slate-300">{section.content}</pre>
                                        </details>
                                    ))}
                                </div>
                            )}
                        </section>
                    </div>
                )}
            </div>
        </div>
    );
};

// ── Scorecard row ────────────────────────────────────────────────────────────

const VERDICT_STYLES: Record<string, string> = {
    good_call: 'bg-green-100 text-green-700 dark:bg-green-900/30 dark:text-green-400',
    regret: 'bg-red-100 text-red-700 dark:bg-red-900/30 dark:text-red-400',
    missed_opportunity: 'bg-yellow-100 text-yellow-700 dark:bg-yellow-900/30 dark:text-yellow-400',
    bullet_dodged: 'bg-blue-100 text-blue-700 dark:bg-blue-900/30 dark:text-blue-400',
};

function verdictLabel(verdict: string, t: TFunction): string {
    switch (verdict) {
        case 'good_call': return t('decisionHub.verdict.goodCall');
        case 'regret': return t('decisionHub.verdict.regret');
        case 'missed_opportunity': return t('decisionHub.verdict.missed');
        case 'bullet_dodged': return t('decisionHub.verdict.dodged');
        default: return verdict;
    }
}

const VerificationStatusBadge: React.FC<{ status: VerificationStatus; verificationResult?: string | null }> = ({ status, verificationResult }) => {
    const { t } = useTranslation('reports');
    if (status === 'verified') {
        const isManual = !!verificationResult;
        return (
            <span className="inline-flex items-center gap-1.5 rounded-full bg-emerald-100 px-2 py-0.5 text-[11px] font-medium text-emerald-700 dark:bg-emerald-900/30 dark:text-emerald-400">
                <span className="material-symbols-outlined !text-[12px]">check_circle</span>
                {isManual ? t('decisionHub.verification.manuallyVerified') : t('decisionHub.verification.verifiedBySync')}
            </span>
        );
    }
    if (status === 'unmatched') {
        return (
            <span className="inline-flex items-center gap-1.5 rounded-full bg-orange-100 px-2 py-0.5 text-[11px] font-medium text-orange-700 dark:bg-orange-900/30 dark:text-orange-400">
                <span className="material-symbols-outlined !text-[12px]">warning</span>
                {t('decisionHub.verification.noMatchingTransaction')}
            </span>
        );
    }
    return (
        <span className="inline-flex items-center gap-1.5 rounded-full bg-yellow-100 px-2 py-0.5 text-[11px] font-medium text-yellow-700 dark:bg-yellow-900/30 dark:text-yellow-400">
            <span className="inline-block size-1.5 rounded-full bg-yellow-500 dark:bg-yellow-300" />
            {t('decisionHub.verification.pending')}
        </span>
    );
};

const ScorecardRow: React.FC<{ item: ScorecardItem }> = ({ item }) => {
    const { t } = useTranslation('reports');
    return (
    <tr className="hover:bg-slate-50 dark:hover:bg-slate-800/30 transition-colors">
        <td className="py-3 pr-4 text-slate-500 dark:text-slate-400 whitespace-nowrap">{item.date}</td>
        <td className="py-3 pr-4">
            <span className="font-medium text-slate-900 dark:text-white">{item.asset_name || item.asset_id}</span>
            {item.asset_name && <span className="block text-xs text-slate-400">{item.asset_id}</span>}
        </td>
        <td className="py-3 pr-4">
            <span className={`inline-flex items-center px-2 py-0.5 rounded text-xs font-medium ${
                item.action?.toLowerCase().includes('buy') || item.action?.toLowerCase() === 'buy'
                    ? 'bg-emerald-100 text-emerald-700 dark:bg-emerald-900/30 dark:text-emerald-400'
                    : 'bg-rose-100 text-rose-700 dark:bg-rose-900/30 dark:text-rose-400'
            }`}>
                {item.action}
            </span>
        </td>
        <td className="py-3 pr-4 text-slate-500 dark:text-slate-400 text-xs">{item.source || '—'}</td>
        <td className="py-3 pr-4 max-w-[220px]">
            {item.linked_insight_title ? (
                <div>
                    <span className="block text-xs font-medium text-slate-700 dark:text-slate-200 line-clamp-2">{item.linked_insight_title}</span>
                    <span className="inline-flex mt-1 px-2 py-0.5 rounded text-[11px] font-medium bg-slate-100 text-slate-600 dark:bg-slate-800 dark:text-slate-300">
                        {item.match_status || t('decisionHub.scorecard.matched')}
                    </span>
                </div>
            ) : (
                <span className="text-xs text-slate-400">{item.match_status || t('decisionHub.scorecard.unmatched')}</span>
            )}
        </td>
        <td className="py-3 pr-4 whitespace-nowrap">
            <VerificationStatusBadge status={item.verification_status} verificationResult={item.verification_result} />
        </td>
        <td className="py-3 pr-4 max-w-[260px]">
            {item.verification_result ? (
                <span className="text-xs text-slate-600 dark:text-slate-300 line-clamp-2" title={item.verification_result}>
                    {item.verification_result}
                </span>
            ) : item.why_unscored ? (
                <span className="text-xs text-slate-400" title={item.why_unscored}>
                    {item.why_unscored.replaceAll('_', ' ')}
                </span>
            ) : (
                <span className="text-xs text-slate-400">—</span>
            )}
        </td>
        <td className="py-3 pr-4 text-right whitespace-nowrap">
            {item.outcome_pct != null ? (
                <span className={`font-semibold ${item.outcome_pct >= 0 ? 'text-green-600 dark:text-green-400' : 'text-red-600 dark:text-red-400'}`}>
                    {item.outcome_pct > 0 ? '+' : ''}{item.outcome_pct.toFixed(2)}%
                </span>
            ) : (
                <span className="text-slate-400">—</span>
            )}
        </td>
        <td className="py-3 pr-4">
            {item.verdict ? (
                <span className={`inline-flex items-center px-2 py-0.5 rounded text-xs font-medium ${VERDICT_STYLES[item.verdict] || 'bg-slate-100 text-slate-600'}`}>
                    {verdictLabel(item.verdict, t)}
                </span>
            ) : (
                <span className="text-slate-400 text-xs">—</span>
            )}
        </td>
        <td className="py-3">
            {item.grade ? (
                <span className={`inline-flex items-center justify-center w-6 h-6 rounded-full text-xs font-bold ${
                    item.grade === 'A'
                        ? 'bg-green-100 text-green-700 dark:bg-green-900/30 dark:text-green-400'
                        : 'bg-yellow-100 text-yellow-700 dark:bg-yellow-900/30 dark:text-yellow-400'
                }`}>
                    {item.grade}
                </span>
            ) : (
                <span
                    className="text-slate-400 text-xs"
                    title={t('decisionHub.scorecard.noReview')}
                >
                    —
                </span>
            )}
        </td>
    </tr>
    );
};

const DecisionPatternsSection: React.FC<{ intelligence: DecisionIntelligence }> = ({ intelligence }) => {
    const { t } = useTranslation('reports');
    return (
    <div className="space-y-6">
        <FunnelChart funnel={intelligence.decision_patterns.funnel} />

        <div className="grid grid-cols-1 xl:grid-cols-2 gap-6">
            <div className="rounded-xl border border-slate-200 dark:border-border-dark p-4">
                <h3 className="text-sm font-semibold text-slate-900 dark:text-white mb-3">{t('decisionHub.intelligence.sourceMix')}</h3>
                <div className="space-y-3">
                    {intelligence.decision_patterns.sources.map(source => (
                        <div key={source.source} className="flex items-center justify-between gap-4">
                            <div>
                                <p className="text-sm font-medium text-slate-900 dark:text-white">{source.source}</p>
                                <p className="text-xs text-slate-400">
                                    {t('decisionHub.intelligence.sourceMixDetail', {
                                        adopted: source.adopted,
                                        pending: source.pending,
                                        rejected: source.rejected,
                                        linkedTrades: source.linked_trades ?? 0,
                                    })}
                                </p>
                            </div>
                            <span className="text-sm font-semibold text-slate-600 dark:text-slate-300">{source.total}</span>
                        </div>
                    ))}
                </div>
            </div>

            <div className="rounded-xl border border-slate-200 dark:border-border-dark p-4">
                <h3 className="text-sm font-semibold text-slate-900 dark:text-white mb-3">{t('decisionHub.intelligence.leaderboard')}</h3>
                {intelligence.decision_patterns.leaderboard.length === 0 ? (
                    <p className="text-sm text-slate-400 dark:text-slate-500 italic">
                        {t('decisionHub.intelligence.noLeaderboard')}
                    </p>
                ) : (
                    <div className="space-y-3">
                        {intelligence.decision_patterns.leaderboard.map(src => (
                            <div key={src.source} className="flex items-center justify-between gap-4">
                                <div>
                                    <p className="text-sm font-medium text-slate-900 dark:text-white">{src.source}</p>
                                    <p className="text-xs text-slate-400">
                                        {t('decisionHub.intelligence.scoredFraction', { scored: src.scored, total: src.total })}
                                    </p>
                                </div>
                                <span className="text-sm font-semibold text-slate-600 dark:text-slate-300">
                                    {src.hit_rate}%
                                </span>
                            </div>
                        ))}
                    </div>
                )}
            </div>
        </div>
    </div>
    );
};

// ── Funnel chart ─────────────────────────────────────────────────────────────

const FunnelStep: React.FC<{ label: string; value: number; sublabel?: string; color: string }> = ({ label, value, sublabel, color }) => (
    <div className="flex-1 text-center">
        <div className={`mx-auto w-20 h-20 rounded-full flex flex-col items-center justify-center ${color} mb-2`}>
            <span className="text-2xl font-bold">{value}</span>
        </div>
        <p className="text-sm font-medium text-slate-700 dark:text-slate-200">{label}</p>
        {sublabel && <p className="text-xs text-slate-400 mt-0.5">{sublabel}</p>}
    </div>
);

const FunnelArrow: React.FC = () => (
    <div className="flex items-center pb-8 px-1">
        <span className="material-symbols-outlined text-slate-300 dark:text-slate-600 text-[28px]">chevron_right</span>
    </div>
);

const FunnelChart: React.FC<{ funnel: DecisionFunnel }> = ({ funnel }) => {
    const { t } = useTranslation('reports');
    const scored = funnel.good_call + funnel.regret + funnel.missed_opportunity + funnel.bullet_dodged;
    const adoptionRate = funnel.total > 0 ? Math.round(funnel.adopted / funnel.total * 100) : 0;
    const hitRate = scored > 0 ? Math.round(funnel.good_call / scored * 100) : 0;

    return (
        <div>
            <div className="flex items-start">
                <FunnelStep
                    label={t('decisionHub.funnel.totalInsights')}
                    value={funnel.total}
                    sublabel={t('decisionHub.funnel.allRecommendations')}
                    color="bg-slate-100 text-slate-700 dark:bg-slate-800 dark:text-slate-200"
                />
                <FunnelArrow />
                <FunnelStep
                    label={t('decisionHub.funnel.adopted')}
                    value={funnel.adopted}
                    sublabel={t('decisionHub.funnel.adoptionRatePct', { pct: adoptionRate })}
                    color="bg-indigo-100 text-indigo-700 dark:bg-indigo-900/40 dark:text-indigo-300"
                />
                <FunnelArrow />
                <FunnelStep
                    label={t('decisionHub.funnel.scored')}
                    value={scored}
                    sublabel={t('decisionHub.funnel.withVerification')}
                    color="bg-blue-100 text-blue-700 dark:bg-blue-900/40 dark:text-blue-300"
                />
                <FunnelArrow />
                <FunnelStep
                    label={t('decisionHub.funnel.goodCalls')}
                    value={funnel.good_call}
                    sublabel={t('decisionHub.funnel.hitRatePct', { pct: hitRate })}
                    color="bg-green-100 text-green-700 dark:bg-green-900/40 dark:text-green-300"
                />
            </div>

            {/* Outcome breakdown */}
            {(scored > 0 || (funnel.linked_adopted_trades || 0) > 0) && (
                <div className="mt-6 flex gap-3 flex-wrap">
                    {(funnel.linked_adopted_trades || 0) > 0 && (
                        <span className="inline-flex items-center gap-1 px-3 py-1.5 rounded-full text-xs font-medium bg-indigo-100 text-indigo-700 dark:bg-indigo-900/30 dark:text-indigo-300">
                            <span className="material-symbols-outlined text-[14px]">link</span>
                            {t('decisionHub.funnel.linkedTrades', { count: funnel.linked_adopted_trades })}
                        </span>
                    )}
                    {funnel.good_call > 0 && (
                        <span className="inline-flex items-center gap-1 px-3 py-1.5 rounded-full text-xs font-medium bg-green-100 text-green-700 dark:bg-green-900/30 dark:text-green-400">
                            <span className="material-symbols-outlined text-[14px]">check_circle</span>
                            {t('decisionHub.funnel.goodCallCount', { count: funnel.good_call, s: funnel.good_call !== 1 ? 's' : '' })}
                        </span>
                    )}
                    {funnel.regret > 0 && (
                        <span className="inline-flex items-center gap-1 px-3 py-1.5 rounded-full text-xs font-medium bg-red-100 text-red-700 dark:bg-red-900/30 dark:text-red-400">
                            <span className="material-symbols-outlined text-[14px]">cancel</span>
                            {t('decisionHub.funnel.regretCount', { count: funnel.regret, s: funnel.regret !== 1 ? 's' : '' })}
                        </span>
                    )}
                    {funnel.missed_opportunity > 0 && (
                        <span className="inline-flex items-center gap-1 px-3 py-1.5 rounded-full text-xs font-medium bg-yellow-100 text-yellow-700 dark:bg-yellow-900/30 dark:text-yellow-400">
                            <span className="material-symbols-outlined text-[14px]">warning</span>
                            {t('decisionHub.funnel.missedCount', { count: funnel.missed_opportunity })}
                        </span>
                    )}
                    {funnel.bullet_dodged > 0 && (
                        <span className="inline-flex items-center gap-1 px-3 py-1.5 rounded-full text-xs font-medium bg-blue-100 text-blue-700 dark:bg-blue-900/30 dark:text-blue-400">
                            <span className="material-symbols-outlined text-[14px]">shield</span>
                            {t('decisionHub.funnel.bulletDodgedCount', { count: funnel.bullet_dodged })}
                        </span>
                    )}
                </div>
            )}
        </div>
    );
};

// ── Alerts section ────────────────────────────────────────────────────────────

const ALERT_PRIORITY_STYLES: Record<string, string> = {
    high: 'bg-red-100 text-red-700 dark:bg-red-900/30 dark:text-red-400',
    medium: 'bg-yellow-100 text-yellow-700 dark:bg-yellow-900/30 dark:text-yellow-400',
    low: 'bg-blue-100 text-blue-700 dark:bg-blue-900/30 dark:text-blue-400',
};

const ALERT_CATEGORY_ICONS: Record<string, string> = {
    drift: 'trending_down',
    verification: 'fact_check',
    trading: 'swap_horiz',
    strategy: 'policy',
};

const AlertsSection: React.FC<{
    alerts: DecisionAlert[];
    allAlerts: DecisionAlert[];
    filter: AlertFilter;
    onFilterChange: (filter: AlertFilter) => void;
}> = ({ alerts, allAlerts, filter, onFilterChange }) => {
    const { t } = useTranslation('reports');
    return (
    <div className="bg-white dark:bg-card-dark rounded-xl border border-slate-200 dark:border-border-dark overflow-hidden">
        <div className="px-6 py-4 border-b border-slate-200 dark:border-border-dark flex items-center justify-between">
            <div className="space-y-3">
                <h2 className="text-base font-semibold text-slate-900 dark:text-white flex items-center gap-2">
                    <span className="material-symbols-outlined text-[18px]">notifications</span>
                    {t('decisionHub.alerts.title')}
                </h2>
                <div className="flex items-center gap-2 flex-wrap">
                    {(['all', 'drift', 'verification', 'trading', 'strategy'] as AlertFilter[]).map(option => {
                        const count = option === 'all'
                            ? allAlerts.length
                            : allAlerts.filter(alert => alert.category === option).length;
                        if (!count) return null;
                        return (
                            <button
                                key={option}
                                type="button"
                                onClick={() => onFilterChange(option)}
                                className={`px-2.5 py-1 rounded-full text-xs font-semibold transition-colors ${
                                    filter === option
                                        ? 'bg-slate-900 text-white dark:bg-white dark:text-slate-900'
                                        : 'bg-slate-100 text-slate-500 dark:bg-slate-800 dark:text-slate-300'
                                }`}
                            >
                                {option === 'all' ? t('decisionHub.alerts.all') : option} {count}
                            </button>
                        );
                    })}
                </div>
            </div>
            {alerts.length > 0 && (
                <div className="flex items-center gap-2">
                    {['high', 'medium', 'low'].map(p => {
                        const count = alerts.filter(a => a.priority === p).length;
                        if (!count) return null;
                        return (
                            <span key={p} className={`px-2 py-0.5 rounded-full text-xs font-semibold ${ALERT_PRIORITY_STYLES[p]}`}>
                                {count} {p}
                            </span>
                        );
                    })}
                </div>
            )}
        </div>
        {alerts.length === 0 ? (
            <div className="px-6 py-5 text-sm text-slate-400 dark:text-slate-500 italic">{t('decisionHub.alerts.none')}</div>
        ) : (
            <div className="divide-y divide-slate-100 dark:divide-slate-800">
                {alerts.map((alert, i) => (
                    <div key={i} className="px-6 py-4 flex items-start gap-4 hover:bg-slate-50 dark:hover:bg-slate-800/30 transition-colors">
                        <div className="mt-0.5 size-9 rounded-lg bg-slate-100 dark:bg-slate-800 flex items-center justify-center shrink-0">
                            <span className="material-symbols-outlined text-[18px] text-slate-500 dark:text-slate-400">
                                {ALERT_CATEGORY_ICONS[alert.category] || 'info'}
                            </span>
                        </div>
                        <div className="flex-1 min-w-0">
                            <div className="flex items-center gap-2 mb-0.5">
                                <p className="text-sm font-semibold text-slate-900 dark:text-white truncate">{alert.title}</p>
                                <span className={`shrink-0 px-2 py-0.5 rounded text-xs font-medium uppercase ${ALERT_PRIORITY_STYLES[alert.priority]}`}>
                                    {alert.priority}
                                </span>
                            </div>
                            <p className="text-xs text-slate-500 dark:text-slate-400">{alert.message}</p>
                        </div>
                        <span className="shrink-0 px-2 py-0.5 rounded bg-slate-100 dark:bg-slate-800 text-xs text-slate-500 dark:text-slate-400 capitalize">
                            {alert.category}
                        </span>
                    </div>
                ))}
            </div>
        )}
    </div>
    );
};

// ── Stat card (shared) ───────────────────────────────────────────────────────

const StatCard = ({ title, value, icon, color = 'text-slate-900 dark:text-white', onClick }: any) => (
    <button
        type="button"
        onClick={onClick}
        className="bg-white dark:bg-card-dark p-6 rounded-xl shadow-sm border border-slate-200 dark:border-border-dark flex items-center gap-4 text-left hover:border-slate-300 dark:hover:border-slate-600 transition-colors"
    >
        <div className={`p-3 rounded-lg bg-slate-100 dark:bg-slate-800 ${color} bg-opacity-10`}>
            <span className={`material-symbols-outlined ${color}`}>{icon}</span>
        </div>
        <div>
            <p className="text-sm font-medium text-slate-500 dark:text-slate-400">{title}</p>
            <h3 className="text-2xl font-bold text-slate-900 dark:text-white">{value}</h3>
        </div>
    </button>
);
