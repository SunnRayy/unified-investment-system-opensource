import React from 'react';
import { useTranslation } from 'react-i18next';
import type { TimeInMarket, InvestmentContributionsSummary } from '../../src/services/api/types';

/**
 * OnTrackTiles — "Your Path" (W-5) Section 2.4, "Are You On Track?".
 * docs/design/2026-07-26-your-path.dc.html.md §2.4.
 *
 * Two behavior-quality signals, NOT a second forecast — habits that keep the
 * realized return close to what the headline above assumes.
 *
 * Tile 1: Time in Market — GET /north-star/panel's existing `time_in_market`.
 * Tile 2: PARTICIPATION (months-with-any-contribution out of 12) — the
 * design mock's second tile ("N/12 months at or above run-rate",
 * `consistencyAbove = [1,1,1,0,...]`) is REJECTED per design-record §3.1 /
 * ADR-025 §2: a per-month AMOUNT comparison is meaningless on this data
 * (lump-sum investing produced a 341% "savings rate" in one real month).
 * Owner decision 2026-07-26: replace with a pure participation signal —
 * "did any money get invested this month?" — derived from
 * investment_contributions.monthly_investment_flows via the new
 * `months_with_contribution` / `months_with_contribution_window` fields on
 * GET /north-star/contributions?window_months=12 (src/services/
 * investment_contributions.py::contributions_summary_v2). Never fabricated:
 * null -> empty state, never a phantom 0/12.
 *
 * The 12-slot strips are cosmetic detail only, both derived from data
 * already present in the SAME response objects passed in as props (never a
 * second fetch, never re-derived math):
 *   - Time in Market: `timeInMarket.monthly_weights` (already live).
 *   - Participation: `investment.series.slice(-12)`, using the exact same
 *     `gross_invested > 0` rule the backend's months_with_contribution used
 *     — the same boolean read off the same underlying rows, not a
 *     re-implementation of the backend's counting logic.
 */

interface OnTrackTilesProps {
    timeInMarket: TimeInMarket | null;
    investment: InvestmentContributionsSummary | null;
    loading: boolean;
}

function Slots({ filled }: Readonly<{ filled: boolean[] }>) {
    return (
        <div style={{ display: 'flex', gap: 3, marginTop: 10 }}>
            {filled.map((f, i) => (
                <div
                    key={i}
                    style={{
                        width: '100%', height: 16, borderRadius: 3,
                        background: f ? 'var(--color-primary)' : 'transparent',
                        border: f ? 'none' : '1.5px solid var(--color-fg-4)',
                    }}
                />
            ))}
        </div>
    );
}

export const OnTrackTiles: React.FC<OnTrackTilesProps> = ({ timeInMarket, investment, loading }) => {
    const { t } = useTranslation('reports');
    const timSlots = (timeInMarket?.monthly_weights ?? []).slice(-12).map(
        w => w.weight_pct >= (timeInMarket?.target_pct ?? 100),
    );
    const participationSlots = (investment?.series ?? []).slice(-12).map(m => m.gross_invested > 0);

    return (
        <div className="card">
            <div className="card-head">
                <span className="card-title">
                    <span className="material-symbols-outlined">route</span>
                    {t('forecast.onTrackTiles.title')}
                </span>
                <span className="card-hint">{t('forecast.onTrackTiles.hint')}</span>
            </div>
            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 1, background: 'var(--color-border-soft)' }}>
                <div style={{ background: 'var(--color-card)', padding: '20px 22px' }}>
                    <div style={{ fontSize: 10, textTransform: 'uppercase', letterSpacing: '0.06em', color: 'var(--color-fg-3)', fontWeight: 700 }}>
                        {t('forecast.onTrackTiles.timeInMarket')}
                    </div>
                    {loading && !timeInMarket && <div style={{ fontSize: 12, color: 'var(--color-fg-3)', marginTop: 8 }}>{t('forecast.onTrackTiles.loading')}</div>}
                    {timeInMarket?.insufficient_data && (
                        <div style={{ fontSize: 12, color: 'var(--color-fg-3)', marginTop: 8 }}>
                            {timeInMarket.reason ?? t('forecast.onTrackTiles.notEnoughData')}
                        </div>
                    )}
                    {timeInMarket && !timeInMarket.insufficient_data && (
                        <>
                            <div style={{ fontFamily: 'var(--font-mono)', fontWeight: 700, fontSize: 26 }}>
                                {timeInMarket.ratio != null ? `${(timeInMarket.ratio * 100).toFixed(0)}%` : '—'}
                            </div>
                            <div style={{ fontFamily: 'var(--font-mono)', fontSize: 11, color: 'var(--color-fg-3)' }}>
                                {t('forecast.onTrackTiles.monthsFullyInvested', { inMonths: timeInMarket.in_market_months ?? '—', totalMonths: timeInMarket.total_months ?? '—' })}
                            </div>
                            {timSlots.length > 0 && <Slots filled={timSlots} />}
                        </>
                    )}
                </div>

                <div style={{ background: 'var(--color-card)', padding: '20px 22px' }}>
                    <div style={{ fontSize: 10, textTransform: 'uppercase', letterSpacing: '0.06em', color: 'var(--color-fg-3)', fontWeight: 700 }}>
                        {t('forecast.onTrackTiles.participation')}
                    </div>
                    {loading && !investment && <div style={{ fontSize: 12, color: 'var(--color-fg-3)', marginTop: 8 }}>{t('forecast.onTrackTiles.loading')}</div>}
                    {investment && investment.months_with_contribution == null && (
                        <div style={{ fontSize: 12, color: 'var(--color-fg-3)', marginTop: 8 }}>{t('forecast.onTrackTiles.notEnoughData')}</div>
                    )}
                    {investment && investment.months_with_contribution != null && (
                        <>
                            <div style={{ fontFamily: 'var(--font-mono)', fontWeight: 700, fontSize: 26 }}>
                                {investment.months_with_contribution}/{investment.months_with_contribution_window}
                            </div>
                            <div style={{ fontFamily: 'var(--font-mono)', fontSize: 11, color: 'var(--color-fg-3)' }}>
                                {t('forecast.onTrackTiles.monthsWithContribution')}
                            </div>
                            {participationSlots.length > 0 && <Slots filled={participationSlots} />}
                        </>
                    )}
                </div>
            </div>
        </div>
    );
};
