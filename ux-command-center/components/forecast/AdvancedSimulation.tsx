import React, { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { AnalyticsAPI, ProjectionDefaults, ProjectionResult } from '../../src/services/api';
import { useFormatCurrency } from '../../src/utils/format';
import { usePortfolioFilter } from '../../src/context/usePortfolioFilter';

/**
 * AdvancedSimulation — "Your Path" (W-5) Section 2.6, "Advanced: Custom
 * Simulation". docs/design/2026-07-26-your-path.dc.html.md §2.6.
 *
 * Collapsible, closed by default — this is the demoted what-if tool (the
 * old "Explore a different scenario" section). Every number still comes
 * from a live GET /analytics/projection call (Monte Carlo, backend) —
 * changing the inputs and clicking Run is not client-side projection math,
 * it is a parameterized call to the SAME engine as everywhere else on this
 * page.
 */

const INPUT_STYLE: React.CSSProperties = {
    width: '100%', fontSize: 13, padding: '7px 10px', borderRadius: 8,
    border: '1px solid var(--color-border)', background: 'var(--color-card)', color: 'var(--color-fg-1)',
};
const LABEL_STYLE: React.CSSProperties = {
    display: 'block', fontSize: 11, fontWeight: 600, color: 'var(--color-fg-3)', marginBottom: 6,
};

export const AdvancedSimulation: React.FC = () => {
    const { t } = useTranslation('reports');
    const { includeNonRebalanceable } = usePortfolioFilter();
    const formatMoney = useFormatCurrency();
    const [open, setOpen] = useState(false);
    const [defaults, setDefaults] = useState<ProjectionDefaults | null>(null);
    const [years, setYears] = useState(10);
    const [returnPct, setReturnPct] = useState(7);
    const [volatilityPct, setVolatilityPct] = useState(15);
    const [contribution, setContribution] = useState(0);
    const [projection, setProjection] = useState<ProjectionResult | null>(null);
    const [loading, setLoading] = useState(false);

    useEffect(() => {
        AnalyticsAPI.getProjectionDefaults().then(d => {
            setDefaults(d);
            setReturnPct(d.suggested_return != null ? Math.round(d.suggested_return * 1000) / 10 : 7);
            setVolatilityPct(d.suggested_volatility != null ? Math.round(d.suggested_volatility * 1000) / 10 : 15);
            setContribution(d.suggested_contribution_run_rate != null ? Math.round(d.suggested_contribution_run_rate) : 0);
        }).catch(() => { /* leave form defaults in place — the page's shared error banner covers the primary fetches */ });
    }, []);

    const runSimulation = async () => {
        setLoading(true);
        try {
            const safeYears = Number.isFinite(years) && years >= 1 ? Math.round(years) : 10;
            const params: Record<string, string> = {
                years: safeYears.toString(),
                annual_return: (returnPct / 100).toString(),
                annual_volatility: (volatilityPct / 100).toString(),
                annual_contribution: (contribution * 12).toString(),
                seed: '42',
            };
            const res = await AnalyticsAPI.getProjection(params, includeNonRebalanceable);
            if (res && Array.isArray(res.years)) setProjection(res);
        } catch {
            // no separate error state here — a failed what-if run just leaves the previous result visible.
        } finally {
            setLoading(false);
        }
    };

    return (
        <div className="card">
            <div className="adv-head" onClick={() => setOpen(v => !v)}>
                <span className="card-title">
                    <span className="material-symbols-outlined">tune</span>
                    {t('forecast.advancedSimulation.title')}
                </span>
                <span className="material-symbols-outlined">{open ? 'expand_less' : 'expand_more'}</span>
            </div>
            {open && (
                <div className="card-body">
                    <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: 16 }}>
                        <div>
                            <label style={LABEL_STYLE}>{t('forecast.advancedSimulation.years')}</label>
                            <input type="number" style={INPUT_STYLE} value={years} onChange={e => setYears(Number(e.target.value))} />
                        </div>
                        <div>
                            <label style={LABEL_STYLE}>{t('forecast.advancedSimulation.expectedReturn')}</label>
                            <input type="number" step="0.1" style={INPUT_STYLE} value={returnPct} onChange={e => setReturnPct(Number(e.target.value))} />
                        </div>
                        <div>
                            <label style={LABEL_STYLE}>{t('forecast.advancedSimulation.volatility')}</label>
                            <input type="number" step="0.1" style={INPUT_STYLE} value={volatilityPct} onChange={e => setVolatilityPct(Number(e.target.value))} />
                        </div>
                        <div>
                            <label style={LABEL_STYLE}>{t('forecast.advancedSimulation.monthlyContribution')}</label>
                            <input type="number" style={INPUT_STYLE} value={contribution} onChange={e => setContribution(Number(e.target.value))} />
                            <div style={{ fontSize: 9.5, color: 'var(--color-fg-4)', marginTop: 4 }}>
                                {t('forecast.advancedSimulation.defaultsHint')}
                                {defaults?.suggested_contribution_run_rate != null && (
                                    <> ({formatMoney(defaults.suggested_contribution_run_rate)}{t('forecast.advancedSimulation.perMonth')})</>
                                )}
                            </div>
                        </div>
                    </div>

                    <div style={{ marginTop: 16, display: 'flex', justifyContent: 'flex-end' }}>
                        <button type="button" className="btn btn--primary" onClick={runSimulation} disabled={loading}>
                            <span className="material-symbols-outlined">{loading ? 'hourglass_empty' : 'play_arrow'}</span>
                            {loading ? t('forecast.advancedSimulation.running') : t('forecast.advancedSimulation.runSimulation')}
                        </button>
                    </div>

                    {projection?.final_value_stats && (
                        <div className="kpi-row cols-4" style={{ marginTop: 16 }}>
                            <div className="kpi-card">
                                <span className="kpi-label">{t('forecast.advancedSimulation.mean')}</span>
                                <span className="kpi-value">{formatMoney(projection.final_value_stats.mean)}</span>
                            </div>
                            <div className="kpi-card">
                                <span className="kpi-label">{t('forecast.advancedSimulation.median')}</span>
                                <span className="kpi-value accent">{formatMoney(projection.final_value_stats.median)}</span>
                            </div>
                            <div className="kpi-card">
                                <span className="kpi-label">{t('forecast.advancedSimulation.worst')}</span>
                                <span className="kpi-value neg">{formatMoney(projection.final_value_stats.min)}</span>
                            </div>
                            <div className="kpi-card">
                                <span className="kpi-label">{t('forecast.advancedSimulation.best')}</span>
                                <span className="kpi-value pos">{formatMoney(projection.final_value_stats.max)}</span>
                            </div>
                        </div>
                    )}
                </div>
            )}
        </div>
    );
};
