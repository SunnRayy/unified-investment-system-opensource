import React from 'react';
import { describe, expect, it } from 'vitest';
import { render, screen } from '../test-utils';
import { MemoryRouter } from 'react-router-dom';
import { Layout } from '../components/Layout';
import { ThemeProvider } from '../src/theme/useTheme';

describe('app shell', () => {
  it('renders left navigation labels', () => {
    render(
      <ThemeProvider>
        <Layout>
          <div>content</div>
        </Layout>
      </ThemeProvider>
    );

    expect(screen.getByText('Unified Portfolio')).toBeInTheDocument();
    expect(screen.getByText('Risk Matrix')).toBeInTheDocument();
  });
});
