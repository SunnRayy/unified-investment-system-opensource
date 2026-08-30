import React, { useState } from 'react';
import { useTranslation } from 'react-i18next';

interface BriefSectionProps {
  /**
   * Stable ASCII section ID (`macro_outlook`, `trade_summary`, …) — machine
   * identity, never a display string. The label is resolved from the catalog
   * below and is NEVER taken from the model's output, which is why an English
   * brief and a Chinese brief render through exactly the same code path.
   *
   * Legacy rows are mapped to IDs by the backend's read-time adapter
   * (`src/services/ai_advisor/section_ids.py`); anything that still arrives
   * unrecognised falls back to being displayed verbatim rather than dropped.
   */
  title: string;
  content: Record<string, unknown>;
}

// ── Section order ────────────────────────────────────────────────────────────
// Exported so the two consumers (AIAdvisor page, ReviewFlow) order sections off
// the same list this component styles them from. Keeping the order and the
// SECTION_META keys in one file is what stops them drifting apart.

export const BRIEF_SECTION_ORDER = [
  'macro_outlook',
  'holdings_risk',
  'risk_alerts',
  'action_items',
  'watchlist',
];

export const REVIEW_SECTION_ORDER = [
  'trade_summary',
  'advice_accuracy',
  'portfolio_performance',
  'lessons_learned',
  'rule_updates',
];

// ── Section metadata ─────────────────────────────────────────────────────────

const SECTION_META: Record<string, { icon: string; accent: string; iconColor: string }> = {
  macro_outlook:         { icon: 'public',        accent: 'border-t-blue-400',    iconColor: 'text-blue-500' },
  holdings_risk:         { icon: 'monitor_heart', accent: 'border-t-amber-400',   iconColor: 'text-amber-500' },
  risk_alerts:           { icon: 'warning',       accent: 'border-t-red-400',     iconColor: 'text-red-500' },
  action_items:          { icon: 'trending_up',   accent: 'border-t-emerald-400', iconColor: 'text-emerald-500' },
  watchlist:             { icon: 'visibility',    accent: 'border-t-purple-400',  iconColor: 'text-purple-500' },
  trade_summary:         { icon: 'receipt_long',  accent: 'border-t-slate-400',   iconColor: 'text-slate-500' },
  advice_accuracy:       { icon: 'rule',          accent: 'border-t-indigo-400',  iconColor: 'text-indigo-500' },
  portfolio_performance: { icon: 'bar_chart',     accent: 'border-t-cyan-400',    iconColor: 'text-cyan-500' },
  lessons_learned:       { icon: 'school',        accent: 'border-t-violet-400',  iconColor: 'text-violet-500' },
  rule_updates:          { icon: 'auto_fix_high', accent: 'border-t-teal-400',    iconColor: 'text-teal-500' },
};

// ── Badge helpers ────────────────────────────────────────────────────────────

const ACTION_STYLES: Record<string, { bg: string; text: string; icon: string }> = {
  buy:  { bg: 'bg-emerald-100 dark:bg-emerald-900/40', text: 'text-emerald-700 dark:text-emerald-300', icon: 'arrow_upward' },
  sell: { bg: 'bg-red-100 dark:bg-red-900/40',         text: 'text-red-700 dark:text-red-300',         icon: 'arrow_downward' },
  hold: { bg: 'bg-slate-100 dark:bg-slate-800',        text: 'text-slate-600 dark:text-slate-300',     icon: 'pause' },
};

const ACTION_BORDER: Record<string, string> = {
  buy:  'border-l-emerald-400',
  sell: 'border-l-red-400',
  hold: 'border-l-slate-300 dark:border-l-slate-600',
};

