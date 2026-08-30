import React from 'react';
import { describe, expect, it } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { OpsKpi } from '../components/operations/OpsKpi';
import { OpsTable } from '../components/operations/OpsTable';
import { StatusPill } from '../components/operations/Pill';
import { fmtCNY, fmtPct } from '../src/utils/formatMoney';

describe('OpsKpi', () => {
  it('renders label and value', () => {
    render(<OpsKpi label="HEALTH SCORE" value="92%" />);
    expect(screen.getByText('HEALTH SCORE')).toBeTruthy();
    expect(screen.getByText('92%')).toBeTruthy();
  });

  it('renders sub text when provided', () => {
    render(<OpsKpi label="OPEN CASES" value={4} sub="no change" />);
    expect(screen.getByText('no change')).toBeTruthy();
  });
});

describe('OpsTable', () => {
  const cols = [
    { label: 'Name', key: 'name' },
    { label: 'Status', key: 'status' },
  ];
  const rows = [
    { name: 'Schwab CSV', status: 'ok' },
    { name: 'CN Fund', status: 'warning' },
  ];

  it('renders column headers', () => {
    render(<OpsTable cols={cols} rows={rows} />);
    expect(screen.getByText('Name')).toBeTruthy();
    expect(screen.getByText('Status')).toBeTruthy();
  });

  it('renders row data', () => {
    render(<OpsTable cols={cols} rows={rows} />);
    expect(screen.getByText('Schwab CSV')).toBeTruthy();
    expect(screen.getByText('CN Fund')).toBeTruthy();
  });

  it('calls onRowClick when row clicked', async () => {
    let clicked: unknown = null;
    render(<OpsTable cols={cols} rows={rows} onRowClick={(r) => { clicked = r; }} />);
    await userEvent.click(screen.getByText('Schwab CSV'));
    expect((clicked as { name: string }).name).toBe('Schwab CSV');
  });
});

describe('StatusPill', () => {
  it('renders ok status', () => {
    render(<StatusPill status="ok" />);
    expect(screen.getByText('OK')).toBeTruthy();
  });

  it('renders warning status', () => {
    render(<StatusPill status="warning" />);
    expect(screen.getByText('Warning')).toBeTruthy();
  });

  it('accepts custom children', () => {
    render(<StatusPill status="ok">All Good</StatusPill>);
    expect(screen.getByText('All Good')).toBeTruthy();
  });
});

describe('formatters', () => {
  it('fmtCNY formats millions with compact flag', () => {
    expect(fmtCNY(1_500_000, { compact: true })).toBe('¥1.50M');
  });

  it('fmtCNY formats regular numbers', () => {
    expect(fmtCNY(1234)).toBe('¥1,234');
  });

  it('fmtPct formats positive with sign', () => {
    expect(fmtPct(5.5)).toBe('+5.5%');
  });

  it('fmtPct formats negative', () => {
    expect(fmtPct(-2.3)).toBe('−2.3%');
  });
});
