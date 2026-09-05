import React, { useEffect, useMemo, useState } from 'react';
import { useTranslation } from 'react-i18next';
import type { TFunction } from 'i18next';
import { BarChart, Bar, XAxis, YAxis, Tooltip, ResponsiveContainer, Cell, CartesianGrid } from 'recharts';

import { api, StrategyReport, StrategyScopeSummary } from '../src/services/api';
import { UnforcedErrors } from '../components/UnforcedErrors';

export const formatScopeTickLabel = (label: string): string[] => {
    const compactMap: Record<string, string[]> = {
        'Fixed Income': ['Fixed', 'Income'],
        'US Equity': ['US', 'Equity'],
        'CN Equity': ['CN', 'Equity'],
        'HK Equity': ['HK', 'Equity'],
    };
    return compactMap[label] || [label];
};

const formatBehavioralDimensionLabel = (dimension: string): string =>
    dimension
        .split('_')
        .map((part) => part.charAt(0).toUpperCase() + part.slice(1))
        .join(' ');

export const StrategyAlignment: React.FC = () => {
    const { t } = useTranslation('reports');
    const [report, setReport] = useState<StrategyReport | null>(null);
    const [loading, setLoading] = useState(true);
    const [running, setRunning] = useState(false);
    const [error, setError] = useState<string | null>(null);
    const fetchAlignment = async () => {
        try {
            setError(null);
            const data = await api.getStrategyAlignment();
            setReport(data.report);
            return data.report;
        } catch {
            setError(t('strategyAlignment.errors.load'));
            return null;
        } finally {
            setLoading(false);
        }
    };

    useEffect(() => {
        fetchAlignment();
    }, []);

    const handleRunReview = async () => {
        setRunning(true);
        try {
            const triggerResult = await api.triggerStrategyReview();
            let nextReport = triggerResult.report;
            try {
                const refreshed = await api.getStrategyAlignment();
                if (refreshed.report) {
                    nextReport = refreshed.report;
                }
            } catch {
                // Keep the freshly generated review payload when refresh fails.
            }
            if (nextReport) {
                setReport(nextReport);
            }
            setError(null);
        } catch {
            setError(t('strategyAlignment.errors.run'));
        } finally {
            setRunning(false);
            setLoading(false);
        }
    };

    if (loading) {
        return (
            <div className="p-8 text-center text-slate-500 dark:text-slate-400">
                {t('strategyAlignment.loading')}
            </div>
        );
    }

    return (
        <div data-testid="strategy-page" className="p-8 max-w-[1600px] mx-auto w-full space-y-8 bg-gray-50 dark:bg-background-dark min-h-screen">
            <header className="flex items-start justify-between">
                <div>
                    <h1 className="text-3xl font-bold text-slate-900 dark:text-white tracking-tight">{t('strategyAlignment.title')}</h1>
                    <p className="text-slate-500 dark:text-slate-400 mt-1">{t('strategyAlignment.subtitle')}</p>
                </div>
                <button
                    onClick={handleRunReview}
                    disabled={running}
                    className="flex items-center gap-2 px-4 py-2 bg-primary text-white rounded-lg text-sm font-medium hover:bg-primary/90 disabled:opacity-60 transition-colors"
                >
                    <span className="material-symbols-outlined text-[18px]">{running ? 'sync' : 'play_arrow'}</span>
                    {running ? t('strategyAlignment.running') : t('strategyAlignment.runReview')}
                </button>
            </header>

            {error && (
                <div className="p-4 rounded-lg bg-red-50 dark:bg-red-900/20 border border-red-200 dark:border-red-800 text-red-700 dark:text-red-400 text-sm">
                    {error}
                </div>
            )}

            {!report ? (
                <EmptyState onRunReview={handleRunReview} running={running} />
            ) : (
                <>
                    <ScopeSummarySection report={report} />
                    <BehavioralProfileSection report={report} />
                    <AllocationDriftSection report={report} />
                    <TradingFrequencySection report={report} />
                    <ContrarianSection report={report} />
                </>
            )}

            {/* Unforced Errors — always visible, self-contained fetch */}
            <UnforcedErrors />
        </div>
    );
};

