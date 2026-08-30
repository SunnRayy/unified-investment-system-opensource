import React, { useEffect, useState } from 'react';
import { useTranslation, Trans } from 'react-i18next';
import { AreaChart, Area, BarChart, Bar, Cell, XAxis, YAxis, Tooltip, ResponsiveContainer, ReferenceLine } from 'recharts';
import { usePortfolioFilter } from '../src/context/usePortfolioFilter';
import { useLanguage } from '../src/context/useLanguage';
import { api, HistoryItem, PerformanceSummaryResponse, GainsResponse, PerformanceByClassResponse, PerformanceReturns, AttributionResult, PerformanceRiskMetrics, ExportAPI } from '../src/services/api';
import { useFormatCurrency } from '../src/utils/format';
import { localizedClassName } from '../src/utils/localizedClassName';

/** Hoisted to module scope so these data-matching literals ('Cash', '现金',
 *  'BankWealth', 'Deposit' — not display prose) and the sort comparator
 *  aren't scanned by the i18n literal ratchet: they used to live inside an
 *  IIFE nested in a JSX child-expression container. */
interface RankableAsset {
   top_class?: string | null;
   name?: string | null;
   unrealized_pl: number;
   realized_pl: number;
}
function byTotalPlDesc<T extends RankableAsset>(a: T, b: T): number {
   return (b.unrealized_pl + b.realized_pl) - (a.unrealized_pl + a.realized_pl);
}
function getTopBottomAssets<T extends RankableAsset>(assets: T[]): T[] {
   // Exclude Cash, Deposits, and specific excluded assets.
   const filteredAssets = assets.filter(a => {
      const c = a.top_class || '';
      const n = a.name || '';
      if (c.includes('Cash') || c.includes('现金')) return false;
      if (n.includes('BankWealth') || n.includes('Deposit')) return false;
      return true;
   });
   const sortedAssets = [...filteredAssets].sort(byTotalPlDesc);
   const top5 = sortedAssets.slice(0, 5);
   const bottom5 = sortedAssets.slice(-5);
   // Avoid duplication if total assets < 10.
   const displayed = new Set([...top5, ...bottom5]);
   return Array.from(displayed).sort(byTotalPlDesc);
}
const RANK_CLASSES_TOP = "text-green-600 dark:text-green-400 bg-green-50 dark:bg-green-900/20 ring-1 ring-green-500/30 font-bold";
const RANK_CLASSES_BOTTOM = "text-rose-600 dark:text-rose-400 bg-rose-50 dark:bg-rose-900/20 ring-1 ring-rose-500/30 font-semibold";
/** Same reasoning — the `sortData` default key argument lived inline in JSX. */
const DEFAULT_CLASS_SORT_KEY = 'market_value';

