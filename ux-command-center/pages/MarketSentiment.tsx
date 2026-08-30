import React, { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { SentimentAPI, SentimentResponse, SentimentIndicator } from '../src/services/api';

function fmtTimestamp(iso: string | null | undefined): string {
    if (!iso) return '';
    try {
        return new Date(iso).toLocaleString(undefined, {
            month: 'short', day: 'numeric',
            hour: '2-digit', minute: '2-digit',
        });
    } catch {
        return '';
    }
}

const SentimentCard: React.FC<{ indicator: SentimentIndicator }> = ({ indicator }) => {
    const { t } = useTranslation('reports');
    const badgeColorMap: Record<string, string> = {
        'red': 'bg-red-500/10 text-red-600 dark:text-red-400 border border-red-500/20',
        'orange': 'bg-orange-500/10 text-orange-600 dark:text-orange-400 border border-orange-500/20',
        'yellow': 'bg-amber-500/10 text-amber-600 dark:text-amber-400 border border-amber-500/20',
        'light-green': 'bg-emerald-400/10 text-emerald-600 dark:text-emerald-400 border border-emerald-500/20',
        'green': 'bg-emerald-500/10 text-emerald-600 dark:text-emerald-400 border border-emerald-500/20',
        'grey': 'bg-slate-500/10 text-slate-600 dark:text-slate-400 border border-slate-500/20',
    };

    const badgeStyle = badgeColorMap[indicator.zone_color] || 'bg-slate-500/10 text-slate-600 dark:text-slate-400 border border-slate-500/20';
    const isStale = indicator.is_stale === true;
    const isUnavailable = indicator.display_value === 'Unavailable';
    const dataAge = fmtTimestamp(indicator.updated_at);

    return (
        <div className={`p-5 rounded-xl border shadow-sm flex flex-col justify-between transition-all ${
            isStale && !isUnavailable
                ? 'border-amber-300 dark:border-amber-700/50 bg-amber-50/40 dark:bg-amber-900/10'
                : 'border-slate-200 dark:border-border-dark bg-white dark:bg-card-dark'
        }`}>
            <div className="flex justify-between items-start mb-2">
                <p className="text-[10px] font-bold text-slate-500 dark:text-slate-400 uppercase tracking-wider">
                    {indicator.indicator_name}
                </p>
                <div className="flex flex-col items-end gap-1">
                    <div className={`px-2 py-0.5 rounded text-[10px] font-bold ${badgeStyle}`}>
                        {indicator.zone}
                    </div>
                    {isStale && !isUnavailable && (
                        <span className="px-1.5 py-0.5 rounded text-[9px] font-semibold bg-amber-100 text-amber-700 dark:bg-amber-900/30 dark:text-amber-400 border border-amber-200 dark:border-amber-700/40">
                            {t('marketSentiment.stale')}
                        </span>
                    )}
                </div>
            </div>
            <div className="flex items-baseline gap-2 mt-2">
                <p className={`text-2xl font-bold font-mono ${
                    isUnavailable
                        ? 'text-slate-400 dark:text-slate-500'
                        : 'text-slate-800 dark:text-slate-200'
                }`}>
                    {indicator.display_value}
                </p>
            </div>
            <div className="mt-3 pt-3 border-t border-slate-100 dark:border-border-dark/50 space-y-1">
                <p className="text-[11px] text-slate-500 dark:text-slate-400">
                    {indicator.description}
                </p>
                {isStale && indicator.error_detail && (
                    <p className="text-[10px] text-amber-600 dark:text-amber-400">
                        {t('marketSentiment.refreshFailedDetail', { detail: indicator.error_detail })}
                    </p>
                )}
                {dataAge && (
                    <p className="text-[10px] text-slate-400 dark:text-slate-500">
                        {isStale ? t('marketSentiment.lastGoodData') : t('marketSentiment.asOf')}{dataAge}
                    </p>
                )}
            </div>
        </div>
    );
};

export const MarketSentiment: React.FC = () => {
    const { t } = useTranslation('reports');
    const [data, setData] = useState<SentimentResponse | null>(null);
    const [loading, setLoading] = useState(false);
    const [error, setError] = useState<string | null>(null);
    /** Non-blocking refresh toast shown when Refresh completes with warnings. */
    const [refreshToast, setRefreshToast] = useState<string | null>(null);

    const fetchSentiment = async () => {
        try {
            setLoading(true);
            setError(null);
            const res = await SentimentAPI.getCached();
            setData(res);
        } catch (e: any) {
            setError(e.message || t('marketSentiment.fetchFailed'));
        } finally {
            setLoading(false);
        }
    };

    const refreshSentiment = async () => {
        try {
            setLoading(true);
            setRefreshToast(null);
            // Do NOT clear error or data before the call so the page stays populated
            // if the refresh takes a long time or partially fails.
            const res = await SentimentAPI.refresh();
            setData(res);
            setError(null);
            // Count stale cards after refresh to surface a non-blocking warning.
            const staleCount = res.indicators.filter(i => i.is_stale && i.display_value !== 'Unavailable').length;
            const unavailableCount = res.indicators.filter(i => i.display_value === 'Unavailable').length;
            if (staleCount > 0 || unavailableCount > 0) {
                const parts: string[] = [];
                if (staleCount > 0) parts.push(t('marketSentiment.staleIndicators', { count: staleCount }));
                if (unavailableCount > 0) parts.push(t('marketSentiment.unavailableIndicators', { count: unavailableCount }));
                setRefreshToast(parts.join('; '));
            }
        } catch (e: any) {
            // Refresh failed at network/HTTP level — keep existing data, show non-blocking toast.
            setRefreshToast(t('marketSentiment.refreshFailedToast', { detail: e.message || t('marketSentiment.unknownError') }));
        } finally {
            setLoading(false);
        }
    };

    useEffect(() => {
        fetchSentiment();
    }, []);

    // Auto-dismiss toast after 8 seconds.
    useEffect(() => {
        if (!refreshToast) return;
        const t = setTimeout(() => setRefreshToast(null), 8000);
        return () => clearTimeout(t);
    }, [refreshToast]);

    const formatLastUpdated = (iso: string | null) => {
        if (!iso) return t('marketSentiment.never');
        return new Date(iso).toLocaleString();
    };

    const grouped = data?.indicators.reduce((acc, curr) => {
        if (!acc[curr.section]) acc[curr.section] = [];
        acc[curr.section].push(curr);
        return acc;
    }, {} as Record<string, SentimentIndicator[]>) || {};

    const staleTotal = data?.indicators.filter(i => i.is_stale && i.display_value !== 'Unavailable').length ?? 0;

    return (
        <div className="bg-gray-50 dark:bg-background-dark min-h-screen">
            <header className="h-16 flex-shrink-0 flex items-center justify-between px-8 border-b border-slate-200 dark:border-border-dark bg-white/80 dark:bg-background-dark/80 backdrop-blur-md sticky top-0 z-10">
                <div className="flex items-center gap-6">
                    <h2 className="text-lg font-bold">{t('marketSentiment.title')}</h2>
                    {data?.last_updated && (
                        <div className="flex items-center gap-2 text-xs text-slate-500">
                            <span className="size-1.5 rounded-full bg-emerald-500 animate-pulse"></span>
                            <span>{t('marketSentiment.lastUpdated', { date: formatLastUpdated(data.last_updated) })}</span>
                        </div>
                    )}
                    {staleTotal > 0 && (
                        <span className="px-2 py-0.5 rounded-full text-[10px] font-semibold bg-amber-100 text-amber-700 dark:bg-amber-900/30 dark:text-amber-400 border border-amber-200 dark:border-amber-700/40">
                            {t('marketSentiment.staleTotal', { count: staleTotal })}
                        </span>
                    )}
                </div>
                <div className="flex items-center gap-4">
                    <button
                        onClick={refreshSentiment}
                        disabled={loading}
                        className={`flex items-center gap-2 px-3 py-1.5 rounded-lg border transition-all ${loading ? 'bg-blue-100 text-blue-700 border-blue-200' : 'bg-slate-100 hover:bg-slate-200 dark:bg-card-dark border-slate-200 dark:border-border-dark text-slate-500'}`}
                    >
                        <span className={`material-symbols-outlined !text-[16px] ${loading ? 'animate-spin' : ''}`}>sync</span>
                        <span className="text-[11px] font-mono font-medium">
                            {loading ? t('marketSentiment.refreshing') : t('marketSentiment.refresh')}
                        </span>
                    </button>
                </div>
            </header>

            {/* Non-blocking refresh warning toast */}
            {refreshToast && (
                <div className="mx-8 mt-4 p-3 rounded-lg bg-amber-50 dark:bg-amber-900/20 border border-amber-200 dark:border-amber-700/40 text-amber-800 dark:text-amber-300 text-xs flex items-center justify-between gap-3">
                    <div className="flex items-center gap-2">
                        <span className="material-symbols-outlined !text-[14px]">warning</span>
                        <span>{refreshToast}</span>
                    </div>
                    <button
                        onClick={() => setRefreshToast(null)}
                        className="text-amber-600 hover:text-amber-800 dark:text-amber-400 shrink-0"
                    >
                        <span className="material-symbols-outlined !text-[14px]">close</span>
                    </button>
                </div>
            )}

            <div className="p-8 pb-4 space-y-8">
                {/* Only show blocking error if there's NO data at all */}
                {error && !data && (
                    <div className="mb-6 p-4 rounded-lg bg-red-50 text-red-700 border border-red-200 text-sm">
                        {error}
                    </div>
                )}

                {loading && !data && (
                    <div className="flex justify-center items-center h-64">
                        <span className="material-symbols-outlined animate-spin text-4xl text-primary">sync</span>
                    </div>
                )}

                {!loading && !data?.indicators?.length && !error && (
                    <div className="flex flex-col justify-center items-center h-64 text-slate-500">
                        <span className="material-symbols-outlined text-4xl mb-2">monitoring</span>
                        <p className="text-sm">{t('marketSentiment.noData')}</p>
                    </div>
                )}

                {data && data.indicators.length > 0 && (
                    <div className="space-y-8">
                        {/* Equity Macro */}
                        {(grouped['equity_macro'] || []).length > 0 && (
                            <section>
                                <h3 className="text-[10px] font-bold text-slate-500 dark:text-slate-400 uppercase tracking-widest mb-4">{t('marketSentiment.equityMacro')}</h3>
                                <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-4">
                                    {grouped['equity_macro'].map(ind => <SentimentCard key={ind.indicator_key} indicator={ind} />)}
                                </div>
                            </section>
                        )}

                        {/* Gold */}
                        {(grouped['gold'] || []).length > 0 && (
                            <section>
                                <h3 className="text-[10px] font-bold text-slate-500 dark:text-slate-400 uppercase tracking-widest mb-4">{t('marketSentiment.goldMarket')}</h3>
                                <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-4">
                                    {grouped['gold'].map(ind => <SentimentCard key={ind.indicator_key} indicator={ind} />)}
                                </div>
                            </section>
                        )}

                        {/* Crypto */}
                        {(grouped['crypto'] || []).length > 0 && (
                            <section>
                                <h3 className="text-[10px] font-bold text-slate-500 dark:text-slate-400 uppercase tracking-widest mb-4">{t('marketSentiment.cryptoMarket')}</h3>
                                <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-4">
                                    {grouped['crypto'].map(ind => <SentimentCard key={ind.indicator_key} indicator={ind} />)}
                                </div>
                            </section>
                        )}
                    </div>
                )}
            </div>
        </div>
    );
};
