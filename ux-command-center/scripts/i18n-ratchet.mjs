#!/usr/bin/env node
/**
 * i18n literal ratchet — Program BIL / WS-0 (ADR-028).
 *
 * WHY THIS EXISTS AND NOT ESLint `react/jsx-no-literals`:
 * this repo has no ESLint dependency and no ESLint config anywhere. Adopting one mid-program,
 * in a public repo, to serve a single rule means importing a whole lint toolchain plus the
 * config argument that comes with it. This script is the proportionate version: it uses the
 * TypeScript compiler that is ALREADY a devDependency to parse real TSX, so it is not a
 * regex guessing at JSX, and it encodes this project's allowlist directly instead of
 * fighting a generic rule's false positives.
 *
 * RATCHET SEMANTICS: it scans ONLY the files listed in `i18n-converted-files.json`. That
 * list GROWS as workstreams convert files. Unconverted files stay silent (no wall of noise
 * to ignore), and a file that has been converted can never silently regress.
 *
 * WHAT IT FLAGS
 *   1. JSX text nodes containing user-visible text.
 *   2. Static strings in `title=`, `label=`, `placeholder=`, `aria-label=` — including
 *      strings inside a ternary in those attributes.
 *   3. String / template literals inside JSX *child* expression containers, e.g.
 *      `<span>{cond ? 'Night' : 'Day'}</span>`. Attribute expressions are NOT scanned by
 *      this rule (className, onClick handlers, data payloads etc. are full of legitimate
 *      literals) — only the four attributes above are, via rule 2.
 *
 * Anything wrapped in `t(...)` / `i18n.t(...)` is skipped, as is any element whose
 * className contains `material-symbols` (its text node is a Material Symbols ligature
 * name — `dark_mode`, `translate` — not prose).
 *
 * KNOWN FALSE POSITIVES (things it will flag that may be fine — fix by extracting anyway,
 * or add a narrowly-scoped allowlist entry with a comment):
 *   - A `title=` / `label=` that is a machine value rather than prose (a chart series id,
 *     an internal enum). Rare in this codebase; none today.
 *   - Multi-word proper nouns not in BRAND_STRINGS (a new product/vendor name).
 *   - A JSX text node that is a code sample or a unit that happens to contain letters
 *     (`bps`, `YoY`). ALL-CAPS ≤6 chars is allowlisted; mixed case is not.
 *   - A user-visible string that is ALSO a comparison operand. `isMachineValue` suppresses
 *     equality/`case`/property-key operands wholesale; a literal in that position is
 *     assumed to be a program value.
 *   - Rule 3 (`child-expr-literal`) only: an object-literal built inline inside a JSX child
 *     expression — e.g. `{[{ key: 'drawdown' as RiskMetricKey, title: t('...'), barTestId:
 *     'risk-bar-drawdown' }].map(...)}` — carries discriminator/id string literals sitting
 *     right next to the real, correctly-wrapped display string (`title`). Three narrow,
 *     rule-3-only exemptions handle this without touching rules 1/2 (JSX text and the four
 *     enforced attributes stay exactly as strict as before):
 *       (a) the literal is the value of a property named `key`/`id`/`type`/`name`, or a
 *           compound ending in `Id` (`barTestId`, `rowId`) — `isNonDisplayPropertyValue`.
 *       (b) the literal is immediately cast — `'sharpe' as RiskMetricKey` — nobody
 *           type-asserts a display string — `isAsCastValue`.
 *       (c) the literal is a single bare lowercase/kebab/snake token with no whitespace
 *           (`drawdown`, `risk-bar-sharpe`) — real prose almost always has a space, a
 *           capital letter, or punctuation — `isBareIdentifierLiteral`. Deliberately narrow:
 *           add one space, one capital, or one comma and it is flagged again.
 *     None of these touch `isAllowed`, so JSX text nodes and `title=`/`label=`/
 *     `placeholder=`/`aria-label=` attributes are unaffected — a bare-word button label or
 *     a one-word `title=` would still be caught. See the WS-2 gate proof in
 *     `docs/plans/reports/2026-08-21-bil-ws2-report.md` for the red/green check that this
 *     narrowing did not blunt rule 3 against real prose.
 *
 * KNOWN FALSE NEGATIVES (it will NOT catch these — they are on the reviewer):
 *   - Literals in attribute expressions other than the four enforced ones (e.g. a
 *     user-visible string passed as a custom prop such as `emptyMessage="No data"`).
 *   - Strings built in plain TS above the JSX (`const msg = 'No data'; return <p>{msg}</p>`).
 *   - Strings returned from helpers in another module.
 *   - `alt=` (not in the enforced set, though it is user-visible to screen readers).
 *
 * Usage: node scripts/i18n-ratchet.mjs
 */
