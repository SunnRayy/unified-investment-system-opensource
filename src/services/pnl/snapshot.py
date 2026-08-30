"""The canonical CURRENT active-holdings query, with MODES (plan §B.1a).

This is the *one* code path the current-P&L surfaces share — NOT a universal CTE
meant to absorb the ~25 unrelated ``latest_per_asset`` copies scattered across
the codebase. It supports two explicit modes:

- ``mode=current`` (``start_date=None``): per-asset latest snapshot,
  ``is_shadow=FALSE`` (Consolidated is already materialized as the active
  authority, so filtering shadow rows is correct here).
- ``mode=period`` (``start_date`` set): the same per-asset latest, but with
  candidate snapshots constrained ``>= start_date`` — a real behavioral
  difference kept as a mode, not flattened away.

Closed / transaction-only assets are surfaced via ``transaction_asset_ids`` and
handled by the engine as a scope flag (``AssetPnL.is_current=False``), not a
separate query.

Every query is per-asset ``MAX(snapshot_date)`` — never a global MAX (Rule 3).
"""
from __future__ import annotations

from typing import Optional


def _latest_holdings_cte(start_date: Optional[str]) -> tuple[str, list]:
    """Per-asset latest-snapshot CTE. Period mode floors the candidates at
    ``start_date``. SQL is kept byte-identical to the legacy
    ``performance.latest_snapshot_cte`` so migrated surfaces do not shift a row.
    """
    if start_date:
        return (
            """
            WITH latest_per_asset AS (
                SELECT asset_id, MAX(snapshot_date) as latest_date
                FROM holdings
                WHERE is_shadow = FALSE
                  AND snapshot_date >= ?
                GROUP BY asset_id
            )
            """,
            [start_date],
        )
    return (
        """
    WITH latest_per_asset AS (
        SELECT asset_id, MAX(snapshot_date) as latest_date
        FROM holdings WHERE is_shadow = FALSE
        GROUP BY asset_id
    )
""",
        [],
    )


#: Column order of the rows returned by :func:`fetch_active_holdings`.
ACTIVE_COLUMNS = (
    "asset_id", "name", "top_class", "sub_class", "market_value", "quantity",
    "cost_price_unit", "market_price_unit", "currency",
    "asset_class_registry", "source_system",
)


def fetch_active_holdings(
    db,
    *,
    start_date: Optional[str] = None,
    positive_only: bool = False,
    resolve_taxonomy: bool = True,
) -> list:
    """Return one aggregated row per current active asset (see ``ACTIVE_COLUMNS``).

    ``top_class`` / ``sub_class`` are the raw taxonomy strings (the engine
    resolves display names); ``asset_class_registry`` is the *raw* registry
    ``asset_class`` (WealthOS ``type``, no display map) and ``source_system`` is
    the latest holding source. Aggregation matches the legacy performance/gains
    queries exactly: ``SUM(market_value)``, ``SUM(quantity)``,
    ``MAX(cost_price_unit)``, ``MAX(currency)`` and the
    ``COALESCE(MAX(parent_tc), MAX(tc), MAX(class))`` class resolution.

    ``positive_only`` adds ``HAVING SUM(market_value)>0 AND SUM(quantity)>0`` —
    the WealthOS active-holdings filter. ``resolve_taxonomy=False`` drops the
    ``taxonomy_classes`` joins entirely (top/sub fall back to the raw registry
    class) so the WealthOS path does not require that table to exist — matching
    the legacy WealthOS query, which joined only ``asset_registry``.
    """
    cte, params = _latest_holdings_cte(start_date)
    having = "HAVING SUM(h.market_value) > 0 AND SUM(h.quantity) > 0" if positive_only else ""
    if resolve_taxonomy:
        top_class_expr = "COALESCE(MAX(parent_tc.name), MAX(tc.name), MAX(r.asset_class), 'Unclassified')"
        taxonomy_joins = (
            "LEFT JOIN taxonomy_classes tc ON r.asset_class = tc.name\n"
            "            LEFT JOIN taxonomy_classes parent_tc ON tc.parent_id = parent_tc.id"
        )
    else:
        top_class_expr = "COALESCE(MAX(r.asset_class), 'Unclassified')"
        taxonomy_joins = ""
    return db.execute(
        f"""
        {cte}
        SELECT
            h.asset_id,
            MAX(h.asset_name) AS name,
            {top_class_expr} AS top_class,
            COALESCE(MAX(r.asset_class), 'Unclassified') AS sub_class,
            SUM(h.market_value) AS market_value,
            SUM(h.quantity) AS quantity,
            MAX(h.cost_price_unit) AS cost_price_unit,
            MAX(h.market_price_unit) AS market_price_unit,
            MAX(h.currency) AS currency,
            MAX(r.asset_class) AS asset_class_registry,
            MAX(h.source_system) AS source_system
        FROM holdings h
        JOIN latest_per_asset lpa ON h.asset_id = lpa.asset_id AND h.snapshot_date = lpa.latest_date
        LEFT JOIN asset_registry r ON h.asset_id = r.canonical_id
        {taxonomy_joins}
        WHERE h.is_shadow = FALSE
        GROUP BY h.asset_id
        {having}
        """,
        params or None,
    ).fetchall()


