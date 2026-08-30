/**
 * formatMoney — THE locale-aware formatting module (Program BIL / WS-1, ADR-028).
 *
 * Before WS-1 the app had two incompatible formatters:
 *
 *   - `src/utils/format.tsx`                  — `formatCNY()` hardcoded to `'zh-CN'`,
 *                                               `useFormatCurrency()` picking a locale from
 *                                               the *currency* toggle.
 *   - `components/operations/formatters.ts`   — `fmtCNY()`/`fmtPct()` hardcoded to `'en-US'`
 *                                               with hand-rolled `/1e3`, `/1e6` K/M compaction.
 *
 * The second is deleted. All formatting decisions now live in this file; `format.tsx` is a
 * thin React binding layer over it and holds no formatting logic of its own.
 *
 * ── Two axes, deliberately independent ────────────────────────────────────────
 * **Language** (`'en' | 'zh-CN'`) selects the *number system* — grouping, decimal mark and
 * compact suffixes. **Currency** (`CNY`/`USD`, `src/context/useCurrency.tsx`) is a
 * display-only conversion that selects the *symbol*. English UI showing ¥ amounts is a
 * supported, expected combination — never derive one axis from the other.
 *
 * ── Compact notation is free ──────────────────────────────────────────────────
 * `Intl.NumberFormat(locale, { notation: 'compact' })` yields 万/亿 for `zh-CN` and K/M/B for
 * `en-US` from the same call. The old divide-by-1e6 branch is gone; it could only ever
 * produce K/M, which is why Chinese users saw `¥1.50M` instead of `¥150.00万`.
 *
 * Under 1,000 we fall back to plain notation so that `999` does not render as `999.00`,
 * matching the pre-WS-1 output byte-for-byte. At and above 1,000 ICU emits exactly two
 * fraction digits, which reproduces the legacy `.toFixed(2)` form (`¥1.50M`, `¥1.23K`).
 * Deliberate divergences from the deleted module, both unreachable from any call site and
 * both improvements, are recorded in `docs/plans/reports/2026-08-21-bil-ws1-report.md`.
 *
 * ── Two sign conventions, preserved on purpose ────────────────────────────────
 * The two deleted modules disagreed about where a minus sign goes, and the disagreement is
 * visible in the UI:
 *
 *   `formatCNY(-1234)`  →  `¥-1,234`   (ICU places the sign inside; ASCII hyphen-minus)
 *   `fmtCNY(-1234)`     →  `−¥1,234`   (sign hoisted before the symbol; U+2212 MINUS SIGN)
 *
 * `signStyle` keeps both rather than silently restyling every negative number in the app.
 * Unifying them is a visible product change and belongs to an owner decision, not to a
 * refactor whose contract is "zero visual change to any number in English".
 */

export type UiLocale = 'en' | 'zh-CN';

/** Placeholder for null/undefined/NaN. EM DASH, matching every pre-WS-1 formatter. */
export const EMPTY_VALUE = '—';

/** U+2212 MINUS SIGN — typographic minus, used by the `'prefix'` sign style. */
const MINUS = '−';

/**
 * BCP-47 tag handed to `Intl`. `'en'` widens to `'en-US'` because that is what every
 * pre-WS-1 call site pinned, and bare `'en'` can resolve to `en-GB`-ish date order
 * depending on the ICU build.
 */
export function intlLocale(lang: UiLocale): string {
  return lang === 'zh-CN' ? 'zh-CN' : 'en-US';
}

/**
 * Where the minus/plus sign goes.
 *   `'inline'` — let ICU place it (`¥-1,234`). The legacy `formatCNY` behaviour.
 *   `'prefix'` — hoist it in front of the currency symbol using U+2212 (`−¥1,234`).
 *                The legacy operations `fmtCNY` behaviour.
 */
export type SignStyle = 'inline' | 'prefix';

export interface NumberFormatOptions {
  /** Fixed fraction digits. Ignored when `compact` is set. Default 0. */
  decimals?: number;
  /** Use 万/亿 (zh-CN) or K/M/B (en-US). Default false. */
  compact?: boolean;
  /** Show a `+` on positive values. Only meaningful with `signStyle: 'prefix'`. Default false. */
  signed?: boolean;
  /** Default `'inline'`. */
  signStyle?: SignStyle;
}

