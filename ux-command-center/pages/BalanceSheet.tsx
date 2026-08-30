import React, { useEffect, useState, useMemo } from 'react';
import { useTranslation } from 'react-i18next';
import { AreaChart, Area, XAxis, YAxis, Tooltip, ResponsiveContainer, CartesianGrid } from 'recharts';
import { BalanceSheetAPI, BalanceSheetSummary, BalanceSheetHistory } from '../src/services/api';
import { useFormatCurrency } from '../src/utils/format';

// ── Wide-format column classification ──────────────────────────────────────
// Each balance sheet row is ONE snapshot with Chinese column names as keys.
// record_key = "BS_YYYYMMDD" — NOT "assets_*" / "liabilities_*".

const METADATA_KEYS = new Set(['record_key', 'snapshot_date', '日期']);
const SKIP_SUFFIXES = ['_USD', '(克)'];  // raw USD amounts and gram weights

const isAssetCol = (key: string): boolean => {
    if (METADATA_KEYS.has(key) || SKIP_SUFFIXES.some(s => key.endsWith(s))) return false;
    return key.startsWith('RMB') || key.startsWith('美元') || key.startsWith('创业股权投资') ||
        key.startsWith('投资资产_') || key.startsWith('固定资产_');
};

const isLiabilityCol = (key: string): boolean => {
    if (METADATA_KEYS.has(key) || SKIP_SUFFIXES.some(s => key.endsWith(s))) return false;
    return key.startsWith('短期负债_') || key.startsWith('长期负债_');
};

const sumRow = (row: Record<string, any>, predicate: (key: string) => boolean): number =>
    Object.entries(row)
        .filter(([k, v]) => predicate(k) && typeof v === 'number' && v > 0)
        .reduce((acc, [, v]) => acc + (v as number), 0);

// ── Component ────────────────────────────────────────────────────────────────