def latest_snapshot_date(db, start_date: Optional[str] = None) -> Optional[str]:
    """The "as of" display date: ``MAX(snapshot_date)`` over active holdings.

    This is a portfolio-wide display date, not a per-asset value lookup, so the
    global MAX here is intentional and does not violate Rule 3.
    """
    if start_date:
        row = db.execute(
            """
            SELECT MAX(snapshot_date)
            FROM holdings
            WHERE is_shadow=FALSE AND snapshot_date >= ?
            """,
            (start_date,),
        ).fetchone()
    else:
        row = db.execute(
            "SELECT MAX(snapshot_date) FROM holdings WHERE is_shadow=FALSE"
        ).fetchone()
    return str(row[0]) if row and row[0] else None


def assets_with_transactions(db) -> set[str]:
    """asset_ids carrying at least one transaction (balance-only discriminator).

    A holding absent from this set AND without a cost basis is a reported
    *balance* (e.g. a Financial-Summary bond column) whose lifetime P&L is
    unknown — the ``is_balance_only_holding`` predicate depends on it.
    """
    try:
        return {
            str(row[0])
            for row in db.execute(
                "SELECT DISTINCT asset_id FROM transactions WHERE asset_id IS NOT NULL"
            ).fetchall()
            if row and row[0]
        }
    except Exception:
        return set()


def assets_with_reader_transactions(db) -> set[str]:
    """asset_ids fed by a NON-legacy (reader) transaction ledger.

    Drives "may the owner log P&L here?" (#7). An asset whose P&L already comes
    from a real broker/fund ledger must not be offered a manual override — the
    engine would supersede it anyway (plan §C.1), so offering it would invite the
    owner to type a figure that is then ignored.

    Legacy/PIS sources deliberately do NOT count: they are historical baseline
    (ADR-003), not a live ledger, so an asset carrying only PIS rows is still
    manually loggable.

    This is one query rather than a per-asset ``select_transaction_sources`` call
    (which costs an AuthorityResolver round trip each). The two can disagree only
    in one edge case — an asset whose *holdings* authority is legacy while it also
    carries reader transactions — where this returns "has reader data" and
    supersession would not fire. That direction is the safe one: the affordance is
    withheld rather than offered-then-ignored.
    """
    from src.services.transaction_source_selector import LEGACY_TRANSACTION_SOURCES

    placeholders = ", ".join("?" for _ in LEGACY_TRANSACTION_SOURCES)
    try:
        return {
            str(row[0])
            for row in db.execute(
                "SELECT DISTINCT asset_id FROM transactions "
                "WHERE asset_id IS NOT NULL AND source_system IS NOT NULL "
                f"AND source_system NOT IN ({placeholders})",
                sorted(LEGACY_TRANSACTION_SOURCES),
            ).fetchall()
            if row and row[0]
        }
    except Exception:
        return set()


def sold_after_snapshot(db) -> set[str]:
    """asset_ids fully sold *after* their latest non-shadow snapshot.

    The ETHA case: a position sold 2026-02-04 whose last non-shadow row is
    2026-01-29 (qty>0) would otherwise still read as active. Verbatim copy of the
    WealthOS ``sold_check`` query. The caller restricts removal to
    reader-source holdings (``holding_source_systems``) so QDII-lagged assets are
    not wrongly closed.
    """
    rows = db.execute(
        """
        WITH latest_holding AS (
            SELECT asset_id, MAX(snapshot_date) AS latest_date
            FROM holdings WHERE is_shadow = FALSE
            GROUP BY asset_id
        ),
        post_tx AS (
            SELECT
                t.asset_id,
                MAX(CASE WHEN LOWER(t.transaction_type) IN ('sell', 'adjustment_sell')
                         AND COALESCE(t.quantity, 0) != 0
                         AND t.transaction_date > lh.latest_date
                    THEN t.transaction_date END) AS last_post_sell,
                MAX(CASE WHEN LOWER(t.transaction_type) IN (
                            'buy', 'adjustment_buy', 'vest', 'transfer_in', 'rsu_vest', 'premium_payment')
                         AND COALESCE(t.quantity, 0) != 0
                         AND t.transaction_date > lh.latest_date
                    THEN t.transaction_date END) AS last_post_buy,
                SUM(CASE
                        WHEN t.transaction_date <= lh.latest_date THEN 0
                        WHEN LOWER(t.transaction_type) IN (
                            'buy', 'adjustment_buy', 'vest', 'transfer_in', 'rsu_vest', 'premium_payment')
                            THEN ABS(COALESCE(t.quantity, 0))
                        WHEN LOWER(t.transaction_type) IN ('sell', 'adjustment_sell')
                            THEN -ABS(COALESCE(t.quantity, 0))
                        ELSE 0
                    END) AS net_post_snapshot_qty
            FROM transactions t
            JOIN latest_holding lh ON t.asset_id = lh.asset_id
            GROUP BY t.asset_id
        ),
        sold_done AS (
            SELECT asset_id FROM post_tx
            WHERE last_post_sell IS NOT NULL
              AND (last_post_buy IS NULL OR last_post_buy <= last_post_sell)
              AND COALESCE(net_post_snapshot_qty, 0) <= 0
        )
        SELECT asset_id FROM sold_done
        """
    ).fetchall()
    return {r[0] for r in rows if r and r[0]}


