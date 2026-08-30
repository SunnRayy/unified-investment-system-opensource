// Owner-logged P&L for bank-bought assets (#7, Release 2).
// Contract: docs/api-specs/manual-pnl.md
//
// The readers cannot price money-market / 理财 / 债券 / 美元债 holdings — no cost, no
// transactions — so those rows show "—" rather than a fabricated gain. These calls
// let the owner supply the figures the bank told them.
import i18n from '../../i18n';
import { authFetch } from '../authFetch';
import { API_BASE } from './base';

/** Rule-12 error body: {"detail": "..."} (HTTPException) or
 *  {"error": {"message": "..."}} (api_error_response). */
async function readErrorDetail(res: Response, fallback: string): Promise<string> {
  const data = await res.json().catch(() => null);
  return data?.detail || data?.error?.message || fallback;
}

export interface ManualPnL {
  asset_id: string;
  /** What the owner put in, CNY. Yields unrealized = market − cost for non-cash assets. */
  cost_basis_cny: number | null;
  /** Cumulative realized profit to date, CNY. All-time only — period views ignore it. */
  realized_pnl_cny: number | null;
  /** Display provenance only — the date the cumulative figure is "as of". Never used in math. */
  as_of_date: string | null;
  memo: string | null;
  created_at: string | null;
  updated_at: string | null;
  /**
   * False for cash-equivalent assets: a cash balance has no price basis, so a logged
   * cost is stored but produces no unrealized gain. Surface this, or the owner sees a
   * cost the P&L appears to ignore.
   */
  cost_affects_unrealized: boolean;
  /**
   * True once an authoritative reader ledger has taken the asset over — the engine
   * ignores the override (it would double-count) and it should be deleted.
   */
  superseded: boolean;
  /**
   * A logged cost covers the whole position, so buying more or selling part of it
   * invalidates the figure. These record the balance the cost was entered against
   * and how far the balance has moved since, so the UI can prompt for a re-log.
   * A warning only — inferring a new cost would be inventing a number.
   */
  market_value_at_log: number | null;
  current_market_value: number | null;
  value_move_pct: number | null;
  value_looks_stale: boolean;
}

export interface ManualPnLInput {
  cost_basis_cny?: number | null;
  realized_pnl_cny?: number | null;
  as_of_date?: string | null;
  memo?: string | null;
}

export const manualPnlApi = {
  listManualPnl: async (): Promise<ManualPnL[]> => {
    const res = await authFetch(`${API_BASE}/holdings/manual-pnl`);
    if (!res.ok) throw new Error(await readErrorDetail(res, i18n.t('errors:manualPnl.load')));
    return res.json();
  },

  /** Upsert. The API rejects a payload with neither figure (400) — an empty override
   *  is indistinguishable from no override. Use deleteManualPnl to clear. */
  saveManualPnl: async (assetId: string, data: ManualPnLInput): Promise<ManualPnL> => {
    const res = await authFetch(
      `${API_BASE}/holdings/${encodeURIComponent(assetId)}/manual-pnl`,
      {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(data),
      },
    );
    if (!res.ok) throw new Error(await readErrorDetail(res, i18n.t('errors:manualPnl.save')));
    return res.json();
  },

  deleteManualPnl: async (assetId: string): Promise<{ asset_id: string; deleted: boolean }> => {
    const res = await authFetch(
      `${API_BASE}/holdings/${encodeURIComponent(assetId)}/manual-pnl`,
      { method: 'DELETE' },
    );
    if (!res.ok) throw new Error(await readErrorDetail(res, i18n.t('errors:manualPnl.clear')));
    return res.json();
  },
};
