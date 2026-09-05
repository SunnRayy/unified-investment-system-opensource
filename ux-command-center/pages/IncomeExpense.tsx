import React, { useEffect, useState, useMemo } from 'react';
import { useTranslation } from 'react-i18next';
import { ComposedChart, Bar, Line, XAxis, YAxis, Tooltip, ResponsiveContainer, CartesianGrid, Legend } from 'recharts';
import { api, IncomeExpenseAPI, IncomeExpenseSummary, IncomeExpenseHistory } from '../src/services/api';
import type { ContributionsSummary } from '../src/services/api/types';
import { formatCNY } from '../src/utils/format';
import { formatPercent } from '../src/utils/formatMoney';

// ── Wide-format column classification ──────────────────────────────────────
// Each row is ONE month snapshot with Chinese column names as keys.
// record_key = "IE_YYYYMMDD" — NOT "income_*" / "expense_*".
// Prefixes confirmed from HANDOVER.md (2026-02-20):
//   Income:      收入_主动收入_*  /  收入_被动收入_*
//   Expense:     必要开支_*  /  非必要开支_*
//   Investment:  投资理财_*
//   SKIP:        合计 / 总收入 / 总支出 / 必要支出 / 非必要支出 / 理财 (pre-computed totals)
//                参考_* (reference data: gold price, FX rate)
//                metadata fields

const METADATA_KEYS = new Set([
    'record_key', 'snapshot_date', 'transaction_date', '日期', 'asset_id', 'source_system',
]);
// Exact-match aggregate/summary columns to skip (pre-computed totals in the spreadsheet).
// IMPORTANT: use exact match — '理财' as substring would also hit '投资理财_*' columns.
const SKIP_EXACT = new Set(['总收入', '总支出', '必要支出', '非必要支出', '理财']);

const shouldSkip = (key: string): boolean => {
    if (METADATA_KEYS.has(key)) return true;
    if (key.startsWith('参考_')) return true;
    if (SKIP_EXACT.has(key)) return true;        // exact aggregate column names
    if (key.endsWith('合计')) return true;        // any *合计 summary column
    if (key.endsWith('_USD')) return true;        // raw USD amounts — use CNY-converted column instead
    return false;
};

const isIncomeCol = (key: string): boolean => {
    if (shouldSkip(key)) return false;
    return key.startsWith('收入_');
};

const isExpenseCol = (key: string): boolean => {
    if (shouldSkip(key)) return false;
    return key.startsWith('必要开支_') || key.startsWith('非必要开支_');
};

const isInvestmentCol = (key: string): boolean => {
    if (shouldSkip(key)) return false;
    return key.startsWith('投资理财_');
};

const sumRow = (row: Record<string, any>, predicate: (key: string) => boolean): number =>
    Object.entries(row)
        .filter(([k, v]) => predicate(k) && typeof v === 'number' && v > 0)
        .reduce((acc, [, v]) => acc + (v as number), 0);

const stripPrefix = (key: string): string => {
    // Remove first prefix segment to show meaningful subcategory
    const idx = key.indexOf('_');
    return idx !== -1 ? key.substring(idx + 1) : key;
};

const fmtStr = (n: number) => `¥${n.toLocaleString(undefined, { maximumFractionDigits: 0 })}`;

/**
 * null (no income basis / payload unavailable) renders an em-dash — never 0%, never NaN%.
 * Program BIL / WS-2: delegates to formatMoney.ts's `formatPercent` (the input here is a
 * 0-1 FRACTION, not an already-scaled percent, so it's multiplied by 100 first — this was
 * one of WS-1's ~6 flagged local fraction-based fmtPct definitions). `signStyle: 'inline'`
 * + `signed: false` reproduces the old ASCII-hyphen, no-plus-sign output exactly.
 */
const fmtPct = (n: number | null | undefined): string =>
    formatPercent(n == null ? null : n * 100, { signed: false, signStyle: 'inline', digits: 1 });

// ── Component ────────────────────────────────────────────────────────────────