export const BalanceSheet: React.FC = () => {
    const { t } = useTranslation('reports');
    const formatCNY = useFormatCurrency();
    const [summary, setSummary] = useState<BalanceSheetSummary | null>(null);
    const [history, setHistory] = useState<BalanceSheetHistory | null>(null);
    const [error, setError] = useState<string | null>(null);

    useEffect(() => {
        loadData();
    }, []);

    const loadData = async () => {
        setError(null);
        try {
            const [summaryData, historyData] = await Promise.all([
                BalanceSheetAPI.getSummary(),
                BalanceSheetAPI.getHistory()
            ]);
            setSummary(summaryData);
            setHistory(historyData);
        } catch (err) {
            console.error(err);
            setError(t('balanceSheet.errors.load'));
        }
    };

    // KPI totals — sum asset/liability columns from the single latest-snapshot row
    const { totalAssets, totalLiabilities, netWorth } = useMemo(() => {
        if (!summary?.rows?.length) return { totalAssets: 0, totalLiabilities: 0, netWorth: 0 };
        const row = summary.rows[0];
        const assets = sumRow(row, isAssetCol);
        const liabilities = sumRow(row, isLiabilityCol);
        return { totalAssets: assets, totalLiabilities: liabilities, netWorth: assets - liabilities };
    }, [summary]);

    // Detail rows — each column of the wide-format row becomes a line item
    const assetRows = useMemo(() => {
        if (!summary?.rows?.length) return [];
        const row = summary.rows[0];
        const total = totalAssets || 1;
        return Object.entries(row)
            .filter(([k, v]) => isAssetCol(k) && typeof v === 'number' && v > 0)
            .map(([k, v]) => ({ name: k, value_cny: v as number, percentage: ((v as number) / total) * 100 }))
            .sort((a, b) => b.value_cny - a.value_cny);
    }, [summary, totalAssets]);

    const liabilityRows = useMemo(() => {
        if (!summary?.rows?.length) return [];
        const row = summary.rows[0];
        const total = totalLiabilities || 1;
        return Object.entries(row)
            .filter(([k, v]) => isLiabilityCol(k) && typeof v === 'number' && v > 0)
            .map(([k, v]) => ({ name: k, value_cny: v as number, percentage: ((v as number) / total) * 100 }))
            .sort((a, b) => b.value_cny - a.value_cny);
    }, [summary, totalLiabilities]);

    // Chart — compute assets/liabilities totals from each snapshot's wide-format row
    const chartData = useMemo(() => {
        if (!history?.snapshots) return [];
        return history.snapshots.map((s) => {
            const row = s.items[0];
            if (!row) return { date: s.snapshot_date, Assets: 0, Liabilities: 0, NetWorth: 0 };
            const assets = sumRow(row, isAssetCol);
            const liabilities = sumRow(row, isLiabilityCol);
            return { date: s.snapshot_date, Assets: assets, Liabilities: liabilities, NetWorth: assets - liabilities };
        }).sort((a, b) => new Date(a.date).getTime() - new Date(b.date).getTime());
    }, [history]);

    return (
        <div className="p-6 space-y-6 bg-gray-50 dark:bg-background-dark min-h-screen" data-testid="balance-sheet-page">
            {error && (
                <div className="bg-red-50 border border-red-200 rounded-lg p-4 mb-4">
                    <p className="text-red-700 text-sm">{error}</p>
                </div>
            )}
            <div className="flex justify-between items-center mb-6">
                <h1 className="text-2xl font-bold text-slate-900 dark:text-white">{t('balanceSheet.title')}</h1>
                <div className="text-sm font-medium text-slate-500">
                    {t('balanceSheet.latest', { date: summary?.latest_snapshot || '—' })}
                </div>
            </div>

            <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-4">
                <div className="p-5 rounded-xl border border-slate-200 dark:border-border-dark bg-white dark:bg-card-dark shadow-sm">
                    <p className="text-[10px] font-bold text-slate-500 dark:text-slate-400 uppercase tracking-wider mb-2">{t('balanceSheet.totalAssets')}</p>
                    <div className="text-2xl font-bold font-mono text-emerald-600 dark:text-emerald-400">
                        {formatCNY(totalAssets)}
                    </div>
                </div>

                <div className="p-5 rounded-xl border border-slate-200 dark:border-border-dark bg-white dark:bg-card-dark shadow-sm">
                    <p className="text-[10px] font-bold text-slate-500 dark:text-slate-400 uppercase tracking-wider mb-2">{t('balanceSheet.totalLiabilities')}</p>
                    <div className="text-2xl font-bold font-mono text-rose-600 dark:text-rose-400">
                        {formatCNY(totalLiabilities)}
                    </div>
                </div>

                <div className="p-5 rounded-xl border border-slate-200 dark:border-border-dark bg-white dark:bg-card-dark shadow-sm">
                    <p className="text-[10px] font-bold text-slate-500 dark:text-slate-400 uppercase tracking-wider mb-2">{t('balanceSheet.netWorth')}</p>
                    <div className="text-2xl font-bold font-mono text-slate-800 dark:text-slate-100">
                        {formatCNY(netWorth)}
                    </div>
                </div>

                <div className="p-5 rounded-xl border border-slate-200 dark:border-border-dark bg-white dark:bg-card-dark shadow-sm">
                    <p className="text-[10px] font-bold text-slate-500 dark:text-slate-400 uppercase tracking-wider mb-2">{t('balanceSheet.snapshots')}</p>
                    <div className="text-2xl font-bold font-mono text-slate-600 dark:text-slate-400">
                        {summary?.snapshot_count || 0}
                    </div>
                </div>
            </div>

            <div className="p-6 rounded-xl border border-slate-200 dark:border-border-dark bg-white dark:bg-card-dark shadow-sm">
                <h3 className="text-sm font-bold mb-4">{t('balanceSheet.netWorthTrend')}</h3>
                <div className="h-[350px]">
                    {chartData.length > 0 ? (
                        <ResponsiveContainer width="100%" height="100%">
                            <AreaChart data={chartData} margin={{ top: 10, right: 30, left: 20, bottom: 0 }}>
                                <defs>
                                    <linearGradient id="colorAssets" x1="0" y1="0" x2="0" y2="1">
                                        <stop offset="5%" stopColor="#059669" stopOpacity={0.1} />
                                        <stop offset="95%" stopColor="#059669" stopOpacity={0} />
                                    </linearGradient>
                                    <linearGradient id="colorLiab" x1="0" y1="0" x2="0" y2="1">
                                        <stop offset="5%" stopColor="#e11d48" stopOpacity={0.1} />
                                        <stop offset="95%" stopColor="#e11d48" stopOpacity={0} />
                                    </linearGradient>
                                </defs>
                                <CartesianGrid strokeDasharray="3 3" vertical={false} stroke="#334155" opacity={0.2} />
                                <XAxis dataKey="date" tick={{ fontSize: 10 }} tickMargin={10} minTickGap={30} />
                                <YAxis tick={(props: any) => {
                                    const { x, y, payload } = props;
                                    return (
                                        <g transform={`translate(${x},${y})`}>
                                            <text className="money-value" textAnchor="end" fill="#64748b" fontSize={10} dy="0.355em">
                                                {t('balanceSheet.axisThousands', { value: (payload.value / 1000).toFixed(0) })}
                                            </text>
                                        </g>
                                    );
                                }} />
                                <Tooltip
                                    formatter={(value: number) => `¥${value.toLocaleString(undefined, { maximumFractionDigits: 0 })}`}
                                    labelStyle={{ color: '#1e293b' }}
                                />
                                <Area type="monotone" dataKey="Assets" stroke="#059669" fillOpacity={1} fill="url(#colorAssets)" />
                                <Area type="monotone" dataKey="Liabilities" stroke="#e11d48" fillOpacity={1} fill="url(#colorLiab)" />
                                <Area type="monotone" dataKey="NetWorth" stroke="#0ea5e9" strokeWidth={2} fill="none" />
                            </AreaChart>
                        </ResponsiveContainer>
                    ) : (
                        <div className="h-full flex items-center justify-center text-slate-400 text-sm">{t('balanceSheet.noTrendData')}</div>
                    )}
                </div>
            </div>

            <div className="p-6 rounded-xl border border-slate-200 dark:border-border-dark bg-white dark:bg-card-dark shadow-sm">
                <h3 className="text-sm font-bold mb-4">{t('balanceSheet.detailTable')}</h3>

                <div className="space-y-6">
                    <div>
                        <h4 className="flex items-center gap-2 text-sm font-bold text-emerald-600 dark:text-emerald-400 mb-3 pb-2 border-b border-slate-100 dark:border-slate-800">
                            <span className="material-symbols-outlined !text-[16px]">expand_more</span>
                            {t('balanceSheet.assets')}
                        </h4>
                        <div className="space-y-2">
                            {assetRows.length > 0 ? assetRows.map((row, i) => (
                                <div key={i} className="flex justify-between items-center text-sm px-7 py-1 hover:bg-slate-50 dark:hover:bg-slate-800/50 rounded transition-colors">
                                    <span className="text-slate-700 dark:text-slate-300">{row.name}</span>
                                    <div className="flex items-center gap-8 justify-end min-w-[200px]">
                                        <span className="font-mono text-emerald-600 dark:text-emerald-400">
                                            {formatCNY(row.value_cny)}
                                        </span>
                                        <span className="text-xs text-slate-400 w-12 text-right">{row.percentage.toFixed(1)}%</span>
                                    </div>
                                </div>
                            )) : (
                                <div className="text-xs text-slate-400 px-7">{t('balanceSheet.noAssetRecords')}</div>
                            )}
                        </div>
                    </div>

                    <div>
                        <h4 className="flex items-center gap-2 text-sm font-bold text-rose-600 dark:text-rose-400 mb-3 pb-2 border-b border-slate-100 dark:border-slate-800">
                            <span className="material-symbols-outlined !text-[16px]">expand_more</span>
                            {t('balanceSheet.liabilities')}
                        </h4>
                        <div className="space-y-2">
                            {liabilityRows.length > 0 ? liabilityRows.map((row, i) => (
                                <div key={i} className="flex justify-between items-center text-sm px-7 py-1 hover:bg-slate-50 dark:hover:bg-slate-800/50 rounded transition-colors">
                                    <span className="text-slate-700 dark:text-slate-300">{row.name}</span>
                                    <div className="flex items-center gap-8 justify-end min-w-[200px]">
                                        <span className="font-mono text-rose-600 dark:text-rose-400">
                                            {formatCNY(row.value_cny)}
                                        </span>
                                        <span className="text-xs text-slate-400 w-12 text-right">{row.percentage.toFixed(1)}%</span>
                                    </div>
                                </div>
                            )) : (
                                <div className="text-xs text-slate-400 px-7">{t('balanceSheet.noLiabilityRecords')}</div>
                            )}
                        </div>
                    </div>
                </div>
            </div>
        </div>
    );
};
