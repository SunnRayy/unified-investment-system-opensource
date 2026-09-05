import './src/styles/fonts';   // self-hosted webfonts — must load before first paint
import React from 'react';
import ReactDOM from 'react-dom/client';
import App from './App';
import { ThemeProvider } from './src/theme/useTheme';
import { AuthProvider } from './src/context/useAuth';
import { PortfolioFilterProvider } from './src/context/usePortfolioFilter';

const rootElement = document.getElementById('root');
if (!rootElement) {
  throw new Error("Could not find root element to mount to");
}

const root = ReactDOM.createRoot(rootElement);
root.render(
  <React.StrictMode>
    <AuthProvider>
      <ThemeProvider>
        <PortfolioFilterProvider>
          <App />
        </PortfolioFilterProvider>
      </ThemeProvider>
    </AuthProvider>
  </React.StrictMode>
);
