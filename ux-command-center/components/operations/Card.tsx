import React from 'react';

interface CardProps {
  children: React.ReactNode;
  padding?: number | string;
  style?: React.CSSProperties;
}

export function Card({ children, padding = 20, style }: CardProps) {
  return (
    <div style={{
      background: 'var(--color-card)',
      border: '1px solid var(--color-border)',
      borderRadius: 12,
      boxShadow: 'var(--shadow-sm)',
      padding,
      ...style,
    }}>
      {children}
    </div>
  );
}
