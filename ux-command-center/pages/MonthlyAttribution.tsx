/**
 * MonthlyAttribution — Reports page (Attribution & Flows Program WS-1)
 *
 * Monthly per-asset Δ market-value decomposition — "this month's change is
 * price action or capital flow?" Contract-first against
 * docs/api-specs/attribution.md (Section B: Frontend).
 *
 * Features:
 *   • From/To month range picker (default: latest completed calendar month;
 *     "To" optional — when set, activates the API's range mode)
 *   • Level toggle: top_class (default) → sub_class → asset, with
 *     click-to-drill (per-asset rows are the drill-down floor — no further
 *     navigation, per owner decision in the spec)
 *   • Summary strip: month totals split price / flows / income + the
 *     classified-flows savings numbers from GET /attribution/summary
 *     (single-month mode only — the /summary endpoint is per-month, not
 *     range-aware; hidden while a range is active)
 *   • Waterfall chart: Start (mv_start) → Price → Trade → Transfer → Income →
 *     Residual → End (mv_end) for the current level's totals, currency- and
 *     demo-mode aware
 *   • dq_flag badges on rows whose residual exceeds the spec's threshold,
 *     with dq_reason surfaced as a tooltip + muted asset-level caption
 */
import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { useTranslation, Trans } from 'react-i18next';
import {
    BarChart, Bar, Cell, LabelList, XAxis, YAxis, Tooltip, ResponsiveContainer,
} from 'recharts';
import { api } from '../src/services/api';
import type {
    AttributionLevel,
    AttributionRow,
    AttributionMonthlyResponse,
    AttributionSummaryMonth,
    AttributionSummaryResponse,
} from '../src/services/api/types';
import {
    ActionBtn,
    Card,
    ChipBtn,
    OpsKpi,
    OpsTable,
    Pill,
    Section,
    Toolbar,
} from '../components/operations';
import type { ColDef } from '../components/operations';
import { useCurrency } from '../src/context/useCurrency';
import { usePortfolioFilter } from '../src/context/usePortfolioFilter';
import { useLanguage } from '../src/context/useLanguage';
import { formatPercent } from '../src/utils/formatMoney';
import { localizedClassName } from '../src/utils/localizedClassName';

// ── Helpers ──────────────────────────────────────────────────────────────────

/** Earliest selectable month — reader-era history floor (spec: 2026-01). */
const MIN_MONTH = '2026-01';

/** Latest *completed* calendar month as YYYY-MM (data is typically not fully
 *  settled for the in-progress month). */
function defaultMonth(): string {
    const now = new Date();
    const firstOfThisMonth = new Date(now.getFullYear(), now.getMonth(), 1);
    firstOfThisMonth.setMonth(firstOfThisMonth.getMonth() - 1);
    const y = firstOfThisMonth.getFullYear();
    const m = String(firstOfThisMonth.getMonth() + 1).padStart(2, '0');
    return `${y}-${m}`;
}

type WaterfallKind = 'anchor' | 'increase' | 'decrease';

interface WaterfallDatum {
    name: string;
    /** Invisible base segment (transparent) — the standard Recharts
     *  stacked-bar waterfall technique. */
    base: number;
    /** Visible segment height (always >= 0). */
    value: number;
    /** Signed underlying amount (already currency-converted) for
     *  tooltip/label display. */
    raw: number;
    kind: WaterfallKind;
}

// ── Local KPI tile (currency-bearing — wraps value in .money-value for demo
//    blur; OpsKpi's `value` prop is string|number and can't carry a nested
//    span, so this mirrors its look for the currency KPIs only). ──────────────
function MoneyKpi({ label, value, sub, accent, icon }: {
    label: string; value: string; sub?: string; accent?: string; icon?: string;
}) {
    return (
        <div style={{
            padding: 18,
            background: 'var(--color-card)',
            border: '1px solid var(--color-border)',
            borderRadius: 12,
            boxShadow: 'var(--shadow-sm)',
            display: 'flex', flexDirection: 'column', gap: 6, minWidth: 0,
            position: 'relative',
        }}>
            <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 8 }}>
                <span className="uis-eyebrow" style={{ fontSize: 10 }}>{label}</span>
                {icon && <span className="material-symbols-outlined" style={{ fontSize: 14, color: 'var(--color-fg-4)' }}>{icon}</span>}
            </div>
            <div className="money-value" style={{
                fontFamily: 'var(--font-mono)', fontWeight: 700,
                fontSize: 24, letterSpacing: '-0.01em',
                color: accent ?? 'var(--color-fg-1)',
            }}>
                {value}
            </div>
            {sub && (
                <div className="money-value" style={{ fontSize: 11, color: 'var(--color-fg-4)', fontFamily: 'var(--font-mono)' }}>
                    {sub}
                </div>
            )}
        </div>
    );
}

