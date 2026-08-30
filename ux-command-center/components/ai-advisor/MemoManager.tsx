import React, { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import type { TFunction } from 'i18next';
import type { MemoProposal, StrategyMemo } from '../../src/services/api';
import { aiAdvisorVerify, api } from '../../src/services/api';

const BIAS_COLORS: Record<string, string> = {
  defensive: 'bg-blue-100 text-blue-700 dark:bg-blue-900/30 dark:text-blue-300',
  offensive: 'bg-red-100 text-red-700 dark:bg-red-900/30 dark:text-red-300',
  neutral: 'bg-slate-100 text-slate-600 dark:bg-slate-800 dark:text-slate-300',
};

function biasLabel(bias: string, t: TFunction): string {
  switch (bias) {
    case 'defensive': return t('memoManager.bias.defensive');
    case 'offensive': return t('memoManager.bias.offensive');
    default: return t('memoManager.bias.neutral');
  }
}

const slugifyMemoTitle = (title: string) => {
  const normalized = title
    .toLowerCase()
    .trim()
    .replace(/[\s_]+/g, '-')
    .replace(/[^\p{L}\p{N}-]+/gu, '')
    .replace(/-+/g, '-')
    .replace(/^-+|-+$/g, '');

  return (normalized.slice(0, 60).replace(/-+$/g, '') || 'memo');
};

const buildMemoFilename = (memo: StrategyMemo) => `${memo.date}-${slugifyMemoTitle(memo.title)}.md`;

function normalizeContent(raw: string): string {
  return raw
    .replace(/([一二三四五六七八九十百]、)/g, '\n\n$1')
    .replace(/(【[^】]{1,20}】)/g, '\n\n$1')
    .replace(/([⚠🚨])/gu, '\n\n$1')
    .replace(/\n{3,}/g, '\n\n')
    .trim();
}

// ── Memo-update proposal modal ────────────────────────────────────────────────

interface MemoProposalModalProps {
  memo: StrategyMemo;
  onClose: () => void;
  onApplied: (memoId: number) => void;
}

function MemoProposalModal({ memo, onClose, onApplied }: MemoProposalModalProps) {
  const { t } = useTranslation('aiAdvisor');
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [proposals, setProposals] = useState<MemoProposal[]>([]);
  const [accepted, setAccepted] = useState<Set<number>>(new Set());
  const [applying, setApplying] = useState(false);
  const [applyError, setApplyError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    aiAdvisorVerify.proposeMemoUpdates(memo.id).then((result) => {
      if (!cancelled) {
        setProposals(result.proposals);
        setLoading(false);
      }
    }).catch((err) => {
      if (!cancelled) {
        setError(err instanceof Error ? err.message : t('memoManager.errors.generateProposalsFailed'));
        setLoading(false);
      }
    });
    return () => { cancelled = true; };
  }, [memo.id]);

  const toggleAccept = (idx: number) => {
    setAccepted((prev) => {
      const next = new Set(prev);
      if (next.has(idx)) next.delete(idx);
      else next.add(idx);
      return next;
    });
  };

  const handleApply = async () => {
    const acceptedProposals = proposals.filter((_, i) => accepted.has(i));
    if (acceptedProposals.length === 0) return;

    let newContent = memo.content ?? '';
    for (const p of acceptedProposals) {
      if (!p.current_text || p.current_text === 'N/A') {
        newContent = newContent + '\n\n' + p.proposed_text;
      } else {
        newContent = newContent.replace(p.current_text, p.proposed_text);
      }
    }

    setApplying(true);
    setApplyError(null);
    try {
      await api.updateStrategyMemo(memo.id, { content: newContent });
      onApplied(memo.id);
    } catch (err) {
      setApplyError(err instanceof Error ? err.message : t('memoManager.errors.applyFailed'));
    } finally {
      setApplying(false);
    }
  };

  const acceptedCount = accepted.size;

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4" role="dialog" aria-modal="true">
      <div className="bg-white dark:bg-card-dark rounded-xl shadow-xl border border-slate-200 dark:border-border-dark w-full max-w-2xl max-h-[90vh] flex flex-col">
        {/* Header */}
        <div className="flex items-center justify-between px-5 py-4 border-b border-slate-100 dark:border-slate-700 shrink-0">
          <div>
            <h3 className="text-sm font-semibold text-slate-800 dark:text-slate-100">{t('memoManager.proposalModal.title')}</h3>
            <p className="text-xs text-slate-400 mt-0.5">{t('memoManager.proposalModal.subtitle')}</p>
          </div>
          <button type="button" onClick={onClose} className="text-slate-400 hover:text-slate-600 dark:hover:text-slate-300 transition-colors">
            <span className="material-symbols-outlined !text-[18px]">close</span>
          </button>
        </div>

        {/* Body */}
        <div className="flex-1 overflow-y-auto px-5 py-4 space-y-4">
          {loading && (
            <div className="flex items-center justify-center py-12 gap-2 text-slate-400">
              <span className="material-symbols-outlined !text-[20px] animate-spin">progress_activity</span>
              <span className="text-sm">{t('memoManager.proposalModal.generating')}</span>
            </div>
          )}
          {error && (
            <div className="p-3 rounded-lg bg-red-50 dark:bg-red-900/20 border border-red-200 dark:border-red-800 text-sm text-red-600 dark:text-red-400">
              {error}
            </div>
          )}
          {!loading && !error && proposals.length === 0 && (
            <div className="flex flex-col items-center py-10 text-center gap-2">
              <span className="material-symbols-outlined !text-[32px] text-slate-300 dark:text-slate-600">check_circle</span>
              <p className="text-sm text-slate-400">{t('memoManager.proposalModal.noChanges')}</p>
            </div>
          )}
          {proposals.map((p, idx) => {
            const isAccepted = accepted.has(idx);
            return (
              <div
                key={idx}
                className={`rounded-lg border p-4 transition-colors ${
                  isAccepted
                    ? 'border-primary/40 bg-primary/5 dark:bg-primary/10'
                    : 'border-slate-200 dark:border-slate-700 bg-slate-50 dark:bg-slate-800/40'
                }`}
              >
                <div className="flex items-start justify-between gap-3 mb-3">
                  <span className="text-[10px] font-semibold uppercase tracking-wide px-2 py-0.5 rounded-full bg-slate-200 dark:bg-slate-700 text-slate-600 dark:text-slate-300">
                    {p.section}
                  </span>
                  <button
                    type="button"
                    onClick={() => toggleAccept(idx)}
                    className={`shrink-0 px-3 py-1 rounded-lg text-xs font-medium transition-colors ${
                      isAccepted
                        ? 'bg-primary text-white hover:bg-primary/90'
                        : 'bg-slate-200 dark:bg-slate-700 text-slate-600 dark:text-slate-300 hover:bg-slate-300 dark:hover:bg-slate-600'
                    }`}
                  >
                    {isAccepted ? t('memoManager.proposalModal.accepted') : t('memoManager.proposalModal.accept')}
                  </button>
                </div>

                {p.current_text && p.current_text !== 'N/A' && (
                  <div className="mb-2">
                    <p className="text-[10px] text-slate-400 mb-1 uppercase tracking-wide">{t('memoManager.proposalModal.current')}</p>
                    <p className="text-xs text-slate-500 dark:text-slate-400 bg-red-50 dark:bg-red-900/10 rounded px-2 py-1.5 line-through decoration-red-400">
                      {p.current_text}
                    </p>
                  </div>
                )}
                <div className="mb-2">
                  <p className="text-[10px] text-slate-400 mb-1 uppercase tracking-wide">{t('memoManager.proposalModal.proposed')}</p>
                  <p className="text-xs text-slate-700 dark:text-slate-300 bg-emerald-50 dark:bg-emerald-900/10 rounded px-2 py-1.5">
                    {p.proposed_text}
                  </p>
                </div>
                <p className="text-[11px] text-slate-400 italic">{t('memoManager.proposalModal.rationale', { text: p.rationale })}</p>
              </div>
            );
          })}
        </div>

        {/* Footer */}
        {!loading && !error && proposals.length > 0 && (
          <div className="px-5 py-4 border-t border-slate-100 dark:border-slate-700 shrink-0">
            {applyError && (
              <p className="text-xs text-red-600 dark:text-red-400 mb-2">{applyError}</p>
            )}
            <div className="flex justify-end gap-2">
              <button
                type="button"
                onClick={onClose}
                className="px-4 py-2 text-sm rounded-lg border border-slate-200 dark:border-slate-700 text-slate-600 dark:text-slate-300 hover:bg-slate-50 dark:hover:bg-slate-800 transition-colors"
              >
                {t('memoManager.cancel')}
              </button>
              <button
                type="button"
                disabled={applying || acceptedCount === 0}
                onClick={handleApply}
                className="px-4 py-2 text-sm rounded-lg bg-primary text-white hover:bg-primary/90 disabled:opacity-50 transition-colors"
              >
                {applying ? t('memoManager.proposalModal.applying') : t('memoManager.proposalModal.applyAccepted', { count: acceptedCount })}
              </button>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}

// ── main component ────────────────────────────────────────────────────────────

export const MemoManager: React.FC = () => {
  const { t } = useTranslation('aiAdvisor');
  const [memos, setMemos] = useState<StrategyMemo[]>([]);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [savingEdit, setSavingEdit] = useState(false);
  const [importing, setImporting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [importError, setImportError] = useState<string | null>(null);

  const [content, setContent] = useState('');
  const [memoDate, setMemoDate] = useState('');
  const [expandedId, setExpandedId] = useState<number | null>(null);
  const [editingId, setEditingId] = useState<number | null>(null);
  const [editTitle, setEditTitle] = useState('');
  const [editContent, setEditContent] = useState('');
  const [importBannerDismissed, setImportBannerDismissed] = useState(false);
  const [proposingMemo, setProposingMemo] = useState<StrategyMemo | null>(null);
  const [toast, setToast] = useState<string | null>(null);

  const showToast = (msg: string) => {
    setToast(msg);
    setTimeout(() => setToast(null), 4000);
  };

  const missingContentCount = memos.filter((memo) => !memo.content).length;

  const fetchMemos = async () => {
    try {
      const result = await api.getStrategyMemos(true);
      setMemos(result.memos);
      setError(null);
      if (!result.memos.some((memo) => !memo.content)) {
        setImportBannerDismissed(false);
        setImportError(null);
      }
    } catch (e) {
      console.error('Failed to fetch memos', e);
      setError(t('memoManager.errors.loadMemos'));
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchMemos();
  }, []);

  const handleSave = async () => {
    if (!content.trim()) return;
    setSaving(true);
    setError(null);
    try {
      await api.createStrategyMemo(normalizeContent(content), memoDate || undefined);
      setContent('');
      setMemoDate('');
      await fetchMemos();
    } catch (e) {
      console.error('Failed to save memo', e);
      setError(e instanceof Error ? e.message : t('memoManager.errors.saveMemo'));
    } finally {
      setSaving(false);
    }
  };

  const handleDelete = async (e: React.MouseEvent, id: number) => {
    e.stopPropagation();
    if (!window.confirm(t('memoManager.confirmDelete'))) return;
    setError(null);
    try {
      await api.deleteStrategyMemo(id);
      if (expandedId === id) {
        setExpandedId(null);
      }
      if (editingId === id) {
        setEditingId(null);
      }
      await fetchMemos();
    } catch (e) {
      console.error('Failed to delete memo', e);
      setError(e instanceof Error ? e.message : t('memoManager.errors.deleteMemo'));
    }
  };

  const handleDownload = (e: React.MouseEvent, memo: StrategyMemo) => {
    e.stopPropagation();
    if (!memo.content) return;

    const blob = new Blob([memo.content], { type: 'text/markdown' });
    const url = URL.createObjectURL(blob);
    const anchor = document.createElement('a');
    anchor.href = url;
    anchor.download = buildMemoFilename(memo);
    document.body.appendChild(anchor);
    anchor.click();
    document.body.removeChild(anchor);
    URL.revokeObjectURL(url);
  };

  const handleImportFromFiles = async () => {
    setImporting(true);
    setImportError(null);
    try {
      await api.importMemosFromFiles();
      setImportBannerDismissed(true);
      await fetchMemos();
    } catch (e) {
      console.error('Failed to import memos from files', e);
      setImportError(e instanceof Error ? e.message : t('memoManager.errors.importFromFiles'));
    } finally {
      setImporting(false);
    }
  };

  const handleStartEdit = (e: React.MouseEvent, memo: StrategyMemo) => {
    e.stopPropagation();
    setExpandedId(memo.id);
    setEditingId(memo.id);
    setEditTitle(memo.title);
    setEditContent(memo.content ?? '');
  };

  const handleCancelEdit = (e: React.MouseEvent) => {
    e.stopPropagation();
    setEditingId(null);
    setEditTitle('');
    setEditContent('');
  };

  const handleSaveEdit = async (e: React.MouseEvent, memo: StrategyMemo) => {
    e.stopPropagation();
    const trimmedTitle = editTitle.trim();
    if (!trimmedTitle) return;

    const updates: { title: string; content?: string } = { title: trimmedTitle };
    const trimmedContent = editContent.trim();
    if (memo.content !== null && memo.content !== undefined) {
      updates.content = editContent;
    } else if (trimmedContent) {
      updates.content = editContent;
    }

    setSavingEdit(true);
    setError(null);
    try {
      await api.updateStrategyMemo(memo.id, updates);
      setEditingId(null);
      setExpandedId(null);
      setEditTitle('');
      setEditContent('');
      await fetchMemos();
    } catch (e) {
      console.error('Failed to update memo', e);
      setError(e instanceof Error ? e.message : t('memoManager.errors.updateMemo'));
    } finally {
      setSavingEdit(false);
    }
  };

  return (
    <div className="rounded-xl border border-slate-200 dark:border-border-dark bg-white dark:bg-card-dark p-5">
      {toast && (
        <div className="fixed top-4 right-4 z-50 px-4 py-3 rounded-lg bg-emerald-50 dark:bg-emerald-900/30 border border-emerald-200 dark:border-emerald-800 text-sm text-emerald-700 dark:text-emerald-300 shadow-lg">
          {toast}
        </div>
      )}
      {error && (
        <div className="mb-4 flex items-center gap-2 rounded-lg border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-600 dark:border-red-800 dark:bg-red-900/20 dark:text-red-400">
          <span className="material-symbols-outlined !text-[16px]">error</span>
          {error}
        </div>
      )}

      <div className="mb-8">
        <h3 className="mb-3 text-sm font-semibold text-slate-800 dark:text-slate-100">{t('memoManager.recordNew')}</h3>
        <div className="space-y-3">
          <textarea
            value={content}
            onChange={(e) => setContent(e.target.value)}
            placeholder={t('memoManager.pastePlaceholder')}
            rows={5}
            className="w-full rounded-md border border-slate-200 bg-white px-3 py-2 text-sm text-slate-800 placeholder-slate-400 focus:outline-none focus:ring-2 focus:ring-primary/50 dark:border-slate-700 dark:bg-slate-800 dark:text-slate-200"
          />
          <div className="flex items-center gap-3">
            <div className="flex flex-col">
              <label className="mb-1 text-[10px] text-slate-500">{t('memoManager.dateOptional')}</label>
              <input
                type="date"
                value={memoDate}
                onChange={(e) => setMemoDate(e.target.value)}
                className="rounded-md border border-slate-200 bg-white px-2 py-1.5 text-sm text-slate-800 focus:outline-none focus:ring-2 focus:ring-primary/50 dark:border-slate-700 dark:bg-slate-800 dark:text-slate-200"
              />
            </div>
            <button
              onClick={handleSave}
              disabled={saving || !content.trim()}
              className="mt-4 rounded-md bg-primary px-4 py-1.5 text-sm font-medium text-white transition-colors hover:bg-primary/90 disabled:opacity-50"
            >
              {saving ? t('memoManager.saving') : t('memoManager.saveMemo')}
            </button>
          </div>
        </div>
      </div>

      <hr className="mb-6 border-slate-100 dark:border-slate-800" />

      <h3 className="mb-4 text-sm font-semibold text-slate-800 dark:text-slate-100">{t('memoManager.strategyMemos')}</h3>

      {missingContentCount > 0 && !importBannerDismissed && (
        <div className="mb-4 rounded-lg border border-amber-200 bg-amber-50 p-3 text-sm text-amber-800 dark:border-amber-800 dark:bg-amber-900/20 dark:text-amber-200">
          <div className="flex items-start justify-between gap-3">
            <div className="flex items-start gap-2">
              <span className="material-symbols-outlined !text-[16px]">warning</span>
              <div>
                <p>{t('memoManager.importBanner.missingContent', { count: missingContentCount })}</p>
                <div className="mt-2 flex items-center gap-2">
                  <button
                    type="button"
                    onClick={handleImportFromFiles}
                    disabled={importing}
                    className="rounded-md border border-amber-300 px-3 py-1 text-xs font-medium transition-colors hover:bg-amber-100 disabled:cursor-not-allowed disabled:opacity-60 dark:border-amber-700 dark:hover:bg-amber-900/30"
                  >
                    {importing ? t('memoManager.importing') : t('memoManager.importFromFiles')}
                  </button>
                </div>
                {importError && <p className="mt-2 text-xs text-red-600 dark:text-red-300">{importError}</p>}
              </div>
            </div>
            <button
              type="button"
              onClick={() => setImportBannerDismissed(true)}
              className="text-amber-700 transition-colors hover:text-amber-900 dark:text-amber-200 dark:hover:text-white"
              aria-label={t('memoManager.dismissImportBanner')}
            >
              <span className="material-symbols-outlined !text-[18px]">close</span>
            </button>
          </div>
        </div>
      )}

      {loading ? (
        <div className="text-sm text-slate-500">{t('memoManager.loadingMemos')}</div>
      ) : memos.length === 0 ? (
        <div className="flex flex-col items-center justify-center py-10 text-center">
          <span className="material-symbols-outlined mb-3 !text-[36px] text-slate-300 dark:text-slate-600">
            article
          </span>
          <p className="text-sm text-slate-400">{t('memoManager.noMemosFound')}</p>
        </div>
      ) : (
        <div className="space-y-3">
          {memos.map((memo) => {
            const isEditing = editingId === memo.id;
            const downloadTitle = memo.content
              ? t('memoManager.downloadMd')
              : t('memoManager.noContentStored');

            return (
              <div
                key={memo.id}
                onClick={() => setExpandedId(expandedId === memo.id ? null : memo.id)}
                className="cursor-pointer rounded-lg border border-slate-100 p-4 transition-colors hover:bg-slate-50 dark:border-slate-700 dark:hover:bg-slate-800/40"
              >
                <div className="mb-2 flex items-start gap-2">
                  <span className="line-clamp-2 min-w-0 flex-1 overflow-hidden text-sm font-medium text-slate-800 dark:text-slate-100">
                    {memo.title}
                  </span>
                  <span className={`shrink-0 rounded-full px-2 py-0.5 text-[10px] font-medium ${BIAS_COLORS[memo.bias] || BIAS_COLORS.neutral}`}>
                    {biasLabel(memo.bias, t)}
                  </span>
                </div>

                <div className="mt-2 flex items-center justify-between text-xs text-slate-400">
                  <span>{memo.date}</span>
                  <div className="flex items-center gap-2">
                    <button
                      type="button"
                      onClick={(e) => { e.stopPropagation(); setProposingMemo(memo); }}
                      disabled={!memo.content}
                      className={`transition-colors ${memo.content ? 'hover:text-primary dark:hover:text-primary' : 'cursor-not-allowed opacity-50'}`}
                      title={memo.content ? t('memoManager.proposeUpdates') : t('memoManager.importContentFirst')}
                    >
                      <span className="material-symbols-outlined !text-[14px]">auto_fix_high</span>
                    </button>
                    <button
                      type="button"
                      onClick={(e) => handleStartEdit(e, memo)}
                      className="transition-colors hover:text-slate-600 dark:hover:text-slate-200"
                      title={t('memoManager.editMemo')}
                    >
                      <span className="material-symbols-outlined !text-[14px]">edit</span>
                    </button>
                    <button
                      type="button"
                      onClick={(e) => handleDownload(e, memo)}
                      disabled={!memo.content}
                      className={`transition-colors ${memo.content ? 'hover:text-slate-600 dark:hover:text-slate-200' : 'cursor-not-allowed opacity-50'}`}
                      title={downloadTitle}
                    >
                      <span className="material-symbols-outlined !text-[14px]">download</span>
                    </button>
                    <button
                      type="button"
                      onClick={(e) => handleDelete(e, memo.id)}
                      className="transition-colors hover:text-red-500"
                      title={t('memoManager.deleteMemo')}
                    >
                      <span className="material-symbols-outlined !text-[14px]">delete</span>
                    </button>
                  </div>
                </div>

                {expandedId === memo.id && (
                  <div
                    className="mt-4 border-t border-slate-100 pt-4 dark:border-slate-700"
                    data-testid={`memo-content-${memo.id}`}
                  >
                    {isEditing ? (
                      <div
                        className="space-y-3"
                        onClick={(e) => e.stopPropagation()}
                      >
                        <input
                          type="text"
                          aria-label={t('memoManager.memoTitleLabel')}
                          value={editTitle}
                          onChange={(e) => setEditTitle(e.target.value)}
                          className="w-full rounded-md border border-slate-200 bg-white px-3 py-2 text-sm text-slate-800 focus:outline-none focus:ring-2 focus:ring-primary/50 dark:border-slate-700 dark:bg-slate-800 dark:text-slate-200"
                        />
                        <textarea
                          rows={8}
                          aria-label={t('memoManager.memoContentLabel')}
                          value={editContent}
                          onChange={(e) => setEditContent(e.target.value)}
                          placeholder={t('memoManager.editContentPlaceholder')}
                          className="w-full rounded-md border border-slate-200 bg-white px-3 py-2 text-sm text-slate-800 focus:outline-none focus:ring-2 focus:ring-primary/50 dark:border-slate-700 dark:bg-slate-800 dark:text-slate-200"
                        />
                        <div className="flex items-center justify-end gap-2">
                          <button
                            type="button"
                            onClick={handleCancelEdit}
                            className="rounded-md border border-slate-200 px-3 py-1.5 text-xs font-medium text-slate-600 transition-colors hover:bg-slate-50 dark:border-slate-700 dark:text-slate-300 dark:hover:bg-slate-800"
                          >
                            {t('memoManager.cancel')}
                          </button>
                          <button
                            type="button"
                            onClick={(e) => handleSaveEdit(e, memo)}
                            disabled={savingEdit || !editTitle.trim()}
                            className="rounded-md bg-primary px-3 py-1.5 text-xs font-medium text-white transition-colors hover:bg-primary/90 disabled:cursor-not-allowed disabled:opacity-50"
                          >
                            {savingEdit ? t('memoManager.saving') : t('memoManager.saveChanges')}
                          </button>
                        </div>
                      </div>
                    ) : memo.content ? (
                      <div className="whitespace-pre-wrap break-words text-xs text-slate-600 dark:text-slate-400">
                        {memo.content}
                      </div>
                    ) : (
                      <div className="space-y-2">
                        {memo.directives.length > 0 && (
                          <ul className="list-inside list-disc space-y-1 text-xs text-slate-600 dark:text-slate-400">
                            {memo.directives.map((directive, index) => (
                              <li key={index}>{directive}</li>
                            ))}
                          </ul>
                        )}
                        <p className="mt-2 text-xs italic text-slate-400">
                          {t('memoManager.fullContentNotAvailable')}
                        </p>
                      </div>
                    )}
                  </div>
                )}
              </div>
            );
          })}
        </div>
      )}
      {proposingMemo && (
        <MemoProposalModal
          memo={proposingMemo}
          onClose={() => setProposingMemo(null)}
          onApplied={(memoId) => {
            setProposingMemo(null);
            setExpandedId(memoId);
            fetchMemos();
            showToast(t('memoManager.toastUpdated'));
          }}
        />
      )}
    </div>
  );
};
