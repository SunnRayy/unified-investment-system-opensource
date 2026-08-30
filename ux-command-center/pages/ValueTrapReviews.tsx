import React, { useCallback, useEffect, useState } from 'react';
import { Trans, useTranslation } from 'react-i18next';
import type { TFunction } from 'i18next';
import { useNavigate } from 'react-router-dom';
import { api } from '../src/services/api';
import type {
    ValueTrapReview, ValueTrapRuling, ValueTrapScanSummary, ValueTrapStatus,
    ValueTrapContext, ValueTrapMemoEntry, ValueTrapLinkageState, DeferredAssetEntry,
    LotDetail, ValueTrapPendingCount,
} from '../src/services/api/types';

// ── Design tokens ─────────────────────────────────────────────────────────────
const CARD = 'rounded-xl border border-slate-200 dark:border-border-dark bg-white dark:bg-card-dark shadow-sm overflow-hidden';
const INPUT_CLS = 'w-full text-sm px-3 py-2 rounded-lg border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-800/50 text-slate-800 dark:text-slate-200 focus:outline-none focus:ring-2 focus:ring-blue-500/30 transition-colors';
const EYEBROW = 'text-[9.5px] font-bold uppercase tracking-wider text-slate-500 dark:text-slate-400';

type RulingValue = ValueTrapRuling | '';
// Deferred is a UI-only tab (data comes from scan summary, not a separate API filter)
type TabFilter = ValueTrapStatus | 'all' | 'deferred';
type ScanTile = 'evaluated' | 'triggered' | 'deferred' | 'exempt' | 'excluded';

const ESCALATION_STEP_PP = 10;

const getRulingOptions = (t: TFunction): { value: ValueTrapRuling; label: string }[] => [
    { value: 'hold_with_thesis', label: t('valueTrapReviews.rulingOptions.hold') },
    { value: 'trim', label: t('valueTrapReviews.rulingOptions.trim') },                       // Deviation: mock omits Trim; contract wins
    { value: 'liquidate', label: t('valueTrapReviews.rulingOptions.liquidate') },
];

interface RulingFormState {
    thesis_restated: string;
    falsification_check: string;
    would_buy_today: string;
    ruling: RulingValue;
    adversarial_ack: boolean;
    next_review_date: string;
    linkage_ack: boolean;
}

const EMPTY_FORM: RulingFormState = {
    thesis_restated: '',
    falsification_check: '',
    would_buy_today: '',
    ruling: '',
    adversarial_ack: false,
    next_review_date: '',
    linkage_ack: false,
};

// ── Helpers ───────────────────────────────────────────────────────────────────

function pct(v: number | null | undefined): string {
    if (v == null) return '—';
    return `${v.toFixed(1)}%`;
}

function fmt(v: number | null | undefined, currency = 'CNY'): string {
    if (v == null) return '—';
    return currency === 'CNY' ? `¥${v.toLocaleString()}` : `${v.toLocaleString()} ${currency}`;
}

function daysStale(priceDate: string | null, today: string): number | null {
    if (!priceDate) return null;
    const ms = new Date(today).getTime() - new Date(priceDate).getTime();
    return Math.floor(ms / (1000 * 60 * 60 * 24));
}

/** Deferred-panel staleness chip color — pure CSS class selection, not display text.
 *  Pulled out of the JSX child-expression tree so the i18n ratchet's rule-3 scan
 *  (which cannot distinguish a className ternary from a display-string ternary once
 *  both sit inside a .map() callback) doesn't flag these Tailwind class lists. */
function staleChipStyle(staleDays: number | null): string {
    return staleDays != null && staleDays >= 30
        ? 'bg-rose-100 dark:bg-rose-900/30 text-rose-800 dark:text-rose-300'
        : 'bg-amber-100 dark:bg-amber-900/30 text-amber-800 dark:text-amber-400';
}

/** Derive the first unmet save gate for display. */
function getSaveExplain(
    form: RulingFormState,
    isUnresolved: boolean,
    isPriceFresh: boolean,
    t: TFunction,
): { text: string; blocked: boolean } {
    if (!isPriceFresh) return { text: t('valueTrapReviews.saveExplain.stalePrice'), blocked: true };
    if (!form.ruling) return { text: t('valueTrapReviews.saveExplain.selectRuling'), blocked: false };
    if (form.ruling === 'hold_with_thesis' && !form.next_review_date)
        return { text: t('valueTrapReviews.saveExplain.holdNeedsDate'), blocked: true };
    if (form.ruling === 'liquidate' && !form.adversarial_ack)
        return { text: t('valueTrapReviews.saveExplain.liquidateNeedsAck'), blocked: true };
    if (isUnresolved && !form.linkage_ack)
        return { text: t('valueTrapReviews.saveExplain.linkageAckRequired'), blocked: true };
    return { text: '', blocked: false };
}

// ── Escalation Ladder ─────────────────────────────────────────────────────────

const EscalationLadder: React.FC<{ review: ValueTrapReview }> = ({ review }) => {
    const { t: translate } = useTranslation('reports');
    const t = review.trigger_threshold_pct;
    const rungs = [t, t - ESCALATION_STEP_PP, t - 2 * ESCALATION_STEP_PP];
    const loss = review.unrealized_return_pct;

    // Rungs that the current loss has passed (loss <= rung value since both are negative)
    const crossed = rungs.filter(r => loss != null && loss <= r);
    // Deepest = most negative (Math.min on negative numbers)
    const deepest = crossed.length > 0 ? Math.min(...crossed) : null;
    const nextUncrossed = rungs.find(r => !crossed.includes(r));

    // For ruled reviews with next_trigger set, use that; else use first uncrossed rung
    const reArmsAt =
        review.status !== 'open' && review.next_trigger_threshold_pct != null
            ? review.next_trigger_threshold_pct
            : nextUncrossed;
    const noteText = reArmsAt != null ? translate('valueTrapReviews.reArmsAt', { value: pct(reArmsAt) }) : '';

    return (
        <div className="flex items-center gap-0.5">
            {rungs.map((r) => {
                const isCurrent = r === deepest;
                const isCrossed = crossed.includes(r) && !isCurrent;
                if (isCurrent) {
                    return (
                        <span
                            key={r}
                            className="inline-flex items-center justify-center w-8 h-5 rounded text-[9.5px] font-mono font-bold bg-red-500 text-white"
                            style={{ boxShadow: '0 0 0 2px rgb(239 68 68 / 0.25)' }}
                        >
                            {r}
                        </span>
                    );
                }
                if (isCrossed) {
                    return (
                        <span
                            key={r}
                            className="inline-flex items-center justify-center w-8 h-5 rounded text-[9.5px] font-mono font-bold bg-rose-100 dark:bg-rose-900/30 text-rose-800 dark:text-rose-300"
                        >
                            {r}
                        </span>
                    );
                }
                return (
                    <span
                        key={r}
                        className="inline-flex items-center justify-center w-8 h-5 rounded text-[9.5px] font-mono font-bold border border-dashed border-slate-300 dark:border-slate-600 text-slate-400 dark:text-slate-500 bg-transparent"
                    >
                        {r}
                    </span>
                );
            })}
            {noteText && (
                <span className="ml-2 text-[9.5px] font-mono text-slate-400 dark:text-slate-500">
                    {noteText}
                </span>
            )}
        </div>
    );
};

// ── Stale Banner ──────────────────────────────────────────────────────────────

const StaleBanner: React.FC<{ priceDate: string }> = ({ priceDate }) => {
    const { t } = useTranslation('reports');
    return (
    <div className="flex items-center gap-2.5 px-3.5 py-2.5 mb-3.5 bg-rose-100 dark:bg-rose-900/30 border border-rose-400/40 dark:border-rose-700/50 rounded-lg text-xs text-rose-800 dark:text-rose-300 font-medium">
        <svg className="w-4 h-4 flex-shrink-0" fill="currentColor" viewBox="0 0 20 20" aria-hidden>
            <path fillRule="evenodd" d="M8.485 2.495c.673-1.167 2.357-1.167 3.03 0l6.28 10.875c.673 1.167-.17 2.625-1.516 2.625H3.72c-1.347 0-2.189-1.458-1.515-2.625L8.485 2.495zM10 5a.75.75 0 01.75.75v3.5a.75.75 0 01-1.5 0v-3.5A.75.75 0 0110 5zm0 9a1 1 0 100-2 1 1 0 000 2z" clipRule="evenodd" />
        </svg>
        <Trans
            t={t}
            i18nKey="valueTrapReviews.staleBanner"
            values={{ priceDate }}
            components={{ b: <b className="font-mono font-bold" /> }}
        />
    </div>
    );
};

// ── Facts Strip ───────────────────────────────────────────────────────────────