const SEVERITY_STYLES: Record<string, { bg: string; text: string; border: string; icon: string }> = {
  high:   { bg: 'bg-red-50 dark:bg-red-900/20',    text: 'text-red-700 dark:text-red-400',    border: 'border-l-red-400',    icon: 'error' },
  medium: { bg: 'bg-amber-50 dark:bg-amber-900/20',text: 'text-amber-700 dark:text-amber-400',border: 'border-l-amber-400',  icon: 'warning' },
  low:    { bg: 'bg-blue-50 dark:bg-blue-900/20',  text: 'text-blue-700 dark:text-blue-400',  border: 'border-l-blue-400',   icon: 'info' },
};

// Was keyed on the free-text strings '高准确度'/'中准确度'/'低准确度' — which the
// backend never actually emitted, so the map was dead AND would have broken the
// moment a brief was written in English. `accuracy_tier` is an enum now; the
// Chinese is a display label resolved from the catalog.
const ACCURACY_TIER_STYLES: Record<string, { bg: string; text: string }> = {
  high:   { bg: 'bg-emerald-100 dark:bg-emerald-900/30', text: 'text-emerald-700 dark:text-emerald-300' },
  medium: { bg: 'bg-amber-100 dark:bg-amber-900/30',     text: 'text-amber-700 dark:text-amber-300' },
  low:    { bg: 'bg-red-100 dark:bg-red-900/30',         text: 'text-red-700 dark:text-red-300' },
};

/** Fields rendered as their own badge, so the generic key/value list skips them. */
const SCORECARD_BADGE_FIELDS = new Set(['accuracy_tier']);

function ActionBadge({ action }: { action: string }) {
  const { t } = useTranslation('aiAdvisor');
  const key = action.toLowerCase();
  const s = ACTION_STYLES[key] ?? ACTION_STYLES.hold;
  const label = ACTION_STYLES[key]
    ? t(`briefSection.action.${key}`)
    : action.toUpperCase();
  return (
    <span className={`inline-flex items-center gap-0.5 text-[11px] font-semibold px-2 py-0.5 rounded-full ${s.bg} ${s.text}`}>
      <span className="material-symbols-outlined !text-[11px]">{s.icon}</span>
      {label}
    </span>
  );
}

function SeverityBadge({ severity }: { severity: string }) {
  const { t } = useTranslation('aiAdvisor');
  const key = severity.toLowerCase();
  const s = SEVERITY_STYLES[key] ?? SEVERITY_STYLES.medium;
  const label = SEVERITY_STYLES[key] ? t(`briefSection.severity.${key}`) : severity;
  return (
    <span className={`inline-flex items-center gap-0.5 text-[11px] font-semibold px-2 py-0.5 rounded-full ${s.bg} ${s.text}`}>
      <span className="material-symbols-outlined !text-[11px]">{s.icon}</span>
      {label}
    </span>
  );
}

function AccuracyTierBadge({ tier }: { tier: string }) {
  const { t } = useTranslation('aiAdvisor');
  const key = tier.toLowerCase();
  const s = ACCURACY_TIER_STYLES[key];
  if (!s) {
    // Unrecognised value still renders as text — never blank the row.
    return <StatusBadge text={tier} colorClass="bg-slate-100 text-slate-600 dark:bg-slate-800 dark:text-slate-300" />;
  }
  return (
    <span className={`text-[11px] px-2 py-0.5 rounded-full font-semibold ${s.bg} ${s.text}`}>
      {t(`briefSection.accuracyTier.${key}`)}
    </span>
  );
}

function StatusBadge({ text, colorClass }: { text: string; colorClass: string }) {
  return (
    <span className={`text-[11px] px-2 py-0.5 rounded-full font-medium ${colorClass}`}>{text}</span>
  );
}

function PositionStatusBadge({ status }: { status: string }) {
  const { t } = useTranslation('aiAdvisor');
  const key = status.toLowerCase();
  const known = key === 'hold' || key === 'watch' || key === 'alert';
  return (
    <StatusBadge
      text={known ? t(`briefSection.status.${key}`) : status}
      colorClass="bg-slate-100 text-slate-600 dark:bg-slate-800 dark:text-slate-300"
    />
  );
}

