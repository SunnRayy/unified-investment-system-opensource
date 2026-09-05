import React from 'react';
import { useTranslation } from 'react-i18next';

interface StepStageProps {
  sourceSystem: string;
  setSourceSystem: (val: string) => void;
  displayName: string;
  setDisplayName: (val: string) => void;
  prefixes: string;
  setPrefixes: (val: string) => void;
  baseCurrency: string;
  setBaseCurrency: (val: string) => void;
  autoSync: boolean;
  setAutoSync: (val: boolean) => void;
}

export const StepStage: React.FC<StepStageProps> = ({
  sourceSystem,
  setSourceSystem,
  displayName,
  setDisplayName,
  prefixes,
  setPrefixes,
  baseCurrency,
  setBaseCurrency,
  autoSync,
  setAutoSync,
}) => {
  const { t } = useTranslation('system');
  const prefixList = prefixes.split(',').map(s => s.trim()).filter(Boolean);

  const removePrefix = (p: string) => {
    const updated = prefixList.filter(item => item !== p).join(', ');
    setPrefixes(updated);
  };

  const addPrefix = (e: React.KeyboardEvent<HTMLInputElement>) => {
    if (e.key === 'Enter' || e.key === ',') {
      e.preventDefault();
      const val = e.currentTarget.value.trim().replace(',', '');
      if (val && !prefixList.includes(val)) {
        setPrefixes(prefixes ? `${prefixes}, ${val}` : val);
        e.currentTarget.value = '';
      }
    }
  };

  return (
    <div className="py-4">
      <h2 className="text-[14px] font-bold text-slate-900 dark:text-white mb-0.5">{t('wizard.stepStage.title')}</h2>
      <p className="text-[12px] text-slate-500 mb-6">{t('wizard.stepStage.subtitle')}</p>

      <div className="grid grid-cols-1 md:grid-cols-2 gap-x-6 gap-y-5">
        {/* Source System */}
        <div className="space-y-1.5">
          <div className="flex items-center justify-between">
            <label className="text-[9px] font-bold text-slate-400 uppercase tracking-widest">{t('wizard.stepStage.sourceSystem')} <span className="text-primary ml-1">{t('wizard.stepStage.required')}</span></label>
          </div>
          <input
            type="text"
            placeholder={t('wizard.stepStage.sourceSystemPlaceholder')}
            value={sourceSystem}
            onChange={(e) => setSourceSystem(e.target.value.toLowerCase().replace(/[^a-z0-9_]/g, ''))}
            className="w-full bg-white dark:bg-card-dark border border-slate-200 dark:border-slate-800 rounded-xl px-3 py-2 text-[13px] text-slate-900 dark:text-white focus:ring-2 focus:ring-primary/20 focus:border-primary outline-none transition-all"
          />
          <p className="text-[10px] text-slate-400">{t('wizard.stepStage.sourceSystemHint')}</p>
        </div>

        {/* Display Name */}
        <div className="space-y-1.5 opacity-60">
          <label className="text-[9px] font-bold text-slate-400 uppercase tracking-widest">{t('wizard.stepStage.displayName')} <span className="text-[9px] text-slate-300 ml-1">{t('wizard.stepStage.comingSoon')}</span></label>
          <input
            type="text"
            placeholder={t('wizard.stepStage.displayNamePlaceholder')}
            value={displayName}
            disabled
            className="w-full bg-slate-50 dark:bg-slate-900/50 border border-slate-200 dark:border-slate-800 rounded-xl px-3 py-2 text-[13px] text-slate-400 cursor-not-allowed outline-none transition-all"
          />
          <p className="text-[10px] text-slate-400">{t('wizard.stepStage.displayNameHint')}</p>
        </div>

        {/* Account Prefixes */}
        <div className="md:col-span-2 space-y-1.5">
          <label className="text-[9px] font-bold text-slate-400 uppercase tracking-widest">{t('wizard.stepStage.accountPrefixes')} <span className="text-slate-300 ml-1">{t('wizard.stepStage.commaSeparated')}</span></label>
          <div className="w-full bg-white dark:bg-card-dark border border-slate-200 dark:border-slate-800 rounded-xl px-2 py-1 flex flex-wrap gap-1.5 focus-within:ring-2 focus-within:ring-primary/20 focus-within:border-primary transition-all min-h-[38px]">
            {prefixList.map(p => (
              <span key={p} className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded bg-blue-50 text-primary dark:bg-blue-900/30 text-[10px] font-bold tracking-tight">
                {p}
                <button onClick={() => removePrefix(p)} className="hover:text-primary-hover">
                  <span className="material-symbols-outlined !text-[12px]">close</span>
                </button>
              </span>
            ))}
            <input
              type="text"
              placeholder={t('wizard.stepStage.addPrefixPlaceholder')}
              onKeyDown={addPrefix}
              className="flex-1 bg-transparent border-none focus:ring-0 text-[12px] p-0.5 outline-none min-w-[120px]"
            />
          </div>
          <p className="text-[10px] text-slate-400">{t('wizard.stepStage.accountPrefixesHint')}</p>
        </div>

        {/* Base Currency */}
        <div className="space-y-1.5 opacity-60">
          <label className="text-[9px] font-bold text-slate-400 uppercase tracking-widest">{t('wizard.stepStage.baseCurrency')} <span className="text-[9px] text-slate-300 ml-1">{t('wizard.stepStage.comingSoon')}</span></label>
          <input
            type="text"
            placeholder={t('wizard.stepStage.baseCurrencyPlaceholder')}
            value={baseCurrency}
            disabled
            className="w-full bg-slate-50 dark:bg-slate-900/50 border border-slate-200 dark:border-slate-800 rounded-xl px-3 py-2 text-[13px] font-mono text-slate-400 cursor-not-allowed outline-none transition-all"
          />
        </div>

        {/* Auto Sync */}
        <div className="flex items-center justify-between p-3 rounded-xl border border-slate-100 dark:border-slate-800 bg-slate-50/50 dark:bg-slate-900/20">
          <div>
            <p className="text-[11px] font-bold text-slate-900 dark:text-white">{t('wizard.stepStage.autoSync')}</p>
            <p className="text-[10px] text-slate-500">{t('wizard.stepStage.autoSyncHint')}</p>
          </div>
          <button
            onClick={() => setAutoSync(!autoSync)}
            className={`relative inline-flex h-5 w-9 items-center rounded-full transition-colors focus:outline-none ${autoSync ? 'bg-primary' : 'bg-slate-300 dark:bg-slate-600'}`}
          >
            <span className={`inline-block h-3.5 w-3.5 transform rounded-full bg-white shadow-sm transition-transform ${autoSync ? 'translate-x-[18px]' : 'translate-x-[2px]'}`} />
          </button>
        </div>
      </div>
    </div>
  );
};
