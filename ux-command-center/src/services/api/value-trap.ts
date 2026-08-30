import i18n from '../../i18n';
import { authFetch } from '../authFetch';
import { API_BASE } from './base';
import type {
    ValueTrapReview, ValueTrapScanSummary, ValueTrapPendingCount, ValueTrapRulingSubmission,
    ValueTrapStatus, ValueTrapContext, ValueTrapDraft,
} from './types';

// ── Fix 2: confirm-no-memo response shape ───────────────────────────────────
export interface ConfirmNoMemoResult {
    asset_id: string;
    confirmed_no_memo: boolean;
}

export const valueTrapApi = {
    scanValueTraps: async (): Promise<ValueTrapScanSummary> => {
        const res = await authFetch(`${API_BASE}/reviews/value-trap/scan`, { method: 'POST' });
        if (!res.ok) throw new Error(i18n.t('errors:valueTrap.scan'));
        return res.json();
    },

    getValueTrapReviews: async (status: ValueTrapStatus | 'all' = 'open'): Promise<ValueTrapReview[]> => {
        const res = await authFetch(`${API_BASE}/reviews/value-trap?status=${status}`);
        if (!res.ok) throw new Error(i18n.t('errors:valueTrap.reviews'));
        return res.json();
    },

    getValueTrapPendingCount: async (): Promise<ValueTrapPendingCount> => {
        const res = await authFetch(`${API_BASE}/reviews/value-trap/pending-count`);
        if (!res.ok) throw new Error(i18n.t('errors:valueTrap.pendingCount'));
        return res.json();
    },

    submitValueTrapRuling: async (id: number, body: ValueTrapRulingSubmission): Promise<ValueTrapReview> => {
        const res = await authFetch(`${API_BASE}/reviews/value-trap/${id}`, {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(body),
        });
        if (!res.ok) {
            const errorData = await res.json().catch(() => null);
            throw new Error(errorData?.detail || errorData?.error?.message || i18n.t('errors:valueTrap.submitRuling'));
        }
        return res.json();
    },

    /** Fetch Huinsight context for a review: position, loss, memo, decision history. */
    getValueTrapContext: async (id: number): Promise<ValueTrapContext> => {
        const res = await authFetch(`${API_BASE}/reviews/value-trap/${id}/context`);
        if (!res.ok) {
            const errorData = await res.json().catch(() => null);
            throw new Error(errorData?.detail || errorData?.error?.message || i18n.t('errors:valueTrap.context'));
        }
        return res.json();
    },

    /** LLM pre-draft of the three F2.3 answers. Returns draft text, not persisted.
     *  Throws with the backend detail message on HTTP 503 (no key configured). */
    draftValueTrap: async (id: number): Promise<ValueTrapDraft> => {
        const res = await authFetch(`${API_BASE}/reviews/value-trap/${id}/draft`, { method: 'POST' });
        if (!res.ok) {
            const errorData = await res.json().catch(() => null);
            throw new Error(errorData?.detail || errorData?.error?.message || i18n.t('errors:valueTrap.draft', { status: res.status }));
        }
        return res.json();
    },

    /**
     * Fix 2: Owner confirms no memo exists for an asset.
     * After this call, the context panel may display "no memo on record".
     */
    confirmNoMemo: async (assetId: string): Promise<ConfirmNoMemoResult> => {
        const res = await authFetch(
            `${API_BASE}/reviews/value-trap/assets/${encodeURIComponent(assetId)}/confirm-no-memo`,
            { method: 'PUT' },
        );
        if (!res.ok) {
            const errorData = await res.json().catch(() => null);
            throw new Error(errorData?.detail || errorData?.error?.message || i18n.t('errors:valueTrap.confirmNoMemo'));
        }
        return res.json();
    },
};
