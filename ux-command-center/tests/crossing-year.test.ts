import { describe, expect, test } from 'vitest';
import { deriveCrossingYear } from '../src/utils/crossingYear';

describe('deriveCrossingYear', () => {
    test('mid-interval interpolation — crosses halfway between two annual points', () => {
        // year1=15, year2=25, target=20 -> exactly halfway -> 1.5
        const years = [0, 1, 2, 3];
        const values = [10, 15, 25, 30];
        expect(deriveCrossingYear(years, values, 20)).toBeCloseTo(1.5, 6);
    });

    test('exact hit — value lands exactly on target at an annual point', () => {
        const years = [0, 1, 2];
        const values = [10, 20, 30];
        expect(deriveCrossingYear(years, values, 20)).toBe(1);
    });

    test('already above target at the first point — returns years[0]', () => {
        const years = [5, 6, 7];
        const values = [25, 30, 35];
        expect(deriveCrossingYear(years, values, 20)).toBe(5);
    });

    test('never crosses within the horizon — returns null, never clamps', () => {
        const years = [0, 1, 2, 3];
        const values = [5, 8, 10, 12];
        expect(deriveCrossingYear(years, values, 20)).toBeNull();
    });

    test('guard: mismatched array lengths', () => {
        expect(deriveCrossingYear([0, 1, 2], [10, 20], 20)).toBeNull();
    });

    test('guard: empty arrays', () => {
        expect(deriveCrossingYear([], [], 20)).toBeNull();
    });

    test('guard: non-finite values in the path', () => {
        expect(deriveCrossingYear([0, 1, 2], [10, NaN, 30], 20)).toBeNull();
        expect(deriveCrossingYear([0, 1, 2], [10, Infinity, 30], 20)).toBeNull();
    });

    test('guard: non-finite target', () => {
        expect(deriveCrossingYear([0, 1, 2], [10, 20, 30], NaN)).toBeNull();
    });

    test('guard: zero/negative denominator — duplicate consecutive years', () => {
        // years[1] === years[0] would make the interpolation denominator zero.
        expect(deriveCrossingYear([0, 0, 1], [5, 25, 30], 20)).toBeNull();
    });

    test('guard: zero/negative denominator — non-increasing (out-of-order) years', () => {
        expect(deriveCrossingYear([0, 2, 1], [5, 25, 30], 20)).toBeNull();
    });
});
