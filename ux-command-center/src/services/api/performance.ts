import i18n from '../../i18n';
import { authFetch } from '../authFetch';
import { API_BASE } from './base';
import type {
    HistoryItem, RiskMetrics, RiskCorrelation,
    PerformanceSummaryResponse, GainsResponse, PerformanceByClassResponse,
    PerformanceReturns, PerformancePeriod, AttributionResult, PerformanceRiskMetrics,
    MoversResponse,
} from './types';

export const performanceApi = {
    getPerformanceHistory: async (
        period: PerformancePeriod = 'all_time',
        excludeNonBalanceable = false,
        includeNonRebalanceable?: boolean,
    ): Promise<HistoryItem[]> => {
        const params = new URLSearchParams({ period });
        if (includeNonRebalanceable !== undefined) {
            params.set('include_non_rebalanceable', String(includeNonRebalanceable));
        } else {
            params.set('exclude_non_balanceable', String(excludeNonBalanceable));
        }
        const res = await authFetch(`${API_BASE}/performance/history?${params.toString()}`);
        if (!res.ok) return [];
        return res.json();
    },

    /** Throws on failure — deliberately.
     *
     *  This used to return `{volatility: 0, sharpe: 0, var_95: 0, beta: 0,
     *  div_score: 0}` when the request failed, which the Risk Matrix page then
     *  rendered as five confident zeros. The page already renders "--" and a
     *  red banner when it has no metrics; it never got the chance, because a
     *  fabricated object is not falsy. A metric nobody could compute must not
     *  be displayed as a metric that came out to zero. */
    getRiskMetrics: async (includeNonRebalanceable = false): Promise<RiskMetrics> => {
        const res = await authFetch(`${API_BASE}/risk/metrics?include_non_rebalanceable=${includeNonRebalanceable}`);
        if (!res.ok) throw new Error(i18n.t('errors:risk.metrics'));
        return res.json();
    },

    /** Throws on failure, for the same reason. An empty `assets` array is a
     *  legitimate answer ("not enough shared history to correlate") and the
     *  page distinguishes it from both loading and failure — but it must mean
     *  that, and not double as the silent shape of a dead request. */
    getRiskCorrelation: async (level: 'top' | 'sub' = 'top', includeNonRebalanceable = false): Promise<RiskCorrelation> => {
        const res = await authFetch(`${API_BASE}/risk/correlation?level=${level}&include_non_rebalanceable=${includeNonRebalanceable}`);
        if (!res.ok) throw new Error(i18n.t('errors:risk.correlation'));
        return res.json();
    },

    getPerformanceSummary: async (
        period: PerformancePeriod = 'all_time',
        excludeNonBalanceable = false,
        includeNonRebalanceable?: boolean,
    ): Promise<PerformanceSummaryResponse> => {
        const params = new URLSearchParams({ period });
        if (includeNonRebalanceable !== undefined) {
            params.set('include_non_rebalanceable', String(includeNonRebalanceable));
        } else {
            params.set('exclude_non_balanceable', String(excludeNonBalanceable));
        }
        const res = await authFetch(`${API_BASE}/performance/summary?${params.toString()}`);
        if (!res.ok) throw new Error(i18n.t('errors:performance.summary'));
        return res.json();
    },

    getGainsAnalysis: async (
        period: PerformancePeriod = 'all_time',
        excludeNonBalanceable = false,
        includeNonRebalanceable?: boolean,
    ): Promise<GainsResponse> => {
        const params = new URLSearchParams({ period });
        if (includeNonRebalanceable !== undefined) {
            params.set('include_non_rebalanceable', String(includeNonRebalanceable));
        } else {
            params.set('exclude_non_balanceable', String(excludeNonBalanceable));
        }
        const res = await authFetch(`${API_BASE}/performance/gains?${params.toString()}`);
        if (!res.ok) throw new Error(i18n.t('errors:performance.gainsAnalysis'));
        return res.json();
    },

    getPerformanceByClass: async (
        period: PerformancePeriod = 'all_time',
        excludeNonBalanceable = false,
        includeNonRebalanceable?: boolean,
    ): Promise<PerformanceByClassResponse> => {
        const params = new URLSearchParams({ period });
        if (includeNonRebalanceable !== undefined) {
            params.set('include_non_rebalanceable', String(includeNonRebalanceable));
        } else {
            params.set('exclude_non_balanceable', String(excludeNonBalanceable));
        }
        const res = await authFetch(`${API_BASE}/performance/by-class?${params.toString()}`);
        if (!res.ok) throw new Error(i18n.t('errors:performance.byClass'));
        return res.json();
    },

    getReturns: async (
        period: PerformancePeriod = 'all_time',
        excludeNonBalanceable = false,
        includeNonRebalanceable?: boolean,
    ): Promise<PerformanceReturns> => {
        const params = new URLSearchParams({ period });
        if (includeNonRebalanceable !== undefined) {
            params.set('include_non_rebalanceable', String(includeNonRebalanceable));
        } else {
            params.set('exclude_non_balanceable', String(excludeNonBalanceable));
        }
        const res = await authFetch(`${API_BASE}/performance/returns?${params.toString()}`);
        return res.json();
    },

    getAttribution: async (period?: string, includeNonRebalanceable?: boolean): Promise<AttributionResult | null> => {
        const params = new URLSearchParams();
        if (period) params.set('period', period);
        if (includeNonRebalanceable !== undefined) {
            params.set('include_non_rebalanceable', String(includeNonRebalanceable));
        }
        const res = await authFetch(`${API_BASE}/performance/attribution?${params.toString()}`);
        if (!res.ok) return null;
        return res.json();
    },

    getPerformanceRiskMetrics: async (period?: string, includeNonRebalanceable?: boolean): Promise<PerformanceRiskMetrics> => {
        const params = new URLSearchParams();
        if (period) params.set('period', period);
        if (includeNonRebalanceable !== undefined) {
            params.set('include_non_rebalanceable', String(includeNonRebalanceable));
        }
        const res = await authFetch(`${API_BASE}/performance/risk-metrics?${params.toString()}`);
        return res.json();
    },

    /**
     * GET /performance/movers — price-driven movement over a selectable window.
     * @param window  "7d"|"30d"|"3m"|"6m"|"12m"
     * @param level   "asset"|"sub_class"|"top_class" (default "asset")
     * @param limit   max rows (1-50, default 10)
     */
    getMovers: async (
        window: string = '30d',
        level: string = 'asset',
        limit: number = 10,
    ): Promise<MoversResponse> => {
        const params = new URLSearchParams({ window, level, limit: String(limit) });
        const res = await authFetch(`${API_BASE}/performance/movers?${params.toString()}`);
        if (!res.ok) throw new Error(i18n.t('errors:performance.movers', { status: res.status }));
        return res.json();
    },
};
