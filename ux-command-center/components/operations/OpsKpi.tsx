import React from 'react';
import { Icon } from './Icon';

interface OpsKpiProps {
  label: string;
  value: string | number;
  sub?: string;
  subColor?: string;
  accent?: string;
  icon?: string;
  dense?: boolean;
}

export function OpsKpi({ label, value, sub, subColor, accent, icon, dense = false }: OpsKpiProps) {
  return (
    <div style={{
      padding: dense ? 14 : 18,
      background: 'var(--color-card)',
      border: '1px solid var(--color-border)',
      borderRadius: 12,
      boxShadow: 'var(--shadow-sm)',
      display: 'flex', flexDirection: 'column', gap: 6, minWidth: 0,
      position: 'relative',
    }}>
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 8 }}>
        <span className="uis-eyebrow" style={{ fontSize: 10 }}>{label}</span>
        {icon && <Icon name={icon} size={14} color="var(--color-fg-4)" />}
      </div>
      <div style={{
        fontFamily: 'var(--font-mono)', fontWeight: 700,
        fontSize: dense ? 18 : 24, letterSpacing: '-0.01em',
        color: accent ?? 'var(--color-fg-1)',
      }}>
        {value}
      </div>
      {sub && (
        <div style={{ fontSize: 11, color: subColor ?? 'var(--color-fg-4)', fontFamily: 'var(--font-mono)' }}>
          {sub}
        </div>
      )}
    </div>
  );
}
