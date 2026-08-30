/**
 * NorthStarPanel — Slimmed to Glide Path hero + Time in Market (WS-C 2026-07-12)
 *
 * Removed:
 *   - Contributions card (moved to Cash Flow tab in Analytics.tsx)
 *   - Unforced Errors section (moved to StrategyAlignment page via UnforcedErrors component)
 *   - Amber unlock banner + expandable manual-tagging table (moved to Operations classifier page)
 *   - All related state/handlers (auto-classify, tagging, unforced-error, unlock scroll ref)
 *
 * Added:
 *   - Thin run-rate-unavailable pointer (⚠ line), gated on run-rate
 *     availability (run_rate_monthly == null / run_rate_status !== 'available'),
 *     NOT on unclassified_count. Since the 2026-07-25 WS-2 rewire the glide
 *     run-rate no longer reads cash_flow_tags at all (ADR-025 §5.2 — it's
 *     (net_external_ttm + rsu_retained_ttm) / 12), so unclassified flows can
 *     no longer make the run-rate unavailable; they only matter for
 *     attribution (ADR-025 §4d). A separate neutral (non-"pending") pointer
 *     shows when the run-rate IS available but unclassified flows remain.
 *   - TIM 4-state strip: at/above target | in band | below floor | no data
 *   - TIM hover tooltip with real numbers
 *   - Quarter tick labels beneath the 24-slot strip
 *
 * Preserved: debounce/scenario logic, glide path, all Fix-5 / R2-2 / R2-4 behaviors.
 *
 * Token mapping (design CSS → Tailwind):
 *   var(--color-primary)       → text-primary / border-primary / bg-primary
 *   var(--color-card)          → bg-white dark:bg-card-dark
 *   var(--color-border)        → border-slate-200 dark:border-border-dark
 *   var(--color-fg-1)          → text-slate-900 dark:text-slate-100
 *   var(--color-fg-2)          → text-slate-700 dark:text-slate-300
 *   var(--color-fg-3)          → text-slate-500 dark:text-slate-400
 *   var(--color-fg-4)          → text-slate-400 dark:text-slate-500
 */
import React, { useCallback, useEffect, useState } from 'react';
import { Trans, useTranslation } from 'react-i18next';
import type { TFunction } from 'i18next';
import { useNavigate } from 'react-router-dom';
import { api } from '../../src/services/api';
import type {
    NorthStarPanel as NorthStarPanelData,
} from '../../src/services/api/types';

// ── Style helpers ─────────────────────────────────────────────────────────────
const CARD = 'bg-white dark:bg-card-dark rounded-xl border border-slate-200 dark:border-border-dark shadow-sm';
const CARD_PAD = `${CARD} p-6`;
const INPUT_BASE = 'text-sm px-3 py-2 rounded-lg border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-800/50 text-slate-800 dark:text-slate-200 focus:outline-none focus:ring-2 focus:ring-primary/50 transition-colors';

function cny(v: number | null | undefined): string {
    if (v == null) return '—';
    return `¥${v.toLocaleString(undefined, { maximumFractionDigits: 0 })}`;
}
function pct(v: number | null | undefined): string {
    if (v == null) return '—';
    return `${v.toFixed(1)}%`;
}

/** TIM 4-state classification */
type TimState = 'at_target' | 'in_band' | 'below_floor' | 'no_data';

function timState(hasData: boolean, weight: number, target: number, floor: number): TimState {
    if (!hasData) return 'no_data';
    if (weight >= target) return 'at_target';
    if (weight >= floor) return 'in_band';
    return 'below_floor';
}

function timStateLabel(state: TimState, t: TFunction): string {
    switch (state) {
        case 'at_target': return t('northStar.timState.aboveTarget');
        case 'in_band': return t('northStar.timState.inBand');
        case 'below_floor': return t('northStar.timState.belowFloor');
        case 'no_data': return t('northStar.timState.noData');
    }
}

function timTileClass(state: TimState): string {
    switch (state) {
        case 'at_target':
            return 'bg-primary';
        case 'in_band':
            // primary at ~45% opacity — use bg-primary/45
            return 'bg-primary/45';
        case 'below_floor':
            return 'bg-transparent border-[1.5px] border-slate-400 dark:border-slate-500';
        case 'no_data':
            return 'bg-slate-200 dark:bg-slate-700';
    }
}

