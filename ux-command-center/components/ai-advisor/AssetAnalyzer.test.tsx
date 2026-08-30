import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '../../test-utils';
import { AssetAnalyzer } from './AssetAnalyzer';
import * as api from '../../src/services/api';

vi.mock('../../src/services/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../src/services/api')>();
  return {
    ...actual,
    searchAnalyzableAssets: vi.fn(),
    analyzeAsset: vi.fn(),
    getAnalysisHistory: vi.fn(),
  };
});

const mockSearchResults: api.AnalyzableAssetSearchResult[] = [
  { code: 'AAPL', name: 'Apple Inc.', in_portfolio: true, position_pct: 0.05 },
  { code: 'MSFT', name: 'Microsoft Corp', in_portfolio: false },
];

const mockAnalysisResult: api.AnalysisResult = {
  id: 1,
  asset_code: 'AAPL',
  asset_name: 'Apple Inc.',
  technical_signals: {
    signal_score: 72,
    trend_status: 'BULL',
    rsi_value: 55,
    rsi_status: 'neutral',
    macd_status: 'BULLISH',
    ma_alignment_score: 2,
    volume_status: 'HIGH',
    volume_ratio: 1.5,
    support_levels: [170, 165],
    resistance_levels: [185, 190],
  },
  llm_analysis: { timing_signal: 'buy', confidence: 0.75 },
  llm_analysis_markdown: '## AAPL Analysis\nBullish outlook.',
  portfolio_context: { portfolio_weight: 0.05 },
  model_used: 'gemini/gemini-2.5-flash',
  data_source: 'yfinance',
  triggered_by: 'user',
  created_at: '2026-03-27T10:00:00',
};

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(api.getAnalysisHistory).mockResolvedValue([]);
});

describe('AssetAnalyzer', () => {
  it('renders search input without crashing', () => {
    render(<AssetAnalyzer />);
    expect(
      screen.getByPlaceholderText(/Search asset by code or name/i)
    ).toBeInTheDocument();
  });

  it('does not call search for query shorter than 2 chars', async () => {
    render(<AssetAnalyzer />);
    const input = screen.getByPlaceholderText(/Search asset by code or name/i);
    fireEvent.change(input, { target: { value: 'A' } });
    // Wait a tick to ensure no async call was made
    await new Promise((r) => setTimeout(r, 50));
    expect(api.searchAnalyzableAssets).not.toHaveBeenCalled();
  });

  it('calls searchAnalyzableAssets on input >= 2 chars (debounced)', async () => {
    vi.mocked(api.searchAnalyzableAssets).mockResolvedValue(mockSearchResults);

    render(<AssetAnalyzer />);
    const input = screen.getByPlaceholderText(/Search asset by code or name/i);
    fireEvent.change(input, { target: { value: 'AA' } });

    await waitFor(() => {
      expect(api.searchAnalyzableAssets).toHaveBeenCalledWith('AA', expect.any(AbortSignal));
    });

    // Results should appear in dropdown
    await waitFor(() => {
      expect(screen.getByText('Apple Inc.')).toBeInTheDocument();
    });
  });

  it('Analyze button disabled when no asset selected', () => {
    render(<AssetAnalyzer />);
    const analyzeButton = screen.getByRole('button', { name: /Analyze/i });
    expect(analyzeButton).toBeDisabled();
  });

  it('Analyze button calls analyzeAsset and shows result', async () => {
    vi.mocked(api.searchAnalyzableAssets).mockResolvedValue(mockSearchResults);
    vi.mocked(api.analyzeAsset).mockResolvedValue(mockAnalysisResult);
    vi.mocked(api.getAnalysisHistory).mockResolvedValue([
      {
        id: 1,
        asset_code: 'AAPL',
        asset_name: 'Apple Inc.',
        timing_signal: 'buy',
        confidence: 0.75,
        created_at: '2026-03-27T10:00:00',
        model_used: 'gemini/gemini-2.5-flash',
        data_source: 'yfinance',
      },
    ]);

    render(<AssetAnalyzer />);

    // Search
    const input = screen.getByPlaceholderText(/Search asset by code or name/i);
    fireEvent.change(input, { target: { value: 'AA' } });

    await waitFor(() => {
      expect(screen.getByText('Apple Inc.')).toBeInTheDocument();
    });

    // Select asset
    fireEvent.click(screen.getByText('Apple Inc.'));

    // Analyze button should now be enabled
    const analyzeButton = screen.getByRole('button', { name: /Analyze/i });
    expect(analyzeButton).not.toBeDisabled();

    // Click Analyze
    fireEvent.click(analyzeButton);

    await waitFor(() => {
      expect(api.analyzeAsset).toHaveBeenCalledWith('AAPL');
    });

    // Result should be shown
    await waitFor(() => {
      expect(screen.getByText('Technical Signals')).toBeInTheDocument();
    });

    // Markdown content
    await waitFor(() => {
      expect(screen.getByText(/Bullish outlook/i)).toBeInTheDocument();
    });
  });

  it('shows error message when analyzeAsset fails', async () => {
    vi.mocked(api.searchAnalyzableAssets).mockResolvedValue(mockSearchResults);
    vi.mocked(api.analyzeAsset).mockRejectedValue(new Error('Market data unavailable'));

    render(<AssetAnalyzer />);

    const input = screen.getByPlaceholderText(/Search asset by code or name/i);
    fireEvent.change(input, { target: { value: 'AA' } });

    await waitFor(() => {
      expect(screen.getByText('Apple Inc.')).toBeInTheDocument();
    });

    fireEvent.click(screen.getByText('Apple Inc.'));
    fireEvent.click(screen.getByRole('button', { name: /Analyze/i }));

    await waitFor(() => {
      expect(screen.getByText(/Market data unavailable/i)).toBeInTheDocument();
    });
  });
});
