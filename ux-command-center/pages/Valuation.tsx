import React, { useEffect, useState } from 'react';
import { Trans, useTranslation } from 'react-i18next';
import type { TFunction } from 'i18next';
import { api } from '../src/services/api';

// ── Types ──────────────────────────────────────────────────────────────────

interface WatchlistEntry {
    ticker: string;
    display_name: string;
    asset_type: string;
    note: string | null;
    added_at: string;
}

interface ValuationSnapshot {
    id: number;
    snapshot_date: string;
    ticker: string;
    display_name: string | null;
    row_kind: 'holding' | 'tracked_index' | 'watchlist' | string;
    linked_ticker: string | null;
    asset_id: string | null;
    asset_class: string;
    pe_ttm: number | null;
    pe_forward: number | null;
    pb_ratio: number | null;
    peg_ratio: number | null;
    fcf_yield: number | null;
    dividend_yield: number | null;
    ev_ebitda: number | null;
    sec_yield: number | null;
    pe_ttm_pct: number | null;
    pe_fwd_pct: number | null;
    pb_pct: number | null;
    percentile_value: number | null;
    percentile_metric: string | null;
    pct_years: number | null;
    valuation_signal: string | null;
    signal_basis: string | null;
    rate_adjustment_factor: number | null;
    data_source: string | null;
    is_estimable: boolean;
    notes: string | null;
    created_at: string | null;
}

// Same shape as TransactionBrowser's exporter: quote only when the value would
// otherwise break the row, so unquoted cells stay diff-readable.
const toCsv = (rows: ValuationSnapshot[]) => {
    const headers = [
        'Snapshot Date', 'Ticker', 'Name', 'Kind', 'Asset Class',
        'PE TTM', 'PE Forward', 'PB', 'PEG', 'FCF Yield', 'Dividend Yield',
        'EV/EBITDA', 'SEC Yield', 'Percentile', 'Percentile Metric', 'Years',
        'Signal', 'Signal Basis', 'Estimable', 'Data Source', 'Notes',
    ];
    const esc = (v: unknown) => {
        const s = v == null ? '' : String(v);
        return s.includes(',') || s.includes('"') || s.includes('\n') ? `"${s.replace(/"/g, '""')}"` : s;
    };
    const lines = rows.map((r) => [
        r.snapshot_date, r.ticker, r.display_name ?? '', r.row_kind, r.asset_class,
        r.pe_ttm ?? '', r.pe_forward ?? '', r.pb_ratio ?? '', r.peg_ratio ?? '',
        r.fcf_yield ?? '', r.dividend_yield ?? '', r.ev_ebitda ?? '', r.sec_yield ?? '',
        r.percentile_value ?? '', r.percentile_metric ?? '', r.pct_years ?? '',
        r.valuation_signal ?? '', r.signal_basis ?? '', r.is_estimable, r.data_source ?? '',
        r.notes ?? '',
    ].map(esc).join(','));
    return [headers.join(','), ...lines].join('\n');
};

interface MacroData {
    us10y: number;
    rate_adjustment_factor: number;
    source: string;
    fallback_used: boolean;
    usd_cny?: number | null;
}

interface AddWatchlistForm {
    ticker: string;
    display_name: string;
    asset_type: string;
    note: string;
}

// ── Signal pills (Tailwind-only, no inline styles) ─────────────────────────

const SIGNAL_PILL: Record<string, string> = {
    LOW:  'bg-blue-100 text-blue-700 dark:bg-blue-900/40 dark:text-blue-300',
    FAIR: 'bg-emerald-100 text-emerald-700 dark:bg-emerald-900/40 dark:text-emerald-300',
    ELEVATED: 'bg-amber-100 text-amber-700 dark:bg-amber-900/40 dark:text-amber-300',
    HIGH: 'bg-rose-100 text-rose-700 dark:bg-rose-900/40 dark:text-rose-300',
    'N/A': 'bg-slate-100 text-slate-400 dark:bg-slate-800 dark:text-slate-500',
};

/** 'N/A' here is a ROW_BG/SIGNAL_PILL lookup key (must match those objects' literal
 *  'N/A' keys), not display text — SignalPill renders its own translated fallback.
 *  Pulled out of the JSX child-expression tree for the same reason as staleChipStyle()
 *  in ValueTrapReviews.tsx: the ratchet's rule-3 scan can't tell a lookup-key ternary
 *  from a display-string one once both sit inside the same .map() callback. */
function signalOrDefault(signal: string | null | undefined): string {
    return signal || 'N/A';
}

const ROW_BG: Record<string, string> = {
    LOW:  '',
    FAIR:  '',
    ELEVATED: 'bg-amber-50/40 dark:bg-amber-950/10',
    HIGH: 'bg-rose-50/60 dark:bg-rose-950/10',
    'N/A': '',
};

