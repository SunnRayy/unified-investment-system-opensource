import React, { useMemo } from 'react';
import { useTranslation } from 'react-i18next';
import type { ForecastLevers, ProjectionResult } from '../../src/services/api/types';
import { useCurrency } from '../../src/context/useCurrency';
import { deriveCrossingYear } from '../../src/utils/crossingYear';

/**
 * FanChart — "Your Path" (W-5) Section 2.2, "Portfolio Projection to Goal".
 * docs/design/2026-07-26-your-path.dc.html.md §2.2.
 *
 * Hand-rolled SVG with a FIXED viewBox — deliberately NOT a Recharts
 * <ResponsiveContainer>. This removes recharts from this page and fixes two
 * open defects for free (docs/plans/2026-07-26-your-path-design-
 * implementation.md §2):
 *   D-2 — the old auto-scaled linear Y axis crushed the ¥20M goal line to
 *         ~9% of canvas under a P90 of ~¥205M. A LOG Y axis (below) shows
 *         the whole P1-P99 shape without clipping the goal.
 *   D-4 — ResponsiveContainer intermittently measured an unsized parent
 *         (width(-1)/height(-1)), rendering axes with no marks. A
 *         viewBox="0 0 1200 420" SVG cannot hit that bug — it has no
 *         parent-size dependency at all.
 *
 * DEVIATION FROM THE DESIGN RECORD (documented, not silent): the mock's
 * bands are P1-P99 (outer) / P25-P75 (inner). Our Monte Carlo endpoint
 * (GET /analytics/projection) only computes P10/P25/P50/P75/P90 — there is
 * no P1/P99 series to plot without commissioning a new backend percentile,
 * which is out of this workstream's scope (§4b forbids fabricating one
 * client-side). Bands here are P10-P90 (outer) / P25-P75 (inner); the
 * legend labels reflect that honestly instead of claiming P1-P99.
 *
 * All band/median data comes from `projection` (a live GET /analytics/
 * projection call the parent makes using levers.base as input) — never
 * re-derived here. `deriveCrossingYear` is used ONLY for the median marker,
 * which must align pixel-for-pixel with the drawn P50 curve (chart
 * geometry) — the "likely range" caption itself comes from
 * `levers.base.crossing_years` (an independent analytic quantity), per
 * design-record §4.
 */

interface FanChartProps {
    levers: ForecastLevers | null;
    projection: ProjectionResult | null;
    loading: boolean;
}

const F_W = 1200;
const F_H = 420;
const PAD_L = 76;
const PAD_R = 24;
const PAD_T = 20;
const PAD_B = 48;
const PLOT_W = F_W - PAD_L - PAD_R;
const PLOT_H = F_H - PAD_T - PAD_B;

// Log-axis tick ladder — chart LAYOUT configuration (which round numbers a
// log axis is allowed to land a gridline on), not a result figure. Same
// category as the slider min/max/step constants in forecast_levers.py
// (§4b's carve-out for UI/chart configuration vs. computed results).
const NICE_CEILS = [1e6, 2e6, 5e6, 1e7, 2e7, 5e7, 1e8, 2e8, 5e8, 1e9];
const Y_FLOOR = 1_000_000;

function pickYCeil(maxValue: number): number {
    const need = maxValue * 1.08;
    for (const c of NICE_CEILS) {
        if (c >= need) return c;
    }
    return NICE_CEILS[NICE_CEILS.length - 1];
}

function fmtAxis(cnyValue: number, convertFromCNY: (n: number) => number, symbol: string): string {
    const v = convertFromCNY(cnyValue);
    const abs = Math.abs(v);
    if (abs >= 1_000_000) return `${symbol}${(v / 1_000_000).toFixed(abs >= 10_000_000 ? 0 : 1)}M`;
    if (abs >= 1_000) return `${symbol}${(v / 1_000).toFixed(0)}k`;
    return `${symbol}${v.toFixed(0)}`;
}

