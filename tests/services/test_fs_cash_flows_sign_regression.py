"""Regression guard: fs_cash_flow_candidates() must keep negative deltas negative.

Plan: docs/plans/2026-07-25-amount-net-sign-convention-sweep.md §4a (SCOPE TRAP).

`_amount_to_cny` (src/services/north_star_flows.py) was changed to return an
abs() magnitude, because `transactions.amount_net`'s sign is a meaningless
per-reader convention artifact on the transactions path. `fs_cash_flow_candidates()`
is a DIFFERENT path — it reads holdings balance deltas directly (via
`info["amount_cny"]`, never through `_amount_to_cny`), and a balance DECREASE
is a real economic outflow that must stay negative. This is the tripwire for
"fixing" signs too broadly later: 16 of 32 real `fs_cash_delta` candidates are
legitimately negative in production, and a blanket abs() would corrupt them.

Kept in its own file (rather than appended to test_fs_cash_flows.py) to avoid
pushing that file over the repo's 400-line size-check threshold.
"""
from __future__ import annotations

from src.database.connector import DatabaseConnector
from src.database.schema import initialize_schema
from src.services.north_star_flows import fs_cash_flow_candidates


def _make_db() -> DatabaseConnector:
    conn = DatabaseConnector(":memory:")
    initialize_schema(conn)
    return conn


def _insert_fs_holding(
    conn, snapshot_date: str, asset_id: str, market_value: float, *, is_shadow: bool = False,
) -> None:
    conn.execute(
        """
        INSERT INTO holdings
            (snapshot_date, asset_id, asset_name, market_value, currency, source_system, is_shadow)
        VALUES (?, ?, ?, ?, 'CNY', 'Financial_Summary_Excel', ?)
        """,
        [snapshot_date, asset_id, asset_id, market_value, is_shadow],
    )


def test_fs_cash_delta_stays_negative_for_balance_decrease():
    """A month-over-month FS-cash BALANCE DECREASE must surface as a NEGATIVE
    amount_cny — this is genuine economic direction (money left the account),
    not a reader-convention sign artifact. Must NOT be abs()ed."""
    conn = _make_db()
    _insert_fs_holding(conn, "2026-03-01", "CASH_DRAWDOWN", 50000.0)
    _insert_fs_holding(conn, "2026-04-01", "CASH_DRAWDOWN", 20000.0)   # balance dropped

    candidates = fs_cash_flow_candidates(conn)
    key = ("fs_cash_delta", "fscash:CASH_DRAWDOWN|2026-04")
    assert key in candidates
    assert candidates[key]["amount_cny"] == -30000.0, (
        "Balance-decrease deltas must remain negative (real economic direction) — "
        f"got {candidates[key]['amount_cny']}. Do not route fs_cash amounts through "
        "_amount_to_cny() or otherwise abs() them."
    )
    conn.close()