/** Hoisted out of any JSX child-expression container — '12'/'36' are
 *  digit-leading and would otherwise trip the i18n ratchet's child-expr-
 *  literal rule (see the identical pattern in dashboard/DashboardCards.tsx). */
const TIME_FILTERS = ['12', '36', 'ALL'] as const;

export const IncomeExpense: React.FC = () => {
    const { t } = useTranslation('incomeExpense');
    const [summary, setSummary] = useState<IncomeExpenseSummary | null>(null);
    const [history, setHistory] = useState<IncomeExpenseHistory | null>(null);
    const [contributions, setContributions] = useState<ContributionsSummary | null>(null);
    const [timeFilter, setTimeFilter] = useState<'ALL' | '36' | '12'>('ALL');
    const [error, setError] = useState<string | null>(null);

    useEffect(() => {
        const load = async () => {
            setError(null);
            try {
                const [summaryData, historyData] = await Promise.all([
                    IncomeExpenseAPI.getSummary(),
                    IncomeExpenseAPI.getHistory(120),  // fetch up to 120 months for "All Time"
                ]);
                setSummary(summaryData);
                setHistory(historyData);
            } catch (err) {
                console.error(err);
                setError(t('errors.load'));
            }
        };
        load();
    }, []);

    // Savings-rate basis comes from the backend's ie_column role classification
    // (GET /north-star/contributions → investment.*), NOT from re-deriving roles
    // out of Chinese column-name prefixes here: the prefix rule counts 报销 and
    // every 被动收入 redemption as income and drops 工作开支 entirely, which is
    // the exact convention-contract defect the backend classification replaced
    // (plan 2026-08-01-ie-column-mapping-and-ibkr-amounts.md §WS-E/§WS-G).
    // Isolated from the KPI/chart fetch above so a failure here degrades this one
    // tile to "—" instead of blanking the page.
    useEffect(() => {
        let cancelled = false;
        (async () => {
            try {
                const res: ContributionsSummary = await api.getContributions('12');
                if (!cancelled) setContributions(res);
            } catch (err) {
                console.error('contributions summary unavailable', err);
            }
        })();
        return () => { cancelled = true; };
    }, []);

    // ── Parse each history month into chart-ready totals ──────────────────────
    const allChartData = useMemo(() => {
        if (!history?.months) return [];
        return [...history.months]
            .reverse()  // chronological order for chart
            .map(({ month, items }) => {
                const merged: Record<string, any> = Object.assign({}, ...items);
                const income = sumRow(merged, isIncomeCol);
                const expense = sumRow(merged, isExpenseCol);
                const investment = sumRow(merged, isInvestmentCol);
                return {
                    month: month.substring(0, 7),   // "2026-01"
                    Income: income,
                    Expense: expense,
                    Investment: investment,
                    Net: income - expense,
                };
            });
    }, [history]);

    const filteredChartData = useMemo(() => {
        if (timeFilter === '12') return allChartData.slice(-12);
        if (timeFilter === '36') return allChartData.slice(-36);
        return allChartData;
    }, [allChartData, timeFilter]);

    const computedAverages = useMemo(() => {
        const n = filteredChartData.length;
        if (!n) return { avgInc: 0, avgExp: 0, avgInv: 0, avgNet: 0, months: 0 };
        return {
            avgInc: filteredChartData.reduce((a, d) => a + d.Income, 0) / n,
            avgExp: filteredChartData.reduce((a, d) => a + d.Expense, 0) / n,
            avgInv: filteredChartData.reduce((a, d) => a + d.Investment, 0) / n,
            avgNet: filteredChartData.reduce((a, d) => a + d.Net, 0) / n,
            months: n,
        };
    }, [filteredChartData]);

    // ── KPI values from latest-month summary row ──────────────────────────────
    const { latestIncome, latestExpense, latestInvestment, netSavings } = useMemo(() => {
        if (!summary?.rows?.length) {
            return { latestIncome: 0, latestExpense: 0, latestInvestment: 0, netSavings: 0 };
        }
        const row = summary.rows[0];
        const inc = sumRow(row, isIncomeCol);
        const exp = sumRow(row, isExpenseCol);
        const inv = sumRow(row, isInvestmentCol);
        return {
            latestIncome: inc,
            latestExpense: exp,
            latestInvestment: inv,
            netSavings: inc - exp,
        };
    }, [summary]);

    // ── Savings rate — backend-classified basis, never re-derived here ────────
    // Latest month uses that month's own income_basis / expense_basis from the
    // series (same role classification as the trailing figures, so the monthly
    // and 12m numbers are on one 口径). Both TTM rates are shown because they
    // measure different things and are ~19pp apart: savings_rate_ttm is
    // everything not spent, investment_rate_ttm is the share that reached an
    // investment account.
    const { rateMonth, monthSavingsRate, savingsRateTtm, investmentRateTtm } = useMemo(() => {
        const inv = contributions?.investment;
        if (!inv?.series?.length) {
            return { rateMonth: null as string | null, monthSavingsRate: null as number | null, savingsRateTtm: null as number | null, investmentRateTtm: null as number | null };
        }
        const latest = summary?.latest_month ? String(summary.latest_month).substring(0, 7) : null;
        const entry = (latest ? inv.series.find(m => m.month === latest) : undefined) ?? inv.series[inv.series.length - 1];
        const basis = entry?.income_basis ?? 0;
        return {
            rateMonth: entry?.month ?? null,
            monthSavingsRate: entry && basis > 0 ? (basis - entry.expense_basis) / basis : null,
            savingsRateTtm: inv.savings_rate_ttm ?? null,
            investmentRateTtm: inv.investment_rate_ttm ?? null,
        };
    }, [contributions, summary]);

    // ── Detail table items from latest summary row ────────────────────────────
    const { incomeItems, expenseItems, investmentItems } = useMemo(() => {
        if (!summary?.rows?.length) return { incomeItems: [], expenseItems: [], investmentItems: [] };
        const row = summary.rows[0];

        const inc: Array<{ name: string; value: number }> = [];
        const exp: Array<{ name: string; value: number }> = [];
        const inv: Array<{ name: string; value: number }> = [];

        Object.entries(row).forEach(([key, value]) => {
            if (typeof value !== 'number' || value <= 0) return;
            if (isIncomeCol(key))     inc.push({ name: key, value });
            else if (isExpenseCol(key))    exp.push({ name: key, value });
            else if (isInvestmentCol(key)) inv.push({ name: key, value });
            // All other columns (aggregates, metadata, reference) are silently ignored
        });

        return {
            incomeItems:     inc.sort((a, b) => b.value - a.value),
            expenseItems:    exp.sort((a, b) => b.value - a.value),
            investmentItems: inv.sort((a, b) => b.value - a.value),
        };
    }, [summary]);

    // ── Render ────────────────────────────────────────────────────────────────
    return (
        <div className="p-6 space-y-6 bg-gray-50 dark:bg-background-dark min-h-screen" data-testid="income-expense-page">
            {error && (
                <div className="bg-red-50 border border-red-200 rounded-lg p-4 mb-4">
                    <p className="text-red-700 text-sm">{error}</p>
                </div>
            )}
            {/* Header */}
            <div className="flex justify-between items-center">
                <h1 className="text-2xl font-bold text-slate-900 dark:text-white">{t('pageTitle')}</h1>
                <div className="text-sm font-medium text-slate-500">
                    {t('latestLabel', { date: summary?.latest_month || '—' })}
                </div>
            </div>

            {/* KPI Cards — 5 columns */}
            <div className="grid grid-cols-2 lg:grid-cols-5 gap-4">
                <KpiCard label={t('kpi.latestMonthIncome')} value={formatCNY(latestIncome)} color="text-emerald-600 dark:text-emerald-400" />
                <KpiCard label={t('kpi.latestMonthExpense')} value={formatCNY(latestExpense)} color="text-rose-600 dark:text-rose-400" />
                <KpiCard label={t('kpi.latestMonthInvestment')} value={formatCNY(latestInvestment)} color="text-blue-600 dark:text-blue-400" />
                <KpiCard label={t('kpi.latestMonthNet')} value={formatCNY(netSavings)} color={netSavings >= 0 ? 'text-emerald-600 dark:text-emerald-400' : 'text-rose-600 dark:text-rose-400'} />
                <KpiCard
                    label={t('kpi.savingsRateLabel', { month: rateMonth || t('kpi.latestMonthFallback') })}
                    value={fmtPct(monthSavingsRate)}
                    color="text-slate-800 dark:text-slate-100"
                    subtext={t('kpi.savingsRateSubtext', { ttmSaved: fmtPct(savingsRateTtm), ttmInvested: fmtPct(investmentRateTtm) })}
                />
            </div>

            {/* Monthly Trend Chart */}
            <div className="p-6 rounded-xl border border-slate-200 dark:border-border-dark bg-white dark:bg-card-dark shadow-sm">
                <div className="flex justify-between items-center mb-4">
                    <h3 className="text-sm font-bold text-slate-800 dark:text-white">{t('trends.title')}</h3>
                    <div className="flex bg-slate-100 dark:bg-slate-800 rounded-lg p-1">
                        {TIME_FILTERS.map(f => (
                            <button
                                key={f}
                                className={`px-3 py-1 text-xs font-medium rounded-md transition-colors ${timeFilter === f ? 'bg-white dark:bg-slate-700 shadow-sm text-slate-900 dark:text-white' : 'text-slate-500 hover:text-slate-700 dark:hover:text-slate-300'}`}
                                onClick={() => setTimeFilter(f)}
                            >
                                {f === '12' ? t('trends.last12m') : f === '36' ? t('trends.last36m') : t('trends.allTime')}
                            </button>
                        ))}
                    </div>
                </div>
                <div className="h-[350px]">
                    {filteredChartData.length > 0 ? (
                        <ResponsiveContainer width="100%" height="100%">
                            <ComposedChart data={filteredChartData} margin={{ top: 10, right: 10, left: 10, bottom: 0 }}>
                                <CartesianGrid strokeDasharray="3 3" vertical={false} stroke="#334155" opacity={0.2} />
                                <XAxis dataKey="month" tick={{ fontSize: 10 }} tickMargin={10} minTickGap={20} />
                                <YAxis tick={(props: any) => {
                                    const { x, y, payload } = props;
                                    return (
                                        <g transform={`translate(${x},${y})`}>
                                            <text className="money-value" textAnchor="end" fill="#64748b" fontSize={10} dy="0.355em">
                                                {t('trends.axisThousands', { value: (payload.value / 1000).toFixed(0) })}
                                            </text>
                                        </g>
                                    );
                                }} />
                                <Tooltip formatter={(value: number) => fmtStr(value)} labelStyle={{ color: '#1e293b' }} />
                                <Legend iconType="circle" wrapperStyle={{ fontSize: '12px' }} />
                                <Bar dataKey="Income" fill="#059669" radius={[2, 2, 0, 0]} maxBarSize={24} />
                                <Bar dataKey="Expense" fill="#e11d48" radius={[2, 2, 0, 0]} maxBarSize={24} />
                                <Bar dataKey="Investment" fill="#3b82f6" radius={[2, 2, 0, 0]} maxBarSize={24} />
                                <Line type="monotone" dataKey="Net" stroke="#f59e0b" strokeWidth={2} dot={{ r: 2 }} activeDot={{ r: 4 }} />
                            </ComposedChart>
                        </ResponsiveContainer>
                    ) : (
                        <div className="h-full flex items-center justify-center text-slate-400 text-sm">{t('trends.noData')}</div>
                    )}
                </div>
            </div>

            {/* Averages + Detail Table */}
            <div className="grid grid-cols-1 lg:grid-cols-4 gap-6">
                {/* Averages panel */}
                <div className="p-6 rounded-xl border border-slate-200 dark:border-border-dark bg-white dark:bg-card-dark shadow-sm">
                    <h3 className="text-sm font-bold text-slate-800 dark:text-white mb-4">{t('averages.title')}</h3>
                    <div className="space-y-4">
                        <AverageRow label={t('averages.avgIncome')} value={formatCNY(computedAverages.avgInc)} color="text-emerald-600 dark:text-emerald-400" />
                        <AverageRow label={t('averages.avgExpense')} value={formatCNY(computedAverages.avgExp)} color="text-rose-600 dark:text-rose-400" />
                        <AverageRow label={t('averages.avgInvestment')} value={formatCNY(computedAverages.avgInv)} color="text-blue-600 dark:text-blue-400" />
                        <AverageRow label={t('averages.avgNet')} value={formatCNY(computedAverages.avgNet)} color="text-amber-600 dark:text-amber-400" />
                        <AverageRow label={t('averages.months')} value={String(computedAverages.months)} color="text-slate-600 dark:text-slate-400" last />
                    </div>
                </div>

                {/* Detail table */}
                <div className="p-6 rounded-xl border border-slate-200 dark:border-border-dark bg-white dark:bg-card-dark shadow-sm lg:col-span-3">
                    <h3 className="text-sm font-bold text-slate-800 dark:text-white mb-4">{t('detail.title')}</h3>
                    <div className="grid grid-cols-1 md:grid-cols-3 gap-6">
                        <DetailColumn
                            title={t('detail.income')}
                            items={incomeItems}
                            emptyText={t('detail.noIncomeRecords')}
                            valueColor="text-emerald-600 dark:text-emerald-400"
                        />
                        <DetailColumn
                            title={t('detail.expense')}
                            items={expenseItems}
                            emptyText={t('detail.noExpenseRecords')}
                            valueColor="text-rose-600 dark:text-rose-400"
                        />
                        <DetailColumn
                            title={t('detail.investment')}
                            items={investmentItems}
                            emptyText={t('detail.noInvestmentRecords')}
                            valueColor="text-blue-600 dark:text-blue-400"
                        />
                    </div>
                </div>
            </div>
        </div>
    );
};

