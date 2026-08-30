/**
 * i18next-parser config — Program BIL (ADR-028).
 *
 * `npm run i18n:extract` walks the source for `t('ns:section.element')` calls and adds any
 * key it finds to BOTH locale catalogs. New keys land with an EMPTY value, which
 * `scripts/i18n-parity-check.mjs` then fails on — so an extracted-but-untranslated key
 * cannot reach a green build. That is the intended workflow: extract, fill, check.
 *
 * It is NOT part of `i18n:check` and is NOT run in CI. It rewrites the catalogs, and a
 * script that rewrites the thing it is verifying has no business being a gate. Run it by
 * hand, then read the diff.
 *
 * `keepRemoved: true` on purpose: keys resolved dynamically (`t(item.labelKey)` in
 * `Layout.tsx` is exactly this shape) are invisible to a static parser, and without it an
 * extract run would silently delete every one of them.
 */
export default {
  locales: ['en', 'zh-CN'],
  input: [
    'App.tsx',
    'components/**/*.{ts,tsx}',
    'pages/**/*.{ts,tsx}',
    'src/**/*.{ts,tsx}',
    '!**/*.test.{ts,tsx}',
    '!src/i18n/**',
  ],
  output: 'src/i18n/locales/$LOCALE/$NAMESPACE.json',
  defaultNamespace: 'common',
  keySeparator: '.',
  namespaceSeparator: ':',
  defaultValue: '',
  createOldCatalogs: false,
  keepRemoved: true,
  sort: true,
  indentation: 2,
  lineEnding: 'lf',
  failOnWarnings: false,
  verbose: false,
};
