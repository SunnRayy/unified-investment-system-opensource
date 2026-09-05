import React from 'react';
import { Trans, useTranslation } from 'react-i18next';

interface StepValidateProps {
  sourceColumns: string[];
  mapping: Record<string, string>;
  onMappingChange: (mapping: Record<string, string>) => void;
  errors: string[];
  warnings: string[];
  sampleData: Record<string, any> | null;
  importType: string;
  onReAutoMap: () => void;
}

export const StepValidate: React.FC<StepValidateProps> = ({
  sourceColumns,
  mapping,
  onMappingChange,
  errors,
  warnings,
  sampleData,
  importType,
  onReAutoMap
}) => {
  const { t } = useTranslation('system');
  // Fields expected by the backend (ImportAdapterService.stage_import_run)
  // `required` drives the "N required columns mapped" count below — it must stay a
  // dedicated flag, not a substring check on the (now-translatable) label text.
  const targetFields = importType === 'holdings'
    ? [
        { key: 'asset_id', label: t('wizard.stepValidate.field.assetIdRequired'), required: true },
        { key: 'asset_name', label: t('wizard.stepValidate.field.assetName'), required: false },
        { key: 'quantity', label: t('wizard.stepValidate.field.quantityRequired'), required: true },
        { key: 'market_price_unit', label: t('wizard.stepValidate.field.marketPrice'), required: false },
        { key: 'market_value', label: t('wizard.stepValidate.field.marketValue'), required: false },
        { key: 'cost_price_unit', label: t('wizard.stepValidate.field.costPriceUnit'), required: false },
        { key: 'currency', label: t('wizard.stepValidate.field.currency'), required: false },
        { key: 'account', label: t('wizard.stepValidate.field.account'), required: false },
        { key: 'snapshot_date', label: t('wizard.stepValidate.field.snapshotDate'), required: false },
      ]
    : [
        { key: 'asset_id', label: t('wizard.stepValidate.field.assetIdRequired'), required: true },
        { key: 'asset_name', label: t('wizard.stepValidate.field.assetName'), required: false },
        { key: 'transaction_date', label: t('wizard.stepValidate.field.dateRequired'), required: true },
        { key: 'transaction_type', label: t('wizard.stepValidate.field.typeRequired'), required: true },
        { key: 'quantity', label: t('wizard.stepValidate.field.quantity'), required: false },
        { key: 'price_unit', label: t('wizard.stepValidate.field.pricePerUnit'), required: false },
        { key: 'amount_gross', label: t('wizard.stepValidate.field.grossAmount'), required: false },
        { key: 'commission_fee', label: t('wizard.stepValidate.field.commissionFee'), required: false },
        { key: 'currency', label: t('wizard.stepValidate.field.currency'), required: false },
        { key: 'account', label: t('wizard.stepValidate.field.account'), required: false },
        { key: 'memo', label: t('wizard.stepValidate.field.memoDescription'), required: false },
      ];

  const mappedCount = sourceColumns.filter(col => mapping[col] && mapping[col] !== 'ignore').length;
  const totalTargetRequired = targetFields.filter(f => f.required).length;


  return (
    <div className="py-4">
      <div className="flex items-center justify-between mb-0.5">
        <h2 className="text-[14px] font-bold text-slate-900 dark:text-white">{t('wizard.stepValidate.title')}</h2>
        <button
          onClick={onReAutoMap}
          className="flex items-center gap-1.5 px-2.5 py-1 rounded-lg border border-slate-200 bg-white text-[10px] font-bold text-slate-700 hover:bg-slate-50 dark:border-slate-800 dark:bg-card-dark dark:text-slate-300 dark:hover:bg-slate-900 transition-colors"
        >
          <span className="material-symbols-outlined !text-[13px]">auto_fix</span>
          {t('wizard.stepValidate.reAutoMap')}
        </button>
      </div>
      <p className="text-[12px] text-slate-500 mb-5">
        <Trans
          t={t}
          i18nKey="wizard.stepValidate.matchedHeaders"
          values={{ importType }}
          components={{ strong: <span className="font-bold text-slate-700 dark:text-slate-300 capitalize" /> }}
        />
      </p>

      {/* Status Banner */}
      <div className={`mb-5 p-2.5 rounded-xl border flex items-center gap-3 ${
        errors.length > 0 
          ? 'bg-red-50 border-red-100 text-red-700 dark:bg-red-900/10 dark:border-red-900/30' 
          : 'bg-emerald-50 border-emerald-100 text-emerald-700 dark:bg-emerald-900/10 dark:border-emerald-900/30'
      }`}>
        <span className="material-symbols-outlined filled-icon !text-[18px]">
          {errors.length > 0 ? 'error' : 'check_circle'}
        </span>
        <div className="flex-1 text-[12px]">
          <span className="font-bold">{t('wizard.stepValidate.columnsMapped', { mapped: mappedCount, total: totalTargetRequired })}</span>
          <span className="mx-2 text-slate-300">·</span>
          <span>{t('wizard.stepValidate.errorsCount', { count: errors.length })}</span>
          <span className="mx-2 text-slate-300">·</span>
          <span>{t('wizard.stepValidate.warningsCount', { count: warnings.length })}</span>
        </div>
      </div>

      {/* Mapping Table */}
      <div className="rounded-xl border border-slate-200 dark:border-slate-800 overflow-hidden bg-white dark:bg-card-dark shadow-sm">
        <table className="w-full text-left border-collapse">
          <thead>
            <tr className="bg-slate-50/50 dark:bg-slate-900/30 text-[9px] font-bold text-slate-400 dark:text-slate-500 uppercase tracking-widest border-b border-slate-200 dark:border-slate-800">
              <th className="pl-5 py-2.5 w-1/3">{t('wizard.stepValidate.col.sourceColumn')}</th>
              <th className="px-3 py-2.5 w-1/3">{t('wizard.stepValidate.col.targetField')}</th>
              <th className="pr-5 py-2.5 text-right">{t('wizard.stepValidate.col.sample')}</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-slate-100 dark:divide-slate-800/50">
            {sourceColumns.map((col) => (
              <tr key={col} className="hover:bg-slate-50/30 dark:hover:bg-slate-900/20 transition-colors">
                <td className="pl-5 py-2.5">
                  <span className="px-1.5 py-0.5 rounded bg-slate-100 dark:bg-slate-800 font-mono text-[10px] text-slate-700 dark:text-slate-300 border border-slate-200 dark:border-slate-700">
                    {col}
                  </span>
                </td>
                <td className="px-3 py-2.5">
                  <div className="flex items-center gap-2">
                    <span className="material-symbols-outlined text-slate-300 !text-[16px]">arrow_forward</span>
                    <select
                      value={mapping[col] || 'ignore'}
                      onChange={(e) => onMappingChange({ ...mapping, [col]: e.target.value })}
                      className="flex-1 bg-transparent border-none focus:ring-0 text-[12px] font-medium text-slate-900 dark:text-white p-0"
                    >
                      <option value="ignore">{t('wizard.stepValidate.ignoreColumn')}</option>
                      {targetFields.map((f) => (
                        <option key={f.key} value={f.key}>{f.label}</option>
                      ))}
                    </select>
                  </div>
                </td>
                <td className="pr-5 py-2.5 text-right font-mono text-[10px] text-slate-500 truncate max-w-[150px]">
                  {sampleData?.[col] !== undefined ? String(sampleData[col]) : '–'}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {/* Error/Warning Messages */}
      {(errors.length > 0 || warnings.length > 0) && (
        <div className="mt-5 space-y-1.5">
          {errors.map((err, i) => (
            <div key={`err-${i}`} className="flex items-start gap-2 text-[11px] text-red-600 dark:text-red-400 font-medium">
              <span className="uis-badge uis-badge--danger shrink-0 uppercase tracking-tighter !py-0 !px-1.5 h-3.5 flex items-center">{t('wizard.stepValidate.error')}</span>
              <p className="mt-0">{err}</p>
            </div>
          ))}
          {warnings.map((warn, i) => (
            <div key={`warn-${i}`} className="flex items-start gap-2 text-[11px] text-amber-600 dark:text-amber-400 font-medium">
              <span className="uis-badge uis-badge--warning shrink-0 uppercase tracking-tighter !py-0 !px-1.5 h-3.5 flex items-center">{t('wizard.stepValidate.warning')}</span>
              <p className="mt-0">{warn}</p>
            </div>
          ))}
        </div>
      )}
    </div>
  );
};
