import React, { useState } from 'react';
import { useTranslation } from 'react-i18next';

interface LLMDebugLogProps {
  promptText?: string;
  rawResponse?: string;
  modelUsed?: string;
  tokenCount?: number;
  costEstimate?: number;
}

export const LLMDebugLog: React.FC<LLMDebugLogProps> = ({
  promptText,
  rawResponse,
  modelUsed,
  tokenCount,
  costEstimate,
}) => {
  const { t } = useTranslation('aiAdvisor');
  const [isOpen, setIsOpen] = useState(false);

  if (!promptText && !rawResponse) return null;

  return (
    <div className="mt-6 rounded-2xl border border-slate-200 bg-white shadow-sm">
      <button
        type="button"
        onClick={() => setIsOpen(!isOpen)}
        aria-expanded={isOpen}
        aria-label={isOpen ? t('llmDebugLog.collapseAria') : t('llmDebugLog.expandAria')}
        className="w-full flex items-center justify-between gap-3 px-4 py-3 text-sm text-slate-600 hover:bg-slate-50 rounded-2xl transition-colors"
      >
        <span className="flex items-center gap-2 min-w-0">
          <span className="inline-flex h-9 w-9 items-center justify-center rounded-full bg-rose-50 text-rose-600 flex-shrink-0">
            <span className="material-symbols-outlined !text-[18px]" aria-hidden="true">bug_report</span>
          </span>
          <span className="min-w-0">
            <span className="block text-left font-semibold text-slate-800">{t('llmDebugLog.title')}</span>
            <span className="flex flex-wrap items-center gap-2 text-xs text-slate-500">
              {modelUsed && <span className="rounded-full bg-slate-100 px-2.5 py-1 font-medium text-slate-600">{modelUsed}</span>}
              {tokenCount && <span>{t('llmDebugLog.tokens', { count: tokenCount.toLocaleString() })}</span>}
              {costEstimate !== undefined && <span>{t('llmDebugLog.costUsd', { cost: costEstimate.toFixed(4) })}</span>}
            </span>
          </span>
        </span>
        <span className="inline-flex items-center gap-2 rounded-full border border-slate-200 bg-white px-3 py-1.5 text-xs font-medium text-slate-600 shadow-sm">
          <span>{isOpen ? t('llmDebugLog.collapse') : t('llmDebugLog.expand')}</span>
          <span className="material-symbols-outlined !text-[16px]" aria-hidden="true">
            {isOpen ? 'expand_less' : 'expand_more'}
          </span>
        </span>
      </button>

      {isOpen && (
        <div className="border-t border-slate-200 p-4 space-y-4">
          {promptText && (
            <div>
              <h4 className="text-xs font-semibold text-slate-500 uppercase tracking-wide mb-2">{t('llmDebugLog.contextSent')}</h4>
              <pre className="text-xs bg-slate-50 p-3 rounded-xl overflow-auto max-h-64 whitespace-pre-wrap font-mono text-slate-700">
                {promptText}
              </pre>
            </div>
          )}
          {rawResponse && (
            <div>
              <h4 className="text-xs font-semibold text-slate-500 uppercase tracking-wide mb-2">{t('llmDebugLog.rawResponse')}</h4>
              <pre className="text-xs bg-slate-50 p-3 rounded-xl overflow-auto max-h-64 whitespace-pre-wrap font-mono text-slate-700">
                {rawResponse}
              </pre>
            </div>
          )}
        </div>
      )}
    </div>
  );
};