// ── Item renderers ───────────────────────────────────────────────────────────

function ItemList({ itemKey, items }: { itemKey: string; items: unknown[] }): React.ReactElement | null {
  const { t } = useTranslation('aiAdvisor');

  /** Translate a snake_case key to a display label. Falls back to the spaced key. */
  const prettyLabel = (key: string): string =>
    t(`briefSection.keyLabels.${key}`, { defaultValue: key.replace(/_/g, ' ') });

  if (items.length === 0) return null;

  // actions: asset, action, reasoning
  if (itemKey === 'actions') {
    return (
      <div className="space-y-2">
        {items.map((item, i) => {
          const it = item as Record<string, unknown>;
          const act = String(it.action ?? '').toLowerCase();
          const borderColor = ACTION_BORDER[act] ?? ACTION_BORDER.hold;
          return (
            <div key={i} className={`flex items-start gap-3 p-3 rounded-lg bg-slate-50 dark:bg-slate-800/50 border-l-[3px] ${borderColor}`}>
              <div className="flex-1 min-w-0">
                <div className="flex items-center gap-2 flex-wrap">
                  <span className="font-semibold text-sm text-slate-800 dark:text-slate-100">{String(it.asset ?? '')}</span>
                  {it.action ? <ActionBadge action={String(it.action)} /> : null}
                </div>
                {it.reasoning ? (
                  <p className="mt-1 text-xs text-slate-500 dark:text-slate-400 leading-relaxed">{String(it.reasoning)}</p>
                ) : null}
              </div>
            </div>
          );
        })}
      </div>
    );
  }

  // items: title, severity, description (risk alerts)
  if (itemKey === 'items') {
    return (
      <div className="space-y-2">
        {items.map((item, i) => {
          const it = item as Record<string, unknown>;
          const sev = String(it.severity ?? '').toLowerCase();
          const s = SEVERITY_STYLES[sev] ?? SEVERITY_STYLES.medium;
          return (
            <div key={i} className={`p-3 rounded-lg border-l-[3px] ${s.border} ${s.bg}`}>
              <div className="flex items-center gap-2 flex-wrap">
                <span className="font-semibold text-sm text-slate-800 dark:text-slate-100">{String(it.title ?? '')}</span>
                {it.severity ? <SeverityBadge severity={String(it.severity)} /> : null}
              </div>
              {it.description ? (
                <p className="mt-1 text-xs text-slate-600 dark:text-slate-400 leading-relaxed">{String(it.description)}</p>
              ) : null}
            </div>
          );
        })}
      </div>
    );
  }

  // positions: name, status, comment
  if (itemKey === 'positions') {
    return (
      <div className="space-y-1.5">
        {items.map((item, i) => {
          const it = item as Record<string, unknown>;
          return (
            <div key={i} className="flex items-start gap-2 py-1.5 flex-wrap">
              <span className="font-medium text-sm text-slate-800 dark:text-slate-100 shrink-0">{String(it.name ?? '')}</span>
              {it.status ? <PositionStatusBadge status={String(it.status)} /> : null}
              {it.comment ? <span className="text-xs text-slate-500 dark:text-slate-400 flex-1">{String(it.comment)}</span> : null}
            </div>
          );
        })}
      </div>
    );
  }

  // watchlist: item, trigger, level
  if (itemKey === 'watchlist') {
    return (
      <div className="space-y-1.5">
        {items.map((item, i) => {
          const it = item as Record<string, unknown>;
          return (
            <div key={i} className="flex items-start gap-2 py-1">
              <span className="material-symbols-outlined !text-[14px] text-slate-400 mt-0.5 shrink-0">radio_button_unchecked</span>
              <div className="text-sm">
                <span className="font-medium text-slate-800 dark:text-slate-100">{String(it.item ?? '')}</span>
                {it.trigger ? (
                  <span className="text-slate-500 dark:text-slate-400">
                    {t('briefSection.watchlistDetail', { trigger: String(it.trigger) })}
                    {it.level ? t('briefSection.watchlistLevel', { level: String(it.level) }) : ''}
                  </span>
                ) : null}
              </div>
            </div>
          );
        })}
      </div>
    );
  }

  // trades: asset_id, action, grade, date, logic
  if (itemKey === 'trades') {
    return (
      <div className="space-y-2">
        {items.map((item, i) => {
          const it = item as Record<string, unknown>;
          const act = String(it.action ?? it['操作'] ?? '').toLowerCase();
          const assetName = String(it.asset ?? it.asset_id ?? it.name ?? it['资产'] ?? '');
          const tradeDate = String(it.date ?? it['日期'] ?? '');
          const reasoning = String(it.logic ?? it.reasoning ?? it.note ?? it['备注'] ?? '');
          const grade = String(it.grade ?? it.rating ?? it['评级'] ?? '');
          const borderColor = ACTION_BORDER[act] ?? ACTION_BORDER.hold;
          return (
            <div key={i} className={`p-3 rounded-lg bg-slate-50 dark:bg-slate-800/50 border-l-[3px] ${borderColor}`}>
              <div className="flex items-center gap-2 flex-wrap">
                {assetName ? <span className="font-semibold text-sm text-slate-800 dark:text-slate-100">{assetName}</span> : null}
                {it.action ? <ActionBadge action={String(it.action ?? it['操作'] ?? '')} /> : null}
                {tradeDate ? (
                  <span className="text-[11px] px-2 py-0.5 rounded-full bg-slate-100 dark:bg-slate-700 text-slate-500 dark:text-slate-400">{tradeDate}</span>
                ) : null}
                {grade && grade.toUpperCase() !== 'N/A' ? (
                  <span className="text-[11px] px-2 py-0.5 rounded-full bg-slate-100 dark:bg-slate-700 text-slate-600 dark:text-slate-300 font-medium">{grade}</span>
                ) : null}
              </div>
              {reasoning ? (
                <p className="mt-1.5 text-xs text-slate-500 dark:text-slate-400 leading-relaxed">{reasoning}</p>
              ) : null}
            </div>
          );
        })}
      </div>
    );
  }

  // scorecard: accuracy_tier renders as a styled badge; every remaining scalar
  // field renders with a translated label. Skips null/empty values, skips the
  // whole item if nothing is visible. Handles both the legacy
  // {target,status,score,comment} shape and free-form deepseek keys.
  if (itemKey === 'scorecard') {
    return (
      <div className="space-y-2">
        {items.map((item, i) => {
          const it = item as Record<string, unknown>;
          const tier = it.accuracy_tier == null ? '' : String(it.accuracy_tier);
          const visibleFields = Object.entries(it).filter(
            ([k, v]) => v != null && v !== '' && typeof v !== 'object' && !SCORECARD_BADGE_FIELDS.has(k)
          );
          if (visibleFields.length === 0 && !tier) return null;
          return (
            <div key={i} className="flex items-start gap-3 py-2 border-b border-slate-100 dark:border-slate-700/50 last:border-0">
              <div className="flex-1 min-w-0 space-y-0.5">
                {tier ? (
                  <div className="mb-1">
                    <AccuracyTierBadge tier={tier} />
                  </div>
                ) : null}
                {visibleFields.map(([k, v]) => (
                  <div key={k} className="flex gap-1 text-xs">
                    <span className="text-slate-400 shrink-0">{t('briefSection.fieldValue', { label: prettyLabel(k) })}</span>
                    <span className="text-slate-700 dark:text-slate-300">{String(v)}</span>
                  </div>
                ))}
              </div>
            </div>
          );
        })}
      </div>
    );
  }

  // lessons / improvements / suggestions
  if (itemKey === 'lessons' || itemKey === 'improvements' || itemKey === 'suggestions') {
    const icon = itemKey === 'suggestions' ? 'auto_fix_high' : itemKey === 'improvements' ? 'trending_up' : 'lightbulb';
    const iconColor = itemKey === 'suggestions' ? 'text-teal-500' : itemKey === 'improvements' ? 'text-emerald-500' : 'text-violet-500';
    return (
      <div className="space-y-2">
        {items.map((item, i) => {
          if (typeof item === 'string') {
            return (
              <div key={i} className="flex items-start gap-2.5 py-1">
                <span className={`material-symbols-outlined !text-[16px] ${iconColor} shrink-0 mt-0.5`}>{icon}</span>
                <span className="text-sm text-slate-700 dark:text-slate-300 leading-relaxed">{item}</span>
              </div>
            );
          }
          const it = item as Record<string, unknown>;
          return (
            <div key={i} className="flex items-start gap-2.5 py-1">
              <span className={`material-symbols-outlined !text-[16px] ${iconColor} shrink-0 mt-0.5`}>{icon}</span>
              <div className="flex-1">
                {it.title ? <span className="font-medium text-sm text-slate-800 dark:text-slate-100 block">{String(it.title)}</span> : null}
                {it.description ? <span className="text-xs text-slate-500 dark:text-slate-400 leading-relaxed">{String(it.description)}</span> : null}
              </div>
            </div>
          );
        })}
      </div>
    );
  }

  // key_factors or other string[]
  if (items.every((it) => typeof it === 'string')) {
    return (
      <div className="space-y-1">
        {items.map((item, i) => (
          <div key={i} className="flex items-start gap-2 py-0.5">
            <span className="text-slate-300 dark:text-slate-600 shrink-0 mt-1 text-xs">{'●'}</span>
            <span className="text-sm text-slate-600 dark:text-slate-400 leading-relaxed">{String(item)}</span>
          </div>
        ))}
      </div>
    );
  }

  // Generic object[]
  return (
    <div className="space-y-2">
      {items.map((item, i) => {
        if (typeof item !== 'object' || item === null) {
          return (
            <div key={i} className="py-1.5 border-b border-slate-100 dark:border-slate-700/50 last:border-0">
              <span className="text-sm text-slate-600 dark:text-slate-400">{String(item)}</span>
            </div>
          );
        }
        const visibleFields = Object.entries(item as Record<string, unknown>).filter(
          ([, v]) => v != null && v !== '' && typeof v !== 'object'
        );
        if (visibleFields.length === 0) return null;
        return (
          <div key={i} className="py-1.5 border-b border-slate-100 dark:border-slate-700/50 last:border-0">
            <div className="flex flex-wrap gap-x-3 gap-y-0.5">
              {visibleFields.map(([k, v]) => (
                <span key={k} className="text-xs">
                  <span className="text-slate-400">{t('briefSection.fieldValue', { label: prettyLabel(k) })} </span>
                  <span className="text-slate-700 dark:text-slate-300">{String(v)}</span>
                </span>
              ))}
            </div>
          </div>
        );
      })}
    </div>
  );
}