// ── Sub-components ─────────────────────────────────────────────────────────

const KpiCard: React.FC<{ label: string; value: React.ReactNode; color: string; subtext?: string }> = ({ label, value, color, subtext }) => (
    <div className="p-5 rounded-xl border border-slate-200 dark:border-border-dark bg-white dark:bg-card-dark shadow-sm">
        <p className="text-[10px] font-bold text-slate-500 dark:text-slate-400 uppercase tracking-wider mb-2">{label}</p>
        <div className={`text-2xl font-bold font-mono ${color}`}>{value}</div>
        {subtext && <p className="text-[10px] text-slate-400 dark:text-slate-500 mt-1.5 leading-snug">{subtext}</p>}
    </div>
);

const AverageRow: React.FC<{ label: string; value: React.ReactNode; color: string; last?: boolean }> = ({ label, value, color, last }) => (
    <div className={`flex justify-between items-center ${last ? '' : 'pb-3 border-b border-slate-100 dark:border-slate-800'}`}>
        <span className="text-sm text-slate-500">{label}</span>
        <span className={`font-mono font-medium ${color}`}>{value}</span>
    </div>
);

const DetailColumn: React.FC<{
    title: string;
    items: Array<{ name: string; value: number }>;
    emptyText: string;
    valueColor: string;
}> = ({ title, items, emptyText, valueColor }) => (
    <div>
        <h4 className="text-xs font-bold text-slate-500 uppercase tracking-wider mb-3 pb-2 border-b border-slate-100 dark:border-slate-800">
            {title}
        </h4>
        <div className="space-y-2">
            {items.length > 0 ? items.map((item, i) => (
                <div key={i} className="flex justify-between items-center text-sm px-2 py-1.5 hover:bg-slate-50 dark:hover:bg-slate-800/50 rounded transition-colors">
                    <span className="text-slate-700 dark:text-slate-300 truncate max-w-[150px]" title={item.name}>
                        {stripPrefix(item.name)}
                    </span>
                    <span className={`font-mono ${valueColor}`}>{formatCNY(item.value)}</span>
                </div>
            )) : (
                <div className="text-xs text-slate-400 px-2 py-1">{emptyText}</div>
            )}
        </div>
    </div>
);

export default IncomeExpense;
