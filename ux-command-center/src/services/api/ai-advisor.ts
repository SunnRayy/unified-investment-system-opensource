import i18n from '../../i18n';
import { authFetch } from '../authFetch';
import { API_BASE, safeReadError } from './base';
import type {
    LLMSettings, ContextConfig, ContextRenderResponse,
    BriefResponse, BriefHistoryItem, TokenEstimate,
    ReviewQuestion, ReviewAnswer, ReviewResponse, ReviewDetailResponse,
    ReviewHistoryItem, ReviewUpdatePayload,
    BehavioralMetric, BehavioralMetricsResponse, InsightItem,
    AnalysisResult, AnalysisHistoryItem, AnalyzableAssetSearchResult,
    PendingVerificationItem, VerifyTradeBody, CrossCheckAuditResult,
    MemoProposalResult, InsightTradeLink,
    ValidatedCaseResponse, RuleCitation, GovernanceReport,
} from './types';

// AI Advisor API functions
export const getLLMSettings = (): Promise<LLMSettings> =>
  authFetch('/api/ai-advisor/settings/llm').then(r => r.json());

export const updateLLMSettings = (settings: LLMSettings): Promise<LLMSettings> =>
  authFetch('/api/ai-advisor/settings/llm', {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(settings),
  }).then(r => r.json());

export const generateBrief = (
  contextConfig: ContextConfig,
  reviewedContextText?: string
): Promise<BriefResponse> =>
  authFetch('/api/ai-advisor/brief/generate', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      context_config: contextConfig,
      reviewed_context_text: reviewedContextText,
      // Interactive generation carries the locale the user is looking at.
      // A SCHEDULED run sends nothing and falls back to user_profile.language
      // (see src/services/ai_advisor/language_resolver.py).
      language: i18n.language,
    }),
  }).then(r => { if (!r.ok) throw new Error(i18n.t('errors:aiAdvisor.briefGeneration', { status: r.status })); return r.json(); });

export const getLatestBrief = (): Promise<BriefResponse | null> =>
  authFetch('/api/ai-advisor/brief/latest').then(r => r.json());

export const getBriefHistory = (limit = 20): Promise<BriefHistoryItem[]> =>
  authFetch(`/api/ai-advisor/brief/history?limit=${limit}`).then(r => r.json());

export const getBriefById = (id: number): Promise<BriefResponse> =>
  authFetch(`/api/ai-advisor/brief/${id}`).then(r => r.json());

export const getContextPreview = (params: Record<string, string>): Promise<Record<string, TokenEstimate | number>> => {
  const qs = new URLSearchParams(params).toString();
  return authFetch(`/api/ai-advisor/context/preview?${qs}`).then(r => r.json());
};

export const renderAdvisorContext = (
  reportType: 'brief' | 'review',
  contextConfig: ContextConfig,
  reviewPreview?: {
    period_start?: string;
    period_end?: string;
    questions_answers?: ReviewAnswer[];
  }
): Promise<ContextRenderResponse> =>
  authFetch('/api/ai-advisor/context/render', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      report_type: reportType,
      context_config: contextConfig,
      ...reviewPreview,
    }),
  }).then(r => { if (!r.ok) throw new Error(i18n.t('errors:aiAdvisor.contextPreview', { status: r.status })); return r.json(); });

// ── Review API functions ───────────────────────────────────────────────────

export const generateReviewQuestions = (
  periodStart: string,
  periodEnd: string
): Promise<{ questions: ReviewQuestion[] }> =>
  authFetch('/api/ai-advisor/review/questions', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ period_start: periodStart, period_end: periodEnd, language: i18n.language }),
  }).then(r => { if (!r.ok) throw new Error(i18n.t('errors:aiAdvisor.questions', { status: r.status })); return r.json(); });

export const generateReview = (
  questionsAnswers: ReviewAnswer[],
  periodStart: string,
  periodEnd: string,
  contextConfig: ContextConfig,
  reviewedContextText?: string
): Promise<ReviewResponse> =>
  authFetch('/api/ai-advisor/review/generate', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      questions_answers: questionsAnswers,
      period_start: periodStart,
      period_end: periodEnd,
      context_config: contextConfig,
      reviewed_context_text: reviewedContextText,
      language: i18n.language,
    }),
  }).then(r => { if (!r.ok) throw new Error(i18n.t('errors:aiAdvisor.review', { status: r.status })); return r.json(); });

export const getLatestReview = async (): Promise<ReviewDetailResponse | null> => {
  const response = await authFetch('/api/ai-advisor/review/latest');
  if (!response.ok) return null;
  return response.json();
};

export const getReviewById = async (id: number): Promise<ReviewDetailResponse> => {
  const response = await authFetch(`/api/ai-advisor/review/${id}`);
  if (!response.ok) {
    throw new Error(i18n.t('errors:aiAdvisor.reviewById', { id, status: response.status }));
  }
  return response.json();
};

export const getReviewHistory = (limit = 20): Promise<ReviewHistoryItem[]> =>
  authFetch(`/api/ai-advisor/review/history?limit=${limit}`).then(r => r.json());

