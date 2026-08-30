import React from 'react';

interface DriftRowProps {
  current: number;
  target: number;
  color?: string;
}

export function DriftRow({ current, target, color = 'var(--color-primary)' }: DriftRowProps) {
  const max = Math.max(current, target) * 1.4 || 1;
  const curW = (current / max) * 100;
  const tarX = (target / max) * 100;
  return (
    <div style={{ position: 'relative', height: 8, background: 'var(--color-border-soft)', borderRadius: 2 }}>
      <div style={{ position: 'absolute', left: 0, top: 0, height: '100%', width: `${curW}%`, background: color, borderRadius: 2 }} />
      <div style={{ position: 'absolute', left: `${tarX}%`, top: -2, height: 12, width: 2, background: 'var(--color-fg-2)' }} />
    </div>
  );
}

interface DriftGaugeProps {
  drift: number;
  tolerance?: number;
}

export function DriftGauge({ drift, tolerance = 5 }: DriftGaugeProps) {
  const max = Math.max(Math.abs(drift), tolerance) * 1.5;
  const half = max;
  const center = 50;
  const pct = (drift / half) * 50;
  const color = Math.abs(drift) >= tolerance
    ? 'var(--color-danger)'
    : Math.abs(drift) >= tolerance * 0.5 ? 'var(--color-warning)' : 'var(--color-success)';

  return (
    <div style={{ position: 'relative', height: 6, background: 'var(--color-border-soft)', borderRadius: 2 }}>
      <div style={{
        position: 'absolute',
        left: `${50 - (tolerance / max) * 50}%`,
        width: `${(2 * tolerance / max) * 50}%`,
        top: 0, height: '100%',
        background: 'var(--color-border)', opacity: 0.5,
      }} />
      <div style={{ position: 'absolute', left: '50%', top: -2, height: 10, width: 1, background: 'var(--color-fg-3)' }} />
      <div style={{
        position: 'absolute',
        left: drift >= 0 ? `${center}%` : `${center + pct}%`,
        width: `${Math.abs(pct)}%`,
        top: 0, height: '100%',
        background: color, borderRadius: 1,
      }} />
    </div>
  );
}
