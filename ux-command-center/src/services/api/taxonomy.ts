import i18n from '../../i18n';
import { authFetch } from '../authFetch';
import { API_BASE } from './base';
import type {
    TaxonomyClass, TaxonomyRule, AssetAuditResponse,
    RiskProfile, RiskAllocation,
} from './types';

export const TaxonomyAPI = {
    getClasses: async (): Promise<TaxonomyClass[]> => {
        const res = await authFetch(`${API_BASE}/taxonomy/classes`);
        if (!res.ok) throw new Error(i18n.t('errors:taxonomy.classes'));
        const data = await res.json();
        return data.classes;
    },

    createClass: async (data: any): Promise<{ id: number }> => {
        const res = await authFetch(`${API_BASE}/taxonomy/classes`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(data)
        });
        if (!res.ok) throw new Error(i18n.t('errors:taxonomy.createClass'));
        return res.json();
    },

    updateClass: async (id: number, data: any): Promise<void> => {
        const res = await authFetch(`${API_BASE}/taxonomy/classes/${id}`, {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(data)
        });
        if (!res.ok) throw new Error(i18n.t('errors:taxonomy.updateClass'));
    },

    deleteClass: async (id: number): Promise<void> => {
        const res = await authFetch(`${API_BASE}/taxonomy/classes/${id}`, { method: 'DELETE' });
        if (!res.ok) throw new Error(i18n.t('errors:taxonomy.deleteClass'));
    },

    getRules: async (): Promise<TaxonomyRule[]> => {
        const res = await authFetch(`${API_BASE}/taxonomy/rules`);
        if (!res.ok) throw new Error(i18n.t('errors:taxonomy.rules'));
        const data = await res.json();
        return data.rules;
    },

    createRule: async (data: any): Promise<void> => {
        const res = await authFetch(`${API_BASE}/taxonomy/rules`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(data)
        });
        if (!res.ok) {
            const err = await res.json().catch(() => ({}));
            throw new Error(err.detail || i18n.t('errors:taxonomy.createRule'));
        }
    },

    upsertRule: async (data: any): Promise<{ message: string; action: string }> => {
        const res = await authFetch(`${API_BASE}/taxonomy/rules`, {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(data)
        });
        if (!res.ok) throw new Error(i18n.t('errors:taxonomy.upsertRule'));
        return res.json();
    },

    deleteRule: async (id: number): Promise<void> => {
        const res = await authFetch(`${API_BASE}/taxonomy/rules/${id}`, { method: 'DELETE' });
        if (!res.ok) throw new Error(i18n.t('errors:taxonomy.deleteRule'));
    },

    runAutoTag: async (): Promise<{ classified: number, unclassified: number }> => {
        const res = await authFetch(`${API_BASE}/taxonomy/auto-tag`, { method: 'POST' });
        if (!res.ok) throw new Error(i18n.t('errors:taxonomy.autoTag'));
        return res.json();
    },

    getTiers: async (): Promise<any[]> => {
        const res = await authFetch(`${API_BASE}/taxonomy/tiers`);
        if (!res.ok) throw new Error(i18n.t('errors:taxonomy.tiers'));
        const data = await res.json();
        return data.tiers;
    },

    getAssetAudit: async (): Promise<AssetAuditResponse> => {
        const res = await authFetch(`${API_BASE}/taxonomy/audit`);
        if (!res.ok) throw new Error(i18n.t('errors:taxonomy.assetAudit'));
        return res.json();
    },

    deactivateAsset: async (assetId: string): Promise<{ asset_id: string; status: string }> => {
        const res = await authFetch(`${API_BASE}/taxonomy/assets/${encodeURIComponent(assetId)}`, {
            method: 'DELETE',
        });
        if (!res.ok) throw new Error(i18n.t('errors:taxonomy.deactivateAsset'));
        return res.json();
    },

    setAssetTier: async (assetId: string, tierId: string | null): Promise<{ asset_id: string; tier: string | null }> => {
        const res = await authFetch(`${API_BASE}/taxonomy/assets/${encodeURIComponent(assetId)}/tier`, {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ tier_id: tierId }),
        });
        if (!res.ok) throw new Error(i18n.t('errors:taxonomy.setAssetTier'));
        return res.json();
    },

    // Note: duplicate key preserved from original (line 1325 of api.ts)
    // deactivateAsset: async (assetId: string): Promise<void> => { ... }
    // The second definition overwrites at runtime — preserving first definition only.
};

export const RiskProfileAPI = {
    getProfiles: async (): Promise<RiskProfile[]> => {
        const res = await authFetch(`${API_BASE}/risk-profiles`);
        if (!res.ok) throw new Error(i18n.t('errors:taxonomy.riskProfiles'));
        const data = await res.json();
        return data.profiles;
    },

    createProfile: async (data: any): Promise<{ id: number }> => {
        const res = await authFetch(`${API_BASE}/risk-profiles`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(data)
        });
        if (!res.ok) throw new Error(i18n.t('errors:taxonomy.createProfile'));
        return res.json();
    },

    getAllocations: async (profileId: number): Promise<RiskAllocation[]> => {
        const res = await authFetch(`${API_BASE}/risk-profiles/${profileId}/allocations`);
        if (!res.ok) throw new Error(i18n.t('errors:taxonomy.allocations'));
        const data = await res.json();
        return data.allocations;
    },

    updateAllocations: async (profileId: number, allocations: Record<number, number>): Promise<void> => {
        const res = await authFetch(`${API_BASE}/risk-profiles/${profileId}/allocations`, {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ allocations })
        });
        if (!res.ok) throw new Error(i18n.t('errors:taxonomy.updateAllocations'));
    },

    activateProfile: async (id: number): Promise<void> => {
        const res = await authFetch(`${API_BASE}/risk-profiles/${id}/activate`, { method: 'POST' });
        if (!res.ok) throw new Error(i18n.t('errors:taxonomy.activateProfile'));
    }
};