export const NorthStarPanel: React.FC = () => {
    const { t } = useTranslation('reports');
    const navigate = useNavigate();
    const [panel, setPanel] = useState<NorthStarPanelData | null>(null);
    const [loading, setLoading] = useState(true);
    const [loadError, setLoadError] = useState<string | null>(null);

    // Scenario contribution — 500ms debounce before refetch
    const [monthlyContribution, setMonthlyContribution] = useState<number>(0);
    const [debouncedContribution, setDebouncedContribution] = useState<number>(0);
    const [isDebouncing, setIsDebouncing] = useState(false);

    // ── Data loading ─────────────────────────────────────────────────────────
    const load = useCallback(async (contribution: number) => {
        setLoadError(null);
        try {
            const data = await api.getNorthStarPanel(contribution);
            setPanel(data);
        } catch (e) {
            console.error('Failed to fetch North Star panel:', e);
            setLoadError(t('northStar.errors.load'));
        } finally {
            setLoading(false);
        }
    }, []);

    // 500ms debounce — also shows "recalculating…" while waiting
    useEffect(() => {
        setIsDebouncing(true);
        const timer = setTimeout(() => {
            setDebouncedContribution(monthlyContribution);
            setIsDebouncing(false);
        }, 500);
        return () => clearTimeout(timer);
    }, [monthlyContribution]);

    useEffect(() => { load(debouncedContribution); }, [load, debouncedContribution]);

    // ── Derived values ────────────────────────────────────────────────────────
    if (loading) return (
        <div className={`${CARD_PAD} text-center text-slate-500 dark:text-slate-400 py-12 text-sm`}>
            {t('northStar.loading')}
        </div>
    );

    const contrib = panel?.contributions;
    const tim = panel?.time_in_market;
    const gp = panel?.glide_path;
    const unclassifiedCount = contrib?.unclassified_count ?? 0;

    // Run-rate (Fix 5)
    const runRateMonthly = gp?.assumptions?.current_run_rate_monthly ?? null;
    const runRateStatus = gp?.run_rate_status ?? (gp?.assumptions?.run_rate_status ?? null);
    const runRateAvailable = runRateMonthly != null;

    // D-1 (2026-07-25): headlineYears was removed along with the glide hero it fed.
    // The backend still returns headline.years_to_target — this panel deliberately does
    // not read it, because "Your Path" Section 1 is the single source of the goal date
    // (median basis, ADR-026) and this panel answers the behaviour question only.

    const allGlideColumnsIdentical =
        (gp?.required_cagr_grid?.length ?? 0) > 0 &&
        gp!.required_cagr_grid!.every(
            (row) =>
                row.required_cagr_pct.zero === row.required_cagr_pct.current_run_rate &&
                row.required_cagr_pct.zero === row.required_cagr_pct.scenario,
        );

    const timBandPP =
        tim && !tim.insufficient_data && tim.target_pct != null && tim.band_floor_pct != null
            ? Math.round(tim.target_pct - tim.band_floor_pct)
            : 10;

    // ── TIM strip: 24 slots with 4-state ──────────────────────────────────────
    const timStrip = (() => {
        if (tim?.insufficient_data || !tim?.monthly_weights || tim.band_floor_pct == null || tim.target_pct == null) return null;
        const weightByMonth: Record<string, number> = {};
        for (const w of tim.monthly_weights) weightByMonth[w.month] = w.weight_pct;
        const bandFloor = tim.band_floor_pct;
        const target = tim.target_pct;
        const now = new Date();
        return Array.from({ length: 24 }, (_, i) => {
            const d = new Date(now.getFullYear(), now.getMonth() - (23 - i), 1);
            const month = `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}`;
            const hasData = month in weightByMonth;
            const weight = hasData ? weightByMonth[month] : 0;
            const state = timState(hasData, weight, target, bandFloor);
            // Quarter label: show label on tile index 0, 3, 6, 9, 12, 15, 18, 21 (every 3rd)
            const showLabel = i % 3 === 0;
            return { month, hasData, weight, state, showLabel };
        });
    })();

    // Target string for CAGR header
    const targetStr = `¥${(gp?.assumptions?.target ?? 20_000_000).toLocaleString()}`;

    return (
        <div className="space-y-4">
            {loadError && (
                <div className="bg-red-50 dark:bg-red-900/20 border border-red-200 dark:border-red-800 rounded-lg p-4">
                    <p className="text-red-700 dark:text-red-400 text-sm">{loadError}</p>
                </div>
            )}

            {/* ══════════════════════════════════════════════════════════════════
                1. HERO — Glide Path (primary artifact)
            ════════════════════════════════════════════════════════════════════ */}
            <section className="bg-white dark:bg-card-dark rounded-xl border border-slate-200 dark:border-border-dark border-t-[3px] border-t-primary shadow-sm p-6">
                {/* Top row: eyebrow + value | scenario input */}
                <div className="flex items-start justify-between gap-5 flex-wrap">
                    {/* Left: eyebrow / value / secondary / assumptions */}
                    <div className="flex-1 min-w-0">
                        <p className="text-[10px] font-bold uppercase tracking-widest text-primary mb-1.5">
                            {t('northStar.glidePathTo', { target: targetStr })}
                        </p>

                        {gp?.insufficient_data ? (
                            <div className="rounded-lg border border-dashed border-slate-300 dark:border-slate-700 p-5 text-center text-slate-400 dark:text-slate-500 text-sm my-2">
                                {t('northStar.insufficientData')}
                            </div>
                        ) : (
                            <>
                                {/* D-1 (2026-07-25, docs/design/2026-07-25-forecast-page-design-brief.md):
                                    this panel used to publish its OWN years-to-target here — at the
                                    arithmetic trailing TWR — which contradicted "Your Path" Section 1's
                                    median-basis headline by ~1.2 years, one screen apart. ADR-026 says the
                                    page has exactly one engine and one answer, so the rival number is gone
                                    (owner decision, 2026-07-25): "Are you on track?" answers only the
                                    BEHAVIOUR question. The required-CAGR grid below is that answer — what
                                    return each horizon demands, against what the portfolio actually
                                    delivers — and it needs no goal date of its own.

                                    The assumptions paragraph that sat here is also gone: it was one of the
                                    exact caveat sentences plan §4 said must become structure, and Section 1's
                                    input chips now carry all three of its figures (liquid NW, TWR,
                                    contribution) with the liquid-NW exclusion in a tooltip. */}
                                <p className="text-[12px] text-slate-500 dark:text-slate-400 leading-relaxed max-w-2xl mt-1">
                                    <Trans
                                        t={t}
                                        i18nKey="northStar.glideDemandParagraph"
                                        values={{ twr: pct(gp?.assumptions?.trailing_twr_pct) }}
                                        components={{ val: <span className="font-mono font-semibold text-slate-700 dark:text-slate-300" /> }}
                                    />
                                </p>
                            </>
                        )}
                    </div>

                    {/* Right: scenario input */}
                    <div className="flex flex-col gap-1.5 min-w-[190px]">
                        <label className="text-[10px] font-bold uppercase tracking-wider text-slate-500 dark:text-slate-400">
                            {t('northStar.scenarioLabel')}
                        </label>
                        <div className="flex items-center gap-2">
                            <input
                                type="number"
                                min={0}
                                className={`${INPUT_BASE} w-32`}
                                value={monthlyContribution}
                                onChange={(e) => setMonthlyContribution(Number(e.target.value) || 0)}
                            />
                            <span className={`text-[9.5px] font-mono text-slate-400 dark:text-slate-500 transition-opacity duration-150 ${isDebouncing ? 'opacity-100' : 'opacity-0'}`}>
                                {t('northStar.recalculating')}
                            </span>
                        </div>
                    </div>
                </div>

                {/* CAGR grid */}
                {!gp?.insufficient_data && (gp?.required_cagr_grid?.length ?? 0) > 0 && (
                    <div className="mt-5">
                        {allGlideColumnsIdentical && (
                            <p className="text-[10.5px] text-slate-400 dark:text-slate-500 font-mono mb-2">
                                {t('northStar.allColumnsIdentical')}
                            </p>
                        )}
                        <div className="rounded-lg border border-slate-100 dark:border-slate-800 overflow-hidden">
                            <table className="w-full text-[12px]">
                                <thead>
                                    <tr className="text-left text-[11px] uppercase tracking-wider bg-slate-50/80 dark:bg-slate-800/30 border-b border-slate-100 dark:border-slate-800">
                                        <th className="px-4 py-2.5 font-semibold text-slate-500 dark:text-slate-400">{t('northStar.table.horizon')}</th>
                                        <th className="px-4 py-2.5 font-semibold text-slate-500 dark:text-slate-400 text-right">{t('northStar.table.atZero')}</th>
                                        <th className={`px-4 py-2.5 font-semibold text-right ${runRateAvailable ? 'text-slate-500 dark:text-slate-400' : 'text-slate-400 dark:text-slate-500'}`}>
                                            {runRateAvailable
                                                ? t('northStar.table.atRunRate', { amount: cny(runRateMonthly) })
                                                : <>
                                                    {t('northStar.table.runRateUnavailable')}
                                                    <span className="block text-[9.5px] font-normal normal-case tracking-normal text-slate-400 dark:text-slate-500 mt-0.5">
                                                        {runRateStatus || t('northStar.table.noContributionData')}
                                                    </span>
                                                </>
                                            }
                                        </th>
                                        <th className="px-4 py-2.5 font-semibold text-slate-500 dark:text-slate-400 text-right">
                                            {t('northStar.table.atScenario', { amount: cny(monthlyContribution) })}
                                        </th>
                                    </tr>
                                </thead>
                                <tbody className="divide-y divide-slate-100 dark:divide-slate-800/50">
                                    {gp!.required_cagr_grid!.map((row) => {
                                        const scenarioCagrValue = (row.required_cagr_pct.scenario != null && monthlyContribution > 0)
                                            ? pct(row.required_cagr_pct.scenario)
                                            : pct(row.required_cagr_pct.zero);
                                        const cagrIsDiverged = monthlyContribution > 0 && row.required_cagr_pct.scenario != null;
                                        return (
                                            <tr key={row.horizon_years} className="hover:bg-slate-50/60 dark:hover:bg-white/[0.02] transition-colors">
                                                <td className="px-4 py-2.5 font-mono text-slate-700 dark:text-slate-300">{t('northStar.table.horizonYears', { years: row.horizon_years })}</td>
                                                <td className="px-4 py-2.5 font-mono text-right text-slate-700 dark:text-slate-300">
                                                    {pct(row.required_cagr_pct.zero)}
                                                </td>
                                                <td className="px-4 py-2.5 font-mono text-right text-slate-400 dark:text-slate-500 italic">
                                                    {/* R2-2: pending column stays number-free */}
                                                    {runRateAvailable ? pct(row.required_cagr_pct.current_run_rate) : '—'}
                                                </td>
                                                <td className={`px-4 py-2.5 font-mono text-right ${cagrIsDiverged ? 'text-primary font-bold' : 'text-slate-400 dark:text-slate-500'}`}>
                                                    {scenarioCagrValue}
                                                    {!cagrIsDiverged && (
                                                        <span className="text-[9.5px] ml-1.5 text-slate-400 dark:text-slate-500 font-normal">{t('northStar.table.equalsZero')}</span>
                                                    )}
                                                </td>
                                            </tr>
                                        );
                                    })}

                                    {/* D-1: the "Years to {target} @ trailing TWR" summary row was removed
                                        here (2026-07-25). It was the second half of the same contradiction as
                                        the deleted hero — an arithmetic-basis goal date sitting a screen below
                                        Section 1's median-basis one. Required CAGR per horizon is the
                                        behaviour signal this section exists for; a goal date is not. */}
                                </tbody>
                            </table>
                        </div>

                        {/* Assumptions footnote — real methodology notes only (which TWR/
                            run-rate basis a column uses). The old trailing "†
                            years-to-target row uses same deterministic engine" hedge is
                            gone (R-5): that footnote existed to excuse the deterministic
                            number contradicting Monte Carlo, and Section 1 of "Your Path"
                            now shows a probability band from the same unified median-basis
                            engine, so there is nothing left to excuse. Built as an array so
                            removing that always-present last item doesn't leave a dangling
                            " · " separator on whichever item is now last. */}
                        {gp?.assumptions && (() => {
                            const footnoteParts: React.ReactNode[] = [];
                            if (gp.assumptions.twr_basis) {
                                footnoteParts.push(<span key="twr">{t('northStar.footnote.twrBasis', { basis: gp.assumptions.twr_basis })}</span>);
                            }
                            if (gp.assumptions.run_rate_basis) {
                                footnoteParts.push(
                                    <span key="rr">
                                        {t('northStar.footnote.runRateBasis', { basis: gp.assumptions.run_rate_basis })}
                                        {!runRateAvailable && runRateStatus && (
                                            <span className="ml-1 text-amber-600 dark:text-amber-400">({runRateStatus})</span>
                                        )}
                                    </span>
                                );
                            }
                            {/* D-1: gp.assumptions.note is deliberately NOT rendered. Its live value is
                                "deterministic compounding; all inputs are assumptions, not forecasts" — a
                                hedge, not methodology, and the last surviving instance of the caveat class
                                plan §4 said must disappear. The twr_basis / run_rate_basis parts above stay:
                                those say which basis a COLUMN uses, which a reader genuinely needs. */}
                            if (footnoteParts.length === 0) return null;
                            return (
                                <div className="mt-3 text-[10.5px] text-slate-400 dark:text-slate-500 font-mono leading-relaxed">
                                    {footnoteParts.map((part, i) => (
                                        <React.Fragment key={i}>
                                            {i > 0 && ' · '}
                                            {part}
                                        </React.Fragment>
                                    ))}
                                </div>
                            );
                        })()}

                        {/* Run-rate-unavailable pointer — gated on actual run-rate availability,
                            NOT on unclassifiedCount (the run-rate no longer reads cash_flow_tags
                            at all since the WS-2 rewire; see file header + ADR-025 §5.2). */}
                        {!runRateAvailable && (
                            <div className="mt-2 flex items-center gap-1.5 text-[10.5px] font-mono text-amber-700 dark:text-amber-400">
                                <span>{t('northStar.runRateUnavailablePointer', { status: runRateStatus ? ` — ${runRateStatus}` : '' })}</span>
                                <button
                                    onClick={() => navigate('/cash-flow-classification')}
                                    className="text-primary hover:underline underline-offset-2 font-semibold"
                                >
                                    →
                                </button>
                            </div>
                        )}

                        {/* Unclassified-flows pointer — neutral, non-blocking. The run-rate above
                            IS available and unaffected by these; they still matter for attribution
                            (ADR-025 §4d), so keep the link, but never say "pending" here. */}
                        {runRateAvailable && unclassifiedCount > 0 && (
                            <div className="mt-2 flex items-center gap-1.5 text-[10.5px] font-mono text-slate-400 dark:text-slate-500">
                                <span>{t('northStar.unclassifiedFlows', { count: unclassifiedCount })}</span>
                                <button
                                    onClick={() => navigate('/cash-flow-classification')}
                                    className="text-primary hover:underline underline-offset-2 font-semibold"
                                >
                                    →
                                </button>
                            </div>
                        )}
                    </div>
                )}
            </section>

            {/* ══════════════════════════════════════════════════════════════════
                2. TIME IN MARKET — 4-state strip + quarter labels
            ════════════════════════════════════════════════════════════════════ */}
            <section className={CARD}>
                <div className="px-5 py-4 border-b border-slate-100 dark:border-slate-800 flex items-center gap-2">
                    <span className="material-symbols-outlined text-slate-400 text-[18px]">query_stats</span>
                    <h2 className="text-[13px] font-bold text-slate-800 dark:text-slate-100">{t('northStar.timTitle')}</h2>
                </div>
                <div className="p-5 space-y-3">
                    {tim?.insufficient_data ? (
                        <div className="rounded-lg border border-dashed border-slate-200 dark:border-slate-700 p-4 text-center text-slate-400 text-sm">
                            {t('northStar.timInsufficientData', { reason: tim.reason ? ` — ${tim.reason}` : '' })}
                        </div>
                    ) : (
                        <>
                            <div>
                                <p className="font-mono font-bold text-[30px] leading-none text-slate-900 dark:text-slate-100">
                                    {pct((tim?.ratio ?? 0) * 100)}
                                </p>
                                <p className="text-[11px] font-mono text-slate-500 dark:text-slate-400 mt-1">
                                    {t('northStar.timSummary', {
                                        inMonths: tim?.in_market_months,
                                        totalMonths: tim?.total_months,
                                        floor: pct(tim?.band_floor_pct),
                                        target: pct(tim?.target_pct),
                                        bandPP: timBandPP,
                                    })}
                                </p>
                            </div>

                            {/* 24-slot strip with 4-state tiles + quarter labels */}
                            {timStrip && (
                                <div>
                                    {/* Quarter labels row: each label cell is relative-positioned;
                                        the text is absolute so it can overflow right over empty
                                        cells without shifting tile alignment. Labels show
                                        "YYYY" on the first tile of each year, "Qn" on subsequent
                                        quarter starts within the same year. */}
                                    <div className="flex gap-[3px] flex-nowrap mt-1 mb-1 h-3">
                                        {timStrip.map(({ month, showLabel }, idx) => {
                                            let labelText: string | null = null;
                                            if (showLabel) {
                                                const [yr, mo] = month.split('-');
                                                // First tile of the strip or first tile of a new year → show year
                                                const isFirstYear = idx === 0 || (idx > 0 && timStrip[idx - 3]?.month.split('-')[0] !== yr);
                                                if (isFirstYear) {
                                                    labelText = yr;
                                                } else {
                                                    const q = Math.ceil(Number(mo) / 3);
                                                    labelText = t('northStar.quarterLabel', { q });
                                                }
                                            }
                                            return (
                                                <div key={`lbl-${month}`} className="w-4 shrink-0 relative">
                                                    {labelText && (
                                                        <span
                                                            className="absolute left-0 top-0 text-[8px] font-mono text-slate-400 dark:text-slate-500 leading-none whitespace-nowrap"
                                                            title={month}
                                                        >
                                                            {labelText}
                                                        </span>
                                                    )}
                                                </div>
                                            );
                                        })}
                                    </div>
                                    {/* Tile row */}
                                    <div className="flex gap-[3px] flex-nowrap">
                                        {timStrip.map(({ month, hasData, weight, state }) => {
                                            const tooltipText = hasData
                                                ? t('northStar.tooltipData', { month, weight: weight.toFixed(1), floor: pct(tim?.band_floor_pct), target: pct(tim?.target_pct), state: timStateLabel(state, t) })
                                                : t('northStar.tooltipNoData', { month });
                                            return (
                                                <div
                                                    key={month}
                                                    title={tooltipText}
                                                    className={`w-4 h-4 rounded-[3px] shrink-0 transition-colors ${timTileClass(state)}`}
                                                />
                                            );
                                        })}
                                    </div>
                                    {/* Legend — 4 states */}
                                    <div className="flex gap-2.5 flex-wrap mt-2 text-[9.5px] font-mono text-slate-400 dark:text-slate-500">
                                        <span className="flex items-center gap-1">
                                            <span className="inline-block w-[7px] h-[7px] rounded-[2px] bg-primary" />
                                            {t('northStar.legend.atAboveTarget')}
                                        </span>
                                        <span className="flex items-center gap-1">
                                            <span className="inline-block w-[7px] h-[7px] rounded-[2px] bg-primary/45" />
                                            {t('northStar.legend.inBand')}
                                        </span>
                                        <span className="flex items-center gap-1">
                                            <span className="inline-block w-[7px] h-[7px] rounded-[2px] border-[1.5px] border-slate-400 dark:border-slate-500" />
                                            {t('northStar.legend.belowFloor')}
                                        </span>
                                        <span className="flex items-center gap-1">
                                            <span className="inline-block w-[7px] h-[7px] rounded-[2px] bg-slate-200 dark:bg-slate-700" />
                                            {t('northStar.legend.noData')}
                                        </span>
                                        {tim?.total_months != null && (
                                            <span>{t('northStar.historyFooter', { months: tim.total_months })}</span>
                                        )}
                                    </div>
                                </div>
                            )}
                        </>
                    )}
                </div>
            </section>

            {/* D-1: the stamp row ("northstar.v1 · frozen contract: north-star.md") was removed
                2026-07-25. It rendered internal implementation metadata — a module version and a
                spec filename — into the owner's view of their own financial plan. Provenance of
                this kind belongs in the repo, not the UI. */}
        </div>
    );
};
