import React, { createContext, useContext, useState, useEffect } from 'react';

const STORAGE_KEY = 'uis-include-non-rebalanceable';

interface PortfolioFilterContextType {
    includeNonRebalanceable: boolean;
    toggleNonRebalanceable: () => void;
}

const PortfolioFilterContext = createContext<PortfolioFilterContextType | undefined>(undefined);

export const PortfolioFilterProvider: React.FC<{ children: React.ReactNode }> = ({ children }) => {
    const [includeNonRebalanceable, setInclude] = useState(() => {
        const stored = localStorage.getItem(STORAGE_KEY);
        return stored === 'true'; // Default: false (exclude)
    });

    useEffect(() => {
        localStorage.setItem(STORAGE_KEY, String(includeNonRebalanceable));
    }, [includeNonRebalanceable]);

    const toggleNonRebalanceable = () => setInclude(prev => !prev);

    return (
        <PortfolioFilterContext.Provider value={{ includeNonRebalanceable, toggleNonRebalanceable }}>
            {children}
        </PortfolioFilterContext.Provider>
    );
};

export const usePortfolioFilter = () => {
    const ctx = useContext(PortfolioFilterContext);
    if (!ctx) throw new Error('usePortfolioFilter must be used within PortfolioFilterProvider');
    return ctx;
};
