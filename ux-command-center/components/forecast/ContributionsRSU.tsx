import React, { useEffect, useMemo, useState } from 'react';
import { useTranslation } from 'react-i18next';
import type { ContributionsSummary, ContributionsWindow, InvestmentContributionMonth } from '../../src/services/api/types';
import { useFormatCurrency } from '../../src/utils/format';
import { useCurrency } from '../../src/context/useCurrency';
import { useTheme } from '../../src/theme/useTheme';
import { northStarApi } from '../../src/services/api/north-star';

/**
 * ContributionsRSU — "Your Path" (W-5) Section 2.5, "Contributions & RSU".
 * docs/design/2026-07-26-your-path.dc.html.md §2.5.
 *
 * KPI row is a direct render of GET /north-star/contributions (fixed
 * trailing-12M basis, `contributions12` fetched by the parent — see
 * Analytics.tsx's pre-existing `loadContributions12` docstring for why this
 * MUST be fixed-12, independent of the trend chart's own 12M/36M/All
 * toggle). The trend chart below re-fetches on its own toggle, matching the
 * pre-existing Cash Flow "Net New Invested" behavior it replaces.
 *
 * Destination palette: reuses the dataviz-skill-validated categorical set
 * (blue/aqua/yellow/violet) already used elsewhere in the app for these same
 * destinations, rather than the design mock's generic --chart-1..4
 * tokens — same categories should keep the same colors across the app, and
 * this palette is already contrast-validated (scripts/validate_palette.js).
 * `us_ibkr` (added 2026-08-01 with the IBKR 投资理财 columns) takes the
 * palette's orange slot and is stacked LAST so the existing four keep both
 * their colors and their stack order — the re-validated 5-slot order
 * blue/aqua/yellow/violet/orange passes every check in light and dark
 * (worst adjacent CVD ΔE 9.1 light / 8.4 dark).
 *
 * The destination key set is NOT hardcoded: the backend derives it from the
 * ie_column mapping, so a new bucket must appear in the bars on its own or the
 * stack would silently understate gross_invested.
 */

const DEST_COLORS_LIGHT: Record<string, string> = {
    cn_fund: '#2a78d6',
    us_schwab: '#1baf7a',
    gold: '#eda100',
    bank_wealth: '#4a3aa7',
    us_ibkr: '#eb6834',
};
const DEST_COLORS_DARK: Record<string, string> = {
    cn_fund: '#3987e5',
    us_schwab: '#199e70',
    gold: '#c98500',
    bank_wealth: '#9085e9',
    us_ibkr: '#d95926',
};
/** i18n key suffix (under forecast.contributionsRSU.destLabels) for each known
 *  destination. Unknown buckets fall back to the raw key (see destLabel). */
const DEST_LABEL_KEYS: Record<string, string> = {
    cn_fund: 'cnFund',
    us_schwab: 'usSchwab',
    gold: 'gold',
    bank_wealth: 'bankWealth',
    us_ibkr: 'usIbkr',
};
/** Fixed stack/legend order for the destinations we know. Unknown buckets are
 *  appended (never dropped) in a stable alphabetical order. */
const DEST_ORDER = ['cn_fund', 'us_schwab', 'gold', 'bank_wealth', 'us_ibkr'];

/** Every destination present in the series, known ones first in palette order. */
function destKeysOf(series: Array<{ by_destination: Record<string, number> }>): string[] {
    const seen = new Set<string>();
    for (const m of series) for (const k of Object.keys(m.by_destination ?? {})) seen.add(k);
    const known = DEST_ORDER.filter(k => seen.has(k));
    const unknown = [...seen].filter(k => !DEST_ORDER.includes(k)).sort();
    return [...known, ...unknown];
}

interface ContributionsRSUProps {
    contributions12: ContributionsSummary | null;
    /** The SAME run-rate the headline (AnswerSection) uses — for the trend
     * chart's Y-axis ceiling only (`cfTop`), never a second computation. */
    runRateMonthly: number | null;
}

interface Bar {
    label: string;
    by_destination: Record<string, number>;
    total: number;
}

/** design-record §2.5: "All" range with >24 buckets is averaged into 24
 * roughly-equal chunks so the chart stays readable over 78+ months of
 * history. Pure data reduction for display — no projection math. */
function chunkTo24(series: InvestmentContributionMonth[]): Bar[] {
    if (series.length <= 24) {
        return series.map(m => ({ label: m.month, by_destination: m.by_destination, total: m.gross_invested }));
    }
    const chunkCount = 24;
    const chunkSize = Math.ceil(series.length / chunkCount);
    const keys = destKeysOf(series);
    const out: Bar[] = [];
    for (let i = 0; i < series.length; i += chunkSize) {
        const slice = series.slice(i, i + chunkSize);
        const n = slice.length || 1;
        const avg: Record<string, number> = {};
        for (const k of keys) {
            avg[k] = slice.reduce((acc, m) => acc + (m.by_destination?.[k] ?? 0), 0) / n;
        }
        out.push({
            label: `${slice[0].month}–${slice[slice.length - 1].month}`,
            by_destination: avg,
            total: keys.reduce((acc, k) => acc + avg[k], 0),
        });
    }
    return out;
}

