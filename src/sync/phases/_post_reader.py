"""Post-reader pipeline steps: FIFO cost-basis, insurance cost, non-tradeable P&L,
RSU price updates, and adapter authority rule loading.

Extracted verbatim from src/sync/orchestrator.py (lines 1005-1358, 1757-1788).
Do NOT call these functions directly from application code — use orchestrator.py.
"""

import logging
from typing import Any, Dict

import pandas as pd

from src.database.connector import DatabaseConnector
from src.sync.phases._common import (
    INSURANCE_PREFIXES,
    NON_TRADEABLE_PREFIXES,
    READER_HOLDING_SOURCES,
)

logger = logging.getLogger(__name__)


def _backfill_fifo_cost_basis(connector: DatabaseConnector) -> int:
    """Compute FIFO remaining cost basis for reader-sourced holdings with NULL cost.

    Cost basis is stored in the asset's native currency (USD for Schwab_CSV/RSU_Excel,
    CNY for all others). The Schwab/RSU readers provide cost_price_unit directly from
    the source file (already in USD), so this backfill only runs for assets where the
    reader left cost_price_unit as NULL (e.g. new imports without a cost basis field).
    FX conversion is the caller's responsibility at display time — see the P&L method
    comment in src/api/routes/performance.py.
    """
    # One-time migration: null out stale CNY cost_price_unit for USD-sourced holdings.
    # Detectable because _update_from_dsa stores market_price_unit in native USD
    # (from market_daily), but the old FIFO backfill computed cost_price_unit in CNY.
    # Ratio > 4.5 reliably identifies CNY values (FX rate ≈ 6.8–7.2, vs ratio ≤ 1.3
    # for a normal USD cost above/below market). Safe threshold: even a stock down 75%
    # would have cost/market ≈ 4.0, so 4.5 avoids false positives.
    USD_SOURCES = ("Schwab_CSV", "RSU_Excel")
    usd_src_list = ", ".join(f"'{s}'" for s in USD_SOURCES)
    connector.execute(f"""
        UPDATE holdings
        SET cost_price_unit = NULL
        WHERE source_system IN ({usd_src_list})
          AND is_shadow = FALSE
          AND cost_price_unit IS NOT NULL
          AND market_price_unit IS NOT NULL
          AND market_price_unit > 0
          AND cost_price_unit / market_price_unit > 4.5
    """)
    logger.debug(
        "FIFO migration: nulled stale CNY cost_price_unit for USD-sourced holdings "
        "(Schwab_CSV, RSU_Excel) where cost/market ratio > 4.5"
    )

    # C3.3 RISK-3: co-authority broker holdings (e.g. IBKR) carry cost_price_unit=0 for
    # transferred-in lots. Null them so the FIFO loop below recomputes cost from the MERGED
    # ledger (both brokers' transactions). Cash is excluded (cash cost basis is not FIFO).
    from src.identity.authority_resolver import AuthorityResolver
    # Single resolver instance reused for the null-out below AND the per-asset FIFO loop
    # (avoids re-reading config/source_authority.yaml once per asset).
    resolver = AuthorityResolver()
    coauth_sources = resolver.coauthority_sources()
    if coauth_sources:
        coauth_list = ", ".join(f"'{s}'" for s in sorted(coauth_sources))
        connector.execute(f"""
            UPDATE holdings
            SET cost_price_unit = NULL
            WHERE source_system IN ({coauth_list})
              AND is_shadow = FALSE
              AND COALESCE(cost_price_unit, 0) = 0
              AND quantity > 0
              AND asset_id NOT LIKE 'CASH_%'
        """)
        logger.debug(
            "FIFO C3.3: nulled cost_price_unit=0 for co-authority broker holdings "
            "(%s) so merged-ledger FIFO recomputes cost from both brokers' transactions",
            coauth_list,
        )

    reader_list = ", ".join(f"'{source}'" for source in READER_HOLDING_SOURCES)
    rows = connector.execute(
        f"""
        WITH latest_snap AS (
            SELECT asset_id, source_system, MAX(snapshot_date) AS max_date
            FROM holdings
            WHERE is_shadow = FALSE
              AND source_system IN ({reader_list})
            GROUP BY asset_id, source_system
        )
        SELECT h.asset_id, h.quantity, h.source_system, h.snapshot_date
        FROM holdings h
        JOIN latest_snap ls ON h.asset_id = ls.asset_id
                            AND h.source_system = ls.source_system
                            AND h.snapshot_date = ls.max_date
        WHERE h.is_shadow = FALSE
          AND h.cost_price_unit IS NULL
          AND h.quantity IS NOT NULL
          AND h.quantity > 0
        """,
    ).fetchall()
    if not rows:
        return 0

    updated = 0
    for asset_id, _quantity, source_system, snapshot_date in rows:
        try:
            from src.services.transaction_source_selector import select_transaction_sources

            selected_sources = select_transaction_sources(connector, asset_id, resolver=resolver)
            if not selected_sources:
                continue

            placeholders = ", ".join(["?"] * len(selected_sources))
            tx_rows = connector.execute(
                f"""
                SELECT transaction_type, quantity, price_unit, amount_net,
                       currency, transaction_date
                FROM transactions
                WHERE asset_id = ?
                  AND source_system IN ({placeholders})
                ORDER BY transaction_date ASC
                """,
                [asset_id, *selected_sources],
            ).fetchall()
            if not tx_rows:
                continue

            df = pd.DataFrame(
                tx_rows,
                columns=[
                    "transaction_type",
                    "quantity",
                    "price_unit",
                    "amount_net",
                    "currency",
                    "transaction_date",
                ],
            )
            df["transaction_date"] = pd.to_datetime(df["transaction_date"])
            df.set_index("transaction_date", inplace=True)

            from src.financial_analysis.cost_basis import CostBasisCalculator

            calc = CostBasisCalculator(asset_id)
            calc.process_transactions(df)

            total_cost = calc.get_total_cost_basis()
            remaining_qty = calc.get_current_position()
            if remaining_qty <= 0:
                continue

            cost_per_unit = round(total_cost / remaining_qty, 8)
            connector.execute(
                """
                UPDATE holdings
                SET cost_price_unit = ?
                WHERE asset_id = ?
                  AND snapshot_date = ?
                  AND source_system = ?
                  AND is_shadow = FALSE
                """,
                (cost_per_unit, asset_id, snapshot_date, source_system),
            )
            updated += 1
        except Exception as e:
            logger.warning(f"FIFO backfill failed for {asset_id}: {e}")
            continue

    return updated


