import React from 'react';
import { BrowserRouter as Router, Routes, Route } from 'react-router-dom';
import { Layout } from './components/Layout';
import { useAuth } from './src/context/useAuth';
import { LoginPage } from './pages/LoginPage';
import { DemoModeProvider } from './src/context/useDemoMode';
import { CurrencyProvider } from './src/context/useCurrency';
import { LanguageProvider } from './src/context/useLanguage';
import { Dashboard } from './pages/Dashboard';
import { RiskMatrix } from './pages/RiskMatrix';
import { Performance } from './pages/Performance';
import { WealthOS } from './pages/WealthOS';
import { Compass } from './pages/Compass';
import { Audit } from './pages/Audit';
import { Verification } from './pages/Verification';
import { DecisionHub } from './pages/DecisionHub';
import { Taxonomy } from './pages/Taxonomy';
import { RiskProfiles } from './pages/RiskProfiles';
import { TransactionBrowser } from './pages/TransactionBrowser';
import { ImportWorkbench } from './pages/ImportWorkbench';
import { AssetAudit } from './pages/AssetAudit';
import { AssetClassAudit } from './pages/AssetClassAudit';
import { AssetCaseFile } from './pages/AssetCaseFile';
import { TierAudit } from './pages/TierAudit';
import { ClassificationRules } from './pages/ClassificationRules';
import { BalanceSheet } from './pages/BalanceSheet';
import { IncomeExpense } from './pages/IncomeExpense';
import { Analytics } from './pages/Analytics';
import { MarketSentiment } from './pages/MarketSentiment';
import { StrategyAlignment } from './pages/StrategyAlignment';
import { AIAdvisor } from './pages/AIAdvisor';
import { Settings } from './pages/Settings';
import { Valuation } from './pages/Valuation';
import { ValueTrapReviews } from './pages/ValueTrapReviews';
import { CashFlowClassification } from './pages/CashFlowClassification';
import { MonthlyAttribution } from './pages/MonthlyAttribution';

function App() {
  const { isAuthenticated } = useAuth();

  if (!isAuthenticated) {
    return <LoginPage />;
  }

  return (
    <CurrencyProvider>
    <LanguageProvider>
    <DemoModeProvider>
    <Router>
      <Layout>
        <Routes>
          <Route path="/" element={<Dashboard />} />
          <Route path="/risk" element={<RiskMatrix />} />
          <Route path="/performance" element={<Performance />} />
          <Route path="/wealth" element={<WealthOS />} />
          <Route path="/compass" element={<Compass />} />
          <Route path="/audit" element={<Audit />} />
          <Route path="/verify" element={<Verification />} />
          <Route path="/decisions" element={<DecisionHub />} />
          <Route path="/taxonomy" element={<Taxonomy />} />
          <Route path="/risk-profiles" element={<RiskProfiles />} />
          <Route path="/transactions" element={<TransactionBrowser />} />
          <Route path="/import" element={<ImportWorkbench />} />
          <Route path="/asset-class-audit" element={<AssetClassAudit />} />
          <Route path="/asset-audit" element={<AssetAudit />} />
          <Route path="/asset-case-file" element={<AssetCaseFile />} />
          <Route path="/tier-audit" element={<TierAudit />} />
          <Route path="/classification-rules" element={<ClassificationRules />} />
          <Route path="/balance-sheet" element={<BalanceSheet />} />
          <Route path="/income-expense" element={<IncomeExpense />} />
          <Route path="/analytics" element={<Analytics />} />
          <Route path="/market-sentiment" element={<MarketSentiment />} />
          <Route path="/strategy" element={<StrategyAlignment />} />
          <Route path="/ai-advisor" element={<AIAdvisor />} />
          <Route path="/settings" element={<Settings />} />
          <Route path="/ai-models" element={<Settings />} />
          <Route path="/ai-prompts" element={<Settings />} />
          <Route path="/data-sources" element={<Settings />} />
          <Route path="/valuation" element={<Valuation />} />
          <Route path="/value-trap-reviews" element={<ValueTrapReviews />} />
          <Route path="/cash-flow-classification" element={<CashFlowClassification />} />
          <Route path="/monthly-attribution" element={<MonthlyAttribution />} />
        </Routes>
      </Layout>
    </Router>
    </DemoModeProvider>
    </LanguageProvider>
    </CurrencyProvider>
  );
}

export default App;
