import React from 'react';

export interface ColDef<T = Record<string, unknown>> {
  label: string;
  key?: string;
  render?: (row: T) => React.ReactNode;
  align?: 'left' | 'right' | 'center';
  padX?: number;
  mono?: boolean;
  size?: number;
  color?: string;
  nowrap?: boolean;
  width?: number | string;
}

interface OpsTableProps<T = Record<string, unknown>> {
  cols: ColDef<T>[];
  rows: T[];
  onRowClick?: (row: T) => void;
  density?: 'compact' | 'dense' | 'comfy';
  rowKey?: (row: T) => string | number;
  selectedKey?: string | number | null;
}

export function OpsTable<T = Record<string, unknown>>({
  cols, rows, onRowClick, density = 'comfy', rowKey, selectedKey,
}: OpsTableProps<T>) {
  const cellY = density === 'compact' ? '8px' : density === 'dense' ? '10px' : '12px';
  return (
    <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 12 }}>
      <thead>
        <tr style={{ borderBottom: '1px solid var(--color-border)' }}>
          {cols.map((c, i) => (
            <th key={i} style={{
              textAlign: c.align ?? 'left',
              padding: `10px ${c.padX ?? 14}px`,
              fontSize: 10, fontWeight: 700,
              color: 'var(--color-fg-4)',
              textTransform: 'uppercase',
              letterSpacing: '0.06em',
              whiteSpace: 'nowrap',
              width: c.width,
            }}>
              {c.label}
            </th>
          ))}
        </tr>
      </thead>
      <tbody>
        {rows.map((r, i) => {
          const key = rowKey ? rowKey(r) : i;
          const selected = selectedKey != null && key === selectedKey;
          return (
            <tr
              key={key}
              onClick={() => onRowClick?.(r)}
              className="uis-row-hover"
              style={{
                borderBottom: '1px solid var(--color-border-soft)',
                cursor: onRowClick ? 'pointer' : 'default',
                background: selected ? 'var(--fill-primary-soft)' : 'transparent',
              }}
            >
              {cols.map((c, j) => (
                <td key={j} style={{
                  padding: `${cellY} ${c.padX ?? 14}px`,
                  textAlign: c.align ?? 'left',
                  fontFamily: c.mono ? 'var(--font-mono)' : 'var(--font-sans)',
                  fontSize: c.size ?? 12,
                  color: c.color ?? 'var(--color-fg-2)',
                  whiteSpace: c.nowrap ? 'nowrap' : 'normal',
                  verticalAlign: 'top',
                }}>
                  {c.render ? c.render(r) : String((r as Record<string, unknown>)[c.key ?? ''] ?? '')}
                </td>
              ))}
            </tr>
          );
        })}
      </tbody>
    </table>
  );
}
