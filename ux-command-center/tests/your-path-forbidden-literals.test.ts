import { describe, expect, test } from 'vitest';
import fs from 'node:fs';
import path from 'node:path';

/**
 * Structural guard: no "Your Path" design-mock literal in frontend source.
 *
 * Plan: docs/plans/2026-07-26-your-path-design-implementation.md §4b / plan
 * task spec §4b — "no design constant may appear as a literal in src/ or
 * ux-command-center/". The mock (`Forecast - Your Path.dc.html`) hardcodes
 * illustrative NW0/GOAL/RUN_RATE/ER/VOL figures that happen to look
 * plausible but are NOT live data. Live values differ from the mock (ER
 * live ~0.108 vs mock 0.124; VOL ~0.1786 vs 0.176; run-rate ~44,665 vs
 * 44,632) — this guard exists precisely because those numbers are close
 * enough to pass a casual read.
 *
 * No pre-existing forbidden-literal guard was found under
 * ux-command-center/tests/ when W-5 was implemented; this is a new file, not
 * an extension of a prior one, despite the task spec's "extend the
 * existing" phrasing.
 *
 * Companion backend guard: tests/validation/test_your_path_forbidden_literals.py
 * (same literal set, scans src/**\/*.py).
 */

const FORBIDDEN_LITERALS = [
    '3269850',
    '20000000',
    '44632',
    '12.4',
    '17.6',
    '397980',
    '137600',
    '224900',
];

// Word-boundary-ish: not preceded/followed by a digit, letter, or dot, so
// "12.4" doesn't also flag inside "112.45" or "12.40".
const PATTERNS = FORBIDDEN_LITERALS.map(
    lit => new RegExp(`(?<![\\w.])${lit.replace('.', '\\.')}(?![\\w.])`),
);

// Scanned: production frontend source only — NOT ux-command-center/tests
// (fixtures legitimately construct mock objects with arbitrary numbers) and
// NOT ux-command-center/designs (vendored DS reference material, kept
// verbatim on purpose — see reports/app.css's own header comment).
const SCAN_DIRS = ['src', 'components', 'pages'];
const SCAN_EXTENSIONS = ['.ts', '.tsx'];

function repoRoot(): string {
    // ux-command-center/tests/your-path-forbidden-literals.test.ts -> ux-command-center
    return path.resolve(__dirname, '..');
}

function walk(dir: string, out: string[]): void {
    for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
        const full = path.join(dir, entry.name);
        if (entry.isDirectory()) {
            if (entry.name === 'node_modules') continue;
            walk(full, out);
        } else if (SCAN_EXTENSIONS.some(ext => entry.name.endsWith(ext))) {
            out.push(full);
        }
    }
}

function scan(): Array<{ file: string; line: number; text: string }> {
    const root = repoRoot();
    const violations: Array<{ file: string; line: number; text: string }> = [];
    for (const dir of SCAN_DIRS) {
        const abs = path.join(root, dir);
        if (!fs.existsSync(abs)) continue;
        const files: string[] = [];
        walk(abs, files);
        for (const file of files) {
            const text = fs.readFileSync(file, 'utf-8');
            const lines = text.split('\n');
            lines.forEach((line, idx) => {
                if (PATTERNS.some(p => p.test(line))) {
                    violations.push({ file: path.relative(root, file), line: idx + 1, text: line.trim() });
                }
            });
        }
    }
    return violations;
}

describe('"Your Path" design-mock literals are forbidden in frontend source (§4b)', () => {
    test('no forbidden literal appears under src/, components/, pages/', () => {
        const violations = scan();
        if (violations.length > 0) {
            const detail = violations.map(v => `  ${v.file}:${v.line}: ${v.text}`).join('\n');
            throw new Error(
                `'Your Path' design-mock literal found in frontend source — every forecast ` +
                `number must be live/derived, never copied from the illustrative mock ` +
                `(docs/design/2026-07-26-your-path.dc.html.md §4, plan §4b).\n` +
                `Forbidden literals: ${FORBIDDEN_LITERALS.join(', ')}\n` +
                `Violations:\n${detail}`,
            );
        }
        expect(violations).toHaveLength(0);
    });
});
