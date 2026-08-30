import React from 'react';
import { useTranslation } from 'react-i18next';
import { useDemoMode } from '../../src/context/useDemoMode';

export function DemoModeCard() {
  const { t } = useTranslation('system');
  const { demoMode, toggleDemoMode } = useDemoMode();

  return (
    <div className="rounded-xl border border-slate-200 dark:border-slate-700 bg-white dark:bg-card-dark p-5">
      <div className="flex items-center justify-between">
        <div>
          <h3 className="text-sm font-semibold text-slate-800 dark:text-slate-100">{t('demoModeCard.title')}</h3>
          <p className="text-xs text-slate-500 dark:text-slate-400 mt-1 max-w-sm">
            {t('demoModeCard.description')}
          </p>
        </div>
        <button
          role="switch"
          aria-checked={demoMode}
          onClick={toggleDemoMode}
          className={`relative inline-flex h-6 w-11 items-center rounded-full transition-colors focus:outline-none focus:ring-2 focus:ring-primary focus:ring-offset-2 ${
            demoMode ? 'bg-primary' : 'bg-slate-200 dark:bg-slate-600'
          }`}
        >
          <span
            className={`inline-block h-4 w-4 transform rounded-full bg-white shadow-sm transition-transform ${
              demoMode ? 'translate-x-6' : 'translate-x-1'
            }`}
          />
        </button>
      </div>
    </div>
  );
}
