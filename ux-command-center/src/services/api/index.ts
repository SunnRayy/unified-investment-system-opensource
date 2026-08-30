// Barrel: composes the `api` mega-object from domain partials and re-exports
// everything so all existing importers (pages, components, tests) continue to
// resolve `services/api` without any changes.

import { decisionsApi } from './decisions';
import { coreApi } from './core';
import { portfolioApi } from './portfolio';
import { performanceApi } from './performance';
import { valuationApi } from './valuation';
import { valueTrapApi } from './value-trap';
import { northStarApi } from './north-star';
import { attributionApi } from './attribution';
import { forecastApi } from './forecast';

// Recompose the legacy `api` object — same shape, same keys.
// Spread order matches the original api object (lines 768-1220 of the old api.ts).
export const api = {
  ...decisionsApi,
  ...coreApi,
  ...portfolioApi,
  ...performanceApi,
  ...valuationApi,
  ...valueTrapApi,
  ...northStarApi,
  ...attributionApi,
  ...forecastApi,
};

// Re-export all types and named exports so `import { Foo } from 'services/api'` still works.
export * from './types';
export * from './base';       // API_BASE, safeReadError
export * from './taxonomy';   // TaxonomyAPI, RiskProfileAPI
export * from './management'; // ManagementAPI, OperationsAPI
export * from './financials'; // BalanceSheetAPI, IncomeExpenseAPI, AnalyticsAPI
export * from './market';     // SentimentAPI, ExportAPI, AuditAPI
export * from './settings';   // SettingsAPI
export * from './ai-advisor'; // getLLMSettings, generateBrief, aiAdvisorVerify, …
export * from './reader-mappings'; // readerMappingsApi (ADR-023 / WS-A)
export * from './attribution';    // attributionApi (Attribution & Flows Program WS-1)
export * from './forecast';       // forecastApi (Forecast & Planning redesign R-3)
export * from './manual-pnl';     // manualPnlApi (#7 owner-logged P&L, Release 2)
