import i18n from '../../i18n';
import { authFetch } from '../authFetch';
import { API_BASE } from './base';
import type { ForecastLevers } from './types';

/** W-2 optional slider params (docs/plans/2026-07-26-your-path-design-
 * implementation.md §4.3) — omitted entirely -> byte-for-byte the pre-W-2
 * response. All computed server-side; the frontend contains no projection
 * math (see components/forecast/WhereToFocus.tsx). */
export interface ForecastLeversParams {
    savingsPct?: number;
    returnPp?: number;
    volatilityPp?: number;
}

export const forecastApi = {
    /** GET /forecast/levers — base case + savings/return/volatility sensitivity
     * grid, evaluated at the volatility-drag-adjusted median_return (R-1/R-2,
     * docs/plans/2026-07-25-forecast-planning-redesign.md). Every numeric field
     * in the response can be null when an input is unavailable — callers must
     * render an em-dash, never fabricate a 0. `params` (W-2) adds one extra row
     * per supplied slider position; all three omitted reproduces the pre-W-2
     * response exactly. */
    getLevers: async (params?: ForecastLeversParams): Promise<ForecastLevers> => {
        const qs = new URLSearchParams();
        if (params?.savingsPct != null) qs.set('savings_pct', String(params.savingsPct));
        if (params?.returnPp != null) qs.set('return_pp', String(params.returnPp));
        if (params?.volatilityPp != null) qs.set('volatility_pp', String(params.volatilityPp));
        const query = qs.toString();
        const res = await authFetch(`${API_BASE}/forecast/levers${query ? `?${query}` : ''}`);
        if (!res.ok) throw new Error(i18n.t('errors:forecast.levers'));
        return res.json();
    },
};
