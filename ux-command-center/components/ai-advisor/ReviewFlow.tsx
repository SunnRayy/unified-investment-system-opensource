import React, { useCallback, useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import type {
  ContextConfig,
  ReviewQuestion,
  ReviewAnswer,
  ReviewDetailResponse,
  ReviewHistoryItem,
  ReviewResponse,
} from '../../src/services/api';
import {
  deleteReview,
  generateReviewQuestions,
  generateReview,
  getReviewById,
  getReviewHistory,
  renderAdvisorContext,
  updateReview,
} from '../../src/services/api';
import { BriefSection, REVIEW_SECTION_ORDER } from './BriefSection';
import { LLMDebugLog } from './LLMDebugLog';

type ReviewPhase = 'setup' | 'loading-questions' | 'answering' | 'previewing' | 'ready' | 'generating' | 'complete';

interface ReviewFlowProps {
  contextConfig: ContextConfig;
}

// REVIEW_SECTION_ORDER is imported from BriefSection — the section IDs and the
// styling keyed off them belong in one place (Program BIL / WS-5).

function getDefaultPeriodStart(): string {
  const d = new Date(new Date().getFullYear(), new Date().getMonth() - 1, 1);
  return d.toISOString().slice(0, 10);
}

function getDefaultPeriodEnd(): string {
  const d = new Date(new Date().getFullYear(), new Date().getMonth(), 0);
  return d.toISOString().slice(0, 10);
}

function Spinner() {
  return (
    <svg className="animate-spin h-5 w-5 text-primary" viewBox="0 0 24 24" fill="none">
      <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
      <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8v8H4z" />
    </svg>
  );
}

function slugifyReviewTitle(title: string) {
  const normalized = title
    .toLowerCase()
    .trim()
    .replace(/[\s_]+/g, '-')
    .replace(/[^\p{L}\p{N}-]+/gu, '')
    .replace(/-+/g, '-')
    .replace(/^-+|-+$/g, '');

  return (normalized.slice(0, 60).replace(/-+$/g, '') || 'review');
}

function buildReviewFilename(review: Pick<ReviewResponse, 'title' | 'period_start' | 'created_at'>) {
  const datePart = review.period_start || review.created_at.slice(0, 10);
  const titlePart = slugifyReviewTitle(review.title || 'review-report');
  return `${datePart}-${titlePart}.md`;
}

function downloadReviewMarkdown(review: Pick<ReviewResponse, 'content_markdown' | 'title' | 'period_start' | 'created_at'>) {
  const blob = new Blob([review.content_markdown], { type: 'text/markdown' });
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement('a');
  anchor.href = url;
  anchor.download = buildReviewFilename(review);
  document.body.appendChild(anchor);
  anchor.click();
  document.body.removeChild(anchor);
  URL.revokeObjectURL(url);
}

export function ReviewFlow({ contextConfig }: ReviewFlowProps) {
  const { t } = useTranslation('aiAdvisor');
  const [phase, setPhase] = useState<ReviewPhase>('setup');
  const [periodStart, setPeriodStart] = useState(getDefaultPeriodStart());
  const [periodEnd, setPeriodEnd] = useState(getDefaultPeriodEnd());
  const [questions, setQuestions] = useState<ReviewQuestion[]>([]);
  const [answers, setAnswers] = useState<Record<number, string>>({});
  const [review, setReview] = useState<ReviewResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [contextDraft, setContextDraft] = useState('');
  const [previewStateKey, setPreviewStateKey] = useState<string | null>(null);
  const [history, setHistory] = useState<ReviewHistoryItem[]>([]);
  const [historyLoading, setHistoryLoading] = useState(true);
  const [historySelectionId, setHistorySelectionId] = useState<number | null>(null);
  const [historyDownloadingId, setHistoryDownloadingId] = useState<number | null>(null);
  const [historyDeletingId, setHistoryDeletingId] = useState<number | null>(null);
  const [editingReviewId, setEditingReviewId] = useState<number | null>(null);
  const [editTitle, setEditTitle] = useState('');
  const [editJson, setEditJson] = useState('');
  const [savingEdit, setSavingEdit] = useState(false);

  const toRenderableReview = useCallback((detail: ReviewDetailResponse): ReviewResponse => ({
    id: detail.id,
    report_type: 'review',
    title: detail.title,
    content_json: detail.content_json,
    content_markdown: 'content_markdown' in detail ? detail.content_markdown : '',
    model_used: detail.model_used,
    created_at: detail.created_at,
    period_start: detail.period_start,
    period_end: detail.period_end,
    usage: { prompt_tokens: 0, completion_tokens: 0, total_tokens: 0 },
    prompt_text: detail.prompt_text ?? undefined,
    raw_response_text: detail.raw_response_text ?? undefined,
  }), []);

  const loadHistory = useCallback(async () => {
    setHistoryLoading(true);
    try {
      const items = await getReviewHistory(10);
      setHistory(Array.isArray(items) ? items : []);
    } catch {
      setHistory([]);
    } finally {
      setHistoryLoading(false);
    }
  }, []);

  useEffect(() => {
    void loadHistory();
  }, [loadHistory]);

  const questionsAnswers = questions.map((q) => ({
    question: q.question,
    answer: answers[q.id] ?? '',
  }));
  const currentPreviewKey = JSON.stringify({
    contextConfig,
    periodStart,
    periodEnd,
    questions: questions.map((q) => [q.id, answers[q.id] ?? '']),
  });
  const isPreviewCurrent = Boolean(contextDraft.trim()) && previewStateKey === currentPreviewKey;

  const handleLoadQuestions = async () => {
    setError(null);
    setPhase('loading-questions');
    try {
      const result = await generateReviewQuestions(periodStart, periodEnd);
      setQuestions(result.questions);
      setAnswers({});
      setPhase('answering');
    } catch (e) {
      setError(e instanceof Error ? e.message : t('reviewFlow.errors.loadQuestions'));
      setPhase('setup');
    }
  };

  const handlePreviewContext = async () => {
    setError(null);
    setPhase('previewing');
    try {
      const result = await renderAdvisorContext('review', contextConfig, {
        period_start: periodStart,
        period_end: periodEnd,
        questions_answers: questionsAnswers,
      });
      setContextDraft(result.context_text);
      setPreviewStateKey(currentPreviewKey);
      setPhase('ready');
    } catch (e) {
      setError(e instanceof Error ? e.message : t('reviewFlow.errors.previewContext'));
      setPhase('answering');
    }
  };

  const handleSubmit = async () => {
    if (!isPreviewCurrent) {
      return;
    }
    setError(null);
    setPhase('generating');
    try {
      const result = await generateReview(
        questionsAnswers,
        periodStart,
        periodEnd,
        contextConfig,
        contextDraft,
      );
      setReview(result);
      await loadHistory();
      setPhase('complete');
    } catch (e) {
      setError(e instanceof Error ? e.message : t('reviewFlow.errors.generateReview'));
      setPhase('ready');
    }
  };

  const beginEditing = useCallback((detail: ReviewDetailResponse) => {
    setReview(toRenderableReview(detail));
    setEditingReviewId(detail.id);
    setEditTitle(detail.title ?? t('reviewFlow.reviewNumber', { id: detail.id }));
    setEditJson(JSON.stringify(detail.content_json, null, 2));
    setPhase('complete');
  }, [toRenderableReview]);

  const handleOpenHistory = async (id: number) => {
    setError(null);
    setHistorySelectionId(id);
    try {
      const detail = await getReviewById(id);
      setReview(toRenderableReview(detail));
      setPhase('complete');
    } catch (e) {
      setError(e instanceof Error ? e.message : t('reviewFlow.errors.loadSavedReview'));
    } finally {
      setHistorySelectionId(null);
    }
  };

  const handleEditHistory = async (id: number) => {
    setError(null);
    setHistorySelectionId(id);
    try {
      const detail = await getReviewById(id);
      beginEditing(detail);
    } catch (e) {
      setError(e instanceof Error ? e.message : t('reviewFlow.errors.loadSavedReview'));
    } finally {
      setHistorySelectionId(null);
    }
  };

  const handleDownloadHistory = async (id: number) => {
    setError(null);
    setHistoryDownloadingId(id);
    try {
      let detail: ReviewDetailResponse;
      if (review?.id === id && review.content_markdown) {
        detail = {
          id,
          title: review.title ?? t('reviewFlow.reviewNumber', { id }),
          model_used: review.model_used,
          created_at: review.created_at,
          content_json: review.content_json,
          content_markdown: review.content_markdown,
          period_start: review.period_start,
          period_end: review.period_end,
          prompt_text: review.prompt_text ?? null,
          raw_response_text: review.raw_response_text ?? null,
        };
      } else {
        detail = await getReviewById(id);
      }
      downloadReviewMarkdown({
        title: detail.title,
        period_start: detail.period_start,
        created_at: detail.created_at,
        content_markdown: 'content_markdown' in detail ? detail.content_markdown : '',
      });
    } catch (e) {
      setError(e instanceof Error ? e.message : t('reviewFlow.errors.downloadSavedReview'));
    } finally {
      setHistoryDownloadingId(null);
    }
  };

  const handleDeleteHistory = async (id: number) => {
    if (!window.confirm(t('reviewFlow.confirmDelete'))) return;
    setError(null);
    setHistoryDeletingId(id);
    try {
      await deleteReview(id);
      if (review?.id === id) {
        setReview(null);
        setEditingReviewId(null);
        setEditTitle('');
        setEditJson('');
        setPhase('setup');
      }
      await loadHistory();
    } catch (e) {
      setError(e instanceof Error ? e.message : t('reviewFlow.errors.deleteSavedReview'));
    } finally {
      setHistoryDeletingId(null);
    }
  };

  const handleStartEditCurrent = async () => {
    if (!review?.id) return;
    setError(null);
    try {
      const detail = await getReviewById(review.id);
      beginEditing(detail);
    } catch (e) {
      setError(e instanceof Error ? e.message : t('reviewFlow.errors.loadReviewForEditing'));
    }
  };

  const handleSaveEdit = async () => {
    if (!editingReviewId) return;
    const trimmedTitle = editTitle.trim();
    if (!trimmedTitle) {
      setError(t('reviewFlow.errors.titleEmpty'));
      return;
    }

    let parsedContent: Record<string, unknown>;
    try {
      parsedContent = JSON.parse(editJson) as Record<string, unknown>;
    } catch {
      setError(t('reviewFlow.errors.invalidJson'));
      return;
    }

    if (!parsedContent || Array.isArray(parsedContent) || typeof parsedContent !== 'object') {
      setError(t('reviewFlow.errors.jsonMustBeObject'));
      return;
    }

    setSavingEdit(true);
    setError(null);
    try {
      const updated = await updateReview(editingReviewId, {
        title: trimmedTitle,
        content_json: parsedContent as Record<string, typeof review.content_json[string]>,
      });
      setReview(toRenderableReview(updated));
      setEditingReviewId(null);
      await loadHistory();
    } catch (e) {
      setError(e instanceof Error ? e.message : t('reviewFlow.errors.updateReview'));
    } finally {
      setSavingEdit(false);
    }
  };

  const handleCancelEdit = () => {
    setEditingReviewId(null);
    setEditTitle('');
    setEditJson('');
  };

  const handleReset = () => {
    setPhase('setup');
    setQuestions([]);
    setAnswers({});
    setReview(null);
    setError(null);
    setContextDraft('');
    setPreviewStateKey(null);
    setEditingReviewId(null);
    setEditTitle('');
    setEditJson('');
  };

  const allAnswered = questions.length > 0 && questions.every((q) => (answers[q.id] ?? '').trim() !== '');

  const RecentReviews = (
    <div className="rounded-xl border border-slate-200 dark:border-border-dark bg-white dark:bg-card-dark p-4">
      <div className="flex items-center justify-between gap-3 mb-3">
        <div>
          <h3 className="text-sm font-semibold text-slate-800 dark:text-slate-100">{t('reviewFlow.recentReviews')}</h3>
          <p className="text-xs text-slate-400 mt-1">{t('reviewFlow.savedReportsSubtitle')}</p>
        </div>
      </div>
      {historyLoading ? (
        <p className="text-xs text-slate-400">{t('reviewFlow.loadingHistory')}</p>
      ) : history.length === 0 ? (
        <p className="text-xs text-slate-400">{t('reviewFlow.noSavedReviews')}</p>
      ) : (
        <div className="space-y-2">
          {history.map((item) => (
            <div
              key={item.id}
              className="rounded-lg border border-slate-200 dark:border-border-dark px-3 py-3 transition-colors hover:bg-slate-50 dark:hover:bg-slate-800"
            >
              <div className="flex items-center justify-between gap-3">
                <div className="min-w-0">
                  <div className="text-sm font-medium text-slate-800 dark:text-slate-100 truncate">
                    {item.title || t('reviewFlow.reviewNumber', { id: item.id })}
                  </div>
                  <div className="text-xs text-slate-400 mt-1">
                    {item.period_start && item.period_end ? `${item.period_start} — ${item.period_end}` : item.created_at}
                  </div>
                </div>
                <div className="flex items-center gap-2 shrink-0">
                  <button
                    type="button"
                    onClick={() => handleOpenHistory(item.id)}
                    disabled={historySelectionId === item.id}
                    className="text-xs text-slate-400 hover:text-slate-600 dark:hover:text-slate-200 disabled:opacity-50 transition-colors"
                  >
                    {historySelectionId === item.id ? t('reviewFlow.opening') : t('reviewFlow.open')}
                  </button>
                  <button
                    type="button"
                    onClick={() => void handleEditHistory(item.id)}
                    disabled={historySelectionId === item.id}
                    className="text-slate-400 hover:text-slate-600 dark:hover:text-slate-200 disabled:opacity-50 transition-colors"
                    title={t('reviewFlow.editReview')}
                  >
                    <span className="material-symbols-outlined !text-[14px]">edit</span>
                  </button>
                  <button
                    type="button"
                    onClick={() => void handleDownloadHistory(item.id)}
                    disabled={historyDownloadingId === item.id}
                    className="text-slate-400 hover:text-slate-600 dark:hover:text-slate-200 disabled:opacity-50 transition-colors"
                    title={t('reviewFlow.downloadReview')}
                  >
                    <span className="material-symbols-outlined !text-[14px]">download</span>
                  </button>
                  <button
                    type="button"
                    onClick={() => void handleDeleteHistory(item.id)}
                    disabled={historyDeletingId === item.id}
                    className="text-slate-400 hover:text-red-500 disabled:opacity-50 transition-colors"
                    title={t('reviewFlow.deleteReview')}
                  >
                    <span className="material-symbols-outlined !text-[14px]">delete</span>
                  </button>
                </div>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );

  // ── Error banner ──────────────────────────────────────────────────────────
  const ErrorBanner = error ? (
    <div className="mb-4 flex items-start gap-2 p-3 rounded-lg bg-red-50 dark:bg-red-900/20 border border-red-200 dark:border-red-800 text-sm text-red-600 dark:text-red-400">
      <span className="material-symbols-outlined !text-[16px] mt-0.5 flex-shrink-0">error</span>
      <span className="flex-1">{error}</span>
      <button
        type="button"
        onClick={handleReset}
        className="text-xs underline hover:no-underline flex-shrink-0"
      >
        {t('reviewFlow.retry')}
      </button>
    </div>
  ) : null;

  // ── Phase: setup ──────────────────────────────────────────────────────────
  if (phase === 'setup' || phase === 'loading-questions') {
    return (
      <div className="w-full py-6">
        {ErrorBanner}
        <div className="grid gap-5 lg:grid-cols-[minmax(0,1.2fr)_minmax(320px,0.8fr)] items-start">
          <div className="rounded-xl border border-slate-200 dark:border-border-dark bg-white dark:bg-card-dark p-6">
            <div className="flex items-center gap-3 mb-5">
              <span className="material-symbols-outlined !text-[28px] text-primary">rate_review</span>
              <div>
                <h2 className="text-base font-semibold text-slate-800 dark:text-slate-100">{t('reviewFlow.reviewSetup')}</h2>
                <p className="text-xs text-slate-400 mt-0.5">{t('reviewFlow.reviewSetupSubtitle')}</p>
              </div>
            </div>

            <div className="space-y-4">
              <div>
                <label htmlFor="review-period-start" className="block text-xs font-medium text-slate-600 dark:text-slate-400 mb-1">
                  {t('reviewFlow.periodStart')}
                </label>
                <input
                  id="review-period-start"
                  type="date"
                  value={periodStart}
                  onChange={(e) => setPeriodStart(e.target.value)}
                  className="w-full rounded-lg border border-slate-200 dark:border-border-dark bg-slate-50 dark:bg-slate-800 px-3 py-2 text-sm text-slate-800 dark:text-slate-100 focus:outline-none focus:ring-2 focus:ring-primary/50"
                />
              </div>
              <div>
                <label htmlFor="review-period-end" className="block text-xs font-medium text-slate-600 dark:text-slate-400 mb-1">
                  {t('reviewFlow.periodEnd')}
                </label>
                <input
                  id="review-period-end"
                  type="date"
                  value={periodEnd}
                  onChange={(e) => setPeriodEnd(e.target.value)}
                  className="w-full rounded-lg border border-slate-200 dark:border-border-dark bg-slate-50 dark:bg-slate-800 px-3 py-2 text-sm text-slate-800 dark:text-slate-100 focus:outline-none focus:ring-2 focus:ring-primary/50"
                />
              </div>
            </div>

            <button
              type="button"
              onClick={handleLoadQuestions}
              disabled={phase === 'loading-questions' || !periodStart || !periodEnd}
              className="mt-6 w-full flex items-center justify-center gap-2 px-4 py-2.5 bg-primary text-white rounded-lg text-sm font-medium hover:bg-primary/90 disabled:opacity-50 transition-colors"
            >
              {phase === 'loading-questions' ? (
                <>
                  <Spinner />
                  {t('reviewFlow.generatingQuestions')}
                </>
              ) : (
                <>
                  <span className="material-symbols-outlined !text-[16px]">auto_awesome</span>
                  {t('reviewFlow.generateReviewQuestions')}
                </>
              )}
            </button>
          </div>

          {RecentReviews}
        </div>
      </div>
    );
  }

  // ── Phase: answering ──────────────────────────────────────────────────────
  if (phase === 'answering' || phase === 'previewing' || phase === 'ready' || phase === 'generating') {
    return (
      <div className="w-full py-6">
        {ErrorBanner}

        {/* Period header */}
        <div className="flex items-center justify-between mb-5">
          <div>
            <h2 className="text-base font-semibold text-slate-800 dark:text-slate-100">{t('reviewFlow.reviewQuestionnaire')}</h2>
            <p className="text-xs text-slate-400 mt-0.5">
              {periodStart} — {periodEnd}
            </p>
          </div>
          <button
            type="button"
            onClick={handleReset}
            className="text-xs text-slate-400 hover:text-slate-600 dark:hover:text-slate-300 transition-colors flex items-center gap-1"
          >
            <span className="material-symbols-outlined !text-[14px]">arrow_back</span>
            {t('reviewFlow.backToSetup')}
          </button>
        </div>

        {/* Questions */}
        <div className="space-y-4 mb-6">
          {questions.map((q) => (
            <div
              key={q.id}
              className="rounded-xl border border-slate-200 dark:border-border-dark bg-white dark:bg-card-dark p-4"
            >
              <div className="flex items-start justify-between gap-3 mb-2">
                <div className="flex-1">
                  <p className="text-sm font-medium text-slate-800 dark:text-slate-100 leading-snug">
                    {q.question}
                  </p>
                  {q.context && (
                    <p className="text-xs text-slate-400 mt-1 leading-relaxed">{q.context}</p>
                  )}
                </div>
                <button
                  type="button"
                  onClick={() =>
                    setAnswers((prev) => ({ ...prev, [q.id]: '(skipped)' }))
                  }
                  className="text-xs text-slate-400 hover:text-slate-600 dark:hover:text-slate-300 transition-colors flex-shrink-0 underline"
                >
                  {t('reviewFlow.skip')}
                </button>
              </div>
              <textarea
                value={answers[q.id] ?? ''}
                onChange={(e) =>
                  setAnswers((prev) => ({ ...prev, [q.id]: e.target.value }))
                }
                placeholder={t('reviewFlow.answerPlaceholder')}
                rows={3}
                className="w-full rounded-lg border border-slate-200 dark:border-border-dark bg-slate-50 dark:bg-slate-800 px-3 py-2 text-sm text-slate-800 dark:text-slate-100 placeholder-slate-400 focus:outline-none focus:ring-2 focus:ring-primary/50 resize-y"
              />
            </div>
          ))}
        </div>

        <div className="rounded-xl border border-slate-200 dark:border-border-dark bg-white dark:bg-card-dark p-4 mb-4">
          <div className="flex items-start justify-between gap-3 mb-3">
            <div>
              <h3 className="text-sm font-semibold text-slate-800 dark:text-slate-100">{t('reviewFlow.reviewedContext')}</h3>
              <p className="text-xs text-slate-400 mt-1">
                {t('reviewFlow.reviewedContextHint')}
              </p>
            </div>
            <span className={`rounded-full px-2.5 py-1 text-[11px] font-medium ${
              isPreviewCurrent ? 'bg-emerald-50 text-emerald-700' : 'bg-amber-50 text-amber-700'
            }`}>
              {isPreviewCurrent ? t('reviewFlow.previewReady') : t('reviewFlow.previewRequired')}
            </span>
          </div>
          <textarea
            value={contextDraft}
            onChange={(e) => setContextDraft(e.target.value)}
            placeholder={t('reviewFlow.previewPlaceholder')}
            rows={10}
            className="w-full rounded-xl border border-slate-200 dark:border-border-dark bg-slate-50 dark:bg-slate-800 px-3 py-3 text-sm text-slate-800 dark:text-slate-100 placeholder-slate-400 focus:outline-none focus:ring-2 focus:ring-primary/50 resize-y font-mono"
          />
          {!isPreviewCurrent && contextDraft.trim() && (
            <p className="mt-2 text-xs text-amber-600 dark:text-amber-400">
              {t('reviewFlow.previewStale')}
            </p>
          )}
        </div>

        <div className="flex flex-col gap-3">
          <button
            type="button"
            onClick={handlePreviewContext}
            disabled={!allAnswered || phase === 'previewing' || phase === 'generating'}
            className="w-full flex items-center justify-center gap-2 px-4 py-2.5 rounded-lg text-sm font-medium border border-slate-200 dark:border-border-dark bg-white dark:bg-card-dark text-slate-700 dark:text-slate-200 hover:bg-slate-50 dark:hover:bg-slate-800 disabled:opacity-50 transition-colors"
          >
            {phase === 'previewing' ? (
              <>
                <Spinner />
                {t('reviewFlow.previewingContext')}
              </>
            ) : (
              <>
                <span className="material-symbols-outlined !text-[16px]">preview</span>
                {t('reviewFlow.previewContext')}
              </>
            )}
          </button>
          <button
            type="button"
            onClick={handleSubmit}
            disabled={!allAnswered || phase === 'previewing' || phase === 'generating' || !isPreviewCurrent}
            className="w-full flex items-center justify-center gap-2 px-4 py-2.5 bg-primary text-white rounded-lg text-sm font-medium hover:bg-primary/90 disabled:opacity-50 transition-colors"
          >
            {phase === 'generating' ? (
              <>
                <Spinner />
                {t('reviewFlow.generatingReport')}
              </>
            ) : (
              <>
                <span className="material-symbols-outlined !text-[16px]">auto_awesome</span>
                {t('reviewFlow.generateReview')}
              </>
            )}
          </button>
        </div>
        {!allAnswered && phase !== 'previewing' && phase !== 'generating' && (
          <p className="text-xs text-slate-400 text-center mt-2">{t('reviewFlow.answerAllHint')}</p>
        )}
      </div>
    );
  }

  // ── Phase: complete ───────────────────────────────────────────────────────
  if (phase === 'complete' && review) {
    const orderedSections = REVIEW_SECTION_ORDER.filter((k) => k in review.content_json);
    const fallbackSections = Object.keys(review.content_json);

    return (
      <div className="w-full py-6">
        {ErrorBanner}

        {/* Header row */}
        <div className="flex items-center justify-between mb-4">
          <div>
            <h2 className="text-base font-semibold text-slate-800 dark:text-slate-100">
              {review.title || t('reviewFlow.reviewReport')}
            </h2>
            {review.period_start && review.period_end && (
              <p className="text-xs text-slate-400 mt-0.5">
                {review.period_start} — {review.period_end}
              </p>
            )}
          </div>
          <div className="flex items-center gap-2">
            {editingReviewId === review.id ? (
              <>
                <button
                  type="button"
                  onClick={handleCancelEdit}
                  className="flex items-center gap-1.5 text-xs text-slate-500 hover:text-slate-700 dark:hover:text-slate-300 transition-colors px-2.5 py-1 rounded-md border border-slate-200 dark:border-border-dark"
                >
                  {t('reviewFlow.cancel')}
                </button>
                <button
                  type="button"
                  onClick={() => void handleSaveEdit()}
                  disabled={savingEdit}
                  className="flex items-center gap-1.5 text-xs bg-primary text-white transition-colors px-2.5 py-1 rounded-md hover:bg-primary/90 disabled:opacity-50"
                >
                  {savingEdit ? t('reviewFlow.saving') : t('reviewFlow.saveChanges')}
                </button>
              </>
            ) : (
              <>
                <button
                  type="button"
                  onClick={() => void handleStartEditCurrent()}
                  className="flex items-center gap-1.5 text-xs text-slate-500 hover:text-slate-700 dark:hover:text-slate-300 transition-colors px-2.5 py-1 rounded-md border border-slate-200 dark:border-border-dark"
                >
                  <span className="material-symbols-outlined !text-[14px]">edit</span>
                  {t('reviewFlow.edit')}
                </button>
                <button
                  type="button"
                  onClick={() => downloadReviewMarkdown(review)}
                  className="flex items-center gap-1.5 text-xs text-slate-500 hover:text-slate-700 dark:hover:text-slate-300 transition-colors px-2.5 py-1 rounded-md border border-slate-200 dark:border-border-dark"
                >
                  <span className="material-symbols-outlined !text-[14px]">download</span>
                  {t('reviewFlow.download')}
                </button>
                <button
                  type="button"
                  onClick={() => void handleDeleteHistory(review.id!)}
                  className="flex items-center gap-1.5 text-xs text-red-500 hover:text-red-600 transition-colors px-2.5 py-1 rounded-md border border-red-200"
                >
                  <span className="material-symbols-outlined !text-[14px]">delete</span>
                  {t('reviewFlow.delete')}
                </button>
                <button
                  type="button"
                  onClick={handleReset}
                  className="flex items-center gap-1.5 text-xs text-slate-500 hover:text-slate-700 dark:hover:text-slate-300 transition-colors px-2.5 py-1 rounded-md border border-slate-200 dark:border-border-dark"
                >
                  <span className="material-symbols-outlined !text-[14px]">refresh</span>
                  {t('reviewFlow.newReview')}
                </button>
              </>
            )}
          </div>
        </div>

        {editingReviewId === review.id && (
          <div className="rounded-xl border border-slate-200 dark:border-border-dark bg-white dark:bg-card-dark p-4 mb-4">
            <div className="space-y-3">
              <div>
                <label htmlFor="review-title" className="block text-xs font-medium text-slate-600 dark:text-slate-400 mb-1">
                  {t('reviewFlow.reviewTitle')}
                </label>
                <input
                  id="review-title"
                  aria-label={t('reviewFlow.reviewTitle')}
                  type="text"
                  value={editTitle}
                  onChange={(e) => setEditTitle(e.target.value)}
                  className="w-full rounded-lg border border-slate-200 dark:border-border-dark bg-slate-50 dark:bg-slate-800 px-3 py-2 text-sm text-slate-800 dark:text-slate-100 focus:outline-none focus:ring-2 focus:ring-primary/50"
                />
              </div>
              <div>
                <label htmlFor="review-json" className="block text-xs font-medium text-slate-600 dark:text-slate-400 mb-1">
                  {t('reviewFlow.reviewJson')}
                </label>
                <textarea
                  id="review-json"
                  aria-label={t('reviewFlow.reviewJson')}
                  rows={16}
                  value={editJson}
                  onChange={(e) => setEditJson(e.target.value)}
                  className="w-full rounded-xl border border-slate-200 dark:border-border-dark bg-slate-50 dark:bg-slate-800 px-3 py-3 text-sm text-slate-800 dark:text-slate-100 focus:outline-none focus:ring-2 focus:ring-primary/50 resize-y font-mono"
                />
              </div>
            </div>
          </div>
        )}

        {/* Sections */}
        {orderedSections.length > 0
          ? orderedSections.map((key) => (
              <BriefSection key={key} title={key} content={review.content_json[key]} />
            ))
          : fallbackSections.map((key) => (
              <BriefSection key={key} title={key} content={review.content_json[key]} />
            ))}

        {/* Footer */}
        <div className="text-xs text-slate-400 mt-3">
          {t('reviewFlow.footer', {
            model: review.model_used,
            tokens: review.usage?.total_tokens?.toLocaleString(),
            date: new Date(review.created_at).toLocaleString(),
          })}
        </div>

        <LLMDebugLog
          promptText={review.prompt_text}
          rawResponse={review.raw_response_text}
          modelUsed={review.model_used}
          tokenCount={review.usage?.total_tokens}
        />

        {RecentReviews}
      </div>
    );
  }

  return null;
}