const C_W = 1200, C_H = 260, C_PAD_L = 56, C_PAD_R = 16, C_PAD_T = 16, C_PAD_B = 28;
const C_PLOT_W = C_W - C_PAD_L - C_PAD_R;
const C_PLOT_H = C_H - C_PAD_T - C_PAD_B;

function fmtAxis(cnyValue: number, convertFromCNY: (n: number) => number, symbol: string): string {
    const v = convertFromCNY(cnyValue);
    const abs = Math.abs(v);
    if (abs >= 1_000_000) return `${symbol}${(v / 1_000_000).toFixed(1)}M`;
    if (abs >= 1_000) return `${symbol}${(v / 1_000).toFixed(0)}k`;
    return `${symbol}${v.toFixed(0)}`;
}

export const ContributionsRSU: React.FC<ContributionsRSUProps> = ({ contributions12, runRateMonthly }) => {
    const { t } = useTranslation('reports');
    const formatMoney = useFormatCurrency();
    const { convertFromCNY, currencySymbol } = useCurrency();
    const { mode } = useTheme();
    const destColors = mode === 'night' ? DEST_COLORS_DARK : DEST_COLORS_LIGHT;
    const fmtPct = (n: number | null | undefined): string => (n == null ? '—' : `${(n * 100).toFixed(1)}%`);
    const destLabel = (key: string): string => {
        const labelKey = DEST_LABEL_KEYS[key];
        return labelKey ? t(`forecast.contributionsRSU.destLabels.${labelKey}`) : key;
    };

    const [range, setRange] = useState<ContributionsWindow>('36');
    const [trendData, setTrendData] = useState<ContributionsSummary | null>(null);

    useEffect(() => {
        let cancelled = false;
        northStarApi.getContributions(range).then(res => {
            if (!cancelled) setTrendData(res);
        }).catch(() => { /* the shared page error banner is owned by the parent's other fetches */ });
        return () => { cancelled = true; };
    }, [range]);

    const bars = useMemo<Bar[]>(() => {
        const series = trendData?.investment?.series ?? [];
        if (range === '12') return series.slice(-12).map(m => ({ label: m.month, by_destination: m.by_destination, total: m.gross_invested }));
        if (range === '36') return series.slice(-36).map(m => ({ label: m.month, by_destination: m.by_destination, total: m.gross_invested }));
        return chunkTo24(series);
    }, [trendData, range]);

    /** Destinations actually present in the payload — the backend derives them
     *  from the ie_column mapping, so this is never a hardcoded list. */
    const destKeys = useMemo(() => destKeysOf(bars), [bars]);

    const cfTop = useMemo(() => {
        const maxTotal = bars.length ? Math.max(...bars.map(b => b.total)) : 0;
        const floorBasis = Math.max(maxTotal, (runRateMonthly ?? 0) * 1.4, 10_000);
        return Math.ceil(floorBasis / 10_000) * 10_000;
    }, [bars, runRateMonthly]);

    const band = bars.length ? C_PLOT_W / bars.length : 0;
    const cyAt = (v: number) => C_PAD_T + C_PLOT_H - (cfTop > 0 ? (v / cfTop) * C_PLOT_H : 0);
    const xTickEvery = Math.max(1, Math.floor(bars.length / 6));

    const inv = contributions12?.investment;
    const rsu = contributions12?.rsu;

    return (
        <>
            <div className="kpi-row cols-4">
                <div className="kpi-card">
                    <span className="kpi-label">{t('forecast.contributionsRSU.netNewInvested')}</span>
                    <span className="kpi-value">{inv ? formatMoney(inv.net_external_ttm) : '—'}</span>
                    {/* The rate paired with this KPI must be the INVESTMENT rate —
                        (net external + retained RSU) ÷ income. savings_rate_ttm is a
                        different, much larger metric (everything not spent, ~60% vs
                        ~42% live) and reads as this figure's ratio if shown here
                        (plan 2026-08-01-ie-column-mapping-and-ibkr-amounts.md §WS-G). */}
                    <span className="kpi-sub">{t('forecast.contributionsRSU.fixed12mBasis', { pct: fmtPct(inv?.investment_rate_ttm) })}</span>
                </div>
                <div className="kpi-card">
                    <span className="kpi-label">{t('forecast.contributionsRSU.contributionRunRate')}</span>
                    <span className="kpi-value accent" style={{ fontSize: 18 }}>
                        {runRateMonthly != null ? formatMoney(runRateMonthly) : '—'}
                        <span style={{ fontSize: 12, color: 'var(--color-fg-3)' }}>{t('forecast.contributionsRSU.perMonth')}</span>
                    </span>
                    <span className="kpi-sub">{t('forecast.contributionsRSU.trailing12m')}</span>
                </div>
                <div className="kpi-card">
                    <span className="kpi-label">{t('forecast.contributionsRSU.rsuVested')}</span>
                    <span className="kpi-value">{rsu ? formatMoney(rsu.vest_gross_ttm) : '—'}</span>
                    <span className="kpi-sub">{t('forecast.contributionsRSU.trailing12mInvested')}</span>
                </div>
                <div className="kpi-card">
                    <span className="kpi-label">{t('forecast.contributionsRSU.rsuRetained')}</span>
                    <span className="kpi-value pos">{rsu ? formatMoney(rsu.retained_ttm) : '—'}</span>
                    <span className="kpi-sub">{t('forecast.contributionsRSU.trailing12mInvested')}</span>
                </div>
            </div>

            <div className="card">
                <div className="card-head">
                    <span className="card-title">
                        <span className="material-symbols-outlined">waterfall_chart</span>
                        {t('forecast.contributionsRSU.trendTitle')}
                    </span>
                    <div className="card-head-actions">
                        <span className="legend">
                            {destKeys.map(k => (
                                <span key={k} className="lg-item"><span className="lg-dot" style={{ background: destColors[k] ?? 'var(--color-fg-3)' }} />{destLabel(k)}</span>
                            ))}
                        </span>
                        <div className="seg">
                            {(['12', '36', 'all'] as ContributionsWindow[]).map(w => (
                                <button key={w} type="button" className={range === w ? 'on' : ''} onClick={() => setRange(w)}>
                                    {w === '12' ? t('forecast.contributionsRSU.range12m') : w === '36' ? t('forecast.contributionsRSU.range36m') : t('forecast.contributionsRSU.rangeAll')}
                                </button>
                            ))}
                        </div>
                    </div>
                </div>
                <div className="card-body">
                    {bars.length === 0 ? (
                        <div style={{ height: 200, display: 'flex', alignItems: 'center', justifyContent: 'center', color: 'var(--color-fg-3)', fontSize: 13 }}>
                            {t('forecast.contributionsRSU.noHistory')}
                        </div>
                    ) : (
                        <div style={{ overflowX: 'auto' }}>
                            <svg viewBox={`0 0 ${C_W} ${C_H}`} style={{ width: '100%', minWidth: Math.max(480, bars.length * 14), display: 'block' }}>
                                {[0, 1, 2, 3].map(i => {
                                    const v = (i / 3) * cfTop;
                                    const y = cyAt(v);
                                    return (
                                        <g key={i}>
                                            <line x1={C_PAD_L} x2={C_W - C_PAD_R} y1={y} y2={y} stroke="var(--color-border-soft)" strokeDasharray="3 3" />
                                            <text x={C_PAD_L - 8} y={y} textAnchor="end" dy="0.32em" fontFamily="var(--font-mono)" fontSize={10} fill="var(--color-fg-3)">
                                                {fmtAxis(v, convertFromCNY, currencySymbol)}
                                            </text>
                                        </g>
                                    );
                                })}
                                {bars.map((bar, i) => {
                                    if (i % xTickEvery !== 0) return null;
                                    const x = C_PAD_L + band * i + band * 0.5;
                                    return (
                                        <text key={bar.label} x={x} y={C_H - 8} textAnchor="middle" fontFamily="var(--font-mono)" fontSize={9} fill="var(--color-fg-3)">
                                            {bar.label}
                                        </text>
                                    );
                                })}
                                {bars.map((bar, i) => {
                                    const x = C_PAD_L + band * i + band * 0.15;
                                    const w = band * 0.7;
                                    let cumulative = 0;
                                    return (
                                        <g key={bar.label}>
                                            {destKeys.map(k => {
                                                const v = bar.by_destination[k] ?? 0;
                                                const yTop = cyAt(cumulative + v);
                                                const yBottom = cyAt(cumulative);
                                                cumulative += v;
                                                if (v <= 0) return null;
                                                return (
                                                    <rect
                                                        key={k}
                                                        x={x}
                                                        y={yTop}
                                                        width={Math.max(0, w)}
                                                        height={Math.max(0, yBottom - yTop)}
                                                        fill={destColors[k] ?? 'var(--color-fg-3)'}
                                                    />
                                                );
                                            })}
                                        </g>
                                    );
                                })}
                            </svg>
                        </div>
                    )}
                </div>
            </div>
        </>
    );
};
