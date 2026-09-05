import React from 'react';
import { describe, expect, it } from 'vitest';
import { render, screen } from '../test-utils';
import { MemoryRouter } from 'react-router-dom';

import { Layout } from '../components/Layout';
import { ThemeProvider } from '../src/theme/useTheme';
import { PortfolioFilterProvider } from '../src/context/usePortfolioFilter';

describe('layout navigation information architecture', () => {
  it('places AI Advisors between Reports and Operations and applies renamed operation labels', () => {
    const { container } = render(
      <ThemeProvider>
        <PortfolioFilterProvider>
          <Layout>
            <div>content</div>
          </Layout>
        </PortfolioFilterProvider>
      </ThemeProvider>
    );

    const sectionTitles = Array.from(container.querySelectorAll('nav p'))
      .map((node) => node.textContent?.trim() || '')
      .filter(Boolean);

    const portfolioIndex = sectionTitles.indexOf('Portfolio');
    const reportsIndex = sectionTitles.indexOf('Reports');
    const aiAdvisorsIndex = sectionTitles.indexOf('AI Advisors');
    const operationsIndex = sectionTitles.indexOf('Operations');

    expect(portfolioIndex).toBeGreaterThanOrEqual(0);
    expect(reportsIndex).toBeGreaterThanOrEqual(0);
    expect(aiAdvisorsIndex).toBeGreaterThanOrEqual(0);
    expect(operationsIndex).toBeGreaterThanOrEqual(0);
    expect(portfolioIndex).toBeLessThan(reportsIndex);
    expect(reportsIndex).toBeLessThan(aiAdvisorsIndex);
    expect(aiAdvisorsIndex).toBeLessThan(operationsIndex);

    expect(screen.getByText('Review Center')).toBeInTheDocument();
    expect(screen.getByText('Decision Hub')).toBeInTheDocument();

    const operationsLabels = Array.from(container.querySelectorAll('nav span.text-sm'))
      .map((node) => node.textContent?.trim() || '')
      .filter(Boolean);
    const classAuditIndex = operationsLabels.indexOf('Asset Class Audit');
    const assetAuditIndex = operationsLabels.indexOf('Asset Audit');
    const caseFilesIndex = operationsLabels.indexOf('Asset Case Files');

    expect(classAuditIndex).toBeGreaterThanOrEqual(0);
    expect(assetAuditIndex).toBeGreaterThanOrEqual(0);
    expect(caseFilesIndex).toBeGreaterThanOrEqual(0);
    expect(classAuditIndex).toBeLessThan(assetAuditIndex);
    expect(assetAuditIndex).toBeLessThan(caseFilesIndex);

    expect(screen.getByText('Transactions')).toBeInTheDocument();
    expect(screen.getByText('Sync History')).toBeInTheDocument();

    expect(screen.queryByText('Transaction Evidence')).not.toBeInTheDocument();
    expect(screen.queryByText('Sync / Import History')).not.toBeInTheDocument();
  });
});