def _set_insurance_cost_from_premiums(connector: DatabaseConnector) -> int:
    """Set insurance cost_price_unit from cumulative premium payments."""
    rows = connector.execute(
        """
        WITH latest_snap AS (
            SELECT asset_id, source_system, MAX(snapshot_date) AS max_date
            FROM holdings
            WHERE is_shadow = FALSE
            GROUP BY asset_id, source_system
        )
        SELECT h.asset_id, h.market_value, h.snapshot_date, h.quantity, h.source_system
        FROM holdings h
        JOIN latest_snap ls ON h.asset_id = ls.asset_id
                            AND h.source_system = ls.source_system
                            AND h.snapshot_date = ls.max_date
        WHERE h.is_shadow = FALSE
          AND h.asset_id LIKE 'INS_%'
        """,
    ).fetchall()
    if not rows:
        return 0

    updated = 0
    for asset_id, market_value, snapshot_date, quantity, source_system in rows:
        premium_row = connector.execute(
            """
            SELECT COALESCE(SUM(amount_gross), 0)
            FROM transactions
            WHERE asset_id = ? AND transaction_type = 'premium_payment'
            """,
            (asset_id,),
        ).fetchone()
        total_premiums = float(premium_row[0]) if premium_row else 0.0
        effective_market = float(market_value) if (market_value is not None and not pd.isna(market_value) and float(market_value) > 0) else total_premiums
        cost_total = total_premiums if total_premiums > 0 else effective_market
        effective_qty = float(quantity or 0)
        if effective_qty <= 0:
            effective_qty = 1.0
        cost_per_unit = cost_total / effective_qty

        connector.execute(
            """
            UPDATE holdings
            SET cost_price_unit = ?, quantity = ?, market_value = ?
            WHERE asset_id = ?
              AND snapshot_date = ?
              AND source_system = ?
              AND is_shadow = FALSE
            """,
            (cost_per_unit, effective_qty, effective_market, asset_id, snapshot_date, source_system),
        )
        updated += 1

    return updated


