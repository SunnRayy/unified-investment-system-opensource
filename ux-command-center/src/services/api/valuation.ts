import i18n from '../../i18n';
import { authFetch } from '../authFetch';
import { API_BASE } from './base';

export const valuationApi = {
    // ── Valuation API ─────────────────────────────────────────────────────────

    getValuationSnapshots: async (): Promise<any[]> => {
        const res = await authFetch(`${API_BASE}/valuation/snapshot/latest`);
        if (!res.ok) return [];
        return res.json();
    },

    getValuationHistory: async (ticker: string, days = 365): Promise<any[]> => {
        const res = await authFetch(`${API_BASE}/valuation/snapshot/history?ticker=${encodeURIComponent(ticker)}&days=${days}`);
        if (!res.ok) throw new Error(i18n.t('errors:valuation.history', { ticker }));
        return res.json();
    },

    triggerValuationRefresh: async (): Promise<{ status: string; refreshed_count: number; failed: any[] }> => {
        const res = await authFetch(`${API_BASE}/valuation/refresh`, { method: 'POST' });
        if (!res.ok) {
            const errorData = await res.json().catch(() => null);
            throw new Error(errorData?.detail || i18n.t('errors:valuation.refresh', { status: res.status }));
        }
        return res.json();
    },

    getValuationReference: async (): Promise<any[]> => {
        const res = await authFetch(`${API_BASE}/valuation/reference`);
        if (!res.ok) return [];
        return res.json();
    },

    updateValuationReference: async (
        ticker: string,
        metric: string,
        body: { low_threshold: number; high_threshold: number; historical_mean?: number | null; rate_sensitive?: boolean; notes?: string | null }
    ): Promise<{ ticker: string; metric: string; status: string }> => {
        const res = await authFetch(`${API_BASE}/valuation/reference/${encodeURIComponent(ticker)}/${encodeURIComponent(metric)}`, {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(body),
        });
        if (!res.ok) {
            const errorData = await res.json().catch(() => null);
            throw new Error(errorData?.detail || i18n.t('errors:valuation.updateReference', { status: res.status }));
        }
        return res.json();
    },

    getValuationMacro: async (): Promise<{ us10y: number; rate_adjustment_factor: number; source: string; fallback_used: boolean; usd_cny?: number | null }> => {
        const res = await authFetch(`${API_BASE}/valuation/macro`);
        if (!res.ok) return { us10y: 4.26, rate_adjustment_factor: 1.0, source: 'fallback', fallback_used: true };
        return res.json();
    },

    getValuationWatchlist: async (): Promise<Array<{
        ticker: string;
        display_name: string;
        asset_type: string;
        note: string | null;
        added_at: string;
    }>> => {
        const res = await authFetch(`${API_BASE}/valuation/watchlist`);
        if (!res.ok) throw new Error(i18n.t('errors:valuation.getWatchlist', { status: res.status }));
        return res.json();
    },

    addValuationWatchlist: async (item: {
        ticker: string;
        display_name: string;
        asset_type: string;
        note?: string;
    }): Promise<{ ticker: string; status: string; backfill_status: string }> => {
        const res = await authFetch(`${API_BASE}/valuation/watchlist`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(item),
        });
        if (!res.ok) {
            const err = await res.json().catch(() => null);
            throw new Error(err?.detail || i18n.t('errors:valuation.addWatchlist', { status: res.status }));
        }
        return res.json();
    },

    deleteValuationWatchlist: async (ticker: string): Promise<{ ticker: string; status: string }> => {
        const res = await authFetch(`${API_BASE}/valuation/watchlist/${encodeURIComponent(ticker)}`, { method: 'DELETE' });
        if (!res.ok) throw new Error(i18n.t('errors:valuation.deleteWatchlist', { status: res.status }));
        return res.json();
    },
};
