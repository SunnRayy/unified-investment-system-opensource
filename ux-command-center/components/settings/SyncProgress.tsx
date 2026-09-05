import React, { useEffect, useRef, useState } from 'react';
import { Trans, useTranslation } from 'react-i18next';
import { SettingsAPI } from '../../src/services/api';

function readerLabels(t: (key: string) => string): Record<string, string> {
  return {
    schwab: t('syncProgress.readerLabel.schwab'),
    cn_fund: t('syncProgress.readerLabel.cnFund'),
    gold: t('syncProgress.readerLabel.gold'),
    insurance: t('syncProgress.readerLabel.insurance'),
    rsu: t('syncProgress.readerLabel.rsu'),
    financial_summary: t('syncProgress.readerLabel.financialSummary'),
  };
}

interface SyncProgressProps {
  onDismiss: () => void;
  onComplete: () => void;
  targetReader?: string | null;
}

export const SyncProgress: React.FC<SyncProgressProps> = ({ onDismiss, onComplete, targetReader }) => {
  const { t } = useTranslation('system');
  const [lines, setLines] = useState<string[]>([]);
  const [status, setStatus] = useState<'running' | 'success' | 'failed'>('running');
  const scrollRef = useRef<HTMLDivElement>(null);

  const onCompleteRef = useRef(onComplete);
  useEffect(() => { onCompleteRef.current = onComplete; }, [onComplete]);

  useEffect(() => {
    const cleanup = SettingsAPI.streamSyncLogs(
      (msg) => {
        setLines(prev => {
          const next = [...prev, msg];
          return next.length > 200 ? next.slice(-200) : next;
        });
        requestAnimationFrame(() => {
          if (scrollRef.current) {
            scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
          }
        });
      },
      (success) => {
        setStatus(success ? 'success' : 'failed');
        onCompleteRef.current();
      }
    );
    return cleanup;
  }, []);

  return (
    <div className="mb-4 rounded-xl border border-slate-700 bg-slate-900 overflow-hidden">
      {/* Header */}
      <div className="flex items-center justify-between px-4 py-2.5 border-b border-slate-700">
        <div className="flex items-center gap-2">
          {status === 'running' && (
            <span className="material-symbols-outlined !text-[14px] animate-spin text-slate-400">progress_activity</span>
          )}
          {status === 'success' && (
            <span className="material-symbols-outlined !text-[14px] text-emerald-400">check_circle</span>
          )}
          {status === 'failed' && (
            <span className="material-symbols-outlined !text-[14px] text-red-400">error</span>
          )}
          <span className="text-xs font-medium text-slate-300">
            {status === 'running' ? t('syncProgress.inProgress') : status === 'success' ? t('syncProgress.completed') : t('syncProgress.failed')}
          </span>
        </div>
        <button
          onClick={onDismiss}
          disabled={status === 'running'}
          className="text-slate-500 hover:text-slate-300 disabled:opacity-30 disabled:cursor-not-allowed transition-colors"
          aria-label={t('syncProgress.dismiss')}
        >
          <span className="material-symbols-outlined !text-[16px]">close</span>
        </button>
      </div>
      {targetReader && (
        <div className="px-4 py-1.5 bg-slate-800 border-b border-slate-700 text-[11px] text-slate-400 flex items-center gap-1.5">
          <span className="material-symbols-outlined !text-[12px]">filter_alt</span>
          <Trans
            t={t}
            i18nKey="syncProgress.scopeNote"
            values={{ reader: readerLabels(t)[targetReader] ?? targetReader }}
            components={{ strong: <span className="text-slate-200 font-medium" /> }}
          />
        </div>
      )}
      {/* Log area */}
      <div
        ref={scrollRef}
        className="h-48 overflow-y-auto p-3 font-mono text-[11px] leading-relaxed text-slate-300 space-y-0.5"
      >
        {lines.length === 0 ? (
          <span className="text-slate-500">{t('syncProgress.waitingForLog')}</span>
        ) : (
          lines.map((line, i) => (
            <div
              key={i}
              className={
                line.includes('ERROR') ? 'text-red-400' :
                line.includes('WARNING') ? 'text-amber-400' :
                line.includes('SYNC COMPLETED') ? 'text-emerald-400 font-semibold' :
                'text-slate-300'
              }
            >
              {line}
            </div>
          ))
        )}
      </div>
    </div>
  );
};
