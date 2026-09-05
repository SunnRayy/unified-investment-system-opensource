import i18n from '../../i18n';
import { authFetch } from '../authFetch';
import { API_BASE } from './base';
import type {
    AllocationItem, CompassSummary, AllocationRow, CompassMarkdown,
    WealthAsset, WealthOSSummary, CompassAllocationEnvelope,
} from './types';

export const portfolioApi = {
    getAllocation: async (includeNonRebalanceable = false): Promise<AllocationItem[]> => {
        const res = await authFetch(`${API_BASE}/dashboard/allocation?include_non_rebalanceable=${includeNonRebalanceable}`);
        if (!res.ok) throw new Error(i18n.t('errors:portfolio.allocation', { status: res.status }));
        return res.json();
    },

    getCompassSummary: async (includeNonRebalanceable = false): Promise<CompassSummary> => {
        const res = await authFetch(`${API_BASE}/compass/summary?include_non_rebalanceable=${includeNonRebalanceable}`);
        if (!res.ok) throw new Error(i18n.t('errors:portfolio.compassSummary'));
        return res.json();
    },

    /**
     * Fetch compass allocation data.
     *
     * When includePending=false (default): returns AllocationRow[] (plain list).
     * When includePending=true: backend returns CompassAllocationEnvelope
     *   { allocation: AllocationRow[], meta: { pending_trade_count, is_provisional } }.
     * This function always resolves to { rows, meta } — meta is null when includePending=false.
     */
    getCompassAllocation: async (
        includeNonRebalanceable = false,
        includePending = false,
    ): Promise<{ rows: AllocationRow[]; meta: CompassAllocationEnvelope['meta'] | null }> => {
        const url = `${API_BASE}/compass/allocation?include_non_rebalanceable=${includeNonRebalanceable}&include_pending=${includePending}`;
        const res = await authFetch(url);
        if (!res.ok) throw new Error(i18n.t('errors:portfolio.compassAllocation'));
        const data = await res.json();
        if (includePending && data && !Array.isArray(data) && 'allocation' in data) {
            // Envelope shape: { allocation: AllocationRow[], meta: CompassAllocationMeta }
            return { rows: (data as CompassAllocationEnvelope).allocation, meta: (data as CompassAllocationEnvelope).meta };
        }
        // Plain list (legacy / default)
        return { rows: data as AllocationRow[], meta: null };
    },

    getCompassMarkdown: async (): Promise<CompassMarkdown> => {
        const res = await authFetch(`${API_BASE}/compass/markdown`);
        if (!res.ok) throw new Error(i18n.t('errors:portfolio.compassMarkdown'));
        return res.json();
    },

    getWealthOSAssets: async (includeNonRebalanceable = false): Promise<{ assets: WealthAsset[], non_rebalanceable_assets: WealthAsset[] }> => {
        const res = await authFetch(`${API_BASE}/wealthos/assets?include_non_rebalanceable=${includeNonRebalanceable}`);
        if (!res.ok) throw new Error(i18n.t('errors:portfolio.wealthOSAssets', { status: res.status }));
        return res.json();
    },

    getWealthOSSummary: async (includeNonRebalanceable = false): Promise<WealthOSSummary> => {
        const res = await authFetch(`${API_BASE}/wealthos/summary?include_non_rebalanceable=${includeNonRebalanceable}`);
        if (!res.ok) return {
            total_lifetime_gain: 0,
            lifetime_gain_pct: 0,
            annualized_return: null,
            active_asset_count: 0,
            total_asset_count: 0
        };
        return res.json();
    },
};