export const updateReview = async (id: number, updates: ReviewUpdatePayload): Promise<ReviewDetailResponse> => {
  const response = await authFetch(`/api/ai-advisor/review/${id}`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(updates),
  });
  if (!response.ok) {
    const errorData = await response.json().catch(() => null);
    throw new Error(errorData?.detail || i18n.t('errors:aiAdvisor.reviewUpdate', { id, status: response.status }));
  }
  return response.json();
};

export const deleteReview = async (id: number): Promise<void> => {
  const response = await authFetch(`/api/ai-advisor/review/${id}`, { method: 'DELETE' });
  if (!response.ok) {
    const errorData = await response.json().catch(() => null);
    throw new Error(errorData?.detail || i18n.t('errors:aiAdvisor.reviewDelete', { id, status: response.status }));
  }
};

// ── Behavioral Metrics & Insight API functions ─────────────────────────────

export const computeBehavioralMetrics = (windowDays = 90): Promise<BehavioralMetricsResponse> =>
  authFetch(`/api/ai-advisor/behavioral-metrics/compute?window_days=${windowDays}`, { method: 'POST' })
    .then(r => r.json());

export const getLatestBehavioralMetrics = (): Promise<BehavioralMetric[]> =>
  authFetch('/api/ai-advisor/behavioral-metrics/latest').then(r => r.json());

export const listInsights = (params?: { status?: string; category?: string; limit?: number }): Promise<InsightItem[]> => {
  const qs = new URLSearchParams();
  if (params?.status) qs.set('status', params.status);
  if (params?.category) qs.set('category', params.category);
  if (params?.limit) qs.set('limit', String(params.limit));
  return authFetch(`/api/ai-advisor/insights?${qs}`).then(r => r.json());
};

export const promoteInsight = (id: number): Promise<InsightItem> =>
  authFetch(`/api/ai-advisor/insights/${id}/promote`, { method: 'POST' }).then(r => r.json());

export const mergeInsights = (primaryId: number, duplicateId: number): Promise<InsightItem> =>
  authFetch(`/api/ai-advisor/insights/${primaryId}/merge?duplicate_id=${duplicateId}`, { method: 'POST' }).then(r => r.json());

// ── F6 Insight Library governance (PRD 2026-07-07) ─────────────────────────

export const addValidatedCase = (id: number, link: string, note?: string): Promise<ValidatedCaseResponse> =>
  authFetch(`/api/ai-advisor/insights/${id}/validated-cases`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ link, note }),
  }).then(r => r.json());

export const setRuleLayer = (id: number, ruleLayer: 'principle' | 'checklist_item'): Promise<{ insight_id: number; rule_layer: string }> =>
  authFetch(`/api/ai-advisor/insights/${id}/rule-layer`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ rule_layer: ruleLayer }),
  }).then(r => r.json());

export const addCitation = (id: number, memoId: string, note?: string): Promise<RuleCitation> =>
  authFetch(`/api/ai-advisor/insights/${id}/citations`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ memo_id: memoId, note }),
  }).then(r => r.json());

export const listCitations = (id: number): Promise<RuleCitation[]> =>
  authFetch(`/api/ai-advisor/insights/${id}/citations`).then(r => r.json());

export const getGovernanceReport = (year?: number, quarter?: number): Promise<GovernanceReport> => {
  const qs = new URLSearchParams();
  if (year) qs.set('year', String(year));
  if (quarter) qs.set('quarter', String(quarter));
  return authFetch(`/api/ai-advisor/insights/governance-report?${qs}`).then(r => r.json());
};

export const getChecklistExport = (): Promise<string> =>
  authFetch('/api/ai-advisor/insights/checklist-export').then(r => r.text());

// ---------------------------------------------------------------------------
// Asset Analysis (Phase 4 — DSA)
// ---------------------------------------------------------------------------

export async function analyzeAsset(assetCode: string, analysisType = 'full'): Promise<AnalysisResult> {
  const res = await authFetch('/api/ai-advisor/analyze', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ asset_code: assetCode, analysis_type: analysisType }),
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    throw new Error((err as { detail?: string })?.detail || i18n.t('errors:aiAdvisor.analysis', { status: res.status }));
  }
  return res.json();
}

export async function getAnalysisHistory(assetCode?: string, limit = 10): Promise<AnalysisHistoryItem[]> {
  const params = new URLSearchParams();
  if (assetCode) params.set('asset_code', assetCode);
  params.set('limit', String(limit));
  const res = await authFetch(`/api/ai-advisor/analyze/history?${params}`);
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    throw new Error((err as { detail?: string })?.detail || i18n.t('errors:aiAdvisor.historyFetch', { status: res.status }));
  }
  return res.json();
}

export async function getAnalysisById(analysisId: number): Promise<AnalysisResult> {
  const res = await authFetch(`/api/ai-advisor/analyze/${analysisId}`);
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    throw new Error((err as { detail?: string })?.detail || i18n.t('errors:aiAdvisor.analysisFetch', { status: res.status }));
  }
  return res.json();
}

