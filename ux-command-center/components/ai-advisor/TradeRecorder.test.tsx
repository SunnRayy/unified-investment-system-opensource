import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { TradeRecorder } from './TradeRecorder';
import { api } from '../../src/services/api';

vi.mock('../../src/services/api', () => ({
  api: {
    listTrades: vi.fn(),
    createTrade: vi.fn(),
    deleteTrade: vi.fn(),
    searchAssets: vi.fn(),
    getStrategyMemos: vi.fn(),
  }
}));

describe('TradeRecorder', () => {
  const mockTrades = [
    {
      id: 1,
      log_date: '2026-03-25',
      asset_id: 'US_STK_AAPL',
      asset_name: 'Apple Inc',
      action: 'Buy' as const,
      price: 150.0,
      quantity: 10,
      amount: 1500.0,
      currency: 'USD',
      decision_reason: 'Good earnings',
      suggestion_source: 'manual',
      linked_memo_id: 7,
    },
    {
      id: 2,
      log_date: '2026-03-24',
      asset_id: 'CN_FUND_001',
      asset_name: 'China Fund',
      action: 'Sell' as const,
      price: null,
      quantity: null,
      amount: 5000.0,
      currency: 'CNY',
      decision_reason: null,
      suggestion_source: 'AIA',
      linked_memo_id: null,
    },
  ];

  const mockMemos = [
    {
      id: 7,
      date: '2026-03-18',
      title: 'Apple accumulation memo',
      bias: 'neutral' as const,
      directives: [],
      content: '# Apple accumulation memo',
    },
  ];

  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(api.getStrategyMemos).mockResolvedValue({ memos: mockMemos });
  });

  it('renders trade recorder form and initial empty list', async () => {
    vi.mocked(api.listTrades).mockResolvedValueOnce({ trades: [] });
    render(<TradeRecorder />);

    expect(screen.getAllByText('Record Trade').length).toBeGreaterThan(0);
    
    await waitFor(() => {
      expect(screen.getByText('No recent trades found.')).toBeInTheDocument();
    });
  });

  it('renders list of recent trades', async () => {
    let callCount = 0;
    vi.mocked(api.listTrades).mockImplementation(async () => {
      callCount++;
      return { trades: mockTrades };
    });
    render(<TradeRecorder />);

    await waitFor(() => {
      expect(screen.getByText('Apple Inc')).toBeInTheDocument();
      expect(screen.getByText('China Fund')).toBeInTheDocument();
    });

    // Check action badges (also tests form buttons)
    expect(screen.getAllByText('Buy').length).toBeGreaterThan(0);
    expect(screen.getAllByText('Sell').length).toBeGreaterThan(0);
  });

  it('searches for assets and selects one', async () => {
    vi.mocked(api.listTrades).mockResolvedValueOnce({ trades: [] });
    vi.mocked(api.searchAssets).mockResolvedValueOnce({
      assets: [{ asset_id: 'US_STK_MSFT', display_name: 'Microsoft Corp', asset_class: 'US Equity', base_currency: 'USD' }]
    });

    render(<TradeRecorder />);

    const searchInput = screen.getByPlaceholderText(/Search asset by name or code/i);
    
    // Type less than 2 chars should not trigger search
    fireEvent.change(searchInput, { target: { value: 'M' } });
    expect(api.searchAssets).not.toHaveBeenCalled();

    // Type 2+ chars
    fireEvent.change(searchInput, { target: { value: 'MSFT' } });
    
    // Fast-forward or wait for debounce (mocking timeout works or we just wait)
    await waitFor(() => {
      expect(api.searchAssets).toHaveBeenCalledWith('MSFT');
      expect(screen.getAllByText('Microsoft Corp').length).toBeGreaterThan(0);
    });

    // Select the asset
    fireEvent.click(screen.getAllByText('Microsoft Corp')[0]);

    // The input should now show the selected asset
    expect(searchInput).toHaveValue('Microsoft Corp (US_STK_MSFT)');
    expect(screen.getByLabelText('Currency')).toHaveValue('USD');
  });

  it('offers a custom asset fallback when no registry match exists', async () => {
    vi.mocked(api.listTrades).mockResolvedValueOnce({ trades: [] });
    vi.mocked(api.searchAssets).mockResolvedValueOnce({ assets: [] });

    render(<TradeRecorder />);

    const searchInput = screen.getByPlaceholderText(/Search asset by name or code/i);
    fireEvent.change(searchInput, { target: { value: 'NVDA' } });

    await waitFor(() => {
      expect(screen.getByText('+ Use "NVDA" as custom asset')).toBeInTheDocument();
    });

    fireEvent.click(screen.getByText('+ Use "NVDA" as custom asset'));

    expect(searchInput).toHaveValue('NVDA (NVDA)');
    expect(screen.getByLabelText('Currency')).toHaveValue('USD');
  });

  it('submits a new trade with currency override and explicit memo link', async () => {
    let listCallCount = 0;
    vi.mocked(api.listTrades).mockImplementation(async () => {
      listCallCount++;
      return { trades: listCallCount === 1 ? [] : [mockTrades[0]] };
    });

    vi.mocked(api.searchAssets).mockResolvedValueOnce({
      assets: [{ asset_id: 'US_STK_AAPL', display_name: 'Apple Inc', asset_class: 'US Equity', base_currency: 'USD' }]
    });

    vi.mocked(api.createTrade).mockResolvedValueOnce(mockTrades[0]);

    render(<TradeRecorder />);

    // 1. Search and select asset
    const searchInput = screen.getByPlaceholderText(/Search asset by name or code/i);
    fireEvent.change(searchInput, { target: { value: 'AAPL' } });
    
    await waitFor(() => {
      expect(screen.getAllByText('Apple Inc').length).toBeGreaterThan(0);
    });
    fireEvent.click(screen.getAllByText('Apple Inc')[0]);

    // 2. Fill in form
    // Action is Buy by default
    fireEvent.change(screen.getByPlaceholderText('Price'), { target: { value: '150' } });
    fireEvent.change(screen.getByPlaceholderText('Quantity'), { target: { value: '10' } });
    fireEvent.change(screen.getByPlaceholderText('Reason...'), { target: { value: 'Good earnings' } });
    fireEvent.change(screen.getByLabelText('Currency'), { target: { value: 'HKD' } });
    fireEvent.change(screen.getByLabelText('Related Memo (optional)'), { target: { value: 'Apple' } });

    await waitFor(() => {
      expect(screen.getByText('2026-03-18 — Apple accumulation memo')).toBeInTheDocument();
    });
    fireEvent.click(screen.getByText('2026-03-18 — Apple accumulation memo'));

    // Amount should auto-calc
    expect(screen.getByPlaceholderText('Amount')).toHaveValue('1500');

    // 3. Submit
    fireEvent.click(screen.getByRole('button', { name: /Record Trade/i }));

    await waitFor(() => {
      expect(api.createTrade).toHaveBeenCalledWith(expect.objectContaining({
        asset_id: 'US_STK_AAPL',
        action: 'Buy',
        price: 150,
        quantity: 10,
        amount: 1500,
        currency: 'HKD',
        decision_reason: 'Good earnings',
        memo_id: 7,
      }));
    });

    expect(await screen.findByText('Trade recorded. Linked to memo: Apple accumulation memo')).toBeInTheDocument();

    // 4. Verify list refreshes
    await waitFor(() => {
      expect(screen.getAllByText('Apple Inc').length).toBeGreaterThan(0);
      expect(screen.getAllByText('Apple accumulation memo').length).toBeGreaterThan(0);
    });
  });

  it('allows deleting a manual trade but hides button for non-manual', async () => {
    let listCallCount = 0;
    vi.mocked(api.listTrades).mockImplementation(async () => {
      listCallCount++;
      return { trades: listCallCount === 1 ? mockTrades : [mockTrades[1]] };
    });

    vi.mocked(api.deleteTrade).mockResolvedValueOnce();

    render(<TradeRecorder />);

    await waitFor(() => {
      expect(screen.getByText('Apple Inc')).toBeInTheDocument();
      expect(screen.getByText('China Fund')).toBeInTheDocument();
    });

    // There should only be one delete button because the second trade is 'AIA'
    const deleteButtons = screen.getAllByTitle('Delete trade');
    expect(deleteButtons.length).toBe(1);

    window.confirm = vi.fn().mockImplementation(() => true);
    fireEvent.click(deleteButtons[0]);

    expect(window.confirm).toHaveBeenCalled();
    expect(api.deleteTrade).toHaveBeenCalledWith(1);

    await waitFor(() => {
      expect(screen.queryByText('Apple Inc')).not.toBeInTheDocument();
      expect(screen.getByText('China Fund')).toBeInTheDocument();
    });
  });

  it('shows an inline error when trade save fails', async () => {
    vi.mocked(api.listTrades).mockResolvedValueOnce({ trades: [] });
    vi.mocked(api.searchAssets).mockResolvedValueOnce({
      assets: [{ asset_id: 'US_STK_AAPL', display_name: 'Apple Inc', asset_class: 'US Equity', base_currency: 'USD' }]
    });
    vi.mocked(api.createTrade).mockRejectedValueOnce(new Error('Create failed'));
    vi.spyOn(window, 'alert').mockImplementation(() => {});

    render(<TradeRecorder />);

    fireEvent.change(screen.getByPlaceholderText(/Search asset by name or code/i), { target: { value: 'AAPL' } });

    await waitFor(() => {
      expect(screen.getAllByText('Apple Inc').length).toBeGreaterThan(0);
    });
    fireEvent.click(screen.getAllByText('Apple Inc')[0]);
    fireEvent.change(screen.getByPlaceholderText('Price'), { target: { value: '150' } });
    fireEvent.change(screen.getByPlaceholderText('Quantity'), { target: { value: '10' } });
    fireEvent.click(screen.getByRole('button', { name: /Record Trade/i }));

    expect(await screen.findByText(/create failed/i)).toBeInTheDocument();
  });

  it('shows an inline error when trade delete fails', async () => {
    vi.mocked(api.listTrades).mockResolvedValueOnce({ trades: mockTrades });
    vi.mocked(api.deleteTrade).mockRejectedValueOnce(new Error('Delete failed'));
    window.confirm = vi.fn().mockImplementation(() => true);
    vi.spyOn(window, 'alert').mockImplementation(() => {});

    render(<TradeRecorder />);

    await waitFor(() => {
      expect(screen.getByText('Apple Inc')).toBeInTheDocument();
    });

    fireEvent.click(screen.getAllByTitle('Delete trade')[0]);

    expect(await screen.findByText(/delete failed/i)).toBeInTheDocument();
  });
});
