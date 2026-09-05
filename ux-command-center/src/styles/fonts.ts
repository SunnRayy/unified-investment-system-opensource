/**
 * Self-hosted webfonts — no runtime network dependency.
 *
 * These were loaded from fonts.googleapis.com until 2026-08-30. That worked on
 * a normal desktop and failed completely on the networks a self-hosted app is
 * most likely to run on: a homelab behind egress filtering, a corporate
 * network, an air-gapped box. The failure was not subtle — Material Symbols
 * renders as its *ligature text*, so every icon in the UI became the literal
 * word `dashboard`, `trending_up`, `sync`. Verified in a network-restricted
 * environment, not assumed.
 *
 * The rest of this codebase already treats "the network might not be there" as
 * a first-class case — the FX rate falls back to a constant and labels itself
 * FALLBACK on screen. Fonts had no such fallback, and a font is worse than a
 * rate: there is no degraded mode, the interface is simply unreadable.
 *
 * Weight selection mirrors exactly what the old Google Fonts URL requested, so
 * nothing renders differently:
 *   Inter        300, 400, 500, 600, 700
 *   Roboto Mono  400, 500
 *
 * `latin-*` subsets are deliberate. Neither face carries CJK glyphs, so
 * Chinese text has always resolved through the system-font stack in
 * `colors_and_type.css`; shipping the full multi-subset files would add weight
 * that no glyph ever comes from.
 *
 * Material Symbols uses the `fill` variable axis (1.1 MB), not `full` (4.0 MB).
 * The app declares four axes but only ever *varies* FILL, between the 0 and 1
 * of `.material-symbols-outlined` and `.filled-icon`; wght/GRAD/opsz are pinned
 * at 400/0/24, which are the defaults. The three extra axes would cost 2.8 MB
 * to carry values nothing ever changes. If a future design does vary weight or
 * optical size, switch this one import to `/full.css`.
 */

import '@fontsource/inter/latin-300.css';
import '@fontsource/inter/latin-400.css';
import '@fontsource/inter/latin-500.css';
import '@fontsource/inter/latin-600.css';
import '@fontsource/inter/latin-700.css';

import '@fontsource/roboto-mono/latin-400.css';
import '@fontsource/roboto-mono/latin-500.css';

import '@fontsource-variable/material-symbols-outlined/fill.css';
