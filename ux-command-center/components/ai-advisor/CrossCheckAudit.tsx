import React, { useState } from 'react';
import { useTranslation } from 'react-i18next';
import type { CrossCheckAuditResult } from '../../src/services/api';
import { aiAdvisorVerify } from '../../src/services/api';

// ── helpers ───────────────────────────────────────────────────────────────────

function todayISO(): string {
  return new Date().toISOString().slice(0, 10);
}

function sinceISO(days: number): string {
  const d = new Date();
  d.setDate(d.getDate() - days);
  return d.toISOString().slice(0, 10);
}

type PeriodPreset = '30d' | '60d' | '90d' | 'custom';

/**
 * Very lightweight markdown renderer — renders headings, bold, bullet lists,
 * and paragraph breaks.  We intentionally avoid an external library dependency
 * since react-markdown is not in package.json and the audit output is
 * server-controlled (no user-supplied HTML).
 */
function MarkdownBlock({ text }: { text: string }) {
  const lines = text.split('\n');
  const nodes: React.ReactNode[] = [];
  let listBuffer: string[] = [];

  const flushList = (key: string) => {
    if (listBuffer.length === 0) return;
    nodes.push(
      <ul key={key} className="my-2 space-y-1 list-disc pl-5">
        {listBuffer.map((item, i) => (
          <li key={i} className="text-sm text-slate-700 dark:text-slate-300 leading-relaxed">
            {renderInline(item)}
          </li>
        ))}
      </ul>
    );
    listBuffer = [];
  };

  lines.forEach((line, idx) => {
    const trimmed = line.trimStart();

    if (trimmed.startsWith('### ')) {
      flushList(`list-${idx}`);
      nodes.push(
        <h4 key={idx} className="mt-4 mb-1 text-sm font-semibold text-slate-800 dark:text-slate-100">
          {trimmed.slice(4)}
        </h4>
      );
    } else if (trimmed.startsWith('## ')) {
      flushList(`list-${idx}`);
      nodes.push(
        <h3 key={idx} className="mt-5 mb-1 text-base font-bold text-slate-800 dark:text-slate-100">
          {trimmed.slice(3)}
        </h3>
      );
    } else if (trimmed.startsWith('# ')) {
      flushList(`list-${idx}`);
      nodes.push(
        <h2 key={idx} className="mt-6 mb-2 text-lg font-bold text-slate-900 dark:text-white">
          {trimmed.slice(2)}
        </h2>
      );
    } else if (trimmed.startsWith('- ') || trimmed.startsWith('* ')) {
      listBuffer.push(trimmed.slice(2));
    } else if (trimmed === '') {
      flushList(`list-${idx}`);
      // paragraph break — just add spacing via margin on next element
    } else {
      flushList(`list-${idx}`);
      nodes.push(
        <p key={idx} className="text-sm text-slate-700 dark:text-slate-300 leading-relaxed my-1">
          {renderInline(trimmed)}
        </p>
      );
    }
  });
  flushList('list-end');

  return <div className="space-y-0.5">{nodes}</div>;
}

function renderInline(text: string): React.ReactNode {
  // Bold: **text** or __text__
  const parts = text.split(/(\*\*[^*]+\*\*|__[^_]+__)/g);
  return parts.map((part, i) => {
    if ((part.startsWith('**') && part.endsWith('**')) || (part.startsWith('__') && part.endsWith('__'))) {
      return (
        <strong key={i} className="font-semibold text-slate-800 dark:text-slate-100">
          {part.slice(2, -2)}
        </strong>
      );
    }
    return part;
  });
}

// ── main component ────────────────────────────────────────────────────────────

