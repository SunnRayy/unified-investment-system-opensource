"""Portfolio display, classification, and realized-PnL helpers.

Extracted from src/api/routes/performance.py (Pass G item 4c-followup) to eliminate
cross-layer imports: service modules must not import from API routes.

Used by: performance.py, portfolio_semantics.py, context_generator.py,
         src/services/ai_advisor/context_builder.py.
"""

from __future__ import annotations

import logging
from typing import Optional

import pandas as pd

from src.database.connector import DatabaseConnector
from src.financial_analysis.cost_basis import CostBasisCalculator
from src.services.rebalanceable_filter import (
    fetch_non_rebalanceable_asset_ids as _fetch_non_rebalanceable,
)
from src.services.transaction_source_selector import (
    is_realized_pnl_exempt,
    select_transaction_sources,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Display name mapping (bilingual: English → English/Chinese)
#
# DELIBERATELY BILINGUAL — owner ruling, 2026-08-30. These values render both
# languages at once ("Equity (股票)") regardless of the UI locale, which Program
# BIL flagged as the one surface it did not localize. That is intended, not an
# oversight: for asset-class terminology the owner wants the English and Chinese
# names side by side. Do NOT "finish the i18n migration" by splitting these into
# locale catalogs.
#
# This is the single definition. `compass_allocation` used to carry a verbatim
# copy; it now imports from here (2026-09-02).
#
# ⚠ Do NOT derive this table from `taxonomy_seeds.py` without reading the next
# paragraph — that file's note calls the merge "the right move", and it is not a
# refactor. Eight classes deliberately show something other than their taxonomy
# `name_cn`:
#
#     Cash Checking      活期存款  → 活期        Money Market   货币市场 → 货基
#     Cash Deposit       定期存款  → 定期        Other Commodity 其他贵金属 → 大宗商品
#     Insurance Products 保险     → 保险产品     Property       住宅地产 → 房产
#     SMB                创业投资  → 中小企业     HK ETF         港股    → (no Chinese shown)
#
# Some are deliberate abbreviations for a narrow table column; at least two
# (Other Commodity, SMB) are the display and the taxonomy disagreeing about what
# the class *means*. Collapsing them silently relabels eight rows in the UI, so
# it needs an owner ruling, not a tidy-up commit.
# ---------------------------------------------------------------------------

DISPLAY_MAP = {
    # Top-level classes (taxonomy_classes parent nodes)
    "Equity": "Equity (股票)",
    "Fixed Income": "Fixed Income (固定收益)",
    "Cash": "Cash (现金)",
    "Alternative": "Alternatives (另类投资)",
    "Commodity": "Commodities (商品)",
    "Real Estate": "Real Estate (房地产)",
    "Insurance": "Insurance (保险)",

    # Sub-classes (taxonomy_classes child nodes)
    "CN Equity": "CN Equity (A股)",
    "HK ETF": "HK ETF",
    "US Equity": "US Equity (美股)",
    "CN Bonds": "CN Bonds (国债)",
    "US Bonds": "US Bonds (美债)",
    "Bank Wealth": "Bank Wealth (银行理财)",
    "Money Market": "Money Market (货基)",
    "Gold": "Gold (黄金)",
    "Other Commodity": "Other Commodity (大宗商品)",
    "Energy": "Energy (能源)",
    "Cash Checking": "Cash Checking (活期)",
    "Cash Deposit": "Cash Deposit (定期)",
    "Crypto": "Crypto (加密货币)",
    "SMB": "SMB (中小企业)",
    "Property": "Property (房产)",
    "Insurance Products": "Insurance (保险产品)",

    # Legacy Chinese names (backward compat for any remaining registry entries)
    "股票": "Equity (股票)",
    "固定收益": "Fixed Income (固定收益)",
    "现金": "Cash (现金)",
    "另类投资": "Alternatives (另类投资)",
    "商品": "Commodities (商品)",
    "房地产": "Real Estate (房地产)",
    "美国政府债券": "US Bonds (美债)",
    "国内政府债券": "CN Bonds (国债)",
    "活期存款": "Cash Checking (活期)",
    "定期存款": "Cash Deposit (定期)",
    "货币市场": "Money Market (货基)",
    "黄金": "Gold (黄金)",
    "加密货币": "Crypto (加密货币)",
}

# Asset classes that are non-rebalanceable by display name (fallback check).
NON_BALANCEABLE_MARKERS = (
    "real estate",
    "property",
    "房地产",
    "房产",
    "insurance",
    "保险",
)


def get_display_name(key: str) -> str:
    return DISPLAY_MAP.get(key, key)


def resolve_top_class(raw_class: str) -> str:
    """Convert a raw top_class SQL value into a bilingual display name.

    After migrating to taxonomy_classes, the SQL COALESCE already returns the
    correct English top-class name (e.g. 'Equity', 'Fixed Income'). This function
    just applies DISPLAY_MAP for bilingual presentation.
    """
    return get_display_name(raw_class)


def is_non_balanceable_class(class_name: str) -> bool:
    if not class_name:
        return False
    lowered = class_name.lower()
    return any(marker in lowered for marker in NON_BALANCEABLE_MARKERS)


# ---------------------------------------------------------------------------
# Asset-ID query helpers
# ---------------------------------------------------------------------------

def fetch_non_balanceable_asset_ids(db: DatabaseConnector) -> set[str]:
    """Return asset IDs classified as non-rebalanceable (delegates to shared utility)."""
    return _fetch_non_rebalanceable(db)


def fetch_asset_ids_for_period(
    db: DatabaseConnector,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
) -> list[str]:
    """Return all asset IDs that appear in holdings or transactions for the period."""
    date_filters = ""
    params: list[str] = []
    if start_date:
        date_filters += " AND snapshot_date >= ?"
        params.append(start_date)
    if end_date:
        date_filters += " AND snapshot_date <= ?"
        params.append(end_date)
    holdings_rows = db.execute(
        f"""
        SELECT DISTINCT asset_id
        FROM holdings
        WHERE is_shadow = FALSE {date_filters}
        """,
        params or None,
    ).fetchall()

    tx_filters = ""
    tx_params: list[str] = []
    if start_date:
        tx_filters += " AND transaction_date >= ?"
        tx_params.append(start_date)
    if end_date:
        tx_filters += " AND transaction_date <= ?"
        tx_params.append(end_date)
    tx_rows = db.execute(
        f"""
        SELECT DISTINCT asset_id
        FROM transactions
        WHERE 1=1 {tx_filters}
        """,
        tx_params or None,
    ).fetchall()

    merged = {row[0] for row in holdings_rows if row and row[0]}
    merged.update(row[0] for row in tx_rows if row and row[0])
    return sorted(merged)


def fetch_included_asset_ids(
    db: DatabaseConnector,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
) -> list[str]:
    """Return rebalanceable asset IDs that appear in the given time window."""
    excluded = fetch_non_balanceable_asset_ids(db)
    all_ids = fetch_asset_ids_for_period(db, start_date=start_date, end_date=end_date)
    return [aid for aid in all_ids if aid not in excluded]


# ---------------------------------------------------------------------------
# Realized P&L calculation
# ---------------------------------------------------------------------------

def calculate_realized_pnl(
    db: DatabaseConnector,
    asset_id: str,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
) -> tuple[float, str]:
    """Calculate realized P&L for a single asset using FIFO.

    For period views, keep full lot history as FIFO context and then measure only
    the realized delta inside the requested window.
    """
    try:
        if is_realized_pnl_exempt(db, asset_id):
            return 0.0, "CNY"

        date_filters = ""
        date_params: list = []
        if end_date:
            date_filters += " AND transaction_date <= ?"
            date_params.append(end_date)

        selected_sources = select_transaction_sources(db, asset_id)
        if selected_sources:
            placeholders = ", ".join(["?"] * len(selected_sources))
            params = [asset_id, *selected_sources, *date_params]
            tx_rows = db.execute(
                f"""
                SELECT transaction_type, quantity, price_unit, amount_net, currency, transaction_date
                FROM transactions
                WHERE asset_id = ?
                  AND source_system IN ({placeholders})
                  {date_filters}
                ORDER BY transaction_date ASC
                """,
                params,
            ).fetchall()
        else:
            params = [asset_id, *date_params]
            tx_rows = db.execute(
                f"""
                SELECT transaction_type, quantity, price_unit, amount_net, currency, transaction_date
                FROM transactions
                WHERE asset_id = ?
                {date_filters}
                ORDER BY transaction_date ASC
                """,
                params,
            ).fetchall()

        if not tx_rows:
            return 0.0, "CNY"

        df = pd.DataFrame(tx_rows, columns=[
            'transaction_type', 'quantity', 'price_unit', 'amount_net', 'currency',
            'transaction_date',
        ])

        df['transaction_date'] = pd.to_datetime(df['transaction_date'])
        df.set_index('transaction_date', inplace=True)

        if df.empty:
            return 0.0, "CNY"

        # All-time calculation: process full range once.
        if not start_date:
            calculator = CostBasisCalculator(asset_id)
            calculator.process_transactions(df)
            pnl_amount = calculator.realized_pnl
            currency = calculator.native_currency
            logger.debug(
                'Realized P&L %s: %.4f %s (native currency; constant-FX method)',
                asset_id, pnl_amount, currency,
            )
            return pnl_amount, currency

        # Period calculation: keep pre-period transactions as lot context, then
        # return only realized delta inside [start_date, end_date].
        cutoff = pd.to_datetime(start_date)

        calc_full = CostBasisCalculator(asset_id)
        calc_full.process_transactions(df)

        before_df = df[df.index < cutoff]
        if before_df.empty:
            pnl_amount = calc_full.realized_pnl
            currency = calc_full.native_currency
            logger.debug(
                'Realized P&L %s: %.4f %s (native currency; constant-FX method)',
                asset_id, pnl_amount, currency,
            )
            return pnl_amount, currency

        calc_before = CostBasisCalculator(asset_id)
        calc_before.process_transactions(before_df)

        pnl_amount = calc_full.realized_pnl - calc_before.realized_pnl
        currency = calc_full.native_currency
        logger.debug(
            'Realized P&L %s: %.4f %s (native currency; constant-FX method)',
            asset_id, pnl_amount, currency,
        )
        return pnl_amount, currency
    except Exception as e:
        logger.error("Error calculating realized P&L for %s: %s", asset_id, e)
        return 0.0, "CNY"
