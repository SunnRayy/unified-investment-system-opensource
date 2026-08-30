import React, { useEffect, useState, useCallback } from 'react';
import { useTranslation, Trans } from 'react-i18next';
// Side-effect import: guarantees the i18next singleton is initialized before
// AdoptionTooltip/ComparisonTooltip (exported standalone below and rendered
// without a provider in tests/verification-tooltips.test.tsx) ever call
// useTranslation — react-i18next's hook throws without an initialized
// instance, and unlike most pages here nothing else in this module's import
// graph (services/api, recharts) pulls src/i18n in as a side effect.
import '../src/i18n';
import { api, VerificationPeriod, AdoptionMonth } from '../src/services/api';
import {
    BarChart, Bar, Cell,
    XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer,
} from 'recharts';

type TooltipPayloadEntry = { value?: number | string | null };

export const Verification: React.FC = () => {
    const { t } = useTranslation('reports');
    const [latest, setLatest] = useState<VerificationPeriod | null>(null);
    const [loading, setLoading] = useState(true);
    const [running, setRunning] = useState(false);
    const [error, setError] = useState<string | null>(null);

    const loadLatest = useCallback(async () => {
        setError(null);
        try {
            const data = await api.getLatestVerification();
            setLatest(data);
        } catch (error) {
            console.error('Failed to fetch latest verification:', error);
            setError(t('verification.errors.load'));
        } finally {
            setLoading(false);
        }
    }, []);

    useEffect(() => { loadLatest(); }, [loadLatest]);

    const handleRunVerification = async () => {
        setRunning(true);
        try {
            const data = await api.triggerVerification();
            setLatest(data);
        } catch (error) {
            console.error('Failed to run verification:', error);
            setError(t('verification.errors.run'));
        } finally {
            setRunning(false);
        }
    };

    if (loading) return <div className="p-8 text-center text-slate-500">{t('verification.loading')}</div>;

    const adoptionHistory: AdoptionMonth[] = latest?.adoption_history ?? [];
    const hasPortfolioData = latest?.portfolio_return != null || latest?.benchmark_return != null;
    const maxAdoptionRate = adoptionHistory.reduce((max, point) => Math.max(max, point.adoption_rate || 0), 0);
    const adoptionYAxisMax = Math.min(100, Math.max(50, Math.ceil(maxAdoptionRate / 10) * 10 || 50));

    const pvbData = [
        { name: t('verification.portfolio'), value: latest?.portfolio_return ?? 0 },
        { name: t('verification.benchmark'), value: latest?.benchmark_return ?? 0 },
    ];

    return (
        <div data-testid="verification-page" className="p-8 max-w-[1600px] mx-auto w-full space-y-6 bg-gray-50 dark:bg-background-dark min-h-screen">
            {error && (
                <div className="bg-red-50 border border-red-200 rounded-lg p-4 mb-4">
                    <p className="text-red-700 text-sm">{error}</p>
                </div>
            )}
            {/* Header */}
            <header className="flex justify-between items-center">
                <div>
                    <h1 className="text-3xl font-bold text-slate-900 dark:text-white tracking-tight">{t('verification.title')}</h1>
                    <p className="text-slate-500 dark:text-slate-400 mt-1">{t('verification.subtitle')}</p>
                </div>
                <button
                    onClick={handleRunVerification}
                    disabled={running}
                    className="flex items-center gap-2 px-6 py-2.5 bg-blue-600 hover:bg-blue-700 disabled:bg-blue-400 text-white rounded-lg text-sm font-semibold transition-colors shadow-sm"
                >
                    {running ? (
                        <svg className="animate-spin h-4 w-4" viewBox="0 0 24 24" fill="none">
                            <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
                            <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8v8H4z" />
                        </svg>
                    ) : (
                        <svg className="h-4 w-4" viewBox="0 0 24 24" fill="currentColor">
                            <path d="M8 5v14l11-7z" />
                        </svg>
                    )}
                    {running ? t('verification.running') : t('verification.runVerification')}
                </button>
            </header>

            {/* KPI Cards */}
            <div className="grid grid-cols-1 md:grid-cols-4 gap-4">
                <KPICard
                    title={t('verification.adoptionRate')}
                    value={latest?.adoption_rate ?? null}
                    suffix="%"
                    color="blue"
                />
                <KPICard
                    title={t('verification.verdictHitRate')}
                    value={latest?.verdict_hit_rate ?? null}
                    suffix="%"
                    color={latest?.verdict_hit_rate != null && latest.verdict_hit_rate >= 50 ? 'green' : 'yellow'}
                />
                <KPICard
                    title={t('verification.maxDrift')}
                    value={latest?.max_drift ?? null}
                    suffix="%"
                    color={latest?.max_drift != null && latest.max_drift > 5 ? 'red' : 'green'}
                />
                <KPICard
                    title={t('verification.totalInsights')}
                    value={latest?.total_insights ?? null}
                    suffix=""
                    color="purple"
                    noDecimals
                />
            </div>

            {/* Charts Row */}
            <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
                {/* Adoption Rate History */}
                <div className="bg-white dark:bg-card-dark rounded-xl shadow-sm border border-slate-200 dark:border-border-dark p-6">
                    <h3 className="text-lg font-bold text-slate-800 dark:text-slate-100 mb-4">{t('verification.adoptionRateHistory')}</h3>
                    {adoptionHistory.length === 0 ? (
                        <div className="h-[300px] flex items-center justify-center text-slate-400 text-sm">
                            {t('verification.noAdoptionHistory')}
                        </div>
                    ) : (
                        <div className="h-[300px]">
                            <ResponsiveContainer width="100%" height="100%">
                                <BarChart data={adoptionHistory} margin={{ top: 8, right: 8, left: 0, bottom: 0 }}>
                                    <CartesianGrid strokeDasharray="3 3" vertical={false} stroke="#E2E8F0" />
                                    <XAxis
                                        dataKey="period_start"
                                        tickFormatter={(v) => v ? v.substring(0, 7) : ''}
                                        stroke="#94A3B8"
                                        tick={{ fontSize: 11, fill: '#94A3B8' }}
                                        axisLine={false}
                                        tickLine={false}
                                    />
                                    <YAxis
                                        domain={[0, adoptionYAxisMax]}
                                        stroke="#94A3B8"
                                        unit="%"
                                        tick={{ fontSize: 11, fill: '#94A3B8' }}
                                        axisLine={false}
                                        tickLine={false}
                                        tickFormatter={(v) => `${v.toFixed(1)}%`}
                                    />
                                    <Tooltip content={<AdoptionTooltip />} cursor={{ fill: 'rgba(59, 130, 246, 0.08)' }} />
                                    <Bar dataKey="adoption_rate" fill="#3b82f6" radius={[4, 4, 0, 0]} maxBarSize={48} />
                                </BarChart>
                            </ResponsiveContainer>
                        </div>
                    )}
                </div>

                {/* Portfolio vs Benchmark */}
                <div className="bg-white dark:bg-card-dark rounded-xl shadow-sm border border-slate-200 dark:border-border-dark p-6">
                    <h3 className="text-lg font-bold text-slate-800 dark:text-slate-100 mb-4">{t('verification.portfolioVsBenchmark')}</h3>
                    {!hasPortfolioData ? (
                        <div className="h-[300px] flex items-center justify-center">
                            <div className="relative w-full h-full">
                                {/* Dim placeholder bars for visual effect */}
                                <div className="absolute inset-0 flex items-end justify-around px-16 pb-8 opacity-20 pointer-events-none">
                                    <div className="w-20 bg-blue-400 rounded-t" style={{ height: '60%' }} />
                                    <div className="w-20 bg-red-400 rounded-t" style={{ height: '35%' }} />
                                    <div className="w-20 bg-blue-300 rounded-t" style={{ height: '75%' }} />
                                </div>
                                {/* Overlay message */}
                                <div className="absolute inset-0 flex flex-col items-center justify-center gap-2 text-center">
                                    <div className="text-5xl mb-1">📊</div>
                                    <p className="text-sm font-medium text-slate-500 dark:text-slate-400">
                                        <Trans
                                            t={t}
                                            i18nKey="verification.insufficientData"
                                            components={{
                                                br: <br />,
                                                em: <span className="font-semibold text-slate-600 dark:text-slate-300" />,
                                            }}
                                        />
                                    </p>
                                </div>
                            </div>
                        </div>
                    ) : (
                        <div className="h-[300px]">
                            <ResponsiveContainer width="100%" height="100%">
                                <BarChart data={pvbData} margin={{ top: 8, right: 8, left: 0, bottom: 0 }}>
                                    <CartesianGrid strokeDasharray="3 3" vertical={false} stroke="#E2E8F0" />
                                    <XAxis
                                        dataKey="name"
                                        stroke="#94A3B8"
                                        tick={{ fontSize: 12, fill: '#94A3B8' }}
                                        axisLine={false}
                                        tickLine={false}
                                    />
                                    <YAxis
                                        stroke="#94A3B8"
                                        tick={{ fontSize: 11, fill: '#94A3B8' }}
                                        axisLine={false}
                                        tickLine={false}
                                    />
                                    <Tooltip content={<ComparisonTooltip />} cursor={{ fill: 'rgba(59, 130, 246, 0.08)' }} />
                                    <Bar dataKey="value" radius={[4, 4, 0, 0]} maxBarSize={72}>
                                        <Cell fill="#3b82f6" />
                                        <Cell fill="#64748B" />
                                    </Bar>
                                </BarChart>
                            </ResponsiveContainer>
                        </div>
                    )}
                </div>
            </div>

            {/* Verdict Breakdown Table */}
            {(latest?.verdict_breakdown ?? []).length > 0 && (
                <div className="bg-white dark:bg-card-dark rounded-xl shadow-sm border border-slate-200 dark:border-border-dark overflow-hidden">
                    <div className="px-6 py-4 border-b border-slate-200 dark:border-border-dark">
                        <h3 className="text-lg font-bold text-slate-800 dark:text-slate-100">{t('verification.verdictBreakdown')}</h3>
                    </div>
                    <table className="w-full text-left text-sm">
                        <thead className="bg-slate-50 dark:bg-slate-800/50">
                            <tr>
                                <th className="px-6 py-3 text-xs font-semibold text-slate-500 dark:text-slate-400 uppercase tracking-wider">{t('verification.columns.month')}</th>
                                <th className="px-6 py-3 text-xs font-semibold text-slate-500 dark:text-slate-400 uppercase tracking-wider text-right">{t('verification.columns.goodCalls')}</th>
                                <th className="px-6 py-3 text-xs font-semibold text-slate-500 dark:text-slate-400 uppercase tracking-wider text-right">{t('verification.columns.regrets')}</th>
                                <th className="px-6 py-3 text-xs font-semibold text-slate-500 dark:text-slate-400 uppercase tracking-wider text-right">{t('verification.columns.missed')}</th>
                                <th className="px-6 py-3 text-xs font-semibold text-slate-500 dark:text-slate-400 uppercase tracking-wider text-right">{t('verification.columns.totalScored')}</th>
                            </tr>
                        </thead>
                        <tbody className="divide-y divide-slate-100 dark:divide-slate-800">
                            {(latest?.verdict_breakdown ?? []).map((v, i) => (
                                <tr key={i} className="hover:bg-slate-50 dark:hover:bg-slate-800/50 transition-colors">
                                    <td className="px-6 py-3.5 font-medium text-slate-700 dark:text-slate-200">
                                        {v.period_start ? v.period_start.substring(0, 7) : '—'}
                                    </td>
                                    <td className="px-6 py-3.5 text-right">
                                        <Badge value={v.good_calls} variant="green" />
                                    </td>
                                    <td className="px-6 py-3.5 text-right">
                                        <Badge value={v.regrets} variant="red" />
                                    </td>
                                    <td className="px-6 py-3.5 text-right text-slate-500 dark:text-slate-400">
                                        {v.missed_opportunity}
                                    </td>
                                    <td className="px-6 py-3.5 text-right">
                                        <Badge value={v.total_scored} variant="neutral" />
                                    </td>
                                </tr>
                            ))}
                        </tbody>
                    </table>
                </div>
            )}
        </div>
    );
};

