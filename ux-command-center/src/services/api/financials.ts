import { authFetch } from '../authFetch';
import { API_BASE } from './base';
import type {
    BalanceSheetSummary, BalanceSheetHistory,
    IncomeExpenseSummary, IncomeExpenseHistory,
    ProjectionDefaults, ProjectionResult,
    CashFlowAnalysis, CashFlowForecast,
    Goal, GoalCreate, GoalUpdate, MarketRegime,
} from './types';

export const BalanceSheetAPI = {
    getSummary: async (): Promise<BalanceSheetSummary> => {
        const res = await authFetch(`${API_BASE}/balance-sheet/summary`);
        return res.json();
    },
    getHistory: async (limit = 72): Promise<BalanceSheetHistory> => {
        const res = await authFetch(`${API_BASE}/balance-sheet/history?limit=${limit}`);
        return res.json();
    },
    getDates: async (): Promise<{ dates: string[] }> => {
        const res = await authFetch(`${API_BASE}/balance-sheet/dates`);
        return res.json();
    },
};

export const IncomeExpenseAPI = {
    getSummary: async (): Promise<IncomeExpenseSummary> => {
        const res = await authFetch(`${API_BASE}/income-expense/summary`);
        return res.json();
    },
    getHistory: async (limit = 24): Promise<IncomeExpenseHistory> => {
        const res = await authFetch(`${API_BASE}/income-expense/history?limit=${limit}`);
        return res.json();
    },
    getDates: async (): Promise<{ dates: string[] }> => {
        const res = await authFetch(`${API_BASE}/income-expense/dates`);
        return res.json();
    },
};

export const AnalyticsAPI = {
    getProjectionDefaults: async (): Promise<ProjectionDefaults> => {
        const res = await authFetch(`${API_BASE}/analytics/projection/defaults`);
        return res.json();
    },
    getProjection: async (params: Record<string, string> = {}, includeNonRebalanceable = false): Promise<ProjectionResult> => {
        const urlParams = new URLSearchParams(params);
        urlParams.set('include_non_rebalanceable', String(includeNonRebalanceable));
        const res = await authFetch(`${API_BASE}/analytics/projection?${urlParams.toString()}`);
        return res.json();
    },
    getCashFlowTrends: async (): Promise<CashFlowAnalysis> => {
        const res = await authFetch(`${API_BASE}/analytics/cashflow-trends`);
        return res.json();
    },
    getCashFlowForecast: async (months = 6): Promise<CashFlowForecast> => {
        const res = await authFetch(`${API_BASE}/analytics/cashflow-forecast?months=${months}`);
        return res.json();
    },
    listGoals: async (): Promise<Goal[]> => {
        const res = await authFetch(`${API_BASE}/analytics/goals`);
        return res.json();
    },
    createGoal: async (data: GoalCreate): Promise<Goal> => {
        const res = await authFetch(`${API_BASE}/analytics/goals`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(data),
        });
        return res.json();
    },
    updateGoal: async (id: number, data: GoalUpdate): Promise<Goal> => {
        const res = await authFetch(`${API_BASE}/analytics/goals/${id}`, {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(data),
        });
        return res.json();
    },
    deleteGoal: async (id: number): Promise<{ success: boolean }> => {
        const res = await authFetch(`${API_BASE}/analytics/goals/${id}`, { method: 'DELETE' });
        return res.json();
    },
    // probability is null (with status/reason set) when the live return,
    // volatility, or run-rate is unavailable — never a fabricated number.
    getGoalProbability: async (id: number, params: Record<string, string> = {}): Promise<{ goal_id: number; probability: number | null; status?: string; reason?: string }> => {
        const qs = new URLSearchParams(params).toString();
        const res = await authFetch(`${API_BASE}/analytics/goals/${id}/probability${qs ? '?' + qs : ''}`);
        return res.json();
    },
    getMarketRegime: async (): Promise<MarketRegime> => {
        const res = await authFetch(`${API_BASE}/market/regime`);
        return res.json();
    },
};