function SignalPill({ signal, signalBasis }: Readonly<{ signal: string | null; signalBasis?: string | null }>) {
    const { t } = useTranslation('valuation');
    const s = signal || 'N/A';
    const cls = SIGNAL_PILL[s] ?? SIGNAL_PILL['N/A'];
    const noConfig = s === 'N/A' && signalBasis === 'no_reference_config';
    // '降级判断' is a backend signal_basis match key (data value), not display text — never translate it.
    const isFallback = signalBasis?.includes('降级判断') ?? false;
    return (
        <span className="inline-flex items-center gap-1">
            <span
                className={`inline-flex px-2 py-0.5 rounded text-[10px] font-bold tracking-wide ${cls} ${noConfig ? 'cursor-help' : ''}`}
                title={noConfig ? t('signalPill.missingThreshold') : undefined}
            >
                {s}
            </span>
            {isFallback && (
                <span
                    className="text-slate-400 dark:text-slate-500 text-[11px] cursor-help"
                    title={signalBasis ?? t('signalPill.percentileMissingFallback')}
                >
                    ⓘ
                </span>
            )}
        </span>
    );
}

function DataSourceCell({ source }: { source: string | null | undefined }) {
    const { t } = useTranslation('valuation');
    if (!source || source === 'none') {
        return (
            <span className="inline-flex px-1.5 py-0.5 rounded text-[10px] font-semibold bg-amber-100 text-amber-700 dark:bg-amber-900/30 dark:text-amber-400">
                {t('dataUnavailable')}
            </span>
        );
    }
    return <span className="text-xs text-slate-500 dark:text-slate-400">{source}</span>;
}

// ── Metric formatters ──────────────────────────────────────────────────────

function ValHist({ val, pct, invert = false }: { val: string, pct: number | null, invert?: boolean }) {
    if (val === '—') return <span className="text-slate-400">—</span>;
    if (pct == null) return <span className="font-mono text-[11px] font-medium text-slate-600 dark:text-slate-400">{val}</span>;
    
    let colorClass = 'bg-blue-500';
    if (invert) {
        if (pct > 80) colorClass = 'bg-emerald-500';
        else if (pct < 20) colorClass = 'bg-rose-500';
        else if (pct < 40) colorClass = 'bg-amber-400';
    } else {
        if (pct > 80) colorClass = 'bg-rose-500';
        else if (pct > 60) colorClass = 'bg-amber-400';
        else if (pct < 20) colorClass = 'bg-blue-500';
        else colorClass = 'bg-emerald-500';
    }

    return (
        <div className="inline-flex items-center justify-end gap-2.5 w-full">
            <div className="w-12 h-1.5 rounded-full bg-slate-100 dark:bg-slate-800 overflow-hidden flex-shrink-0">
                <div className={`h-full ${colorClass}`} style={{ width: `${Math.max(0, Math.min(100, pct))}%` }}></div>
            </div>
            <span className="font-mono text-[11px] font-medium min-w-[3ch] text-right">{val}</span>
        </div>
    );
}

function PEHist({ snap }: { snap: ValuationSnapshot }) {
    const hasHistory = (snap.pct_years ?? 0) >= 1;
    if (snap.asset_class === 'US_BOND_ETF') {
        const val = snap.sec_yield != null ? `${snap.sec_yield.toFixed(2)}%` : '—';
        return <ValHist val={val} pct={hasHistory ? snap.percentile_value : null} invert={true} />;
    }
    const val = snap.pe_ttm != null ? snap.pe_ttm.toFixed(1) : (snap.pe_forward != null ? `Fwd ${snap.pe_forward.toFixed(1)}` : '—');
    return <ValHist val={val} pct={hasHistory ? snap.percentile_value : null} invert={false} />;
}

function PctText({ snap }: { snap: ValuationSnapshot }) {
    const { t } = useTranslation('valuation');
    const pct = snap.percentile_value;
    const years = snap.pct_years ?? 0;

    if (pct == null || years < 1) {
        const days = Math.round(years * 365);
        return (
            <span className="text-[10px] text-slate-400 dark:text-slate-500 whitespace-nowrap">
                {days > 0 ? t('accumulatingDays', { days }) : t('accumulating')}
            </span>
        );
    }

    const isHigh = pct > 80;
    const n = Math.round(pct);
    // English ordinal suffix (1st/2nd/3rd/4th...) — algorithmic, not display prose; not translated.
    const suffix = n % 10 === 1 && n !== 11 ? 'st' : n % 10 === 2 && n !== 12 ? 'nd' : n % 10 === 3 && n !== 13 ? 'rd' : 'th';
    const yrs = Math.round(years);
    return (
        <span className={`font-mono text-[11px] whitespace-nowrap ${isHigh ? 'text-rose-600 dark:text-rose-400 font-semibold' : 'text-slate-600 dark:text-slate-400'}`}>
            {t('percentileYears', { n, suffix, years: yrs })}
        </span>
    );
}

// ── Shared table styles ────────────────────────────────────────────────────

const TH   = 'px-4 py-2.5 text-left text-[10px] font-bold text-slate-500 dark:text-slate-400 uppercase tracking-wider';
const TH_R = 'px-4 py-2.5 text-right text-[10px] font-bold text-slate-500 dark:text-slate-400 uppercase tracking-wider';
const TD   = 'px-4 py-2.5 text-sm text-slate-700 dark:text-slate-300';
const TD_MONO = 'px-4 py-2.5 text-sm font-mono font-semibold text-slate-800 dark:text-slate-200';
const TD_R = 'px-4 py-2.5 text-sm text-right font-mono text-slate-800 dark:text-slate-200';
const TD_SM = 'px-4 py-2.5 text-xs text-slate-500 dark:text-slate-400';

