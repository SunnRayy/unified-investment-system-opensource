import i18n from '../../i18n';
import { authFetch } from '../authFetch';
import { API_BASE } from './base';
import type {
    AssetSearchResult, CreateTradeRequest, TradeLogEntry,
    ActionItem, VerificationTrends, VerificationPeriod,
    KPI, AuditLog, AuditSummary,
} from './types';

export const coreApi = {
    searchAssets: async (q: string): Promise<{ assets: AssetSearchResult[] }> => {
        if (q.length < 2) return { assets: [] };
        const res = await authFetch(`${API_BASE}/ai-advisor/assets/search?q=${encodeURIComponent(q)}`);
        if (!res.ok) throw new Error(i18n.t('errors:core.searchAssets'));
        return res.json();
    },

    createTrade: async (trade: CreateTradeRequest): Promise<TradeLogEntry> => {
        const res = await authFetch(`${API_BASE}/ai-advisor/trades`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(trade),
        });
        if (!res.ok) {
            const errorData = await res.json().catch(() => null);
            throw new Error(errorData?.detail?.message || errorData?.detail || i18n.t('errors:core.createTrade'));
        }
        return res.json();
    },

    listTrades: async (limit = 50): Promise<{ trades: TradeLogEntry[] }> => {
        const res = await authFetch(`${API_BASE}/ai-advisor/trades?limit=${limit}`);
        if (!res.ok) throw new Error(i18n.t('errors:core.listTrades'));
        return res.json();
    },

    deleteTrade: async (id: number): Promise<void> => {
        const res = await authFetch(`${API_BASE}/ai-advisor/trades/${id}`, { method: 'DELETE' });
        if (!res.ok) {
            const errorData = await res.json().catch(() => null);
            throw new Error(errorData?.detail || i18n.t('errors:core.deleteTrade', { id }));
        }
    },

    getDashboardActions: async (): Promise<{ actions: ActionItem[] }> => {
        const res = await authFetch(`${API_BASE}/dashboard/actions`);
        if (!res.ok) return { actions: [] };
        return res.json();
    },

    getVerificationTrends: async (): Promise<VerificationTrends> => {
        const res = await authFetch(`${API_BASE}/verification/trends`);
        if (!res.ok) throw new Error(i18n.t('errors:core.verificationTrends'));
        return res.json();
    },

    getLatestVerification: async (): Promise<VerificationPeriod> => {
        const res = await authFetch(`${API_BASE}/verification/latest`);
        if (!res.ok) throw new Error(i18n.t('errors:core.latestVerification'));
        return res.json();
    },

    triggerVerification: async (): Promise<VerificationPeriod> => {
        const res = await authFetch(`${API_BASE}/verification/run`, { method: 'POST' });
        if (!res.ok) throw new Error(i18n.t('errors:core.runVerification'));
        return res.json();
    },

    getKPI: async (includeNonRebalanceable = false): Promise<KPI> => {
        const res = await authFetch(`${API_BASE}/dashboard/kpi?include_non_rebalanceable=${includeNonRebalanceable}`);
        if (!res.ok) throw new Error(i18n.t('errors:core.kpi'));
        return res.json();
    },

    getAuditLogs: async (limit = 50): Promise<AuditLog[]> => {
        const res = await authFetch(`${API_BASE}/audit/logs?limit=${limit}`);
        if (!res.ok) throw new Error(i18n.t('errors:core.auditLogs'));
        return res.json();
    },

    getAuditSummary: async (): Promise<AuditSummary> => {
        const res = await authFetch(`${API_BASE}/audit/summary`);
        if (!res.ok) return {
            total_logs: 0,
            last_sync_timestamp: null,
            unresolved_conflicts: 0
        };
        return res.json();
    },

    getInsights: async (): Promise<any[]> => {
        const res = await authFetch(`${API_BASE}/insights`);
        if (!res.ok) throw new Error(i18n.t('errors:core.insights', { status: res.status }));
        return res.json();
    },

    startSync: async (): Promise<{ status: string }> => {
        const res = await authFetch(`${API_BASE}/sync/start`, { method: 'POST' });
        if (!res.ok) throw new Error(i18n.t('errors:core.startSync'));
        return res.json();
    },
};