const EmptyState: React.FC<{ onRunReview: () => void; running: boolean }> = ({ onRunReview, running }) => {
    const { t } = useTranslation('reports');
    return (
        <div className="bg-white dark:bg-card-dark rounded-xl border border-slate-200 dark:border-border-dark p-16 text-center">
            <span className="material-symbols-outlined text-5xl text-slate-300 dark:text-slate-600 block mb-4">policy</span>
            <p className="text-slate-600 dark:text-slate-300 font-semibold text-lg mb-2">{t('strategyAlignment.emptyState.title')}</p>
            <p className="text-slate-400 dark:text-slate-500 text-sm mb-6">{t('strategyAlignment.emptyState.hint')}</p>
            <button
                onClick={onRunReview}
                disabled={running}
                className="px-6 py-2.5 bg-primary text-white rounded-lg text-sm font-medium hover:bg-primary/90 disabled:opacity-60 transition-colors"
            >
                {running ? t('strategyAlignment.running') : t('strategyAlignment.runReviewNow')}
            </button>
        </div>
    );
};

const ALIGNMENT_STYLES: Record<string, string> = {
    aligned: 'bg-green-100 text-green-700 dark:bg-green-900/30 dark:text-green-400 border-green-200 dark:border-green-800',
    drifting: 'bg-yellow-100 text-yellow-700 dark:bg-yellow-900/30 dark:text-yellow-400 border-yellow-200 dark:border-yellow-800',
    misaligned: 'bg-red-100 text-red-700 dark:bg-red-900/30 dark:text-red-400 border-red-200 dark:border-red-800',
};

function alignmentLabel(status: string, t: TFunction): string {
    switch (status) {
        case 'aligned': return t('strategyAlignment.status.aligned');
        case 'drifting': return t('strategyAlignment.status.drifting');
        case 'misaligned': return t('strategyAlignment.status.misaligned');
        default: return status;
    }
}

const ScopeSummarySection: React.FC<{ report: StrategyReport }> = ({ report }) => {
    const { t } = useTranslation('reports');
    return (
        <section className="grid grid-cols-1 lg:grid-cols-2 gap-6">
            <ScopeStatusCard
                title={t('strategyAlignment.strategicTargets')}
                status={report.target_scope_alignment_status}
                summary={report.target_scope_summary}
                reviewDate={report.review_date}
            />
            <ScopeStatusCard
                title={t('strategyAlignment.uisPortfolioScope')}
                status={report.uis_scope_alignment_status}
                summary={report.uis_scope_summary}
                reviewDate={report.review_date}
            />
        </section>
    );
};

const BehavioralProfileSection: React.FC<{ report: StrategyReport }> = ({ report }) => {
    const { t } = useTranslation('reports');
    const behavioralSummary = report.behavioral_summary ?? {};
    const entries = Object.entries(behavioralSummary);

    if (entries.length === 0) {
        return null;
    }

    return (
        <section className="bg-white dark:bg-card-dark rounded-xl border border-slate-200 dark:border-border-dark overflow-hidden">
            <div className="px-6 py-4 border-b border-slate-200 dark:border-border-dark">
                <h2 className="text-base font-semibold text-slate-900 dark:text-white">{t('strategyAlignment.behavioralProfile.title')}</h2>
                <p className="text-xs text-slate-400 mt-0.5">{t('strategyAlignment.behavioralProfile.subtitle')}</p>
            </div>
            <div className="p-6 grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-4">
                {entries.map(([dimension, item]) => {
                    const score = item.score == null ? t('strategyAlignment.notAvailable') : `${Math.round(item.score * 100)}%`;
                    return (
                        <div key={dimension} className="rounded-xl border border-slate-200 dark:border-border-dark bg-slate-50 dark:bg-slate-800/40 p-4">
                            <div className="flex items-start justify-between gap-4">
                                <div>
                                    <p className="text-sm font-semibold text-slate-900 dark:text-white">{formatBehavioralDimensionLabel(dimension)}</p>
                                    <p className="text-xs text-slate-400 mt-1">{item.label || t('strategyAlignment.notAvailable')}</p>
                                </div>
                                <span className="shrink-0 rounded-full bg-white dark:bg-slate-900/60 border border-slate-200 dark:border-slate-700 px-3 py-1 text-sm font-semibold text-slate-900 dark:text-white">
                                    {score}
                                </span>
                            </div>
                        </div>
                    );
                })}
            </div>
        </section>
    );
};