def _zero_pl_for_non_tradeable_assets(connector: DatabaseConnector) -> int:
    """Set cost=market_value for non-tradeable assets so unrealized P&L is zero."""
    non_tradeable_conditions = " OR ".join(
        f"h.asset_id LIKE '{prefix}%'" for prefix in NON_TRADEABLE_PREFIXES
    )
    insurance_conditions = " OR ".join(
        f"h.asset_id LIKE '{prefix}%'" for prefix in INSURANCE_PREFIXES
    )
    rows = connector.execute(
        f"""
        WITH latest_snap AS (
            SELECT asset_id, source_system, MAX(snapshot_date) AS max_date
            FROM holdings WHERE is_shadow = FALSE
            GROUP BY asset_id, source_system
        )
        SELECT h.asset_id, h.market_value, h.snapshot_date, h.source_system
        FROM holdings h
        JOIN latest_snap ls ON h.asset_id = ls.asset_id
                            AND h.source_system = ls.source_system
                            AND h.snapshot_date = ls.max_date
        WHERE h.is_shadow = FALSE
          AND h.market_value > 0
          AND (
              (
                  ({non_tradeable_conditions})
                  AND (h.cost_price_unit IS NULL OR h.cost_price_unit = 0)
              )
              OR (
                  ({insurance_conditions})
                  AND (
                      h.cost_price_unit IS NULL
                      OR h.quantity IS NULL
                      OR h.quantity <> 1
                      OR ABS(h.cost_price_unit - h.market_value) > 0.000001
                  )
              )
          )
        """,
    ).fetchall()
    if not rows:
        return 0

    for asset_id, market_value, snapshot_date, source_system in rows:
        target_cost = float(market_value)
        if asset_id.startswith("Property_"):
            legacy_row = connector.execute(
                """
                SELECT cost_price_unit
                FROM holdings
                WHERE asset_id = ?
                  AND source_system IN ('PIS', 'PIS_Historical')
                  AND is_shadow = TRUE
                  AND cost_price_unit IS NOT NULL
                  AND cost_price_unit > 0
                ORDER BY snapshot_date DESC
                LIMIT 1
                """,
                (asset_id,),
            ).fetchone()
            if legacy_row and legacy_row[0] is not None:
                target_cost = float(legacy_row[0])
            else:
                logger.warning(
                    "No legacy shadow cost found for %s; fallback to market value",
                    asset_id,
                )
        connector.execute(
            """
            UPDATE holdings
            SET cost_price_unit = ?, quantity = 1
            WHERE asset_id = ?
              AND snapshot_date = ?
              AND source_system = ?
              AND is_shadow = FALSE
            """,
            (target_cost, asset_id, snapshot_date, source_system),
        )

    return len(rows)