// grade_breakdown is sometimes a grade→count map ({A:2,B:2,C:2}) and sometimes a
// label→description map ({"A (卓越执行)": "..."}). Count maps were rendered as ugly
// stacked "key / value" boxes (GitHub #26 — the "A 2 / B 2 / C 2" rows); render
// them as compact, grade-colored chips instead. Descriptive maps fall through to
// the label/description layout below.

/** Fallback chip classes. Hoisted out of the JSX so the i18n ratchet does not
 *  read a Tailwind class list as user-visible prose (a documented rule-3 false
 *  positive — the class string is a machine value, not a label). */
const NEUTRAL_CHIP = 'bg-slate-100 text-slate-600 dark:bg-slate-800 dark:text-slate-300';

const GRADE_CHIP: Record<string, string> = {
  A: 'bg-emerald-100 text-emerald-700 dark:bg-emerald-900/40 dark:text-emerald-300',
  B: 'bg-amber-100 text-amber-700 dark:bg-amber-900/40 dark:text-amber-300',
  C: 'bg-orange-100 text-orange-700 dark:bg-orange-900/40 dark:text-orange-300',
  D: 'bg-red-100 text-red-700 dark:bg-red-900/40 dark:text-red-300',
  F: 'bg-red-100 text-red-700 dark:bg-red-900/40 dark:text-red-300',
};

