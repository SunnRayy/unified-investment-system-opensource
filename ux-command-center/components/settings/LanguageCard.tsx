import React from 'react';
import { useTranslation } from 'react-i18next';
import { useLanguage } from '../../src/context/useLanguage';

export function LanguageCard() {
  const { t } = useTranslation('system');
  const { lang, setLang } = useLanguage();

  return (
    <div className="rounded-xl border border-slate-200 dark:border-slate-700 bg-white dark:bg-card-dark p-5">
      <div className="flex items-center justify-between">
        <div>
          <h3 className="text-sm font-semibold text-slate-800 dark:text-slate-100">{t('languageCard.title')}</h3>
          <p className="text-xs text-slate-500 dark:text-slate-400 mt-1 max-w-sm">
            {t('languageCard.description')}
          </p>
        </div>
        <div className="flex items-center gap-1.5">
          <button
            type="button"
            role="radio"
            aria-checked={lang === 'en'}
            onClick={() => setLang('en')}
            className={`px-3 py-1 rounded-md text-xs font-semibold border transition-colors focus:outline-none focus:ring-2 focus:ring-primary focus:ring-offset-1 ${
              lang === 'en'
                ? 'bg-primary text-white border-primary'
                : 'bg-transparent text-slate-600 dark:text-slate-300 border-slate-300 dark:border-slate-600 hover:bg-slate-100 dark:hover:bg-slate-700'
            }`}
          >
            {t('languageCard.english')}
          </button>
          <button
            type="button"
            role="radio"
            aria-checked={lang === 'zh-CN'}
            onClick={() => setLang('zh-CN')}
            className={`px-3 py-1 rounded-md text-xs font-semibold border transition-colors focus:outline-none focus:ring-2 focus:ring-primary focus:ring-offset-1 ${
              lang === 'zh-CN'
                ? 'bg-primary text-white border-primary'
                : 'bg-transparent text-slate-600 dark:text-slate-300 border-slate-300 dark:border-slate-600 hover:bg-slate-100 dark:hover:bg-slate-700'
            }`}
          >
            {t('languageCard.chinese')}
          </button>
        </div>
      </div>
    </div>
  );
}