export const FanChart: React.FC<FanChartProps> = ({ levers, projection, loading }) => {
    const { t } = useTranslation('reports');
    const { convertFromCNY, currencySymbol } = useCurrency();

    const base = levers?.base ?? null;
    const target = base?.target ?? null;
    const currentNw = base?.current_nw ?? null;

    const years = projection?.years ?? [];
    const p10 = projection?.percentiles?.p10 ?? [];
    const p25 = projection?.percentiles?.p25 ?? [];
    const p50 = projection?.percentiles?.p50 ?? [];
    const p75 = projection?.percentiles?.p75 ?? [];
    const p90 = projection?.percentiles?.p90 ?? [];

    const hasData = years.length > 1 && p50.length === years.length;

    const horizon = years.length > 0 ? years[years.length - 1] : 20;

    const yCeil = useMemo(() => {
        if (!hasData) return NICE_CEILS[0];
        const topPercentileValue = p90.length ? Math.max(...p90) : (target ?? Y_FLOOR);
        return pickYCeil(Math.max(topPercentileValue, target ?? 0, currentNw ?? 0, Y_FLOOR));
    }, [hasData, p90, target, currentNw]);

    const logSpan = Math.log(yCeil / Y_FLOOR);

    const fx = (t: number) => PAD_L + (horizon > 0 ? (t / horizon) * PLOT_W : 0);
    const fy = (v: number) => {
        const clamped = Math.max(v, Y_FLOOR);
        return PAD_T + PLOT_H - (Math.log(clamped / Y_FLOOR) / logSpan) * PLOT_H;
    };

    const bandPath = (lower: number[], upper: number[]): string => {
        if (!hasData || lower.length !== years.length || upper.length !== years.length) return '';
        const upperPts = years.map((yr, i) => `${fx(yr)},${fy(upper[i])}`);
        const lowerPts = years.map((yr, i) => `${fx(yr)},${fy(lower[i])}`).reverse();
        return `M${upperPts.join(' L')} L${lowerPts.join(' L')} Z`;
    };

    const medianPoints = hasData ? years.map((yr, i) => `${fx(yr)},${fy(p50[i])}`).join(' ') : '';

    const p50CrossYear = useMemo(() => {
        if (!hasData || target == null) return null;
        return deriveCrossingYear(years, p50, target);
    }, [hasData, years, p50, target]);

    const yTicks = useMemo(
        () => NICE_CEILS.filter(v => v >= Y_FLOOR && v <= yCeil),
        [yCeil],
    );
    const xTickStep = 5;
    const xTicks = useMemo(() => {
        const out: number[] = [];
        for (let t = 0; t <= horizon; t += xTickStep) out.push(t);
        return out;
    }, [horizon]);

    return (
        <div className="card">
            <div className="card-head">
                <span className="card-title">
                    <span className="material-symbols-outlined">insights</span>
                    {t('forecast.fanChart.title')}
                </span>
                <div className="card-head-actions">
                    <span className="legend">
                        <span className="lg-item"><span className="lg-dot" style={{ background: 'rgba(59,130,246,.3)' }} />{t('forecast.fanChart.p10p90')}</span>
                        <span className="lg-item"><span className="lg-dot" style={{ background: 'rgba(59,130,246,.55)' }} />{t('forecast.fanChart.p25p75')}</span>
                        <span className="lg-item"><span className="lg-dot" style={{ background: 'var(--color-primary)' }} />{t('forecast.fanChart.median')}</span>
                    </span>
                </div>
            </div>
            <div className="card-body">
                {!hasData && (
                    <div style={{ height: 320, display: 'flex', alignItems: 'center', justifyContent: 'center', color: 'var(--color-fg-3)', fontSize: 13 }}>
                        {loading ? t('forecast.fanChart.computing') : t('forecast.fanChart.noData')}
                    </div>
                )}
                {hasData && (
                    <svg viewBox={`0 0 ${F_W} ${F_H}`} style={{ width: '100%', display: 'block' }}>
                        <defs>
                            <clipPath id="fanclip">
                                <rect x={72} y={20} width={1104} height={352} />
                            </clipPath>
                        </defs>

                        {/* Y gridlines + labels (log axis) */}
                        {yTicks.map(v => (
                            <g key={v}>
                                <line x1={PAD_L} x2={F_W - PAD_R} y1={fy(v)} y2={fy(v)} stroke="var(--color-border-soft)" strokeDasharray="3 3" />
                                <text x={PAD_L - 8} y={fy(v)} textAnchor="end" dy="0.32em" fontFamily="var(--font-mono)" fontSize={10} fill="var(--color-fg-3)">
                                    {fmtAxis(v, convertFromCNY, currencySymbol)}
                                </text>
                            </g>
                        ))}

                        {/* X ticks */}
                        {xTicks.map(t => (
                            <text key={t} x={fx(t)} y={394} textAnchor="middle" fontFamily="var(--font-mono)" fontSize={11} fill="var(--color-fg-3)">
                                {t}
                            </text>
                        ))}
                        <text x={1176} y={394} textAnchor="end" fontFamily="var(--font-mono)" fontSize={11} fill="var(--color-fg-4)">{t('forecast.fanChart.years')}</text>

                        <g clipPath="url(#fanclip)">
                            <path d={bandPath(p10, p90)} fill="var(--color-primary)" fillOpacity={0.16} stroke="none" />
                            <path d={bandPath(p25, p75)} fill="var(--color-primary)" fillOpacity={0.32} stroke="none" />
                            <polyline points={medianPoints} fill="none" stroke="var(--color-primary)" strokeWidth={2.6} />

                            {target != null && (
                                <>
                                    <line x1={PAD_L} x2={F_W - PAD_R} y1={fy(target)} y2={fy(target)} stroke="var(--color-fg-2)" strokeWidth={1.5} strokeDasharray="5 4" />
                                    <text x={F_W - PAD_R} y={fy(target) - 8} textAnchor="end" fontFamily="var(--font-mono)" fontWeight={700} fontSize={11} fill="var(--color-fg-2)">
                                        {t('forecast.fanChart.goal', { value: fmtAxis(target, convertFromCNY, currencySymbol) })}
                                    </text>
                                </>
                            )}
                            {currentNw != null && (
                                <>
                                    <line x1={PAD_L} x2={F_W - PAD_R} y1={fy(currentNw)} y2={fy(currentNw)} stroke="var(--color-fg-5)" strokeDasharray="2 4" />
                                    <text x={78} y={fy(currentNw) - 6} fontFamily="var(--font-mono)" fontSize={10} fill="var(--color-fg-4)">{t('forecast.fanChart.today')}</text>
                                </>
                            )}
                            {p50CrossYear != null && target != null && (
                                <>
                                    <line x1={fx(p50CrossYear)} x2={fx(p50CrossYear)} y1={fy(target)} y2={372} stroke="var(--color-primary)" strokeWidth={1} strokeDasharray="2 3" />
                                    <circle cx={fx(p50CrossYear)} cy={fy(target)} r={6} fill="var(--color-primary)" stroke="var(--color-card)" strokeWidth={2} />
                                    <text x={fx(p50CrossYear)} y={fy(target) - 14} textAnchor="middle" fontFamily="var(--font-mono)" fontWeight={700} fontSize={11} fill="var(--color-primary)">
                                        {t('forecast.fanChart.medianYears', { years: p50CrossYear.toFixed(1) })}
                                    </text>
                                </>
                            )}
                        </g>
                    </svg>
                )}
            </div>
        </div>
    );
};
