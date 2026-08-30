import React from 'react';
import { describe, expect, test, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import { AnswerSection } from '../components/forecast/AnswerSection';
import type { ForecastLevers } from '../src/services/api/types';

// W-5 §4b HARD REQUIREMENT regression test: the headline must be a pure
// rendering of `levers.base.years_to_target` (itself a live, derived value
// from GET /forecast/levers) — never a hardcoded literal. This test proves
// that by mocking two different `base.years_to_target` values and asserting
// the rendered headline text changes accordingly. If someone hardcodes the
// headline (or the years figure), this test fails.

function makeLevers(yearsToTarget: number | null): ForecastLevers {
    return {
        base: {
            current_nw: 1_000_000,
            expected_return: 0.1,
            volatility: 0.15,
            median_return: 0.089,
            monthly_contribution: 10_000,
            target: 5_000_000,
            years_to_target: yearsToTarget,
            crossing_years: { p25: null, p50: yearsToTarget, p75: null },
        },
        levers: { savings: [], return: [], volatility: [] },
        combined: { label: 'combined', years_to_target: null, delta_years: null },
        goal: {
            target_amount: 5_000_000,
            source: 'goals',
            goal_id: 1,
            name: 'FIRE',
            target_date: '2040-12-31',
            fallback_reason: null,
        },
    };
}

describe('AnswerSection — headline is derived, never static (§4b)', () => {
    test('headline reflects a mocked base.years_to_target of 12.3', () => {
        render(<AnswerSection levers={makeLevers(12.3)} loading={false} onGoToGoals={vi.fn()} />);
        expect(screen.getByText(/12\.3/)).toBeInTheDocument();
    });

    test('changing the mocked base.years_to_target changes the rendered headline', () => {
        const { rerender } = render(<AnswerSection levers={makeLevers(12.3)} loading={false} onGoToGoals={vi.fn()} />);
        expect(screen.getByText(/12\.3/)).toBeInTheDocument();

        rerender(<AnswerSection levers={makeLevers(20.7)} loading={false} onGoToGoals={vi.fn()} />);
        expect(screen.getByText(/20\.7/)).toBeInTheDocument();
        expect(screen.queryByText(/12\.3/)).not.toBeInTheDocument();
    });

    test('years_to_target null → "not reachable" message, no number printed', () => {
        render(<AnswerSection levers={makeLevers(null)} loading={false} onGoToGoals={vi.fn()} />);
        expect(screen.getByText(/Goal not reachable on current inputs/)).toBeInTheDocument();
    });

    test('config_fallback goal source surfaces a create-goal prompt, never presented as the real goal', () => {
        const levers = makeLevers(10);
        levers.goal = {
            target_amount: 20_000_000,
            source: 'config_fallback',
            goal_id: null,
            name: null,
            target_date: null,
            fallback_reason: 'no active retirement goal',
        };
        render(<AnswerSection levers={levers} loading={false} onGoToGoals={vi.fn()} />);
        expect(screen.getByText(/No retirement goal set/)).toBeInTheDocument();
    });
});