function isCountMap(value: Record<string, unknown>): boolean {
  const vals = Object.values(value);
  return (
    vals.length > 0 &&
    vals.every((v) => typeof v === 'number' || (typeof v === 'string' && /^\d+$/.test(String(v).trim())))
  );
}

function CountChips({ chipKey, value }: { chipKey: string; value: Record<string, unknown> }): React.ReactElement | null {
  const { t } = useTranslation('aiAdvisor');
  const entries = Object.entries(value);
  if (entries.length === 0) return null;
  const isGrade = chipKey === 'grade_breakdown';
  return (
    <div className="flex flex-wrap items-center gap-2 py-1">
      {isGrade ? (
        <span className="text-[11px] text-slate-400 dark:text-slate-500 mr-0.5">{t('briefSection.keyLabels.grades')}</span>
      ) : null}
      {entries.map(([label, count]) => {
        const chip = isGrade
          ? GRADE_CHIP[label.trim().charAt(0).toUpperCase()] ?? NEUTRAL_CHIP
          : NEUTRAL_CHIP;
        return (
          <span key={label} className={`inline-flex items-center gap-1 text-[11px] font-semibold px-2 py-0.5 rounded-full ${chip}`}>
            <span>{label}</span>
            <span className="opacity-70">{t('briefSection.countSuffix', { value: String(count) })}</span>
          </span>
        );
      })}
    </div>
  );
}