const FactsStrip: React.FC<{ context: ValueTrapContext }> = ({ context }) => {
    const { t } = useTranslation('reports');
    const pos = context.position;
    const loss = context.loss;
    const isStale = pos?.freshness?.fresh === false;

    return (
        <div className="grid grid-cols-6 gap-6 px-4 py-3.5 mb-3.5 bg-slate-50 dark:bg-slate-900/30 border border-slate-200 dark:border-slate-700/50 rounded-[10px]">
            {/* 1. Current Value */}
            <div className="flex flex-col gap-0.5">
                <div className={EYEBROW}>{t('valueTrapReviews.factsStrip.currentValue')}</div>
                <div className="font-mono text-[13px] font-bold text-slate-900 dark:text-slate-100 mt-0.5">
                    {pos ? fmt(pos.market_value, pos.currency) : '—'}
                </div>
                <div className="text-[9.5px] font-mono text-slate-400 dark:text-slate-500">
                    {t('valueTrapReviews.factsStrip.snapshot', { date: pos?.snapshot_date ?? '—' })}
                </div>
            </div>
            {/* 2. Quantity */}
            <div className="flex flex-col gap-0.5">
                <div className={EYEBROW}>{t('valueTrapReviews.factsStrip.quantity')}</div>
                <div className="font-mono text-[13px] font-bold text-slate-900 dark:text-slate-100 mt-0.5">
                    {pos ? pos.qty.toLocaleString() : '—'}
                </div>
            </div>
            {/* 3. Cost / Unit */}
            <div className="flex flex-col gap-0.5">
                <div className={EYEBROW}>{t('valueTrapReviews.factsStrip.costUnit')}</div>
                <div className="font-mono text-[13px] font-bold text-slate-900 dark:text-slate-100 mt-0.5">
                    {pos ? pos.cost_price_unit.toFixed(4) : '—'}
                    {pos && <span className="font-normal text-[9.5px] text-slate-400 dark:text-slate-500 ml-1">{pos.currency}</span>}
                </div>
                <div className={`text-[9.5px] font-mono ${isStale ? 'text-amber-600 dark:text-amber-400 font-semibold' : 'text-slate-400 dark:text-slate-500'}`}>
                    {t('valueTrapReviews.factsStrip.priceAsOf', { date: pos?.price_date ?? pos?.snapshot_date ?? '—' })}
                </div>
            </div>
            {/* 4. Freshness */}
            <div className="flex flex-col gap-0.5">
                <div className={EYEBROW}>{t('valueTrapReviews.factsStrip.freshness')}</div>
                <div className="mt-0.5">
                    {pos?.freshness ? (
                        pos.freshness.fresh ? (
                            <span className="inline-flex items-center gap-1 text-[9.5px] font-bold px-1.5 py-0.5 rounded bg-emerald-100 dark:bg-emerald-900/30 text-emerald-800 dark:text-emerald-400">
                                {t('valueTrapReviews.factsStrip.fresh')}
                            </span>
                        ) : (
                            <span className="inline-flex items-center gap-1 text-[9.5px] font-bold px-1.5 py-0.5 rounded bg-rose-100 dark:bg-rose-900/30 text-rose-800 dark:text-rose-300">
                                {t('valueTrapReviews.factsStrip.staleMark')}
                            </span>
                        )
                    ) : (
                        <span className="font-mono text-[13px] text-slate-400 dark:text-slate-500">—</span>
                    )}
                </div>
            </div>
            {/* 5. Unrealized */}
            <div className="flex flex-col gap-0.5">
                <div className={EYEBROW}>{t('valueTrapReviews.factsStrip.unrealized')}</div>
                <div className="font-mono text-[13px] font-bold text-rose-700 dark:text-rose-400 mt-0.5">
                    {pct(loss.unrealized_return_pct)}
                </div>
                <div className="text-[9.5px] font-mono text-slate-400 dark:text-slate-500">
                    {t('valueTrapReviews.factsStrip.threshold', { value: pct(loss.trigger_threshold_pct) })}
                </div>
            </div>
            {/* 6. Days Open */}
            <div className="flex flex-col gap-0.5">
                <div className={EYEBROW}>{t('valueTrapReviews.factsStrip.daysOpen')}</div>
                <div className="font-mono text-[13px] font-bold text-slate-900 dark:text-slate-100 mt-0.5">
                    {loss.days_open != null ? `${loss.days_open}d` : '—'}
                </div>
            </div>
        </div>
    );
};

// ── Memo Card ─────────────────────────────────────────────────────────────────

interface MemoCardProps {
    state: ValueTrapLinkageState;
    memos: ValueTrapMemoEntry[];
    displayText: string | null;
    assetId: string;
    linkageAck: boolean;
    onLinkageAckChange: (v: boolean) => void;
    onConfirmNoMemo: (assetId: string) => void;
    confirmingNoMemo: boolean;
}

const MemoCard: React.FC<MemoCardProps> = ({
    state, memos, displayText, assetId,
    linkageAck, onLinkageAckChange, onConfirmNoMemo, confirmingNoMemo,
}) => {
    const { t } = useTranslation('reports');
    if (state === 'linked') {
        return (
            <div className="rounded-[10px] px-4 py-3.5 mb-3.5 bg-emerald-50 dark:bg-emerald-900/20 border border-transparent">
                {memos.map((m) => (
                    <div key={m.memo_id}>
                        <div className="flex items-center gap-2 text-[11.5px] font-bold text-emerald-800 dark:text-emerald-400">
                            <svg className="w-3.5 h-3.5 flex-shrink-0" fill="currentColor" viewBox="0 0 20 20" aria-hidden>
                                <path d="M12.232 4.232a2.5 2.5 0 013.536 3.536l-1.225 1.224a.75.75 0 001.061 1.06l1.224-1.224a4 4 0 00-5.656-5.656l-3 3a4 4 0 00.225 5.865.75.75 0 00.977-1.138 2.5 2.5 0 01-.142-3.667l3-3z" />
                                <path d="M11.603 7.963a.75.75 0 00-.977 1.138 2.5 2.5 0 01.142 3.667l-3 3a2.5 2.5 0 01-3.536-3.536l1.225-1.224a.75.75 0 00-1.061-1.06l-1.224 1.224a4 4 0 105.656 5.656l3-3a4 4 0 00-.225-5.865z" />
                            </svg>
                            <span className="font-mono">{m.memo_id}</span>
                            <span className="font-medium text-slate-600 dark:text-slate-400">{m.title}</span>
                        </div>
                        {m.falsification_summary && (
                            <div className="mt-1.5 text-[11.5px] text-slate-600 dark:text-slate-300 leading-snug">
                                {m.falsification_summary}
                            </div>
                        )}
                    </div>
                ))}
            </div>
        );
    }

    if (state === 'confirmed_none') {
        return (
            <div className="rounded-[10px] px-4 py-3.5 mb-3.5 bg-white dark:bg-card-dark border border-slate-200 dark:border-border-dark">
                <div className="text-[11.5px] text-slate-500 dark:text-slate-400">
                    <span className="font-semibold text-slate-700 dark:text-slate-300 mr-1.5">{t('valueTrapReviews.memoCard.valueMemo')}</span>
                    <span className="italic">{displayText}</span>
                </div>
            </div>
        );
    }

    // unresolved
    return (
        <div className="rounded-[10px] px-4 py-3.5 mb-3.5 bg-amber-50 dark:bg-amber-900/20 border border-amber-400/60 dark:border-amber-700/60">
            <div className="flex items-center gap-2 text-[11.5px] font-bold text-amber-800 dark:text-amber-400 mb-1.5">
                <svg className="w-3.5 h-3.5 flex-shrink-0" fill="currentColor" viewBox="0 0 20 20" aria-hidden>
                    <path fillRule="evenodd" d="M18 10a8 8 0 11-16 0 8 8 0 0116 0zM8.94 6.94a.75.75 0 11-1.061-1.061 3 3 0 112.871 5.026v.345a.75.75 0 01-1.5 0v-.5c0-.72.57-1.172 1.081-1.287A1.5 1.5 0 108.94 6.94zM10 15a1 1 0 100-2 1 1 0 000 2z" clipRule="evenodd" />
                </svg>
                {t('valueTrapReviews.memoCard.notBackfilled')}
            </div>
            <div className="text-[11.5px] text-slate-700 dark:text-slate-300 leading-snug mb-3">
                {displayText ?? t('valueTrapReviews.memoCard.verifyManually')}
            </div>
            <label className="flex items-center gap-2 text-[11.5px] text-slate-700 dark:text-slate-300 cursor-pointer">
                <input
                    type="checkbox"
                    className="w-4 h-4 rounded border-amber-400 dark:border-amber-600 accent-amber-600"
                    checked={linkageAck}
                    onChange={(e) => onLinkageAckChange(e.target.checked)}
                />
                {t('valueTrapReviews.memoCard.verifiedManuallyAck')}
            </label>
            <button
                onClick={() => onConfirmNoMemo(assetId)}
                disabled={confirmingNoMemo}
                className="mt-2 text-[11px] font-semibold text-slate-400 dark:text-slate-500 underline underline-offset-2 hover:text-slate-600 dark:hover:text-slate-300 disabled:opacity-50"
                title={t('valueTrapReviews.memoCard.confirmNoMemoTitle')}
            >
                {confirmingNoMemo ? t('valueTrapReviews.memoCard.confirming') : t('valueTrapReviews.memoCard.ownerConfirmsNoMemo')}
            </button>
        </div>
    );
};

