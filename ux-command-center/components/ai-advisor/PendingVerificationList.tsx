import React, { useCallback, useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import type { TFunction } from 'i18next';
import type { PendingVerificationItem, VerifyTradeBody } from '../../src/services/api';
import { aiAdvisorVerify } from '../../src/services/api';

function verifiedStatusLabel(status: string, t: TFunction): string {
  switch (status) {
    case 'verified': return t('pendingVerification.status.verified');
    case 'verification_blocked': return t('pendingVerification.status.blocked');
    default: return status;
  }
}

// ── helpers ──────────────────────────────────────────────────────────────────

/** Backend prefix marker on auto-generated verification_result text — a data-matching
 *  key, not display text. Extracted for the same reason as verifiedStatusLabel etc.:
 *  the ratchet's rule-3 scan can't tell a match-key literal from a display string once
 *  it's inside a .map() callback embedded in JSX. */
const AUTO_VERIFICATION_PREFIX = 'auto:';

function todayISO(): string {
  return new Date().toISOString().slice(0, 10);
}

function sinceISO(days: number): string {
  const d = new Date();
  d.setDate(d.getDate() - days);
  return d.toISOString().slice(0, 10);
}

function daysUntilMaturity(logDate: string): number {
  const maturityDate = new Date(logDate);
  maturityDate.setDate(maturityDate.getDate() + 30);
  const diff = maturityDate.getTime() - Date.now();
  return Math.max(0, Math.ceil(diff / (1000 * 60 * 60 * 24)));
}

function verdictLabel(verdict: string, t: TFunction): string {
  switch (verdict) {
    case 'good_call': return t('pendingVerification.verdict.goodCall');
    case 'regret': return t('pendingVerification.verdict.regret');
    case 'bullet_dodged': return t('pendingVerification.verdict.bulletDodged');
    case 'missed_opportunity': return t('pendingVerification.verdict.missedOpportunity');
    case 'neutral': return t('pendingVerification.verdict.neutral');
    default: return verdict;
  }
}

const VERDICT_EMOJI: Record<string, string> = {
  good_call: '✅',
  regret: '😖',
  bullet_dodged: '🛡',
  missed_opportunity: '😩',
  neutral: '⚪',
};

// ── sub-components ────────────────────────────────────────────────────────────

function SkeletonRow() {
  return (
    <tr className="animate-pulse">
      {Array.from({ length: 8 }).map((_, i) => (
        <td key={i} className="px-3 py-3">
          <div className="h-3 bg-slate-200 dark:bg-slate-700 rounded w-full" />
        </td>
      ))}
    </tr>
  );
}

interface VerdictChipProps {
  verdict: string | null;
  isMatured?: boolean;
  outcomePct?: number | null;
}
function VerdictChip({ verdict, isMatured, outcomePct }: VerdictChipProps) {
  const { t } = useTranslation('aiAdvisor');
  if (!verdict) {
    // Matured trade with a computed (inconclusive) outcome — needs explicit manual verdict.
    // This fallback only fires when the backend did not return a suggested_verdict
    // (e.g., no outcome data for a previously within-band trade). Normally, within-band
    // matured outcomes now surface as suggested_verdict='neutral' from the backend.
    if (isMatured && outcomePct != null) {
      return (
        <span
          className="inline-flex items-center gap-1 text-[11px] px-2 py-0.5 rounded-full bg-amber-100 text-amber-700 dark:bg-amber-900/30 dark:text-amber-300 font-medium whitespace-nowrap cursor-help"
          title={t('pendingVerification.setManuallyTitle')}
        >
          {t('pendingVerification.setManually')}
        </span>
      );
    }
    return <span className="text-slate-400">—</span>;
  }
  // Neutral verdict: slate/gray chip — within ±5% band, "按计划"
  if (verdict === 'neutral') {
    return (
      <span className="inline-flex items-center gap-1 text-[11px] px-2 py-0.5 rounded-full bg-slate-100 text-slate-600 dark:bg-slate-700 dark:text-slate-400 font-medium whitespace-nowrap">
        {VERDICT_EMOJI.neutral} {verdictLabel('neutral', t)}
      </span>
    );
  }
  return (
    <span className="inline-flex items-center gap-1 text-[11px] px-2 py-0.5 rounded-full bg-blue-100 text-blue-700 dark:bg-blue-900/30 dark:text-blue-300 font-medium whitespace-nowrap">
      {VERDICT_EMOJI[verdict] ?? ''} {verdictLabel(verdict, t)}
    </span>
  );
}

interface MaturityBadgeProps {
  isMatured: boolean;
  logDate: string;
}
function MaturityBadge({ isMatured, logDate }: MaturityBadgeProps) {
  const { t } = useTranslation('aiAdvisor');
  if (isMatured) {
    return (
      <span className="text-[11px] px-2 py-0.5 rounded-full bg-emerald-100 text-emerald-700 dark:bg-emerald-900/30 dark:text-emerald-300 font-medium whitespace-nowrap">
        {t('pendingVerification.matured')}
      </span>
    );
  }
  const daysLeft = daysUntilMaturity(logDate);
  return (
    <span className="text-[11px] px-2 py-0.5 rounded-full bg-amber-100 text-amber-700 dark:bg-amber-900/30 dark:text-amber-300 font-medium whitespace-nowrap">
      {t('pendingVerification.preWindow', { days: daysLeft })}
    </span>
  );
}

interface OutcomeCellProps {
  isMatured: boolean;
  logDate: string;
  outcomePct: number | null;
  outcomeToDatePct?: number | null;
  outcomeToDateAsof?: string | null;
}
function OutcomeCell({ isMatured, logDate, outcomePct, outcomeToDatePct, outcomeToDateAsof }: OutcomeCellProps) {
  const { t } = useTranslation('aiAdvisor');
  if (!isMatured) {
    // Interim "outcome so far" — show when a baseline + a later price both exist
    if (outcomeToDatePct != null) {
      const isPositive = outcomeToDatePct >= 0;
      const formatted = `${isPositive ? '+' : ''}${outcomeToDatePct.toFixed(2)}%`;
      // "截至 MM-DD" label for the as-of date
      const asofLabel = outcomeToDateAsof ? t('pendingVerification.asOf', { date: outcomeToDateAsof.slice(5) }) : null;
      return (
        <span className="flex flex-col items-start gap-0.5">
          <span
            className={`font-mono text-xs ${
              isPositive
                ? 'text-emerald-600/70 dark:text-emerald-400/70'
                : 'text-red-600/70 dark:text-red-400/70'
            }`}
            title={asofLabel ?? undefined}
          >
            {t('pendingVerification.toDate', { value: formatted })}
          </span>
          {asofLabel && (
            <span className="text-[10px] text-slate-400 dark:text-slate-500">{asofLabel}</span>
          )}
        </span>
      );
    }
    const daysLeft = daysUntilMaturity(logDate);
    return (
      <span
        className="text-slate-400 cursor-help underline decoration-dotted"
        title={t('pendingVerification.maturesIn', { count: daysLeft })}
      >
        —
      </span>
    );
  }
  // Matured: keep existing bold rendering
  if (outcomePct == null) return <span className="text-slate-400">—</span>;
  const isPositive = outcomePct >= 0;
  const formatted = `${isPositive ? '+' : ''}${outcomePct.toFixed(2)}%`;
  return (
    <span className={`font-mono text-xs font-medium ${isPositive ? 'text-emerald-600 dark:text-emerald-400' : 'text-red-600 dark:text-red-400'}`}>
      {formatted}
    </span>
  );
}

// ── verify modal ──────────────────────────────────────────────────────────────

interface VerifyModalProps {
  row: PendingVerificationItem;
  onClose: () => void;
  onSuccess: (updated: PendingVerificationItem) => void;
  onStale: () => void;
}

function VerifyModal({ row, onClose, onSuccess, onStale }: VerifyModalProps) {
  const { t } = useTranslation('aiAdvisor');
  const [narrative, setNarrative] = useState(row.verification_result ?? '');
  const [verificationDate, setVerificationDate] = useState(todayISO());
  const [verdict, setVerdict] = useState<string>(row.verdict ?? row.suggested_verdict ?? '');
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Matured trade with no outcome data (no price history): user MUST pick a verdict
  // because the backend cannot auto-classify without price data.
  const mustPickVerdict = row.is_matured && !row.outcome_pct_preview && !row.suggested_verdict;

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!narrative.trim()) {
      setError(t('pendingVerification.errors.narrativeRequired'));
      return;
    }
    // Enforce manual verdict when no market data is available
    if (mustPickVerdict && !verdict) {
      setError(t('pendingVerification.errors.selectVerdict'));
      return;
    }
    setSubmitting(true);
    setError(null);
    try {
      const body: VerifyTradeBody = {
        verification_result: narrative.trim(),
        verification_date: verificationDate,
        expected_updated_at: row.updated_at,
      };
      if (verdict) {
        body.verdict = verdict as VerifyTradeBody['verdict'];
      }
      const updated = await aiAdvisorVerify.verifyTrade(row.id, body);
      onSuccess(updated);
    } catch (err) {
      const msg = err instanceof Error ? err.message : t('pendingVerification.errors.unknown');
      if (msg.startsWith('stale_updated_at')) {
        setError(t('pendingVerification.errors.rowChanged'));
        onStale();
      } else if (msg.startsWith('conflict')) {
        setError(t('pendingVerification.errors.alreadyVerified'));
      } else {
        setError(msg);
      }
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40" role="dialog" aria-modal="true">
      <div className="bg-white dark:bg-card-dark rounded-xl shadow-xl border border-slate-200 dark:border-border-dark p-6 w-full max-w-lg mx-4">
        <div className="flex items-center justify-between mb-4">
          <h3 className="text-sm font-semibold text-slate-800 dark:text-slate-100">
            {t('pendingVerification.verifyTradeTitle', { asset: row.asset_name ?? row.asset_id, action: row.action })}
          </h3>
          <button
            type="button"
            onClick={onClose}
            className="text-slate-400 hover:text-slate-600 dark:hover:text-slate-300 transition-colors"
            aria-label={t('pendingVerification.close')}
          >
            <span className="material-symbols-outlined !text-[18px]">close</span>
          </button>
        </div>

        {error && (
          <div className="mb-4 p-3 rounded-lg bg-red-50 dark:bg-red-900/20 border border-red-200 dark:border-red-800 text-sm text-red-600 dark:text-red-400">
            {error}
          </div>
        )}

        <form onSubmit={handleSubmit} className="space-y-4">
          <div>
            <label className="block text-xs font-medium text-slate-700 dark:text-slate-300 mb-1">
              {t('pendingVerification.narrativeLabel')}
            </label>
            <textarea
              value={narrative}
              onChange={(e) => setNarrative(e.target.value)}
              placeholder={t('pendingVerification.narrativePlaceholder')}
              rows={4}
              className="w-full rounded-lg border border-slate-200 dark:border-slate-700 bg-slate-50 dark:bg-slate-800 px-3 py-2 text-sm text-slate-800 dark:text-slate-100 placeholder-slate-400 focus:outline-none focus:ring-2 focus:ring-primary/50"
            />
          </div>

          {/* Hint: no price data → user must pick a verdict manually */}
          {mustPickVerdict && (
            <div className="flex items-start gap-2 p-2.5 rounded-lg bg-amber-50 dark:bg-amber-900/20 border border-amber-200 dark:border-amber-700/40 text-xs text-amber-700 dark:text-amber-300">
              <span className="material-symbols-outlined !text-[14px] mt-0.5 shrink-0">info</span>
              <span>
                {t('pendingVerification.noMarketDataHint')}
              </span>
            </div>
          )}

          <div className="grid grid-cols-2 gap-3">
            <div>
              <label className="block text-xs font-medium text-slate-700 dark:text-slate-300 mb-1">
                {t('pendingVerification.verificationDate')}
              </label>
              <input
                type="date"
                value={verificationDate}
                onChange={(e) => setVerificationDate(e.target.value)}
                className="w-full rounded-lg border border-slate-200 dark:border-slate-700 bg-slate-50 dark:bg-slate-800 px-3 py-2 text-sm text-slate-800 dark:text-slate-100 focus:outline-none focus:ring-2 focus:ring-primary/50"
              />
            </div>

            <div>
              <label className="block text-xs font-medium text-slate-700 dark:text-slate-300 mb-1">
                {t('pendingVerification.verdictLabel')}{' '}
                {mustPickVerdict
                  ? t('pendingVerification.verdictRequired')
                  : row.outcome_pct_preview != null || row.suggested_verdict
                  ? t('pendingVerification.verdictOptional')
                  : t('pendingVerification.verdictSelectManually')}
              </label>
              <select
                value={verdict}
                onChange={(e) => setVerdict(e.target.value)}
                className="w-full rounded-lg border border-slate-200 dark:border-slate-700 bg-slate-50 dark:bg-slate-800 px-3 py-2 text-sm text-slate-800 dark:text-slate-100 focus:outline-none focus:ring-2 focus:ring-primary/50"
              >
                {/* Hide "Let backend decide" when backend has no data to auto-classify */}
                {!mustPickVerdict && <option value="">{t('pendingVerification.letBackendDecide')}</option>}
                {mustPickVerdict && <option value="" disabled>{t('pendingVerification.selectAVerdict')}</option>}
                <option value="good_call">✅ {t('pendingVerification.verdict.goodCall')}</option>
                <option value="regret">😖 {t('pendingVerification.verdict.regret')}</option>
                <option value="bullet_dodged">🛡 {t('pendingVerification.verdict.bulletDodged')}</option>
                <option value="missed_opportunity">😩 {t('pendingVerification.verdict.missedOpportunity')}</option>
                <option value="neutral">⚪ {t('pendingVerification.neutralOption')}</option>
              </select>
            </div>
          </div>

          <div className="flex justify-end gap-2 pt-2">
            <button
              type="button"
              onClick={onClose}
              className="px-4 py-2 text-sm rounded-lg border border-slate-200 dark:border-slate-700 text-slate-600 dark:text-slate-300 hover:bg-slate-50 dark:hover:bg-slate-800 transition-colors"
            >
              {t('pendingVerification.cancel')}
            </button>
            <button
              type="submit"
              disabled={submitting || (mustPickVerdict && !verdict)}
              className="px-4 py-2 text-sm rounded-lg bg-primary text-white hover:bg-primary/90 disabled:opacity-50 transition-colors"
            >
              {submitting ? t('pendingVerification.submitting') : t('pendingVerification.submitVerification')}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}

// ── period chip helpers ───────────────────────────────────────────────────────

type PeriodPreset = '30d' | '60d' | '90d' | 'custom';

// ── main component ────────────────────────────────────────────────────────────

export const PendingVerificationList: React.FC = () => {
  const { t } = useTranslation('aiAdvisor');
  const [preset, setPreset] = useState<PeriodPreset>('90d');
  const [customSince, setCustomSince] = useState(sinceISO(90));
  const [customUntil, setCustomUntil] = useState(todayISO());

  const [items, setItems] = useState<PendingVerificationItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [verifyingRow, setVerifyingRow] = useState<PendingVerificationItem | null>(null);
  const [toast, setToast] = useState<string | null>(null);

  const [verifiedItems, setVerifiedItems] = useState<PendingVerificationItem[]>([]);
  const [verifiedLoading, setVerifiedLoading] = useState(false);
  const [verifiedError, setVerifiedError] = useState<string | null>(null);
  const [verifiedHistoryOpen, setVerifiedHistoryOpen] = useState(false);
  const [reopeningId, setReopeningId] = useState<number | null>(null);

  const since = preset === 'custom' ? customSince : sinceISO(parseInt(preset, 10));
  const until = preset === 'custom' ? customUntil : undefined;

  const fetchItems = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const data = await aiAdvisorVerify.listPending(since, until);
      setItems(data.items);
    } catch (err) {
      setError(err instanceof Error ? err.message : t('pendingVerification.errors.loadPending'));
    } finally {
      setLoading(false);
    }
  }, [since, until]);

  const fetchVerifiedItems = useCallback(async () => {
    setVerifiedLoading(true);
    setVerifiedError(null);
    try {
      // Always use a 365-day window independent of the pending list filter.
      // Verified history is for finding old trades to reopen — it must not be
      // date-restricted by the same window used for "needs action" pending trades.
      const data = await aiAdvisorVerify.listPending(sinceISO(365), undefined, 100, 'verified');
      setVerifiedItems(data.items);
    } catch (err) {
      setVerifiedError(err instanceof Error ? err.message : t('pendingVerification.errors.loadVerifiedHistory'));
    } finally {
      setVerifiedLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchItems();
  }, [fetchItems]);

  useEffect(() => {
    if (verifiedHistoryOpen) fetchVerifiedItems();
  }, [verifiedHistoryOpen]); // fetchVerifiedItems is stable (no deps)

  const showToast = (msg: string) => {
    setToast(msg);
    setTimeout(() => setToast(null), 3000);
  };

  const handleSuccess = (updated: PendingVerificationItem) => {
    setVerifyingRow(null);

    if (updated.verification_status === 'verified') {
      // Trade is now fully verified — remove from pending list, auto-expand Verified History
      // so the user can see the trade moved there rather than perceiving it "disappeared."
      setItems((prev) => prev.filter((it) => it.id !== updated.id));
      setVerifiedHistoryOpen(true);
      showToast(t('pendingVerification.toast.verificationSaved'));
    } else {
      // Narrative saved but no verdict yet (status remains 'pending_window').
      // The trade stays in the pending list; let the user know their narrative was saved.
      setItems((prev) =>
        prev.map((it) => (it.id === updated.id ? { ...it, verification_result: updated.verification_result, updated_at: updated.updated_at } : it))
      );
      showToast(t('pendingVerification.toast.narrativeSaved'));
    }

    fetchItems();
    fetchVerifiedItems();
  };

  const handleStale = () => {
    setVerifyingRow(null);
    fetchItems();
  };

  const handleReopen = async (row: PendingVerificationItem) => {
    setReopeningId(row.id);
    try {
      const updated = await aiAdvisorVerify.reopenVerification(row.id, row.updated_at);
      showToast(t('pendingVerification.toast.reopened'));
      fetchVerifiedItems();
      fetchItems();
      // Open VerifyModal immediately. Merge: preserve enriched fields from the original
      // list item (is_matured, outcome_pct_preview, suggested_verdict — absent from the
      // reopen endpoint's truncated response shape) and patch only the fields reopen changes.
      setVerifyingRow({
        ...row,
        verification_status: updated.verification_status,
        verdict: updated.verdict,
        outcome_pct: updated.outcome_pct,
        updated_at: updated.updated_at,
        verification_result: updated.verification_result,
      });
    } catch (err) {
      const msg = err instanceof Error ? err.message : t('pendingVerification.errors.reopenFailed');
      if (msg.startsWith('stale_updated_at')) {
        showToast(t('pendingVerification.toast.rowChanged'));
        fetchVerifiedItems();
      } else {
        showToast(t('pendingVerification.toast.reopenFailedWithMsg', { msg }));
      }
    } finally {
      setReopeningId(null);
    }
  };

  const handleToggleVerifiedHistory = () => {
    setVerifiedHistoryOpen((v) => !v);
  };

  const presets: PeriodPreset[] = ['30d', '60d', '90d', 'custom'];

  return (
    <div className="rounded-xl border border-slate-200 dark:border-border-dark bg-white dark:bg-card-dark p-5">
      {/* Toast */}
      {toast && (
        <div className="fixed top-4 right-4 z-50 px-4 py-3 rounded-lg bg-emerald-50 dark:bg-emerald-900/30 border border-emerald-200 dark:border-emerald-800 text-sm text-emerald-700 dark:text-emerald-300 shadow-lg">
          {toast}
        </div>
      )}

      {/* Header row */}
      <div className="flex items-center gap-3 mb-4 flex-wrap">
        <h3 className="text-sm font-semibold text-slate-800 dark:text-slate-100">
          {t('pendingVerification.tradesPending')}
          <span className="ml-2 text-xs px-2 py-0.5 rounded-full bg-slate-100 dark:bg-slate-800 text-slate-500 font-normal">
            {items.length}
          </span>
        </h3>

        {/* Period chips */}
        <div className="ml-auto flex items-center gap-1 flex-wrap">
          {presets.map((p) => (
            <button
              key={p}
              type="button"
              onClick={() => setPreset(p)}
              className={`px-3 py-1 rounded-full text-xs font-medium transition-colors ${
                preset === p
                  ? 'bg-primary text-white'
                  : 'bg-slate-100 dark:bg-slate-800 text-slate-600 dark:text-slate-300 hover:bg-slate-200 dark:hover:bg-slate-700'
              }`}
            >
              {p === 'custom' ? t('pendingVerification.custom') : p}
            </button>
          ))}
        </div>
      </div>

      {/* Custom date inputs */}
      {preset === 'custom' && (
        <div className="flex items-center gap-3 mb-4 flex-wrap">
          <div className="flex items-center gap-2">
            <label className="text-xs text-slate-500">{t('pendingVerification.from')}</label>
            <input
              type="date"
              value={customSince}
              onChange={(e) => setCustomSince(e.target.value)}
              className="rounded-md border border-slate-200 dark:border-slate-700 bg-slate-50 dark:bg-slate-800 px-2 py-1 text-xs text-slate-800 dark:text-slate-100 focus:outline-none focus:ring-1 focus:ring-primary"
            />
          </div>
          <div className="flex items-center gap-2">
            <label className="text-xs text-slate-500">{t('pendingVerification.to')}</label>
            <input
              type="date"
              value={customUntil}
              onChange={(e) => setCustomUntil(e.target.value)}
              className="rounded-md border border-slate-200 dark:border-slate-700 bg-slate-50 dark:bg-slate-800 px-2 py-1 text-xs text-slate-800 dark:text-slate-100 focus:outline-none focus:ring-1 focus:ring-primary"
            />
          </div>
        </div>
      )}

      {/* Error */}
      {error && (
        <div className="mb-4 p-3 rounded-lg bg-red-50 dark:bg-red-900/20 border border-red-200 dark:border-red-800 text-sm text-red-600 dark:text-red-400">
          {error}
        </div>
      )}

      {/* Legend */}
      <p className="mb-2 text-[10px] text-slate-400 dark:text-slate-500">
        {t('pendingVerification.legend')}
      </p>

      {/* Table */}
      <div className="overflow-x-auto">
        <table className="w-full text-xs">
          <thead>
            <tr className="border-b border-slate-100 dark:border-slate-700 text-left">
              <th className="px-3 py-2 text-slate-500 dark:text-slate-400 font-medium">{t('pendingVerification.cols.date')}</th>
              <th className="px-3 py-2 text-slate-500 dark:text-slate-400 font-medium">{t('pendingVerification.cols.asset')}</th>
              <th className="px-3 py-2 text-slate-500 dark:text-slate-400 font-medium">{t('pendingVerification.cols.action')}</th>
              <th className="px-3 py-2 text-slate-500 dark:text-slate-400 font-medium">{t('pendingVerification.cols.qty')}</th>
              <th className="px-3 py-2 text-slate-500 dark:text-slate-400 font-medium">{t('pendingVerification.cols.price')}</th>
              <th className="px-3 py-2 text-slate-500 dark:text-slate-400 font-medium">{t('pendingVerification.cols.reasonLinkedInsight')}</th>
              <th className="px-3 py-2 text-slate-500 dark:text-slate-400 font-medium">{t('pendingVerification.cols.outcome')}</th>
              <th className="px-3 py-2 text-slate-500 dark:text-slate-400 font-medium">{t('pendingVerification.cols.suggestedVerdict')}</th>
              <th className="px-3 py-2 text-slate-500 dark:text-slate-400 font-medium">{t('pendingVerification.cols.maturity')}</th>
              <th className="px-3 py-2 text-slate-500 dark:text-slate-400 font-medium">{t('pendingVerification.cols.action')}</th>
            </tr>
          </thead>
          <tbody>
            {loading
              ? Array.from({ length: 3 }).map((_, i) => <SkeletonRow key={i} />)
              : items.length === 0
              ? (
                <tr>
                  <td colSpan={10} className="py-12 text-center">
                    <div className="flex flex-col items-center gap-2">
                      <span className="material-symbols-outlined !text-[36px] text-slate-300 dark:text-slate-600">
                        check_circle
                      </span>
                      <p className="text-slate-400 text-sm">
                        {t('pendingVerification.noTradesNeedVerification')}{' '}
                        {preset !== '90d' && (
                          <button
                            type="button"
                            className="text-primary underline"
                            onClick={() => setPreset('90d')}
                          >
                            {t('pendingVerification.try90dWindow')}
                          </button>
                        )}
                      </p>
                    </div>
                  </td>
                </tr>
              )
              : items.map((row) => (
                <tr
                  key={row.id}
                  className="border-b border-slate-50 dark:border-slate-800 hover:bg-slate-50 dark:hover:bg-slate-800/50 transition-colors"
                >
                  <td className="px-3 py-3 text-slate-600 dark:text-slate-400 whitespace-nowrap">{row.log_date}</td>
                  <td className="px-3 py-3">
                    <span className="font-medium text-slate-800 dark:text-slate-100">
                      {row.asset_name ?? row.asset_id}
                    </span>
                    {row.asset_name && (
                      <span className="block text-[10px] text-slate-400">{row.asset_id}</span>
                    )}
                  </td>
                  <td className="px-3 py-3">
                    <span className={`px-2 py-0.5 rounded-full font-medium ${
                      row.action.toLowerCase() === 'buy'
                        ? 'bg-emerald-100 text-emerald-700 dark:bg-emerald-900/30 dark:text-emerald-300'
                        : 'bg-red-100 text-red-700 dark:bg-red-900/30 dark:text-red-300'
                    }`}>
                      {row.action}
                    </span>
                  </td>
                  <td className="px-3 py-3 text-slate-600 dark:text-slate-400 font-mono">
                    {row.quantity != null ? row.quantity.toLocaleString() : '—'}
                  </td>
                  <td className="px-3 py-3 text-slate-600 dark:text-slate-400 font-mono">
                    {row.price != null ? row.price.toLocaleString() : '—'}
                  </td>
                  <td className="px-3 py-3 max-w-[200px]">
                    {row.linked_insight_title ? (
                      <div>
                        {row.decision_reason && (
                          <p className="text-slate-500 dark:text-slate-400 mb-1 truncate" title={row.decision_reason}>
                            {row.decision_reason}
                          </p>
                        )}
                        <a
                          href="#insights"
                          className="text-primary underline text-[10px]"
                          title={t('pendingVerification.insightNumber', { id: row.linked_insight_id })}
                        >
                          {row.linked_insight_title}
                        </a>
                      </div>
                    ) : (
                      <span className="text-slate-500 dark:text-slate-400 truncate block" title={row.decision_reason ?? ''}>
                        {row.decision_reason ?? '—'}
                      </span>
                    )}
                  </td>
                  <td className="px-3 py-3">
                    <OutcomeCell
                      isMatured={row.is_matured}
                      logDate={row.log_date}
                      outcomePct={row.outcome_pct_preview}
                      outcomeToDatePct={row.outcome_to_date_pct}
                      outcomeToDateAsof={row.outcome_to_date_asof}
                    />
                  </td>
                  <td className="px-3 py-3">
                    <VerdictChip verdict={row.verdict ?? row.suggested_verdict} isMatured={row.is_matured} outcomePct={row.outcome_pct_preview} />
                  </td>
                  <td className="px-3 py-3">
                    <MaturityBadge isMatured={row.is_matured} logDate={row.log_date} />
                  </td>
                  <td className="px-3 py-3">
                    <button
                      type="button"
                      onClick={() => setVerifyingRow(row)}
                      className="px-3 py-1 rounded-lg text-xs font-medium bg-primary/10 text-primary hover:bg-primary/20 transition-colors whitespace-nowrap"
                    >
                      {t('pendingVerification.verify')}
                    </button>
                  </td>
                </tr>
              ))
            }
          </tbody>
        </table>
      </div>

      {/* Verified history — collapsed by default */}
      <div className="mt-6 border-t border-slate-100 dark:border-slate-800 pt-4">
        <button
          type="button"
          onClick={handleToggleVerifiedHistory}
          className="flex items-center gap-1.5 text-xs font-medium text-slate-500 hover:text-slate-700 dark:hover:text-slate-300 transition-colors"
        >
          <span className="material-symbols-outlined !text-[16px]">
            {verifiedHistoryOpen ? 'expand_less' : 'expand_more'}
          </span>
          {t('pendingVerification.verifiedHistory')}
          {verifiedItems.length > 0 && (
            <span className="ml-1 px-1.5 py-0.5 rounded-full bg-slate-100 dark:bg-slate-800 text-slate-500 text-[10px]">
              {verifiedItems.length}
            </span>
          )}
        </button>

        {verifiedHistoryOpen && (
          <div className="mt-3">
            {verifiedError && (
              <div className="mb-3 p-2 rounded-lg bg-red-50 dark:bg-red-900/20 border border-red-200 dark:border-red-800 text-xs text-red-600 dark:text-red-400">
                {verifiedError}
              </div>
            )}
            <div className="overflow-x-auto">
              <table className="w-full text-xs">
                <thead>
                  <tr className="border-b border-slate-100 dark:border-slate-700 text-left">
                    <th className="px-3 py-2 text-slate-400 font-medium">{t('pendingVerification.cols.date')}</th>
                    <th className="px-3 py-2 text-slate-400 font-medium">{t('pendingVerification.cols.asset')}</th>
                    <th className="px-3 py-2 text-slate-400 font-medium">{t('pendingVerification.cols.action')}</th>
                    <th className="px-3 py-2 text-slate-400 font-medium">{t('pendingVerification.cols.verdict')}</th>
                    <th className="px-3 py-2 text-slate-400 font-medium">{t('pendingVerification.cols.outcome')}</th>
                    <th className="px-3 py-2 text-slate-400 font-medium">{t('pendingVerification.cols.status')}</th>
                    <th className="px-3 py-2 text-slate-400 font-medium">{t('pendingVerification.cols.action')}</th>
                  </tr>
                </thead>
                <tbody>
                  {verifiedLoading ? (
                    Array.from({ length: 3 }).map((_, i) => <SkeletonRow key={i} />)
                  ) : verifiedItems.length === 0 ? (
                    <tr>
                      <td colSpan={7} className="py-6 text-center text-slate-400 text-xs">
                        {t('pendingVerification.noVerifiedTrades')}
                      </td>
                    </tr>
                  ) : verifiedItems.map((row) => (
                    <tr
                      key={row.id}
                      className="border-b border-slate-50 dark:border-slate-800 hover:bg-slate-50 dark:hover:bg-slate-800/50 transition-colors"
                    >
                      <td className="px-3 py-2.5 text-slate-500 dark:text-slate-400 whitespace-nowrap">{row.log_date}</td>
                      <td className="px-3 py-2.5">
                        <span className="font-medium text-slate-700 dark:text-slate-200">{row.asset_name ?? row.asset_id}</span>
                        {row.asset_name && <span className="block text-[10px] text-slate-400">{row.asset_id}</span>}
                      </td>
                      <td className="px-3 py-2.5">
                        <span className={`px-2 py-0.5 rounded-full font-medium ${
                          row.action.toLowerCase() === 'buy'
                            ? 'bg-emerald-100 text-emerald-700 dark:bg-emerald-900/30 dark:text-emerald-300'
                            : 'bg-red-100 text-red-700 dark:bg-red-900/30 dark:text-red-300'
                        }`}>
                          {row.action}
                        </span>
                      </td>
                      <td className="px-3 py-2.5">
                        <span className="inline-flex items-center gap-1 flex-wrap">
                          <VerdictChip verdict={row.verdict} />
                          {row.verification_result?.startsWith(AUTO_VERIFICATION_PREFIX) && (
                            <span className="text-[10px] px-1.5 py-0.5 rounded-full bg-slate-100 dark:bg-slate-700 text-slate-400 dark:text-slate-500 font-medium whitespace-nowrap">
                              {t('pendingVerification.auto')}
                            </span>
                          )}
                        </span>
                      </td>
                      <td className="px-3 py-2.5 font-mono">
                        {row.outcome_pct != null ? (
                          <span className={row.outcome_pct >= 0 ? 'text-emerald-600 dark:text-emerald-400' : 'text-red-600 dark:text-red-400'}>
                            {row.outcome_pct >= 0 ? '+' : ''}{row.outcome_pct.toFixed(2)}%
                          </span>
                        ) : <span className="text-slate-400">—</span>}
                      </td>
                      <td className="px-3 py-2.5">
                        <span className={`px-2 py-0.5 rounded-full text-[10px] font-medium ${
                          row.verification_status === 'verified'
                            ? 'bg-emerald-100 text-emerald-700 dark:bg-emerald-900/30 dark:text-emerald-300'
                            : 'bg-orange-100 text-orange-700 dark:bg-orange-900/30 dark:text-orange-300'
                        }`}>
                          {verifiedStatusLabel(row.verification_status, t)}
                        </span>
                      </td>
                      <td className="px-3 py-2.5">
                        <button
                          type="button"
                          disabled={reopeningId === row.id}
                          onClick={() => handleReopen(row)}
                          className="px-3 py-1 rounded-lg text-xs font-medium bg-slate-100 dark:bg-slate-700 text-slate-600 dark:text-slate-300 hover:bg-slate-200 dark:hover:bg-slate-600 disabled:opacity-50 transition-colors whitespace-nowrap"
                        >
                          {reopeningId === row.id ? t('pendingVerification.reopening') : t('pendingVerification.reopen')}
                        </button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        )}
      </div>

      {/* Verify modal */}
      {verifyingRow && (
        <VerifyModal
          row={verifyingRow}
          onClose={() => setVerifyingRow(null)}
          onSuccess={handleSuccess}
          onStale={handleStale}
        />
      )}
    </div>
  );
};