const CARD = 'rounded-xl border border-slate-200 dark:border-border-dark bg-white dark:bg-card-dark shadow-sm overflow-hidden';

// ── Section card ───────────────────────────────────────────────────────────

function SectionCard({ title, count, hint, action, children }: Readonly<{
    title: string;
    count?: number;
    hint?: string;
    action?: React.ReactNode;
    children: React.ReactNode;
}>) {
    return (
        <div className={CARD}>
            <div className="flex items-center justify-between px-5 py-4 border-b border-slate-100 dark:border-slate-800">
                <div className="flex items-center gap-2.5">
                    <h2 className="text-[15px] font-bold text-slate-800 dark:text-slate-100">{title}</h2>
                    {count != null && (
                        <span className="text-[11px] font-mono font-medium text-slate-500 dark:text-slate-400 bg-slate-100 dark:bg-slate-800 px-1.5 py-0.5 rounded">
                            {count}
                        </span>
                    )}
                    {hint && <span className="text-[11px] text-slate-400 dark:text-slate-500 ml-1">{hint}</span>}
                </div>
                <div className="flex items-center gap-2">
                    {action}
                </div>
            </div>
            {children}
        </div>
    );
}

// ── Holdings table (持仓) ──────────────────────────────────────────────────

function HoldingsTable({ rows }: Readonly<{ rows: ValuationSnapshot[] }>) {
    const { t } = useTranslation('valuation');
    const estimable    = rows.filter(r => r.is_estimable);
    const nonEstimable = rows.filter(r => !r.is_estimable);

    if (rows.length === 0) {
        return (
            <div className="px-5 py-10 text-center text-sm text-slate-400 dark:text-slate-500">
                {t('holdings.empty')}
            </div>
        );
    }
    return (
        <div>
            {estimable.length > 0 && (
                <>
                <div className="overflow-x-auto">
                    <table className="min-w-full">
                        <thead>
                            <tr className="border-b border-slate-100 dark:border-slate-800">
                                <th className={TH}>{t('holdings.cols.tickerName')}</th>
                                <th className={TH}>{t('holdings.cols.type')}</th>
                                <th className={TH_R}>{t('holdings.cols.currentValuation')}</th>
                                <th className={TH_R}>{t('holdings.cols.percentile')}</th>
                                <th className={TH}>{t('holdings.cols.signal')}</th>
                                <th className={TH}>{t('holdings.cols.source')}</th>
                            </tr>
                        </thead>
                        <tbody>
                            {estimable.map(snap => {
                                const sig = signalOrDefault(snap.valuation_signal);
                                const isAmznDistorted = snap.ticker === 'AMZN';
                                const isVoo = snap.ticker === 'VOO';
                                const vooFwdNote = isVoo && snap.pe_forward != null
                                    ? t('holdings.vooFwdNote', { value: snap.pe_forward.toFixed(1) })
                                    : null;
                                return (
                                    <tr key={snap.ticker}
                                        className={`border-t border-slate-50 dark:border-slate-800/50 ${ROW_BG[sig] ?? ''}`}>
                                        <td className={TD_MONO}>
                                            <span>{snap.display_name || snap.ticker}</span>
                                            {isAmznDistorted && (
                                                <span className="ml-1.5 text-[9px] font-bold text-amber-600 dark:text-amber-400 bg-amber-50 dark:bg-amber-950/40 border border-amber-200 dark:border-amber-700 px-1 py-0.5 rounded"
                                                    title={t('holdings.peTtmDistortedTitle')}>
                                                    {t('holdings.peTtmDistorted')}
                                                </span>
                                            )}
                                        </td>
                                        <td className={TD_SM}>{snap.asset_class}</td>
                                        <td className={TD_R}><PEHist snap={snap} /></td>
                                        <td className={TD_R}><PctText snap={snap} /></td>
                                        <td className={TD}>
                                            <SignalPill signal={snap.valuation_signal} signalBasis={snap.signal_basis} />
                                            {vooFwdNote && (
                                                <div className="mt-0.5 text-[9px] text-slate-400 dark:text-slate-500" title={t('holdings.vooFwdNoteTitle')}>
                                                    {vooFwdNote}
                                                </div>
                                            )}
                                        </td>
                                        <td className={TD_SM}><DataSourceCell source={snap.data_source} /></td>
                                    </tr>
                                );
                            })}
                        </tbody>
                    </table>
                </div>
                <div className="border-t border-slate-100 dark:border-slate-800 px-5 py-2.5 bg-slate-50/60 dark:bg-slate-900/20">
                    <p className="text-[10px] text-slate-400 dark:text-slate-500">
                        {t('holdings.footnote')}
                    </p>
                </div>
                </>
            )}
            {nonEstimable.length > 0 && (
                <div className="border-t border-slate-100 dark:border-slate-800 bg-slate-50/50 dark:bg-slate-900/20 px-5 pt-3.5 pb-4">
                    <div className="flex items-center gap-2 mb-3">
                        <span className="text-[11px] font-bold text-slate-700 dark:text-slate-300">{t('holdings.untracked')}</span>
                        <span className="text-[10px] font-medium bg-slate-200 dark:bg-slate-700 text-slate-600 dark:text-slate-300 px-1.5 py-0.5 rounded">{nonEstimable.length}</span>
                        <span className="text-[10px] text-slate-400 dark:text-slate-500 ml-1">{t('holdings.noValuationSource')}</span>
                    </div>
                    <div className="flex flex-wrap gap-1.5">
                        {nonEstimable.map(s => (
                            <span key={s.ticker}
                                className="text-[10px] font-mono bg-white dark:bg-card-dark border border-slate-200 dark:border-slate-700 px-2 py-0.5 rounded text-slate-500 dark:text-slate-400 whitespace-nowrap">
                                {s.ticker}
                            </span>
                        ))}
                    </div>
                </div>
            )}
        </div>
    );
}