// ── Lot Detail Block (collapsible) ────────────────────────────────────────────

const LotDetailBlock: React.FC<{
    lotDetail: LotDetail;
    costPriceUnit: number | null;
    currency: string;
}> = ({ lotDetail, costPriceUnit, currency }) => {
    const { t } = useTranslation('reports');
    const [open, setOpen] = useState(false);

    return (
        <div className="border border-slate-200 dark:border-slate-700 rounded-[10px] mb-2.5 overflow-hidden">
            <button
                onClick={() => setOpen(v => !v)}
                className="w-full flex items-center gap-2 px-4 py-2.5 text-left text-xs font-semibold text-slate-600 dark:text-slate-300 hover:bg-slate-50 dark:hover:bg-slate-800/30 transition-colors"
            >
                <svg className="w-4 h-4 text-slate-400 dark:text-slate-500 flex-shrink-0" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.8} aria-hidden>
                    <path strokeLinecap="round" strokeLinejoin="round" d="M6 6h12M6 12h12M6 18h12" />
                </svg>
                {t('valueTrapReviews.lotDetail.title')}
                <span className="ml-auto text-[10.5px] font-mono font-normal text-slate-400 dark:text-slate-500">
                    {t('valueTrapReviews.lotDetail.openLots', { count: lotDetail.open_lot_count, avgCost: lotDetail.avg_cost.toFixed(4) })}
                    {costPriceUnit != null ? t('valueTrapReviews.lotDetail.reconciles') : ''}
                </span>
                <svg
                    className={`w-4 h-4 text-slate-400 dark:text-slate-500 flex-shrink-0 transition-transform ${open ? 'rotate-90' : ''}`}
                    fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2} aria-hidden
                >
                    <path strokeLinecap="round" strokeLinejoin="round" d="M9 5l7 7-7 7" />
                </svg>
            </button>
            {open && (
                <div className="border-t border-slate-100 dark:border-slate-700/50">
                    <div className="overflow-y-auto max-h-56">
                        <table className="w-full text-xs font-mono">
                            <thead>
                                <tr className="text-[10px] text-slate-400 dark:text-slate-500 uppercase sticky top-0 bg-slate-50 dark:bg-slate-900">
                                    <th className="text-left pl-5 pr-4 py-2 font-semibold">{t('valueTrapReviews.lotDetail.cols.date')}</th>
                                    <th className="text-right pr-4 py-2 font-semibold">{t('valueTrapReviews.lotDetail.cols.qty')}</th>
                                    <th className="text-right pr-5 py-2 font-semibold">{t('valueTrapReviews.lotDetail.cols.price', { currency })}</th>
                                </tr>
                            </thead>
                            <tbody className="divide-y divide-slate-100 dark:divide-slate-700/30">
                                {lotDetail.lots.map((lot, i) => (
                                    <tr key={i}>
                                        <td className="pl-5 pr-4 py-1 text-slate-600 dark:text-slate-300">{lot.date}</td>
                                        <td className="pr-4 py-1 text-right">{lot.quantity.toLocaleString(undefined, { maximumFractionDigits: 4 })}</td>
                                        <td className="pr-5 py-1 text-right">{lot.price_unit.toFixed(4)}</td>
                                    </tr>
                                ))}
                            </tbody>
                        </table>
                    </div>
                    <div className="px-5 py-2 text-[10.5px] text-slate-400 dark:text-slate-500">
                        {t('valueTrapReviews.lotDetail.showingLots', { shown: lotDetail.lots.length, total: lotDetail.open_lot_count })}
                        {lotDetail.truncated ? t('valueTrapReviews.lotDetail.firstShown') : ''}.
                    </div>
                </div>
            )}
        </div>
    );
};

// ── Decision History Block (collapsible) ──────────────────────────────────────

const DecisionHistoryBlock: React.FC<{
    history: ValueTrapContext['decision_history'];
    total: number;
}> = ({ history, total }) => {
    const { t } = useTranslation('reports');
    const [open, setOpen] = useState(false);
    if (!history.length) return null;

    return (
        <div className="border border-slate-200 dark:border-slate-700 rounded-[10px] mb-2.5 overflow-hidden">
            <button
                onClick={() => setOpen(v => !v)}
                className="w-full flex items-center gap-2 px-4 py-2.5 text-left text-xs font-semibold text-slate-600 dark:text-slate-300 hover:bg-slate-50 dark:hover:bg-slate-800/30 transition-colors"
            >
                <svg className="w-4 h-4 text-slate-400 dark:text-slate-500 flex-shrink-0" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.8} aria-hidden>
                    <path strokeLinecap="round" strokeLinejoin="round" d="M12 8v4l3 3m6-3a9 9 0 11-18 0 9 9 0 0118 0z" />
                </svg>
                {t('valueTrapReviews.decisionHistory.title')}
                <span className="ml-auto text-[10.5px] font-mono font-normal text-slate-400 dark:text-slate-500">
                    {t('valueTrapReviews.decisionHistory.showing', { count: history.length, total })}
                </span>
                <svg
                    className={`w-4 h-4 text-slate-400 dark:text-slate-500 flex-shrink-0 transition-transform ${open ? 'rotate-90' : ''}`}
                    fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2} aria-hidden
                >
                    <path strokeLinecap="round" strokeLinejoin="round" d="M9 5l7 7-7 7" />
                </svg>
            </button>
            {open && (
                <div className="border-t border-slate-100 dark:border-slate-700/50">
                    <table className="w-full text-xs">
                        <thead>
                            <tr className="text-[10px] text-slate-400 dark:text-slate-500 uppercase bg-slate-50 dark:bg-slate-900">
                                <th className="text-left pl-5 pr-3 py-2 font-semibold">{t('valueTrapReviews.decisionHistory.cols.date')}</th>
                                <th className="text-left pr-3 py-2 font-semibold">{t('valueTrapReviews.decisionHistory.cols.action')}</th>
                                <th className="text-right pr-3 py-2 font-semibold">{t('valueTrapReviews.decisionHistory.cols.qty')}</th>
                                <th className="text-right pr-3 py-2 font-semibold">{t('valueTrapReviews.decisionHistory.cols.price')}</th>
                                <th className="text-left pr-3 py-2 font-semibold">{t('valueTrapReviews.decisionHistory.cols.bucket')}</th>
                                <th className="text-left pr-5 py-2 font-semibold">{t('valueTrapReviews.decisionHistory.cols.status')}</th>
                            </tr>
                        </thead>
                        <tbody className="divide-y divide-slate-100 dark:divide-slate-800/50">
                            {history.map((h, i) => (
                                <tr key={i}>
                                    <td className="pl-5 pr-3 py-1.5 font-mono text-slate-600 dark:text-slate-300">{h.log_date ?? '—'}</td>
                                    <td className="pr-3 py-1.5 text-slate-700 dark:text-slate-300">{h.action ?? '—'}</td>
                                    <td className="pr-3 py-1.5 font-mono text-right text-slate-700 dark:text-slate-300">
                                        {h.quantity != null ? h.quantity.toLocaleString() : '—'}
                                    </td>
                                    <td className="pr-3 py-1.5 font-mono text-right text-slate-700 dark:text-slate-300">
                                        {h.price != null ? h.price.toFixed(4) : '—'}
                                    </td>
                                    <td className="pr-3 py-1.5 text-slate-500 dark:text-slate-400">{h.rule_bucket ?? '—'}</td>
                                    <td className="pr-5 py-1.5 text-slate-500 dark:text-slate-400">{h.verification_status ?? '—'}</td>
                                </tr>
                            ))}
                        </tbody>
                    </table>
                </div>
            )}
        </div>
    );
};

// ── Deferred Panel (redesigned) ───────────────────────────────────────────────

