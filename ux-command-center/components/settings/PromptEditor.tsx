import React, { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { PromptBlock } from '../../src/services/api';

interface PromptEditorProps {
  label: string;
  blockKey: string;
  promptType: 'brief' | 'review' | 'review_questions';
  block: PromptBlock;
  draftText: string;
  defaultText: string;
  sharedPersonaDraft?: string;
  onChange: (text: string) => void;
  onReset: () => void;
  onPreview: () => void;
  isDirty: boolean;
}

function formatRelativeTime(t: (key: string, opts?: Record<string, unknown>) => string, isoStr: string | null): string {
  if (!isoStr) return t('promptEditor.defaultNeverSaved');
  const d = new Date(isoStr);
  const diffMs = Date.now() - d.getTime();
  const diffMins = Math.floor(diffMs / 60000);
  if (diffMins < 1) return t('promptEditor.justNow');
  if (diffMins < 60) return t('promptEditor.minutesAgo', { count: diffMins });
  const diffHrs = Math.floor(diffMins / 60);
  if (diffHrs < 24) return t('promptEditor.hoursAgo', { count: diffHrs });
  return t('promptEditor.daysAgo', { count: Math.floor(diffHrs / 24) });
}

export const PromptEditor: React.FC<PromptEditorProps> = ({
  label, block, draftText, onChange, onReset, onPreview, isDirty,
}) => {
  const { t } = useTranslation('system');
  const [isExpanded, setIsExpanded] = useState(false);

  return (
    <div className="border border-slate-200 dark:border-border-dark rounded-xl bg-white dark:bg-card-dark mb-3 overflow-hidden">
      {/* Header row */}
      <div
        onClick={() => setIsExpanded(e => !e)}
        className="flex items-center gap-3 px-4 py-3 cursor-pointer select-none"
      >
        <span className="text-slate-400 dark:text-slate-500 text-sm">
          {isExpanded ? '▼' : '▶'}
        </span>
        <span className="flex-1 font-medium text-slate-800 dark:text-slate-100 text-sm">
          {label}
        </span>
        {isDirty && (
          <span className="text-xs text-amber-600 dark:text-amber-400 bg-amber-50 dark:bg-amber-900/20 px-2 py-0.5 rounded font-semibold">
            {t('promptEditor.modified')}
          </span>
        )}
        <span className="text-xs text-slate-400 dark:text-slate-500 bg-slate-100 dark:bg-slate-800 px-2 py-0.5 rounded font-mono">
          {t('promptEditor.versionPrefix')}{block.version}
        </span>
        <span className="text-xs text-slate-400 dark:text-slate-500">
          {formatRelativeTime(t, block.updated_at)}
        </span>
      </div>

      {/* Expanded body */}
      {isExpanded && (
        <div className="px-4 pb-4">
          <textarea
            value={draftText}
            onChange={e => onChange(e.target.value)}
            lang="zh"
            rows={12}
            className="w-full font-mono text-xs bg-slate-50 dark:bg-slate-900 text-slate-700 dark:text-slate-300 border border-slate-200 dark:border-border-dark rounded p-3 resize-y leading-relaxed focus:outline-none focus:ring-2 focus:ring-primary/30"
            spellCheck={false}
          />
          <div className="flex gap-2 mt-2 items-center">
            <span className="text-xs text-slate-400 dark:text-slate-500 flex-1">
              {t('promptEditor.charsCount', { count: draftText.length, formatted: draftText.length.toLocaleString() })}
            </span>
            <button
              onClick={onPreview}
              className="px-3 py-1.5 text-xs cursor-pointer bg-blue-50 dark:bg-blue-900/20 text-blue-600 dark:text-blue-400 border border-blue-200 dark:border-blue-800 rounded hover:bg-blue-100 dark:hover:bg-blue-900/30 transition-colors"
            >
              {t('promptEditor.previewComposed')}
            </button>
            <button
              onClick={onReset}
              className="px-3 py-1.5 text-xs cursor-pointer bg-slate-50 dark:bg-slate-800 text-slate-500 dark:text-slate-400 border border-slate-200 dark:border-border-dark rounded hover:bg-slate-100 dark:hover:bg-slate-700 transition-colors"
            >
              {t('promptEditor.resetToDefault')}
            </button>
          </div>
        </div>
      )}
    </div>
  );
};
