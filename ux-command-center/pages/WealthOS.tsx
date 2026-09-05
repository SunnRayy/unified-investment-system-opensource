import React, { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { useNavigate } from 'react-router-dom';
import { usePortfolioFilter } from '../src/context/usePortfolioFilter';
import { useLanguage } from '../src/context/useLanguage';
import { api, WealthAsset, WealthOSSummary, ExportAPI, manualPnlApi, ManualPnL } from '../src/services/api';
import { formatCNY } from '../src/utils/format';
import { localizedClassName } from '../src/utils/localizedClassName';
import { LogPnlDialog } from '../components/wealthos/LogPnlDialog';

export const WealthOS: React.FC = () => {
   const { t } = useTranslation('portfolio');
   const navigate = useNavigate();
   const { lang } = useLanguage();
   const { includeNonRebalanceable } = usePortfolioFilter();
   const [assets, setAssets] = useState<WealthAsset[]>([]);
   const [nonRebAssets, setNonRebAssets] = useState<WealthAsset[]>([]);
   const [summary, setSummary] = useState<WealthOSSummary | null>(null);
   const [sortConfig, setSortConfig] = useState<{ key: keyof WealthAsset; direction: 'asc' | 'desc' } | null>(null);
   const [loading, setLoading] = useState(true);
   const [error, setError] = useState<string | null>(null);
   // #7: owner-logged P&L, keyed by asset_id. Loaded alongside the table so a row
   // can open its dialog pre-filled without a second round-trip.
   const [manualByAsset, setManualByAsset] = useState<Record<string, ManualPnL>>({});
   const [logging, setLogging] = useState<WealthAsset | null>(null);

   useEffect(() => {
      loadData();
   }, [includeNonRebalanceable]);

   const loadData = async () => {
      setLoading(true);
      setError(null);
      try {
         const results = await Promise.allSettled([
            api.getWealthOSAssets(includeNonRebalanceable),
            api.getWealthOSSummary(includeNonRebalanceable),
            manualPnlApi.listManualPnl()
         ]);
         const assetsResult = results[0];
         const summaryResult = results[1];
         const manualResult = results[2];
         if (assetsResult.status === 'fulfilled') {
            setAssets(assetsResult.value.assets || []);
            setNonRebAssets(assetsResult.value.non_rebalanceable_assets || []);
         } else {
            console.error("getWealthOSAssets failed:", assetsResult.reason);
            setError(t('wealthOS.errors.assetsLoad'));
         }
         if (summaryResult.status === 'fulfilled') {
            setSummary(summaryResult.value);
         }
         // Overrides are an enhancement, not a prerequisite: if this call fails the
         // table still renders, rows just fall back to the "Log P&L" affordance.
         if (manualResult.status === 'fulfilled') {
            setManualByAsset(Object.fromEntries(manualResult.value.map((m) => [m.asset_id, m])));
         } else {
            console.error("listManualPnl failed:", manualResult.reason);
         }
      } catch (e) {
         console.error(e);
         setError(t('wealthOS.errors.load'));
      } finally {
         setLoading(false);
      }
   };

   const formatCurrency = (val: number): string => {
      if (val >= 0) return `+¥${val.toLocaleString()}`;
      return `-¥${Math.abs(val).toLocaleString()}`;
   };

   const formatPnlAmount = (val: number, currency?: string): string => {
      const sign = val >= 0 ? '+' : '-';
      const abs = Math.abs(val).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 });
      if (currency && currency !== 'CNY') return `${sign}${abs}`;
      return `${sign}¥${abs}`;
   };

   const sortAssets = (data: WealthAsset[]) => {
      if (!sortConfig) return data;
      return [...data].sort((a, b) => {
         const aValue = a[sortConfig.key];
         const bValue = b[sortConfig.key];
         if (aValue < bValue) return sortConfig.direction === 'asc' ? -1 : 1;
         if (aValue > bValue) return sortConfig.direction === 'asc' ? 1 : -1;
         return 0;
      });
   };

   const requestSort = (key: keyof WealthAsset) => {
      let direction: 'asc' | 'desc' = 'asc';
      if (sortConfig && sortConfig.key === key && sortConfig.direction === 'asc') {
         direction = 'desc';
      }
      setSortConfig({ key, direction });
   };

   if (loading) return <div className="p-8 text-slate-500">{t('wealthOS.loading')}</div>;
   if (error) return <div className="p-8 text-red-500">{error}</div>;

   return (
      <div data-testid="wealth-page" className="p-8 space-y-8 min-h-screen bg-gray-50 dark:bg-background-dark">
         <div className="flex flex-col md:flex-row md:items-center justify-between gap-4">
            <div className="flex items-center gap-3">
               <h2 className="text-2xl font-bold tracking-tight">{t('wealthOS.title')}</h2>
               <button disabled title={t('wealthOS.comingSoon')} className="text-xs font-bold px-3 py-1 bg-slate-100 dark:bg-surface-dark text-slate-500 rounded opacity-50 cursor-not-allowed">{t('wealthOS.hideTable')}</button>
            </div>
            <div className="flex gap-3">
               <button disabled title={t('wealthOS.comingSoon')} className="flex items-center gap-2 px-4 py-2 border border-slate-200 dark:border-border-dark rounded-lg font-semibold text-sm bg-slate-50 dark:bg-transparent text-slate-400 cursor-not-allowed">
                  <span className="material-symbols-outlined text-sm">filter_list</span> {t('wealthOS.filter')}
               </button>
               <button onClick={() => ExportAPI.downloadAiContext()} className="flex items-center gap-2 px-4 py-2 bg-primary text-white rounded-lg font-semibold text-sm shadow-lg shadow-primary/25 hover:bg-blue-600 transition-colors">
                  <span className="material-symbols-outlined text-sm">download</span> {t('wealthOS.exportAiContext')}
               </button>
            </div>
         </div>

         <div className="grid grid-cols-1 md:grid-cols-3 gap-6">
            <div className="bg-white dark:bg-card-dark p-6 rounded-2xl border border-slate-200 dark:border-border-dark shadow-sm relative overflow-hidden">
               <div className="absolute -right-4 -bottom-4 opacity-5">
                  <span className="material-symbols-outlined text-8xl text-primary">payments</span>
               </div>
               <h3 className="text-[10px] font-bold text-slate-400 dark:text-slate-500 uppercase tracking-widest mb-2">{t('wealthOS.totalLifetimeGain')}</h3>
               <div className={`text-3xl font-bold font-mono ${(summary?.total_lifetime_gain ?? 0) >= 0 ? 'text-emerald-500' : 'text-rose-500'}`}>
                  <span className="money-value">{formatCurrency(summary?.total_lifetime_gain ?? 0)}</span>
               </div>
               <p className="text-xs text-slate-500 dark:text-slate-400 mt-2 font-medium">
                  {summary?.lifetime_gain_pct !== undefined ? t('wealthOS.returnPct', { pct: summary.lifetime_gain_pct.toFixed(2) }) : t('wealthOS.combinedRealized')}
               </p>
            </div>
            <div className="bg-white dark:bg-card-dark p-6 rounded-2xl border border-slate-200 dark:border-border-dark shadow-sm relative overflow-hidden">
               <div className="absolute -right-4 -bottom-4 opacity-5">
                  <span className="material-symbols-outlined text-8xl text-primary">trending_up</span>
               </div>
               <h3 className="text-[10px] font-bold text-slate-400 dark:text-slate-500 uppercase tracking-widest mb-2">{t('wealthOS.annualizedReturn')}</h3>
               <div className="text-3xl font-bold font-mono text-primary">
                  {summary?.annualized_return !== null && summary?.annualized_return !== undefined ? `${summary.annualized_return.toFixed(2)}%` : t('wealthOS.notAvailable')}
               </div>
               <p className="text-xs text-slate-500 dark:text-slate-400 mt-2 font-medium">
                  {summary?.annualized_return === null ? t('wealthOS.requiresFullHistory') : t('wealthOS.overallPerformance')}
               </p>
            </div>
            <div className="bg-white dark:bg-card-dark p-6 rounded-2xl border border-slate-200 dark:border-border-dark shadow-sm relative overflow-hidden">
               <div className="absolute -right-4 -bottom-4 opacity-5">
                  <span className="material-symbols-outlined text-8xl text-primary">inventory_2</span>
               </div>
               <h3 className="text-[10px] font-bold text-slate-400 dark:text-slate-500 uppercase tracking-widest mb-2">{t('wealthOS.activeAssets')}</h3>
               <div className="text-3xl font-bold font-mono">
                  {summary?.active_asset_count ?? 0} / <span className="text-slate-300 dark:text-slate-600">{summary?.total_asset_count ?? 0}</span>
               </div>
               <p className="text-xs text-slate-500 dark:text-slate-400 mt-2 font-medium">{t('wealthOS.currentHoldingDistribution')}</p>
            </div>
         </div>

         <div className="bg-white dark:bg-card-dark border border-slate-200 dark:border-border-dark rounded-xl overflow-hidden shadow-sm">
            <div className="overflow-x-auto">
               <table className="w-full text-left border-collapse">
                  <thead>
                     <tr className="bg-slate-50/50 dark:bg-surface-dark/50 border-b border-slate-200 dark:border-border-dark select-none">
                        <th className="px-6 py-4 text-[10px] font-bold text-slate-400 dark:text-slate-500 uppercase tracking-wider cursor-pointer hover:bg-slate-50 dark:hover:bg-white/[0.02]" onClick={() => requestSort('name')}>
                           {t('wealthOS.columns.assetName')} {sortConfig?.key === 'name' ? (sortConfig.direction === 'asc' ? '↑' : '↓') : ''}
                        </th>
                        <th className="px-6 py-4 text-[10px] font-bold text-slate-400 dark:text-slate-500 uppercase tracking-wider cursor-pointer hover:bg-slate-50 dark:hover:bg-white/[0.02]" onClick={() => requestSort('type')}>
                           {t('wealthOS.columns.assetClass')} {sortConfig?.key === 'type' ? (sortConfig.direction === 'asc' ? '↑' : '↓') : ''}
                        </th>
                        <th className="px-6 py-4 text-[10px] font-bold text-slate-400 dark:text-slate-500 uppercase tracking-wider text-right cursor-pointer hover:bg-slate-50 dark:hover:bg-white/[0.02]" onClick={() => requestSort('period')}>
                           {t('wealthOS.columns.holdingPeriod')} {sortConfig?.key === 'period' ? (sortConfig.direction === 'asc' ? '↑' : '↓') : ''}
                        </th>
                        <th className="px-6 py-4 text-[10px] font-bold text-slate-400 dark:text-slate-500 uppercase tracking-wider text-center cursor-pointer hover:bg-slate-50 dark:hover:bg-white/[0.02]" onClick={() => requestSort('status')}>
                           {sortConfig?.key === 'status' ? (sortConfig.direction === 'asc' ? '↑' : '↓') : ''} {t('wealthOS.columns.status')}
                        </th>
                        <th className="px-6 py-4 text-[10px] font-bold text-slate-400 dark:text-slate-500 uppercase tracking-wider text-right cursor-pointer hover:bg-slate-50 dark:hover:bg-white/[0.02]" onClick={() => requestSort('invested')}>
                           {t('wealthOS.columns.totalInvested')} {sortConfig?.key === 'invested' ? (sortConfig.direction === 'asc' ? '↑' : '↓') : ''}
                        </th>
                        <th className="px-6 py-4 text-[10px] font-bold text-slate-400 dark:text-slate-500 uppercase tracking-wider text-right cursor-pointer hover:bg-slate-50 dark:hover:bg-white/[0.02]" onClick={() => requestSort('cur')}>
                           {t('wealthOS.columns.currentValue')} {sortConfig?.key === 'cur' ? (sortConfig.direction === 'asc' ? '↑' : '↓') : ''}
                        </th>
                        <th className="px-6 py-4 text-[10px] font-bold text-slate-400 dark:text-slate-500 uppercase tracking-wider text-right cursor-pointer hover:bg-slate-50 dark:hover:bg-white/[0.02]" onClick={() => requestSort('pl')}>
                           {t('wealthOS.columns.profitLoss')} {sortConfig?.key === 'pl' ? (sortConfig.direction === 'asc' ? '↑' : '↓') : ''}
                        </th>
                        <th className="px-6 py-4 text-[10px] font-bold text-slate-400 dark:text-slate-500 uppercase tracking-wider text-right cursor-pointer hover:bg-slate-50 dark:hover:bg-white/[0.02]" onClick={() => requestSort('unrealized_current_lots_pct')}>
                           {t('wealthOS.columns.unrealizedCurrentLots')} {sortConfig?.key === 'unrealized_current_lots_pct' ? (sortConfig.direction === 'asc' ? '↑' : '↓') : ''}
                        </th>
                        <th className="px-6 py-4 text-[10px] font-bold text-slate-400 dark:text-slate-500 uppercase tracking-wider text-right cursor-pointer hover:bg-slate-50 dark:hover:bg-white/[0.02]" onClick={() => requestSort('ret')}>
                           {t('wealthOS.columns.returnLifetime')} {sortConfig?.key === 'ret' ? (sortConfig.direction === 'asc' ? '↑' : '↓') : ''}
                        </th>
                     </tr>
                  </thead>
                  <tbody className="divide-y divide-slate-100 dark:divide-border-dark/30">
                     {sortAssets(assets).map((row, i) => {
                        const pnlValue = row.pl_native ?? row.pl;
                        const pnlCurrency = row.pnl_currency ?? 'CNY';
                        return (
                        <tr key={i} className="group hover:bg-slate-50/50 dark:hover:bg-surface-dark/30 transition-colors">
                           <td className="px-6 py-4">
                              <div className="flex items-center gap-2 font-bold text-sm">
                                 {row.name}
                                 {row.open_value_trap_review && (
                                    <button
                                       onClick={() => navigate('/value-trap-reviews')}
                                       className="inline-flex items-center px-1.5 py-0.5 rounded text-[9px] font-bold bg-amber-100 text-amber-700 dark:bg-amber-900/30 dark:text-amber-400 hover:bg-amber-200 dark:hover:bg-amber-800/50 transition-colors shrink-0"
                                       title={t('wealthOS.openValueTrapReview')}
                                    >
                                       {t('wealthOS.reviewDue')}
                                    </button>
                                 )}
                              </div>
                              <div className="text-[10px] text-slate-400 dark:text-slate-500 font-mono">{row.code}</div>
                           </td>
                           <td className="px-6 py-4 text-sm text-slate-600 dark:text-slate-400">{localizedClassName(row.type, row.type_cn, lang)}</td>
                           <td className="px-6 py-4 text-sm text-right font-mono text-slate-500">{row.period}</td>
                           <td className="px-6 py-4 text-center">
                              <span className={`inline-flex px-2 py-1 rounded text-[10px] font-bold ${row.status === 'ACTIVE' ? 'bg-emerald-500/10 text-emerald-500' : 'bg-slate-100 dark:bg-slate-700 text-slate-500'}`}>{row.status}</span>
                           </td>
                           <td className="px-6 py-4 text-sm text-right font-mono text-slate-500">{row.invested != null ? formatCNY(row.invested) : <span className="text-slate-400" title={t('wealthOS.costBasisUnknown')}>—</span>}</td>
                           <td className="px-6 py-4 text-sm text-right font-mono font-medium">{formatCNY(row.cur)}</td>
                           <td className={`px-6 py-4 text-sm text-right font-mono font-bold ${pnlValue == null ? 'text-slate-400' : pnlValue >= 0 ? 'text-emerald-500' : 'text-rose-500'}`}>
                              {/* P&L DISPLAY: Values are in native asset currency (USD for Schwab/RSU).
                                  Backend converts to CNY for totals using today's FX rate (constant-FX method).
                                  Null = balance-only asset whose cost basis is unknown → render "—",
                                  with a "Log P&L" affordance so the owner can supply what the
                                  readers cannot know (#7).
                                  See src/api/routes/performance.py header comment for full explanation. */}
                              {pnlValue != null ? (
                                 <div className="flex items-center justify-end gap-2 whitespace-nowrap">
                                    <span>
                                       <span className="money-value">{formatPnlAmount(pnlValue, pnlCurrency)}</span>
                                       {pnlCurrency !== 'CNY' && <span className="text-xs opacity-60 ml-1">{pnlCurrency}</span>}
                                    </span>
                                    {row.has_manual_data && (() => {
                                       // A logged cost is for the whole position, so a later
                                       // buy/sell silently turns the difference into phantom
                                       // P&L. Flag it amber rather than quietly showing a
                                       // number we know is out of date.
                                       const stale = manualByAsset[row.code]?.value_looks_stale;
                                       return (
                                          <button
                                             onClick={() => setLogging(row)}
                                             className={`inline-flex items-center px-1.5 py-0.5 rounded text-[9px] font-bold transition-colors shrink-0 ${stale
                                                ? 'bg-amber-100 text-amber-700 dark:bg-amber-900/30 dark:text-amber-400 hover:bg-amber-200 dark:hover:bg-amber-800/50'
                                                : 'bg-sky-100 text-sky-700 dark:bg-sky-900/30 dark:text-sky-400 hover:bg-sky-200 dark:hover:bg-sky-800/50'}`}
                                             title={stale
                                                ? t('wealthOS.logPnl.staleTitle')
                                                : t('wealthOS.logPnl.freshTitle')}
                                          >
                                             {stale ? t('wealthOS.logPnl.loggedStale') : t('wealthOS.logPnl.logged')}
                                          </button>
                                       );
                                    })()}
                                 </div>
                              ) : (
                                 <div className="flex items-center justify-end gap-2 whitespace-nowrap">
                                    <span title={t('wealthOS.costBasisUnknown')}>—</span>
                                 </div>
                              )}
                              {/* Offer logging wherever no reader ledger owns this asset's P&L —
                                  NOT merely where it shows "—". Bank wealth and pension holdings
                                  display a real-looking +¥0.00, and inferring the affordance from
                                  an empty-looking figure silently skipped them.

                                  A row with no figure at all keeps the button visible (there is
                                  nothing else to see); a row already showing a number reveals it
                                  on hover/focus, so the capability is everywhere it applies
                                  without putting 16 buttons on the page at rest. */}
                              {row.can_log_manual_pnl && !row.has_manual_data && (
                                 <div className={`flex justify-end mt-1 transition-opacity ${pnlValue != null
                                    ? 'opacity-0 group-hover:opacity-100 focus-within:opacity-100'
                                    : 'opacity-100'}`}>
                                    <button
                                       onClick={() => setLogging(row)}
                                       className="inline-flex items-center px-1.5 py-0.5 rounded text-[9px] font-bold bg-slate-100 text-slate-500 dark:bg-slate-700/50 dark:text-slate-400 hover:bg-primary/10 hover:text-primary transition-colors shrink-0"
                                       title={t('wealthOS.logPnl.noLedgerTitle')}
                                    >
                                       {t('wealthOS.logPnl.logPnl')}
                                    </button>
                                 </div>
                              )}
                           </td>
                           <td className={`px-6 py-4 text-sm text-right font-mono font-bold ${row.unrealized_current_lots_pct == null ? 'text-slate-400' : row.unrealized_current_lots_pct >= 0 ? 'text-emerald-500' : 'text-rose-500'}`}>
                              {row.unrealized_current_lots_pct != null ? `${row.unrealized_current_lots_pct.toFixed(2)}%` : '—'}
                           </td>
                           <td className={`px-6 py-4 text-sm text-right font-mono font-bold ${row.ret == null ? 'text-slate-400' : row.ret >= 0 ? 'text-emerald-500' : 'text-rose-500'}`}>{row.ret != null ? `${row.ret.toFixed(2)}%` : '—'}</td>
                        </tr>
                     )})}
                     {sortAssets(nonRebAssets).map((row, i) => {
                        const pnlValue = row.pl_native ?? row.pl;
                        const pnlCurrency = row.pnl_currency ?? 'CNY';
                        return (
                        <tr key={`nr-${i}`} className="hover:bg-slate-50/50 dark:hover:bg-surface-dark/30 transition-colors opacity-40 grayscale pointer-events-none relative" title={t('wealthOS.illiquidTitle')}>
                           <td className="px-6 py-4">
                              <div className="font-bold text-sm">{row.name}</div>
                              <div className="text-[10px] text-slate-400 dark:text-slate-500 font-mono">{row.code}</div>
                           </td>
                           <td className="px-6 py-4 text-sm text-slate-600 dark:text-slate-400">{localizedClassName(row.type, row.type_cn, lang)}</td>
                           <td className="px-6 py-4 text-sm text-right font-mono text-slate-500">{row.period}</td>
                           <td className="px-6 py-4 text-center">
                              <span className={`inline-flex px-2 py-1 rounded text-[10px] font-bold ${row.status === 'ACTIVE' ? 'bg-slate-500/10 text-slate-500' : 'bg-slate-100 dark:bg-slate-700 text-slate-500'}`}>{row.status}</span>
                           </td>
                           <td className="px-6 py-4 text-sm text-right font-mono text-slate-500">{row.invested != null ? formatCNY(row.invested) : <span className="text-slate-400">—</span>}</td>
                           <td className="px-6 py-4 text-sm text-right font-mono font-medium text-slate-400">{formatCNY(row.cur)}</td>
                           <td className="px-6 py-4 text-sm text-right font-mono font-bold text-slate-400">
                              {pnlValue != null ? (
                                 <>
                                    <span className="money-value">{formatPnlAmount(pnlValue, pnlCurrency)}</span>
                                    {pnlCurrency !== 'CNY' && <span className="text-xs opacity-60 ml-1">{pnlCurrency}</span>}
                                 </>
                              ) : '—'}
                           </td>
                           <td className="px-6 py-4 text-sm text-right font-mono font-bold text-slate-400">
                              {row.unrealized_current_lots_pct != null ? `${row.unrealized_current_lots_pct.toFixed(2)}%` : '—'}
                           </td>
                           <td className="px-6 py-4 text-sm text-right font-mono font-bold text-slate-400">{row.ret != null ? `${row.ret.toFixed(2)}%` : '—'}</td>
                        </tr>
                     )})}
                  </tbody>
               </table>
            </div>
         </div>

         {logging && (
            <LogPnlDialog
               assetId={logging.code}
               assetName={logging.name}
               existing={manualByAsset[logging.code] ?? null}
               onClose={() => setLogging(null)}
               onSaved={loadData}
            />
         )}
      </div>
   );
};