const DeferredPanel: React.FC<{
    assets: DeferredAssetEntry[];
    today: string;
}> = ({ assets, today }) => {
    const { t } = useTranslation('reports');
    if (!assets.length) {
        return (
            <div className="px-5 py-12 text-center text-slate-400 dark:text-slate-500 text-xs">
                {t('valueTrapReviews.deferredPanel.none')}
            </div>
        );
    }

    return (
        <>
            <table className="w-full text-xs">
                <thead>
                    <tr className="border-b border-slate-100 dark:border-slate-800/50 bg-slate-50/50 dark:bg-slate-900/20 text-[10px] font-bold uppercase tracking-wider text-slate-500 dark:text-slate-400">
                        <th className="text-left px-5 py-3">{t('valueTrapReviews.deferredPanel.cols.asset')}</th>
                        <th className="text-left px-5 py-3">{t('valueTrapReviews.deferredPanel.cols.lastPriceDate')}</th>
                        <th className="text-left px-5 py-3">{t('valueTrapReviews.deferredPanel.cols.freshness')}</th>
                        <th className="text-left px-5 py-3">{t('valueTrapReviews.deferredPanel.cols.dataFixId')}</th>
                        <th className="text-left px-5 py-3">{t('valueTrapReviews.deferredPanel.cols.dueDate')}</th>
                        <th className="text-left px-5 py-3">{t('valueTrapReviews.deferredPanel.cols.status')}</th>
                    </tr>
                </thead>
                <tbody className="divide-y divide-slate-100 dark:divide-slate-800/50">
                    {assets.map((a) => {
                        const overdue = a.data_fix_due_at != null && a.data_fix_due_at < today;
                        const staleDays = daysStale(a.price_date, today);
                        // stale (danger) for ≥30d; aging (warning) for <30d
                        const chipStyle = staleChipStyle(staleDays);
                        return (
                            <tr key={a.asset_id} className="hover:bg-slate-50/50 dark:hover:bg-slate-800/20 transition-colors">
                                <td className="px-5 py-2.5 font-mono text-slate-700 dark:text-slate-300 font-medium">
                                    {a.asset_id}
                                </td>
                                <td className="px-5 py-2.5 font-mono text-slate-500 dark:text-slate-400">
                                    {a.price_date ?? '—'}
                                </td>
                                <td className="px-5 py-2.5">
                                    {staleDays != null ? (
                                        <span className={`text-[9.5px] font-bold px-1.5 py-0.5 rounded ${chipStyle}`}>
                                            {t('valueTrapReviews.deferredPanel.staleDays', { days: staleDays })}
                                        </span>
                                    ) : (
                                        <span className="text-slate-400 dark:text-slate-500">—</span>
                                    )}
                                </td>
                                <td className="px-5 py-2.5 font-mono text-slate-500 dark:text-slate-400">
                                    {a.data_fix_id != null ? `#${a.data_fix_id}` : '—'}
                                </td>
                                <td className="px-5 py-2.5 font-mono text-slate-500 dark:text-slate-400">
                                    {a.data_fix_due_at ?? '—'}
                                </td>
                                <td className="px-5 py-2.5">
                                    {overdue ? (
                                        <span className="inline-flex items-center text-[9.5px] font-bold px-2 py-0.5 rounded bg-rose-100 dark:bg-rose-900/30 text-rose-800 dark:text-rose-300">
                                            {t('valueTrapReviews.deferredPanel.overdue')}
                                        </span>
                                    ) : (
                                        <span className="inline-flex items-center text-[9.5px] font-bold px-2 py-0.5 rounded bg-amber-100 dark:bg-amber-900/30 text-amber-800 dark:text-amber-400">
                                            {t('valueTrapReviews.deferredPanel.withinDueDate')}
                                        </span>
                                    )}
                                </td>
                            </tr>
                        );
                    })}
                </tbody>
            </table>
            <div className="px-5 py-3 text-[10.5px] font-mono text-slate-400 dark:text-slate-500 border-t border-slate-100 dark:border-slate-800/50">
                {t('valueTrapReviews.deferredPanel.showingAssets', { count: assets.length })}
            </div>
        </>
    );
};

// ── Main Page ─────────────────────────────────────────────────────────────────