const ScopeStatusCard = ({
    title,
    status,
    summary,
    reviewDate,
}: {
    title: string;
    status: string;
    summary: StrategyScopeSummary;
    reviewDate: string;
}) => {
    const { t } = useTranslation('reports');
    return (
        <div className="w-full bg-white dark:bg-card-dark rounded-xl border border-slate-200 dark:border-border-dark p-6">
            <div className="flex items-start justify-between gap-4">
                <div>
                    <p className="text-sm font-semibold text-slate-900 dark:text-white">{title}</p>
                    <p className="text-xs text-slate-400 mt-1">{t('strategyAlignment.reviewDate', { date: reviewDate })}</p>
                </div>
                <span className={`px-3 py-1 rounded-full border text-xs font-semibold uppercase tracking-wide ${ALIGNMENT_STYLES[status] || ALIGNMENT_STYLES.drifting}`}>
                    {alignmentLabel(status, t)}
                </span>
            </div>
            <p className="mt-4 text-sm text-slate-600 dark:text-slate-300">{summary.coverage_note}</p>
            <div className="mt-4 flex flex-wrap gap-2">
                {summary.included_classes.map((cls) => (
                    <span key={cls} className="inline-flex items-center rounded-full bg-slate-100 dark:bg-slate-800 px-2.5 py-1 text-xs font-medium text-slate-600 dark:text-slate-300">
                        {cls}
                    </span>
                ))}
            </div>
            {(summary.excluded_classes ?? []).length > 0 && (
                <div className="mt-4">
                    <p className="text-xs font-semibold uppercase tracking-wide text-slate-400 mb-2">{t('strategyAlignment.excludedFromScope')}</p>
                    <div className="flex flex-wrap gap-2">
                        {(summary.excluded_classes ?? []).map((cls) => (
                            <span key={cls} className="inline-flex items-center rounded-full bg-amber-50 dark:bg-amber-900/20 px-2.5 py-1 text-xs font-medium text-amber-700 dark:text-amber-300">
                                {cls}
                            </span>
                        ))}
                    </div>
                </div>
            )}
        </div>
    );
};