def first_buy_dates(db) -> dict:
    """asset_id -> earliest buy-type ``transaction_date`` (holding-period start)."""
    return {
        r[0]: r[1]
        for r in db.execute(
            """
            SELECT asset_id, MIN(transaction_date) AS first_date
            FROM transactions
            WHERE LOWER(transaction_type) IN (
                'buy', 'vest', 'transfer_in', 'rsu_vest', 'premium_payment')
            GROUP BY asset_id
            """
        ).fetchall()
    }


def total_invested_native(db) -> dict:
    """asset_id -> SUM(quantity * price_unit) over buy-type rows (native currency).

    The closed-asset invested basis: matches the FIFO lot cost the
    ``CostBasisCalculator`` sums, in the asset's native currency.
    """
    return {
        r[0]: float(r[1] or 0.0)
        for r in db.execute(
            """
            SELECT asset_id, SUM(quantity * price_unit) AS total_invested
            FROM transactions
            WHERE LOWER(transaction_type) IN (
                'buy', 'vest', 'transfer_in', 'rsu_vest', 'premium_payment')
            GROUP BY asset_id
            """
        ).fetchall()
    }


def transaction_currency(db) -> dict:
    """asset_id -> MAX(currency) over its transactions (closed-asset currency)."""
    return {
        r[0]: str(r[1] or "CNY")
        for r in db.execute(
            """
            SELECT asset_id, MAX(currency) AS currency
            FROM transactions
            WHERE asset_id IS NOT NULL
            GROUP BY asset_id
            """
        ).fetchall()
    }


def closed_asset_meta(db, closed_ids: list) -> dict:
    """asset_id -> {name, type, top_class, sub_class} for closed (fully-sold)
    assets, from transactions joined to the registry taxonomy.

    ``top_class`` / ``sub_class`` are the *raw* taxonomy strings resolved through
    the same ``COALESCE(parent_tc.name, tc.name, r.asset_class, 'Unclassified')``
    ladder the current-holdings query uses, so a by-class surface can land a
    closed asset's realized P&L in exactly the class the legacy per-site query
    did. ``'Unclassified'`` when the asset has no registry row (LEFT JOIN NULL),
    matching the legacy default. The caller still applies ``resolve_top_class`` /
    ``get_display_name`` on top, identical to the active-asset path.
    """
    if not closed_ids:
        return {}
    placeholders = ", ".join("?" for _ in closed_ids)
    return {
        r[0]: {
            "name": r[1],
            "type": r[2] or "Unknown",
            "top_class": r[3] or "Unclassified",
            "sub_class": r[4] or "Unclassified",
        }
        for r in db.execute(
            f"""
            SELECT
                t.asset_id,
                MAX(t.asset_name) AS name,
                MAX(r.asset_class) AS type,
                COALESCE(MAX(parent_tc.name), MAX(tc.name), MAX(r.asset_class), 'Unclassified') AS top_class,
                COALESCE(MAX(r.asset_class), 'Unclassified') AS sub_class
            FROM transactions t
            LEFT JOIN asset_registry r ON t.asset_id = r.canonical_id
            LEFT JOIN taxonomy_classes tc ON r.asset_class = tc.name
            LEFT JOIN taxonomy_classes parent_tc ON tc.parent_id = parent_tc.id
            WHERE t.asset_id IN ({placeholders})
            GROUP BY t.asset_id
            """,
            closed_ids,
        ).fetchall()
    }


def open_value_trap_asset_ids(db) -> set[str]:
    """asset_ids with an open value-trap review (badge). Empty if table absent."""
    try:
        return {
            r[0]
            for r in db.execute(
                "SELECT DISTINCT asset_id FROM value_trap_reviews WHERE status = 'open'"
            ).fetchall()
        }
    except Exception:
        return set()


def transaction_asset_ids(db, start_date: Optional[str] = None) -> set[str]:
    """Distinct asset_ids appearing in transactions, floored at ``start_date``.

    Unioned with the current holdings to build the realized-P&L asset set —
    exactly the ``UNION SELECT DISTINCT asset_id FROM transactions`` the legacy
    performance summary used, so closed (fully-sold) assets still contribute
    their realized P&L.
    """
    if start_date:
        rows = db.execute(
            "SELECT DISTINCT asset_id FROM transactions WHERE transaction_date >= ?",
            (start_date,),
        ).fetchall()
    else:
        rows = db.execute("SELECT DISTINCT asset_id FROM transactions").fetchall()
    return {row[0] for row in rows if row and row[0]}
