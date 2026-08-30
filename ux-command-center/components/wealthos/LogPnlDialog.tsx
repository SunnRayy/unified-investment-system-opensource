// Owner-logged P&L for one asset (#7, Release 2 / plan §C.4).
// Contract: docs/api-specs/manual-pnl.md
//
// These are the bank-bought assets the readers cannot price — no cost, no
// transactions — so WealthOS shows "—". The owner does know the economics ("I put
// in X, it earned Y"), and this is where they say so.
import React, { useEffect, useState } from 'react';
import { Trans, useTranslation } from 'react-i18next';
import { manualPnlApi, ManualPnL } from '../../src/services/api';

interface Props {
   assetId: string;
   assetName: string;
   /** Existing override, if any — null means "logging for the first time". */
   existing: ManualPnL | null;
   onClose: () => void;
   /** Called after a successful save/clear so the caller can refetch. */
   onSaved: () => void;
}

/** Empty string -> null (absent), otherwise a parsed number. Returns undefined
 *  for text that is not a number at all, so the caller can reject it rather than
 *  silently sending NaN. */
function parseAmount(raw: string): number | null | undefined {
   const trimmed = raw.trim();
   if (trimmed === '') return null;
   const value = Number(trimmed);
   return Number.isFinite(value) ? value : undefined;
}

