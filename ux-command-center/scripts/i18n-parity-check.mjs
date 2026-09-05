#!/usr/bin/env node
/**
 * i18n catalog parity check — Program BIL / WS-0 (ADR-028).
 *
 * Every key must exist in BOTH locales with a non-empty value. Nothing else in the build
 * notices when a translator adds `foo.bar` to `en/reports.json` and forgets `zh-CN` — the
 * app just silently renders the English fallback, which looks fine to an English reviewer
 * and looks broken to the Chinese user the program exists for.
 *
 * Fails (exit 1) on:
 *   - a namespace file missing from either locale, or missing from REQUIRED_NAMESPACES
 *   - a key present in one locale but not the other
 *   - an empty-string / whitespace-only value in either locale
 *   - malformed JSON
 *
 * Deliberately NOT checked: a zh-CN value byte-identical to its EN value. Brand names
 * (`WealthOS`) and language labels (`English`) are legitimately identical in both.
 *
 * Usage: node scripts/i18n-parity-check.mjs
 */
import { readFileSync, readdirSync, existsSync } from 'node:fs';
import { join, dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const LOCALES_DIR = join(ROOT, 'src', 'i18n', 'locales');
const LOCALES = ['en', 'zh-CN'];
const BASE_LOCALE = 'en';

/** Mirrors NAMESPACES in src/i18n/index.ts. Hardcoded so deleting a namespace from BOTH
 *  locales still fails loudly instead of silently passing. */
const REQUIRED_NAMESPACES = [
  'common',
  'portfolio',
  'performance',
  'reports',
  'incomeExpense',
  'valuation',
  'aiAdvisor',
  'operations',
  'management',
  'system',
  'errors',
];

// Check 5 (below) flags any en/*.json value that is PURE CJK — see the comment at its call
// site for why this class of defect exists. These two are the sole legitimate exceptions:
// a language names itself in a language picker, in both catalogs.
const ALLOWED_CJK = new Set(['common:settings.chinese', 'system:languageCard.chinese']);
const CJK_RE = /[一-鿿]/;
const LATIN_RE = /[A-Za-z]/;

const errors = [];

/** { "a": { "b": "x" } } -> Map { "a.b" => "x" } */
function flatten(obj, prefix = '', out = new Map()) {
  for (const [key, value] of Object.entries(obj)) {
    const path = prefix ? `${prefix}.${key}` : key;
    if (value !== null && typeof value === 'object' && !Array.isArray(value)) {
      flatten(value, path, out);
    } else {
      out.set(path, value);
    }
  }
  return out;
}

function loadNamespace(locale, ns) {
  const file = join(LOCALES_DIR, locale, `${ns}.json`);
  if (!existsSync(file)) {
    errors.push(`[${ns}] missing namespace file: src/i18n/locales/${locale}/${ns}.json`);
    return null;
  }
  try {
    return flatten(JSON.parse(readFileSync(file, 'utf8')));
  } catch (err) {
    errors.push(`[${ns}] ${locale}/${ns}.json is not valid JSON: ${err.message}`);
    return null;
  }
}

function listNamespaces(locale) {
  const dir = join(LOCALES_DIR, locale);
  if (!existsSync(dir)) {
    errors.push(`missing locale directory: src/i18n/locales/${locale}`);
    return [];
  }
  return readdirSync(dir)
    .filter((f) => f.endsWith('.json'))
    .map((f) => f.replace(/\.json$/, ''))
    .sort();
}

// --- namespace-set checks ------------------------------------------------------------
const onDisk = new Map(LOCALES.map((l) => [l, listNamespaces(l)]));
for (const locale of LOCALES) {
  const present = new Set(onDisk.get(locale));
  for (const ns of REQUIRED_NAMESPACES) {
    if (!present.has(ns)) {
      errors.push(`[${ns}] required namespace missing from locale "${locale}"`);
    }
  }
  for (const ns of present) {
    if (!REQUIRED_NAMESPACES.includes(ns)) {
      errors.push(
        `[${ns}] locale "${locale}" has an unregistered namespace — add it to ` +
          `REQUIRED_NAMESPACES here AND to NAMESPACES in src/i18n/index.ts, or delete it`,
      );
    }
  }
}

// --- key-set + value checks ----------------------------------------------------------
let totalKeys = 0;
for (const ns of REQUIRED_NAMESPACES) {
  const catalogs = new Map();
  for (const locale of LOCALES) {
    const flat = loadNamespace(locale, ns);
    if (flat) catalogs.set(locale, flat);
  }
  if (catalogs.size !== LOCALES.length) continue;

  const base = catalogs.get(BASE_LOCALE);
  totalKeys += base.size;

  for (const locale of LOCALES) {
    const flat = catalogs.get(locale);
    const empty = [...flat.entries()]
      .filter(([, v]) => typeof v !== 'string' || v.trim() === '')
      .map(([k]) => k);
    if (empty.length) {
      errors.push(
        `[${ns}] ${locale}: ${empty.length} empty or non-string value(s):\n` +
          empty.map((k) => `        ${k}`).join('\n'),
      );
    }
  }

  for (const locale of LOCALES) {
    if (locale === BASE_LOCALE) continue;
    const other = catalogs.get(locale);
    const missingInOther = [...base.keys()].filter((k) => !other.has(k));
    const missingInBase = [...other.keys()].filter((k) => !base.has(k));
    if (missingInOther.length) {
      errors.push(
        `[${ns}] ${missingInOther.length} key(s) in ${BASE_LOCALE} but missing from ${locale}:\n` +
          missingInOther.map((k) => `        ${k}`).join('\n'),
      );
    }
    if (missingInBase.length) {
      errors.push(
        `[${ns}] ${missingInBase.length} key(s) in ${locale} but missing from ${BASE_LOCALE}:\n` +
          missingInBase.map((k) => `        ${k}`).join('\n'),
      );
    }
  }

  // ── Check 5: no PURE-CJK value in an en/*.json catalog ──
  //
  // Added 2026-08-28 (Program BIL WS-11). Program OSR's core migration rule was "EN catalog
  // value byte-identical to the literal you replaced." Where the pre-i18n code had hardcoded
  // Chinese text inside a nominally-English UI, that rule faithfully copied the Chinese
  // straight into en/*.json. Correct for a byte-preserving migration, wrong as an end state:
  // an English-locale user reads Chinese. Checks 1-4 above never catch this, because the EN
  // and zh-CN values matched each other perfectly — parity was never the problem here,
  // translation was.
  //
  // A value is flagged only if it is PURE CJK: contains a CJK character AND zero Latin
  // letters. Mixed values (a "{{date}} 至今" template, an "e.g. 招行" example, a bilingual
  // "Language / 语言" label) are left alone — those are legitimate content, not leftover
  // Chinese. The only exemptions are the two ALLOWED_CJK entries above: "中文" genuinely IS
  // the English name of the Chinese language, the way a language picker names itself.
  for (const [key, value] of base.entries()) {
    if (typeof value !== 'string') continue;
    if (!CJK_RE.test(value) || LATIN_RE.test(value)) continue;
    if (ALLOWED_CJK.has(`${ns}:${key}`)) continue;
    errors.push(
      `[${ns}] en.${key} is pure CJK with no Latin letters: "${value}" — author real ` +
        `English, or add "${ns}:${key}" to ALLOWED_CJK at the top of this file if it is ` +
        `deliberate (e.g. "中文" in a language picker)`,
    );
  }
}

// ── Check 3: every namespace a CONVERTED file actually uses must be non-empty ──
//
// Added 2026-08-22 after WS-2. `pages/Performance.tsx` called
// useTranslation('performance') while performance.json was `{}` in BOTH locales, so every
// t() call rendered its raw key in the UI. Checks 1 and 2 passed the whole time: two empty
// catalogs are perfectly "in sync", and the ratchet only looks for un-wrapped literals, not
// for wrapped ones that resolve to nothing. The hole was found by a human noticing extra
// test failures — which is exactly the kind of luck a gate is supposed to replace.
//
// Scope is deliberately the converted-files list: an unconverted file may legitimately name
// a namespace that has not been populated yet.
{
  const convertedPath = join(ROOT, 'i18n-converted-files.json');
  if (existsSync(convertedPath)) {
    const converted = JSON.parse(readFileSync(convertedPath, 'utf8'));
    const used = new Map(); // ns -> Set(file)
    for (const rel of converted) {
      const abs = join(ROOT, rel);
      if (!existsSync(abs)) continue;
      const src = readFileSync(abs, 'utf8');
      for (const m of src.matchAll(/useTranslation\(\s*['"`]([A-Za-z0-9_-]+)['"`]/g)) {
        if (!used.has(m[1])) used.set(m[1], new Set());
        used.get(m[1]).add(rel);
      }
    }
    for (const [ns, files] of used) {
      for (const locale of LOCALES) {
        const file = join(LOCALES_DIR, locale, `${ns}.json`);
        if (!existsSync(file)) {
          errors.push(`[${ns}] used by ${[...files].join(', ')} but ${locale}/${ns}.json does not exist`);
          continue;
        }
        const flat = flatten(JSON.parse(readFileSync(file, 'utf8')));
        if (flat.size === 0) {
          errors.push(
            `[${ns}] ${locale}/${ns}.json is EMPTY but is used by ${[...files].join(', ')} — ` +
              `every t() call in those files renders its raw key`,
          );
        }
      }
    }
  }
}

// ── Check 4: COVERAGE — a file with user-visible text must be on the converted list ──
//
// Added 2026-08-28 after the owner found the Dashboard header and the login page still in
// English with the UI set to Chinese. Checks 1-3 and the ratchet all stayed green, because
// every one of them only inspects files that are ALREADY on the converted list. An
// un-converted file is invisible to the entire gate — which is fail-closed for regressions
// and fail-OPEN for omissions.
//
// The batch scopes were hand-enumerated ("pages/dashboard/*"), which silently excluded
// pages/Dashboard.tsx, pages/LoginPage.tsx and components/wealthos/. This check replaces
// hand-enumeration with a set difference, so a file cannot be forgotten again.
{
  const convertedPath = join(ROOT, 'i18n-converted-files.json');
  const converted = new Set(
    existsSync(convertedPath) ? JSON.parse(readFileSync(convertedPath, 'utf8')) : [],
  );
  // JSX text nodes and the four string-bearing props the ratchet also enforces.
  const VISIBLE = />[\s]*[A-Z][A-Za-z0-9 ,.:%/&'-]{3,}[\s]*<|(?:title|label|placeholder|aria-label)="[A-Z][^"]{2,}"/;
  const walk = (dir, out = []) => {
    for (const e of readdirSync(dir, { withFileTypes: true })) {
      const full = join(dir, e.name);
      if (e.isDirectory()) walk(full, out);
      else if (e.name.endsWith('.tsx') && !e.name.endsWith('.test.tsx')) out.push(full);
    }
    return out;
  };
  const missed = [];
  for (const base of ['pages', 'components']) {
    const dir = join(ROOT, base);
    if (!existsSync(dir)) continue;
    for (const abs of walk(dir)) {
      const rel = abs.slice(ROOT.length + 1);
      if (converted.has(rel)) continue;
      if (VISIBLE.test(readFileSync(abs, 'utf8'))) missed.push(rel);
    }
  }
  if (missed.length) {
    errors.push(
      `${missed.length} file(s) hold user-visible English but are NOT on the converted list — ` +
        `the ratchet cannot see them:\n` + missed.map((f) => `        ${f}`).join('\n'),
    );
  }
}

if (errors.length) {
  console.error('i18n parity check FAILED\n');
  for (const e of errors) console.error(`  ✗ ${e}`);
  console.error(`\n${errors.length} problem(s). Locales: ${LOCALES.join(', ')}`);
  process.exit(1);
}

console.log(
  `i18n parity check passed — ${REQUIRED_NAMESPACES.length} namespaces, ` +
    `${totalKeys} keys, ${LOCALES.length} locales in sync.`,
);