function ObjectValue({ value }: { value: Record<string, unknown> }): React.ReactElement | null {
  const { t } = useTranslation('aiAdvisor');
  const prettyLabel = (key: string): string =>
    t(`briefSection.keyLabels.${key}`, { defaultValue: key.replace(/_/g, ' ') });

  const rendered: React.ReactElement[] = [];

  for (const [label, detail] of Object.entries(value)) {
    const displayLabel = prettyLabel(label);
    let detailNode: React.ReactNode = null;

    if (typeof detail === 'object' && detail !== null) {
      const obj = detail as Record<string, unknown>;
      if (isCountMap(obj)) {
        // Nested count-map (e.g. grades: {"N/A":12}) → chips
        detailNode = <CountChips chipKey={label} value={obj} />;
      } else {
        // Nested object → recurse one level as label/value lines
        const subEntries = Object.entries(obj).filter(([, v]) => v != null && v !== '');
        if (subEntries.length > 0) {
          detailNode = (
            <div className="space-y-0.5">
              {subEntries.map(([k2, v2]) => (
                <div key={k2} className="flex gap-1">
                  <span className="text-slate-400">{t('briefSection.fieldValue', { label: prettyLabel(k2) })}</span>
                  <span>{String(v2)}</span>
                </div>
              ))}
            </div>
          );
        }
      }
    } else if (detail != null && detail !== '') {
      detailNode = String(detail);
    }

    if (detailNode == null) continue;

    rendered.push(
      <div key={label} className="p-3 rounded-lg bg-slate-50 dark:bg-slate-800/50">
        <div className="text-sm font-medium text-slate-800 dark:text-slate-100 mb-1">{displayLabel}</div>
        <div className="text-xs text-slate-500 dark:text-slate-400 leading-relaxed">
          {detailNode}
        </div>
      </div>
    );
  }

  if (rendered.length === 0) return null;
  return <div className="space-y-2">{rendered}</div>;
}

// ── Section component ────────────────────────────────────────────────────────

