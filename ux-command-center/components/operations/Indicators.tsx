import React from 'react';

interface SourceChipProps {
  name: string;
  status?: string;
  count?: number;
}

export function SourceChip({ name, status = 'ok', count }: SourceChipProps) {
  const color = status === 'warning' ? 'var(--color-warning)'
              : status === 'error'   ? 'var(--color-danger)'
              :                        'var(--color-success)';
  return (
    <div style={{
      display: 'inline-flex', alignItems: 'center', gap: 8,
      padding: '6px 10px', borderRadius: 8,
      border: '1px solid var(--color-border)',
      background: 'var(--color-card)', fontSize: 11,
    }}>
      <span style={{ width: 6, height: 6, borderRadius: 3, background: color }} />
      <span style={{ fontFamily: 'var(--font-mono)', color: 'var(--color-fg-2)' }}>{name}</span>
      {count != null && <span style={{ color: 'var(--color-fg-4)', fontFamily: 'var(--font-mono)' }}>{count}</span>}
    </div>
  );
}

interface SevDotProps {
  sev: string;
}

export function SevDot({ sev }: SevDotProps) {
  const color = sev === 'high'   ? 'var(--color-danger)'
              : sev === 'review' ? 'var(--color-warning)'
              : sev === 'info'   ? 'var(--color-primary)'
              :                    'var(--color-success)';
  return <span style={{ width: 6, height: 6, borderRadius: 3, background: color, flexShrink: 0, display: 'inline-block' }} />;
}
