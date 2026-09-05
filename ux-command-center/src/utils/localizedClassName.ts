/**
 * localizedClassName — the ONE place that decides whether a taxonomy class /
 * sub-class name renders in English or Chinese (Program BIL / WS-9).
 *
 * Background: `asset_class` / taxonomy class names are stable KEYS used across
 * the backend for joins, filters and grouping — they are never translated at
 * the API layer, and they never live in the i18n catalogs (src/i18n/locales).
 * A catalog entry would be a second source of truth competing with
 * `taxonomy_classes.name_cn`, which the owner edits live via the Taxonomy page —
 * exactly the "two sources for one value" bug class this project keeps getting
 * bitten by. Instead, endpoints that render a class/sub-class name return the
 * English field (unchanged, still the key) PLUS an additive `*_cn` companion
 * pulled from `taxonomy_classes.name_cn`. Every display site funnels both
 * fields through this resolver rather than deciding for itself.
 *
 * Precedence: `zh-CN` UI language AND a non-empty `nameCn` -> nameCn;
 * otherwise the English `name`. Never returns an empty string from a missing
 * `nameCn` and never a raw `null` — a user-created taxonomy class with no
 * `name_cn` set must still render its English name, not go blank.
 */
import type { UiLocale } from './formatMoney';

export function localizedClassName(
  name: string | null | undefined,
  nameCn: string | null | undefined,
  lang: UiLocale,
): string {
  if (lang === 'zh-CN' && nameCn) return nameCn;
  return name ?? '';
}