export async function searchAnalyzableAssets(
  q: string,
  signal?: AbortSignal
): Promise<AnalyzableAssetSearchResult[]> {
  const res = await authFetch(`/api/ai-advisor/analyze/search?q=${encodeURIComponent(q)}`, { signal });
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    throw new Error((err as { detail?: string })?.detail || i18n.t('errors:aiAdvisor.searchFailed', { status: res.status }));
  }
  return res.json();
}

// ---------------------------------------------------------------------------
// Decision Intelligence Feedback Loop — Phase 1 client (Steps 10–12)
// ---------------------------------------------------------------------------

export const aiAdvisorVerify = {
  async listPending(since: string, until?: string, limit = 50, status = 'pending'): Promise<{ items: PendingVerificationItem[] }> {
    const params = new URLSearchParams({ since, limit: String(limit), status });
    if (until) params.set('until', until);
    const res = await authFetch(`${API_BASE}/ai-advisor/trades/pending-verification?${params}`);
    if (!res.ok) throw new Error(await safeReadError(res, i18n.t('errors:aiAdvisor.pendingVerifications')));
    return res.json();
  },

  async verifyTrade(id: number, body: VerifyTradeBody): Promise<PendingVerificationItem> {
    const res = await authFetch(`${API_BASE}/ai-advisor/trades/${id}/verify`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
    if (res.status === 412) throw new Error(`stale_updated_at: ${i18n.t('errors:aiAdvisor.staleUpdatedAt')}`);
    if (res.status === 409) throw new Error(`conflict: ${i18n.t('errors:aiAdvisor.conflictAlreadyVerified')}`);
    if (!res.ok) throw new Error(await safeReadError(res, i18n.t('errors:aiAdvisor.verifyFailed')));
    return res.json();
  },

  async reopenVerification(id: number, expected_updated_at?: string): Promise<PendingVerificationItem> {
    const res = await authFetch(`${API_BASE}/ai-advisor/trades/${id}/reopen-verification`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ expected_updated_at }),
    });
    if (res.status === 412) throw new Error(`stale_updated_at: ${i18n.t('errors:aiAdvisor.staleUpdatedAt')}`);
    if (!res.ok) throw new Error(await safeReadError(res, i18n.t('errors:aiAdvisor.reopenFailed')));
    return res.json();
  },

  async generateCrossCheckAudit(period_start: string, period_end: string, model?: string): Promise<CrossCheckAuditResult> {
    const res = await authFetch(`${API_BASE}/ai-advisor/review/cross-check`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ period_start, period_end, model }),
    });
    if (res.status === 422) throw new Error(await safeReadError(res, i18n.t('errors:aiAdvisor.periodExceedsCaps')));
    if (!res.ok) throw new Error(await safeReadError(res, i18n.t('errors:aiAdvisor.crossCheckAudit')));
    return res.json();
  },

  async getVerdictMismatchRate(since: string): Promise<{ since: string; total_scored: number; threshold_keyword_mismatch_count: number; mismatch_rate_pct: number }> {
    const res = await authFetch(`${API_BASE}/ai-advisor/diagnostics/verdict-mismatch-rate?since=${since}`);
    if (!res.ok) throw new Error(await safeReadError(res, i18n.t('errors:aiAdvisor.mismatchRate')));
    return res.json();
  },

  async proposeMemoUpdates(memoId: number, auditReportId?: number): Promise<MemoProposalResult> {
    const res = await authFetch(`${API_BASE}/ai-advisor/memos/${memoId}/propose-updates`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ audit_report_id: auditReportId ?? null }),
    });
    if (res.status === 404) throw new Error(await safeReadError(res, i18n.t('errors:aiAdvisor.memoNotFound')));
    if (!res.ok) throw new Error(await safeReadError(res, i18n.t('errors:aiAdvisor.generateMemoProposals')));
    return res.json();
  },

  async getInsightLinks(insightId: number): Promise<{ links: InsightTradeLink[] }> {
    const res = await authFetch(`${API_BASE}/ai-advisor/insights/${insightId}/links`);
    if (!res.ok) throw new Error(await safeReadError(res, i18n.t('errors:aiAdvisor.insightLinks')));
    return res.json();
  },

  async addManualLink(insightId: number, tradeId: number, rationale?: string): Promise<{ id: number; insight_id: number; trade_id: number; link_type: string }> {
    const res = await authFetch(`${API_BASE}/ai-advisor/links`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ insight_id: insightId, trade_id: tradeId, rationale: rationale ?? '' }),
    });
    if (res.status === 409) throw new Error(i18n.t('errors:aiAdvisor.linkExists'));
    if (!res.ok) throw new Error(await safeReadError(res, i18n.t('errors:aiAdvisor.createLink')));
    return res.json();
  },

  async deleteLink(linkId: number): Promise<void> {
    const res = await authFetch(`${API_BASE}/ai-advisor/links/${linkId}`, { method: 'DELETE' });
    if (!res.ok) throw new Error(await safeReadError(res, i18n.t('errors:aiAdvisor.deleteLink')));
  },
};