// ── Tracked indexes table (跟踪指数) ──────────────────────────────────────

function TrackedIndexTable({ rows }: Readonly<{ rows: ValuationSnapshot[] }>) {
    const { t } = useTranslation('valuation');
    if (rows.length === 0) {
        return (
            <div className="px-5 py-10 text-center text-sm text-slate-400 dark:text-slate-500">
                {t('trackedIndex.empty')}
            </div>
        );
    }
    return (
        <div className="overflow-x-auto">
            <table className="min-w-full">
                <thead>
                    <tr className="border-b border-slate-100 dark:border-slate-800">
                        <th className={TH}>{t('trackedIndex.cols.index')}</th>
                        <th className={TH}>{t('trackedIndex.cols.linkedFund')}</th>
                        <th className={TH_R}>{t('trackedIndex.cols.peTtm')}</th>
                        <th className={TH_R}>{t('trackedIndex.cols.pb')}</th>
                        <th className={TH_R}>{t('trackedIndex.cols.percentile')}</th>
                        <th className={TH}>{t('trackedIndex.cols.signal')}</th>
                        <th className={TH}>{t('trackedIndex.cols.source')}</th>
                    </tr>
                </thead>
                <tbody>
                    {rows.map(snap => {
                        const sig = signalOrDefault(snap.valuation_signal);
                        return (
                            <tr key={snap.ticker}
                                className={`border-t border-slate-50 dark:border-slate-800/50 ${ROW_BG[sig] ?? ''}`}>
                                <td className={TD_MONO}>{snap.display_name || snap.ticker}</td>
                                <td className={TD_SM}>{snap.linked_ticker || '—'}</td>
                                <td className={TD_R}>{snap.pe_ttm != null ? snap.pe_ttm.toFixed(1) : '—'}</td>
                                <td className={TD_R}>{snap.pb_ratio != null ? snap.pb_ratio.toFixed(2) : '—'}</td>
                                <td className={TD_R}><PctText snap={snap} /></td>
                                <td className={TD}><SignalPill signal={snap.valuation_signal} signalBasis={snap.signal_basis} /></td>
                                <td className={TD_SM}><DataSourceCell source={snap.data_source} /></td>
                            </tr>
                        );
                    })}
                </tbody>
            </table>
            <div className="border-t border-slate-100 dark:border-slate-800 px-5 py-2.5 bg-slate-50/60 dark:bg-slate-900/20">
                <p className="text-[10px] text-slate-400 dark:text-slate-500">
                    {t('trackedIndex.footnote')}
                </p>
            </div>
        </div>
    );
}

// ── Watchlist table (观察列表) ────────────────────────────────────────────

function WatchlistTable({ entries, snapshots, onDelete }: Readonly<{
    entries: WatchlistEntry[];
    snapshots: ValuationSnapshot[];
    onDelete: (ticker: string) => void;
}>) {
    const { t } = useTranslation('valuation');
    if (entries.length === 0) {
        return (
            <div className="px-5 py-10 text-center text-sm text-slate-400 dark:text-slate-500">
                {t('watchlist.empty')}
            </div>
        );
    }
    const snapByTicker = Object.fromEntries(
        snapshots.filter(s => s.row_kind === 'watchlist').map(s => [s.ticker, s])
    );
    return (
        <div className="overflow-x-auto">
            <table className="min-w-full">
                <thead>
                    <tr className="border-b border-slate-100 dark:border-slate-800">
                        <th className={TH}>{t('watchlist.cols.name')}</th>
                        <th className={TH}>{t('watchlist.cols.type')}</th>
                        <th className={TH_R}>{t('watchlist.cols.currentValuation')}</th>
                        <th className={TH_R}>{t('watchlist.cols.percentile')}</th>
                        <th className={TH}>{t('watchlist.cols.signal')}</th>
                        <th className={TH}>{t('watchlist.cols.source')}</th>
                        <th className={TH}></th>
                    </tr>
                </thead>
                <tbody>
                    {entries.map(entry => {
                        const snap = snapByTicker[entry.ticker];
                        const sig = signalOrDefault(snap?.valuation_signal);
                        return (
                            <tr key={entry.ticker}
                                className={`border-t border-slate-50 dark:border-slate-800/50 ${ROW_BG[sig] ?? ''}`}>
                                <td className={TD_MONO}>{entry.display_name || entry.ticker}</td>
                                <td className={TD_SM}>{entry.asset_type}</td>
                                <td className={TD_R}>{snap ? <PEHist snap={snap} /> : '—'}</td>
                                <td className={TD_R}>{snap ? <PctText snap={snap} /> : <span className="text-[10px] text-slate-400">{t('watchlist.awaitingRefresh')}</span>}</td>
                                <td className={TD}>{snap ? <SignalPill signal={snap.valuation_signal} signalBasis={snap.signal_basis} /> : <SignalPill signal={null} />}</td>
                                <td className={TD_SM}><DataSourceCell source={snap?.data_source} /></td>
                                <td className="px-4 py-2.5 text-right">
                                    <button
                                        onClick={() => onDelete(entry.ticker)}
                                        className="text-xs font-semibold text-rose-500 hover:text-rose-700 dark:text-rose-400 dark:hover:text-rose-300 transition-colors"
                                    >
                                        {t('watchlist.remove')}
                                    </button>
                                </td>
                            </tr>
                        );
                    })}
                </tbody>
            </table>
        </div>
    );
}

