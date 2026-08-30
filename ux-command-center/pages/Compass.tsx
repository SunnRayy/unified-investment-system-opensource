import React, { useEffect, useState } from 'react';
import { useTranslation, Trans } from 'react-i18next';
import { PieChart, Pie, Cell, Tooltip, ResponsiveContainer } from 'recharts';
import { usePortfolioFilter } from '../src/context/usePortfolioFilter';
import { api, CompassSummary, AllocationRow, CompassAllocationMeta } from '../src/services/api';
import { APP_VERSION_DISPLAY } from '../src/version';
import { useCurrency } from '../src/context/useCurrency';

const CLASS_HEX: Record<string, string> = {
   equity: '#2563eb',
   'fixed income': '#6366f1',
   cash: '#64748b',
   commodities: '#f59e0b',
   alternatives: '#14b8a6',
};
const getClassHex = (name: string): string => {
   const lower = name.toLowerCase();
   for (const [key, color] of Object.entries(CLASS_HEX)) {
      if (lower.includes(key)) return color;
   }
   return '#a855f7';
};

/** Hoisted out of the table-row-render callback (nested inside a JSX child-
 *  expression container) so these Tailwind class / Material-icon-name
 *  literals aren't scanned by the i18n literal ratchet as prose. */
function getAllocationRowStyle(row: AllocationRow) {
   const isOver = row.status === 'over';
   const isUnder = row.status === 'under';
   return {
      isOver,
      isUnder,
      driftValColor: isOver ? 'text-amber-500 font-semibold' : isUnder ? 'text-red-500 font-semibold' : 'text-green-600 dark:text-green-500',
      statusIcon: isOver ? 'warning' : isUnder ? 'arrow_downward' : 'radio_button_unchecked',
      statusColor: isOver ? 'text-amber-500' : isUnder ? 'text-red-500' : 'text-slate-400',
      bgRow: row.is_top_level ? 'bg-slate-50/50 dark:bg-surface-dark/10' : '',
   };
}
function getDeltaColor(deltaCNY: number | null): string {
   if (deltaCNY != null && deltaCNY > 0) return 'text-green-600 dark:text-green-400';
   if (deltaCNY != null && deltaCNY < 0) return 'text-red-500';
   return 'text-slate-500';
}
function getLadderStyle(status: string) {
   const isOver = status === 'over';
   const isUnder = status === 'under';
   return {
      markerColor: isOver ? 'bg-amber-500' : isUnder ? 'bg-red-500' : 'bg-slate-400',
      valColor: isOver ? 'text-amber-500 font-semibold' : isUnder ? 'text-red-500 font-semibold' : 'text-slate-500',
      icon: isOver ? 'warning' : isUnder ? 'arrow_downward' : 'check_circle',
      iconColor: isOver ? 'text-amber-500' : isUnder ? 'text-red-500' : 'text-green-500',
   };
}

const DriftBar: React.FC<{ drift: number; tolerance?: number }> = ({ drift, tolerance = 2.5 }) => {
   const MAX_RANGE = 15; // ±15%
   const clampedDrift = Math.max(-MAX_RANGE, Math.min(MAX_RANGE, drift));
   
   const isOver = drift > tolerance;
   const isUnder = drift < -tolerance;
   const fillColor = isOver ? 'bg-amber-500' : isUnder ? 'bg-red-500' : 'bg-slate-400 dark:bg-slate-500';
   
   const start = Math.min(0, clampedDrift);
   const width = Math.abs(clampedDrift);
   
   const leftPct = 50 + (start / MAX_RANGE) * 50;
   const widthPct = (width / MAX_RANGE) * 50;
   
   const tolWidthPct = (tolerance / MAX_RANGE) * 50 * 2;
   const tolLeftPct = 50 - (tolWidthPct / 2);

   return (
      <div className="relative w-16 h-1.5 bg-slate-200 dark:bg-slate-800 rounded-full mx-auto md:mx-0 md:ml-auto">
         <div className="absolute top-[-2px] bottom-[-2px] left-1/2 w-px bg-slate-400 dark:bg-slate-500"></div>
         <div className="absolute top-0 bottom-0 bg-green-500/15 rounded-full" style={{ left: `${tolLeftPct}%`, width: `${tolWidthPct}%` }}></div>
         <div className={`absolute top-0 bottom-0 rounded-full ${fillColor}`} style={{ left: `${leftPct}%`, width: `${widthPct}%` }}></div>
      </div>
   );
};