export interface MoneyFormatOptions extends NumberFormatOptions {
  /** Currency symbol to prefix. Default `'¥'`. */
  symbol?: string;
}

function isBlank(value: number | null | undefined): value is null | undefined {
  return value == null || Number.isNaN(value);
}

/** Plain (non-compact) magnitude, no sign handling. */
function plain(abs: number, lang: UiLocale, decimals: number): string {
  return new Intl.NumberFormat(intlLocale(lang), {
    minimumFractionDigits: decimals,
    maximumFractionDigits: decimals,
  }).format(abs);
}

/** Compact magnitude (万/亿 or K/M/B), no sign handling. See the header note on <1000. */
function compact(abs: number, lang: UiLocale): string {
  if (abs < 1000) {
    return new Intl.NumberFormat(intlLocale(lang), { maximumFractionDigits: 0 }).format(abs);
  }
  return new Intl.NumberFormat(intlLocale(lang), {
    notation: 'compact',
    compactDisplay: 'short',
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  }).format(abs);
}

/**
 * Locale-aware number. No currency symbol.
 * Returns `EMPTY_VALUE` for null/undefined/NaN — every pre-WS-1 formatter did.
 */
export function formatNumber(
  value: number | null | undefined,
  lang: UiLocale,
  options: NumberFormatOptions = {},
): string {
  if (isBlank(value)) return EMPTY_VALUE;
  const { decimals = 0, compact: useCompact = false, signed = false, signStyle = 'inline' } = options;

  if (signStyle === 'inline') {
    // ICU owns the sign. `signed` is honoured via signDisplay so the option is never a lie.
    const body = useCompact ? compact(Math.abs(value), lang) : plain(Math.abs(value), lang, decimals);
    if (value < 0) return `-${body}`;
    return signed && value > 0 ? `+${body}` : body;
  }

  const sign = value < 0 ? MINUS : signed && value > 0 ? '+' : '';
  const abs = Math.abs(value);
  return `${sign}${useCompact ? compact(abs, lang) : plain(abs, lang, decimals)}`;
}

/**
 * Locale-aware money string: symbol + number.
 *
 * The symbol is prefixed manually rather than via `style: 'currency'`. ICU renders CNY in an
 * English locale as `CN¥1,234`, which is not what this app has ever shown and would change
 * every amount on every page the moment the UI language flipped.
 */
export function formatMoneyStr(
  value: number | null | undefined,
  lang: UiLocale,
  options: MoneyFormatOptions = {},
): string {
  if (isBlank(value)) return EMPTY_VALUE;
  const { symbol = '¥', decimals = 0, compact: useCompact = false, signed = false, signStyle = 'inline' } = options;
  const abs = Math.abs(value);
  const body = useCompact ? compact(abs, lang) : plain(abs, lang, decimals);

  if (signStyle === 'prefix') {
    const sign = value < 0 ? MINUS : signed && value > 0 ? '+' : '';
    return `${sign}${symbol}${body}`;
  }
  // 'inline': symbol first, then ICU's own sign — `¥-1,234`.
  const sign = value < 0 ? '-' : signed && value > 0 ? '+' : '';
  return `${symbol}${sign}${body}`;
}

export interface PercentFormatOptions {
  /** Fraction digits. Default 1. */
  digits?: number;
  /** Show `+` on positives. Default true — percent deltas read as signed everywhere here. */
  signed?: boolean;
  /**
   * Negative sign glyph. `'prefix'` (default) uses U+2212, matching the deleted operations
   * `fmtPct`. `'inline'` uses the ASCII hyphen that raw `toFixed()` produced, which is what
   * `formatPercentAlreadyScaled` has always rendered. Same reason as the money `signStyle`:
   * the two pre-WS-1 formatters disagreed and both glyphs are on screen today.
   */
  signStyle?: SignStyle;
}