export const LogPnlDialog: React.FC<Props> = ({ assetId, assetName, existing, onClose, onSaved }) => {
   const { t } = useTranslation('portfolio');
   const [cost, setCost] = useState('');
   const [realized, setRealized] = useState('');
   const [asOf, setAsOf] = useState('');
   const [memo, setMemo] = useState('');
   const [saving, setSaving] = useState(false);
   const [error, setError] = useState<string | null>(null);

   useEffect(() => {
      setCost(existing?.cost_basis_cny != null ? String(existing.cost_basis_cny) : '');
      setRealized(existing?.realized_pnl_cny != null ? String(existing.realized_pnl_cny) : '');
      setAsOf(existing?.as_of_date ?? '');
      setMemo(existing?.memo ?? '');
   }, [existing]);

   const costAffectsUnrealized = existing?.cost_affects_unrealized ?? true;

   const handleSave = async () => {
      const costValue = parseAmount(cost);
      const realizedValue = parseAmount(realized);
      if (costValue === undefined || realizedValue === undefined) {
         setError(t('wealthOS.logPnlDialog.errorNotNumbers'));
         return;
      }
      // Mirrors the API's 400: an override with neither figure is indistinguishable
      // from no override, so it would be a silent no-op. Clearing is a separate action.
      if (costValue === null && realizedValue === null) {
         setError(t('wealthOS.logPnlDialog.errorNeitherFigure'));
         return;
      }
      setSaving(true);
      setError(null);
      try {
         await manualPnlApi.saveManualPnl(assetId, {
            cost_basis_cny: costValue,
            realized_pnl_cny: realizedValue,
            as_of_date: asOf.trim() === '' ? null : asOf.trim(),
            memo: memo.trim() === '' ? null : memo.trim(),
         });
         onSaved();
         onClose();
      } catch (e) {
         setError(e instanceof Error ? e.message : t('wealthOS.logPnlDialog.errorSaveFailed'));
      } finally {
         setSaving(false);
      }
   };

   const handleClear = async () => {
      setSaving(true);
      setError(null);
      try {
         await manualPnlApi.deleteManualPnl(assetId);
         onSaved();
         onClose();
      } catch (e) {
         setError(e instanceof Error ? e.message : t('wealthOS.logPnlDialog.errorClearFailed'));
      } finally {
         setSaving(false);
      }
   };

   return (
      <div
         className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4"
         onClick={onClose}
         data-testid="log-pnl-backdrop"
      >
         <div
            className="w-full max-w-lg bg-white dark:bg-card-dark rounded-2xl border border-slate-200 dark:border-border-dark shadow-xl"
            onClick={(e) => e.stopPropagation()}
            role="dialog"
            aria-label={t('wealthOS.logPnlDialog.ariaLabel')}
         >
            <div className="px-6 py-4 border-b border-slate-200 dark:border-border-dark">
               <h3 className="text-lg font-bold">{existing ? t('wealthOS.logPnlDialog.editTitle') : t('wealthOS.logPnl.logPnl')}</h3>
               <p className="text-xs text-slate-500 dark:text-slate-400 mt-1">
                  {assetName} <span className="font-mono text-slate-400">{assetId}</span>
               </p>
            </div>

            <div className="px-6 py-5 space-y-4">
               <p className="text-xs text-slate-500 dark:text-slate-400 leading-relaxed">
                  <Trans
                     t={t}
                     i18nKey="wealthOS.logPnlDialog.intro"
                     components={{ strong: <strong /> }}
                  />
               </p>

               <div>
                  <label className="block text-[10px] font-bold text-slate-400 uppercase tracking-widest mb-1">
                     {t('wealthOS.logPnlDialog.costLabel')}
                  </label>
                  <input
                     type="text"
                     inputMode="decimal"
                     value={cost}
                     onChange={(e) => setCost(e.target.value)}
                     placeholder={t('wealthOS.logPnlDialog.costPlaceholder')}
                     className="w-full px-3 py-2 rounded-lg border border-slate-200 dark:border-border-dark bg-white dark:bg-surface-dark font-mono text-sm"
                  />
                  <p className="text-[11px] text-slate-400 mt-1">
                     {costAffectsUnrealized
                        ? t('wealthOS.logPnlDialog.unrealizedHint')
                        : t('wealthOS.logPnlDialog.cashEquivalentHint')}
                  </p>
               </div>

               <div>
                  <label className="block text-[10px] font-bold text-slate-400 uppercase tracking-widest mb-1">
                     {t('wealthOS.logPnlDialog.profitLabel')}
                  </label>
                  <input
                     type="text"
                     inputMode="decimal"
                     value={realized}
                     onChange={(e) => setRealized(e.target.value)}
                     placeholder={t('wealthOS.logPnlDialog.profitPlaceholder')}
                     className="w-full px-3 py-2 rounded-lg border border-slate-200 dark:border-border-dark bg-white dark:bg-surface-dark font-mono text-sm"
                  />
                  <p className="text-[11px] text-slate-400 mt-1">
                     {t('wealthOS.logPnlDialog.profitHint')}
                  </p>
               </div>

               <div className="grid grid-cols-2 gap-4">
                  <div>
                     <label className="block text-[10px] font-bold text-slate-400 uppercase tracking-widest mb-1">
                        {t('wealthOS.logPnlDialog.asOfLabel')}
                     </label>
                     <input
                        type="date"
                        value={asOf}
                        onChange={(e) => setAsOf(e.target.value)}
                        className="w-full px-3 py-2 rounded-lg border border-slate-200 dark:border-border-dark bg-white dark:bg-surface-dark text-sm"
                     />
                  </div>
                  <div>
                     <label className="block text-[10px] font-bold text-slate-400 uppercase tracking-widest mb-1">
                        {t('wealthOS.logPnlDialog.noteLabel')}
                     </label>
                     <input
                        type="text"
                        value={memo}
                        onChange={(e) => setMemo(e.target.value)}
                        placeholder={t('wealthOS.logPnlDialog.notePlaceholder')}
                        className="w-full px-3 py-2 rounded-lg border border-slate-200 dark:border-border-dark bg-white dark:bg-surface-dark text-sm"
                     />
                  </div>
               </div>

               {existing?.value_looks_stale && (
                  <div className="text-xs px-3 py-2 rounded-lg bg-amber-50 dark:bg-amber-900/20 text-amber-700 dark:text-amber-400 leading-relaxed">
                     <Trans
                        t={t}
                        i18nKey="wealthOS.logPnlDialog.staleWarning"
                        values={{
                           marketValueAtLog: existing.market_value_at_log?.toLocaleString(),
                           currentMarketValue: existing.current_market_value?.toLocaleString(),
                           sign: existing.value_move_pct != null && existing.value_move_pct > 0 ? '+' : '',
                           pct: existing.value_move_pct,
                        }}
                        components={{ strong1: <strong />, strong2: <strong /> }}
                     />
                  </div>
               )}

               {existing?.superseded && (
                  <div className="text-xs px-3 py-2 rounded-lg bg-amber-50 dark:bg-amber-900/20 text-amber-700 dark:text-amber-400">
                     {t('wealthOS.logPnlDialog.supersededWarning')}
                  </div>
               )}

               {error && (
                  <div className="text-xs px-3 py-2 rounded-lg bg-rose-50 dark:bg-rose-900/20 text-rose-600 dark:text-rose-400">
                     {error}
                  </div>
               )}
            </div>

            <div className="px-6 py-4 border-t border-slate-200 dark:border-border-dark flex items-center justify-between">
               <div>
                  {existing && (
                     <button
                        onClick={handleClear}
                        disabled={saving}
                        className="px-3 py-2 text-sm font-semibold text-rose-600 hover:bg-rose-50 dark:hover:bg-rose-900/20 rounded-lg disabled:opacity-50"
                     >
                        {t('wealthOS.logPnlDialog.clear')}
                     </button>
                  )}
               </div>
               <div className="flex gap-2">
                  <button
                     onClick={onClose}
                     disabled={saving}
                     className="px-4 py-2 text-sm font-semibold border border-slate-200 dark:border-border-dark rounded-lg disabled:opacity-50"
                  >
                     {t('wealthOS.logPnlDialog.cancel')}
                  </button>
                  <button
                     onClick={handleSave}
                     disabled={saving}
                     className="px-4 py-2 text-sm font-semibold bg-primary text-white rounded-lg shadow-lg shadow-primary/25 hover:bg-blue-600 transition-colors disabled:opacity-50"
                  >
                     {saving ? t('wealthOS.logPnlDialog.saving') : t('wealthOS.logPnlDialog.save')}
                  </button>
               </div>
            </div>
         </div>
      </div>
   );
};
