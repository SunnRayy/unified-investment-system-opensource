import React, { ReactElement, ReactNode } from 'react';
import { render as rtlRender, RenderOptions } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { AuthProvider } from './src/context/useAuth';
import { ThemeProvider } from './src/theme/useTheme';
import { PortfolioFilterProvider } from './src/context/usePortfolioFilter';
import { CurrencyProvider } from './src/context/useCurrency';
import { LanguageProvider } from './src/context/useLanguage';
import { DemoModeProvider } from './src/context/useDemoMode';

// Mirrors the real provider tree exactly (index.tsx's AuthProvider >
// ThemeProvider > PortfolioFilterProvider, then App.tsx's CurrencyProvider >
// LanguageProvider > DemoModeProvider > Router) since App.tsx owns its own
// Router and page/component tests render below that level. Any component
// under test that calls useAuth/useTheme/usePortfolioFilter/useCurrency/
// useLanguage/useDemoMode/useNavigate needs this wrapper, not
// @testing-library/react's bare `render()`.
//
// Exported as `render` (not `renderWithProviders`) so existing test files
// only need to change their import source, not every call site.
function AllProviders({ children, initialEntries }: { children: ReactNode; initialEntries?: string[] }) {
  return (
    <AuthProvider>
      <ThemeProvider>
        <PortfolioFilterProvider>
          <CurrencyProvider>
            <LanguageProvider>
              <DemoModeProvider>
                <MemoryRouter initialEntries={initialEntries}>
                  {children}
                </MemoryRouter>
              </DemoModeProvider>
            </LanguageProvider>
          </CurrencyProvider>
        </PortfolioFilterProvider>
      </ThemeProvider>
    </AuthProvider>
  );
}

export function render(
  ui: ReactElement,
  options?: Omit<RenderOptions, 'wrapper'> & { initialEntries?: string[] }
) {
  const { initialEntries, ...renderOptions } = options ?? {};
  return rtlRender(ui, {
    wrapper: ({ children }) => <AllProviders initialEntries={initialEntries}>{children}</AllProviders>,
    ...renderOptions,
  });
}

// Explicit re-export (not `export *`) — a wildcard re-export of the same
// name as a local export is ambiguous across bundlers/transpilers, and it
// silently lost to @testing-library/react's own `render` here.
export {
  screen,
  waitFor,
  fireEvent,
  within,
  cleanup,
  act,
  renderHook,
} from '@testing-library/react';