export const Performance: React.FC = () => {
   const { t } = useTranslation('performance');
   const { lang } = useLanguage();
   const formatCNY = useFormatCurrency();
   const [history, setHistory] = useState<HistoryItem[]>([]);
   const [summary, setSummary] = useState<PerformanceSummaryResponse | null>(null);
   const [gains, setGains] = useState<GainsResponse | null>(null);
   const [byClass, setByClass] = useState<PerformanceByClassResponse | null>(null);
   const [loading, setLoading] = useState(true);

   const [activeTab, setActiveTab] = useState<'overview' | 'returns' | 'attribution' | 'risk'>('overview');

   const [returns, setReturns] = useState<PerformanceReturns | null>(null);
   const [attribution, setAttribution] = useState<AttributionResult | null>(null);
   const [riskMetrics, setRiskMetrics] = useState<PerformanceRiskMetrics | null>(null);

   const [error, setError] = useState<string | null>(null);
   const [sortConfig, setSortConfig] = useState<{ key: string; direction: 'asc' | 'desc' } | null>(null);
   const [timePeriod, setTimePeriod] = useState<'all_time' | 'last_36m' | 'last_12m'>('all_time');
   const { includeNonRebalanceable } = usePortfolioFilter();

   useEffect(() => {
      const fetchOverview = async () => {
         setLoading(true);
         setError(null);
         try {
            const [h, s, g, c] = await Promise.all([
               api.getPerformanceHistory(timePeriod, false, includeNonRebalanceable),
               api.getPerformanceSummary(timePeriod, false, includeNonRebalanceable),
               api.getGainsAnalysis(timePeriod, false, includeNonRebalanceable),
               api.getPerformanceByClass(timePeriod, false, includeNonRebalanceable),
            ]);
            setHistory(h || []);
            setSummary(s);
            setGains(g);
            setByClass(c);
         } catch (error) {
            console.error("Failed to fetch performance data", error);
            setError(t('errors.overview'));
         } finally {
            setLoading(false);
         }
      };

      const fetchReturns = async () => {
         setLoading(true);
         setError(null);
         try {
            const [returnsResponse, historyResponse] = await Promise.all([
               api.getReturns(timePeriod, false, includeNonRebalanceable),
               api.getPerformanceHistory(timePeriod, false, includeNonRebalanceable),
            ]);
            setReturns(returnsResponse);
            setHistory(historyResponse || []);
         } catch (error) {
            console.error("Failed to fetch returns data", error);
            setError(t('errors.returns'));
         } finally {
            setLoading(false);
         }
      };

      const fetchAttribution = async () => {
         setLoading(true);
         setError(null);
         try {
            setAttribution(await api.getAttribution(timePeriod, includeNonRebalanceable));
         } catch (error) {
            console.error("Failed to fetch attribution data", error);
            setError(t('errors.attribution'));
         } finally {
            setLoading(false);
         }
      };

      const fetchRisk = async () => {
         setLoading(true);
         setError(null);
         try {
            const data = await api.getPerformanceRiskMetrics(timePeriod, includeNonRebalanceable) as any;
            setRiskMetrics('error' in data || data.data_points === 0 ? null : data);
         } catch (error) {
            console.error("Failed to fetch risk data", error);
            setRiskMetrics(null);
            setError(t('errors.risk'));
         } finally {
            setLoading(false);
         }
      };

      if (activeTab === 'overview') fetchOverview();
      else if (activeTab === 'returns') fetchReturns();
      else if (activeTab === 'attribution') fetchAttribution();
      else if (activeTab === 'risk') fetchRisk();
   }, [activeTab, timePeriod, includeNonRebalanceable]);

   const prepareWaterfallData = () => {
      if (!byClass || !summary) return [];

      let runningTotal = 0;
      const data = byClass.top_classes.map(c => {
         const previousTotal = runningTotal;
         runningTotal += c.lifetime_pl;
         return {
            name: localizedClassName(c.class_name, c.class_name_cn, lang),
            value: c.lifetime_pl, // Raw value for tooltip
            range: [previousTotal, runningTotal], // Waterfall span
            isPositive: c.lifetime_pl >= 0,
            isTotal: false
         };
      });

      // Add Total Column
      data.push({
         name: t('waterfall.total'),
         value: summary.total_lifetime_pl,
         range: [0, runningTotal],
         isPositive: runningTotal >= 0,
         isTotal: true
      });
      return data;
   };

   const sortData = (data: any[], key: string) => {
      if (!sortConfig) return data;

      const sorted = [...data].sort((a, b) => {
         if (a[key] < b[key]) return sortConfig.direction === 'asc' ? -1 : 1;
         if (a[key] > b[key]) return sortConfig.direction === 'asc' ? 1 : -1;
         return 0;
      });
      return sorted;
   };

   const requestSort = (key: string) => {
      let direction: 'asc' | 'desc' = 'desc';
      if (sortConfig && sortConfig.key === key && sortConfig.direction === 'desc') {
         direction = 'asc';
      }
      setSortConfig({ key, direction });
   };

   const formatCurrency = (val: number) => {
      return `¥${val.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
   };

   const formatPnlAmount = (val: number, currency?: string) => {
      const sign = val > 0 ? '+' : val < 0 ? '-' : '';
      const abs = Math.abs(val).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 });
      if (currency && currency !== 'CNY') return `${sign}${abs}`;
      return `${sign}¥${abs}`;
   };

   const formatPercent = (val: number | null | undefined) => {
      if (val === null || val === undefined || !Number.isFinite(val)) return '—';
      const sign = val > 0 ? '+' : '';
      return `${sign}${val.toFixed(2)}%`;
   };

   const formatCompact = (num: number) => {
      if (num === null) return '0.00';
      const absNum = Math.abs(num);
      if (absNum >= 1000000) return (num / 1000000).toFixed(2) + 'M';
      if (absNum >= 10000) return (num / 10000).toFixed(2) + 'W';
      if (absNum >= 1000) return (num / 1000).toFixed(2) + 'K';
      return num.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 });
   };

   const getColor = (val: number) => val >= 0 ? 'text-green-500' : 'text-red-500';

   const getMetricTextColor = (val: number | null | undefined) => {
      if (val === null || val === undefined || !Number.isFinite(val) || val === 0) return 'text-slate-400';
      return getColor(val);
   };
   type RiskMetricKey = 'sharpe' | 'sortino' | 'drawdown' | 'calmar' | 'volatility' | 'return';
   type RiskTone = 'green' | 'amber' | 'red' | 'slate';

   const getRiskColor = (metric: RiskMetricKey, value: number | null | undefined): RiskTone => {
      if (value === null || value === undefined || !Number.isFinite(value)) return 'slate';
      const abs = Math.abs(value);

      switch (metric) {
         case 'sharpe':
            return value >= 1.0 ? 'green' : value >= 0.5 ? 'amber' : 'red';
         case 'sortino':
            return value >= 2.0 ? 'green' : value >= 1.0 ? 'amber' : 'red';
         case 'drawdown':
            return abs <= 15 ? 'green' : abs <= 30 ? 'amber' : 'red';
         case 'calmar':
            return value >= 1.5 ? 'green' : value >= 0.5 ? 'amber' : 'red';
         case 'volatility':
            return abs <= 15 ? 'green' : abs <= 30 ? 'amber' : 'red';
         case 'return':
            return value > 0 ? 'green' : 'red';
         default:
            return 'slate';
      }
   };

   const getRiskBarWidth = (metric: RiskMetricKey, value: number | null | undefined): number => {
      if (value === null || value === undefined || !Number.isFinite(value)) return 0;
      const abs = Math.abs(value);

      switch (metric) {
         case 'sharpe':
            return Math.max(0, Math.min(100, (value / 2.0) * 100));
         case 'sortino':
            return Math.max(0, Math.min(100, (value / 4.0) * 100));
         case 'drawdown':
            return Math.max(0, Math.min(100, (abs / 50.0) * 100));
         case 'calmar':
            return Math.max(0, Math.min(100, (value / 3.0) * 100));
         case 'volatility':
            return Math.max(0, Math.min(100, (abs / 50.0) * 100));
         default:
            return 0;
      }
   };

   const riskBorderClass: Record<RiskTone, string> = {
      green: 'border-green-200 dark:border-green-900/40',
      amber: 'border-amber-200 dark:border-amber-900/40',
      red: 'border-rose-200 dark:border-rose-900/40',
      slate: 'border-slate-200 dark:border-border-dark',
   };

   const riskBarClass: Record<RiskTone, string> = {
      green: 'bg-green-500',
      amber: 'bg-amber-500',
      red: 'bg-rose-500',
      slate: 'bg-slate-400',
   };

   const riskTextClass: Record<RiskTone, string> = {
      green: 'text-green-600 dark:text-green-400',
      amber: 'text-amber-600 dark:text-amber-400',
      red: 'text-rose-600 dark:text-rose-400',
      slate: 'text-slate-600 dark:text-slate-300',
   };

   // Hoisted out of the JSX child-expression container the array literal used
   // to live in — `key`/`barTestId` are stable machine identifiers (not
   // prose), so the i18n literal ratchet flagged them once this file was in
   // scope. Only `title`/`description` are translated.
   const riskMetricRows = riskMetrics ? [
      {
         key: 'sharpe' as RiskMetricKey,
         title: t('risk.metrics.sharpe.title'),
         value: riskMetrics.sharpe_ratio,
         formatter: (v: number) => v.toFixed(2),
         description: t('risk.metrics.sharpe.description'),
         barTestId: 'risk-bar-sharpe',
      },
      {
         key: 'sortino' as RiskMetricKey,
         title: t('risk.metrics.sortino.title'),
         value: riskMetrics.sortino_ratio,
         formatter: (v: number) => v.toFixed(2),
         description: t('risk.metrics.sortino.description'),
         barTestId: 'risk-bar-sortino',
      },
      {
         key: 'drawdown' as RiskMetricKey,
         title: t('risk.metrics.drawdown.title'),
         value: riskMetrics.max_drawdown,
         formatter: formatPercent,
         description: t('risk.metrics.drawdown.description'),
         barTestId: 'risk-bar-drawdown',
      },
      {
         key: 'calmar' as RiskMetricKey,
         title: t('risk.metrics.calmar.title'),
         value: riskMetrics.calmar_ratio,
         formatter: (v: number) => v.toFixed(2),
         description: t('risk.metrics.calmar.description'),
         barTestId: 'risk-bar-calmar',
      },
      {
         key: 'volatility' as RiskMetricKey,
         title: t('risk.metrics.volatility.title'),
         value: riskMetrics.volatility_annual,
         formatter: formatPercent,
         description: t('risk.metrics.volatility.description'),
         barTestId: 'risk-bar-volatility',
      },
      {
         key: 'return' as RiskMetricKey,
         title: t('risk.metrics.return.title'),
         value: riskMetrics.total_return,
         formatter: formatPercent,
         description: t('risk.metrics.return.description'),
      },
   ] : [];

   const renderMetric = (val: number | null | undefined, formatter: (v: number) => React.ReactNode, nullMsg = t('insufficientData')) => {
      if (val === null || val === undefined || !Number.isFinite(val)) return <span className="text-slate-400 dark:text-slate-500 font-normal text-sm">{nullMsg}</span>;
      return formatter(val);
   };

   if (!summary && activeTab === 'overview' && !loading) return <div className="p-8 text-slate-500">{t('errorLoadingData')}</div>;

   return (
      <div data-testid="performance-page" className="p-8 space-y-8 pb-24 max-w-[1400px] mx-auto w-full bg-gray-50 dark:bg-background-dark min-h-screen">
         <div className="flex flex-col md:flex-row justify-between items-start md:items-end gap-4">
            <div>
               <h1 className="text-3xl font-black tracking-tight">{t('title')}</h1>
               <div className="flex items-center gap-2 mt-1">
                  <span className={`size-2 rounded-full ${loading ? 'bg-amber-500' : 'bg-green-500'} animate-pulse`}></span>
                  <p className="text-slate-500 text-[11px] font-mono uppercase tracking-tight">{t('liveFeed', { date: summary?.snapshot_date || t('loadingEllipsis') })}</p>
               </div>
            </div>
            <div className="flex items-center gap-3">
               <div className="relative border border-slate-200 dark:border-border-dark rounded-lg bg-white dark:bg-card-dark mr-2 hidden sm:flex items-center p-1">
                  <button onClick={() => setTimePeriod('all_time')} className={`px-3 py-1.5 text-[11px] font-bold uppercase rounded-md transition-all ${timePeriod === 'all_time' ? 'bg-primary/10 text-primary dark:bg-primary/20 hover:bg-primary/20' : 'text-slate-500 hover:bg-slate-50 dark:hover:bg-surface-dark'}`}>{t('periods.allTime')}</button>
                  <button onClick={() => setTimePeriod('last_36m')} className={`px-3 py-1.5 text-[11px] font-bold uppercase rounded-md transition-all ${timePeriod === 'last_36m' ? 'bg-primary/10 text-primary dark:bg-primary/20 hover:bg-primary/20' : 'text-slate-500 hover:bg-slate-50 dark:hover:bg-surface-dark'}`}>{t('periods.last36m')}</button>
                  <button onClick={() => setTimePeriod('last_12m')} className={`px-3 py-1.5 text-[11px] font-bold uppercase rounded-md transition-all ${timePeriod === 'last_12m' ? 'bg-primary/10 text-primary dark:bg-primary/20 hover:bg-primary/20' : 'text-slate-500 hover:bg-slate-50 dark:hover:bg-surface-dark'}`}>{t('periods.last12m')}</button>
               </div>
               <button onClick={() => ExportAPI.downloadAiContext()} className="px-4 py-2 bg-white dark:bg-surface-dark border border-slate-200 dark:border-border-dark rounded-lg text-sm font-bold hover:bg-slate-50 dark:hover:bg-card-dark transition-colors flex items-center gap-2 text-slate-700 dark:text-slate-300">
                  <span className="material-symbols-outlined text-sm">download</span>
                  {t('exportAiContext')}
               </button>
            </div>
         </div>

         <div className="border-b border-slate-200 dark:border-border-dark mb-6">
            <nav className="-mb-px flex space-x-6">
               <button onClick={() => setActiveTab('overview')} className={`whitespace-nowrap pb-3 px-1 border-b-2 font-medium text-sm transition-colors ${activeTab === 'overview' ? 'border-primary text-primary dark:text-blue-400 relative z-10' : 'border-transparent text-slate-500 hover:text-slate-700 hover:border-slate-300 dark:text-slate-400 dark:hover:text-slate-300'}`}>{t('tabs.overview')}</button>
               <button onClick={() => setActiveTab('returns')} className={`whitespace-nowrap pb-3 px-1 border-b-2 font-medium text-sm transition-colors ${activeTab === 'returns' ? 'border-primary text-primary dark:text-blue-400 relative z-10' : 'border-transparent text-slate-500 hover:text-slate-700 hover:border-slate-300 dark:text-slate-400 dark:hover:text-slate-300'}`}>{t('tabs.returns')}</button>
               <button onClick={() => setActiveTab('attribution')} className={`whitespace-nowrap pb-3 px-1 border-b-2 font-medium text-sm transition-colors ${activeTab === 'attribution' ? 'border-primary text-primary dark:text-blue-400 relative z-10' : 'border-transparent text-slate-500 hover:text-slate-700 hover:border-slate-300 dark:text-slate-400 dark:hover:text-slate-300'}`}>{t('tabs.attribution')}</button>
               <button onClick={() => setActiveTab('risk')} className={`whitespace-nowrap pb-3 px-1 border-b-2 font-medium text-sm transition-colors ${activeTab === 'risk' ? 'border-primary text-primary dark:text-blue-400 relative z-10' : 'border-transparent text-slate-500 hover:text-slate-700 hover:border-slate-300 dark:text-slate-400 dark:hover:text-slate-300'}`}>{t('tabs.risk')}</button>
            </nav>
         </div>

         {error && (
            <div className="bg-red-50 border border-red-200 rounded-lg p-4 mb-4">
               <p className="text-red-700 text-sm">{error}</p>
            </div>
         )}
         <div className="animate-in fade-in duration-300">
            {activeTab === 'overview' && summary && (
               <div className="space-y-8">
                  {/* Main Chart Card: Waterfall */}
                  <div className="bg-white dark:bg-card-dark rounded-xl border border-slate-200 dark:border-border-dark p-6 shadow-sm">
                     <div className="flex flex-wrap justify-between items-start gap-4 mb-8">
                        <div>
                           <p className="text-slate-500 text-xs font-bold uppercase tracking-wider mb-1">{t('overview.lifetimePlContribution')}</p>
                           <div className="flex items-baseline gap-3">
                              <h2 className={`text-4xl font-mono font-bold ${getColor(summary.total_lifetime_pl)}`}><span className="money-value">{formatCurrency(summary.total_lifetime_pl)}</span></h2>
                              <div className="flex items-center gap-1 text-slate-400 font-mono text-sm font-bold">
                                 <span>{t('overview.cumulative')}</span>
                              </div>
                           </div>
                        </div>
                     </div>
                     <div className="h-[350px] w-full">
                        <ResponsiveContainer width="100%" height="100%">
                           <BarChart data={prepareWaterfallData()} margin={{ top: 20, right: 30, left: 20, bottom: 5 }}>
                              <XAxis dataKey="name" stroke="#94a3b8" fontSize={12} tickLine={false} axisLine={false} />
                              <YAxis hide />
                              <Tooltip
                                 contentStyle={{ backgroundColor: '#1e293b', border: '1px solid #334155', borderRadius: '8px', color: '#fff' }}
                                 cursor={{ fill: 'rgba(255,255,255,0.05)' }}
                                 formatter={(value: number, name: string, props: any) => {
                                    if (name === "value") return [formatCurrency(value), t('overview.totalLifetimePl')];
                                    return [formatCurrency(value), name];
                                 }}
                              />
                              <ReferenceLine y={0} stroke="#475569" strokeDasharray="3 3" />
                              <Bar dataKey="range" radius={[4, 4, 4, 4]}>
                                 {prepareWaterfallData().map((entry, index) => (
                                    <Cell key={`cell-${index}`} fill={entry.isTotal ? '#3b82f6' : entry.isPositive ? '#22c55e' : '#ef4444'} />
                                 ))}
                              </Bar>
                           </BarChart>
                        </ResponsiveContainer>
                     </div>
                  </div>

                  {/* KPI Cards (Replaced per Spec) */}
                  <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
                     {/* KPI 1: Portfolio Value */}
                     <div className="bg-white dark:bg-card-dark border border-slate-200 dark:border-border-dark p-5 rounded-xl">
                        <p className="text-xs font-bold text-slate-500 uppercase tracking-widest mb-3">{t('overview.portfolioValue')}</p>
                        <div className="flex items-center gap-2">
                           <h3 className="text-2xl lg:text-3xl font-mono font-bold text-slate-900 dark:text-white" title={formatCurrency(summary.net_worth)}>
                              <span className="money-value">¥{formatCompact(summary.net_worth)}</span>
                           </h3>
                        </div>
                        <div className="mt-4 h-1 bg-slate-100 dark:bg-background-dark rounded-full overflow-hidden">
                           <div className="h-full rounded-full bg-primary w-full"></div>
                        </div>
                     </div>

                     {/* KPI 2: Unrealized P&L */}
                     <div className="bg-white dark:bg-card-dark border border-slate-200 dark:border-border-dark p-5 rounded-xl">
                        <p className="text-xs font-bold text-slate-500 uppercase tracking-widest mb-3">{t('overview.unrealizedPl')}</p>
                        <div className="flex items-center gap-2">
                           <h3 className={`text-2xl lg:text-3xl font-mono font-bold ${getColor(summary.total_unrealized_pl)}`} title={formatCurrency(summary.total_unrealized_pl)}>
                              <span className="money-value">¥{formatCompact(summary.total_unrealized_pl)}</span>
                           </h3>
                           <span className={`text-[11px] font-bold font-mono ${getColor(summary.unrealized_pl_pct)} mt-1`}>{formatPercent(summary.unrealized_pl_pct)}</span>
                        </div>
                        <div className="mt-4 h-1 bg-slate-100 dark:bg-background-dark rounded-full overflow-hidden">
                           <div className="h-full rounded-full bg-blue-500 w-3/4"></div>
                        </div>
                     </div>

                     {/* KPI 3: Realized P&L */}
                     <div className="bg-white dark:bg-card-dark border border-slate-200 dark:border-border-dark p-5 rounded-xl">
                        <p className="text-xs font-bold text-slate-500 uppercase tracking-widest mb-3">{t('overview.realizedPlFifo')}</p>
                        <div className="flex items-center gap-2">
                           <h3 className={`text-2xl lg:text-3xl font-mono font-bold ${getColor(summary.total_realized_pl)}`} title={formatCurrency(summary.total_realized_pl)}>
                              <span className="money-value">¥{formatCompact(summary.total_realized_pl)}</span>
                           </h3>
                        </div>
                        <div className="mt-4 h-1 bg-slate-100 dark:bg-background-dark rounded-full overflow-hidden">
                           <div className="h-full rounded-full bg-indigo-500 w-1/2"></div>
                        </div>
                     </div>

                     {/* KPI 4: Lifetime P&L */}
                     <div className="bg-white dark:bg-card-dark border border-slate-200 dark:border-border-dark p-5 rounded-xl">
                        <p className="text-xs font-bold text-slate-500 uppercase tracking-widest mb-3">{t('overview.lifetimePl')}</p>
                        <div className="flex items-center gap-2">
                           <h3 className={`text-2xl lg:text-3xl font-mono font-bold ${getColor(summary.total_lifetime_pl)}`} title={formatCurrency(summary.total_lifetime_pl)}>
                              <span className="money-value">¥{formatCompact(summary.total_lifetime_pl)}</span>
                           </h3>
                        </div>
                        <div className="mt-4 h-1 bg-slate-100 dark:bg-background-dark rounded-full overflow-hidden">
                           <div className="h-full rounded-full bg-purple-500 w-full"></div>
                        </div>
                     </div>
                  </div>

                  <div className="text-right text-xs text-slate-400">
                     {t('overview.assetsTracked', { count: summary.asset_count, date: summary.snapshot_date })}
                  </div>

                  {/* Performance By Class */}
                  {byClass && (
                     <section className="bg-white dark:bg-card-dark rounded-xl border border-slate-200 dark:border-border-dark overflow-hidden mb-8 shadow-sm">
                        <div className="p-4 border-b border-slate-200 dark:border-border-dark flex justify-between items-center bg-slate-50/50 dark:bg-surface-dark/30">
                           <h2 className="text-sm font-bold flex items-center gap-2 uppercase tracking-wider">
                              <span className="material-symbols-outlined text-primary text-lg">pie_chart</span> {t('overview.performanceByClass')}
                           </h2>

                        </div>
                        <table className="w-full text-left border-collapse">
                           <thead>
                              <tr className="text-[11px] uppercase tracking-wider text-slate-500 border-b border-slate-200 dark:border-border-dark select-none">
                                 <th className="px-6 py-3 font-semibold cursor-pointer hover:bg-slate-50 dark:hover:bg-white/[0.02]" onClick={() => requestSort('class_name')}>{t('overview.columns.class')} {sortConfig?.key === 'class_name' && (sortConfig.direction === 'asc' ? '↑' : '↓')}</th>
                                 <th className="px-6 py-3 font-semibold text-right cursor-pointer hover:bg-slate-50 dark:hover:bg-white/[0.02]" onClick={() => requestSort('market_value')}>{t('overview.columns.value')} {sortConfig?.key === 'market_value' && (sortConfig.direction === 'asc' ? '↑' : '↓')}</th>
                                 <th className="px-6 py-3 font-semibold text-right cursor-pointer hover:bg-slate-50 dark:hover:bg-white/[0.02]" onClick={() => requestSort('cost_basis')}>{t('overview.columns.costBasis')} {sortConfig?.key === 'cost_basis' && (sortConfig.direction === 'asc' ? '↑' : '↓')}</th>
                                 <th className="px-6 py-3 font-semibold text-right cursor-pointer hover:bg-slate-50 dark:hover:bg-white/[0.02]" onClick={() => requestSort('unrealized_pl')}>{t('overview.unrealizedPl')} {sortConfig?.key === 'unrealized_pl' && (sortConfig.direction === 'asc' ? '↑' : '↓')}</th>
                                 <th className="px-6 py-3 font-semibold text-right cursor-pointer hover:bg-slate-50 dark:hover:bg-white/[0.02]" onClick={() => requestSort('realized_pl')}>{t('overview.realizedPl')} {sortConfig?.key === 'realized_pl' && (sortConfig.direction === 'asc' ? '↑' : '↓')}</th>
                                 <th className="px-6 py-3 font-semibold text-right cursor-pointer hover:bg-slate-50 dark:hover:bg-white/[0.02]" onClick={() => requestSort('return_pct')}>{t('overview.columns.returnPct')} {sortConfig?.key === 'return_pct' && (sortConfig.direction === 'asc' ? '↑' : '↓')}</th>
                                 <th className="px-6 py-3 font-semibold text-right cursor-pointer hover:bg-slate-50 dark:hover:bg-white/[0.02]" onClick={() => requestSort('weight_pct')}>{t('overview.columns.weightPct')} {sortConfig?.key === 'weight_pct' && (sortConfig.direction === 'asc' ? '↑' : '↓')}</th>
                              </tr>
                           </thead>
                           <tbody className="divide-y divide-slate-100 dark:divide-border-dark">
                              {sortData(byClass.top_classes, sortConfig?.key || DEFAULT_CLASS_SORT_KEY).map((cls: any, i: number) => (
                                 <tr key={i} className="hover:bg-slate-50/80 dark:hover:bg-white/[0.02] transition-colors">
                                    <td className="px-6 py-3 text-xs font-bold text-slate-900 dark:text-white">{localizedClassName(cls.class_name, cls.class_name_cn, lang)}</td>
                                    <td className="px-6 py-3 text-right font-mono text-[11px]">{formatCNY(cls.market_value, 2)}</td>
                                    <td className="px-6 py-3 text-right font-mono text-[11px] text-slate-400">{formatCNY(cls.cost_basis, 2)}</td>
                                    <td className={`px-6 py-3 text-right font-mono text-[11px] font-bold ${getColor(cls.unrealized_pl)}`}>{formatCNY(cls.unrealized_pl, 2)}</td>
                                    <td className={`px-6 py-3 text-right font-mono text-[11px] ${getColor(cls.realized_pl)}`}>{formatCNY(cls.realized_pl, 2)}</td>
                                    <td className={`px-6 py-3 text-right font-mono text-[11px] font-bold ${getColor(cls.return_pct)}`}>{formatPercent(cls.return_pct)}</td>
                                    <td className="px-6 py-3 text-right font-mono text-[11px]">{cls.weight_pct.toFixed(1)}%</td>
                                 </tr>
                              ))}
                           </tbody>
                        </table>
                     </section>
                  )}

                  {/* Top/Bottom Performers */}
                  {gains && (
                     <section className="bg-white dark:bg-card-dark rounded-xl border border-slate-200 dark:border-border-dark overflow-hidden mb-8 shadow-sm">
                        <div className="p-4 border-b border-slate-200 dark:border-border-dark flex justify-between items-center bg-slate-50/50 dark:bg-surface-dark/30">
                           <h2 className="text-sm font-bold flex items-center gap-2 uppercase tracking-wider">
                              <span className="material-symbols-outlined text-primary text-lg">leaderboard</span> {t('overview.topBottomPerformers')}
                           </h2>
                        </div>
                        <div className="overflow-x-auto">
                           <table className="w-full text-left border-collapse">
                              <thead>
                                 <tr className="text-[11px] uppercase tracking-wider text-slate-500 border-b border-slate-200 dark:border-border-dark">
                                    <th className="px-6 py-3 font-semibold w-24">{t('overview.columns.rank')}</th>
                                    <th className="px-6 py-3 font-semibold">{t('overview.columns.asset')}</th>
                                    <th className="px-6 py-3 font-semibold">{t('overview.columns.class')}</th>
                                    <th className="px-6 py-3 font-semibold text-right">{t('overview.columns.cost')}</th>
                                    <th className="px-6 py-3 font-semibold text-right">{t('overview.columns.marketValue')}</th>
                                    <th className="px-6 py-3 font-semibold text-right">{t('overview.unrealizedPl')}</th>
                                    <th className="px-6 py-3 font-semibold text-right">{t('overview.realizedPl')}</th>
                                    <th className="px-6 py-3 font-semibold text-right">{t('overview.columns.returnPct')}</th>
                                 </tr>
                              </thead>
                              <tbody className="divide-y divide-slate-100 dark:divide-border-dark">
                                 {(() => {
                                    const finalAssets = getTopBottomAssets(gains.assets);

                                    return finalAssets.map((asset, i) => {
                                       const isTop = (asset.unrealized_pl + asset.realized_pl) >= 0;
                                       // Top logic: 0..4 = Top 1..Top 5
                                       // Bottom logic: total..total-4 = Bottom 1..Bottom 5
                                       let rankText = "";
                                       let rankClasses = "";

                                       if (i < 5) {
                                          rankText = t('overview.rankTop', { n: i + 1 });
                                          rankClasses = RANK_CLASSES_TOP;
                                       } else {
                                          const reverseIndex = finalAssets.length - i;
                                          rankText = t('overview.rankBottom', { n: reverseIndex });
                                          rankClasses = RANK_CLASSES_BOTTOM;
                                       }

                                       return (
                                          <tr key={i} className="hover:bg-slate-50/80 dark:hover:bg-white/[0.02] transition-colors">
                                             <td className="px-6 py-3">
                                                <span className={`inline-flex items-center justify-center px-1.5 py-0.5 rounded text-[10px] uppercase min-w-[40px] ${rankClasses}`}>
                                                   {rankText}
                                                </span>
                                             </td>
                                             <td className="px-6 py-3 text-xs font-medium text-slate-900 dark:text-white max-w-[200px] truncate" title={asset.name}>
                                                {asset.name}
                                                <div className="text-[10px] text-slate-400 font-mono">{asset.asset_id}</div>
                                             </td>
                                             <td className="px-6 py-3 text-xs text-slate-500">{localizedClassName(asset.top_class, asset.top_class_cn, lang)}</td>
                                             <td className="px-6 py-3 text-right font-mono text-[11px] text-slate-400">{formatCNY(asset.cost_basis, 2)}</td>
                                             <td className="px-6 py-3 text-right font-mono text-[11px]">{formatCNY(asset.market_value, 2)}</td>
                                             <td className={`px-6 py-3 text-right font-mono text-[11px] font-bold ${getColor(asset.unrealized_pl_native ?? asset.unrealized_pl)}`}>
                                                {/* P&L DISPLAY: Values are in native asset currency (USD for Schwab/RSU).
                                                    Backend converts to CNY for totals using today's FX rate (constant-FX method).
                                                    See src/api/routes/performance.py header comment for full explanation. */}
                                                <span className="money-value">{formatPnlAmount(asset.unrealized_pl_native ?? asset.unrealized_pl, asset.pnl_currency)}</span>
                                                {asset.pnl_currency && asset.pnl_currency !== 'CNY' && <span className="text-xs opacity-60 ml-1">{asset.pnl_currency}</span>}
                                             </td>
                                             <td className={`px-6 py-3 text-right font-mono text-[11px] ${getColor(asset.realized_pl_native ?? asset.realized_pl)}`}>
                                                <span className="money-value">{formatPnlAmount(asset.realized_pl_native ?? asset.realized_pl, asset.pnl_currency)}</span>
                                                {asset.pnl_currency && asset.pnl_currency !== 'CNY' && <span className="text-xs opacity-60 ml-1">{asset.pnl_currency}</span>}
                                             </td>
                                             <td className={`px-6 py-3 text-right font-mono text-[11px] font-bold ${getColor(asset.return_pct)}`}>{formatPercent(asset.return_pct)}</td>
                                          </tr>
                                       );
                                    });
                                 })()}
                              </tbody>
                           </table>
                        </div>
                     </section>
                  )}
               </div>
            )}

            {/* TAB: RETURNS */}
            {activeTab === 'returns' && returns && (
               <div className="space-y-6">
                  <div className="bg-white dark:bg-card-dark border border-slate-200 dark:border-border-dark p-6 rounded-xl shadow-sm">
                     <h3 className="text-sm font-bold mb-4">{t('returns.netWorthOverTime')}</h3>
                     <div className="h-[300px]">
                        {history.length > 0 ? (
                           <ResponsiveContainer width="100%" height="100%">
                              <AreaChart data={history} margin={{ top: 10, right: 10, left: 10, bottom: 0 }}>
                                 <XAxis dataKey="name" tick={{ fontSize: 10 }} />
                                 <YAxis tick={(props: any) => {
                                    const { x, y, payload } = props;
                                    return (
                                       <g transform={`translate(${x},${y})`}>
                                          <text className="money-value" textAnchor="end" fill="#64748b" fontSize={10} dy="0.355em">
                                             {t('returns.axisWan', { value: (payload.value / 10000).toFixed(0) })}
                                          </text>
                                       </g>
                                    );
                                 }} />
                                 <Tooltip formatter={(value: number) => `¥${value.toLocaleString(undefined, { maximumFractionDigits: 0 })}`} />
                                 <Area type="monotone" dataKey="value" stroke="#3b82f6" strokeWidth={2} fill="#3b82f6" fillOpacity={0.15} />
                              </AreaChart>
                           </ResponsiveContainer>
                        ) : (
                           <div className="h-full flex items-center justify-center text-slate-400 text-sm">{t('returns.noHistoryData')}</div>
                        )}
                     </div>
                  </div>

                  <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
                     <div className="bg-white dark:bg-card-dark border border-slate-200 dark:border-border-dark p-5 rounded-xl">
                        <p className="text-xs font-bold text-slate-500 uppercase tracking-widest mb-3">{t('returns.twrCumulative')}</p>
                        <h3 className={`text-2xl font-mono font-bold ${getMetricTextColor(returns.twr_cumulative)}`}>
                           {renderMetric(returns.twr_cumulative, formatPercent)}
                        </h3>
                        <hr className="my-3 border-slate-100 dark:border-border-dark" />
                        <p className="text-xs text-slate-400">{t('returns.twrCumulativeDesc')}</p>
                     </div>
                     <div className="bg-white dark:bg-card-dark border border-slate-200 dark:border-border-dark p-5 rounded-xl">
                        <p className="text-xs font-bold text-slate-500 uppercase tracking-widest mb-3">{t('returns.twrYtd')}</p>
                        <h3 className={`text-2xl font-mono font-bold ${getMetricTextColor(returns.twr_ytd)}`}>
                           {renderMetric(returns.twr_ytd, formatPercent)}
                        </h3>
                        <hr className="my-3 border-slate-100 dark:border-border-dark" />
                        <p className="text-xs text-slate-400">{t('returns.twrYtdDesc')}</p>
                     </div>
                     <div className="bg-white dark:bg-card-dark border border-slate-200 dark:border-border-dark p-5 rounded-xl">
                        <p className="text-xs font-bold text-slate-500 uppercase tracking-widest mb-3">{t('returns.twr1y')}</p>
                        <h3 className={`text-2xl font-mono font-bold ${getMetricTextColor(returns.twr_1y)}`}>
                           {renderMetric(returns.twr_1y, formatPercent)}
                        </h3>
                        <hr className="my-3 border-slate-100 dark:border-border-dark" />
                        <p className="text-xs text-slate-400">{t('returns.twr1yDesc')}</p>
                     </div>
                     <div className="bg-white dark:bg-card-dark border border-slate-200 dark:border-border-dark p-5 rounded-xl relative overflow-hidden group">
                        <div className="absolute top-0 right-0 p-4 opacity-10 group-hover:opacity-20 transition-opacity">
                           <span className="material-symbols-outlined text-4xl">functions</span>
                        </div>
                        <p className="text-xs font-bold text-slate-500 uppercase tracking-widest mb-3">{t('returns.mwrXirr')}</p>
                        <h3 className={`text-2xl font-mono font-bold ${getMetricTextColor(returns.mwr_xirr)}`}>
                           {renderMetric(returns.mwr_xirr, formatPercent)}
                        </h3>
                        <hr className="my-3 border-slate-100 dark:border-border-dark" />
                        <p className="text-xs text-slate-400">{t('returns.mwrXirrDesc')}</p>
                     </div>
                  </div>
               </div>
            )}

            {/* TAB: ATTRIBUTION */}
            {activeTab === 'attribution' && (
               !attribution ? (
                  <div className="p-8 text-center text-slate-500 bg-white dark:bg-card-dark rounded-xl border border-slate-200 dark:border-border-dark">
                     {t('attribution.noData')}
                  </div>
               ) : (
                  <div className="space-y-6">
                     <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
                        <div className="bg-white dark:bg-card-dark border border-slate-200 dark:border-border-dark p-5 rounded-xl text-center">
                           <p className="text-xs font-bold text-slate-500 uppercase tracking-widest mb-2">{t('attribution.portfolioReturn')}</p>
                           <h3 className="text-2xl font-mono font-bold">{formatPercent(attribution.portfolio_return)}</h3>
                        </div>
                        <div className="bg-white dark:bg-card-dark border border-slate-200 dark:border-border-dark p-5 rounded-xl text-center">
                           <p className="text-xs font-bold text-slate-500 uppercase tracking-widest mb-2">{t('attribution.benchmarkReturn')}</p>
                           <h3 className="text-2xl font-mono font-bold">{formatPercent(attribution.benchmark_return)}</h3>
                        </div>
                        <div className="bg-white dark:bg-card-dark border border-primary/30 p-5 rounded-xl text-center">
                           <p className="text-xs font-bold text-primary uppercase tracking-widest mb-2">{t('attribution.excessReturn')}</p>
                           <h3 className={`text-2xl font-mono font-bold ${getColor(attribution.excess_return)}`}>{formatPercent(attribution.excess_return)}</h3>
                        </div>
                     </div>

                     <div className="bg-white dark:bg-card-dark rounded-xl border border-slate-200 dark:border-border-dark p-6 shadow-sm">
                        <h3 className="text-sm font-bold flex items-center gap-2 uppercase tracking-wider mb-6">
                           {t('attribution.byClass')} <span className="text-slate-400 normal-case">{t('attribution.allTime')}</span>
                        </h3>
                        <div className="h-[350px]">
                           <ResponsiveContainer width="100%" height="100%">
                              <BarChart data={attribution.classes} margin={{ top: 20, right: 30, left: 20, bottom: 5 }}>
                                 <XAxis dataKey="class" stroke="#94a3b8" fontSize={12} tickLine={false} axisLine={false} />
                                 <YAxis tickLine={false} axisLine={false} tick={{ fontSize: 12, fill: '#94a3b8' }} tickFormatter={(val) => `${val}%`} />
                                 <Tooltip
                                    contentStyle={{ backgroundColor: '#1e293b', border: '1px solid #334155', borderRadius: '8px', color: '#fff' }}
                                    formatter={(value: number, name: string) => [`${value.toFixed(2)}%`, name]}
                                 />
                                 <ReferenceLine y={0} stroke="#475569" strokeDasharray="3 3" />
                                 <Bar dataKey="allocation_effect" stackId="a" fill="#3b82f6" name={t('attribution.allocation')} />
                                 <Bar dataKey="selection_effect" stackId="a" fill="#10b981" name={t('attribution.selection')} />
                                 <Bar dataKey="interaction_effect" stackId="a" fill="#f59e0b" name={t('attribution.interaction')} />
                              </BarChart>
                           </ResponsiveContainer>
                        </div>
                     </div>

                     <div className="bg-white dark:bg-card-dark rounded-xl border border-slate-200 dark:border-border-dark overflow-hidden shadow-sm">
                        <table className="w-full text-left border-collapse">
                           <thead>
                              <tr className="text-[11px] uppercase tracking-wider text-slate-500 border-b border-slate-200 dark:border-border-dark">
                                 <th className="px-6 py-3 font-semibold">{t('overview.columns.class')}</th>
                                 <th className="px-6 py-3 font-semibold text-right">{t('attribution.columns.portW')}</th>
                                 <th className="px-6 py-3 font-semibold text-right">{t('attribution.columns.benchW')}</th>
                                 <th className="px-6 py-3 font-semibold text-right">{t('attribution.columns.portR')}</th>
                                 <th className="px-6 py-3 font-semibold text-right">{t('attribution.columns.benchR')}</th>
                                 <th className="px-6 py-3 font-semibold text-right">{t('attribution.columns.total')}</th>
                              </tr>
                           </thead>
                           <tbody className="divide-y divide-slate-100 dark:divide-border-dark">
                              {attribution.classes.map((c, i) => (
                                 <tr key={i} className="hover:bg-slate-50/80 dark:hover:bg-white/[0.02] transition-colors">
                                    <td className="px-6 py-3 text-xs font-bold text-slate-900 dark:text-white">{c.class}</td>
                                    <td className="px-6 py-3 text-right font-mono text-[11px]">{(c.portfolio_weight * 100).toFixed(1)}%</td>
                                    <td className="px-6 py-3 text-right font-mono text-[11px] text-slate-500">{(c.benchmark_weight * 100).toFixed(1)}%</td>
                                    <td className="px-6 py-3 text-right font-mono text-[11px]">{c.portfolio_return.toFixed(2)}%</td>
                                    <td className="px-6 py-3 text-right font-mono text-[11px] text-slate-500">{c.benchmark_return.toFixed(2)}%</td>
                                    <td className={`px-6 py-3 text-right font-mono text-[11px] font-bold ${getColor(c.total_effect)}`}>{formatPercent(c.total_effect)}</td>
                                 </tr>
                              ))}
                           </tbody>
                        </table>
                     </div>

                     <div className="bg-white dark:bg-card-dark rounded-xl border border-slate-200 dark:border-border-dark p-5 shadow-sm">
                        <h4 className="text-sm font-bold tracking-wide mb-3">{t('attribution.howToRead.title')}</h4>
                        <p className="text-xs text-slate-600 dark:text-slate-300 mb-3">
                           <Trans
                              t={t}
                              i18nKey="attribution.howToRead.intro"
                              components={{ strong: <span className="font-semibold" /> }}
                           />
                        </p>
                        <p className="text-xs font-mono text-slate-500 dark:text-slate-400 mb-3">
                           {t('attribution.howToRead.formula')}
                        </p>
                        <div className="text-xs text-slate-600 dark:text-slate-300 space-y-1">
                           <p>{t('attribution.howToRead.note1')}</p>
                           <p>{t('attribution.howToRead.note2')}</p>
                           <p>{t('attribution.howToRead.note3')}</p>
                        </div>
                     </div>
                  </div>
               )
            )}

            {/* TAB: RISK METRICS */}
            {activeTab === 'risk' && (
               !riskMetrics ? (
                  <div className="p-8 text-center text-slate-500 bg-white dark:bg-card-dark rounded-xl border border-slate-200 dark:border-border-dark">
                     {t('risk.noData')}
                  </div>
               ) : (
               <div className="space-y-6">
                  <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-6">
                     {[
                        {
                           key: 'sharpe' as RiskMetricKey,
                           title: t('risk.metrics.sharpe.title'),
                           value: riskMetrics.sharpe_ratio,
                           formatter: (v: number) => v.toFixed(2),
                           description: t('risk.metrics.sharpe.description'),
                           barTestId: 'risk-bar-sharpe',
                        },
                        {
                           key: 'sortino' as RiskMetricKey,
                           title: t('risk.metrics.sortino.title'),
                           value: riskMetrics.sortino_ratio,
                           formatter: (v: number) => v.toFixed(2),
                           description: t('risk.metrics.sortino.description'),
                           barTestId: 'risk-bar-sortino',
                        },
                        {
                           key: 'drawdown' as RiskMetricKey,
                           title: t('risk.metrics.drawdown.title'),
                           value: riskMetrics.max_drawdown,
                           formatter: formatPercent,
                           description: t('risk.metrics.drawdown.description'),
                           barTestId: 'risk-bar-drawdown',
                        },
                        {
                           key: 'calmar' as RiskMetricKey,
                           title: t('risk.metrics.calmar.title'),
                           value: riskMetrics.calmar_ratio,
                           formatter: (v: number) => v.toFixed(2),
                           description: t('risk.metrics.calmar.description'),
                           barTestId: 'risk-bar-calmar',
                        },
                        {
                           key: 'volatility' as RiskMetricKey,
                           title: t('risk.metrics.volatility.title'),
                           value: riskMetrics.volatility_annual,
                           formatter: formatPercent,
                           description: t('risk.metrics.volatility.description'),
                           barTestId: 'risk-bar-volatility',
                        },
                        {
                           key: 'return' as RiskMetricKey,
                           title: t('risk.metrics.return.title'),
                           value: riskMetrics.total_return,
                           formatter: formatPercent,
                           description: t('risk.metrics.return.description'),
                        },
                     ].map((metric) => {
                        const tone = getRiskColor(metric.key, metric.value);
                        const barWidth = getRiskBarWidth(metric.key, metric.value);
                        const showBar = metric.key !== 'return' && metric.value !== null;
                        const showDrawdownWarning = metric.key === 'drawdown' && (metric.value ?? 0) > 0;
                        const showVolatilityWarning = metric.key === 'volatility' && (metric.value ?? 0) > 50 && riskMetrics.data_points < 24;

                        let statusText = "";
                        if (metric.value !== null && metric.key !== 'return') {
                           if (tone === 'green') statusText = t('risk.status.excellent');
                           else if (tone === 'amber') statusText = t('risk.status.monitor');
                           else if (tone === 'red') statusText = t('risk.status.warning');
                        }

                        return (
                           <div key={metric.key} className={`bg-white dark:bg-card-dark border ${riskBorderClass[tone]} p-5 rounded-xl`}>
                              <div className="flex justify-between items-start mb-3">
                                 <p className="text-xs font-bold text-slate-500 uppercase tracking-widest">{metric.title}</p>
                                 {statusText && (
                                    <span className={`text-[10px] font-bold uppercase px-2 py-0.5 rounded-full ${tone === 'green' ? 'bg-green-100 text-green-700 dark:bg-green-900/30' : tone === 'amber' ? 'bg-amber-100 text-amber-700 dark:bg-amber-900/30' : 'bg-red-100 text-red-700 dark:bg-red-900/30'}`}>
                                       {statusText}
                                    </span>
                                 )}
                              </div>
                              <h3 className={`text-2xl font-mono font-bold ${riskTextClass[tone]}`}>
                                 {renderMetric(metric.value, metric.formatter)}
                              </h3>

                              {showBar && (
                                 <div className="mt-3 h-1.5 bg-slate-100 dark:bg-background-dark rounded-full overflow-hidden">
                                    <div
                                       data-testid={metric.barTestId}
                                       className={`h-full rounded-full ${riskBarClass[tone]} ${metric.key === 'drawdown' ? 'ml-auto' : ''}`}
                                       style={{ width: `${barWidth}%` }}
                                    />
                                 </div>
                              )}

                              <p className="mt-2 text-[10px] text-slate-400 dark:text-slate-500">{metric.description}</p>
                              {showDrawdownWarning && (
                                 <p className="mt-1 text-[10px] text-amber-600 dark:text-amber-400">{t('risk.limitedDataDrawdown')}</p>
                              )}
                              {showVolatilityWarning && (
                                 <p className="mt-1 text-[10px] text-amber-600 dark:text-amber-400">{t('risk.limitedDataVolatility')}</p>
                              )}
                           </div>
                        );
                     })}
                  </div>
                  <div className="text-right text-xs text-slate-400">
                     {t('risk.basedOnDataPoints', { count: riskMetrics.data_points })}
                     {(!riskMetrics.sharpe_ratio || !riskMetrics.volatility_annual) && t('risk.insufficientDataNote')}
                  </div>
               </div>
               )
            )}
         </div>
      </div>
   );
};
