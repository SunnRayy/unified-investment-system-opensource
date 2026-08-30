import i18n from '../../i18n';
import { authFetch } from '../authFetch';
import { API_BASE } from './base';
import type {
    NorthStarPanel, UnclassifiedFlow, FlowTagRequest, FlowTagResult, FlowClassifySummary,
    UnforcedError, UnforcedErrorCreate,
    ClassifiedFlow, BulkTagRequest, BulkTagResult, UntagRequest, UntagResult, ContributionsSummary,
    CashFlowClassification, ContributionsWindow,
} from './types';

export const northStarApi = {
    getNorthStarPanel: async (monthlyContribution: number = 0): Promise<NorthStarPanel> => {
        const res = await authFetch(`${API_BASE}/north-star/panel?monthly_contribution=${monthlyContribution}`);
        if (!res.ok) throw new Error(i18n.t('errors:northStar.panel'));
        return res.json();
    },

    classifyFlows: async (dryRun = false): Promise<FlowClassifySummary> => {
        const url = `${API_BASE}/north-star/flows/classify${dryRun ? '?dry_run=true' : ''}`;
        const res = await authFetch(url, { method: 'POST' });
        if (!res.ok) throw new Error(i18n.t('errors:northStar.classifyFlows'));
        return res.json();
    },

    revertFlowClassify: async (ids: number[]): Promise<{ deleted: number }> => {
        const res = await authFetch(`${API_BASE}/north-star/flows/classify/revert`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ ids }),
        });
        if (!res.ok) throw new Error(i18n.t('errors:northStar.revertClassify'));
        return res.json();
    },

    getUnclassifiedFlows: async (): Promise<UnclassifiedFlow[]> => {
        const res = await authFetch(`${API_BASE}/north-star/flows/unclassified`);
        if (!res.ok) throw new Error(i18n.t('errors:northStar.unclassifiedFlows'));
        return res.json();
    },

    tagFlow: async (body: FlowTagRequest): Promise<FlowTagResult> => {
        const res = await authFetch(`${API_BASE}/north-star/flows/tag`, {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(body),
        });
        if (!res.ok) {
            const errorData = await res.json().catch(() => null);
            throw new Error(errorData?.detail || errorData?.error?.message || i18n.t('errors:northStar.tagFlow'));
        }
        return res.json();
    },

    getUnforcedErrors: async (): Promise<UnforcedError[]> => {
        const res = await authFetch(`${API_BASE}/north-star/unforced-errors`);
        if (!res.ok) throw new Error(i18n.t('errors:northStar.unforcedErrors'));
        return res.json();
    },

    createUnforcedError: async (body: UnforcedErrorCreate): Promise<UnforcedError> => {
        const res = await authFetch(`${API_BASE}/north-star/unforced-errors`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(body),
        });
        if (!res.ok) {
            const errorData = await res.json().catch(() => null);
            throw new Error(errorData?.detail || errorData?.error?.message || i18n.t('errors:northStar.createUnforcedError'));
        }
        return res.json();
    },

    updateUnforcedErrorCost: async (id: number, est_cost_cny: number | null): Promise<UnforcedError> => {
        const res = await authFetch(`${API_BASE}/north-star/unforced-errors/${id}`, {
            method: 'PATCH',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ est_cost_cny }),
        });
        if (!res.ok) {
            const errorData = await res.json().catch(() => null);
            throw new Error(errorData?.detail || errorData?.error?.message || i18n.t('errors:northStar.updateErrorCost'));
        }
        return res.json();
    },

    /** GET /north-star/flows/classified?classification= — list already-tagged flows */
    getClassifiedFlows: async (classification?: CashFlowClassification): Promise<ClassifiedFlow[]> => {
        const url = classification
            ? `${API_BASE}/north-star/flows/classified?classification=${classification}`
            : `${API_BASE}/north-star/flows/classified`;
        const res = await authFetch(url);
        if (!res.ok) throw new Error(i18n.t('errors:northStar.classifiedFlows'));
        return res.json();
    },

    /** PUT /north-star/flows/tag/bulk — upsert-tag multiple flows */
    tagFlowsBulk: async (body: BulkTagRequest): Promise<BulkTagResult> => {
        const res = await authFetch(`${API_BASE}/north-star/flows/tag/bulk`, {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(body),
        });
        if (!res.ok) {
            const errorData = await res.json().catch(() => null);
            throw new Error(errorData?.detail || errorData?.error?.message || i18n.t('errors:northStar.bulkTagFlows'));
        }
        return res.json();
    },

    /** DELETE /north-star/flows/tag — remove tags (untag) */
    untagFlows: async (body: UntagRequest): Promise<UntagResult> => {
        const res = await authFetch(`${API_BASE}/north-star/flows/tag`, {
            method: 'DELETE',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(body),
        });
        if (!res.ok) {
            const errorData = await res.json().catch(() => null);
            throw new Error(errorData?.detail || errorData?.error?.message || i18n.t('errors:northStar.untagFlows'));
        }
        return res.json();
    },

    /** GET /north-star/contributions — YTD + trailing-12M contribution metrics.
     * `window` controls investment.* and rsu.* only ('12'/'36'/'all', default '12') —
     * ytd_sum/trailing_12m_sum/by_classification are always fixed trailing-12M/YTD. */
    getContributions: async (window: ContributionsWindow = '12'): Promise<ContributionsSummary> => {
        const res = await authFetch(`${API_BASE}/north-star/contributions?window_months=${window}`);
        if (!res.ok) throw new Error(i18n.t('errors:northStar.contributions'));
        return res.json();
    },
};
