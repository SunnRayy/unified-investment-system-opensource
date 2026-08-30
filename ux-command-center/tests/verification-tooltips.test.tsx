import React from 'react';
import { describe, expect, it } from 'vitest';
import { render, screen } from '@testing-library/react';

import { AdoptionTooltip, ComparisonTooltip } from '../pages/Verification';

describe('Verification chart tooltips', () => {
  it('renders adoption tooltip with shared layout and formatted rate', () => {
    render(
      <AdoptionTooltip
        active
        label="2026-03-01"
        payload={[{ value: 80 }]}
      />,
    );

    expect(screen.getByText('2026-03')).toBeInTheDocument();
    expect(screen.getByText('Adoption Rate')).toBeInTheDocument();
    expect(screen.getByText('80.0%')).toBeInTheDocument();
  });

  it('renders comparison tooltip without the default blank series label', () => {
    render(
      <ComparisonTooltip
        active
        label="Portfolio"
        payload={[{ value: 6.2199 }]}
      />,
    );

    expect(screen.getByText('Portfolio')).toBeInTheDocument();
    expect(screen.getByText('Return')).toBeInTheDocument();
    expect(screen.getByText('6.2%')).toBeInTheDocument();
  });
});
