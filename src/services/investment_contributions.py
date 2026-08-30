"""Investment contributions & savings from 月度收支 (plan 2026-07-20-investment-contributions-savings.md, WS-A).

READ-ONLY service — no writes to any table. Computes the monthly
investment-contribution + savings series directly from
`income_expense_monthly.payload` (the FS Excel 月度收支 tab), which is the
owner's own monthly record of money invested by destination.

⚠️ COLUMN SEMANTICS ARE DATA, NOT CODE (plan 2026-08-01 WS-A, migration V82).
Which column means "invested into US brokerage", which means "redemption",
and which is a native-currency display sibling now lives in the
`reader_mappings` table (`reader_key='financial_summary'`,
`mapping_kind='ie_column'`), seeded from
`src.database.mapping_seeds.IE_COLUMN_SEED` and editable by the owner. This
module reads that mapping and derives every total from it — it names no
月度收支 column literal anywhere. Before V82 the six column names were
hardcoded here, so a column the owner added to the Excel was silently
dropped out of `gross_invested` with no error (the silent-failure /
convention-contract class in the `uis-failure-classes` memory).

Aggregation rules (all driven by the mapping's `role` / `bucket` /
`currency` fields — see IEColumn's docstring for the vocabulary):

  gross_invested  Σ role='invested', currency='CNY', grouped into
                  `by_destination` by `bucket`.
  redemptions     Σ role='redemption', currency='CNY'.
  pass_through    Σ role='pass_through', currency='CNY', split by `bucket`
                  into 'inflow' (报销, money repaid to the owner) and
                  'outflow' (工作开支, the spending he fronted). Two ends of
                  ONE round trip, so BOTH are excluded from BOTH bases — one
                  shared role rather than two unrelated exclusions makes that
                  pairing structural. Never 'redemption': that role also
                  subtracts from the investment NUMERATOR, and a repayment
                  must not.
  income_basis    Σ role='income', currency='CNY' — the LEAF income columns
                  (工资 / RSU / 公积金 / 其他偶然 / 股票卖出收益 / …). The
                  ledger's own aggregate columns (总收入合计 / 主动收入合计 /
                  被动收入合计) are role='computed' and are NEVER read as a
                  calculation input — owner ruling 2026-08-01: 所有 excel 里的
                  计算/合计值都不应该被 Huinsight 读取使用. Huinsight re-derives the
                  Excel-equivalent total itself and only compares against
                  those columns to warn (src/services/ie_ledger.py).
  expense_basis   Σ role='expense', currency='CNY'. ⚠️ NOT the Excel's 总支出,
                  which bundles 理财 (investment) in — investing is not
                  spending.
  everything else contributes to nothing (computed / reference / ignored).

⚠️ `currency='USD'` COLUMNS CONTRIBUTE TO NOTHING. The Excel keeps a
native-currency sibling next to several CNY columns (`_Schawab_USD`,
`_IBKR_USD`, `_RSU_USD`, `_股票卖出收益_USD`); the owner applies the FX rate
himself before entry, so the CNY column is already the whole amount. ADR-025
§3 verified `Schawab == Schawab_USD × 参考_美元汇率` exactly, every month —
summing both would ~2x US investment. This is Rule 2 (all stored/derived
values are CNY) at the ledger layer.

⚠️ A SALE IS ONLY A `redemption` IF THE MONEY ENTERED VIA 投资理财. RSU shares
retained at the broker entered the ledger as `收入_主动收入_RSU*` (income), never
as a 投资理财 column, so `收入_被动收入_股票卖出收益` is role='income', NOT
role='redemption' — tagging it a redemption would subtract money that was never
added (the double-subtract ADR-025 §4b warns about). Owner session 2026-08-01.

⚠️ TRAILING-ONLY SAVINGS RATE — never per-month. A naive per-month
`external = invested − redeemed` breaks on real data: one month can show
invested well above that month's income (a lump-sum deployment of
previously-accumulated cash, producing a nonsensical >300% "savings rate"),
and another month can show redemptions exceeding invested in the same month
(reallocation + cash-out timing). Redemptions and reinvestments do
NOT align within a
calendar month, so any external/savings-rate figure MUST be computed over a
trailing window (see `contributions_summary_v2`), never for a single month.
Per-month GROSS invested (by destination) is fine as a historical series —
that part is directly observed, not derived.

See docs/plans/2026-07-20-investment-contributions-savings.md §Data
("⚠️ CORRECTION") and §Model for the full derivation and owner sign-off, and
ADR-025 for the three-sources-never-summed invariant.
"""
from __future__ import annotations

