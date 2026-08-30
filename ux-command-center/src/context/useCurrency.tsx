/**
 * CurrencyContext — display-only CNY/USD toggle.
 *
 * All stored values are in CNY. When USD is selected the frontend divides
 * by the fetched USD/CNY rate for display only. No stored data is mutated.
 *
 * Rate is fetched once from GET /market/fx-rate on provider mount.
 * Falls back to 7.0 if the call fails (matching the backend fallback).
 */
import React, { createContext, useContext, useState, useEffect, useCallback, ReactNode } from 'react';
import { authFetch } from '../services/authFetch';
import { API_BASE } from '../services/api/base';

export type ReportingCurrency = 'CNY' | 'USD';

const STORAGE_KEY = 'uis-reporting-currency';
const FALLBACK_RATE = 7.0;

export interface CurrencyContextType {
  /** Currently selected reporting currency for display. Default: 'CNY'. */
  currency: ReportingCurrency;
  /** Set the reporting currency and persist to localStorage. */
  setCurrency: (c: ReportingCurrency) => void;
  /** Latest USD→CNY rate fetched from /market/fx-rate (or fallback 7.0). */
  usdCnyRate: number;
  /** True if the rate is the hardcoded fallback (i.e. live fetch failed). */
  rateIsFallback: boolean;
  /** ISO 8601 as_of from the API, or null. */
  rateAsOf: string | null;
  /**
   * Convert a CNY value to the selected display currency.
   * Returns the CNY value unchanged when currency === 'CNY'.
   * Returns cny / usdCnyRate when currency === 'USD'.
   */
  convertFromCNY: (cnySumValue: number) => number;
  /** Currency symbol for the selected reporting currency. */
  currencySymbol: string;
}

const CurrencyContext = createContext<CurrencyContextType | undefined>(undefined);

export const CurrencyProvider: React.FC<{ children: ReactNode }> = ({ children }) => {
  const [currency, setCurrencyState] = useState<ReportingCurrency>(() => {
    const stored = localStorage.getItem(STORAGE_KEY);
    return stored === 'USD' ? 'USD' : 'CNY';
  });
  const [usdCnyRate, setUsdCnyRate] = useState<number>(FALLBACK_RATE);
  const [rateIsFallback, setRateIsFallback] = useState<boolean>(true);
  const [rateAsOf, setRateAsOf] = useState<string | null>(null);

  useEffect(() => {
    authFetch(`${API_BASE}/market/fx-rate`)
      .then(r => r.ok ? r.json() : null)
      .then((data: { pair: string; rate: number; as_of: string | null } | null) => {
        if (data && typeof data.rate === 'number' && data.rate > 0) {
          setUsdCnyRate(data.rate);
          setRateIsFallback(data.rate === FALLBACK_RATE);
          setRateAsOf(data.as_of ?? null);
        }
      })
      .catch(() => {
        // Keep fallback rate — already set as default state.
      });
  }, []);

  const setCurrency = useCallback((c: ReportingCurrency) => {
    localStorage.setItem(STORAGE_KEY, c);
    setCurrencyState(c);
  }, []);

  const convertFromCNY = useCallback(
    (cnySumValue: number): number => {
      if (currency === 'USD') return cnySumValue / usdCnyRate;
      return cnySumValue;
    },
    [currency, usdCnyRate],
  );

  const currencySymbol = currency === 'USD' ? '$' : '¥';

  return (
    <CurrencyContext.Provider
      value={{ currency, setCurrency, usdCnyRate, rateIsFallback, rateAsOf, convertFromCNY, currencySymbol }}
    >
      {children}
    </CurrencyContext.Provider>
  );
};

/** CNY-mode no-op fallback used when the hook is called outside a CurrencyProvider (e.g. in tests). */
const CNY_FALLBACK: CurrencyContextType = {
  currency: 'CNY',
  setCurrency: () => {},
  usdCnyRate: FALLBACK_RATE,
  rateIsFallback: true,
  rateAsOf: null,
  convertFromCNY: (v: number) => v,
  currencySymbol: '¥',
};

export function useCurrency(): CurrencyContextType {
  const ctx = useContext(CurrencyContext);
  // Outside a provider → return CNY passthrough so existing tests don't break.
  return ctx ?? CNY_FALLBACK;
}