export const CrossCheckAudit: React.FC = () => {
  const { t } = useTranslation('aiAdvisor');
  const [preset, setPreset] = useState<PeriodPreset>('90d');
  const [customStart, setCustomStart] = useState(sinceISO(90));
  const [customEnd, setCustomEnd] = useState(todayISO());

  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [isPeriodTooLarge, setIsPeriodTooLarge] = useState(false);
  const [result, setResult] = useState<CrossCheckAuditResult | null>(null);

  const periodStart = preset === 'custom' ? customStart : sinceISO(parseInt(preset, 10));
  const periodEnd = preset === 'custom' ? customEnd : todayISO();

  const handleGenerate = async () => {
    setLoading(true);
    setError(null);
    setIsPeriodTooLarge(false);
    try {
      const data = await aiAdvisorVerify.generateCrossCheckAudit(periodStart, periodEnd);
      setResult(data);
    } catch (err) {
      const msg = err instanceof Error ? err.message : t('crossCheckAudit.unknownError');
      if (msg.includes('Period exceeds caps') || msg.includes('narrow the date range')) {
        setIsPeriodTooLarge(true);
      } else {
        setError(msg);
      }
    } finally {
      setLoading(false);
    }
  };

  const presets: PeriodPreset[] = ['30d', '60d', '90d', 'custom'];

  return (
    <div className="rounded-xl border border-slate-200 dark:border-border-dark bg-white dark:bg-card-dark p-5">
      {/* Header */}
      <div className="flex items-center gap-3 mb-4 flex-wrap">
        <h3 className="text-sm font-semibold text-slate-800 dark:text-slate-100">{t('crossCheckAudit.title')}</h3>

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
              {p === 'custom' ? t('crossCheckAudit.custom') : p}
            </button>
          ))}
        </div>
      </div>

      {/* Custom date inputs */}
      {preset === 'custom' && (
        <div className="flex items-center gap-3 mb-4 flex-wrap">
          <div className="flex items-center gap-2">
            <label className="text-xs text-slate-500">{t('crossCheckAudit.from')}</label>
            <input
              type="date"
              value={customStart}
              onChange={(e) => setCustomStart(e.target.value)}
              className="rounded-md border border-slate-200 dark:border-slate-700 bg-slate-50 dark:bg-slate-800 px-2 py-1 text-xs text-slate-800 dark:text-slate-100 focus:outline-none focus:ring-1 focus:ring-primary"
            />
          </div>
          <div className="flex items-center gap-2">
            <label className="text-xs text-slate-500">{t('crossCheckAudit.to')}</label>
            <input
              type="date"
              value={customEnd}
              onChange={(e) => setCustomEnd(e.target.value)}
              className="rounded-md border border-slate-200 dark:border-slate-700 bg-slate-50 dark:bg-slate-800 px-2 py-1 text-xs text-slate-800 dark:text-slate-100 focus:outline-none focus:ring-1 focus:ring-primary"
            />
          </div>
        </div>
      )}

      {/* Generate button */}
      <div className="mb-4">
        <button
          type="button"
          onClick={handleGenerate}
          disabled={loading}
          className="flex items-center gap-2 px-4 py-2 rounded-lg text-sm font-medium bg-primary text-white hover:bg-primary/90 disabled:opacity-50 transition-colors"
        >
          {loading ? (
            <>
              <svg className="animate-spin h-4 w-4" viewBox="0 0 24 24" fill="none">
                <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
                <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8v8H4z" />
              </svg>
              {t('crossCheckAudit.generatingAudit')}
            </>
          ) : result ? (
            <>
              <span className="material-symbols-outlined !text-[16px]">refresh</span>
              {t('crossCheckAudit.regenerate')}
            </>
          ) : (
            <>
              <span className="material-symbols-outlined !text-[16px]">auto_awesome</span>
              {t('crossCheckAudit.generateAudit')}
            </>
          )}
        </button>
      </div>

      {/* Period too large banner */}
      {isPeriodTooLarge && (
        <div className="mb-4 p-3 rounded-lg bg-red-50 dark:bg-red-900/20 border border-red-200 dark:border-red-800 text-sm text-red-600 dark:text-red-400">
          {t('crossCheckAudit.periodTooLarge')}
        </div>
      )}

      {/* Generic error */}
      {error && (
        <div className="mb-4 p-3 rounded-lg bg-red-50 dark:bg-red-900/20 border border-red-200 dark:border-red-800 text-sm text-red-600 dark:text-red-400">
          {error}
        </div>
      )}

      {/* Audit result */}
      {result && (
        <div>
          <div className="rounded-lg border border-slate-100 dark:border-slate-700 bg-slate-50 dark:bg-slate-800/50 p-4">
            <MarkdownBlock text={result.audit_markdown} />
          </div>
          <p className="mt-3 text-[11px] text-slate-400 dark:text-slate-500">
            {t('crossCheckAudit.generatedBy', {
              model: result.model_used,
              date: new Date(result.generated_at).toLocaleString(),
            })}
          </p>
        </div>
      )}
    </div>
  );
};