from typing import Dict, Optional

from src.database.mapping_seeds import IEColumn
from src.services.ie_ledger import (
    IE_MAPPING_KIND,
    IE_READER_KEY,
    destination_buckets as _destination_buckets,
    load_ie_column_mapping,
    payload_dict as _payload_dict,
    role_totals,
)

__all__ = [
    "IE_MAPPING_KIND",
    "IE_READER_KEY",
    "contributions_summary_v2",
    "load_ie_column_mapping",
    "monthly_investment_flows",
]


def monthly_investment_flows(db, mapping: Optional[Dict[str, IEColumn]] = None) -> list[dict]:
    """Per-month investment-by-destination series from income_expense_monthly.

    Read-only: SELECTs income_expense_monthly (and, via
    `load_ie_column_mapping`, reader_mappings) only — no writes anywhere in
    this module. Returns one dict per month present in the table (ascending
    by month), skipping months where gross_invested, redemptions, and
    income are ALL zero (a month with no 投资理财/被动收入/收入 data at all —
    keeping it would just be a row of zeros with no information content).

    Each entry:
        {
          "month": "YYYY-MM",
          "by_destination": {bucket: CNY amount, ...},  # buckets from the mapping
          "gross_invested": sum(by_destination.values()),
          "redemptions": Σ role='redemption' (CNY),
          "pass_through_in": Σ role='pass_through' bucket='inflow' (报销),
          "pass_through_out": Σ role='pass_through' bucket='outflow' (工作开支),
          "income_basis": Σ role='income' (CNY) — the LEAF income columns,
          "expense_basis": Σ role='expense' (CNY) — 必要/非必要开支 leaves,
          "income": income_basis + redemptions + pass_through_in — the
              Excel-equivalent 总收入合计, DERIVED from the leaves, never read
              from the aggregate column (owner ruling 2026-08-01),
        }

    All amounts are already CNY — no FX conversion happens anywhere in this
    service (see the module docstring on `currency='USD'` siblings).

    Args:
        db: DatabaseConnector-like object exposing `.execute()`.
        mapping: pre-loaded strip-normalized ie_column mapping (see
            `load_ie_column_mapping`). Defaults to loading it from `db` —
            passed explicitly by `contributions_summary_v2` so one summary
            call loads it once.
    """
    mapping = mapping if mapping is not None else load_ie_column_mapping(db)
    buckets = _destination_buckets(mapping)

    rows = db.execute(
        "SELECT transaction_date, payload FROM income_expense_monthly ORDER BY transaction_date ASC"
    ).fetchall()

    out: list[dict] = []
    for transaction_date, payload_json in rows:
        payload = _payload_dict(payload_json)
        totals = role_totals(payload, mapping, buckets=buckets)

        gross_invested = totals.invested
        redemptions = totals.redemption
        income_basis = totals.income
        expense_basis = totals.expense
        income = totals.gross_income

        if gross_invested == 0.0 and redemptions == 0.0 and income == 0.0:
            continue

        month_str = (
            transaction_date.strftime("%Y-%m")
            if hasattr(transaction_date, "strftime")
            else str(transaction_date)[:7]
        )
        out.append({
            "month": month_str,
            "by_destination": totals.by_bucket,
            "gross_invested": gross_invested,
            "redemptions": redemptions,
            "pass_through_in": totals.pass_through_in,
            "pass_through_out": totals.pass_through_out,
            "income_basis": income_basis,
            "expense_basis": expense_basis,
            "income": income,
        })

    out.sort(key=lambda r: r["month"])
    return out