export function BriefSection({ title, content }: BriefSectionProps) {
  const { t } = useTranslation('aiAdvisor');
  const [expanded, setExpanded] = useState(true);
  const narrative = typeof content.narrative === 'string' ? content.narrative : '';
  const meta = SECTION_META[title];

  // The label comes from the catalog, keyed by the stable ID. An unknown key
  // (a hand-edited payload, a section we do not know) still renders — verbatim.
  const displayTitle = t(`briefSection.sections.${title}`, { defaultValue: title });

  const prettyLabel = (key: string): string =>
    t(`briefSection.keyLabels.${key}`, { defaultValue: key.replace(/_/g, ' ') });

  const arrays = Object.entries(content).filter(([k, v]) => k !== 'narrative' && Array.isArray(v)) as [string, unknown[]][];
  const objects = Object.entries(content).filter(
    ([k, v]) => k !== 'narrative' && !Array.isArray(v) && typeof v === 'object' && v !== null
  ) as [string, Record<string, unknown>][];
  // Scalar leaf keys (e.g. post-normalizer total_trades/notes) — must not vanish
  const scalars = Object.entries(content).filter(
    ([k, v]) => k !== 'narrative' && (typeof v === 'string' || typeof v === 'number') && v !== ''
  ) as [string, string | number][];

  const itemCount = arrays.reduce((sum, [, arr]) => sum + arr.length, 0);

  return (
    <div className={`rounded-xl border border-slate-200 dark:border-border-dark bg-white dark:bg-card-dark mb-3 border-t-[3px] overflow-hidden ${meta?.accent ?? 'border-t-slate-300'}`}>
      {/* Header */}
      <button
        type="button"
        onClick={() => setExpanded(!expanded)}
        className="w-full flex items-center justify-between px-4 py-3.5 text-left hover:bg-slate-50/60 dark:hover:bg-slate-800/30 transition-colors"
      >
        <div className="flex items-center gap-2.5">
          {meta ? (
            <span className={`material-symbols-outlined !text-[18px] ${meta.iconColor}`}>{meta.icon}</span>
          ) : null}
          <h3 className="font-semibold text-sm text-slate-800 dark:text-slate-100">{displayTitle}</h3>
          {itemCount > 0 ? (
            <span className="text-[10px] font-medium px-1.5 py-0.5 rounded-full bg-slate-100 dark:bg-slate-800 text-slate-500 dark:text-slate-400">
              {itemCount}
            </span>
          ) : null}
        </div>
        <span className={`material-symbols-outlined !text-[16px] text-slate-400 transition-transform ${expanded ? '' : '-rotate-90'}`}>
          expand_more
        </span>
      </button>

      {/* Body */}
      {expanded && (
        <div className="px-4 pb-4">
          {/* Narrative */}
          {narrative && (
            <div className="flex gap-3 mb-3 p-3 rounded-lg bg-slate-50 dark:bg-slate-800/40 border border-slate-100 dark:border-slate-700/50">
              <span className="material-symbols-outlined !text-[16px] text-slate-300 dark:text-slate-600 shrink-0 mt-0.5">format_quote</span>
              <p className="text-sm text-slate-700 dark:text-slate-300 leading-relaxed whitespace-pre-wrap">{narrative}</p>
            </div>
          )}

          {/* Structured arrays */}
          {arrays.map(([key, items]) => (
            <div key={key} className="mt-2">
              <ItemList itemKey={key} items={items} />
            </div>
          ))}

          {/* Object values */}
          {objects.map(([key, value]) => (
            <div key={key} className="mt-2">
              {isCountMap(value) ? <CountChips chipKey={key} value={value} /> : <ObjectValue value={value} />}
            </div>
          ))}

          {/* Scalar leaf values */}
          {scalars.map(([key, value]) => (
            <div key={key} className="mt-2 flex gap-1.5 text-xs">
              <span className="text-slate-400 shrink-0">{t('briefSection.fieldValue', { label: prettyLabel(key) })}</span>
              <span className="text-slate-600 dark:text-slate-400 leading-relaxed whitespace-pre-wrap">{String(value)}</span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