// ── Add watchlist modal ────────────────────────────────────────────────────

const getAssetTypeOptions = (t: TFunction) => [
    { value: 'US_STOCK',  label: t('assetTypes.usStock') },
    { value: 'US_INDEX',  label: t('assetTypes.usIndex') },
    { value: 'HK_INDEX',  label: t('assetTypes.hkIndex') },
    { value: 'CN_INDEX',  label: t('assetTypes.cnIndex') },
    { value: 'CN_MARKET', label: t('assetTypes.cnMarket') },
];

const INPUT_CLS = 'w-full text-sm px-3 py-2 rounded-lg border border-slate-200 dark:border-border-dark bg-white dark:bg-card-dark text-slate-800 dark:text-slate-200 focus:outline-none focus:ring-2 focus:ring-primary/50';

function AddWatchlistModal({ onClose, onAdded }: Readonly<{
    onClose: () => void;
    onAdded: () => void;
}>) {
    const { t } = useTranslation('valuation');
    const [form, setForm] = useState<AddWatchlistForm>({
        ticker: '', display_name: '', asset_type: 'US_INDEX', note: '',
    });
    const [submitting, setSubmitting] = useState(false);
    const [result, setResult] = useState<string | null>(null);
    const [err, setErr] = useState<string | null>(null);

    const handleSubmit = async (e: React.FormEvent) => {
        e.preventDefault();
        if (!form.ticker.trim() || !form.display_name.trim()) return;
        setSubmitting(true);
        setErr(null);
        try {
            const res = await api.addValuationWatchlist({
                ticker: form.ticker.trim(),
                display_name: form.display_name.trim(),
                asset_type: form.asset_type,
                note: form.note.trim() || undefined,
            });
            const msg = res.backfill_status === 'seeded'
                ? t('addModal.resultSeeded')
                : t('addModal.resultDailyRefresh');
            setResult(msg);
            setTimeout(() => { onAdded(); onClose(); }, 1400);
        } catch (e: any) {
            setErr(e?.message || t('addModal.addFailed'));
        } finally {
            setSubmitting(false);
        }
    };

    return (
        <div className="fixed inset-0 z-50 flex items-center justify-center">
            <div className="absolute inset-0 bg-black/40 dark:bg-black/60" onClick={onClose} />
            <div className={`relative z-10 w-full max-w-md mx-4 p-6 ${CARD}`}>
                <h2 className="text-sm font-bold text-slate-800 dark:text-slate-100 mb-5">{t('addModal.title')}</h2>
                <form onSubmit={handleSubmit} className="space-y-4">
                    <div>
                        <label className="block text-xs font-semibold text-slate-500 dark:text-slate-400 mb-1">{t('addModal.ticker')}</label>
                        <input
                            type="text"
                            value={form.ticker}
                            onChange={e => setForm(f => ({ ...f, ticker: e.target.value }))}
                            placeholder={t('addModal.tickerPlaceholder')}
                            className={INPUT_CLS}
                            required
                        />
                    </div>
                    <div>
                        <label className="block text-xs font-semibold text-slate-500 dark:text-slate-400 mb-1">{t('addModal.name')}</label>
                        <input
                            type="text"
                            value={form.display_name}
                            onChange={e => setForm(f => ({ ...f, display_name: e.target.value }))}
                            placeholder={t('addModal.namePlaceholder')}
                            className={INPUT_CLS}
                            required
                        />
                    </div>
                    <div>
                        <label className="block text-xs font-semibold text-slate-500 dark:text-slate-400 mb-1">{t('addModal.type')}</label>
                        <select
                            value={form.asset_type}
                            onChange={e => setForm(f => ({ ...f, asset_type: e.target.value }))}
                            className={INPUT_CLS}
                        >
                            {getAssetTypeOptions(t).map(o => (
                                <option key={o.value} value={o.value}>{o.label}</option>
                            ))}
                        </select>
                    </div>
                    <div>
                        <label className="block text-xs font-semibold text-slate-500 dark:text-slate-400 mb-1">{t('addModal.note')}</label>
                        <input
                            type="text"
                            value={form.note}
                            onChange={e => setForm(f => ({ ...f, note: e.target.value }))}
                            placeholder={t('addModal.notePlaceholder')}
                            className={INPUT_CLS}
                        />
                    </div>

                    {result && <p className="text-sm text-emerald-600 dark:text-emerald-400">{result}</p>}
                    {err    && <p className="text-sm text-rose-600 dark:text-rose-400">{err}</p>}

                    <div className="flex justify-end gap-3 pt-2">
                        <button
                            type="button"
                            onClick={onClose}
                            className="px-4 py-2 text-sm text-slate-600 dark:text-slate-400 hover:bg-slate-100 dark:hover:bg-slate-800 rounded-lg transition-colors"
                        >
                            {t('addModal.cancel')}
                        </button>
                        <button
                            type="submit"
                            disabled={submitting || !!result}
                            className="px-4 py-2 text-sm bg-primary text-white rounded-lg hover:bg-primary/90 disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
                        >
                            {submitting ? t('addModal.adding') : t('addModal.add')}
                        </button>
                    </div>
                </form>
            </div>
        </div>
    );
}