# see docs/architecture/data-sources.md Change Log (changed from AIA JSON to yfinance at V5.2.1)
def _update_rsu_prices_from_external_sources(connector: DatabaseConnector, config: Dict[str, Any]) -> int:
    """Update RSU_AMZN market price using Huinsight yfinance fetcher (primary) or Financial Summary (fallback)."""
    # 1. Try Huinsight MarketDataService (yfinance)
    yfinance_price = None
    try:
        from src.market_data.service import MarketDataService
        quote = MarketDataService().get_realtime_quote("RSU_AMZN")
        if quote and quote.price and quote.price > 0:
            yfinance_price = quote.price
            logger.info(f"Found yfinance price for RSU_AMZN: ${yfinance_price}")
    except Exception as e:
        logger.warning(f"Error fetching yfinance price for RSU: {e}")

    # 2. Try Financial Summary (fallback)
    fs_price = None
    if not yfinance_price:
        try:
            # Query income_expense_monthly for the latest payload
            # Look for "参考_Amazon Stock Price" in the payload JSON
            fs_row = connector.execute(
                """
                SELECT payload FROM income_expense_monthly
                WHERE payload LIKE '%参考_Amazon Stock Price%'
                ORDER BY transaction_date DESC
                LIMIT 1
                """
            ).fetchone()
            if fs_row:
                import json
                payload = json.loads(fs_row[0])
                val = payload.get("参考_Amazon Stock Price")
                if val:
                    try:
                        fs_price = float(val)
                        logger.info(f"Found Financial Summary price for RSU_AMZN: ${fs_price}")
                    except (ValueError, TypeError):
                        pass
        except Exception as e:
            logger.warning(f"Error reading Financial Summary price for RSU: {e}")

    best_price = yfinance_price or fs_price
    if not best_price:
        return 0

    # Determine price source label
    rsu_price_source = "yfinance" if yfinance_price else "financial_summary"

    # 3. Update holdings table for RSU_AMZN
    # market_value = quantity * best_price * USD_CNY_rate (live rate from yfinance, same source as US stocks)
    from src.market_data.fetchers.yfinance_fetcher import fetch_fx_rates
    usd_cny_rate = float(fetch_fx_rates().get("USD", 7.0))

    # Check if already refreshed today by the portfolio price refresh step — skip if so
    from datetime import date as _date
    today = _date.today()
    already_refreshed = connector.execute(
        """
        SELECT COUNT(*)
        FROM holdings
        WHERE asset_id = 'RSU_AMZN'
          AND is_shadow = FALSE
          AND snapshot_date = (SELECT MAX(snapshot_date) FROM holdings WHERE asset_id = 'RSU_AMZN' AND is_shadow = FALSE)
          AND CAST(price_updated_at AS DATE) = ?
        """,
        (today,),
    ).fetchone()
    if already_refreshed and already_refreshed[0] > 0:
        logger.info(
            "RSU_AMZN price already refreshed today — skipping override"
        )
        return 0

    # Constrain update to only the latest snapshot date to avoid corrupting
    # historical price data across all snapshot rows.
    # Note: market_value is ALWAYS in CNY in this DB schema.
    query = """
        UPDATE holdings
        SET market_price_unit = ?,
            market_value = quantity * ? * ?,
            price_source = ?
        WHERE asset_id = 'RSU_AMZN'
          AND is_shadow = FALSE
          AND snapshot_date = (
              SELECT MAX(snapshot_date) FROM holdings
              WHERE asset_id = 'RSU_AMZN' AND is_shadow = FALSE
          )
    """
    connector.execute(query, (best_price, best_price, usd_cny_rate, rsu_price_source))
    return 1


def _load_adapter_authority_rules(connector: DatabaseConnector) -> list:
    """Load authority rules from approved import adapters.

    Returns a list of rule dicts compatible with AuthorityResolver.rules,
    built from import_adapter_approvals.asset_prefixes_json and authority_priority.
    See ADR-004 for rationale (dynamic injection at sync time).
    """
    import json as _json
    try:
        # ADR-018 Phase 3: AND generated_reader_key IS NULL — reader-backed adapters
        # have their authority declared in source_authority.yaml (written by
        # generate_reader_artifacts); only reader-less adapters need dynamic injection.
        rows = connector.execute(
            "SELECT source_system, asset_prefixes_json, authority_priority "
            "FROM import_adapter_approvals WHERE enabled = TRUE "
            "AND generated_reader_key IS NULL"
        ).fetchall()
    except Exception:
        # Table may not exist in older DBs — safe to skip
        return []

    rules = []
    for source_system, prefixes_json, priority in rows:
        prefixes = _json.loads(prefixes_json) if prefixes_json else []
        for prefix in prefixes:
            # Convert prefix like "US_STK_" to glob pattern "US_STK_*"
            pattern = prefix.rstrip('*') + '*' if not prefix.endswith('*') else prefix
            rules.append({
                'pattern': pattern,
                'authority': source_system,
                'priority': priority,
                'note': f'Dynamic rule from import adapter {source_system}',
            })
    return rules
