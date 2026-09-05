import React, { useEffect, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { useNavigate } from 'react-router-dom';
import type { ForecastLevers } from '../../src/services/api/types';
import { useFormatCurrency } from '../../src/utils/format';
import { forecastApi } from '../../src/services/api/forecast';

/**
 * WhereToFocus — "Your Path" (W-5) Section 2.3, "Where To Focus".
 * docs/design/2026-07-26-your-path.dc.html.md §2.3. Replaces LeverTable.tsx's
 * fixed-preset table with three live sliders, ranked by years saved.
 *
 * NO PROJECTION MATH HERE. Every slider move debounces (~150ms) a call to
 * GET /forecast/levers?savings_pct=&return_pp=&volatility_pp= (W-2,
 * src/services/forecast_levers.py) — the SAME server-side sensitivity engine
 * the fixed presets used, just queried at an arbitrary slider position
 * instead of the three hardcoded steps. The previous response is held while
 * a new one is in flight so the bars/deltas never flicker or blank out on a
 * ~5ms round trip (design-implementation plan §4.3).
 *
 * `baseLevers` (the UNPARAMETERIZED GET /forecast/levers response, fetched
 * once by the parent) supplies the "Current Pace" reference row only — it
 * must stay stable while the owner drags a slider, so the headline
 * (AnswerSection) never appears to move because of exploration here.
 */

const SAVE_MIN = 0, SAVE_MAX = 60, SAVE_STEP = 5;
const EARN_MIN = 0, EARN_MAX = 6, EARN_STEP = 0.5;
const DERISK_MIN = 0, DERISK_MAX = 10, DERISK_STEP = 0.5;
const DEBOUNCE_MS = 150;

// Opening slider positions. NOT results — these mirror the FIRST preset step of
// each backend lever (_SAVINGS_STEPS_PCT[0]=25, _RETURN_STEPS_PP[0]=1,
// _VOLATILITY_STEPS_PP[0]=5 in src/services/forecast_levers.py), so the page
// opens on the same scenario the backend's `combined` row already defaults to.
//
// They must not be zero: at 0 every lever returns the base years, so all five
// rows render an identical number with empty bars and "ranked by years saved"
// ranks nothing — the section can only demonstrate its point if it opens on a
// non-trivial position. Slider positions are UI configuration (plan §4b
// explicitly permits ranges/steps as constants); the RESULTS stay server-side.
const SAVE_DEFAULT = 25;
const EARN_DEFAULT = 1;
const DERISK_DEFAULT = 5;

interface WhereToFocusProps {
    baseLevers: ForecastLevers | null;
    baseLoading: boolean;
    onNavigateToCashFlow: () => void;
}

function fmtYears(y: number | null): string {
    return y != null ? y.toFixed(1) : '—';
}

function useDebouncedLevers(savingsPct: number, returnPp: number, volatilityPp: number) {
    const [data, setData] = useState<ForecastLevers | null>(null);
    const [loading, setLoading] = useState(false);
    const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

    useEffect(() => {
        if (timerRef.current) clearTimeout(timerRef.current);
        timerRef.current = setTimeout(() => {
            setLoading(true);
            forecastApi
                .getLevers({ savingsPct, returnPp, volatilityPp })
                .then(res => setData(res))
                .catch(() => { /* keep the previous (stale) data on error — never blank the UI */ })
                .finally(() => setLoading(false));
        }, DEBOUNCE_MS);
        return () => {
            if (timerRef.current) clearTimeout(timerRef.current);
        };
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [savingsPct, returnPp, volatilityPp]);

    return { data, loading };
}

export const WhereToFocus: React.FC<WhereToFocusProps> = ({ baseLevers, baseLoading, onNavigateToCashFlow }) => {
    const { t } = useTranslation('reports');
    const navigate = useNavigate();
    const formatMoney = useFormatCurrency();
    const fmtPct = (n: number | null | undefined): string => (n == null ? '—' : `${(n * 100).toFixed(1)}%`);

    const [savingsPct, setSavingsPct] = useState(SAVE_DEFAULT);
    const [returnPp, setReturnPp] = useState(EARN_DEFAULT);
    const [volatilityPp, setVolatilityPp] = useState(DERISK_DEFAULT);

    const { data: sliderLevers, loading: sliderLoading } = useDebouncedLevers(savingsPct, returnPp, volatilityPp);

    // Hold the last non-null response so bars never disappear mid-flight.
    const displayLevers = sliderLevers ?? baseLevers;

    const baseYears = baseLevers?.base?.years_to_target ?? null;

    const savingsRow = displayLevers?.levers?.savings?.length
        ? displayLevers.levers.savings[displayLevers.levers.savings.length - 1]
        : null;
    const returnRow = displayLevers?.levers?.return?.length
        ? displayLevers.levers.return[displayLevers.levers.return.length - 1]
        : null;
    const volatilityRow = displayLevers?.levers?.volatility?.length
        ? displayLevers.levers.volatility[displayLevers.levers.volatility.length - 1]
        : null;
    const combined = displayLevers?.combined ?? null;

    const yearsSaved = (delta: number | null): number | null => (delta == null ? null : -delta);
    const candidates = [yearsSaved(savingsRow?.delta_years ?? null), yearsSaved(returnRow?.delta_years ?? null), yearsSaved(volatilityRow?.delta_years ?? null), yearsSaved(combined?.delta_years ?? null)]
        .filter((v): v is number => v != null && v > 0);
    const maxSaved = candidates.length ? Math.max(...candidates) : 0;

    // Success-green means "this lever actually bought you time". A lever parked
    // at 0 saves nothing, so it must render neutral — colouring a zero delta
    // green reads as an improvement that did not happen.
    const deltaColor = (delta: number | null | undefined): string => {
        const saved = yearsSaved(delta ?? null);
        return saved != null && saved > 0 ? 'var(--color-success)' : 'var(--color-fg-4)';
    };

    const barWidthPct = (delta: number | null): number => {
        const saved = yearsSaved(delta);
        if (saved == null || saved <= 0 || maxSaved <= 0) return 0;
        return Math.max(2, Math.round((saved / maxSaved) * 100));
    };

    const loading = baseLoading && !displayLevers;
    // Subtle opacity dip while a debounced slider request is in flight —
    // deliberately NOT a spinner (design-implementation plan §4.3: a ~5ms
    // round trip must not visibly flicker or show a loading spinner).
    const rowsStyle: React.CSSProperties = { opacity: sliderLoading ? 0.7 : 1, transition: 'opacity 120ms ease' };

    return (
        <div className="card">
            <div className="card-head">
                <span className="card-title">
                    <span className="material-symbols-outlined">bar_chart</span>
                    {t('forecast.whereToFocus.title')}
                </span>
                <span className="card-hint">{t('forecast.whereToFocus.hint')}</span>
            </div>

            {!displayLevers && loading && <div style={{ padding: 20, fontSize: 13, color: 'var(--color-fg-3)' }}>{t('forecast.whereToFocus.computing')}</div>}
            {!displayLevers && !loading && <div style={{ padding: 20, fontSize: 13, color: 'var(--color-fg-3)' }}>{t('forecast.whereToFocus.unavailable')}</div>}

            {displayLevers && (
                <div style={rowsStyle}>
                    {/* Current Pace — static reference row, never moves with the sliders */}
                    <div className="lv-row">
                        <div>
                            <div style={{ fontSize: 13, fontWeight: 700 }}>{t('forecast.whereToFocus.currentPace')}</div>
                            <div style={{ fontSize: 11, color: 'var(--color-fg-3)' }}>
                                {t('forecast.whereToFocus.currentPaceDetail', {
                                    money: formatMoney(baseLevers?.base?.monthly_contribution ?? 0),
                                    returnPct: fmtPct(baseLevers?.base?.expected_return),
                                    volPct: fmtPct(baseLevers?.base?.volatility),
                                })}
                            </div>
                        </div>
                        <div className="lv-track" />
                        <div style={{ textAlign: 'right', fontFamily: 'var(--font-mono)', fontWeight: 700, fontSize: 18, color: 'var(--color-fg-2)' }}>
                            {fmtYears(baseYears)}{t('forecast.whereToFocus.yearsSuffix')}
                        </div>
                    </div>

                    {/* Save more */}
                    <div className="lv-row">
                        <div>
                            <div style={{ fontSize: 13, fontWeight: 700 }}>{t('forecast.whereToFocus.saveMore')}</div>
                            <div style={{ fontSize: 11, color: 'var(--color-fg-3)' }}>
                                {t('forecast.whereToFocus.saveMoreDetail', {
                                    pct: savingsPct,
                                    amount: savingsRow?.monthly_contribution != null ? formatMoney(savingsRow.monthly_contribution) : '—',
                                })}
                            </div>
                            <input
                                type="range"
                                className="lv-slider"
                                min={SAVE_MIN}
                                max={SAVE_MAX}
                                step={SAVE_STEP}
                                value={savingsPct}
                                onChange={e => setSavingsPct(Number(e.target.value))}
                                aria-label={t('forecast.whereToFocus.ariaSave')}
                            />
                            <div style={{ marginTop: 4 }}>
                                <button type="button" onClick={onNavigateToCashFlow} className="btn btn--ghost" style={{ padding: '2px 0', fontSize: 11 }}>
                                    {t('forecast.whereToFocus.cashFlowLink')}
                                </button>
                            </div>
                        </div>
                        <div className="lv-track"><div className="lv-fill" style={{ width: `${barWidthPct(savingsRow?.delta_years ?? null)}%` }} /></div>
                        <div style={{ textAlign: 'right' }}>
                            <div style={{ fontFamily: 'var(--font-mono)', fontWeight: 700, fontSize: 18, color: deltaColor(savingsRow?.delta_years) }}>
                                {fmtYears(savingsRow?.years_to_target ?? null)}{t('forecast.whereToFocus.yearsSuffix')}
                            </div>
                        </div>
                    </div>

                    {/* Earn more */}
                    <div className="lv-row">
                        <div>
                            <div style={{ fontSize: 13, fontWeight: 700 }}>{t('forecast.whereToFocus.earnMore')}</div>
                            <div style={{ fontSize: 11, color: 'var(--color-fg-3)' }}>
                                {t('forecast.whereToFocus.earnMoreDetail', { pp: returnPp, pct: fmtPct(returnRow?.expected_return) })} <span style={{ color: 'var(--zone-yellow-fg)' }}>{t('forecast.whereToFocus.higherVariance')}</span>
                            </div>
                            <input
                                type="range"
                                className="lv-slider"
                                min={EARN_MIN}
                                max={EARN_MAX}
                                step={EARN_STEP}
                                value={returnPp}
                                onChange={e => setReturnPp(Number(e.target.value))}
                                aria-label={t('forecast.whereToFocus.ariaEarn')}
                            />
                            <div style={{ marginTop: 4 }}>
                                <button type="button" onClick={() => navigate('/performance')} className="btn btn--ghost" style={{ padding: '2px 0', fontSize: 11 }}>
                                    {t('forecast.whereToFocus.performanceRiskLink')}
                                </button>
                            </div>
                        </div>
                        <div className="lv-track"><div className="lv-fill" style={{ width: `${barWidthPct(returnRow?.delta_years ?? null)}%` }} /></div>
                        <div style={{ textAlign: 'right' }}>
                            <div style={{ fontFamily: 'var(--font-mono)', fontWeight: 700, fontSize: 18, color: deltaColor(returnRow?.delta_years) }}>
                                {fmtYears(returnRow?.years_to_target ?? null)}{t('forecast.whereToFocus.yearsSuffix')}
                            </div>
                        </div>
                    </div>

                    {/* De-risk */}
                    <div className="lv-row">
                        <div>
                            <div style={{ fontSize: 13, fontWeight: 700 }}>{t('forecast.whereToFocus.deRisk')}</div>
                            <div style={{ fontSize: 11, color: 'var(--color-fg-3)' }}>
                                {t('forecast.whereToFocus.deRiskDetail', { pp: volatilityPp, pct: fmtPct(volatilityRow?.volatility) })} <span style={{ color: 'var(--color-success)' }}>{t('forecast.whereToFocus.freeSameReturn')}</span>
                            </div>
                            <input
                                type="range"
                                className="lv-slider"
                                min={DERISK_MIN}
                                max={DERISK_MAX}
                                step={DERISK_STEP}
                                value={volatilityPp}
                                onChange={e => setVolatilityPp(Number(e.target.value))}
                                aria-label={t('forecast.whereToFocus.ariaDerisk')}
                            />
                            <div style={{ marginTop: 4 }}>
                                <button type="button" onClick={() => navigate('/performance')} className="btn btn--ghost" style={{ padding: '2px 0', fontSize: 11 }}>
                                    {t('forecast.whereToFocus.performanceRiskLink')}
                                </button>
                            </div>
                        </div>
                        <div className="lv-track"><div className="lv-fill" style={{ width: `${barWidthPct(volatilityRow?.delta_years ?? null)}%` }} /></div>
                        <div style={{ textAlign: 'right' }}>
                            <div style={{ fontFamily: 'var(--font-mono)', fontWeight: 700, fontSize: 18, color: deltaColor(volatilityRow?.delta_years) }}>
                                {fmtYears(volatilityRow?.years_to_target ?? null)}{t('forecast.whereToFocus.yearsSuffix')}
                            </div>
                        </div>
                    </div>

                    {/* Combined */}
                    <div className="lv-row combined">
                        <div>
                            <div style={{ fontSize: 13, fontWeight: 700 }}>{t('forecast.whereToFocus.combined')}</div>
                            <div style={{ fontSize: 11, color: 'var(--color-fg-3)' }}>{combined?.label ?? '—'}</div>
                        </div>
                        <div className="lv-track"><div className="lv-fill combined" style={{ width: `${barWidthPct(combined?.delta_years ?? null)}%` }} /></div>
                        <div style={{ textAlign: 'right' }}>
                            <div style={{ fontFamily: 'var(--font-mono)', fontWeight: 700, fontSize: 20, color: deltaColor(combined?.delta_years) }}>
                                {fmtYears(combined?.years_to_target ?? null)}{t('forecast.whereToFocus.yearsSuffix')}
                            </div>
                        </div>
                    </div>
                </div>
            )}
        </div>
    );
};
