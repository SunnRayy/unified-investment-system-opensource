import React, { useMemo } from 'react';
import { Trans, useTranslation } from 'react-i18next';
import { ImportAdapterStagedRow } from '../../../src/services/api';

interface StepApproveProps {
  importType: string;
  file: File | null;
  mapping: Record<string, string>;
  errors: string[];
  warnings: string[];
  sourceSystem: string;
  displayName: string;
  prefixes: string;
  autoSync: boolean;
  baseCurrency: string;
  onEdit: (step: 1 | 2 | 3 | 4) => void;
  stagedCount: number;
  stagedRows: ImportAdapterStagedRow[];
  generateReader: boolean;
  onGenerateReaderChange: (value: boolean) => void;
  generatedReaderKey?: string;
  readerWarning?: string;
}

export const StepApprove: React.FC<StepApproveProps> = ({
  importType,
  file,
  mapping,
  errors,
  warnings,
  sourceSystem,
  displayName,
  prefixes,
  autoSync,
  baseCurrency,
  onEdit,
  stagedCount,
  stagedRows,
  generateReader,
  onGenerateReaderChange,
  generatedReaderKey,
  readerWarning,
}) => {
  const { t } = useTranslation('system');
  const VALUE_FORMAT = useMemo(
    () => new Intl.NumberFormat(undefined, { style: 'currency', currency: baseCurrency || 'USD', maximumFractionDigits: 0 }),
    [baseCurrency]
  );

  const mappedCount = Object.values(mapping).filter(v => v && v !== 'ignore').length;

  const totalValue = stagedRows.reduce((sum, r) => {
    const mv = Number(r.payload.market_value);
    return sum + (isNaN(mv) ? 0 : mv);
  }, 0);

  const warningRows = stagedRows.filter(r => r.validation_status === 'warning').length;
  const PREVIEW_LIMIT = 8;
  const previewRows = stagedRows.slice(0, PREVIEW_LIMIT);
  const overflow = stagedRows.length - previewRows.length;

  const sections = [
    { id: 1, label: t('wizard.stepApprove.section.adapter'), value: importType.toUpperCase(), badge: true, badgeType: 'primary' },
    { id: 2, label: t('wizard.stepApprove.section.file'), value: file ? t('wizard.stepApprove.fileSummary', { name: file.name, size: (file.size / 1024).toFixed(1), rows: stagedCount }) : '–' },
    { id: 3, label: t('wizard.stepApprove.section.validation'), value: t('wizard.stepApprove.validationSummary', { mapped: mappedCount, errors: errors.length, warnings: warnings.length }), status: errors.length === 0 },
    { id: 4, label: t('wizard.stepApprove.section.sourceSystem'), value: sourceSystem },
    { id: 4, label: t('wizard.stepApprove.section.prefixes'), value: prefixes || t('wizard.stepApprove.none') },
    { id: 4, label: t('wizard.stepApprove.section.autoSync'), value: autoSync ? t('wizard.stepApprove.enabledDaily') : t('wizard.stepApprove.disabled') },
  ];

  return (
    <div className="py-4">
      <h2 className="text-[14px] font-bold text-slate-900 dark:text-white mb-0.5">{t('wizard.stepApprove.title')}</h2>
      <p className="text-[12px] text-slate-500 mb-5">{t('wizard.stepApprove.subtitle')}</p>

      {/* Configuration summary */}
      <div className="space-y-0.5 rounded-xl border border-slate-200 dark:border-slate-800 overflow-hidden bg-white dark:bg-card-dark mb-5">
        {sections.map((section, idx) => (
          <div key={`${section.label}-${idx}`} className="group flex items-center justify-between p-3 hover:bg-slate-50/50 dark:hover:bg-slate-900/20 transition-colors border-b last:border-b-0 border-slate-100 dark:border-slate-800/50">
            <div className="flex flex-col gap-0.5">
              <span className="text-[9px] font-bold text-slate-400 uppercase tracking-widest">{section.label}</span>
              <div className="flex items-center gap-2">
                {section.badge ? (
                  <span className={`uis-badge !text-[9px] uis-badge--${section.badgeType}`}>{section.value}</span>
                ) : section.status !== undefined ? (
                  <span className={`flex items-center gap-1.5 text-[12px] font-medium ${section.status ? 'text-emerald-600' : 'text-red-600'}`}>
                    <span className="material-symbols-outlined !text-[14px]">{section.status ? 'check_circle' : 'error'}</span>
                    {section.value}
                  </span>
                ) : (
                  <span className="text-[12px] font-medium text-slate-900 dark:text-white">{section.value}</span>
                )}
              </div>
            </div>
            <button
              onClick={() => onEdit(section.id as any)}
              className="flex items-center gap-1 text-[10px] font-bold text-primary opacity-0 group-hover:opacity-100 transition-opacity hover:underline"
            >
              <span className="material-symbols-outlined !text-[13px]">edit</span>
              {t('wizard.stepApprove.edit')}
            </button>
          </div>
        ))}
      </div>

      {/* Staged data preview */}
      {stagedRows.length > 0 && (
        <div className="mb-5">
          <div className="flex items-center justify-between mb-2">
            <span className="text-[10px] font-bold text-slate-400 uppercase tracking-widest">{t('wizard.stepApprove.stagedDataPreview')}</span>
            <div className="flex items-center gap-3">
              {warningRows > 0 && (
                <span className="flex items-center gap-1 text-[10px] font-bold text-amber-600">
                  <span className="material-symbols-outlined !text-[12px]">warning</span>
                  {t('wizard.stepApprove.warningsCount', { count: warningRows })}
                </span>
              )}
              <span className="text-[10px] font-bold text-slate-500">
                {t('wizard.stepApprove.totalValueLabel')} <span className="text-slate-900 dark:text-white">{VALUE_FORMAT.format(totalValue)}</span>
              </span>
            </div>
          </div>
          <div className="rounded-xl border border-slate-200 dark:border-slate-800 overflow-hidden">
            <table className="w-full text-left">
              <thead>
                <tr className="bg-slate-50 dark:bg-slate-900/50 border-b border-slate-200 dark:border-slate-800">
                  {[
                    t('wizard.stepApprove.col.hash'),
                    t('wizard.stepApprove.col.assetId'),
                    importType === 'holdings' ? t('wizard.stepApprove.col.quantity') : t('wizard.stepApprove.col.type'),
                    importType === 'holdings' ? t('wizard.stepApprove.col.marketValue') : t('wizard.stepApprove.col.amount'),
                    t('wizard.stepApprove.col.date'),
                    t('wizard.stepApprove.col.status'),
                  ].map(h => (
                    <th key={h} className="px-3 py-2 text-[9px] font-bold text-slate-400 uppercase tracking-widest whitespace-nowrap">{h}</th>
                  ))}
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-100 dark:divide-slate-800/50">
                {previewRows.map((row) => {
                  const p = row.payload;
                  const isWarning = row.validation_status === 'warning';
                  const valueField = importType === 'holdings' ? p.market_value : p.amount_gross;
                  const qtyOrType = importType === 'holdings' ? p.quantity : p.transaction_type;
                  const date = (p.snapshot_date || p.transaction_date || '–') as string;
                  return (
                    <tr key={row.row_index} className={`text-[11px] transition-colors ${isWarning ? 'bg-amber-50/50 dark:bg-amber-900/10' : 'hover:bg-slate-50/50 dark:hover:bg-slate-900/20'}`}>
                      <td className="px-3 py-2 font-mono text-slate-400">{row.row_index + 1}</td>
                      <td className="px-3 py-2 font-mono text-slate-900 dark:text-white">{(p.asset_id || p.ticker || '–') as string}</td>
                      <td className="px-3 py-2 text-slate-600 dark:text-slate-400">{String(qtyOrType ?? '–')}</td>
                      <td className="px-3 py-2 font-mono text-slate-900 dark:text-white">
                        {valueField != null ? VALUE_FORMAT.format(Number(valueField)) : '–'}
                      </td>
                      <td className="px-3 py-2 text-slate-500 font-mono text-[10px]">{date.slice(0, 10)}</td>
                      <td className="px-3 py-2">
                        {isWarning ? (
                          <span className="inline-flex items-center gap-1 text-[9px] font-bold text-amber-600 uppercase">
                            <span className="material-symbols-outlined !text-[11px]">warning</span>{t('wizard.stepApprove.warn')}
                          </span>
                        ) : (
                          <span className="inline-flex items-center gap-1 text-[9px] font-bold text-emerald-600 uppercase">
                            <span className="material-symbols-outlined !text-[11px] filled-icon">check_circle</span>{t('wizard.stepApprove.ok')}
                          </span>
                        )}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
            {overflow > 0 && (
              <div className="px-3 py-2 bg-slate-50 dark:bg-slate-900/30 border-t border-slate-100 dark:border-slate-800/50 text-[10px] text-slate-400 text-center">
                {t('wizard.stepApprove.moreRowsNotShown', { count: overflow })}
              </div>
            )}
          </div>
        </div>
      )}

      {/* Generate reader checkbox */}
      <div className="mb-4 p-3 rounded-xl border border-slate-200 dark:border-slate-800 bg-white dark:bg-card-dark">
        <label className="flex items-start gap-3 cursor-pointer">
          <input
            type="checkbox"
            checked={generateReader}
            onChange={(e) => onGenerateReaderChange(e.target.checked)}
            className="mt-0.5 h-4 w-4 rounded border-slate-300 dark:border-slate-600 text-primary focus:ring-primary cursor-pointer shrink-0"
          />
          <div className="flex flex-col gap-0.5">
            <span className="text-[12px] font-semibold text-slate-900 dark:text-white leading-snug">
              {t('wizard.stepApprove.createReaderLabel')}
            </span>
            <span className="text-[11px] text-slate-500 leading-relaxed">
              {t('wizard.stepApprove.createReaderHint')}
            </span>
          </div>
        </label>
      </div>

      {/* Post-approve reader key success line */}
      {generatedReaderKey && (
        <div className="mb-4 p-3 rounded-xl border border-emerald-100 bg-emerald-50/50 dark:border-emerald-900/30 dark:bg-emerald-900/10 flex items-center gap-2">
          <span className="material-symbols-outlined filled-icon !text-[16px] text-emerald-500 shrink-0">check_circle</span>
          <p className="text-[12px] text-emerald-800 dark:text-emerald-400 font-medium">
            <Trans
              t={t}
              i18nKey="wizard.stepApprove.createdReader"
              values={{ key: generatedReaderKey }}
              components={{ strong: <span className="font-mono font-bold" /> }}
            />
          </p>
        </div>
      )}

      {/* Post-approve reader warning */}
      {readerWarning && (
        <div className="mb-4 p-3 rounded-xl border border-amber-200 bg-amber-50/50 dark:border-amber-900/30 dark:bg-amber-900/10 flex items-start gap-2">
          <span className="material-symbols-outlined !text-[16px] text-amber-500 shrink-0 mt-0.5">warning</span>
          <p className="text-[12px] text-amber-800 dark:text-amber-400">{readerWarning}</p>
        </div>
      )}

      <div className="p-3 rounded-xl border border-emerald-100 bg-emerald-50/50 dark:border-emerald-900/30 dark:bg-emerald-900/10 flex items-start gap-3">
        <div className="w-5 h-5 rounded-full bg-emerald-500 text-white flex items-center justify-center shrink-0">
          <span className="material-symbols-outlined !text-[14px] filled-icon">check</span>
        </div>
        <p className="text-[12px] text-emerald-800 dark:text-emerald-400 leading-relaxed">
          <Trans
            t={t}
            i18nKey="wizard.stepApprove.readyToImport"
            values={{ count: stagedCount, importType, sourceSystem }}
            components={{ strong1: <span className="font-bold" />, strong2: <span className="font-bold" /> }}
          />
        </p>
      </div>
    </div>
  );
};
