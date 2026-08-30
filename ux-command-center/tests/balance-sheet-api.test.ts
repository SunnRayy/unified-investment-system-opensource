import { afterEach, describe, expect, it, vi } from 'vitest';
import { BalanceSheetAPI } from '../src/services/api';

describe('BalanceSheetAPI', () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('uses 72 months as default history limit', async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({ snapshots: [] }),
    });
    vi.stubGlobal('fetch', fetchMock);

    await BalanceSheetAPI.getHistory();

    expect(fetchMock).toHaveBeenCalledWith('/api/balance-sheet/history?limit=72');
  });
});