// ─── Sub-components ──────────────────────────────────────────────────────────

const KPICard = ({
    title, value, suffix, color, noDecimals,
}: {
    title: string;
    value: number | null | undefined;
    suffix: string;
    color: string;
    noDecimals?: boolean;
}) => {
    const { t } = useTranslation('reports');
    const isNull = value == null || (typeof value === 'number' && isNaN(value));

    let displayValue = t('verification.notAvailable');
    let colorClass = 'text-slate-400 dark:text-slate-500';

    if (!isNull) {
        displayValue = noDecimals
            ? String(Math.round(value as number)) + suffix
            : (value as number).toFixed(1) + suffix;

        if (color === 'green')  colorClass = 'text-green-600 dark:text-green-400';
        else if (color === 'red')    colorClass = 'text-red-600 dark:text-red-400';
        else if (color === 'yellow') colorClass = 'text-yellow-500 dark:text-yellow-400';
        else if (color === 'orange') colorClass = 'text-orange-500 dark:text-orange-400';
        else if (color === 'blue')   colorClass = 'text-blue-600 dark:text-blue-400';
        else if (color === 'purple') colorClass = 'text-purple-600 dark:text-purple-400';
        else colorClass = 'text-slate-900 dark:text-white';
    }

    return (
        <div className="bg-white dark:bg-card-dark p-6 rounded-xl shadow-sm border border-slate-200 dark:border-border-dark">
            <p className="text-sm font-medium text-slate-500 dark:text-slate-400 mb-2">{title}</p>
            <p className={`text-4xl font-bold tracking-tight ${colorClass}`}>{displayValue}</p>
        </div>
    );
};

