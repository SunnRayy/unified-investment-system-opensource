import i18n from '../../i18n';
import { authFetch } from '../authFetch';
import { API_BASE } from './base';
import type {
    DecisionTimeline, DecisionStats, DecisionScorecard, DecisionIntelligence,
    DecisionFunnel, DecisionLeaderboard, DecisionAlert,
    StrategyReport, StrategyMemo,
} from './types';

export const decisionsApi = {
    getDecisionsTimeline: async (limit = 50, type = 'all'): Promise<DecisionTimeline> => {
        const res = await authFetch(`${API_BASE}/decisions/timeline?limit=${limit}&type=${type}`);
        if (!res.ok) throw new Error(i18n.t('errors:decisions.timeline'));
        return res.json();
    },

    getDecisionsStats: async (): Promise<DecisionStats> => {
        const res = await authFetch(`${API_BASE}/decisions/stats`);
        if (!res.ok) throw new Error(i18n.t('errors:decisions.stats'));
        return res.json();
    },

    getDecisionsScorecard: async (limit = 50): Promise<DecisionScorecard> => {
        const res = await authFetch(`${API_BASE}/decisions/scorecard?limit=${limit}`);
        if (!res.ok) throw new Error(i18n.t('errors:decisions.scorecard'));
        return res.json();
    },

    getDecisionsIntelligence: async (): Promise<DecisionIntelligence> => {
        const res = await authFetch(`${API_BASE}/decisions/intelligence`);
        if (!res.ok) throw new Error(i18n.t('errors:decisions.intelligence'));
        return res.json();
    },

    getDecisionsFunnel: async (): Promise<DecisionFunnel> => {
        const res = await authFetch(`${API_BASE}/decisions/funnel`);
        if (!res.ok) throw new Error(i18n.t('errors:decisions.funnel'));
        return res.json();
    },

    getDecisionsLeaderboard: async (): Promise<DecisionLeaderboard> => {
        const res = await authFetch(`${API_BASE}/decisions/leaderboard`);
        if (!res.ok) throw new Error(i18n.t('errors:decisions.leaderboard'));
        return res.json();
    },

    getDecisionAlerts: async (): Promise<{alerts: DecisionAlert[]; counts: {high: number; medium: number; low: number}}> => {
        const res = await authFetch(`${API_BASE}/decisions/alerts`);
        if (!res.ok) throw new Error(i18n.t('errors:decisions.alerts'));
        return res.json();
    },

    getStrategyAlignment: async (): Promise<{report: StrategyReport | null; message?: string}> => {
        const res = await authFetch(`${API_BASE}/strategy/alignment`);
        if (!res.ok) throw new Error(i18n.t('errors:decisions.strategyAlignment'));
        return res.json();
    },

    triggerStrategyReview: async (): Promise<{status: string; report: StrategyReport}> => {
        const res = await authFetch(`${API_BASE}/strategy/review`, { method: 'POST' });
        if (!res.ok) throw new Error(i18n.t('errors:decisions.triggerReview'));
        return res.json();
    },

    getStrategyMemos: async (includeContent = false): Promise<{memos: StrategyMemo[]}> => {
        const res = await authFetch(`${API_BASE}/strategy/memos${includeContent ? '?include_content=true' : ''}`);
        if (!res.ok) throw new Error(i18n.t('errors:decisions.memos'));
        return res.json();
    },

    getStrategyMemo: async (id: number): Promise<StrategyMemo> => {
        const res = await authFetch(`${API_BASE}/strategy/memos/${id}`);
        if (!res.ok) throw new Error(i18n.t('errors:decisions.memoById', { id }));
        return res.json();
    },

    createStrategyMemo: async (content: string, memoDate?: string): Promise<StrategyMemo> => {
        const payload: Record<string, string> = { content };
        if (memoDate) payload.memo_date = memoDate;

        const res = await authFetch(`${API_BASE}/strategy/memos`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload),
        });
        if (!res.ok) {
            const errorData = await res.json().catch(() => null);
            throw new Error(errorData?.detail?.message || errorData?.detail || i18n.t('errors:decisions.createMemo'));
        }
        return res.json();
    },

    updateStrategyMemo: async (id: number, updates: { content?: string; title?: string; memo_date?: string }): Promise<StrategyMemo> => {
        const res = await authFetch(`${API_BASE}/strategy/memos/${id}`, {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(updates),
        });
        if (!res.ok) {
            const errorData = await res.json().catch(() => null);
            throw new Error(errorData?.detail || i18n.t('errors:decisions.updateMemo'));
        }
        return res.json();
    },

    deleteStrategyMemo: async (id: number): Promise<void> => {
        const res = await authFetch(`${API_BASE}/strategy/memos/${id}`, { method: 'DELETE' });
        if (!res.ok) throw new Error(i18n.t('errors:decisions.deleteMemo', { id }));
    },

    importMemosFromFiles: async (): Promise<{ status: string; created: number; updated: number; skipped: number }> => {
        const res = await authFetch(`${API_BASE}/strategy/memos/import-from-files`, { method: 'POST' });
        if (!res.ok) {
            const errorData = await res.json().catch(() => null);
            throw new Error(errorData?.detail || i18n.t('errors:decisions.importMemos'));
        }
        return res.json();
    },
};
