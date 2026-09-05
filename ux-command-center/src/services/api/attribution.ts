import i18n from '../../i18n';
import { authFetch } from '../authFetch';
import { API_BASE } from './base';
import { safeReadError } from './base';
import type {
    AttributionLevel,
    AttributionMonthlyResponse,
    AttributionAssetHistoryResponse,
    AttributionSummaryResponse,
    AttributionRecomputeRequest,
    AttributionRecomputeResponse,
} from './types';

/** Monthly Attribution — docs/api-specs/attribution.md (WS-1).
 *  Contract-first: coded against the locked spec response shapes while the
 *  backend router is implemented in parallel. */
export const attributionApi = {
    /** GET /attribution/monthly — one month's decomposition at a roll-up level.
     *  `monthTo` (optional, inclusive) activates range mode — aggregates the
     *  whole [month, monthTo] range server-side (see spec → Range mode). */
    getMonthlyAttribution: async (
        month: string,
        level: AttributionLevel = 'sub_class',
        includeNonRebalanceable = true,
        monthTo?: string,
    ): Promise<AttributionMonthlyResponse> => {
        const params = new URLSearchParams({
            month,
            level,
            include_non_rebalanceable: String(includeNonRebalanceable),
        });
        if (monthTo) params.set('month_to', monthTo);
        const res = await authFetch(`${API_BASE}/attribution/monthly?${params.toString()}`);
        if (!res.ok) throw new Error(await safeReadError(res, i18n.t('errors:attribution.monthly')));
        return res.json();
    },

    /** GET /attribution/asset/{asset_id} — per-asset attribution history (newest first). */
    getAssetAttributionHistory: async (
        assetId: string,
        months = 6,
    ): Promise<AttributionAssetHistoryResponse> => {
        const res = await authFetch(`${API_BASE}/attribution/asset/${encodeURIComponent(assetId)}?months=${months}`);
        if (!res.ok) throw new Error(await safeReadError(res, i18n.t('errors:attribution.assetHistory')));
        return res.json();
    },

    /** GET /attribution/summary — multi-month totals series + savings metrics. */
    getAttributionSummary: async (months = 12): Promise<AttributionSummaryResponse> => {
        const res = await authFetch(`${API_BASE}/attribution/summary?months=${months}`);
        if (!res.ok) throw new Error(await safeReadError(res, i18n.t('errors:attribution.summary')));
        return res.json();
    },

    /** POST /attribution/recompute — recompute newest N months (write, mark_dirty on backend). */
    recomputeAttribution: async (
        body: AttributionRecomputeRequest,
    ): Promise<AttributionRecomputeResponse> => {
        const res = await authFetch(`${API_BASE}/attribution/recompute`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(body),
        });
        if (!res.ok) throw new Error(await safeReadError(res, i18n.t('errors:attribution.recompute')));
        return res.json();
    },
};
