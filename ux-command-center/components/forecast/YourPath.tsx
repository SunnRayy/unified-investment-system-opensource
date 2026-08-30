import React, { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import '../../src/styles/your-path.css';
import { AnalyticsAPI, ProjectionResult } from '../../src/services/api';
import { forecastApi } from '../../src/services/api/forecast';
import { northStarApi } from '../../src/services/api/north-star';
import type { ForecastLevers, ContributionsSummary, TimeInMarket } from '../../src/services/api/types';
import { usePortfolioFilter } from '../../src/context/usePortfolioFilter';
import { AnswerSection } from './AnswerSection';
import { FanChart } from './FanChart';
import { WhereToFocus } from './WhereToFocus';
import { OnTrackTiles } from './OnTrackTiles';
import { ContributionsRSU } from './ContributionsRSU';
import { AdvancedSimulation } from './AdvancedSimulation';

/**
 * YourPath — "Your Path" (W-5) top-level composer.
 * docs/design/2026-07-26-your-path.dc.html.md, all seven sections in the
 * fixed order the design specifies:
 *   2.1 The Answer -> 2.2 Portfolio Projection to Goal -> 2.3 Where To
 *   Focus -> 2.4 Are You On Track? -> 2.5 Contributions & RSU -> 2.6
 *   Advanced: Custom Simulation -> 2.7 stamp row.
 *
 * Owns all state/fetching for the tab (previously split across Analytics.tsx
 * top-level state). Every fetch here is independent (no awaiting one before
 * starting the next) so a slow one never blocks a fast one.
 */

// HORIZON = max(20, min(32, ceil(p75Crossing + 9))) — design-record §2.2
// chart geometry (how far out the fan chart's X axis extends), NOT a result
// figure. Falls back to the upper bound (32) when p75 crossing is unknown
// (goal unreachable within the analytic solver's horizon) so the chart still
// renders a sensible span instead of guessing a shorter one.
function computeHorizon(p75CrossingYears: number | null): number {
    const raw = p75CrossingYears != null ? Math.ceil(p75CrossingYears + 9) : 32;
    return Math.max(20, Math.min(32, raw));
}

interface YourPathProps {
    onGoToGoals: () => void;
}

export const YourPath: React.FC<YourPathProps> = ({ onGoToGoals }) => {
    const { t } = useTranslation('reports');
    const { includeNonRebalanceable } = usePortfolioFilter();

    const [forecastLevers, setForecastLevers] = useState<ForecastLevers | null>(null);
    const [forecastLeversLoading, setForecastLeversLoading] = useState(false);
    const [answerProjection, setAnswerProjection] = useState<ProjectionResult | null>(null);
    const [answerProjectionLoading, setAnswerProjectionLoading] = useState(false);
    const [contributions12, setContributions12] = useState<ContributionsSummary | null>(null);
    const [timeInMarket, setTimeInMarket] = useState<TimeInMarket | null>(null);
    const [northStarLoading, setNorthStarLoading] = useState(false);
    const [error, setError] = useState<string | null>(null);

    useEffect(() => {
        setForecastLeversLoading(true);
        forecastApi.getLevers()
            .then(setForecastLevers)
            .catch(err => { console.error(err); setError(t('forecast.yourPath.errors.levers')); })
            .finally(() => setForecastLeversLoading(false));

        northStarApi.getContributions('12')
            .then(setContributions12)
            .catch(err => { console.error(err); setError(t('forecast.yourPath.errors.contributions')); });

        setNorthStarLoading(true);
        northStarApi.getNorthStarPanel()
            .then(panel => setTimeInMarket(panel.time_in_market))
            .catch(err => console.error('Failed to load North Star panel', err))
            .finally(() => setNorthStarLoading(false));
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [includeNonRebalanceable]);

    // Section 2.2's Monte Carlo fan run — fires once forecastLevers.base has
    // arrived, driven ENTIRELY by base's live values (never by the Advanced
    // Simulation form). Horizon is computed from the SAME crossing_years
    // the "likely range" caption renders, so the chart's X axis and the
    // headline's stated range describe the same underlying computation.
    useEffect(() => {
        const base = forecastLevers?.base;
        if (!base || answerProjection || answerProjectionLoading) return;
        if (base.expected_return == null || base.volatility == null || base.target == null) return;

        const horizon = computeHorizon(base.crossing_years?.p75 ?? null);
        setAnswerProjectionLoading(true);
        const params: Record<string, string> = {
            years: String(horizon),
            simulations: '10000',
            annual_return: String(base.expected_return),
            annual_volatility: String(base.volatility),
            annual_contribution: String((base.monthly_contribution ?? 0) * 12),
            goal_target: String(base.target),
            seed: '42',
        };
        AnalyticsAPI.getProjection(params, includeNonRebalanceable)
            .then(res => { if (res && Array.isArray(res.years)) setAnswerProjection(res); })
            .catch(err => { console.error(err); setError(t('forecast.yourPath.errors.projection')); })
            .finally(() => setAnswerProjectionLoading(false));
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [forecastLevers]);

    const scrollToCashFlow = () => {
        document.getElementById('your-path-contributions')?.scrollIntoView({ behavior: 'smooth', block: 'start' });
    };

    return (
        <div className="your-path">
            {error && (
                <div className="sig sig--danger" style={{ padding: '10px 14px' }}>{error}</div>
            )}

            <AnswerSection levers={forecastLevers} loading={forecastLeversLoading} onGoToGoals={onGoToGoals} />

            <FanChart levers={forecastLevers} projection={answerProjection} loading={forecastLeversLoading || answerProjectionLoading} />

            <WhereToFocus baseLevers={forecastLevers} baseLoading={forecastLeversLoading} onNavigateToCashFlow={scrollToCashFlow} />

            <OnTrackTiles timeInMarket={timeInMarket} investment={contributions12?.investment ?? null} loading={northStarLoading} />

            <div id="your-path-contributions" style={{ display: 'flex', flexDirection: 'column', gap: 20 }}>
                <ContributionsRSU contributions12={contributions12} runRateMonthly={forecastLevers?.base?.monthly_contribution ?? null} />
            </div>

            <AdvancedSimulation />

            <div className="stamp-row">
                <span>{t('forecast.yourPath.stampVersion')}</span>
                <span className="sep" />
                <span>{t('forecast.yourPath.stampBasis')}</span>
                <span className="sep" />
                <span>{t('forecast.yourPath.stampTz')}</span>
            </div>
        </div>
    );
};
