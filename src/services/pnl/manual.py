"""Owner-entered P&L overrides (#7, plan §C.1) — loading and supersession.

The bank-bought assets the readers cannot price (money-market / 理财 / 债券 /
美元债) have no cost and no transactions, so the engine's base treatment can only
honestly report "—". This module supplies the owner's own figures for them.

It owns two decisions and deliberately no arithmetic: *which* overrides exist,
and *whether* each one is still allowed to apply. The overlay itself — how a
loaded override adjusts cost / unrealized / realized — lives in
``engine.py`` next to the base treatment it overlays, because the precedence
between the two is one rule, not two.
"""
from __future__ import annotations

import logging

from src.services.transaction_source_selector import (
    LEGACY_TRANSACTION_SOURCES,
    select_transaction_sources,
)
from src.services.pnl.models import ManualPnL

logger = logging.getLogger(__name__)

# Owner ruling (2026-08-09): manual P&L logging is for **investments bought through
# a bank** — 债券 / 理财 / 个人养老金 and anything similar the owner buys directly
# rather than through a broker or fund platform. Three families are excluded
# because they are not that:
#
#   CASH_*      cash and deposit accounts are not investments. A checking balance
#               earning nothing is honestly ¥0, not an unlogged position.
#   INS_/Ins_   insurance has its own reader and its own cash-value semantics
#               (market_value = surrender value); P&L there is a different question.
#   Property_   real estate is not a bank product, and it already carries a real
#               cost basis from the Financial-Summary sheet.
#
# Deliberately an exclusion list, not an allowlist: a bank product the owner buys
# next year is loggable by default, which is the safe direction. Prefixes match the
# canonical conventions already used by INSURANCE_PREFIXES / NON_TRADEABLE_PREFIXES
# (`src/sync/phases/_common.py`) and REALIZED_PNL_EXEMPT_PREFIXES.
NOT_MANUALLY_LOGGABLE_PREFIXES = ("CASH_", "Property_", "INS_", "Ins_")


def is_manually_loggable(asset_id: str, *, has_reader_transactions: bool) -> bool:
    """May the owner log P&L for this asset?

    Two conditions, both required:

    1. no authoritative reader ledger feeds it — otherwise an override would be
       superseded (plan §C.1), so offering it would invite a figure that is
       then ignored;
    2. it is an investment rather than cash, insurance or property (see above).
    """
    if has_reader_transactions:
        return False
    return not str(asset_id or "").startswith(NOT_MANUALLY_LOGGABLE_PREFIXES)


def load_manual_overrides(db) -> dict:
    """Owner-entered P&L overrides, loaded in bulk **once per call** (plan §B.1).

    Returns ``{asset_id: ManualPnL}``. One query for the whole table — never a
    per-asset lookup, which would reintroduce the N+1 the engine exists to kill.

    A DB predating the V86 migration has no ``manual_asset_pnl`` table, so its
    absence is checked explicitly and yields ``{}``. That check is deliberately
    narrow: a *missing table* is a known pre-migration state, but any other
    failure propagates rather than being swallowed into a silent empty result
    (Rule 12 — an override the owner typed must never disappear quietly).
    """
    exists = db.execute(
        "SELECT 1 FROM information_schema.tables WHERE table_name = 'manual_asset_pnl'"
    ).fetchone()
    if not exists:
        return {}

    rows = db.execute(
        "SELECT asset_id, cost_basis_cny, realized_pnl_cny, as_of_date, memo "
        "FROM manual_asset_pnl"
    ).fetchall()
    return {
        row[0]: ManualPnL(
            asset_id=row[0],
            cost_basis_cny=None if row[1] is None else float(row[1]),
            realized_pnl_cny=None if row[2] is None else float(row[2]),
            as_of_date=row[3],
            memo=row[4],
        )
        for row in rows
        if row and row[0]
    }


def superseded_override_ids(db, override_ids) -> frozenset:
    """Override asset_ids that an authoritative reader ledger has taken over.

    Plan §C.1: once an asset receives *authoritative reader* transactions, the
    owner-entered cumulative figure is **superseded, not added** — otherwise the
    manual profit double-counts the reader-derived profit.

    The test is deliberately NOT "has any transaction": legacy/PIS rows exist
    for many assets and must not trigger supersession. It routes through
    ``select_transaction_sources`` — the same authority-aware classification the
    realized replay uses — and supersedes only when a surviving source is a
    non-legacy (reader) source. Per-asset iteration is fine here: it runs over
    the handful of *overridden* assets, not the portfolio.
    """
    superseded = set()
    for aid in override_ids:
        sources = select_transaction_sources(db, aid)
        if any(s not in LEGACY_TRANSACTION_SOURCES for s in sources):
            superseded.add(aid)
    if superseded:
        logger.warning(
            "[MANUAL-SUPERSEDED] %d manual P&L override(s) ignored — the asset now has "
            "authoritative reader transactions, so the reader ledger wins and the "
            "owner-entered figure would double-count: %s. Delete the stale override(s).",
            len(superseded),
            sorted(superseded),
        )
    return frozenset(superseded)
