/**
 * format.tsx — React binding layer over `src/utils/formatMoney.ts`.
 *
 * Program BIL / WS-1 collapsed the app's two competing formatters into one module. **All
 * formatting logic lives in `formatMoney.ts`**; this file only binds it to React context
 * (currency + language) and wraps the result in the `.money-value` span. If you are about to
 * add a rounding rule, a locale pin or a sign convention here, it belongs there instead.
 *
 * Public surface is unchanged from before WS-1 so no page needed re-pointing.
 */
import React from 'react';
import { useCurrency } from '../context/useCurrency';
import { useLanguage } from '../context/useLanguage';
import i18n, { normalizeLang } from '../i18n';
import { formatMoneyStr, formatPercent, type UiLocale } from './formatMoney';

export {
  formatNumber,
  formatMoneyStr,
  formatPercent,
  formatDate,
  formatTime,
  formatDateTime,
  intlLocale,
  EMPTY_VALUE,
  type UiLocale,
} from './formatMoney';

/**
 * Current UI locale, read from the i18next singleton rather than a hook.
 *
 * `formatCNY` is a plain function, not a hook, and is called from ~7 pages that have not yet
 * been migrated to `useFormatCurrency`. Reading the singleton keeps it locale-*correct* on
 * every render without changing its signature. Caveat: a component that subscribes to
 * nothing from i18next will not re-render on a language toggle, so its numbers keep the old
 * locale until the next render. WS-2/3/4 remove that caveat by migrating those pages —
 * every one of them will be calling `useTranslation()` by then.
 */
function currentLang(): UiLocale {
  return normalizeLang(i18n.resolvedLanguage ?? i18n.language);
}

/**
 * formatCNY — CNY-only formatter. Always renders ¥ regardless of the selected reporting
 * currency; prefer `useFormatCurrency()` in new code.
 *
 * Sign style is `'inline'` (`¥-1,234`), preserved byte-for-byte from the pre-WS-1
 * implementation. See the `signStyle` note in `formatMoney.ts` — the operations formatter
 * used the opposite convention and both are still in the UI.
 *
 * Call sites still on this function (all migrate in WS-2/3/4):
 *   TierAudit · Analytics · WealthOS · AssetAudit · IncomeExpense · DataSourceManager ·
 *   SourceHealthDashboard
 */
export function formatCNY(value: number, decimals = 0): JSX.Element {
  return <span className="money-value">{formatMoneyStr(value, currentLang(), { decimals })}</span>;
}

/**
 * useFormatCurrency — context-aware money formatter hook.
 *
 * Returns `formatMoney(cnySumValue, decimals?)`:
 *   - reporting currency `CNY` → `¥<value>`
 *   - reporting currency `USD` → divides by the fetched rate, renders `$<value>`
 *
 * Grouping and decimal marks follow the **UI language**, not the currency. Those are two
 * independent axes: an English UI showing ¥ amounts is a supported combination, and before
 * WS-1 this hook derived its locale from the currency toggle, which conflated them.
 *
 * Migrated call sites: HeroKpis · NetWorthTrend · Performance · BalanceSheet · Compass.
 */
export function useFormatCurrency() {
  const format = useFormatCurrencyStr();
  return function formatMoney(cnySumValue: number, decimals = 0): JSX.Element {
    return <span className="money-value">{format(cnySumValue, decimals)}</span>;
  };
}

/**
 * Plain-string variant of `useFormatCurrency` for non-JSX contexts — chart tooltips, aria
 * labels, string concatenation.
 */
export function useFormatCurrencyStr() {
  const { convertFromCNY, currencySymbol } = useCurrency();
  const { lang } = useLanguage();

  return function formatMoneyStrBound(cnySumValue: number, decimals = 0): string {
    return formatMoneyStr(convertFromCNY(cnySumValue), lang, {
      decimals,
      symbol: currencySymbol,
    });
  };
}

/**
 * formatPercentAlreadyScaled — value is ALREADY a percent (14.1 means 14.1%), NOT a 0-1
 * fraction. Unsigned by default, unlike `formatPercent`.
 *
 * Retained as a distinct name because ~20 call sites rely on the unsigned default; see
 * `formatPercent` in `formatMoney.ts` for the full contract and the "1410.0%" bug history.
 */
export function formatPercentAlreadyScaled(
  value: number | null | undefined,
  decimals = 1,
): string {
  if (value == null) return '—';
  return formatPercent(value, { digits: decimals, signed: false, signStyle: 'inline' });
}

/** Locale of the running i18next instance. Exported for tests and non-React helpers. */
export { currentLang };