/**
 * Percent whose input is ALREADY scaled (14.1 means 14.1%), NOT a 0-1 fraction.
 *
 * Several backend fields return pre-scaled percents (e.g.
 * `src/financial_analysis/cash_flow.py::calculate_trends`'s `savings_rate`, computed as
 * `avg_net / avg_income * 100`), while others return raw 0-1 fractions meant for
 * `(value * 100).toFixed(n)` at the call site. Mixing the two produced the "1410.0%" Cash
 * Surplus Rate bug (docs/plans/2026-07-25-cash-flow-classification-completion.md). Always
 * check the producing function's contract before formatting a percent.
 *
 * `toFixed` is used rather than `Intl` on purpose: `en-US` and `zh-CN` render percents
 * identically (ASCII digits, `.` decimal, trailing `%`), so routing through ICU would buy no
 * localization and would silently start grouping at 1,000% .
 */
export function formatPercent(
  value: number | null | undefined,
  options: PercentFormatOptions = {},
): string {
  if (isBlank(value)) return EMPTY_VALUE;
  const { digits = 1, signed = true, signStyle = 'prefix' } = options;
  const negative = signStyle === 'inline' ? '-' : MINUS;
  const sign = value > 0 && signed ? '+' : value < 0 ? negative : '';
  return `${sign}${Math.abs(value).toFixed(digits)}%`;
}

/* ────────────────────────────────────────────────────────────────────────────
 * Dates
 *
 * Six files pinned `'en-US'` or `'zh-CN'` inline and disagreed with each other; two of them
 * rendered Chinese dates inside an English-default UI. Everything routes through here so the
 * date follows the UI language like every other string.
 * ──────────────────────────────────────────────────────────────────────────── */

function toDate(value: string | number | Date | null | undefined): Date | null {
  if (value == null) return null;
  const d = value instanceof Date ? value : new Date(value);
  return Number.isNaN(d.getTime()) ? null : d;
}

export function formatDate(
  value: string | number | Date | null | undefined,
  lang: UiLocale,
  options?: Intl.DateTimeFormatOptions,
): string {
  const d = toDate(value);
  if (!d) return EMPTY_VALUE;
  return new Intl.DateTimeFormat(intlLocale(lang), options).format(d);
}

export function formatTime(
  value: string | number | Date | null | undefined,
  lang: UiLocale,
  options: Intl.DateTimeFormatOptions = { hour: 'numeric', minute: '2-digit', second: '2-digit' },
): string {
  const d = toDate(value);
  if (!d) return EMPTY_VALUE;
  return new Intl.DateTimeFormat(intlLocale(lang), options).format(d);
}

export function formatDateTime(
  value: string | number | Date | null | undefined,
  lang: UiLocale,
  options: Intl.DateTimeFormatOptions = { dateStyle: 'medium', timeStyle: 'short' },
): string {
  const d = toDate(value);
  if (!d) return EMPTY_VALUE;
  return new Intl.DateTimeFormat(intlLocale(lang), options).format(d);
}

/* ────────────────────────────────────────────────────────────────────────────
 * Compatibility surface for the deleted `components/operations/formatters.ts`.
 *
 * Same names, same defaults, same output — but locale-aware. Kept as named exports so the
 * operations barrel and `PipelinePanel` re-point with an import change rather than a rewrite.
 * ──────────────────────────────────────────────────────────────────────────── */

/** Operations-style money: sign hoisted before the symbol, U+2212 minus. Defaults to `en`. */
export function fmtCNY(
  n: number | null | undefined,
  opts: { compact?: boolean; signed?: boolean; lang?: UiLocale; symbol?: string } = {},
): string {
  const { compact: useCompact = false, signed = false, lang = 'en', symbol = '¥' } = opts;
  return formatMoneyStr(n, lang, { compact: useCompact, signed, symbol, signStyle: 'prefix' });
}

/** Operations-style percent. Identical to `formatPercent`; kept for call-site compatibility. */
export function fmtPct(
  n: number | null | undefined,
  opts: { signed?: boolean; digits?: number } = {},
): string {
  return formatPercent(n, opts);
}