const Badge = ({ value, variant }: { value: number | string; variant: 'green' | 'red' | 'neutral' }) => {
    const classes = {
        green:   'bg-green-100 text-green-700 dark:bg-green-900/30 dark:text-green-400',
        red:     'bg-red-100 text-red-600 dark:bg-red-900/30 dark:text-red-400',
        neutral: 'bg-slate-100 text-slate-600 dark:bg-slate-800 dark:text-slate-300',
    }[variant];

    return (
        <span className={`inline-flex items-center justify-center min-w-[3rem] px-2.5 py-0.5 rounded-full text-xs font-semibold ${classes}`}>
            {value}
        </span>
    );
};

export const ChartTooltipCard = ({
    title,
    rows,
}: {
    title: string;
    rows: Array<{ label: string; value: string; valueClassName?: string }>;
}) => (
    <div className="min-w-[148px] rounded-xl border border-slate-700/80 bg-slate-900/95 px-3.5 py-3 text-xs text-slate-100 shadow-xl backdrop-blur">
        <p className="mb-2 text-sm font-semibold text-white">{title}</p>
        <div className="space-y-1.5">
            {rows.map((row) => (
                <div key={row.label} className="flex items-center justify-between gap-4">
                    <span className="text-slate-300">{row.label}</span>
                    <span className={`font-semibold text-white ${row.valueClassName ?? ''}`}>{row.value}</span>
                </div>
            ))}
        </div>
    </div>
);

export const AdoptionTooltip = ({ active, payload, label }: { active?: boolean; payload?: TooltipPayloadEntry[]; label?: string }) => {
    const { t } = useTranslation('reports');
    if (!active || !payload?.length) return null;
    const period = label ? label.substring(0, 7) : label;
    const rate = payload[0]?.value as number;
    return <ChartTooltipCard title={period} rows={[{ label: t('verification.adoptionRate'), value: `${rate?.toFixed(1)}%`, valueClassName: 'text-blue-300' }]} />;
};

export const ComparisonTooltip = ({ active, payload, label }: { active?: boolean; payload?: TooltipPayloadEntry[]; label?: string }) => {
    const { t } = useTranslation('reports');
    if (!active || !payload?.length) return null;
    const rawValue = payload[0]?.value;
    const value = typeof rawValue === 'number' ? rawValue : Number(rawValue ?? 0);
    const valueClassName = value >= 0 ? 'text-blue-300' : 'text-rose-300';
    return <ChartTooltipCard title={label ?? t('verification.series')} rows={[{ label: t('verification.return'), value: `${value.toFixed(1)}%`, valueClassName }]} />;
};
