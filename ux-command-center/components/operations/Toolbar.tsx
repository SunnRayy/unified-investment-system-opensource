import React from 'react';
import { Icon } from './Icon';

interface ToolbarProps {
  children: React.ReactNode;
  style?: React.CSSProperties;
}

export function Toolbar({ children, style }: ToolbarProps) {
  return (
    <div style={{
      display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap',
      padding: 14,
      background: 'var(--color-border-soft)',
      border: '1px solid var(--color-border)',
      borderRadius: 10,
      ...style,
    }}>
      {children}
    </div>
  );
}

interface SearchInputProps {
  value: string;
  onChange: (v: string) => void;
  placeholder?: string;
  width?: number | string;
}

export function SearchInput({ value, onChange, placeholder, width = 220 }: SearchInputProps) {
  return (
    <div style={{
      display: 'inline-flex', alignItems: 'center', gap: 8,
      padding: '6px 10px', width,
      background: 'var(--color-card)',
      border: '1px solid var(--color-border)',
      borderRadius: 8,
    }}>
      <Icon name="search" size={14} color="var(--color-fg-4)" />
      <input
        value={value}
        onChange={(e) => onChange(e.target.value)}
        placeholder={placeholder}
        style={{
          flex: 1, border: 'none', outline: 'none', background: 'transparent',
          fontSize: 12, color: 'var(--color-fg-1)', fontFamily: 'var(--font-sans)',
        }}
      />
    </div>
  );
}

interface SelectOption {
  value: string;
  label: string;
}

interface OpsSelectProps {
  value: string;
  onChange: (v: string) => void;
  options: (string | SelectOption)[];
  label?: string;
  width?: number | string;
}

const DROPDOWN_SVG = `url("data:image/svg+xml;utf8,<svg xmlns='http://www.w3.org/2000/svg' width='12' height='12' viewBox='0 0 24 24' fill='%2394a3b8'><path d='M7 10l5 5 5-5z'/></svg>")`;

export function OpsSelect({ value, onChange, options, label, width }: OpsSelectProps) {
  return (
    <div style={{ display: 'inline-flex', alignItems: 'center', gap: 6 }}>
      {label && <span style={{ fontSize: 11, color: 'var(--color-fg-4)', fontFamily: 'var(--font-mono)' }}>{label}</span>}
      <select
        value={value}
        onChange={(e) => onChange(e.target.value)}
        style={{
          padding: '6px 28px 6px 10px',
          background: 'var(--color-card)',
          border: '1px solid var(--color-border)',
          borderRadius: 8,
          fontSize: 12, color: 'var(--color-fg-1)',
          fontFamily: 'var(--font-sans)',
          cursor: 'pointer', outline: 'none',
          appearance: 'none',
          backgroundImage: DROPDOWN_SVG,
          backgroundRepeat: 'no-repeat',
          backgroundPosition: 'right 8px center',
          width,
        }}
      >
        {options.map((o) => {
          const val = typeof o === 'string' ? o : o.value;
          const lbl = typeof o === 'string' ? o : o.label;
          return <option key={val} value={val}>{lbl}</option>;
        })}
      </select>
    </div>
  );
}
