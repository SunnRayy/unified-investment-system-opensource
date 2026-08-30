import React from 'react';
import { useTranslation } from 'react-i18next';
import { useCurrency } from '../../src/context/useCurrency';

export function CurrencyCard() {
  const { t } = useTranslation(['system', 'common']);
  const { currency, setCurrency, usdCnyRate, rateIsFallback, rateAsOf } = useCurrency();

  const rateNote = rateIsFallback
    ? t('common:currency.rateApproxFallback', { rate: usdCnyRate.toFixed(4) })
    : rateAsOf
      ? t('common:currency.rateApproxAsOf', { rate: usdCnyRate.toFixed(4), date: rateAsOf.slice(0, 10) })
      : t('common:currency.rateApprox', { rate: usdCnyRate.toFixed(4) });

  return (
    <div className="rounded-xl border border-slate-200 dark:border-slate-700 bg-white dark:bg-card-dark p-5">
      <div className="flex items-center justify-between">
        <div>
          <h3 className="text-sm font-semibold text-slate-800 dark:text-slate-100">{t('currencyCard.title')}</h3>
          <p className="text-xs text-slate-500 dark:text-slate-400 mt-1 max-w-sm">
            {t('currencyCard.description')}
          </p>
          {currency === 'USD' && (
            <p className="text-xs text-amber-500 dark:text-amber-400 mt-1 leading-tight">
              {rateNote}
            </p>
          )}
        </div>
        <div className="flex items-center gap-1.5">
          <button
            type="button"
            role="radio"
            aria-checked={currency === 'CNY'}
            onClick={() => setCurrency('CNY')}
            className={`px-3 py-1 rounded-md text-xs font-semibold border transition-colors focus:outline-none focus:ring-2 focus:ring-primary focus:ring-offset-1 ${
              currency === 'CNY'
                ? 'bg-primary text-white border-primary'
                : 'bg-transparent text-slate-600 dark:text-slate-300 border-slate-300 dark:border-slate-600 hover:bg-slate-100 dark:hover:bg-slate-700'
            }`}
          >
            {t('currencyCard.cny')}
          </button>
          <button
            type="button"
            role="radio"
            aria-checked={currency === 'USD'}
            onClick={() => setCurrency('USD')}
            className={`px-3 py-1 rounded-md text-xs font-semibold border transition-colors focus:outline-none focus:ring-2 focus:ring-primary focus:ring-offset-1 ${
              currency === 'USD'
                ? 'bg-primary text-white border-primary'
                : 'bg-transparent text-slate-600 dark:text-slate-300 border-slate-300 dark:border-slate-600 hover:bg-slate-100 dark:hover:bg-slate-700'
            }`}
          >
            {t('currencyCard.usd')}
          </button>
        </div>
      </div>
    </div>
  );
}
