import React from 'react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import { Taxonomy } from '../pages/Taxonomy';
import { RiskProfiles } from '../pages/RiskProfiles';

// Mock the API
const taxonomyMocks = vi.hoisted(() => ({
    getClasses: vi.fn(),
    getRules: vi.fn(),
    createClass: vi.fn(),
    updateClass: vi.fn(),
    deleteClass: vi.fn(),
    runAutoTag: vi.fn(),
}));

const riskMocks = vi.hoisted(() => ({
    getProfiles: vi.fn(),
    getAllocations: vi.fn(),
    createProfile: vi.fn(),
    updateAllocations: vi.fn(),
    activateProfile: vi.fn(),
}));

vi.mock('../src/services/api', async (importOriginal) => {
    const actual = await importOriginal();
    return {
        ...actual,
        TaxonomyAPI: taxonomyMocks,
        RiskProfileAPI: riskMocks,
    };
});

describe('Management UI Smoke Tests', () => {
    beforeEach(() => {
        vi.clearAllMocks();
    });

    describe('Taxonomy Page', () => {
        it('renders and fetches data', async () => {
            taxonomyMocks.getClasses.mockResolvedValue([
                { id: 1, name: 'Equity', level: 0, children: [] }
            ]);
            taxonomyMocks.getRules.mockResolvedValue([]);

            render(<Taxonomy />);

            expect(screen.getByText('Taxonomy Management')).toBeInTheDocument();
            await waitFor(() => {
                expect(taxonomyMocks.getClasses).toHaveBeenCalled();
            });
            expect(screen.getByText('Equity')).toBeInTheDocument();
        });
    });

    describe('Risk Profiles Page', () => {
        it('renders and fetches profiles', async () => {
            riskMocks.getProfiles.mockResolvedValue([
                { id: 1, name: 'Balanced', is_active: true }
            ]);
            riskMocks.getAllocations.mockResolvedValue([]);

            render(<RiskProfiles />);

            expect(screen.getByText('Risk Profiles')).toBeInTheDocument();
            await waitFor(() => {
                expect(riskMocks.getProfiles).toHaveBeenCalled();
            });
            expect(screen.getByText('Balanced')).toBeInTheDocument();
        });
    });
});