def contributions_summary_v2(db, window_months: int = 12) -> dict:
    """Trailing-window contribution/savings summary derived from 月度收支.

    Read-only: delegates to monthly_investment_flows(db) for its full
    2020-> now series, no writes anywhere in this module.

    TRAILING-ONLY SAVINGS RATE (see module docstring): the returned
    `savings_rate_ttm` and `net_external_ttm` are computed over a trailing
    window of the most recent `window_months` DISTINCT months that actually
    appear in `series` — anchored to the latest DATA month, not to today's
    calendar date (the Excel data lags real time, so "today minus 12
    calendar months" would not line up with the available months). A
    per-month savings rate is never computed or returned — it has been
    proven meaningless on this data (2025-05 shows a 341% per-month rate
    from lump-sum investing).

    TWO RATES (ADR-025 Amendment 2026-08-01, owner-approved — supersedes §2's
    single formula):

        income_basis_ttm    = Σ(role='income', CNY)   over the window
        expense_basis_ttm   = Σ(role='expense', CNY)  over the window
        savings_rate_ttm    = (income_basis_ttm − expense_basis_ttm) / income_basis_ttm
        investment_rate_ttm = (net_external_ttm + rsu_retained_ttm) / income_basis_ttm

    - **The metric itself was wrong** (WS-G). Huinsight computed "money that reached
      an investment account / income" and called it the SAVINGS rate. That is
      an *investment* rate: money earned and left in a bank account is saved,
      just not deployed. Both ship, separately; the gap between them is
      `undeployed_cash_ttm`. This is why every correction still felt too low to
      the owner (15.94% -> 23.18% -> 40.47% -> 41.56%).
    - **The investment numerator** includes retained RSU. The denominator books
      the FULL vest as income (`收入_主动收入_RSU`), so excluding the shares
      that vested and were KEPT understated it. The North Star glide run-rate
      has always included it (`(net_external_ttm + retained_cny) / 12`,
      ADR-025 §4c); the two now agree. This is a DELIBERATE, owner-approved
      exception to ADR-025's "the three sources are never summed" invariant,
      scoped to this one metric — see the §Reconciliation test, which still
      forbids every other combination.
    - **The denominator is a LEAF sum, never an Excel aggregate** (WS-E, owner
      ruling: 所有 excel 里的计算/合计值都不应该被 Huinsight 读取使用). It is narrower
      than the Excel's `总收入合计` on purpose:
        * the redemption columns (the whole of `被动收入合计`) are out — a
          redemption converts an asset to cash, and leaving it in penalised it
          TWICE: subtracted in the numerator (`net_external_ttm`) and added
          back in the denominator as if it were income;
        * BOTH ends of the 报销 / 工作开支 pass-through are out — the owner
          fronts a work expense and is repaid, so neither end is real income
          nor real consumption (e.g. ¥25,800.00 in vs ¥24,900.00 out in a
          given window; a small gap is timing, not a defect).
      Example trailing window: ¥890,000 basis, ¥354,000 expense
      basis -> ~60% savings rate / ~40% investment rate. The realized-gain column
      `收入_被动收入_股票卖出收益` is role='income', NOT 'redemption', so it
      legitimately STAYS in the basis once the owner starts filling it in —
      which is exactly why every exclusion is derived from the ie_column
      `role`, never from a hardcoded column list. `收入_主动收入_公积金`
      (housing-fund withdrawals) and `其他偶然` (bonus) also STAY: owner
      decisions of the same date, recorded in the ADR.
    - **`expense_basis` excludes investment.** The Excel's `总支出` BUNDLES
      理财 in (verified 2026-07: 36,149.00 + 535.00 + 0.00 + 37,222.35 =
      73,906.35); investing is not spending. `LedgerTotals.total_outflow` is
      the Excel-equivalent figure when that is what you mean.

    `income_ttm` (the Excel-equivalent gross total, derived) is still returned
    next to `income_basis_ttm` — the two are never conflated.

    Returns:
        {
          "series": [...],  # full monthly_investment_flows(db) output
          "gross_invested_ttm": float,
          "redemptions_ttm": float,
          "pass_through_in_ttm": float,   # 报销 — arrives, but is not earnings
          "pass_through_out_ttm": float,  # 工作开支 — leaves, but is not consumption
          "income_ttm": float,            # Excel-equivalent 总收入合计, DERIVED — not a denominator
          "income_basis_ttm": max(Σ role='income' (CNY), 0.0),
          "expense_basis_ttm": max(Σ role='expense' (CNY), 0.0),
          "rsu_retained_ttm": float,      # vested in-window and still held, CNY
          "investment_numerator_ttm": net_external_ttm + rsu_retained_ttm,
          "net_external_ttm": max(gross_invested_ttm - redemptions_ttm, 0.0),
          "internal_realloc_ttm": min(gross_invested_ttm, redemptions_ttm),
          "savings_rate_ttm": (income_basis_ttm - expense_basis_ttm) / income_basis_ttm
                              if income_basis_ttm > 0 else None,
          "investment_rate_ttm": investment_numerator_ttm / income_basis_ttm
                              if income_basis_ttm > 0 else None,
          "undeployed_cash_ttm": income_basis_ttm - expense_basis_ttm
                              - investment_numerator_ttm,
          "by_destination_ttm": {bucket: float, ...},  # same keys as series entries
          "window_start_month": "YYYY-MM" or None,
          "window_end_month": "YYYY-MM" or None,
          "months_with_contribution": int or None,
          "months_with_contribution_window": int or None,
        }

    `by_destination_ttm`'s keys are whatever destinations the ie_column
    mapping declares (see `_destination_buckets`) — a destination with no
    money in the window is still present, at 0.0, so the key set stays stable
    for the UI across windows.

    If `series` is empty (no data), all sums are 0.0, savings_rate_ttm is
    None, and window_start_month/window_end_month are None.

    months_with_contribution / months_with_contribution_window (owner
    decision 2026-07-26, docs/design/2026-07-26-your-path.dc.html.md §3.1):
    a PARTICIPATION signal, not a per-month amount — count of months in this
    same trailing window that had ANY non-zero investment inflow
    (gross_invested > 0), out of the number of months actually examined
    (== len(window), which is window_months when >= that much history
    exists, else the shorter true history — never padded to look like a
    full window). This deliberately sidesteps the ADR-025 §2 trap that killed
    the mock's "N/12 months at or above run-rate" tile: that metric compared
    a per-month AMOUNT against the run-rate, which lump-sum investing months
    make meaningless (a single large-deployment month showed a >300% "savings rate").
    Participation ("did you invest anything this month?") carries no such
    per-month-amount claim. None (never fabricated) when window is empty —
    the UI must render an empty state, not a phantom 0/0.
    """
    mapping = load_ie_column_mapping(db)
    series = monthly_investment_flows(db, mapping=mapping)

    window = series[-window_months:] if window_months > 0 else []

    gross_invested_ttm = sum(m["gross_invested"] for m in window)
    redemptions_ttm = sum(m["redemptions"] for m in window)
    pass_through_in_ttm = sum(m["pass_through_in"] for m in window)
    pass_through_out_ttm = sum(m["pass_through_out"] for m in window)
    income_basis_raw = sum(m["income_basis"] for m in window)
    expense_basis_raw = sum(m["expense_basis"] for m in window)
    income_ttm = sum(m["income"] for m in window)

    net_external_ttm = max(gross_invested_ttm - redemptions_ttm, 0.0)
    internal_realloc_ttm = min(gross_invested_ttm, redemptions_ttm)

    window_start_month = window[0]["month"] if window else None
    window_end_month = window[-1]["month"] if window else None

    # ADR-025 Amendment 2026-08-01 — savings-rate numerator includes RSU that
    # vested in this same window and is still held. Local import (mirrors
    # src/services/north_star_glide.py::_contribution_run_rate) purely as an
    # import-cycle guard: rsu_contributions is a peer service and several
    # modules import this one at module level. It is read-only (SELECT on
    # transactions + an in-memory FIFO replay) and is given THIS function's own
    # window bounds, never a recomputed pair, so the two figures cannot drift
    # onto different windows — the same discipline the glide run-rate uses.
    rsu_retained_cny = 0.0
    if window_start_month is not None and window_end_month is not None:
        from src.services.rsu_contributions import rsu_retained_ttm  # noqa: PLC0415

        rsu_retained_cny = float(
            rsu_retained_ttm(db, window_start_month, window_end_month)["retained_cny"]
        )

    # Both derived figures are built from the ROUNDED components this function
    # actually reports, not from the raw float sums, so the response is
    # arithmetically self-consistent on its own face:
    #   income_ttm               == income_basis_ttm + redemptions_ttm
    #                               + pass_through_in_ttm
    #   investment_numerator_ttm == net_external_ttm + rsu_retained_ttm
    # Summing many months of float ledger values otherwise leaves a sub-cent
    # residue that shows up as a subtraction the owner can't reproduce by hand
    # (a sub-cent mismatch between the reported total and what the two
    # displayed numbers sum to by hand). rsu_retained_ttm arrives already
    # rounded to 2dp.
    investment_numerator_ttm = round(net_external_ttm, 2) + rsu_retained_cny
    # The denominator is the SUM OF THE INCOME LEAF COLUMNS — never
    # `总收入合计 − redemptions − pass_through`, and never the Excel aggregate
    # itself (owner ruling 2026-08-01; see the module docstring and
    # src/services/ie_ledger.py). The two are arithmetically the same thing
    # today (verified live to the cent), but only the leaf sum stays correct
    # when the owner inserts a column his SUM range does not reach.
    # Clamped at 0 for the pathological case of a negative leaf total.
    income_basis_ttm = max(round(income_basis_raw, 2), 0.0)
    expense_basis_ttm = max(round(expense_basis_raw, 2), 0.0)

    # TWO metrics, honestly named (ADR-025 Amendment 2026-08-01, owner-approved):
    #   savings_rate    — what he KEPT: (income − consumption) / income. Money
    #                     earned and left in the bank is saved; it was simply
    #                     never deployed.
    #   investment_rate — what he DEPLOYED into investments: the figure this
    #                     system used to (mis)label "savings rate".
    # The gap between them is undeployed cash. Exposed as its own field rather
    # than left for consumers to subtract — cross-checked against the balance
    # sheet (implied undeployed cash vs actual CASH_* net change; a small
    # residual is expected from the flat-7.0 USD conversion, known-issues
    # §fx-constant).
    savings_rate_ttm = (
        ((income_basis_ttm - expense_basis_ttm) / income_basis_ttm) if income_basis_ttm > 0 else None
    )
    investment_rate_ttm = (
        (investment_numerator_ttm / income_basis_ttm) if income_basis_ttm > 0 else None
    )
    undeployed_cash_ttm = round(income_basis_ttm - expense_basis_ttm - investment_numerator_ttm, 2)

    by_destination_ttm = {
        bucket: sum(m["by_destination"].get(bucket, 0.0) for m in window)
        for bucket in _destination_buckets(mapping)
    }

    months_with_contribution = sum(1 for m in window if m["gross_invested"] > 0) if window else None
    months_with_contribution_window = len(window) if window else None

    return {
        "series": series,
        "gross_invested_ttm": round(gross_invested_ttm, 2),
        "redemptions_ttm": round(redemptions_ttm, 2),
        "pass_through_in_ttm": round(pass_through_in_ttm, 2),
        "pass_through_out_ttm": round(pass_through_out_ttm, 2),
        "income_ttm": round(income_ttm, 2),
        "income_basis_ttm": round(income_basis_ttm, 2),
        "expense_basis_ttm": round(expense_basis_ttm, 2),
        "net_external_ttm": round(net_external_ttm, 2),
        "internal_realloc_ttm": round(internal_realloc_ttm, 2),
        "rsu_retained_ttm": round(rsu_retained_cny, 2),
        "investment_numerator_ttm": round(investment_numerator_ttm, 2),
        "savings_rate_ttm": savings_rate_ttm,
        "investment_rate_ttm": investment_rate_ttm,
        "undeployed_cash_ttm": undeployed_cash_ttm,
        "by_destination_ttm": {k: round(v, 2) for k, v in by_destination_ttm.items()},
        "window_start_month": window_start_month,
        "window_end_month": window_end_month,
        "months_with_contribution": months_with_contribution,
        "months_with_contribution_window": months_with_contribution_window,
    }
