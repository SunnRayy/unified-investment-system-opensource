import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { MemoManager } from './MemoManager';
import { api } from '../../src/services/api';

// Mock the API calls
vi.mock('../../src/services/api', () => ({
  api: {
    getStrategyMemos: vi.fn(),
    createStrategyMemo: vi.fn(),
    updateStrategyMemo: vi.fn(),
    deleteStrategyMemo: vi.fn(),
    importMemosFromFiles: vi.fn(),
  }
}));

describe('MemoManager', () => {
  const mockMemos = [
    {
      id: 1,
      date: '2026-03-25',
      title: 'Weekly Strategy',
      bias: 'defensive' as const,
      directives: ['Reduce tech', 'Increase cash'],
      content: '# Weekly Strategy\n\nReduce tech.',
    },
    {
      id: 2,
      date: '2026-03-20',
      title: 'Market Update',
      bias: 'offensive' as const,
      directives: ['Buy dips'],
      content: '# Market Update\n\nBuy dips.',
    },
  ];

  beforeEach(() => {
    vi.clearAllMocks();
  });

  afterEach(() => {
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
  });

  it('renders loading state and then empty state when no memos', async () => {
    vi.mocked(api.getStrategyMemos).mockResolvedValueOnce({ memos: [] });
    
    render(<MemoManager />);
    
    expect(screen.getByText('Loading memos...')).toBeInTheDocument();
    
    await waitFor(() => {
      expect(screen.getByText('No memos found.')).toBeInTheDocument();
    });
  });

  it('renders list of memos after loading', async () => {
    vi.mocked(api.getStrategyMemos).mockResolvedValueOnce({ memos: mockMemos });
    
    render(<MemoManager />);
    
    await waitFor(() => {
      expect(screen.getByText('Weekly Strategy')).toBeInTheDocument();
      expect(screen.getByText('Market Update')).toBeInTheDocument();
    });

    // Check badges
    expect(screen.getByText('Defensive')).toBeInTheDocument();
    expect(screen.getByText('Offensive')).toBeInTheDocument();
  });

  it('allows creating a new memo', async () => {
    let callCount = 0;
    vi.mocked(api.getStrategyMemos).mockImplementation(async () => {
      callCount++;
      if (callCount === 1) return { memos: [] };
      return { memos: [mockMemos[0]] };
    });
    
    vi.mocked(api.createStrategyMemo).mockResolvedValueOnce(mockMemos[0]);
    
    render(<MemoManager />);
    
    await waitFor(() => {
      expect(screen.getByText('No memos found.')).toBeInTheDocument();
    });

    const textarea = screen.getByPlaceholderText(/Paste your LLM strategy memo here/i);
    fireEvent.change(textarea, { target: { value: '# Weekly Strategy\n\nReduce tech.' } });
    
    const saveButton = screen.getByRole('button', { name: /Save/i });
    fireEvent.click(saveButton);

    expect(api.createStrategyMemo).toHaveBeenCalledWith('# Weekly Strategy\n\nReduce tech.', undefined);

    await waitFor(() => {
      expect(screen.getByText('Weekly Strategy')).toBeInTheDocument();
    });
  });

  it('allows expanding a memo to see content', async () => {
    vi.mocked(api.getStrategyMemos).mockResolvedValueOnce({ memos: mockMemos });
    
    render(<MemoManager />);
    
    await waitFor(() => {
      expect(screen.getByText('Weekly Strategy')).toBeInTheDocument();
    });
    
    // Content should not be fully visible in expanded panel initially
    expect(screen.queryByTestId('memo-content-1')).not.toBeInTheDocument();
    
    // Click to expand
    const card = screen.getByText('Weekly Strategy');
    fireEvent.click(card);
    
    await waitFor(() => {
      expect(screen.getByTestId('memo-content-1')).toBeInTheDocument();
      expect(screen.getByText(/Reduce tech\./i)).toBeInTheDocument();
    });
  });

  it('allows deleting a memo', async () => {
    let callCount = 0;
    vi.mocked(api.getStrategyMemos).mockImplementation(async () => {
      callCount++;
      if (callCount === 1) return { memos: mockMemos };
      return { memos: [mockMemos[1]] };
    });
    
    vi.mocked(api.deleteStrategyMemo).mockResolvedValueOnce();

    render(<MemoManager />);
    
    await waitFor(() => {
      expect(screen.getByText('Weekly Strategy')).toBeInTheDocument();
    });

    window.confirm = vi.fn().mockImplementation(() => true);

    const deleteButtons = screen.getAllByTitle('Delete memo');
    fireEvent.click(deleteButtons[0]);

    expect(window.confirm).toHaveBeenCalled();
    expect(api.deleteStrategyMemo).toHaveBeenCalledWith(1);

    await waitFor(() => {
      expect(screen.queryByText('Weekly Strategy')).not.toBeInTheDocument();
      expect(screen.getByText('Market Update')).toBeInTheDocument();
    });
  });

  it('shows an inline error when saving fails', async () => {
    vi.mocked(api.getStrategyMemos).mockResolvedValueOnce({ memos: [] });
    vi.mocked(api.createStrategyMemo).mockRejectedValueOnce(new Error('Save failed'));
    vi.spyOn(window, 'alert').mockImplementation(() => {});

    render(<MemoManager />);

    await waitFor(() => {
      expect(screen.getByText('No memos found.')).toBeInTheDocument();
    });

    fireEvent.change(screen.getByPlaceholderText(/Paste your LLM strategy memo here/i), {
      target: { value: '# Weekly Strategy\n\nReduce tech.' },
    });
    fireEvent.click(screen.getByRole('button', { name: /Save Memo/i }));

    expect(await screen.findByText(/save failed/i)).toBeInTheDocument();
  });

  it('shows an inline error when deleting fails', async () => {
    vi.mocked(api.getStrategyMemos).mockResolvedValueOnce({ memos: mockMemos });
    vi.mocked(api.deleteStrategyMemo).mockRejectedValueOnce(new Error('Delete failed'));
    window.confirm = vi.fn().mockImplementation(() => true);
    vi.spyOn(window, 'alert').mockImplementation(() => {});

    render(<MemoManager />);

    await waitFor(() => {
      expect(screen.getByText('Weekly Strategy')).toBeInTheDocument();
    });

    fireEvent.click(screen.getAllByTitle('Delete memo')[0]);

    expect(await screen.findByText(/delete failed/i)).toBeInTheDocument();
  });

  it('shows fallback copy for imported memos without stored content', async () => {
    vi.mocked(api.getStrategyMemos).mockResolvedValueOnce({
      memos: [{ ...mockMemos[0], content: undefined }],
    });

    render(<MemoManager />);

    await waitFor(() => {
      expect(screen.getByText('Weekly Strategy')).toBeInTheDocument();
    });

    fireEvent.click(screen.getByText('Weekly Strategy'));

    expect(await screen.findByText(/Full content not available/i)).toBeInTheDocument();
  });

  it('shows import banner for memos without stored content and refreshes after import', async () => {
    let callCount = 0;
    vi.mocked(api.getStrategyMemos).mockImplementation(async () => {
      callCount += 1;
      if (callCount === 1) {
        return { memos: [{ ...mockMemos[0], content: undefined }] };
      }
      return { memos: [mockMemos[0]] };
    });
    vi.mocked(api.importMemosFromFiles).mockResolvedValueOnce({
      status: 'ok',
      created: 0,
      updated: 1,
      skipped: 0,
    });

    render(<MemoManager />);

    expect(await screen.findByText(/1 memos were imported from files/i)).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: /Import from files/i }));

    await waitFor(() => {
      expect(api.importMemosFromFiles).toHaveBeenCalledTimes(1);
      expect(screen.queryByText(/were imported from files/i)).not.toBeInTheDocument();
    });
  });

  it('allows editing a memo title and content', async () => {
    let callCount = 0;
    const updatedMemo = {
      ...mockMemos[0],
      title: 'Updated Weekly Strategy',
      content: '# Updated Weekly Strategy\n\nStay defensive.',
    };
    vi.mocked(api.getStrategyMemos).mockImplementation(async () => {
      callCount += 1;
      if (callCount === 1) return { memos: mockMemos };
      return { memos: [updatedMemo, mockMemos[1]] };
    });
    vi.mocked(api.updateStrategyMemo).mockResolvedValueOnce(updatedMemo);

    render(<MemoManager />);

    expect(await screen.findByText('Weekly Strategy')).toBeInTheDocument();

    fireEvent.click(screen.getAllByTitle('Edit memo')[0]);
    fireEvent.change(screen.getByLabelText('Memo Title'), {
      target: { value: 'Updated Weekly Strategy' },
    });
    fireEvent.change(screen.getByLabelText('Memo Content'), {
      target: { value: '# Updated Weekly Strategy\n\nStay defensive.' },
    });
    fireEvent.click(screen.getByRole('button', { name: /Save Changes/i }));

    await waitFor(() => {
      expect(api.updateStrategyMemo).toHaveBeenCalledWith(1, {
        title: 'Updated Weekly Strategy',
        content: '# Updated Weekly Strategy\n\nStay defensive.',
      });
      expect(screen.getByText('Updated Weekly Strategy')).toBeInTheDocument();
    });
  });

  it('disables download for imported memos without stored content', async () => {
    vi.mocked(api.getStrategyMemos).mockResolvedValueOnce({
      memos: [{ ...mockMemos[0], content: undefined }],
    });

    render(<MemoManager />);

    expect(await screen.findByText('Weekly Strategy')).toBeInTheDocument();

    const button = screen.getByTitle("No content stored. Use 'Import from files' to restore.");
    expect(button).toBeDisabled();
  });

  it('downloads memos with a slugified filename capped at 60 chars', async () => {
    const longTitleMemo = {
      ...mockMemos[0],
      title: 'Alpha Beta Gamma Delta Epsilon Zeta Eta Theta Iota Kappa Lambda Mu Nu Xi',
    };
    vi.mocked(api.getStrategyMemos).mockResolvedValueOnce({ memos: [longTitleMemo] });
    vi.stubGlobal('URL', {
      createObjectURL: vi.fn(() => 'blob:test'),
      revokeObjectURL: vi.fn(),
    });
    const clickSpy = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {});

    render(<MemoManager />);

    expect(await screen.findByText(longTitleMemo.title)).toBeInTheDocument();

    fireEvent.click(screen.getByTitle('Download .md'));

    const anchor = clickSpy.mock.instances[0] as HTMLAnchorElement;
    const slug = anchor.download.replace('2026-03-25-', '').replace('.md', '');
    expect(anchor.download.startsWith('2026-03-25-')).toBe(true);
    expect(anchor.download.endsWith('.md')).toBe(true);
    expect(slug.length).toBeLessThanOrEqual(60);
    expect(slug.endsWith('-')).toBe(false);
  });
});