// ── Main page ──────────────────────────────────────────────────────────────

export const Valuation: React.FC = () => {
    const { t } = useTranslation('valuation');
    const [snapshots, setSnapshots]           = useState<ValuationSnapshot[]>([]);
    const [watchlistEntries, setWatchlistEntries] = useState<WatchlistEntry[]>([]);
    const [macro, setMacro]                   = useState<MacroData | null>(null);
    const [loading, setLoading]               = useState(true);
    const [refreshing, setRefreshing]         = useState(false);
    const [error, setError]                   = useState<string | null>(null);
    const [refreshResult, setRefreshResult]   = useState<string | null>(null);
    const [showAddModal, setShowAddModal]     = useState(false);

    const loadData = async () => {
        setLoading(true);
        setError(null);
        try {
            const [snaps, macroData, wlEntries] = await Promise.all([
                api.getValuationSnapshots(),
                api.getValuationMacro(),
                api.getValuationWatchlist(),
            ]);
            setSnapshots(snaps);
            setMacro(macroData);
            setWatchlistEntries(wlEntries);
        } catch (err: any) {
            setError(err?.message || t('errors.loadFailed'));
        } finally {
            setLoading(false);
        }
    };

    useEffect(() => { loadData(); }, []);

    const handleRefresh = async () => {
        setRefreshing(true);
        setRefreshResult(null);
        try {
            const result = await api.triggerValuationRefresh();
            const failCount = result.failed?.length ?? 0;
            const failNote = failCount > 0 ? t('refresh.failNote', { count: failCount, tickers: result.failed.map((f: any) => f.ticker ?? f).join(', ') }) : '';
            setRefreshResult(t('refresh.resultSummary', { count: result.refreshed_count, status: result.status, failNote }));
            await loadData();
        } catch (err: any) {
            if (err?.message?.includes('409')) {
                setRefreshResult(t('refresh.inProgress'));
            } else if (err?.message?.includes('429')) {
                setRefreshResult(t('refresh.dailyLimitReached'));
            } else {
                setRefreshResult(t('refresh.errorPrefix', { message: err?.message || 'Unknown' }));
            }
        } finally {
            setRefreshing(false);
        }
    };

    const handleDelete = async (ticker: string) => {
        setError(null);
        try {
            await api.deleteValuationWatchlist(ticker);
            await loadData();
        } catch (err: any) {
            setError(t('errors.deleteFailed', { message: err?.message || 'Unknown' }));
        }
    };

    // US_ETF (index ETFs like VOO/QQQ) belong in the Benchmark section, not Holdings
    const holdingRows = snapshots.filter(s => s.row_kind === 'holding' && s.asset_class !== 'US_ETF');
    const etfBenchmarkRows = snapshots.filter(s => s.row_kind === 'holding' && s.asset_class === 'US_ETF');
    const trackedRows = [
        ...snapshots.filter(s => s.row_kind === 'tracked_index'),
        ...etfBenchmarkRows,
    ];

    const lastRefresh = snapshots.reduce<string | null>(
        (l, s) => s.created_at && s.created_at > (l ?? '') ? s.created_at : l,
        null,
    );

    // Exports exactly what the two tables above render, in their display order,
    // so a row in the file can be matched back to a row on screen.
    const handleExportCsv = () => {
        const rows = [...holdingRows, ...trackedRows];
        if (rows.length === 0) return;
        const blob = new Blob([toCsv(rows)], { type: 'text/csv;charset=utf-8;' });
        const url = URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url; a.download = `valuation-${new Date().toISOString().slice(0, 10)}.csv`;
        document.body.appendChild(a); a.click(); document.body.removeChild(a);
        URL.revokeObjectURL(url);
    };
    
    const flaggedHigh = snapshots.filter(s => s.valuation_signal === 'HIGH');
    const flaggedCount = flaggedHigh.length;

    return (
        <div className="p-6 space-y-6 bg-gray-50 dark:bg-background-dark min-h-screen">

            {/* ── Header ─────────────────────────────────────────── */}
            <div className="flex items-center justify-between mb-2">
                <div>
                    <div className="text-[10px] uppercase tracking-widest text-slate-500 dark:text-slate-400 mb-1">{t('breadcrumb')}</div>
                    <h1 className="text-2xl font-bold text-slate-900 dark:text-white">{t('title')}</h1>
                    <p className="text-sm text-slate-500 dark:text-slate-400 mt-0.5">
                        {t('subtitle')}
                    </p>
                </div>
                <div className="flex items-center gap-3">
                    {lastRefresh && (
                        <span className="text-xs font-mono text-slate-400 dark:text-slate-500">
                            {lastRefresh.slice(0, 10)} · {lastRefresh.slice(11, 16)}
                        </span>
                    )}
                    <button
                        onClick={handleExportCsv}
                        disabled={loading || snapshots.length === 0}
                        className="px-4 py-2 bg-white dark:bg-card-dark border border-slate-200 dark:border-slate-700 text-slate-600 dark:text-slate-300 rounded-lg text-sm font-medium hover:bg-slate-50 dark:hover:bg-slate-800 disabled:opacity-50 disabled:cursor-not-allowed transition-colors flex items-center gap-2"
                    >
                        <span className="material-symbols-outlined text-[18px]">download</span>
                        {t('export')}
                    </button>
                    <button
                        onClick={handleRefresh}
                        disabled={refreshing || loading}
                        className="px-4 py-2 bg-primary text-white rounded-lg text-sm font-medium hover:bg-primary/90 disabled:opacity-50 disabled:cursor-not-allowed transition-colors flex items-center gap-2 shadow-[0_4px_12px_-4px_rgba(59,130,246,0.5)]"
                    >
                        <span className="material-symbols-outlined text-[18px]">{refreshing ? 'hourglass_empty' : 'sync'}</span>
                        {refreshing ? t('refreshingLabel') : t('refreshData')}
                    </button>
                </div>
            </div>

            {/* ── Status banners ───────────────────────────────────── */}
            {refreshResult && (
                <div className="px-4 py-3 rounded-lg bg-slate-100 dark:bg-card-dark text-sm text-slate-700 dark:text-slate-300 mb-4">
                    {refreshResult}
                </div>
            )}
            {error && (
                <div className="px-4 py-3 rounded-lg bg-rose-50 dark:bg-rose-900/20 border border-rose-200 dark:border-rose-700 text-sm text-rose-700 dark:text-rose-400 mb-4">
                    {error}
                </div>
            )}

            {loading ? (
                <div className="flex items-center justify-center py-24 text-slate-400 dark:text-slate-500">
                    <div className="text-center">
                        <div className="text-4xl mb-3">📊</div>
                        <div className="text-sm">{t('loadingData')}</div>
                    </div>
                </div>
            ) : (
                <>
                    {/* ── Unified Status Block ───────────────────────────── */}
                    <div className="rounded-xl border border-slate-200 dark:border-slate-800 bg-white dark:bg-card-dark shadow-sm overflow-hidden mb-6">
                        <div className="flex flex-col md:flex-row md:items-stretch border-b border-slate-100 dark:border-slate-800/50">
                            {/* Freshness */}
                            <div className="p-5 flex items-start gap-4 md:w-[30%] border-b md:border-b-0 md:border-r border-slate-100 dark:border-slate-800/50">
                                <div className="mt-1.5 w-2 h-2 rounded-full bg-emerald-500 shadow-[0_0_8px_rgba(16,185,129,0.6)]"></div>
                                <div className="flex flex-col gap-0.5">
                                    <div className="text-[13px] text-slate-700 dark:text-slate-300">
                                        <Trans
                                            t={t}
                                            i18nKey="statusBlock.refreshedItems"
                                            values={{ count: snapshots.length }}
                                            components={{
                                                strong: <strong className="font-semibold text-slate-900 dark:text-white" />,
                                                sep: <span className="text-slate-400 mx-1" />,
                                                ok: <span className="text-emerald-600 dark:text-emerald-400 font-medium" />,
                                            }}
                                        />
                                    </div>
                                    <div className="text-[11px] text-slate-500 dark:text-slate-400">
                                        {t('statusBlock.readersErrors', { fallback: macro?.fallback_used ? t('statusBlock.oneFallback') : t('statusBlock.zeroFallbacks') })}
                                    </div>
                                </div>
                            </div>
                            
                            {/* KPIs */}
                            <div className="flex-1 grid grid-cols-2 md:grid-cols-4 divide-x divide-slate-100 dark:divide-slate-800/50">
                                <div className="p-4 flex flex-col justify-center">
                                    <span className="text-[10px] uppercase tracking-wider font-bold text-slate-400 dark:text-slate-500 mb-0.5">{t('statusBlock.us10y')}</span>
                                    <span className="font-mono text-xl font-bold text-primary">{macro ? `${macro.us10y.toFixed(2)}%` : '—'}</span>
                                    <span className="text-[11px] text-slate-400 dark:text-slate-500 mt-0.5">{t('statusBlock.rateAdjust', { factor: macro ? macro.rate_adjustment_factor.toFixed(3) : '—' })}</span>
                                </div>
                                <div className="p-4 flex flex-col justify-center">
                                    <span className="text-[10px] uppercase tracking-wider font-bold text-slate-400 dark:text-slate-500 mb-0.5">{t('statusBlock.cn10y')}</span>
                                    <span className="font-mono text-xl font-bold text-slate-700 dark:text-slate-200">2.31%</span>
                                    <span className="text-[11px] text-slate-400 dark:text-slate-500 mt-0.5">{macro?.usd_cny ? t('statusBlock.usdCny', { rate: macro.usd_cny.toFixed(4) }) : t('statusBlock.usdCnyUnknown')}</span>
                                </div>
                                <div className="p-4 flex flex-col justify-center">
                                    <span className="text-[10px] uppercase tracking-wider font-bold text-slate-400 dark:text-slate-500 mb-0.5">{t('statusBlock.signalsFlagged')}</span>
                                    <span className="font-mono text-xl font-bold text-amber-600 dark:text-amber-500">
                                        {flaggedCount} <span className="text-slate-400 font-normal text-sm">/ {snapshots.length}</span>
                                    </span>
                                    <span className="text-[11px] text-slate-400 dark:text-slate-500 mt-0.5 truncate">
                                        {t('statusBlock.highTickers', { tickers: flaggedCount > 0 ? flaggedHigh.map(s => s.ticker).slice(0,2).join(', ') : t('statusBlock.none') })}
                                    </span>
                                </div>
                                <div className="p-4 flex flex-col justify-center items-start">
                                    <span className="text-[10px] uppercase tracking-wider font-bold text-slate-400 dark:text-slate-500 mb-1.5">{t('statusBlock.regime')}</span>
                                    <span className="inline-flex items-center px-2 py-0.5 rounded text-[11px] font-semibold bg-slate-100 text-slate-600 dark:bg-slate-800 dark:text-slate-300 w-max">{t('statusBlock.neutral')}</span>
                                    <span className="text-[11px] text-slate-400 dark:text-slate-500 mt-1">{t('statusBlock.marketSentimentCache')}</span>
                                </div>
                            </div>
                        </div>
                        <div className="px-5 py-2.5 bg-slate-50/50 dark:bg-slate-900/20 flex flex-wrap items-center gap-3 text-[10px] font-mono text-slate-400 dark:text-slate-500">
                            <span>
                                <Trans
                                    t={t}
                                    i18nKey="statusBlock.runId"
                                    values={{ value: lastRefresh ? lastRefresh.replace(' ', 'T') + 'Z' : 'N/A' }}
                                    components={{ strong: <strong className="font-medium text-slate-500 dark:text-slate-400" /> }}
                                />
                            </span>
                            <div className="w-1 h-1 rounded-full bg-slate-300 dark:bg-slate-700 hidden sm:block"></div>
                            <span>
                                <Trans
                                    t={t}
                                    i18nKey="statusBlock.cacheAsOf"
                                    values={{ value: lastRefresh ?? 'N/A' }}
                                    components={{ strong: <strong className="font-medium text-slate-500 dark:text-slate-400" /> }}
                                />
                            </span>
                            <span className="ml-auto">{t('statusBlock.tzShanghai')}</span>
                        </div>
                    </div>

                    {/* ── Section 1: 持仓 ──────────────────────────── */}
                    <SectionCard
                        title={t('sections.holdingsTitle')}
                        count={holdingRows.length}
                        hint={t('sections.holdingsHint')}
                    >
                        <HoldingsTable rows={holdingRows} />
                    </SectionCard>

                    {/* ── Section 2: 跟踪指数 ──────────────────────── */}
                    <SectionCard
                        title={t('sections.benchmarkTitle')}
                        count={trackedRows.length}
                        hint={t('sections.benchmarkHint')}
                    >
                        <TrackedIndexTable rows={trackedRows} />
                    </SectionCard>

                    {/* ── Section 3: 观察列表 ──────────────────────── */}
                    <SectionCard
                        title={t('sections.watchlistTitle')}
                        count={watchlistEntries.length}
                        hint={t('sections.watchlistHint')}
                        action={
                            <button
                                onClick={() => setShowAddModal(true)}
                                className="text-xs font-semibold text-primary hover:text-primary/80 px-3 py-1.5 rounded hover:bg-primary/10 transition-colors flex items-center gap-1.5"
                            >
                                <span className="material-symbols-outlined text-[16px]">add</span>
                                {t('sections.addWatchItem')}
                            </button>
                        }
                    >
                        <WatchlistTable
                            entries={watchlistEntries}
                            snapshots={snapshots}
                            onDelete={handleDelete}
                        />
                    </SectionCard>
                </>
            )}

            {/* ── Add watchlist modal ──────────────────────────────── */}
            {showAddModal && (
                <AddWatchlistModal
                    onClose={() => setShowAddModal(false)}
                    onAdded={loadData}
                />
            )}
        </div>
    );
};
