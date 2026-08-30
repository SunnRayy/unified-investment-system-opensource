import React, { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { SettingsAPI } from '../../src/services/api';

interface PromptPreviewModalProps {
  promptType: 'brief' | 'review' | 'review_questions';
  sharedPersonaDraft: string | null;
  instructionsDraft: string | null;
  /** When set, skip the API call and show this before/after directly. */
  directCurrent?: string;
  directProposed?: string;
  directTitle?: string;
  onClose: () => void;
}

function computeLineDiff(current: string, proposed: string) {
  const currentLines = current.split('\n');
  const proposedLines = proposed.split('\n');
  const maxLen = Math.max(currentLines.length, proposedLines.length);
  const result: Array<{ current: string | null; proposed: string | null; changed: boolean }> = [];
  for (let i = 0; i < maxLen; i++) {
    const c = i < currentLines.length ? currentLines[i] : null;
    const p = i < proposedLines.length ? proposedLines[i] : null;
    result.push({ current: c, proposed: p, changed: c !== p });
  }
  return result;
}

export const PromptPreviewModal: React.FC<PromptPreviewModalProps> = ({
  promptType, sharedPersonaDraft, instructionsDraft,
  directCurrent, directProposed, directTitle,
  onClose,
}) => {
  const { t } = useTranslation('system');
  const isDirect = directCurrent !== undefined && directProposed !== undefined;
  const [loading, setLoading] = useState(!isDirect);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<{ composed_prompt: string; current_prompt: string; prompt_hash: string } | null>(
    isDirect ? { composed_prompt: directProposed!, current_prompt: directCurrent!, prompt_hash: '' } : null
  );

  useEffect(() => {
    if (isDirect) return;
    setLoading(true);
    setError(null);
    SettingsAPI.previewPrompt(promptType, sharedPersonaDraft, instructionsDraft)
      .then(r => { setResult(r); setLoading(false); })
      .catch(e => { setError(e.message); setLoading(false); });
  }, [isDirect, promptType, sharedPersonaDraft, instructionsDraft]);

  const displayTitle = directTitle ?? promptType;
  const diff = result ? computeLineDiff(result.current_prompt, result.composed_prompt) : [];
  const hasChanges = diff.some(d => d.changed);

  return (
    <div
      className="fixed inset-0 bg-black/60 flex items-center justify-center z-50"
      onClick={e => { if (e.target === e.currentTarget) onClose(); }}
    >
      <div className="bg-white dark:bg-card-dark border border-slate-200 dark:border-border-dark rounded-xl w-[90vw] max-w-5xl h-[80vh] flex flex-col overflow-hidden shadow-xl">
        {/* Modal header */}
        <div className="flex items-center px-5 py-3.5 border-b border-slate-200 dark:border-border-dark">
          <span className="flex-1 font-semibold text-slate-900 dark:text-white text-sm">
            {t('promptPreviewModal.title', { displayTitle })}
          </span>
          {result && (
            <span className="text-xs text-slate-400 dark:text-slate-500 mr-4 font-mono">
              {t('promptPreviewModal.hash', { hash: result.prompt_hash.slice(0, 12) })}
            </span>
          )}
          {!hasChanges && result && (
            <span className="text-xs text-emerald-600 dark:text-emerald-400 mr-4">{t('promptPreviewModal.noChanges')}</span>
          )}
          <button
            onClick={onClose}
            className="text-slate-400 hover:text-slate-600 dark:text-slate-500 dark:hover:text-slate-300 cursor-pointer text-lg border-none bg-transparent leading-none"
          >
            ✕
          </button>
        </div>

        {/* Body */}
        {loading && (
          <div className="flex-1 flex items-center justify-center gap-2 text-slate-400 dark:text-slate-500 text-sm">
            <span className="material-symbols-outlined !text-[18px] animate-spin">progress_activity</span>
            {t('promptPreviewModal.composing')}
          </div>
        )}
        {error && (
          <div className="flex-1 p-5 text-red-500 dark:text-red-400 text-sm">{t('promptPreviewModal.errorPrefix', { error })}</div>
        )}
        {result && (
          <div className="flex-1 flex overflow-hidden">
            {/* Current column */}
            <div className="flex-1 flex flex-col border-r border-slate-200 dark:border-border-dark min-w-0">
              <div className="px-4 py-2 bg-slate-50 dark:bg-slate-800/50 text-xs text-slate-500 dark:text-slate-400 font-semibold uppercase tracking-wide">
                {t('promptPreviewModal.current')}
              </div>
              <div className="flex-1 overflow-auto py-2">
                {diff.map((row, i) => (
                  <div
                    key={i}
                    className="px-4 font-mono text-xs whitespace-pre-wrap min-h-[18px] leading-[18px]"
                    style={{
                      background: row.changed ? 'rgba(239,68,68,0.08)' : 'transparent',
                      color: row.changed ? 'rgb(220,38,38)' : undefined,
                    }}
                  >
                    <span className={row.changed ? '' : 'text-slate-500 dark:text-slate-500'}>
                      {row.current ?? ''}
                    </span>
                  </div>
                ))}
              </div>
            </div>
            {/* Proposed column */}
            <div className="flex-1 flex flex-col min-w-0">
              <div className="px-4 py-2 bg-slate-50 dark:bg-slate-800/50 text-xs text-slate-500 dark:text-slate-400 font-semibold uppercase tracking-wide">
                {t('promptPreviewModal.proposed')}
              </div>
              <div className="flex-1 overflow-auto py-2">
                {diff.map((row, i) => (
                  <div
                    key={i}
                    className="px-4 font-mono text-xs whitespace-pre-wrap min-h-[18px] leading-[18px]"
                    style={{
                      background: row.changed ? 'rgba(34,197,94,0.08)' : 'transparent',
                      color: row.changed ? 'rgb(22,163,74)' : undefined,
                    }}
                  >
                    <span className={row.changed ? '' : 'text-slate-500 dark:text-slate-500'}>
                      {row.proposed ?? ''}
                    </span>
                  </div>
                ))}
              </div>
            </div>
          </div>
        )}
      </div>
    </div>
  );
};
