import i18n from '../../i18n';
import { authFetch } from '../authFetch';
import { API_BASE } from './base';
import type {
    Transaction, TransactionFilters,
    PortfolioAuditSummary, AssetClassInvestigation, AssetCaseFile,
    SyncHistoryRun, SyncHistoryDetail, PipelineStatusResponse,
} from './types';

export const ManagementAPI = {
    searchTransactions: async (params: any): Promise<{ transactions: Transaction[], total: number }> => {
        const query = new URLSearchParams(params).toString();
        const res = await authFetch(`${API_BASE}/management/transactions?${query}`);
        if (!res.ok) throw new Error(i18n.t('errors:management.searchTransactions'));
        return res.json();
    },

    getTransactionFilters: async (): Promise<TransactionFilters> => {
        const res = await authFetch(`${API_BASE}/management/transactions/filters`);
        if (!res.ok) throw new Error(i18n.t('errors:management.transactionFilters'));
        return res.json();
    },

    previewImports: async (): Promise<{ readers: any[], error?: string }> => {
        const res = await authFetch(`${API_BASE}/management/import/preview`);
        if (!res.ok) return { readers: [], error: i18n.t('errors:management.previewImports') };
        return res.json();
    }
};

export const OperationsAPI = {
    getPortfolioAudit: async (): Promise<PortfolioAuditSummary> => {
        const res = await authFetch(`${API_BASE}/operations/portfolio-audit`);
        if (!res.ok) throw new Error(i18n.t('errors:management.portfolioAudit'));
        return res.json();
    },

    getAssetClassAudit: async (className: string): Promise<AssetClassInvestigation> => {
        const res = await authFetch(`${API_BASE}/operations/asset-class-audit?class=${encodeURIComponent(className)}`);
        if (!res.ok) throw new Error(i18n.t('errors:management.assetClassAudit'));
        return res.json();
    },

    getAssetCaseFile: async (assetId: string): Promise<AssetCaseFile> => {
        const res = await authFetch(`${API_BASE}/operations/asset-case-file?asset_id=${encodeURIComponent(assetId)}`);
        if (!res.ok) throw new Error(i18n.t('errors:management.assetCaseFile'));
        return res.json();
    },

    getSyncHistory: async (limit = 20, filter: 'meaningful' | 'all' | 'no_change' = 'meaningful'): Promise<{ runs: SyncHistoryRun[] }> => {
        const res = await authFetch(`${API_BASE}/operations/sync-history?limit=${limit}&filter=${filter}`);
        if (!res.ok) throw new Error(i18n.t('errors:management.syncHistory'));
        return res.json();
    },

    getSyncHistoryDetail: async (runId: string): Promise<SyncHistoryDetail> => {
        const res = await authFetch(`${API_BASE}/operations/sync-history/${encodeURIComponent(runId)}`);
        if (!res.ok) throw new Error(i18n.t('errors:management.syncHistoryDetail'));
        return res.json();
    },

    getPipelineStatus: async (): Promise<PipelineStatusResponse> => {
        const res = await authFetch(`${API_BASE}/operations/pipeline`);
        if (!res.ok) throw new Error(i18n.t('errors:management.pipelineStatus'));
        return res.json();
    },
};
