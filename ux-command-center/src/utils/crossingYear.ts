/**
 * crossingYear — finds where an annual value path first reaches a target,
 * by linear interpolation between the two bracketing annual points.
 *
 * SCOPE (narrowed in V7.7.0/W-3 — this file no longer backs the "likely
 * range"): the ONLY remaining caller is the Forecast fan chart's median
 * crossing MARKER, which must sit on the drawn p50 curve. That is chart
 * geometry, not projection math.
 *
 * The "likely range" now comes from the backend —
 * `GET /forecast/levers` -> `base.crossing_years`, computed by
 * `src.financial_analysis.projection_defaults.crossing_time_percentiles`,
 * which solves P(t) = p for t directly. Because P(t) is monotone in t,
 * p25 < p50 < p75 holds by construction, which is why the old
 * percentile-of-VALUE-path inversion (P75 supplying the LOW year) and the
 * ADR-026 "ordering trap" caveat are both gone.
 *
 * Do NOT reintroduce a range derivation here: one projection formula, in
 * one language, in one place (ADR-026 + its 2026-07-26 amendment).
 */

/**
 * Find the first year `years[i]` at which `values[i]` reaches `target`,
 * linearly interpolating between the two bracketing annual points.
 *
 * - Already at/above target at the first point → returns `years[0]`.
 * - Never crosses within the horizon → returns `null` (caller renders `>Ny`,
 *   never clamps, never fabricates a number).
 * - Malformed input (mismatched array lengths, empty arrays, non-finite
 *   values, or a non-increasing `years` array — which would make the
 *   interpolation denominator zero or negative) → returns `null`.
 */
export function deriveCrossingYear(
    years: number[],
    values: number[],
    target: number,
): number | null {
    if (!Array.isArray(years) || !Array.isArray(values)) return null;
    if (years.length === 0 || values.length === 0) return null;
    if (years.length !== values.length) return null;
    if (!Number.isFinite(target)) return null;

    for (let i = 0; i < years.length; i++) {
        if (!Number.isFinite(years[i]) || !Number.isFinite(values[i])) return null;
        // years must be strictly increasing — a duplicate/decreasing year
        // would make the interpolation denominator zero or negative.
        if (i > 0 && years[i] <= years[i - 1]) return null;
    }

    if (values[0] >= target) return years[0];

    for (let i = 1; i < years.length; i++) {
        const prevValue = values[i - 1];
        const currValue = values[i];
        if (currValue >= target) {
            const valueDenom = currValue - prevValue;
            if (!Number.isFinite(valueDenom) || valueDenom <= 0) return null;
            const yearDenom = years[i] - years[i - 1];
            if (!Number.isFinite(yearDenom) || yearDenom <= 0) return null;
            const frac = (target - prevValue) / valueDenom;
            return years[i - 1] + frac * yearDenom;
        }
    }

    return null;
}