export const Compass: React.FC = () => {
   const { t } = useTranslation('reports');
   const { includeNonRebalanceable } = usePortfolioFilter();
   const { convertFromCNY, currencySymbol } = useCurrency();
   const [summary, setSummary] = useState<CompassSummary | null>(null);
   const [allocation, setAllocation] = useState<AllocationRow[]>([]);
   const [allocationMeta, setAllocationMeta] = useState<CompassAllocationMeta | null>(null);
   const [includePending, setIncludePending] = useState(false);
   const [loading, setLoading] = useState(true);
   const [error, setError] = useState<string | null>(null);
   const [copied, setCopied] = useState(false);

   useEffect(() => {
      const fetchData = async () => {
         setError(null);
         try {
            const [summaryData, allocationResult] = await Promise.all([
               api.getCompassSummary(includeNonRebalanceable),
               api.getCompassAllocation(includeNonRebalanceable, includePending)
            ]);
            setSummary(summaryData);
            setAllocation(allocationResult.rows);
            setAllocationMeta(allocationResult.meta);
         } catch (err) {
            console.error("Failed to fetch Compass data:", err);
            setError(t('compass.errors.load'));
         } finally {
            setLoading(false);
         }
      };
      fetchData();
   }, [includeNonRebalanceable, includePending]);

   /**
    * formatCurrency — displays a value in the user's selected reporting currency.
    * When the asset's native currency is CNY (most portfolio values), it converts
    * to USD using the context rate if USD is selected.
    * Non-CNY native values (e.g. HKD) are passed through unchanged with their own symbol.
    */
   const formatCurrency = (value: number, nativeCurrency: string) => {
      if (nativeCurrency === 'CNY') {
         const displayValue = convertFromCNY(value);
         return `${currencySymbol}${displayValue.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
      }
      const symbolMap: Record<string, string> = { 'CNY': '¥', 'USD': '$', 'HKD': 'HK$' };
      const symbol = symbolMap[nativeCurrency] || nativeCurrency;
      return `${symbol}${value.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
   };

   const formatPercent = (value: number, signed = false) => {
      const sign = signed && value > 0 ? '+' : '';
      return `${sign}${value.toFixed(2)}%`;
   };

   if (loading) return <div className="p-8 text-slate-500">{t('compass.loading')}</div>;
   if (error) return <div className="p-8 text-red-500">{error}</div>;
   if (!summary) return <div className="p-8 text-slate-500">{t('compass.noData')}</div>;

   const driftBreached = summary.drift_index > 5;
   const driftIcon = driftBreached ? 'warning' : 'check_circle';
   const driftColor = driftBreached ? 'text-amber-500' : 'text-slate-900 dark:text-white';
   const driftIconColor = driftBreached ? 'text-amber-500' : 'text-green-500';
   const sources = summary.last_sync_source ? summary.last_sync_source.split(', ') : [];

   const topLevelAlloc = allocation.filter(r => r.is_top_level);
   const driftingClassNames = topLevelAlloc
      .filter(r => r.status !== 'within_range')
      .map(r => r.asset_class.split(' ')[0])
      .join(' · ');

   const buildAllocationMarkdown = () => {
      const filterLabel = includeNonRebalanceable ? 'All assets' : 'Rebalanceable assets only';
      const lines: string[] = [
         `## Portfolio Allocation: Current vs Target`,
         `*As of ${summary?.last_sync_date ?? new Date().toISOString().slice(0, 10)} | ${filterLabel} | Tolerance ±${allocation[0]?.tolerance_pct ?? 5}%*`,
         ``,
         `| Asset Class | Current % | Target % | Drift | Status |`,
         `|---|---|---|---|---|`,
      ];
      for (const row of allocation) {
         const name = row.is_top_level ? `**${row.asset_class}**` : `└ ${row.asset_class}`;
         const drift = (row.drift_pct >= 0 ? '+' : '') + row.drift_pct.toFixed(1) + '%';
         const status = row.status === 'over' ? 'OVER ▲' : row.status === 'under' ? 'UNDER ▼' : 'ok';
         lines.push(`| ${name} | ${row.current_pct.toFixed(1)}% | ${row.target_pct.toFixed(1)}% | ${drift} | ${status} |`);
      }
      lines.push(``, `*Total Net Worth: ¥${summary?.total_net_worth.toLocaleString()} CNY | Drift Index: ${summary?.drift_index.toFixed(2)}%*`);
      return lines.join('\n');
   };

   const handleCopyMarkdown = () => {
      navigator.clipboard.writeText(buildAllocationMarkdown()).then(() => {
         setCopied(true);
         setTimeout(() => setCopied(false), 2000);
      });
   };



   return (
      <div data-testid="compass-page" className="min-h-screen pb-16 bg-gray-50 dark:bg-background-dark">
         {/* HEADER */}
         <header className="sticky top-0 z-10 bg-gray-50/85 dark:bg-background-dark/85 backdrop-blur border-b border-slate-200 dark:border-border-dark px-8 py-4 flex items-end justify-between gap-5">
            <div>
               <div className="text-[10px] text-slate-500 uppercase tracking-widest mb-1 font-semibold">{t('compass.breadcrumb')}</div>
               <h1 className="text-[22px] font-bold tracking-tight leading-tight">{t('compass.pageTitle')}</h1>
               <div className="text-xs text-slate-500 mt-1 font-mono">{t('compass.portfolioLine', { time: new Date().toLocaleTimeString() })}</div>
            </div>
            <div className="flex items-center gap-2.5">
               <button disabled title={t('compass.comingSoon')} className="inline-flex items-center gap-1.5 px-3.5 py-1.5 rounded-lg text-xs font-semibold border border-slate-200 dark:border-border-dark bg-white dark:bg-card-dark text-slate-700 dark:text-slate-200 hover:bg-slate-50 dark:hover:bg-surface-dark transition-colors shadow-sm disabled:opacity-50 disabled:cursor-not-allowed">
                  <span className="material-symbols-outlined !text-[16px]">download</span>{t('compass.export')}
               </button>
               <a href="/ai-advisor" className="inline-flex items-center gap-1.5 px-3.5 py-1.5 rounded-lg text-xs font-semibold bg-primary hover:bg-primary-hover text-white shadow-[0_4px_12px_-2px_rgba(59,130,246,0.4)] transition-colors border border-transparent">
                  <span className="material-symbols-outlined !text-[16px]">auto_awesome</span>{t('compass.sendToAiAdvisor')}
               </a>
            </div>
         </header>

         <div className="p-5 md:px-8 md:py-5 flex flex-col gap-4">
            
            {/* KPI ROW */}
            <div className="grid grid-cols-1 md:grid-cols-[1.4fr_1fr_1fr_1.6fr] gap-4">
               <div className="bg-white dark:bg-card-dark border border-slate-200 dark:border-border-dark rounded-xl shadow-sm p-3.5 px-4.5 flex flex-col gap-1">
                  <div className="text-[10px] font-bold uppercase tracking-wider text-slate-500">{t('compass.totalNetWorth')}</div>
                  <div className="font-mono font-bold text-[22px] text-slate-900 dark:text-white tabular-nums tracking-tight flex items-center gap-2">
                     <span className="money-value">{formatCurrency(summary.total_net_worth, 'CNY')}</span>
                  </div>
                  <div className="text-[11px] text-slate-500 font-mono">—</div>
               </div>

               <div className="bg-white dark:bg-card-dark border border-slate-200 dark:border-border-dark rounded-xl shadow-sm p-3.5 px-4.5 flex flex-col gap-1">
                  <div className="text-[10px] font-bold uppercase tracking-wider text-slate-500">{t('compass.currentDriftIndex')}</div>
                  <div className={`font-mono font-bold text-[22px] tabular-nums tracking-tight flex items-center gap-2 ${driftColor}`}>
                     {formatPercent(summary.drift_index)} <span className={`material-symbols-outlined !text-[18px] ${driftIconColor}`}>{driftIcon}</span>
                  </div>
                  <div className="text-[11px] text-slate-500 font-mono">{t('compass.thresholdLine', { status: summary.drift_index > 5 ? t('compass.breached') : t('compass.ok') })}</div>
               </div>

               <div className="bg-white dark:bg-card-dark border border-slate-200 dark:border-border-dark rounded-xl shadow-sm p-3.5 px-4.5 flex flex-col gap-1">
                  <div className="text-[10px] font-bold uppercase tracking-wider text-slate-500">{t('compass.classesInDrift')}</div>
                  <div className="font-mono font-bold text-[22px] text-slate-900 dark:text-white tabular-nums tracking-tight flex items-center gap-1.5">
                     <span className={summary.classes_in_drift > 0 ? "text-red-500" : "text-green-500"}>{summary.classes_in_drift}</span>
                     <span className="text-slate-400 font-medium mx-0.5">/</span>
                     <span className="text-slate-500 font-medium">{summary.total_classes}</span>
                  </div>
                  <div className="text-[11px] text-slate-500 font-mono truncate">{driftingClassNames || t('compass.allWithinRange')}</div>
               </div>

               <div className="bg-white dark:bg-card-dark border border-slate-200 dark:border-border-dark rounded-xl shadow-sm p-3.5 px-4.5 flex flex-col gap-1 justify-center">
                  <div className="text-[10px] font-bold uppercase tracking-wider text-slate-500">{t('compass.lastSync')}</div>
                  <div className="font-mono font-semibold text-[16px] text-slate-900 dark:text-white tabular-nums tracking-tight mb-1">
                     {summary.last_sync_date}
                  </div>
                  <div className="flex flex-wrap gap-1 mt-0.5 font-mono text-[10px]">
                     {sources.map(src => (
                        <span key={src} className="px-1.5 py-0.5 rounded-[3px] bg-slate-100 dark:bg-surface-dark text-slate-500">{src}</span>
                     ))}
                  </div>
               </div>
            </div>

            {/* HIERARCHICAL ASSET CLASS TABLE */}
            <section className="bg-white dark:bg-card-dark border border-slate-200 dark:border-border-dark rounded-xl shadow-sm overflow-hidden">
               <div className="flex items-center justify-between px-5 py-3.5 border-b border-slate-200 dark:border-border-dark bg-slate-50/50 dark:bg-surface-dark/30">
                  <div className="text-[13px] font-bold text-slate-900 dark:text-white flex items-center gap-2">
                     <span className="material-symbols-outlined !text-[16px] text-primary">grid_view</span>
                     {t('compass.assetClassAllocation')}
                  </div>
                  <div className="flex items-center gap-3.5">
                     <div className="flex items-center gap-3.5 text-[10px] text-slate-500 font-mono uppercase tracking-widest font-medium">
                        <span className="inline-flex items-center gap-1.5"><span className="w-1.5 h-1.5 rounded-full bg-red-500"></span>{t('compass.driftOverTolerance')}</span>
                        <span className="inline-flex items-center gap-1.5"><span className="w-1.5 h-1.5 rounded-full bg-green-500"></span>{t('compass.withinRange')}</span>
                     </div>
                     <label
                        className="inline-flex items-center gap-2 cursor-pointer select-none"
                        title={t('compass.pendingTradesTitle')}
                     >
                        <input
                           type="checkbox"
                           checked={includePending}
                           onChange={e => setIncludePending(e.target.checked)}
                           className="sr-only"
                        />
                        <span
                           data-testid="pending-toggle"
                           aria-checked={includePending}
                           role="switch"
                           className={`relative inline-flex w-8 h-4 rounded-full transition-colors ${includePending ? 'bg-amber-500' : 'bg-slate-300 dark:bg-slate-600'}`}
                        >
                           <span className={`absolute top-0.5 left-0.5 w-3 h-3 rounded-full bg-white shadow transition-transform ${includePending ? 'translate-x-4' : 'translate-x-0'}`} />
                        </span>
                        <span className="text-[11px] font-medium text-slate-600 dark:text-slate-300 whitespace-nowrap">
                           {t('compass.includePendingTrades')} <span className="text-[10px] font-mono text-slate-400">{t('compass.includePendingTradesCn')}</span>
                        </span>
                     </label>
                  </div>
               </div>

               {/* Provisional banner — shown only when include_pending is on */}
               {includePending && allocationMeta && (
                  <div
                     data-testid="provisional-banner"
                     className="flex items-center gap-2.5 px-5 py-2.5 bg-amber-50 dark:bg-amber-900/20 border-b border-amber-200 dark:border-amber-700 text-[11px] text-amber-800 dark:text-amber-300"
                  >
                     <span className="material-symbols-outlined !text-[14px] text-amber-500">warning</span>
                     <span className="font-mono text-[10px] font-bold uppercase tracking-widest text-amber-600 dark:text-amber-400">{t('compass.provisional')}</span>
                     <span className="text-amber-700 dark:text-amber-300">
                        <Trans
                           t={t}
                           i18nKey={allocationMeta.pending_trade_count === 1 ? 'compass.provisionalBannerOne' : 'compass.provisionalBannerOther'}
                           values={{ count: allocationMeta.pending_trade_count }}
                           components={{ strong: <strong data-testid="pending-trade-count" /> }}
                        />
                     </span>
                  </div>
               )}

               <div className="flex items-center gap-2.5 px-5 py-2.5 bg-blue-50/30 dark:bg-blue-900/10 border-b border-slate-200 dark:border-border-dark text-[11px] text-slate-700 dark:text-slate-300">
                  <span className="material-symbols-outlined !text-[14px] text-primary">auto_awesome</span>
                  <span className="font-mono text-[10px] font-bold uppercase tracking-widest text-primary">{t('compass.aiAdvisor')}</span>
                  <span className="flex-1 text-slate-500 italic">
                     {t(summary.classes_in_drift === 1 ? 'compass.classesOutsideToleranceOne' : 'compass.classesOutsideToleranceOther', { count: summary.classes_in_drift })}
                  </span>
                  <a href="/ai-advisor" className="text-primary font-semibold no-underline hover:underline inline-flex items-center gap-1 ml-auto">
                     {t('compass.openInAiAdvisor')}<span className="material-symbols-outlined !text-[14px]">arrow_forward</span>
                  </a>
               </div>

               <div className="overflow-x-auto">
                  <table className="w-full border-collapse">
                     <thead>
                        <tr>
                           <th className="text-left text-[10px] font-bold uppercase tracking-wider text-slate-500 px-5 py-2.5 border-b border-slate-200 dark:border-border-dark whitespace-nowrap">{t('compass.columns.assetClass')}</th>
                           <th className="text-right text-[10px] font-bold uppercase tracking-wider text-slate-500 px-5 py-2.5 border-b border-slate-200 dark:border-border-dark whitespace-nowrap">{t('compass.columns.currentValue')}</th>
                           <th className="text-right text-[10px] font-bold uppercase tracking-wider text-slate-500 px-5 py-2.5 border-b border-slate-200 dark:border-border-dark whitespace-nowrap">{t('compass.columns.currentPct')}</th>
                           {includePending && (
                              <th className="text-right text-[10px] font-bold uppercase tracking-wider text-amber-600 dark:text-amber-400 px-5 py-2.5 border-b border-slate-200 dark:border-border-dark whitespace-nowrap">
                                 {t('compass.columns.provisionalPct')} <span className="text-[9px] text-amber-400">{t('compass.columns.pendingSuffix')}</span>
                              </th>
                           )}
                           {includePending && (
                              <th className="text-right text-[10px] font-bold uppercase tracking-wider text-amber-600 dark:text-amber-400 px-5 py-2.5 border-b border-slate-200 dark:border-border-dark whitespace-nowrap">
                                 {t('compass.columns.deltaCny')}
                              </th>
                           )}
                           <th className="text-right text-[10px] font-bold uppercase tracking-wider text-slate-500 px-5 py-2.5 border-b border-slate-200 dark:border-border-dark whitespace-nowrap">{t('compass.columns.targetPct')}</th>
                           <th className="text-right text-[10px] font-bold uppercase tracking-wider text-slate-500 px-5 py-2.5 border-b border-slate-200 dark:border-border-dark whitespace-nowrap min-w-[170px]">{t('compass.columns.drift')}</th>
                           <th className="text-center text-[10px] font-bold uppercase tracking-wider text-slate-500 px-5 py-2.5 border-b border-slate-200 dark:border-border-dark whitespace-nowrap w-16">{t('compass.columns.status')}</th>
                        </tr>
                     </thead>
                     <tbody>
                        {allocation.map((row, i) => {
                           const isTop = row.is_top_level;
                           const { statusIcon, statusColor, driftValColor, bgRow } = getAllocationRowStyle(row);
                           const driftStr = formatPercent(row.drift_pct, true);

                           const hasProvisional = includePending && row.provisional_pct != null;
                           const deltaCNY = row.provisional_delta_cny ?? null;
                           const deltaColor = getDeltaColor(deltaCNY);

                           return (
                              <tr key={i} className={`border-b border-slate-200/60 dark:border-border-dark last:border-0 hover:bg-slate-50 dark:hover:bg-surface-dark transition-colors ${bgRow}`}>
                                 <td className={`px-5 py-2 text-xs ${isTop ? 'font-bold text-slate-900 dark:text-white' : 'text-slate-700 dark:text-slate-300 pl-11'}`}>
                                    {!isTop && <span className="font-mono text-slate-400 mr-1.5">└─</span>}
                                    {row.asset_class}
                                 </td>
                                 <td className={`px-5 py-2 text-right font-mono text-xs tabular-nums ${isTop ? 'font-semibold text-slate-900 dark:text-white' : 'text-slate-700 dark:text-slate-300'}`}>
                                    <span className="money-value">{formatCurrency(row.current_value, row.currency)}</span>
                                 </td>
                                 <td className={`px-5 py-2 text-right font-mono text-xs tabular-nums ${isTop ? 'font-semibold text-slate-900 dark:text-white' : 'text-slate-700 dark:text-slate-300'}`}>
                                    {row.current_pct.toFixed(2)}%
                                 </td>
                                 {includePending && (
                                    <td className="px-5 py-2 text-right font-mono text-xs tabular-nums">
                                       {hasProvisional ? (
                                          <span
                                             data-testid={`provisional-pct-${row.asset_class}`}
                                             className="inline-flex items-center gap-1 text-amber-700 dark:text-amber-400 font-semibold"
                                          >
                                             {row.provisional_pct!.toFixed(2)}%
                                             <span className="text-[9px] font-mono text-amber-500 bg-amber-100 dark:bg-amber-900/40 px-1 rounded">{t('compass.est')}</span>
                                          </span>
                                       ) : (
                                          <span className="text-slate-400">—</span>
                                       )}
                                    </td>
                                 )}
                                 {includePending && (
                                    <td className="px-5 py-2 text-right font-mono text-xs tabular-nums">
                                       {deltaCNY != null ? (
                                          <span className={`font-medium ${deltaColor}`}>
                                             {deltaCNY > 0 ? '+' : ''}{formatCurrency(deltaCNY, 'CNY')}
                                          </span>
                                       ) : (
                                          <span className="text-slate-400">—</span>
                                       )}
                                    </td>
                                 )}
                                 <td className={`px-5 py-2 text-right font-mono text-xs tabular-nums ${isTop ? 'font-semibold text-slate-900 dark:text-white' : 'text-slate-700 dark:text-slate-300'}`}>
                                    {row.target_pct.toFixed(2)}%
                                 </td>
                                 <td className="px-5 py-2 text-right font-mono text-xs tabular-nums">
                                    <div className="inline-flex items-center gap-2.5 justify-end w-full">
                                       <DriftBar drift={row.drift_pct} tolerance={2.5} />
                                       <span className={`min-w-[48px] text-right font-medium ${driftValColor}`}>{driftStr}</span>
                                    </div>
                                 </td>
                                 <td className="px-5 py-2 text-center">
                                    <span className={`material-symbols-outlined !text-[16px] inline-flex items-center justify-center size-[22px] ${statusColor}`}>
                                       {statusIcon}
                                    </span>
                                 </td>
                              </tr>
                           );
                        })}
                     </tbody>
                  </table>
               </div>
            </section>

            {/* ALLOCATION LENS */}
            <section className="bg-white dark:bg-card-dark border border-slate-200 dark:border-border-dark rounded-xl shadow-sm overflow-hidden">
               <div className="flex items-center justify-between px-5 py-3.5 border-b border-slate-200 dark:border-border-dark bg-slate-50/50 dark:bg-surface-dark/30">
                  <div className="text-[13px] font-bold text-slate-900 dark:text-white flex items-center gap-2">
                     <span className="material-symbols-outlined !text-[16px] text-primary">tune</span>
                     {t('compass.allocationLens')}
                  </div>
                  <div className="flex items-center gap-3.5 text-[10px] text-slate-500 font-mono uppercase tracking-widest font-medium">
                     <span className="inline-flex items-center gap-1.5"><span className="w-1.5 h-1.5 rounded-full bg-green-500"></span>{t('compass.withinTolerance')}</span>
                     <span className="inline-flex items-center gap-1.5"><span className="w-1.5 h-1.5 rounded-full bg-amber-500"></span>{t('compass.overTarget')}</span>
                     <span className="inline-flex items-center gap-1.5"><span className="w-1.5 h-1.5 rounded-full bg-red-500"></span>{t('compass.underTarget')}</span>
                  </div>
               </div>

               <div className="grid grid-cols-1 md:grid-cols-[1.1fr_1fr]">
                  {/* LEFT: Two side-by-side donuts */}
                  <div className="p-5 flex flex-col border-b md:border-b-0 md:border-r border-slate-200 dark:border-border-dark">
                     <div className="flex items-center justify-between mb-3">
                        <span className="text-[11px] font-bold uppercase tracking-wider text-slate-500">{t('compass.currentVsTargetMix')}</span>
                        <span className="font-mono text-[10px] text-slate-400 font-medium">{t('compass.pctOfNetWorth')}</span>
                     </div>

                     <div className="grid grid-cols-2 gap-2 flex-1">
                        {/* Current donut */}
                        <div className="flex flex-col items-center gap-1">
                           <span className="text-[10px] font-mono font-semibold text-slate-600 dark:text-slate-300 uppercase tracking-widest">{t('compass.current')}</span>
                           <div className="relative w-full" style={{ height: 150 }}>
                              <ResponsiveContainer width="100%" height="100%">
                                 <PieChart>
                                    <Pie
                                       data={topLevelAlloc}
                                       dataKey="current_pct"
                                       nameKey="asset_class"
                                       cx="50%" cy="50%"
                                       innerRadius="42%" outerRadius="72%"
                                       paddingAngle={2}
                                       startAngle={90} endAngle={-270}
                                       strokeWidth={0}
                                    >
                                       {topLevelAlloc.map((r, i) => (
                                          <Cell key={i} fill={getClassHex(r.asset_class)} />
                                       ))}
                                    </Pie>
                                    <Tooltip
                                       formatter={(v: number, n: string) => [`${v.toFixed(1)}%`, (n as string).split(' ')[0]]}
                                       contentStyle={{ fontSize: 11, fontFamily: 'monospace', border: '1px solid #e2e8f0', borderRadius: 8, padding: '4px 10px' }}
                                    />
                                 </PieChart>
                              </ResponsiveContainer>
                              <div className="absolute inset-0 flex flex-col items-center justify-center pointer-events-none">
                                 <span className="text-[9px] font-mono text-slate-400 uppercase tracking-widest leading-tight">{t('compass.now')}</span>
                              </div>
                           </div>
                           <div className="flex flex-col gap-0.5 w-full px-1">
                              {topLevelAlloc.map((r, i) => (
                                 <div key={i} className="flex items-center justify-between text-[10px] font-mono">
                                    <span className="flex items-center gap-1 text-slate-500 uppercase tracking-wider">
                                       <span className="w-1.5 h-1.5 rounded-[2px] flex-shrink-0" style={{ backgroundColor: getClassHex(r.asset_class) }}></span>
                                       {r.asset_class.split(' ')[0]}
                                    </span>
                                    <span className="font-semibold text-slate-800 dark:text-slate-200 tabular-nums">{r.current_pct.toFixed(1)}%</span>
                                 </div>
                              ))}
                           </div>
                        </div>

                        {/* Target donut */}
                        <div className="flex flex-col items-center gap-1">
                           <span className="text-[10px] font-mono font-semibold text-slate-600 dark:text-slate-300 uppercase tracking-widest">{t('compass.target')}</span>
                           <div className="relative w-full" style={{ height: 150 }}>
                              <ResponsiveContainer width="100%" height="100%">
                                 <PieChart>
                                    <Pie
                                       data={topLevelAlloc}
                                       dataKey="target_pct"
                                       nameKey="asset_class"
                                       cx="50%" cy="50%"
                                       innerRadius="42%" outerRadius="72%"
                                       paddingAngle={2}
                                       startAngle={90} endAngle={-270}
                                       strokeWidth={0}
                                    >
                                       {topLevelAlloc.map((r, i) => (
                                          <Cell key={i} fill={getClassHex(r.asset_class)} />
                                       ))}
                                    </Pie>
                                    <Tooltip
                                       formatter={(v: number, n: string) => [`${v.toFixed(1)}%`, (n as string).split(' ')[0]]}
                                       contentStyle={{ fontSize: 11, fontFamily: 'monospace', border: '1px solid #e2e8f0', borderRadius: 8, padding: '4px 10px' }}
                                    />
                                 </PieChart>
                              </ResponsiveContainer>
                              <div className="absolute inset-0 flex flex-col items-center justify-center pointer-events-none">
                                 <span className="text-[9px] font-mono text-slate-400 uppercase tracking-widest leading-tight">{t('compass.goal')}</span>
                              </div>
                           </div>
                           <div className="flex flex-col gap-0.5 w-full px-1">
                              {topLevelAlloc.map((r, i) => (
                                 <div key={i} className="flex items-center justify-between text-[10px] font-mono">
                                    <span className="flex items-center gap-1 text-slate-500 uppercase tracking-wider">
                                       <span className="w-1.5 h-1.5 rounded-[2px] flex-shrink-0" style={{ backgroundColor: getClassHex(r.asset_class) }}></span>
                                       {r.asset_class.split(' ')[0]}
                                    </span>
                                    <span className="font-semibold text-slate-800 dark:text-slate-200 tabular-nums">{r.target_pct.toFixed(1)}%</span>
                                 </div>
                              ))}
                           </div>
                        </div>
                     </div>
                  </div>

                  {/* RIGHT: Drift Ladder */}
                  <div className="p-4.5 md:p-5">
                     <div className="flex items-center justify-between mb-3">
                        <span className="text-[11px] font-bold uppercase tracking-wider text-slate-500">{t('compass.driftVsTarget')}</span>
                        <span className="font-mono text-[10px] text-slate-400 font-medium">{t('compass.tolerance25')}</span>
                     </div>

                     <div className="grid grid-cols-[90px_1fr_60px_50px] gap-3 font-mono text-[9px] text-slate-400 tracking-wider mb-1">
                        <span></span>
                        <div className="flex justify-between px-0.5"><span>−10%</span><span>−5%</span><span>0</span><span>+5%</span><span>+10%</span></div>
                        <span className="text-right">{t('compass.driftAxisLabel')}</span>
                        <span></span>
                     </div>

                     <div className="flex flex-col gap-2">
                        {topLevelAlloc.map(r => {
                           const { markerColor, valColor, icon, iconColor } = getLadderStyle(r.status);

                           // Math for ladder marker (assume ±12.5% range for layout matching HTML design)
                           const clamped = Math.max(-12.5, Math.min(12.5, r.drift_pct));
                           const markerPct = 50 + (clamped / 25) * 100;

                           return (
                              <div key={r.asset_class} className="grid grid-cols-[90px_1fr_60px_50px] items-center gap-3 py-1.5 border-b border-dashed border-slate-200 dark:border-border-dark last:border-0">
                                 <span className="text-xs text-slate-700 dark:text-slate-300 font-medium truncate">{r.asset_class.split(' ')[0]}</span>
                                 <div className="relative h-2 bg-slate-100 dark:bg-slate-800 rounded-full">
                                    <div className="absolute top-0 bottom-0 left-[40%] w-[20%] bg-green-500/15 rounded-full"></div>
                                    <div className="absolute top-[-2px] bottom-[-2px] left-1/2 w-px bg-slate-400 dark:bg-slate-500"></div>
                                    <div className={`absolute top-[-3px] bottom-[-3px] w-[3px] rounded-sm ${markerColor}`} style={{ left: `calc(${markerPct}% - 1.5px)` }}></div>
                                 </div>
                                 <span className={`font-mono text-[11px] text-right tabular-nums ${valColor}`}>{formatPercent(r.drift_pct, true)}</span>
                                 <span className={`material-symbols-outlined !text-[14px] text-center ${iconColor}`}>{icon}</span>
                              </div>
                           );
                        })}
                     </div>
                  </div>
               </div>
            </section>

            {/* ALLOCATION SNAPSHOT — copyable markdown for LLM paste */}
            <section className="bg-white dark:bg-card-dark border border-slate-200 dark:border-border-dark rounded-xl shadow-sm overflow-hidden">
               <div className="flex items-center justify-between px-5 py-3.5 border-b border-slate-200 dark:border-border-dark bg-slate-50/50 dark:bg-surface-dark/30">
                  <div className="text-[13px] font-bold text-slate-900 dark:text-white flex items-center gap-2">
                     <span className="material-symbols-outlined !text-[16px] text-primary">content_copy</span>
                     {t('compass.allocationSnapshot')}
                  </div>
                  <button
                     onClick={handleCopyMarkdown}
                     className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-[11px] font-semibold border border-slate-200 dark:border-border-dark bg-white dark:bg-card-dark text-slate-700 dark:text-slate-200 hover:bg-slate-50 dark:hover:bg-surface-dark transition-colors shadow-sm"
                  >
                     <span className="material-symbols-outlined !text-[14px]">{copied ? 'check' : 'content_copy'}</span>
                     {copied ? t('compass.copied') : t('compass.copyMarkdown')}
                  </button>
               </div>

               <div className="p-5">
                  <div className="text-[10px] font-mono text-slate-400 mb-2 uppercase tracking-widest">
                     {t('compass.pasteHint')}
                  </div>
                  <pre className="text-[11px] font-mono text-slate-700 dark:text-slate-300 bg-slate-50 dark:bg-surface-dark border border-slate-200 dark:border-border-dark rounded-lg p-4 overflow-x-auto whitespace-pre leading-relaxed select-all">
                     {buildAllocationMarkdown()}
                  </pre>
               </div>
            </section>

            {/* Footer stamp */}
            <div className="flex items-center gap-3.5 px-0.5 py-1.5 font-mono text-[10px] text-slate-400">
               <span>{t('compass.generated', { time: new Date().toISOString() })}</span>
               <span className="w-1 h-1 rounded-full bg-slate-300 dark:bg-slate-700"></span>
               <span>{t('compass.footerTolerance')}</span>
               <span className="w-1 h-1 rounded-full bg-slate-300 dark:bg-slate-700"></span>
               <span>{t('compass.footerVersion', { version: APP_VERSION_DISPLAY })}</span>
            </div>

         </div>
      </div>
   );
};