export const ValueTrapReviews: React.FC = () => {
    const { t } = useTranslation('reports');
    const navigate = useNavigate();

    // List + filter state
    const [statusFilter, setStatusFilter] = useState<TabFilter>('open');
    const [reviews, setReviews] = useState<ValueTrapReview[]>([]);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState<string | null>(null);

    // Scan state
    const [scanning, setScanning] = useState(false);
    const [scanSummary, setScanSummary] = useState<ValueTrapScanSummary | null>(null);
    const [scanRunAt, setScanRunAt] = useState<string | null>(null);   // client-side timestamp
    const [activeTile, setActiveTile] = useState<ScanTile | null>(null);

    // Pending count for tab badges (fetched separately on mount)
    const [pendingCount, setPendingCount] = useState<ValueTrapPendingCount | null>(null);

    // Expand / form state
    const [expandedId, setExpandedId] = useState<number | null>(null);
    const [form, setForm] = useState<RulingFormState>(EMPTY_FORM);
    const [submitting, setSubmitting] = useState(false);
    const [submitError, setSubmitError] = useState<string | null>(null);

    // Context state (per-review)
    const [contextMap, setContextMap] = useState<Record<number, ValueTrapContext | null>>({});
    const [contextLoading, setContextLoading] = useState<Record<number, boolean>>({});
    const [contextError, setContextError] = useState<Record<number, string | null>>({});

    // AI draft state
    const [draftLoading, setDraftLoading] = useState(false);
    const [draftError, setDraftError] = useState<string | null>(null);
    const [draftIs503, setDraftIs503] = useState(false);

    // Confirm-no-memo state (per-asset)
    const [confirmingNoMemo, setConfirmingNoMemo] = useState<Record<string, boolean>>({});

    const today = new Date().toISOString().slice(0, 10);

    // ── Load reviews ──────────────────────────────────────────────────────────

    const load = useCallback(async () => {
        // Deferred tab shows scan summary data, not API reviews
        if (statusFilter === 'deferred') {
            setLoading(false);
            return;
        }
        setError(null);
        try {
            const data = await api.getValueTrapReviews(statusFilter as ValueTrapStatus | 'all');
            setReviews(data);
        } catch (e) {
            console.error('Failed to fetch value-trap reviews:', e);
            setError(t('valueTrapReviews.errors.load'));
        } finally {
            setLoading(false);
        }
    }, [statusFilter]);

    useEffect(() => { load(); }, [load]);

    // Fetch pending count on mount for Open tab badge
    useEffect(() => {
        api.getValueTrapPendingCount()
            .then(setPendingCount)
            .catch(() => null);
    }, []);

    // ── Context loading ───────────────────────────────────────────────────────

    const fetchContext = async (review: ValueTrapReview) => {
        const id = review.id;
        if (contextMap[id] !== undefined || contextLoading[id]) return;
        setContextLoading(prev => ({ ...prev, [id]: true }));
        setContextError(prev => ({ ...prev, [id]: null }));
        try {
            const ctx = await api.getValueTrapContext(id);
            setContextMap(prev => ({ ...prev, [id]: ctx }));
        } catch (e) {
            console.error('Failed to fetch context for review', id, e);
            setContextError(prev => ({
                ...prev,
                [id]: e instanceof Error ? e.message : t('valueTrapReviews.errors.loadContext'),
            }));
            setContextMap(prev => ({ ...prev, [id]: null }));
        } finally {
            setContextLoading(prev => ({ ...prev, [id]: false }));
        }
    };

    // ── Handlers ──────────────────────────────────────────────────────────────

    const handleScan = async () => {
        setScanning(true);
        setError(null);
        setScanSummary(null);
        try {
            const summary = await api.scanValueTraps();
            setScanSummary(summary);
            setScanRunAt(new Date().toLocaleString('sv').replace('T', ' ').slice(0, 16));
            // Refresh pending count after scan
            api.getValueTrapPendingCount().then(setPendingCount).catch(() => null);
            await load();
        } catch (e) {
            console.error('Failed to run value-trap scan:', e);
            setError(t('valueTrapReviews.errors.scanFailed'));
        } finally {
            setScanning(false);
        }
    };

    const openReviewForm = (review: ValueTrapReview) => {
        setExpandedId(review.id);
        setForm(EMPTY_FORM);
        setSubmitError(null);
        setDraftError(null);
        setDraftIs503(false);
        fetchContext(review);
    };

    const handleSubmitRuling = async (review: ValueTrapReview) => {
        setSubmitting(true);
        setSubmitError(null);
        try {
            await api.submitValueTrapRuling(review.id, {
                thesis_restated: form.thesis_restated,
                falsification_check: form.falsification_check,
                would_buy_today: form.would_buy_today,
                ruling: form.ruling as ValueTrapRuling,
                adversarial_ack: form.adversarial_ack,
                next_review_date: form.next_review_date || undefined,
                linkage_ack: form.linkage_ack,
            });
            setExpandedId(null);
            // Refresh pending count after ruling
            api.getValueTrapPendingCount().then(setPendingCount).catch(() => null);
            await load();
        } catch (e) {
            console.error('Failed to submit value-trap ruling:', e);
            setSubmitError(e instanceof Error ? e.message : t('valueTrapReviews.errors.submitRuling'));
        } finally {
            setSubmitting(false);
        }
    };

    const handleDraft = async (review: ValueTrapReview) => {
        setDraftLoading(true);
        setDraftError(null);
        setDraftIs503(false);
        try {
            const draft = await api.draftValueTrap(review.id);
            setForm(prev => ({
                ...prev,
                thesis_restated: draft.thesis_draft,
                falsification_check: draft.falsification_draft,
                would_buy_today: draft.buy_today_draft,
            }));
        } catch (e) {
            const msg = e instanceof Error ? e.message : t('valueTrapReviews.errors.draftFailed');
            setDraftError(msg);
            const is503 = msg.includes('503') || /no llm|key configured/i.test(msg);
            setDraftIs503(is503);
        } finally {
            setDraftLoading(false);
        }
    };

    const handleOpenCaseFile = (assetId: string) => {
        navigate(`/asset-case-file?asset_id=${encodeURIComponent(assetId)}`);
    };

    const handleConfirmNoMemo = async (assetId: string) => {
        setConfirmingNoMemo(prev => ({ ...prev, [assetId]: true }));
        try {
            await api.confirmNoMemo(assetId);
            // Invalidate cached context for any review with this asset
            setContextMap(prev => {
                const updated: Record<number, ValueTrapContext | null> = { ...prev };
                Object.entries(updated).forEach(([idStr, ctx]) => {
                    if (ctx && ctx.asset_id === assetId) delete updated[Number(idStr)];
                });
                return updated;
            });
            if (expandedId != null) {
                const review = reviews.find(r => r.id === expandedId);
                if (review) fetchContext(review);
            }
        } catch (e) {
            console.error('Failed to confirm no memo:', e);
        } finally {
            setConfirmingNoMemo(prev => ({ ...prev, [assetId]: false }));
        }
    };

    const handleTileClick = (tile: ScanTile) => {
        setActiveTile(tile);
        setExpandedId(null);
        if (tile === 'deferred') setStatusFilter('deferred');
        else if (tile === 'triggered' || tile === 'evaluated') setStatusFilter('open');
        // Exempt and Excluded: just mark the tile visually, no tab switch
    };

    const handleTabChange = (tab: TabFilter) => {
        setStatusFilter(tab);
        setExpandedId(null);
        setActiveTile(null);
    };

    // ── Derived state ─────────────────────────────────────────────────────────

    const currentContext = expandedId != null ? contextMap[expandedId] ?? null : null;
    const linkageState: ValueTrapLinkageState | null = currentContext?.memo_linkage?.state ?? null;
    const isUnresolved = linkageState === 'unresolved';

    const isPriceFresh =
        !currentContext?.position ||
        currentContext.position.freshness?.fresh !== false;

    const canDraft =
        form.thesis_restated.trim() === '' &&
        form.falsification_check.trim() === '' &&
        form.would_buy_today.trim() === '';

    const submitDisabled =
        submitting ||
        !form.ruling ||
        (form.ruling === 'hold_with_thesis' && !form.next_review_date) ||
        (form.ruling === 'liquidate' && !form.adversarial_ack) ||
        (isUnresolved && !form.linkage_ack) ||
        !isPriceFresh;

    const saveExplain = getSaveExplain(form, isUnresolved, isPriceFresh, t);

    // Tab count helpers
    const openCount = statusFilter === 'open' ? reviews.length : (pendingCount?.open ?? null);
    const ruledCount = statusFilter === 'ruled' ? reviews.length : null;
    const allCount = statusFilter === 'all' ? reviews.length : null;
    const deferredCount = scanSummary?.deferred_unreliable ?? null;

    // Reconciliation equation for scan banner footer
    function buildReconciliationLine(s: ValueTrapScanSummary, ts: string | null): string {
        const eq = [
            t('valueTrapReviews.reconciliation.evaluated', { count: s.evaluated }),
            t('valueTrapReviews.reconciliation.deferred', { count: s.deferred_unreliable }),
            t('valueTrapReviews.reconciliation.exempt', { count: s.exempt_cash_like }),
            t('valueTrapReviews.reconciliation.excluded', { count: s.skipped_bucket }),
        ];
        if (s.skipped_no_cost > 0) eq.push(t('valueTrapReviews.reconciliation.zeroCost', { count: s.skipped_no_cost }));
        const parts: string[] = [t('valueTrapReviews.reconciliation.holdingsScanned', { count: s.scanned })];
        if (ts) parts.push(t('valueTrapReviews.reconciliation.lastRun', { ts }));
        parts.push(eq.join(' + ') + ` = ${s.scanned}`);
        return parts.join(' · ');
    }

    // ── Render ────────────────────────────────────────────────────────────────

    if (loading && statusFilter !== 'deferred') {
        return (
            <div className="p-8 text-center text-slate-500 dark:text-slate-400 text-sm">
                {t('valueTrapReviews.loading')}
            </div>
        );
    }

    const TABS: { key: TabFilter; label: string; count: number | null }[] = [
        { key: 'open', label: t('valueTrapReviews.tabs.open'), count: openCount },
        { key: 'ruled', label: t('valueTrapReviews.tabs.ruled'), count: ruledCount },
        { key: 'all', label: t('valueTrapReviews.tabs.all'), count: allCount },
        { key: 'deferred', label: t('valueTrapReviews.tabs.deferred'), count: deferredCount },
    ];

    const SCAN_TILES: { key: ScanTile; label: string; sub: string; value: number; danger?: boolean }[] = scanSummary
        ? [
            { key: 'evaluated', label: t('valueTrapReviews.scanTiles.evaluated'), sub: t('valueTrapReviews.scanTiles.thisScan'), value: scanSummary.evaluated },
            { key: 'triggered', label: t('valueTrapReviews.scanTiles.triggered'), sub: t('valueTrapReviews.scanTiles.newOpenReview'), value: scanSummary.hits, danger: scanSummary.hits > 0 },
            { key: 'deferred', label: t('valueTrapReviews.scanTiles.deferred'), sub: t('valueTrapReviews.scanTiles.staleValuation'), value: scanSummary.deferred_unreliable },
            { key: 'exempt', label: t('valueTrapReviews.scanTiles.exempt'), sub: t('valueTrapReviews.scanTiles.cashLike'), value: scanSummary.exempt_cash_like },
            { key: 'excluded', label: t('valueTrapReviews.scanTiles.excluded'), sub: t('valueTrapReviews.scanTiles.ruleBucket'), value: scanSummary.skipped_bucket },
        ]
        : [];

    return (
        <div
            data-testid="value-trap-reviews-page"
            className="p-8 max-w-[1400px] mx-auto w-full space-y-5 bg-gray-50 dark:bg-background-dark min-h-screen"
        >
            {/* ── Error banner ── */}
            {error && (
                <div className="flex items-center gap-2 bg-red-50 dark:bg-red-900/20 border border-red-200 dark:border-red-800/50 rounded-lg px-4 py-3">
                    <svg className="w-4 h-4 text-red-500 flex-shrink-0" fill="currentColor" viewBox="0 0 20 20" aria-hidden>
                        <path fillRule="evenodd" d="M10 18a8 8 0 100-16 8 8 0 000 16zM8.28 7.22a.75.75 0 00-1.06 1.06L8.94 10l-1.72 1.72a.75.75 0 101.06 1.06L10 11.06l1.72 1.72a.75.75 0 101.06-1.06L11.06 10l1.72-1.72a.75.75 0 00-1.06-1.06L10 8.94 8.28 7.22z" clipRule="evenodd" />
                    </svg>
                    <p className="text-red-700 dark:text-red-300 text-sm">{error}</p>
                </div>
            )}

            {/* ── Page header ── */}
            <header className="flex justify-between items-start gap-4">
                <div>
                    <div className="text-[10px] font-bold uppercase tracking-widest text-slate-400 dark:text-slate-500 mb-1">
                        {t('valueTrapReviews.breadcrumb')}
                    </div>
                    <h1 className="text-2xl font-bold text-slate-900 dark:text-slate-100 tracking-tight leading-tight">
                        {t('valueTrapReviews.title')}
                    </h1>
                    <p className="text-xs text-slate-500 dark:text-slate-400 mt-1 font-mono">
                        {t('valueTrapReviews.subtitle')}
                    </p>
                </div>
                <button
                    onClick={handleScan}
                    disabled={scanning}
                    className="flex-shrink-0 flex items-center gap-2 px-5 py-2 bg-blue-600 hover:bg-blue-700 disabled:bg-blue-400 text-white rounded-lg text-sm font-bold transition-colors shadow-sm"
                    style={{ boxShadow: '0 10px 15px -3px rgb(59 130 246 / 0.20), 0 4px 6px -4px rgb(59 130 246 / 0.20)' }}
                >
                    <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2} aria-hidden>
                        <path strokeLinecap="round" strokeLinejoin="round" d="M9 3H5a2 2 0 00-2 2v4m6-6h10a2 2 0 012 2v4M9 3v18m0 0h10a2 2 0 002-2V9M9 21H5a2 2 0 01-2-2V9m0 0h18" />
                    </svg>
                    {scanning ? t('valueTrapReviews.scanning') : t('valueTrapReviews.runScanNow')}
                </button>
            </header>

            {/* ── Scan summary card ── */}
            {scanSummary && (
                <div className={CARD}>
                    <div className="flex items-stretch">
                        {SCAN_TILES.map((tile, i) => {
                            const isActive = activeTile === tile.key;
                            const isLast = i === SCAN_TILES.length - 1;
                            return (
                                <button
                                    key={tile.key}
                                    onClick={() => handleTileClick(tile.key)}
                                    className={[
                                        'flex-1 flex flex-col gap-1 px-4 py-3.5 text-left cursor-pointer transition-colors',
                                        !isLast ? 'border-r border-slate-100 dark:border-slate-800/60' : '',
                                        isActive
                                            ? 'bg-blue-500/10 dark:bg-blue-500/10'
                                            : 'hover:bg-slate-50 dark:hover:bg-slate-800/30',
                                    ].join(' ')}
                                >
                                    <div className={[
                                        'font-mono font-bold text-[22px] leading-none',
                                        tile.value === 0
                                            ? 'text-slate-400 dark:text-slate-500'
                                            : tile.danger
                                                ? 'text-rose-700 dark:text-rose-400'
                                                : 'text-slate-900 dark:text-slate-100',
                                    ].join(' ')}>
                                        {tile.value}
                                    </div>
                                    <div className="text-[10px] font-bold uppercase tracking-wider text-slate-500 dark:text-slate-400">
                                        {tile.label}
                                    </div>
                                    <div className="text-[10px] font-mono text-slate-400 dark:text-slate-500">
                                        {tile.sub}
                                    </div>
                                </button>
                            );
                        })}
                    </div>
                    <div className="px-5 py-3 font-mono text-[10.5px] text-slate-400 dark:text-slate-500 border-t border-slate-100 dark:border-slate-800/50">
                        {buildReconciliationLine(scanSummary, scanRunAt)}
                    </div>
                </div>
            )}

            {/* ── Filter tabs ── */}
            <div className="flex items-center gap-1">
                {TABS.map((tab) => (
                    <button
                        key={tab.key}
                        onClick={() => handleTabChange(tab.key)}
                        className={[
                            'px-3.5 py-1.5 rounded-lg text-xs font-semibold transition-colors border',
                            statusFilter === tab.key
                                ? 'bg-blue-600 text-white border-transparent'
                                : 'bg-white dark:bg-card-dark text-slate-600 dark:text-slate-300 border-slate-200 dark:border-border-dark hover:bg-slate-50 dark:hover:bg-slate-800/40',
                        ].join(' ')}
                    >
                        {tab.label}
                        {tab.count != null ? (
                            <span className={`ml-1.5 ${statusFilter === tab.key ? 'opacity-70' : 'opacity-50'}`}>
                                ({tab.count})
                            </span>
                        ) : (
                            tab.key === 'deferred' && (
                                <span className="ml-1.5 opacity-50">(—)</span>
                            )
                        )}
                    </button>
                ))}
            </div>

            {/* ── Panel: Deferred ── */}
            {statusFilter === 'deferred' && (
                <div className={CARD}>
                    {scanSummary ? (
                        <DeferredPanel
                            assets={scanSummary.deferred_assets ?? []}
                            today={today}
                        />
                    ) : (
                        <div className="px-5 py-12 text-center text-slate-400 dark:text-slate-500 text-sm">
                            <Trans
                                t={t}
                                i18nKey="valueTrapReviews.noScanYet"
                                components={{ b: <span className="font-semibold text-slate-600 dark:text-slate-300" /> }}
                            />
                        </div>
                    )}
                </div>
            )}

            {/* ── Panel: Ruled (empty state) ── */}
            {statusFilter === 'ruled' && reviews.length === 0 && (
                <div className={CARD}>
                    <div className="flex flex-col items-center justify-center py-12 gap-3 text-slate-400 dark:text-slate-500">
                        <svg className="w-7 h-7" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.5} aria-hidden>
                            <path strokeLinecap="round" strokeLinejoin="round" d="M12 3v1m0 16v1M3 12h1m16 0h1M6.343 6.343l.707.707M16.95 16.95l.707.707M6.343 17.657l.707-.707M16.95 7.05l.707-.707" />
                            <path strokeLinecap="round" strokeLinejoin="round" d="M9 12l2 2 4-4M7.835 4.697a3.42 3.42 0 001.946-.806 3.42 3.42 0 014.438 0 3.42 3.42 0 001.946.806 3.42 3.42 0 013.138 3.138 3.42 3.42 0 00.806 1.946 3.42 3.42 0 010 4.438 3.42 3.42 0 00-.806 1.946 3.42 3.42 0 01-3.138 3.138 3.42 3.42 0 00-1.946.806 3.42 3.42 0 01-4.438 0 3.42 3.42 0 00-1.946-.806 3.42 3.42 0 01-3.138-3.138 3.42 3.42 0 00-.806-1.946 3.42 3.42 0 010-4.438 3.42 3.42 0 00.806-1.946 3.42 3.42 0 013.138-3.138z" />
                        </svg>
                        <p className="text-xs text-center max-w-xs">
                            {t('valueTrapReviews.noRulingsYet')}
                        </p>
                    </div>
                </div>
            )}

            {/* ── Panel: Open / Ruled / All review list ── */}
            {statusFilter !== 'deferred' && (reviews.length > 0 || statusFilter === 'open') && (
                <div className={CARD}>
                    {/* Table header */}
                    <div className="grid gap-3 px-5 py-3 border-b border-slate-100 dark:border-slate-800/50 bg-slate-50/50 dark:bg-slate-900/20"
                        style={{ gridTemplateColumns: '2.2fr 1fr 1.6fr 0.9fr 0.8fr 1fr' }}>
                        {[
                            t('valueTrapReviews.reviewCols.asset'),
                            t('valueTrapReviews.reviewCols.lossPct'),
                            t('valueTrapReviews.reviewCols.escalation'),
                            t('valueTrapReviews.reviewCols.daysOpen'),
                            '',
                            t('valueTrapReviews.reviewCols.status'),
                        ].map((h, i) => (
                            <div key={i} className="text-[10px] font-bold uppercase tracking-wider text-slate-500 dark:text-slate-400">
                                {h}
                            </div>
                        ))}
                    </div>

                    {/* Empty state for open */}
                    {reviews.length === 0 && statusFilter === 'open' && (
                        <div className="px-5 py-10 text-center text-slate-400 dark:text-slate-500 text-sm">
                            {t('valueTrapReviews.noOpenReviews')}
                        </div>
                    )}

                    {/* Review rows */}
                    {reviews.map((review) => {
                        const isExpanded = expandedId === review.id;
                        const ctx = contextMap[review.id] ?? null;

                        return (
                            <div
                                key={review.id}
                                className="border-b border-slate-100 dark:border-slate-800/50 last:border-b-0"
                            >
                                {/* Row */}
                                <div
                                    className={[
                                        'grid gap-3 px-5 py-3.5 cursor-pointer transition-colors items-center',
                                        'hover:bg-slate-50/60 dark:hover:bg-slate-800/20',
                                        review.overdue ? 'bg-red-50/60 dark:bg-red-900/10' : '',
                                    ].join(' ')}
                                    style={{ gridTemplateColumns: '2.2fr 1fr 1.6fr 0.9fr 0.8fr 1fr' }}
                                    onClick={() => (isExpanded ? setExpandedId(null) : openReviewForm(review))}
                                >
                                    {/* Col 1: Asset */}
                                    <div>
                                        <div className="text-[13px] font-semibold text-slate-800 dark:text-slate-100">
                                            {review.asset_name || review.asset_id}
                                        </div>
                                        <div className="text-[10.5px] font-mono text-slate-400 dark:text-slate-500 mt-0.5">
                                            {review.asset_id}
                                        </div>
                                    </div>

                                    {/* Col 2: Loss % */}
                                    <div className="font-mono font-bold text-[15px] text-rose-700 dark:text-rose-400">
                                        {pct(review.unrealized_return_pct)}
                                    </div>

                                    {/* Col 3: Escalation ladder */}
                                    <EscalationLadder review={review} />

                                    {/* Col 4: Days open */}
                                    <div className={[
                                        'font-mono text-xs',
                                        review.overdue
                                            ? 'text-rose-700 dark:text-rose-400 font-bold'
                                            : 'text-slate-600 dark:text-slate-300',
                                    ].join(' ')}>
                                        {review.days_open != null ? `${review.days_open}d` : '—'}
                                        {review.overdue && (
                                            <span className="ml-1.5 inline-flex items-center text-[9.5px] font-bold px-1.5 py-0.5 rounded bg-rose-100 dark:bg-rose-900/30 text-rose-800 dark:text-rose-300">
                                                {t('valueTrapReviews.overdue')}
                                            </span>
                                        )}
                                    </div>

                                    {/* Col 5: Spacer */}
                                    <div />

                                    {/* Col 6: Status + chevron */}
                                    <div className="flex items-center gap-2">
                                        <span className={[
                                            'inline-flex items-center text-[9.5px] font-bold px-2 py-0.5 rounded',
                                            review.status === 'open' && !review.overdue
                                                ? 'bg-slate-100 dark:bg-slate-800 text-slate-500 dark:text-slate-400'
                                                : review.overdue
                                                    ? 'bg-rose-100 dark:bg-rose-900/30 text-rose-800 dark:text-rose-300'
                                                    : 'bg-emerald-100 dark:bg-emerald-900/30 text-emerald-800 dark:text-emerald-400',
                                        ].join(' ')}>
                                            {review.status === 'open' ? t('valueTrapReviews.statusOpen') : t('valueTrapReviews.statusRuled')}
                                        </span>
                                        <svg
                                            className={`w-4 h-4 text-slate-400 dark:text-slate-500 transition-transform ${isExpanded ? 'rotate-90' : ''}`}
                                            fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2} aria-hidden
                                        >
                                            <path strokeLinecap="round" strokeLinejoin="round" d="M9 5l7 7-7 7" />
                                        </svg>
                                    </div>
                                </div>

                                {/* Expanded detail */}
                                {isExpanded && (
                                    <div className="px-5 pb-6 pt-3 bg-slate-50/40 dark:bg-slate-900/10 border-t border-slate-100 dark:border-slate-800/50">
                                        {/* Context loading / error */}
                                        {contextLoading[review.id] && (
                                            <div className="mb-3 text-xs text-slate-400 dark:text-slate-500 italic">
                                                {t('valueTrapReviews.loadingContext')}
                                            </div>
                                        )}
                                        {contextError[review.id] && (
                                            <div className="mb-3 text-xs text-red-500 dark:text-red-400">
                                                {contextError[review.id]}
                                            </div>
                                        )}

                                        {ctx && (
                                            <>
                                                {/* Stale banner */}
                                                {ctx.position?.freshness?.fresh === false && (
                                                    <StaleBanner
                                                        priceDate={ctx.position.price_date ?? ctx.position.snapshot_date ?? '—'}
                                                    />
                                                )}

                                                {/* Facts strip */}
                                                {ctx.position && <FactsStrip context={ctx} />}

                                                {/* Memo card */}
                                                <MemoCard
                                                    state={ctx.memo_linkage.state}
                                                    memos={ctx.memo_linkage.memos}
                                                    displayText={ctx.memo_linkage.display_text}
                                                    assetId={ctx.asset_id}
                                                    linkageAck={form.linkage_ack}
                                                    onLinkageAckChange={(v) => setForm({ ...form, linkage_ack: v })}
                                                    onConfirmNoMemo={handleConfirmNoMemo}
                                                    confirmingNoMemo={!!confirmingNoMemo[ctx.asset_id]}
                                                />

                                                {/* Analysis blocks */}
                                                {ctx.lot_detail && (
                                                    <LotDetailBlock
                                                        lotDetail={ctx.lot_detail}
                                                        costPriceUnit={ctx.position?.cost_price_unit ?? null}
                                                        currency={ctx.position?.currency ?? 'CNY'}
                                                    />
                                                )}
                                                <DecisionHistoryBlock
                                                    history={ctx.decision_history}
                                                    total={ctx.decision_history_total ?? ctx.decision_history.length}
                                                />

                                                {/* Case file link */}
                                                <a
                                                    href="#"
                                                    onClick={(e) => { e.preventDefault(); handleOpenCaseFile(ctx.case_file.asset_id); }}
                                                    className="inline-flex items-center gap-1 my-3 text-[11px] text-blue-600 dark:text-blue-400 hover:underline"
                                                >
                                                    {t('valueTrapReviews.openCaseFile')}
                                                </a>
                                            </>
                                        )}

                                        {/* ── Structured review (questions doc) ── */}
                                        <div className="border border-slate-200 dark:border-border-dark rounded-xl bg-slate-50 dark:bg-slate-900/30 px-6 py-5 mb-3.5">
                                            <div className="flex items-center justify-between mb-1.5">
                                                <div className="text-[13px] font-bold text-slate-900 dark:text-slate-100">
                                                    {t('valueTrapReviews.structuredReview.title')}
                                                </div>
                                                <button
                                                    onClick={() => handleDraft(review)}
                                                    disabled={draftLoading || !canDraft || !isPriceFresh}
                                                    title={
                                                        !isPriceFresh
                                                            ? t('valueTrapReviews.structuredReview.draftBlockedStale')
                                                            : !canDraft
                                                                ? t('valueTrapReviews.structuredReview.draftClearFields')
                                                                : t('valueTrapReviews.structuredReview.draftHint')
                                                    }
                                                    className="inline-flex items-center gap-1.5 px-3.5 py-1.5 rounded-lg text-[11.5px] font-bold border border-indigo-400 dark:border-indigo-600 text-indigo-600 dark:text-indigo-400 bg-indigo-500/8 hover:bg-indigo-500/15 disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
                                                    style={{ background: 'rgb(99 102 241 / 0.08)' }}
                                                >
                                                    <svg className="w-3.5 h-3.5" fill="currentColor" viewBox="0 0 20 20" aria-hidden>
                                                        <path d="M15.98 1.804a1 1 0 00-1.96 0l-.24 1.192a1 1 0 01-.784.785l-1.192.238a1 1 0 000 1.962l1.192.238a1 1 0 01.785.785l.238 1.192a1 1 0 001.962 0l.238-1.192a1 1 0 01.785-.785l1.192-.238a1 1 0 000-1.962l-1.192-.238a1 1 0 01-.785-.785l-.238-1.192zM6.949 5.684a1 1 0 00-1.898 0l-.683 2.051a1 1 0 01-.633.633l-2.051.683a1 1 0 000 1.898l2.051.684a1 1 0 01.633.632l.683 2.051a1 1 0 001.898 0l.683-2.051a1 1 0 01.633-.633l2.051-.683a1 1 0 000-1.898l-2.051-.683a1 1 0 01-.633-.633L6.95 5.684z" />
                                                    </svg>
                                                    {draftLoading ? t('valueTrapReviews.structuredReview.drafting') : t('valueTrapReviews.structuredReview.draftWithAI')}
                                                </button>
                                            </div>

                                            {/* LLM unavailable danger strip (503) */}
                                            {draftIs503 && draftError && (
                                                <div className="flex items-center gap-2 mt-2 mb-3 px-3 py-2 rounded-lg bg-rose-100 dark:bg-rose-900/30 text-[11.5px] text-rose-800 dark:text-rose-300">
                                                    <svg className="w-4 h-4 flex-shrink-0" fill="currentColor" viewBox="0 0 20 20" aria-hidden>
                                                        <path fillRule="evenodd" d="M18 10a8 8 0 11-16 0 8 8 0 0116 0zm-7 4a1 1 0 11-2 0 1 1 0 012 0zm-1-9a1 1 0 00-1 1v4a1 1 0 102 0V6a1 1 0 00-1-1z" clipRule="evenodd" />
                                                    </svg>
                                                    {t('valueTrapReviews.structuredReview.noLlmKey')}
                                                </div>
                                            )}
                                            {/* Other draft errors */}
                                            {!draftIs503 && draftError && (
                                                <p className="mt-2 mb-3 text-xs text-amber-600 dark:text-amber-400 bg-amber-50 dark:bg-amber-900/20 rounded px-3 py-2 border border-amber-200 dark:border-amber-800">
                                                    {draftError}
                                                </p>
                                            )}

                                            {/* Q1 */}
                                            <div className="pt-4 border-t border-slate-200/60 dark:border-slate-700/40">
                                                <div className="text-[10.5px] font-bold font-mono text-blue-600 dark:text-blue-400 mb-0.5">01</div>
                                                <div className="text-[13px] font-semibold text-slate-800 dark:text-slate-100 mb-2">
                                                    {t('valueTrapReviews.questions.q1Title')}
                                                </div>
                                                <textarea
                                                    className={INPUT_CLS}
                                                    rows={6}
                                                    style={{ overflow: 'hidden', minHeight: '9rem', resize: 'vertical' }}
                                                    placeholder={t('valueTrapReviews.questions.q1Placeholder')}
                                                    value={form.thesis_restated}
                                                    onInput={(e) => {
                                                        const t = e.currentTarget;
                                                        t.style.height = 'auto';
                                                        t.style.height = `${t.scrollHeight}px`;
                                                    }}
                                                    onChange={(e) => setForm({ ...form, thesis_restated: e.target.value })}
                                                />
                                            </div>

                                            {/* Q2 */}
                                            <div className="pt-4 border-t border-slate-200/60 dark:border-slate-700/40">
                                                <div className="text-[10.5px] font-bold font-mono text-blue-600 dark:text-blue-400 mb-0.5">02</div>
                                                <div className="text-[13px] font-semibold text-slate-800 dark:text-slate-100 mb-1">
                                                    {t('valueTrapReviews.questions.q2Title')}
                                                </div>
                                                <div className="text-[10.5px] font-mono text-slate-400 dark:text-slate-500 mb-2">
                                                    {t('valueTrapReviews.questions.q2Hint')}
                                                </div>
                                                <textarea
                                                    className={INPUT_CLS}
                                                    rows={6}
                                                    style={{ overflow: 'hidden', minHeight: '9rem', resize: 'vertical' }}
                                                    placeholder={t('valueTrapReviews.questions.q2Placeholder')}
                                                    value={form.falsification_check}
                                                    onInput={(e) => {
                                                        const t = e.currentTarget;
                                                        t.style.height = 'auto';
                                                        t.style.height = `${t.scrollHeight}px`;
                                                    }}
                                                    onChange={(e) => setForm({ ...form, falsification_check: e.target.value })}
                                                />
                                            </div>

                                            {/* Q3 */}
                                            <div className="pt-4 border-t border-slate-200/60 dark:border-slate-700/40">
                                                <div className="text-[10.5px] font-bold font-mono text-blue-600 dark:text-blue-400 mb-0.5">03</div>
                                                <div className="text-[13px] font-semibold text-slate-800 dark:text-slate-100 mb-1">
                                                    {t('valueTrapReviews.questions.q3Title')}
                                                </div>
                                                <div className="text-[10.5px] font-mono text-slate-400 dark:text-slate-500 mb-2">
                                                    {t('valueTrapReviews.questions.q3Hint')}
                                                </div>
                                                <textarea
                                                    className={INPUT_CLS}
                                                    rows={6}
                                                    style={{ overflow: 'hidden', minHeight: '9rem', resize: 'vertical' }}
                                                    placeholder={t('valueTrapReviews.questions.q3Placeholder')}
                                                    value={form.would_buy_today}
                                                    onInput={(e) => {
                                                        const t = e.currentTarget;
                                                        t.style.height = 'auto';
                                                        t.style.height = `${t.scrollHeight}px`;
                                                    }}
                                                    onChange={(e) => setForm({ ...form, would_buy_today: e.target.value })}
                                                />
                                            </div>
                                        </div>

                                        {/* ── Ruling card ── */}
                                        <div className="border border-slate-200 dark:border-border-dark rounded-xl px-5 py-4">
                                            <div className="flex items-end gap-4 flex-wrap">
                                                <div className="flex-1 min-w-[180px]">
                                                    <label className="block text-[10px] font-bold uppercase tracking-wider text-slate-500 dark:text-slate-400 mb-1.5">
                                                        {t('valueTrapReviews.rulingCard.ruling')} <span className="text-red-500">*</span>
                                                    </label>
                                                    <select
                                                        className={INPUT_CLS}
                                                        value={form.ruling}
                                                        disabled={!isPriceFresh}
                                                        onChange={(e) => setForm({ ...form, ruling: e.target.value as RulingValue })}
                                                    >
                                                        <option value="" disabled>{t('valueTrapReviews.rulingCard.selectRuling')}</option>
                                                        {getRulingOptions(t).map((opt) => (
                                                            <option key={opt.value} value={opt.value}>{opt.label}</option>
                                                        ))}
                                                    </select>
                                                </div>

                                                {/* Hold → next review date (required) */}
                                                {form.ruling === 'hold_with_thesis' && (
                                                    <div className="flex-1 min-w-[180px]">
                                                        <label className="block text-[10px] font-bold uppercase tracking-wider text-slate-500 dark:text-slate-400 mb-1.5">
                                                            {t('valueTrapReviews.rulingCard.nextReviewDate')} <span className="text-red-500">*</span>
                                                        </label>
                                                        <input
                                                            type="date"
                                                            className={INPUT_CLS}
                                                            value={form.next_review_date}
                                                            onChange={(e) => setForm({ ...form, next_review_date: e.target.value })}
                                                        />
                                                    </div>
                                                )}

                                                {/* Trim / Liquidate → next review date (optional) */}
                                                {form.ruling !== '' && form.ruling !== 'hold_with_thesis' && (
                                                    <div className="flex-1 min-w-[180px]">
                                                        <label className="block text-[10px] font-bold uppercase tracking-wider text-slate-500 dark:text-slate-400 mb-1.5">
                                                            {t('valueTrapReviews.rulingCard.nextReviewDate')}
                                                        </label>
                                                        <input
                                                            type="date"
                                                            className={INPUT_CLS}
                                                            value={form.next_review_date}
                                                            onChange={(e) => setForm({ ...form, next_review_date: e.target.value })}
                                                        />
                                                    </div>
                                                )}

                                                <div className="flex items-end gap-2">
                                                    <button
                                                        onClick={() => setExpandedId(null)}
                                                        className="px-4 py-2 text-sm font-semibold text-slate-600 dark:text-slate-300 hover:bg-slate-100 dark:hover:bg-slate-800 rounded-lg transition-colors"
                                                    >
                                                        {t('valueTrapReviews.rulingCard.cancel')}
                                                    </button>
                                                    <button
                                                        onClick={() => handleSubmitRuling(review)}
                                                        disabled={submitDisabled}
                                                        className="px-4 py-2 bg-blue-600 hover:bg-blue-700 disabled:bg-blue-400 text-white rounded-lg text-sm font-bold transition-colors"
                                                    >
                                                        {submitting ? t('valueTrapReviews.rulingCard.saving') : t('valueTrapReviews.rulingCard.saveRuling')}
                                                    </button>
                                                </div>
                                            </div>

                                            {/* Liquidate adversarial ack */}
                                            {form.ruling === 'liquidate' && (
                                                <label className="flex items-center gap-2 mt-3 text-sm text-red-700 dark:text-red-400 bg-red-50 dark:bg-red-900/10 border border-red-200 dark:border-red-900/30 rounded-lg px-3 py-2 cursor-pointer">
                                                    <input
                                                        type="checkbox"
                                                        className="w-4 h-4 accent-red-600"
                                                        checked={form.adversarial_ack}
                                                        onChange={(e) => setForm({ ...form, adversarial_ack: e.target.checked })}
                                                    />
                                                    {t('valueTrapReviews.rulingCard.adversarialAck')}
                                                </label>
                                            )}

                                            {/* Save explain line */}
                                            {saveExplain.text && (
                                                <div className={`mt-2.5 text-[11px] font-mono ${saveExplain.blocked ? 'text-rose-700 dark:text-rose-400' : 'text-slate-400 dark:text-slate-500'}`}>
                                                    {saveExplain.text}
                                                </div>
                                            )}

                                            {/* Submit error */}
                                            {submitError && (
                                                <p className="mt-2 text-sm text-red-600 dark:text-red-400">{submitError}</p>
                                            )}
                                        </div>
                                    </div>
                                )}
                            </div>
                        );
                    })}
                </div>
            )}

            {/* ── Stamp row ── */}
            <div className="flex items-center gap-3.5 py-2 font-mono text-[10px] text-slate-400 dark:text-slate-500">
                <span>{t('valueTrapReviews.stampVersion')}</span>
                <span className="w-[3px] h-[3px] rounded-full bg-slate-300 dark:bg-slate-700 flex-shrink-0" />
                <span>{t('valueTrapReviews.stampContract')}</span>
                <span className="w-[3px] h-[3px] rounded-full bg-slate-300 dark:bg-slate-700 flex-shrink-0" />
                <span>{t('valueTrapReviews.stampMechanism')}</span>
            </div>
        </div>
    );
};
