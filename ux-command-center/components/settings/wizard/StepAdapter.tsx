import React from 'react';
import { useTranslation } from 'react-i18next';

interface StepAdapterProps {
  onSelect: (type: 'holdings' | 'transactions' | 'accounts') => void;
  selectedType: 'holdings' | 'transactions' | 'accounts';
}

export const StepAdapter: React.FC<StepAdapterProps> = ({ onSelect, selectedType }) => {
  const { t } = useTranslation('system');
  const options = [
    {
      id: 'holdings',
      title: t('wizard.stepAdapter.holdings.title'),
      icon: 'account_balance_wallet',
      description: t('wizard.stepAdapter.holdings.description'),
      formats: ['CSV', 'XLSX'],
      cols: 4,
    },
    {
      id: 'transactions',
      title: t('wizard.stepAdapter.transactions.title'),
      icon: 'swap_horiz',
      description: t('wizard.stepAdapter.transactions.description'),
      formats: ['CSV', 'XLSX'],
      cols: 6,
    },
    {
      id: 'accounts',
      title: t('wizard.stepAdapter.accounts.title'),
      icon: 'account_balance',
      description: t('wizard.stepAdapter.accounts.description'),
      formats: ['CSV', 'XLSX'],
      cols: 3,
      disabled: true,
    },
  ] as const;

  return (
    <div className="py-4">
      <h2 className="text-[14px] font-bold text-slate-900 dark:text-white mb-0.5">{t('wizard.stepAdapter.title')}</h2>
      <p className="text-[12px] text-slate-500 mb-5">{t('wizard.stepAdapter.subtitle')}</p>

      <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
        {options.map((option) => (
          <button
            key={option.id}
            onClick={() => !option.disabled && onSelect(option.id as any)}
            disabled={option.disabled}
            className={`relative flex flex-col p-4 rounded-xl border-2 text-left transition-all ${
              selectedType === option.id
                ? 'border-primary bg-blue-50/30 ring-1 ring-primary dark:bg-blue-900/10'
                : option.disabled
                ? 'border-slate-100 bg-slate-50/50 opacity-60 cursor-not-allowed dark:border-slate-800/50 dark:bg-slate-900/50'
                : 'border-slate-200 bg-white hover:border-slate-300 dark:border-slate-800 dark:bg-card-dark'
            }`}
          >
            {selectedType === option.id && (
              <div className="absolute top-2.5 right-2.5 text-primary">
                <span className="material-symbols-outlined filled-icon !text-[18px]">check_circle</span>
              </div>
            )}
            {!selectedType && !option.disabled && (
              <div className="absolute top-2.5 right-2.5 text-slate-200">
                <div className="w-4 h-4 rounded-full border-2 border-current"></div>
              </div>
            )}

            <div className={`w-8 h-8 rounded-lg flex items-center justify-center mb-3 ${
              selectedType === option.id ? 'bg-primary text-white' : 'bg-slate-100 text-slate-500 dark:bg-slate-800'
            }`}>
              <span className="material-symbols-outlined !text-[18px]">{option.icon}</span>
            </div>

            <h3 className="text-[13px] font-bold text-slate-900 dark:text-white mb-0.5">{option.title}</h3>
            <p className="text-[11px] text-slate-500 leading-relaxed mb-3 flex-1">
              {option.description}
            </p>

            <div className="mt-auto pt-3 border-t border-slate-100 dark:border-slate-800/50 flex items-center gap-2">
              <div className="flex gap-1">
                {option.formats.map(f => (
                  <span key={f} className="text-[9px] font-bold text-slate-400 uppercase tracking-wider">{f}</span>
                ))}
              </div>
              <span className="text-slate-200">•</span>
              <span className="text-[9px] font-bold text-slate-700 dark:text-slate-300 uppercase tracking-wider">
                {t('wizard.stepAdapter.requiredCols', { count: option.cols })}
              </span>
            </div>
          </button>
        ))}
      </div>
    </div>
  );
};