const AllocationDriftSection: React.FC<{ report: StrategyReport }> = ({ report }) => {
    const { t } = useTranslation('reports');
    const alignment = report.target_scope_alignment;
    const summary = report.target_scope_summary;

    const data = useMemo(
        () =>
            Object.entries(alignment).map(([cls, item]) => ({
                name: cls,
                actual: item.actual_pct,
                target: item.target_pct ?? 0,
                drift: item.drift_pct ?? 0,
                drifting: item.status === 'drifting',
            })),
        [alignment],
    );

    if (data.length === 0) return null;

    return (
        <section id="strategy-allocation-drift" className="bg-white dark:bg-card-dark rounded-xl border border-slate-200 dark:border-border-dark overflow-hidden">
            <div className="px-6 py-4 border-b border-slate-200 dark:border-border-dark">
                <h2 className="text-base font-semibold text-slate-900 dark:text-white">{t('strategyAlignment.allocationDrift.title')}</h2>
                <p className="text-xs text-slate-400 mt-0.5">{t('strategyAlignment.allocationDrift.subtitle')}</p>
            </div>
            <div className="p-6">
                <p className="text-sm text-slate-500 dark:text-slate-400 mb-4">{summary.coverage_note}</p>
                <div className="flex items-center gap-6 mb-4 text-xs text-slate-500 dark:text-slate-400">
                    <span className="flex items-center gap-1.5"><span className="inline-block w-3 h-3 rounded-sm bg-indigo-500"></span>{t('strategyAlignment.actual')}</span>
                    <span className="flex items-center gap-1.5"><span className="inline-block w-3 h-3 rounded-sm bg-slate-400"></span>{t('strategyAlignment.target')}</span>
                </div>
                <ResponsiveContainer width="100%" height={320}>
                    <BarChart data={data} margin={{ top: 5, right: 20, left: 0, bottom: 82 }}>
                        <CartesianGrid strokeDasharray="3 3" vertical={false} stroke="#E2E8F0" />
                        <XAxis
                            dataKey="name"
                            height={62}
                            tickLine={false}
                            interval={0}
                            tick={<WrappedScopeTick />}
                        />
                        <YAxis tick={{ fontSize: 11, fill: '#94a3b8' }} tickFormatter={(v) => `${v}%`} />
                        <Tooltip content={<ScopeAlignmentTooltip />} cursor={{ fill: 'rgba(59, 130, 246, 0.08)' }} />
                        <Bar dataKey="actual" name="Actual" radius={[3, 3, 0, 0]}>
                            {data.map((entry, idx) => (
                                <Cell key={idx} fill={entry.drifting ? '#f97316' : '#6366f1'} />
                            ))}
                        </Bar>
                        <Bar dataKey="target" name="Target" fill="#94a3b8" radius={[3, 3, 0, 0]} />
                    </BarChart>
                </ResponsiveContainer>
                <div className="mt-6 overflow-x-auto">
                    <table className="w-full text-sm">
                        <thead>
                            <tr className="border-b border-slate-200 dark:border-border-dark text-left">
                                <th className="pb-2 pr-4 text-xs font-semibold uppercase tracking-wide text-slate-400">{t('strategyAlignment.class')}</th>
                                <th className="pb-2 pr-4 text-xs font-semibold uppercase tracking-wide text-slate-400 text-right">{t('strategyAlignment.actual')}</th>
                                <th className="pb-2 pr-4 text-xs font-semibold uppercase tracking-wide text-slate-400 text-right">{t('strategyAlignment.target')}</th>
                                <th className="pb-2 text-xs font-semibold uppercase tracking-wide text-slate-400 text-right">{t('strategyAlignment.drift')}</th>
                            </tr>
                        </thead>
                        <tbody className="divide-y divide-slate-100 dark:divide-slate-800">
                            {data.map((item) => (
                                <tr key={item.name}>
                                    <td className="py-3 pr-4 font-medium text-slate-900 dark:text-white">{item.name}</td>
                                    <td className="py-3 pr-4 text-right text-slate-600 dark:text-slate-300">{item.actual.toFixed(1)}%</td>
                                    <td className="py-3 pr-4 text-right text-slate-600 dark:text-slate-300">{item.target.toFixed(1)}%</td>
                                    <td className={`py-3 text-right font-medium ${item.drift === 0 ? 'text-slate-500 dark:text-slate-400' : item.drift > 0 ? 'text-amber-600 dark:text-amber-400' : 'text-blue-600 dark:text-blue-400'}`}>
                                        {item.drift > 0 ? '+' : ''}{item.drift.toFixed(1)}%
                                    </td>
                                </tr>
                            ))}
                        </tbody>
                    </table>
                </div>
            </div>
        </section>
    );
};

const WrappedScopeTick = ({ x = 0, y = 0, payload }: { x?: number; y?: number; payload?: { value?: string } }) => {
    const lines = formatScopeTickLabel(payload?.value || '');
    return (
        <g transform={`translate(${x},${y})`}>
            <text x={0} y={16} textAnchor="middle" fill="#94a3b8" fontSize={11}>
                {lines.map((line, index) => (
                    <tspan key={`${line}-${index}`} x={0} dy={index === 0 ? 0 : 12}>
                        {line}
                    </tspan>
                ))}
            </text>
        </g>
    );
};

const ScopeAlignmentTooltip = ({ active, payload, label }: { active?: boolean; payload?: Array<{ dataKey?: string; value?: number }>; label?: string }) => {
    const { t } = useTranslation('reports');
    if (!active || !payload?.length) return null;

    return (
        <div className="min-w-[150px] rounded-xl border border-slate-700/80 bg-slate-900/95 px-3.5 py-3 text-xs text-slate-100 shadow-xl backdrop-blur">
            <p className="mb-2 text-sm font-semibold text-white">{label}</p>
            <div className="space-y-1.5">
                {payload.map((item) => (
                    <div key={item.dataKey} className="flex items-center justify-between gap-4">
                        <span className="text-slate-300">{item.dataKey === 'actual' ? t('strategyAlignment.actual') : t('strategyAlignment.target')}</span>
                        <span className={`font-semibold ${item.dataKey === 'actual' ? 'text-blue-300' : 'text-slate-100'}`}>{Number(item.value ?? 0).toFixed(1)}%</span>
                    </div>
                ))}
            </div>
        </div>
    );
};

const FREQ_ASSESSMENT_STYLES: Record<string, string> = {
    aligned: 'bg-green-100 text-green-700 dark:bg-green-900/30 dark:text-green-400',
    moderate: 'bg-yellow-100 text-yellow-700 dark:bg-yellow-900/30 dark:text-yellow-400',
    high_frequency: 'bg-red-100 text-red-700 dark:bg-red-900/30 dark:text-red-400',
};

