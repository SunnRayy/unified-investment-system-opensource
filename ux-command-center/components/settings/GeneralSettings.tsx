import React from 'react';
import { AccountManager } from './AccountManager';
import { LanguageCard } from './LanguageCard';
import { CurrencyCard } from './CurrencyCard';
import { IncludeIlliquidCard } from './IncludeIlliquidCard';
import { DemoModeCard } from './DemoModeCard';

export function GeneralSettings() {
  return (
    <div className="space-y-6 p-6">
      <AccountManager />
      <LanguageCard />
      <CurrencyCard />
      <IncludeIlliquidCard />
      <DemoModeCard />
    </div>
  );
}