import { readFileSync, existsSync } from 'node:fs';
import { join, dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import ts from 'typescript';

const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const LIST_FILE = join(ROOT, 'i18n-converted-files.json');

const ENFORCED_ATTRS = new Set(['title', 'label', 'placeholder', 'aria-label']);

/** Exact strings that are brand/product wordmarks and must NOT be translated. */
const BRAND_STRINGS = new Set(['Huinsight', 'Huinsight Command', 'WealthOS']);

/** Punctuation, symbols, digits and whitespace only — `—`, `·`, `×`, `%`, `$`, `¥`, `?`. */
const PUNCT_ONLY = /^[\p{P}\p{S}\p{Z}\s\d]+$/u;

/** Ticker / currency / locale codes: short ALL-CAPS runs — `USD`, `CNY`, `SPY`, `GOOGL`, `EN`. */
const CODE_LIKE = /^[A-Z][A-Z0-9.]{0,5}$/;

function isAllowed(text) {
  const s = text.trim();
  if (s === '') return true;
  if (PUNCT_ONLY.test(s)) return true;
  if (BRAND_STRINGS.has(s)) return true;
  if (CODE_LIKE.test(s)) return true;
  return false;
}

// ---------------------------------------------------------------------------------------

if (!existsSync(LIST_FILE)) {
  console.error(`i18n ratchet FAILED\n  ✗ missing ${LIST_FILE}`);
  process.exit(1);
}

let files;
try {
  files = JSON.parse(readFileSync(LIST_FILE, 'utf8'));
  if (!Array.isArray(files)) throw new Error('expected a JSON array of paths');
} catch (err) {
  console.error(`i18n ratchet FAILED\n  ✗ i18n-converted-files.json: ${err.message}`);
  process.exit(1);
}

const findings = [];

function attrName(attr) {
  return ts.isIdentifier(attr.name) || ts.isJsxNamespacedName(attr.name)
    ? attr.name.getText()
    : String(attr.name.escapedText ?? '');
}

function isTranslationCall(node) {
  if (!ts.isCallExpression(node)) return false;
  const callee = node.expression;
  if (ts.isIdentifier(callee)) return callee.text === 't';
  if (ts.isPropertyAccessExpression(callee)) return callee.name.text === 't';
  return false;
}

/**
 * A string literal compared against, switched on, or used as a property key is a program
 * value, not output: `mode === 'day'`, `case 'sold':`, `obj['id']`. Translating one would
 * break the code. Suppressed everywhere.
 */
function isMachineValue(node) {
  const p = node.parent;
  if (!p) return false;
  if (ts.isBinaryExpression(p)) {
    const op = p.operatorToken.kind;
    return (
      op === ts.SyntaxKind.EqualsEqualsEqualsToken ||
      op === ts.SyntaxKind.ExclamationEqualsEqualsToken ||
      op === ts.SyntaxKind.EqualsEqualsToken ||
      op === ts.SyntaxKind.ExclamationEqualsToken
    );
  }
  if (ts.isCaseClause(p)) return true;
  if (ts.isElementAccessExpression(p)) return p.argumentExpression === node;
  if (ts.isPropertyAssignment(p)) return p.name === node;
  return false;
}

/**
 * Rule-3-only exemption (a): the literal is a discriminator/id, not prose — the value of a
 * property named `key`/`id`/`type`/`name` (exact), or a compound identifier ending in `Id`
 * (`barTestId`, `rowId`). Scoped to object-literal PropertyAssignment initializers only, so
 * it never touches JSX attributes (those go through rules 1/2, unaffected).
 */
const NON_DISPLAY_PROP_EXACT = new Set(['key', 'id', 'type', 'name']);
function isNonDisplayPropertyValue(node) {
  const p = node.parent;
  if (!p || !ts.isPropertyAssignment(p) || p.initializer !== node) return false;
  if (!ts.isIdentifier(p.name) && !ts.isStringLiteral(p.name)) return false;
  const propName = p.name.text;
  return NON_DISPLAY_PROP_EXACT.has(propName) || /Id$/.test(propName);
}

/**
 * Rule-3-only exemption (b): `'value' as SomeType` — nobody type-asserts a display string;
 * the cast is the tell that this is a typed discriminator/enum value.
 */
function isAsCastValue(node) {
  const p = node.parent;
  return !!p && ts.isAsExpression(p) && p.expression === node;
}

/**
 * Rule-3-only exemption (c): a single bare lowercase/kebab/snake token with no whitespace
 * (`drawdown`, `risk-bar-sharpe`, `not_found`). Real prose almost always has a space, a
 * capital letter, or punctuation; deliberately narrow so it does NOT catch two-word phrases,
 * capitalized words, or anything with trailing punctuation.
 */
const BARE_IDENTIFIER = /^[a-z][a-z0-9_-]*$/;
function isBareIdentifierLiteral(text) {
  return BARE_IDENTIFIER.test(text.trim());
}

/** `forEachChild` aborts as soon as the callback returns anything truthy. Every visitor
 *  below must therefore return undefined — hence the braces. */
function eachChild(node, fn) {
  node.forEachChild((child) => {
    fn(child);
  });
}

/** Does the opening element carry a className that marks it as a Material Symbols icon? */
function isIconElement(node) {
  const opening = ts.isJsxElement(node)
    ? node.openingElement
    : ts.isJsxSelfClosingElement(node)
      ? node
      : null;
  if (!opening) return false;
  return opening.attributes.properties.some(
    (p) =>
      ts.isJsxAttribute(p) &&
      attrName(p) === 'className' &&
      p.initializer &&
      p.initializer.getText().includes('material-symbols'),
  );
}

function scanFile(relPath) {
  const abs = join(ROOT, relPath);
  if (!existsSync(abs)) {
    findings.push({ file: relPath, line: 0, rule: 'missing-file', text: 'listed in i18n-converted-files.json but not on disk' });
    return;
  }
  const source = readFileSync(abs, 'utf8');
  const sf = ts.createSourceFile(abs, source, ts.ScriptTarget.ES2022, true, ts.ScriptKind.TSX);

  const report = (node, rule, text) => {
    const { line } = sf.getLineAndCharacterOfPosition(node.getStart(sf));
    findings.push({ file: relPath, line: line + 1, rule, text: text.trim().replace(/\s+/g, ' ') });
  };

  /**
   * @param inAttr        inside any JSX attribute value (suppresses rule 3)
   * @param inChildExpr   inside a JSX *child* expression container (enables rule 3)
   * @param inT           inside a `t(...)` call (suppresses everything)
   * @param inIcon        inside a material-symbols element (suppresses rules 1 and 3)
   */
  function walk(node, inAttr, inChildExpr, inT, inIcon) {
    // Rule 1 — JSX text nodes.
    if (ts.isJsxText(node)) {
      if (!inIcon && !isAllowed(node.text)) report(node, 'jsx-text', node.text);
      return;
    }

    // Rule 2 — enforced attributes.
    if (ts.isJsxAttribute(node)) {
      const name = attrName(node);
      if (ENFORCED_ATTRS.has(name) && node.initializer) {
        for (const { n, text } of collectStatic(node.initializer, false)) {
          if (!isAllowed(text)) report(n, `attr:${name}`, text);
        }
      }
      // Descend for nested JSX only; literals below are handled (or intentionally not) above.
      eachChild(node, (c) => walk(c, true, false, inT, inIcon));
      return;
    }

    // Rule 3 — literals in JSX child expression containers.
    if (
      inChildExpr &&
      !inAttr &&
      !inT &&
      !inIcon &&
      (ts.isStringLiteral(node) || ts.isNoSubstitutionTemplateLiteral(node))
    ) {
      if (
        !isAllowed(node.text) &&
        !isMachineValue(node) &&
        !isNonDisplayPropertyValue(node) &&
        !isAsCastValue(node) &&
        !isBareIdentifierLiteral(node.text)
      ) {
        report(node, 'child-expr-literal', node.text);
      }
    }

    const nextIcon = inIcon || isIconElement(node);
    const nextT = inT || isTranslationCall(node);
    const opensChildExpr =
      ts.isJsxExpression(node) &&
      node.parent &&
      (ts.isJsxElement(node.parent) || ts.isJsxFragment(node.parent));

    eachChild(node, (c) =>
      walk(c, inAttr, opensChildExpr ? true : inChildExpr, nextT, nextIcon),
    );
  }

  /** Static strings reachable from an attribute initializer (literal, ternary branches,
   *  template heads/tails), excluding anything inside a `t(...)` call. */
  function collectStatic(node, inT, out = []) {
    if (isTranslationCall(node)) inT = true;
    if (!inT && !isMachineValue(node)) {
      if (ts.isStringLiteral(node) || ts.isNoSubstitutionTemplateLiteral(node)) {
        out.push({ n: node, text: node.text });
      } else if (ts.isTemplateHead(node) || ts.isTemplateMiddle(node) || ts.isTemplateTail(node)) {
        out.push({ n: node.parent ?? node, text: node.text });
      }
    }
    eachChild(node, (c) => collectStatic(c, inT, out));
    return out;
  }

  walk(sf, false, false, false, false);
}

for (const f of files) scanFile(f);

if (findings.length) {
  console.error('i18n ratchet FAILED — raw user-visible literals in converted files\n');
  let current = null;
  for (const f of findings) {
    if (f.file !== current) {
      current = f.file;
      console.error(`  ${current}`);
    }
    console.error(`    ✗ ${String(f.line).padStart(4)}  [${f.rule}]  ${JSON.stringify(f.text)}`);
  }
  console.error(
    `\n${findings.length} literal(s) in ${new Set(findings.map((f) => f.file)).size} file(s).` +
      `\nWrap them with t('<namespace>:<section>.<element>') and add the key to BOTH` +
      `\nsrc/i18n/locales/en/ and src/i18n/locales/zh-CN/ — the EN value must be` +
      `\nbyte-identical to the literal you removed.`,
  );
  process.exit(1);
}

console.log(`i18n ratchet passed — ${files.length} converted file(s), 0 raw literals.`);