const TradingFrequencySection: React.FC<{ report: StrategyReport }> = ({ report }) => {
    const { t } = useTranslation('reports');
    const freq = report.trading_frequency;
    const cards = [
        { label: t('strategyAlignment.tradingFrequency.days30'), value: freq.period_30d },
        { label: t('strategyAlignment.tradingFrequency.days60'), value: freq.period_60d },
        { label: t('strategyAlignment.tradingFrequency.days90'), value: freq.period_90d },
    ];

    return (
        <section className="bg-white dark:bg-card-dark rounded-xl border border-slate-200 dark:border-border-dark overflow-hidden">
            <div className="px-6 py-4 border-b border-slate-200 dark:border-border-dark flex items-center justify-between">
                <div>
                    <h2 className="text-base font-semibold text-slate-900 dark:text-white">{t('strategyAlignment.tradingFrequency.title')}</h2>
                    <p className="text-xs text-slate-400 mt-0.5">{t('strategyAlignment.tradingFrequency.philosophyThreshold', { threshold: freq.philosophy_threshold })}</p>
                </div>
                <span className={`px-3 py-1 rounded-full text-xs font-semibold uppercase tracking-wide ${FREQ_ASSESSMENT_STYLES[freq.assessment] || FREQ_ASSESSMENT_STYLES.moderate}`}>
                    {freq.assessment.replace('_', ' ')}
                </span>
            </div>
            <div className="p-6 grid grid-cols-3 gap-4">
                {cards.map((card) => (
                    <div key={card.label} className="bg-slate-50 dark:bg-slate-800/50 rounded-lg p-4 text-center">
                        <p className="text-3xl font-bold text-slate-900 dark:text-white">{card.value}</p>
                        <p className="text-xs text-slate-500 dark:text-slate-400 mt-1">{card.label}</p>
                    </div>
                ))}
            </div>
        </section>
    );
};

const ContrarianSection: React.FC<{ report: StrategyReport }> = ({ report }) => {
    const { t } = useTranslation('reports');
    const score = report.contrarian_score;
    const details = report.contrarian_details;

    return (
        <section className="bg-white dark:bg-card-dark rounded-xl border border-slate-200 dark:border-border-dark overflow-hidden">
            <div className="px-6 py-4 border-b border-slate-200 dark:border-border-dark">
                <h2 className="text-base font-semibold text-slate-900 dark:text-white">{t('strategyAlignment.contrarian.title')}</h2>
                <p className="text-xs text-slate-400 mt-0.5">{t('strategyAlignment.contrarian.subtitle')}</p>
            </div>
            <div className="p-6">
                {details.status === 'insufficient_market_context' || score == null ? (
                    <div className="rounded-xl border border-slate-200 dark:border-border-dark bg-slate-50 dark:bg-slate-800/40 p-5">
                        <p className="text-lg font-semibold text-slate-900 dark:text-white">{t('strategyAlignment.contrarian.limitedData')}</p>
                        <p className="text-sm text-slate-500 dark:text-slate-400 mt-1">
                            {t('strategyAlignment.contrarian.limitedDataDetail')}
                        </p>
                        <p className="text-xs text-slate-400 mt-3">
                            {t('strategyAlignment.contrarian.reviewedSells', { sellCount: details.sell_count, panicCount: details.panic_sell_count })}
                        </p>
                    </div>
                ) : (
                    <div className="flex items-start gap-8">
                        <div className="text-center shrink-0">
                            <p className={`text-6xl font-bold ${score >= 70 ? 'text-green-600 dark:text-green-400' : score >= 50 ? 'text-yellow-600 dark:text-yellow-400' : 'text-red-600 dark:text-red-400'}`}>{score.toFixed(0)}</p>
                            <p className="text-xs text-slate-400 mt-1">{t('strategyAlignment.contrarian.outOf100')}</p>
                            <p className="text-xs text-slate-500 dark:text-slate-400 mt-2">
                                {t('strategyAlignment.contrarian.panicSells', { count: details.panic_sell_count, total: details.sell_count })}
                            </p>
                        </div>
                    </div>
                )}
            </div>
        </section>
    );
};