// ── Page component ───────────────────────────────────────────────────────────

export const MonthlyAttribution: React.FC = () => {
    const { t } = useTranslation('reports');
    const { currency, convertFromCNY, currencySymbol } = useCurrency();
    const { includeNonRebalanceable } = usePortfolioFilter();
    const { lang } = useLanguage();

    const LEVEL_LABELS: Record<AttributionLevel, string> = useMemo(() => ({
        top_class: t('monthlyAttribution.levels.topClass'),
        sub_class: t('monthlyAttribution.levels.subClass'),
        asset: t('monthlyAttribution.levels.asset'),
        total: t('monthlyAttribution.levels.total'),
    }), [t]);

    /** Format an already-currency-converted display value (no further
     *  conversion — used by the waterfall chart, which converts once when
     *  building its dataset per the "convert before charting" requirement). */
    const fmtDisplayVal = useCallback((displayVal: number, opts: { compact?: boolean; signed?: boolean } = {}): string => {
        const { compact = false, signed = false } = opts;
        const sign = signed ? (displayVal > 0 ? '+' : displayVal < 0 ? '−' : '') : displayVal < 0 ? '−' : '';
        const abs = Math.abs(displayVal);
        if (compact) {
            if (abs >= 1_000_000) return `${sign}${currencySymbol}${(abs / 1_000_000).toFixed(2)}M`;
            if (abs >= 1_000) return `${sign}${currencySymbol}${(abs / 1_000).toFixed(1)}K`;
            return `${sign}${currencySymbol}${abs.toFixed(0)}`;
        }
        return `${sign}${currencySymbol}${abs.toLocaleString('en-US', { maximumFractionDigits: 0 })}`;
    }, [currencySymbol]);

    const fmtAmt = useCallback((n: number | null | undefined, opts: { compact?: boolean; signed?: boolean } = {}): string => {
        if (n == null) return '—';
        return fmtDisplayVal(convertFromCNY(n), opts);
    }, [convertFromCNY, fmtDisplayVal]);

    // Program BIL / WS-2: fraction-based (0-1) local fmtPct → delegates to
    // formatMoney.ts's formatPercent (one of WS-1's ~6 flagged definitions).
    const fmtPctVal = (n: number | null | undefined): string =>
        formatPercent(n == null ? null : n * 100, { signed: false, signStyle: 'inline', digits: 1 });

    // ── State ────────────────────────────────────────────────────────────────

    const [monthFrom, setMonthFrom] = useState<string>(defaultMonth());
    const [monthTo, setMonthTo] = useState<string>(''); // optional — empty = single-month mode
    const [level, setLevel] = useState<AttributionLevel>('top_class');
    const [drillTop, setDrillTop] = useState<string | null>(null);
    const [drillTopCn, setDrillTopCn] = useState<string | null>(null);
    const [drillSub, setDrillSub] = useState<string | null>(null);
    const [drillSubCn, setDrillSubCn] = useState<string | null>(null);

    const [monthly, setMonthly] = useState<AttributionMonthlyResponse | null>(null);
    const [loading, setLoading] = useState(false);
    const [error, setError] = useState<string | null>(null);

    const [summaryMonths, setSummaryMonths] = useState<AttributionSummaryMonth[]>([]);
    // Full response — needed for the response-level trailing-12m fields
    // (savings_rate_ttm etc.), which are NOT per-month (plan
    // 2026-07-20-investment-contributions-savings.md).
    const [summaryResponse, setSummaryResponse] = useState<AttributionSummaryResponse | null>(null);
    const [summaryLoading, setSummaryLoading] = useState(false);
    const [summaryError, setSummaryError] = useState<string | null>(null);

    const [recomputing, setRecomputing] = useState(false);
    const [recomputeError, setRecomputeError] = useState<string | null>(null);

    // ── Range validation (Item 5) — disable invalid states, never error ──────

    const monthValid = (m: string): boolean => /^\d{4}-\d{2}$/.test(m) && m >= MIN_MONTH;
    const fromValid = monthValid(monthFrom);
    const rangeValid = !monthTo || (monthValid(monthTo) && monthTo >= monthFrom);
    const fetchEnabled = fromValid && rangeValid;
    const isRangeActive = Boolean(monthTo) && rangeValid;

    // ── Data loading ─────────────────────────────────────────────────────────

    const loadMonthly = useCallback(async (m: string, lvl: AttributionLevel, mTo?: string) => {
        setLoading(true);
        setError(null);
        try {
            const res = await api.getMonthlyAttribution(m, lvl, includeNonRebalanceable, mTo);
            setMonthly(res);
        } catch (e) {
            console.error('Failed to load monthly attribution:', e);
            setError(t('monthlyAttribution.errors.loadMonth'));
            setMonthly(null);
        } finally {
            setLoading(false);
        }
    }, [includeNonRebalanceable]);

    useEffect(() => {
        if (!fetchEnabled) return;
        loadMonthly(monthFrom, level, monthTo || undefined);
    }, [monthFrom, monthTo, level, fetchEnabled, loadMonthly]);

    const loadSummary = useCallback(async () => {
        setSummaryLoading(true);
        setSummaryError(null);
        try {
            const res = await api.getAttributionSummary(24);
            setSummaryMonths(res.months ?? []);
            setSummaryResponse(res);
        } catch (e) {
            console.error('Failed to load attribution summary:', e);
            setSummaryError(t('monthlyAttribution.errors.loadSummary'));
        } finally {
            setSummaryLoading(false);
        }
    }, []);

    useEffect(() => { loadSummary(); }, [loadSummary]);

    // ── Recompute (Rule 19: empty state must have an action, not a dead end) ──

    const handleRecompute = useCallback(async () => {
        setRecomputing(true);
        setRecomputeError(null);
        try {
            await api.recomputeAttribution({ months: 7 });
            const tasks: Promise<unknown>[] = [loadSummary()];
            if (fetchEnabled) tasks.push(loadMonthly(monthFrom, level, monthTo || undefined));
            await Promise.all(tasks);
        } catch (e) {
            console.error('Recompute attribution failed:', e);
            setRecomputeError(t('monthlyAttribution.errors.recompute'));
        } finally {
            setRecomputing(false);
        }
    }, [loadMonthly, loadSummary, monthFrom, monthTo, level, fetchEnabled]);

    const currentSummary = useMemo(
        () => (isRangeActive ? null : summaryMonths.find(s => s.month === monthFrom) ?? null),
        [summaryMonths, monthFrom, isRangeActive],
    );

    // ── Level / drill-down handling ──────────────────────────────────────────

    const handleLevelToggle = (lvl: AttributionLevel) => {
        setDrillTop(null);
        setDrillTopCn(null);
        setDrillSub(null);
        setDrillSubCn(null);
        setLevel(lvl);
    };

    const handleMonthFromChange = (m: string) => {
        setMonthFrom(m);
        setDrillTop(null);
        setDrillTopCn(null);
        setDrillSub(null);
        setDrillSubCn(null);
    };

    const handleMonthToChange = (m: string) => {
        setMonthTo(m);
        setDrillTop(null);
        setDrillTopCn(null);
        setDrillSub(null);
        setDrillSubCn(null);
    };

    const handleClearRange = () => handleMonthToChange('');

    const handleRowClick = (row: AttributionRow) => {
        if (level === 'top_class') {
            setDrillTop(row.key);
            setDrillTopCn(row.key_cn ?? null);
            setLevel('sub_class');
        } else if (level === 'sub_class') {
            setDrillSub(row.key);
            setDrillSubCn(row.key_cn ?? null);
            setLevel('asset');
        }
        // level === 'asset' → drill-down floor, no-op.
    };

    const handleResetDrill = () => {
        setDrillTop(null);
        setDrillTopCn(null);
        setDrillSub(null);
        setDrillSubCn(null);
        setLevel('top_class');
    };

    // Client-side narrow by parent key — the spec's endpoint doesn't take a
    // parent filter, so each level fetch returns the full roll-up and we
    // filter to the drilled branch. Falls back to the unfiltered set if the
    // expected parent field isn't present on any row (defensive — the field
    // isn't in the spec's documented example payload).
    const displayRows = useMemo<AttributionRow[]>(() => {
        const rows = monthly?.rows ?? [];
        if (level === 'sub_class' && drillTop) {
            const filtered = rows.filter(r => r.top_class === drillTop);
            return filtered.length > 0 ? filtered : rows;
        }
        if (level === 'asset' && drillSub) {
            const filtered = rows.filter(r => r.sub_class === drillSub || r.top_class === drillSub);
            return filtered.length > 0 ? filtered : rows;
        }
        return rows;
    }, [monthly, level, drillTop, drillSub]);

    // ── Waterfall chart data (Item 3) — current level's TOTALS, never the
    //    drilled-down subset, so it always reads "the whole month/range". ────

    const waterfallData = useMemo<WaterfallDatum[]>(() => {
        if (!monthly) return [];
        const rows = monthly.rows ?? [];
        const mvStartTotal = convertFromCNY(rows.reduce((s, r) => s + (r.mv_start || 0), 0));
        const totals = monthly.totals;
        const effects: Array<[string, number]> = [
            [t('monthlyAttribution.effects.price'), convertFromCNY(totals.price_effect)],
            [t('monthlyAttribution.effects.trade'), convertFromCNY(totals.trade_effect)],
            [t('monthlyAttribution.effects.transfer'), convertFromCNY(totals.transfer_effect)],
            [t('monthlyAttribution.effects.income'), convertFromCNY(totals.income_effect)],
            [t('monthlyAttribution.effects.residual'), convertFromCNY(totals.residual)],
        ];
        const data: WaterfallDatum[] = [
            { name: t('monthlyAttribution.effects.start'), base: 0, value: mvStartTotal, raw: mvStartTotal, kind: 'anchor' },
        ];
        let running = mvStartTotal;
        effects.forEach(([name, val]) => {
            const base = val >= 0 ? running : running + val;
            data.push({ name, base, value: Math.abs(val), raw: val, kind: val >= 0 ? 'increase' : 'decrease' });
            running += val;
        });
        data.push({ name: t('monthlyAttribution.effects.end'), base: 0, value: running, raw: running, kind: 'anchor' });
        return data;
    }, [monthly, convertFromCNY, t]);

    const waterfallBarColor = (kind: WaterfallKind): string =>
        kind === 'increase' ? 'var(--color-success)' : kind === 'decrease' ? 'var(--color-danger)' : 'var(--color-fg-4)';

    // Custom tooltip content — closes over fmtDisplayVal so it stays in sync
    // with the reporting currency toggle. Recharts wraps custom content in
    // .recharts-tooltip-wrapper, which the demo-mode CSS already blurs.
    const WaterfallTooltipContent = ({ active, payload }: { active?: boolean; payload?: Array<{ payload: WaterfallDatum }> }) => {
        if (!active || !payload || payload.length === 0) return null;
        const d = payload[0].payload;
        return (
            <div style={{
                background: 'var(--color-card)', border: '1px solid var(--color-border)',
                borderRadius: 8, padding: '8px 12px', fontSize: 12, boxShadow: 'var(--shadow-md)',
            }}>
                <div style={{ fontWeight: 700, color: 'var(--color-fg-1)', marginBottom: 4 }}>{d.name}</div>
                <div style={{ fontFamily: 'var(--font-mono)', color: waterfallBarColor(d.kind) }}>
                    {fmtDisplayVal(d.raw, { signed: d.kind !== 'anchor' })}
                </div>
            </div>
        );
    };

    // ── Table columns ────────────────────────────────────────────────────────

    const cols: ColDef<AttributionRow>[] = useMemo(() => [
        {
            label: LEVEL_LABELS[level],
            key: 'key',
            render: (r) => (
                <div>
                    <div style={{ fontWeight: 600, color: 'var(--color-fg-1)' }}>
                        {r.asset_name ?? localizedClassName(r.key, r.key_cn, lang)}
                    </div>
                    {level === 'asset' && r.asset_id && (
                        <div style={{ fontSize: 10, fontFamily: 'var(--font-mono)', color: 'var(--color-fg-4)' }}>
                            {r.asset_id}
                        </div>
                    )}
                    {level !== 'top_class' && r.top_class && (
                        <div style={{ fontSize: 10, color: 'var(--color-fg-4)' }}>{localizedClassName(r.top_class, r.top_class_cn, lang)}</div>
                    )}
                    {level === 'asset' && r.dq_flag && r.dq_reason && (
                        <div style={{ fontSize: 10, color: 'var(--color-fg-4)', marginTop: 3, maxWidth: 280, lineHeight: 1.4 }}>
                            {r.dq_reason}
                        </div>
                    )}
                </div>
            ),
        },
        { label: t('monthlyAttribution.columns.mvEnd', { currency }), align: 'right', mono: true, render: (r) => <span className="money-value">{fmtAmt(r.mv_end, { compact: true })}</span> },
        {
            label: t('monthlyAttribution.columns.delta'), align: 'right', mono: true,
            render: (r) => (
                <span className="money-value" style={{ color: r.delta >= 0 ? 'var(--color-success)' : 'var(--color-danger)', fontWeight: 600 }}>
                    {fmtAmt(r.delta, { compact: true, signed: true })}
                </span>
            ),
        },
        { label: t('monthlyAttribution.effects.price'), align: 'right', mono: true, render: (r) => <span className="money-value">{fmtAmt(r.price_effect, { compact: true, signed: true })}</span> },
        { label: t('monthlyAttribution.effects.trade'), align: 'right', mono: true, render: (r) => <span className="money-value">{fmtAmt(r.trade_effect, { compact: true, signed: true })}</span> },
        { label: t('monthlyAttribution.effects.transfer'), align: 'right', mono: true, render: (r) => <span className="money-value">{fmtAmt(r.transfer_effect, { compact: true, signed: true })}</span> },
        { label: t('monthlyAttribution.effects.income'), align: 'right', mono: true, render: (r) => <span className="money-value">{fmtAmt(r.income_effect, { compact: true, signed: true })}</span> },
        { label: t('monthlyAttribution.effects.residual'), align: 'right', mono: true, render: (r) => <span className="money-value">{fmtAmt(r.residual, { compact: true, signed: true })}</span> },
        {
            label: t('monthlyAttribution.columns.dq'), align: 'center',
            render: (r) => r.dq_flag ? (
                <span title={r.dq_reason ?? undefined}>
                    <Pill tone="danger">{t('monthlyAttribution.flagged')}</Pill>
                </span>
            ) : null,
        },
    ], [level, currency, fmtAmt, t, LEVEL_LABELS, lang]);

    // ── Render ───────────────────────────────────────────────────────────────

    const breadcrumb: string[] = [];
    if (drillTop) breadcrumb.push(localizedClassName(drillTop, drillTopCn, lang));
    if (drillSub) breadcrumb.push(localizedClassName(drillSub, drillSubCn, lang));

    const toMinAttr = monthFrom > MIN_MONTH ? monthFrom : MIN_MONTH;
    const rangeCaption = monthly
        ? (isRangeActive ? `Range: ${monthly.month}` : `Month: ${monthly.month}`)
        : (isRangeActive ? `Range: ${monthFrom} → ${monthTo}` : `Month: ${monthFrom}`);

    return (
        <div style={{ minHeight: '100vh', background: 'var(--color-bg)', padding: '24px 28px' }}>
            <div style={{ display: 'flex', flexDirection: 'column', gap: 20 }}>

                {/* Header */}
                <Card style={{ padding: 0, overflow: 'hidden' }}>
                    <div style={{ padding: '10px 20px', borderBottom: '1px solid var(--color-border-soft)' }}>
                        <span className="uis-eyebrow" style={{ fontSize: 10 }}>{t('monthlyAttribution.breadcrumb')}</span>
                    </div>
                    <div style={{
                        display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between',
                        gap: 16, padding: '18px 20px', flexWrap: 'wrap',
                    }}>
                        <div>
                            <h1 style={{ margin: 0, fontSize: 20, fontWeight: 700, color: 'var(--color-fg-1)' }}>
                                {t('monthlyAttribution.title')}
                            </h1>
                            <p style={{ margin: '4px 0 0', fontSize: 12, color: 'var(--color-fg-3)' }}>
                                <Trans
                                    t={t}
                                    i18nKey="monthlyAttribution.priceOrFlows"
                                    components={{ strong1: <strong />, strong2: <strong /> }}
                                />
                            </p>
                        </div>
                        <div style={{ display: 'flex', alignItems: 'flex-end', gap: 10, flexWrap: 'wrap' }}>
                            <label style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 12, color: 'var(--color-fg-3)' }}>
                                {t('monthlyAttribution.from')}
                                <input
                                    type="month"
                                    value={monthFrom}
                                    onChange={(e) => handleMonthFromChange(e.target.value)}
                                    min={MIN_MONTH}
                                    style={{
                                        padding: '6px 10px', fontSize: 12,
                                        background: 'var(--color-card)',
                                        border: `1px solid ${fromValid ? 'var(--color-border)' : 'var(--color-danger)'}`,
                                        borderRadius: 8, color: 'var(--color-fg-1)', fontFamily: 'var(--font-mono)',
                                    }}
                                />
                            </label>
                            <label style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 12, color: 'var(--color-fg-3)' }}>
                                {t('monthlyAttribution.to')} <span style={{ color: 'var(--color-fg-4)', fontSize: 10 }}>{t('monthlyAttribution.optional')}</span>
                                <input
                                    type="month"
                                    value={monthTo}
                                    onChange={(e) => handleMonthToChange(e.target.value)}
                                    min={toMinAttr}
                                    style={{
                                        padding: '6px 10px', fontSize: 12,
                                        background: 'var(--color-card)',
                                        border: `1px solid ${rangeValid ? 'var(--color-border)' : 'var(--color-danger)'}`,
                                        borderRadius: 8, color: 'var(--color-fg-1)', fontFamily: 'var(--font-mono)',
                                    }}
                                />
                                {monthTo && (
                                    <button
                                        onClick={handleClearRange}
                                        title={t('monthlyAttribution.clearRange')}
                                        style={{
                                            border: 'none', background: 'transparent', cursor: 'pointer',
                                            color: 'var(--color-fg-4)', fontSize: 14, lineHeight: 1, padding: 2,
                                        }}
                                    >
                                        ×
                                    </button>
                                )}
                            </label>
                            <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'flex-end', gap: 4 }}>
                                <ActionBtn
                                    icon="refresh"
                                    variant="secondary"
                                    onClick={handleRecompute}
                                    disabled={recomputing}
                                >
                                    {recomputing ? t('monthlyAttribution.recomputing') : t('monthlyAttribution.recompute')}
                                </ActionBtn>
                                {recomputeError && (
                                    <span style={{ fontSize: 11, color: 'var(--color-danger)' }}>{recomputeError}</span>
                                )}
                            </div>
                        </div>
                    </div>
                    {!rangeValid && (
                        <div style={{
                            padding: '8px 20px', fontSize: 11, color: 'var(--color-danger)',
                            borderTop: '1px solid var(--color-border-soft)',
                        }}>
                            {t('monthlyAttribution.rangeInvalid', { minMonth: MIN_MONTH })}
                        </div>
                    )}
                </Card>

                {/* Summary strip — single-month mode only; /summary is per-month,
                    not range-aware, so we never sum it client-side across a range. */}
                {isRangeActive ? (
                    <Card>
                        <Section icon="date_range" title={t('monthlyAttribution.rangeMode.title')}>
                            <p style={{ margin: 0, fontSize: 12, color: 'var(--color-fg-3)' }}>
                                <Trans
                                    t={t}
                                    i18nKey="monthlyAttribution.rangeMode.body"
                                    values={{ rangeCaption }}
                                    components={{ strong: <strong /> }}
                                />
                            </p>
                        </Section>
                    </Card>
                ) : (
                    <Card>
                        <Section icon="insights" title={t('monthlyAttribution.summaryTitle', { month: monthFrom })}>
                            {summaryLoading ? (
                                <div style={{ padding: '16px', textAlign: 'center', fontSize: 12, color: 'var(--color-fg-4)' }}>
                                    {t('monthlyAttribution.loadingSummary')}
                                </div>
                            ) : summaryError ? (
                                <div style={{ padding: '12px 16px', fontSize: 12, color: 'var(--color-danger)' }}>
                                    {summaryError}
                                </div>
                            ) : !currentSummary ? (
                                <div style={{ padding: '16px', textAlign: 'center', fontSize: 12, color: 'var(--color-fg-4)' }}>
                                    {t('monthlyAttribution.noSummaryData', { month: monthFrom })}
                                </div>
                            ) : (
                                <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(150px, 1fr))', gap: 12 }}>
                                    <MoneyKpi
                                        label={t('monthlyAttribution.kpi.totalDelta')}
                                        value={fmtAmt(currentSummary.delta, { compact: true, signed: true })}
                                        accent={currentSummary.delta >= 0 ? 'var(--color-success)' : 'var(--color-danger)'}
                                        icon="trending_up"
                                    />
                                    <MoneyKpi label={t('monthlyAttribution.kpi.priceEffect')} value={fmtAmt(currentSummary.price_effect, { compact: true, signed: true })} icon="show_chart" />
                                    <MoneyKpi
                                        label={t('monthlyAttribution.kpi.netFlows')}
                                        // flows === null → month has NO classified cash_flow_tags at
                                        // all ("we don't know"), distinct from a true ¥0 (Item E)
                                        value={currentSummary.flows ? fmtAmt(currentSummary.flows.net_external, { compact: true, signed: true }) : '—'}
                                        sub={currentSummary.flows
                                            ? t('monthlyAttribution.kpi.netFlowsSub', { in: fmtAmt(currentSummary.flows.external_in, { compact: true }), out: fmtAmt(currentSummary.flows.external_out, { compact: true }) })
                                            : t('monthlyAttribution.kpi.noClassifiedFlows')}
                                        icon="swap_horiz"
                                    />
                                    <MoneyKpi label={t('monthlyAttribution.kpi.incomeEffect')} value={fmtAmt(currentSummary.income_effect, { compact: true, signed: true })} icon="payments" />
                                    {/* Two DIFFERENT rates (plan 2026-08-01 §WS-G) — ~19pp apart
                                        on live data. Never collapse them into one tile: money
                                        earned and left in a bank account is saved, just not
                                        deployed. Both from the 月度收支 ledger, trailing 12 DATA
                                        months; per-month omitted (lump-sum investing makes a
                                        single month meaningless). */}
                                    <div title={t('monthlyAttribution.kpi.savingsRateTooltip')}>
                                        <OpsKpi
                                            label={t('monthlyAttribution.kpi.savingsRate12m')}
                                            value={fmtPctVal(summaryResponse?.savings_rate_ttm)}
                                            sub={t('monthlyAttribution.kpi.savingsRateSub')}
                                            icon="savings"
                                        />
                                    </div>
                                    <div title={t('monthlyAttribution.kpi.investmentRateTooltip')}>
                                        <OpsKpi
                                            label={t('monthlyAttribution.kpi.investmentRate12m')}
                                            value={fmtPctVal(summaryResponse?.investment_rate_ttm)}
                                            sub={t('monthlyAttribution.kpi.investmentRateSub')}
                                            icon="trending_up"
                                        />
                                    </div>
                                    <div title={t('monthlyAttribution.kpi.undeployedCashTooltip')}>
                                        <MoneyKpi
                                            label={t('monthlyAttribution.kpi.undeployedCash12m')}
                                            value={fmtAmt(summaryResponse?.undeployed_cash_ttm, { compact: true })}
                                            sub={t('monthlyAttribution.kpi.undeployedCashSub')}
                                            icon="account_balance_wallet"
                                        />
                                    </div>
                                    <OpsKpi label={t('monthlyAttribution.kpi.investRatio')} value={fmtPctVal(currentSummary.invest_ratio)} icon="pie_chart" />
                                    <OpsKpi
                                        label={t('monthlyAttribution.kpi.dqFlags')}
                                        value={currentSummary.dq_count}
                                        accent={currentSummary.dq_count > 0 ? 'var(--color-danger)' : undefined}
                                        icon="flag"
                                    />
                                </div>
                            )}
                            {!isRangeActive && !summaryLoading && !summaryError && (
                                <p style={{ margin: '10px 2px 0', fontSize: 11, color: 'var(--color-fg-4)' }}>
                                    {t('monthlyAttribution.contributionsNote')}
                                </p>
                            )}
                        </Section>
                    </Card>
                )}

                {/* Global error */}
                {error && (
                    <div style={{
                        padding: '12px 16px', background: 'var(--color-danger-bg)',
                        border: '1px solid var(--color-danger)', borderRadius: 10,
                        fontSize: 13, color: 'var(--color-danger)',
                    }}>
                        {error}
                    </div>
                )}

                {/* Waterfall chart — Start → Price → Trade → Transfer → Income →
                    Residual → End, for the current level's totals (never the
                    drilled-down subset). */}
                <Card>
                    <Section icon="waterfall_chart" title={t('monthlyAttribution.waterfall.title')} right={
                        <span style={{ fontSize: 11, color: 'var(--color-fg-4)', fontFamily: 'var(--font-mono)' }}>{rangeCaption}</span>
                    }>
                        <div style={{ fontSize: 11, color: 'var(--color-fg-4)', marginBottom: 8 }}>
                            <Trans
                                t={t}
                                i18nKey="monthlyAttribution.waterfall.description"
                                components={{ strong: <strong /> }}
                            />
                        </div>
                        {loading ? (
                            <div style={{ padding: '32px', textAlign: 'center', fontSize: 12, color: 'var(--color-fg-4)' }}>
                                {t('monthlyAttribution.waterfall.loading')}
                            </div>
                        ) : waterfallData.length === 0 ? (
                            <div style={{ padding: '32px', textAlign: 'center', fontSize: 12, color: 'var(--color-fg-4)' }}>
                                {t('monthlyAttribution.waterfall.noData')}
                            </div>
                        ) : (
                            <div style={{ height: 320 }}>
                                <ResponsiveContainer width="100%" height="100%">
                                    <BarChart data={waterfallData} margin={{ top: 24, right: 16, left: 8, bottom: 0 }} barCategoryGap={2}>
                                        <XAxis dataKey="name" tick={{ fontSize: 11, fill: 'var(--color-fg-4)' }} tickLine={false} axisLine={{ stroke: 'var(--color-border)' }} />
                                        <YAxis tickFormatter={(v: number) => fmtDisplayVal(v, { compact: true })} tick={{ fontSize: 10 }} width={64} tickLine={false} axisLine={false} />
                                        <Tooltip content={<WaterfallTooltipContent />} cursor={{ fill: 'var(--color-border-soft)' }} />
                                        <Bar dataKey="base" stackId="wf" fill="transparent" isAnimationActive={false} />
                                        <Bar dataKey="value" stackId="wf" radius={[2, 2, 2, 2]} isAnimationActive={false} maxBarSize={72}>
                                            {waterfallData.map((d, i) => (
                                                <Cell key={i} fill={waterfallBarColor(d.kind)} />
                                            ))}
                                            <LabelList
                                                dataKey="value"
                                                content={(props: unknown) => {
                                                    const { x, y, width, index } = props as { x: number; y: number; width: number; index: number };
                                                    if (width == null || width < 20) return null;
                                                    const d = waterfallData[index];
                                                    if (!d) return null;
                                                    const label = fmtDisplayVal(d.raw, { compact: true, signed: d.kind !== 'anchor' });
                                                    return (
                                                        <text
                                                            x={x + width / 2}
                                                            y={y - 6}
                                                            textAnchor="middle"
                                                            className="money-value"
                                                            fontSize={10}
                                                            fontFamily="var(--font-mono)"
                                                            fill="var(--color-fg-3)"
                                                        >
                                                            {label}
                                                        </text>
                                                    );
                                                }}
                                            />
                                        </Bar>
                                    </BarChart>
                                </ResponsiveContainer>
                            </div>
                        )}
                    </Section>
                </Card>

                {/* Roll-up table */}
                <Card>
                    <Section icon="table_chart" title={t('monthlyAttribution.decomposition.title')}>
                        <Toolbar style={{ marginBottom: 16, justifyContent: 'space-between' }}>
                            <div style={{ display: 'flex', gap: 8 }}>
                                <ChipBtn primary={level === 'top_class'} onClick={() => handleLevelToggle('top_class')}>
                                    {LEVEL_LABELS.top_class}
                                </ChipBtn>
                                <ChipBtn primary={level === 'sub_class'} onClick={() => handleLevelToggle('sub_class')}>
                                    {LEVEL_LABELS.sub_class}
                                </ChipBtn>
                                <ChipBtn primary={level === 'asset'} onClick={() => handleLevelToggle('asset')}>
                                    {LEVEL_LABELS.asset}
                                </ChipBtn>
                            </div>
                            {breadcrumb.length > 0 && (
                                <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                                    <span style={{ fontSize: 11, color: 'var(--color-fg-4)', fontFamily: 'var(--font-mono)' }}>
                                        {breadcrumb.join(' › ')}
                                    </span>
                                    <ActionBtn icon="undo" variant="secondary" onClick={handleResetDrill}>
                                        {t('monthlyAttribution.reset')}
                                    </ActionBtn>
                                </div>
                            )}
                        </Toolbar>

                        {monthly && monthly.dq_flagged_assets.length > 0 && (
                            <div style={{
                                display: 'flex', alignItems: 'center', gap: 8, marginBottom: 12,
                                padding: '8px 12px', background: 'var(--fill-danger-soft)',
                                border: '1px solid color-mix(in srgb, var(--color-danger) 25%, transparent)',
                                borderRadius: 8, fontSize: 12, color: 'var(--color-danger)',
                            }}>
                                <span className="material-symbols-outlined" style={{ fontSize: 14 }}>flag</span>
                                {t('monthlyAttribution.flaggedAssets', { count: monthly.dq_flagged_assets.length, names: monthly.dq_flagged_assets.join(', ') })}
                            </div>
                        )}

                        {loading ? (
                            <div style={{ padding: '32px', textAlign: 'center', fontSize: 12, color: 'var(--color-fg-4)' }}>
                                {t('monthlyAttribution.decomposition.loading')}
                            </div>
                        ) : displayRows.length === 0 ? (
                            <div style={{
                                padding: '32px', textAlign: 'center', fontSize: 12, color: 'var(--color-fg-4)',
                                display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 12,
                            }}>
                                <span>
                                    {error ? t('monthlyAttribution.decomposition.unableToLoad') : t('monthlyAttribution.decomposition.noRows', { caption: rangeCaption, level: LEVEL_LABELS[level].toLowerCase() })}
                                </span>
                                <ActionBtn
                                    icon="calculate"
                                    variant="primary"
                                    onClick={handleRecompute}
                                    disabled={recomputing}
                                >
                                    {recomputing ? t('monthlyAttribution.computing') : t('monthlyAttribution.computeAttribution')}
                                </ActionBtn>
                                {recomputeError && (
                                    <span style={{ color: 'var(--color-danger)' }}>{recomputeError}</span>
                                )}
                            </div>
                        ) : (
                            <div style={{ overflowX: 'auto' }}>
                                <OpsTable
                                    cols={cols}
                                    rows={displayRows}
                                    rowKey={(r) => r.asset_id ?? r.key}
                                    onRowClick={level === 'asset' ? undefined : handleRowClick}
                                />
                            </div>
                        )}

                        {monthly && (
                            <div style={{
                                display: 'flex', justifyContent: 'flex-end', gap: 20,
                                marginTop: 14, paddingTop: 14, borderTop: '1px solid var(--color-border-soft)',
                                fontSize: 11, color: 'var(--color-fg-3)', fontFamily: 'var(--font-mono)',
                            }}>
                                <span className="money-value">
                                    <Trans
                                        t={t}
                                        i18nKey="monthlyAttribution.footer.totalsDelta"
                                        values={{ value: fmtAmt(monthly.totals.delta, { compact: true, signed: true }) }}
                                        components={{ strong: <strong /> }}
                                    />
                                </span>
                                <span className="money-value">{t('monthlyAttribution.footer.price', { value: fmtAmt(monthly.totals.price_effect, { compact: true, signed: true }) })}</span>
                                <span className="money-value">{t('monthlyAttribution.footer.trade', { value: fmtAmt(monthly.totals.trade_effect, { compact: true, signed: true }) })}</span>
                                <span className="money-value">{t('monthlyAttribution.footer.transfer', { value: fmtAmt(monthly.totals.transfer_effect, { compact: true, signed: true }) })}</span>
                                <span className="money-value">{t('monthlyAttribution.footer.income', { value: fmtAmt(monthly.totals.income_effect, { compact: true, signed: true }) })}</span>
                                <span className="money-value">{t('monthlyAttribution.footer.residual', { value: fmtAmt(monthly.totals.residual, { compact: true, signed: true }) })}</span>
                            </div>
                        )}
                    </Section>
                </Card>
            </div>
        </div>
    );
};
