import i18n from '../../i18n';
import { authFetch } from '../authFetch';
import { API_BASE } from './base';
import type {
    SentimentResponse,
    SyncAuditSummary, SyncAuditDetail, IntegrityStatus, OnDemandAuditResult,
} from './types';

export interface FxRateResponse {
    /** Always "USD/CNY". */
    pair: string;
    /** Latest USD→CNY rate. Divide CNY values by this for USD display. */
    rate: number;
    /** ISO 8601 timestamp or null if unavailable. */
    as_of: string | null;
}

export const FxRateAPI = {
    /** Fetch latest USD/CNY rate for display-only conversion. */
    get: async (): Promise<FxRateResponse> => {
        const res = await authFetch(`${API_BASE}/market/fx-rate`);
        if (!res.ok) throw new Error(i18n.t('errors:market.fxRate'));
        return res.json();
    },
};

export const SentimentAPI = {
    getCached: async (): Promise<SentimentResponse> => {
        const res = await authFetch(`${API_BASE}/market/sentiment`);
        if (!res.ok) throw new Error(i18n.t('errors:market.fetchSentiment'));
        return res.json();
    },
    refresh: async (): Promise<SentimentResponse> => {
        const res = await authFetch(`${API_BASE}/market/sentiment/refresh`, { method: 'POST' });
        if (!res.ok) throw new Error(i18n.t('errors:market.refreshSentiment'));
        return res.json();
    },
    /** Diagnostics: which external keys are configured (server-side names only, no values). */
    diagnostics: async (): Promise<{ keys_configured: string[]; keys_missing: string[] }> => {
        const res = await authFetch(`${API_BASE}/market/sentiment/diagnostics`);
        if (!res.ok) return { keys_configured: [], keys_missing: [] };
        return res.json();
    }
};

export const ExportAPI = {
    downloadAiContext: async () => {
        const response = await authFetch(`${API_BASE}/export/ai-context`);
        if (!response.ok) throw new Error(i18n.t('errors:market.exportAiContext'));
        const blob = await response.blob();
        const url = URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        a.download = `Personal_Investment_Analysis_Context_${new Date().toISOString().slice(0, 10).replace(/-/g, '')}.md`;
        document.body.appendChild(a);
        a.click();
        document.body.removeChild(a);
        URL.revokeObjectURL(url);
    }
};

export const AuditAPI = {
    getReports: async (limit = 20): Promise<{ reports: SyncAuditSummary[]; total: number }> => {
        const res = await authFetch(`${API_BASE}/audit/v2/reports?limit=${limit}`);
        if (!res.ok) throw new Error(i18n.t('errors:market.auditReports'));
        return res.json();
    },
    getReportDetail: async (id: string): Promise<SyncAuditDetail> => {
        const res = await authFetch(`${API_BASE}/audit/v2/reports/${id}`);
        if (!res.ok) throw new Error(i18n.t('errors:market.auditReportDetail'));
        return res.json();
    },
    getIntegrity: async (): Promise<IntegrityStatus> => {
        const res = await authFetch(`${API_BASE}/audit/v2/integrity`);
        if (!res.ok) throw new Error(i18n.t('errors:market.integrityChecks'));
        return res.json();
    },
    runOnDemandAudit: async (): Promise<OnDemandAuditResult> => {
        const res = await authFetch(`${API_BASE}/audit/v2/on-demand`, { method: 'POST' });
        if (!res.ok) throw new Error(i18n.t('errors:market.onDemandAudit'));
        return res.json();
    },
    getLatest: async (): Promise<SyncAuditSummary | null> => {
        const res = await authFetch(`${API_BASE}/audit/v2/latest`);
        if (!res.ok) return null;
        return res.json();
    },
};
