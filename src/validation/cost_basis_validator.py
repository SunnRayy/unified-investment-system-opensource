"""Validate holdings.cost_price_unit against transaction-derived cost basis."""

import logging
from typing import Dict, List, Optional, Any

from src.database.connector import DatabaseConnector
from src.sources.registry import get_registry

logger = logging.getLogger(__name__)

# Derived from registry — same name, same value (5-element tuple, no Financial_Summary_Excel).
READER_HOLDING_SOURCES: tuple = get_registry().holding_source_systems()

_KNOWN_EXCEPTION_ASSET_IDS = {"CN_FUND_110020", "CN_FUND_161725", "RSU_AMZN", "RSU_RSU_AMZN"}
def _is_known_exception(asset_id: str) -> bool:
    return str(asset_id) in _KNOWN_EXCEPTION_ASSET_IDS
def _normalize_transactions_for_validation(df, holding_source: str):
    """Align validator math with the source-specific holdings convention.

    V5.2.0+: Schwab cost_price_unit is stored in native USD (same as transaction
    price_unit). No FX conversion needed — CostBasisCalculator works in native currency.
    """
    # No-op: transactions are in native USD; cost_price_unit is also in native USD.
    # (Pre-V5.2.0 this multiplied by USD_TO_CNY_RATE=7.0 — removed after native-currency fix.)
    return df


def validate_cost_basis(
    connector: DatabaseConnector,
    threshold_pct: float = 1.0,
    asset_id: Optional[str] = None
) -> List[Dict[str, Any]]:
    """Validate synced holdings against transaction-derived average cost."""
    # Reader sources whose cost is FIFO-computed by Huinsight (PIS holdings are authoritative ground truth)
    reader_in = ", ".join(f"'{s}'" for s in READER_HOLDING_SOURCES)

    discrepancies = []

    if asset_id:
        assets_query = f"""
            SELECT DISTINCT h.asset_id, h.source_system
            FROM holdings h
            JOIN (
                SELECT source_system, MAX(snapshot_date) AS max_date
                FROM holdings
                WHERE is_shadow = FALSE AND source_system IN ({reader_in})
                GROUP BY source_system
            ) ls ON h.source_system = ls.source_system AND h.snapshot_date = ls.max_date
            WHERE h.asset_id = ?
              AND h.is_shadow = FALSE
              AND h.cost_price_unit IS NOT NULL
        """
        assets_result = connector.execute(assets_query, (asset_id,)).fetchall()
    else:
        assets_query = f"""
            SELECT DISTINCT h.asset_id, h.source_system
            FROM holdings h
            JOIN (
                SELECT source_system, MAX(snapshot_date) AS max_date
                FROM holdings
                WHERE is_shadow = FALSE AND source_system IN ({reader_in})
                GROUP BY source_system
            ) ls ON h.source_system = ls.source_system AND h.snapshot_date = ls.max_date
            WHERE h.is_shadow = FALSE
              AND h.cost_price_unit IS NOT NULL
        """
        assets_result = connector.execute(assets_query).fetchall()

    logger.info(f"Validating cost basis for {len(assets_result)} reader-sourced holdings")

    from src.services.transaction_source_selector import select_transaction_sources

    for (current_asset_id, holding_source) in assets_result:
        try:
            if _is_known_exception(str(current_asset_id)):
                logger.debug("%s: known cost basis edge case, skipping validation", current_asset_id)
                continue

            if 'Ins_' in str(current_asset_id):
                continue

            if str(current_asset_id).startswith('CASH_'):
                continue

            synced_query = """
                SELECT cost_price_unit, quantity, asset_name
                FROM holdings
                WHERE asset_id = ?
                  AND source_system = ?
                  AND is_shadow = FALSE
                  AND snapshot_date = (
                      SELECT MAX(snapshot_date) FROM holdings
                      WHERE source_system = ? AND is_shadow = FALSE
                  )
                LIMIT 1
            """
            synced_result = connector.execute(
                synced_query, (current_asset_id, holding_source, holding_source)
            ).fetchone()

            if not synced_result or synced_result[0] is None:
                logger.debug(f"{current_asset_id}: No synced cost_price_unit, skipping")
                continue

            synced_cost = float(synced_result[0])
            quantity = float(synced_result[1]) if synced_result[1] else 0.0
            asset_name = synced_result[2] or current_asset_id

            if 'RSU_AMZN' in str(current_asset_id):
                logger.info("DEBUG RSU_AMZN: Synced Cost=%s, Quantity=%s", synced_cost, quantity)

            if quantity <= 0:
                logger.debug(f"{current_asset_id}: Zero or negative quantity, skipping")
                continue

            selected_sources = select_transaction_sources(connector, current_asset_id)
            if not selected_sources:
                logger.debug(f"{current_asset_id}: No selected transaction sources, skipping")
                continue

            placeholders = ", ".join(["?"] * len(selected_sources))
            tx_query = f"""
                SELECT
                    transaction_type,
                    quantity,
                    price_unit,
                    amount_net,
                    currency,
                    transaction_date
                FROM transactions
                WHERE asset_id = ?
                  AND source_system IN ({placeholders})
                ORDER BY transaction_date ASC
            """
            tx_rows = connector.execute(
                tx_query, (current_asset_id, *selected_sources)
            ).fetchall()
            
            if not tx_rows:
                logger.debug(f"{current_asset_id}: No valid transaction data for calculation")
                continue

            import pandas as pd
            from src.financial_analysis.cost_basis import CostBasisCalculator
            
            df = pd.DataFrame(tx_rows, columns=[
                'transaction_type', 'quantity', 'price_unit', 'amount_net', 'currency', 'transaction_date'
            ])
            df = _normalize_transactions_for_validation(df, holding_source)
            
            df['transaction_date'] = pd.to_datetime(df['transaction_date'])
            df.set_index('transaction_date', inplace=True)
            
            calculator = CostBasisCalculator(current_asset_id)
            calculator.process_transactions(df)
            
            net_shares = calculator.get_current_position()
            total_bought_cost = calculator.get_total_cost_basis()
            
            if net_shares <= 0 or total_bought_cost <= 0:
                 logger.debug(f"{current_asset_id}: Cannot calculate average cost (zero shares or zero cost)")
                 continue

            calculated_cost = calculator.get_average_cost()
            
            if calculated_cost > 0:
                diff_pct = abs(synced_cost - calculated_cost) / calculated_cost * 100
            elif synced_cost > 0:
                diff_pct = 100.0
            else:
                diff_pct = 0.0

            if diff_pct > threshold_pct:
                discrepancy = {
                    'asset_id': current_asset_id,
                    'asset_name': asset_name,
                    'synced_cost': synced_cost,
                    'calculated_cost': calculated_cost,
                    'diff_pct': diff_pct,
                    'quantity': quantity,
                    'total_bought': total_bought_cost,
                    'net_shares': net_shares
                }
                discrepancies.append(discrepancy)

                logger.warning(
                    f"Cost basis discrepancy for {current_asset_id}: "
                    f"synced={synced_cost:.2f}, calculated={calculated_cost:.2f} "
                    f"({diff_pct:.2f}% diff)"
                )

                # Log to sync_audit_logs table
                _log_discrepancy(connector, discrepancy)
        except Exception as e:
            logger.error(f"Error validating asset {current_asset_id}: {e}")
            continue

    logger.info(f"Cost basis validation complete: {len(discrepancies)} discrepancies found")
    return discrepancies


def _log_discrepancy(connector: DatabaseConnector, discrepancy: Dict[str, Any]) -> None:
    """
    Log a cost basis discrepancy to the sync_audit_logs table.

    Args:
        connector: DuckDB database connector
        discrepancy: Discrepancy dictionary with validation results
    """
    try:
        # Check if a similar unresolved discrepancy already exists
        check_query = """
            SELECT id FROM sync_audit_logs
            WHERE source_system = 'PIS'
            AND target_table = 'holdings'
            AND record_key = ?
            AND conflict_type = 'cost_basis_mismatch'
            AND is_resolved = FALSE
            LIMIT 1
        """
        existing = connector.execute(check_query, (discrepancy['asset_id'],)).fetchone()

        if existing:
            # Update existing discrepancy
            update_query = """
                UPDATE sync_audit_logs
                SET sync_timestamp = CURRENT_TIMESTAMP,
                    source_value = ?,
                    target_value = ?,
                    resolution_notes = ?
                WHERE id = ?
            """
            connector.execute(update_query, (
                str(discrepancy['synced_cost']),
                str(discrepancy['calculated_cost']),
                f"Updated: {discrepancy['diff_pct']:.2f}% difference detected",
                existing[0]
            ))
            logger.debug(f"Updated existing audit log for {discrepancy['asset_id']}")
        else:
            # Insert new discrepancy
            insert_query = """
                INSERT INTO sync_audit_logs (
                    sync_timestamp,
                    source_system,
                    target_table,
                    record_key,
                    conflict_type,
                    source_value,
                    target_value,
                    resolution,
                    resolution_notes,
                    is_resolved
                ) VALUES (
                    CURRENT_TIMESTAMP,
                    'PIS',
                    'holdings',
                    ?,
                    'cost_basis_mismatch',
                    ?,
                    ?,
                    'flagged_for_review',
                    ?,
                    FALSE
                )
            """
            connector.execute(insert_query, (
                discrepancy['asset_id'],
                str(discrepancy['synced_cost']),
                str(discrepancy['calculated_cost']),
                f"Synced: {discrepancy['synced_cost']:.2f}, Calculated: {discrepancy['calculated_cost']:.2f} ({discrepancy['diff_pct']:.2f}% diff)"
            ))
            logger.debug(f"Created new audit log for {discrepancy['asset_id']}")

    except Exception as e:
        logger.error(f"Failed to log discrepancy for {discrepancy['asset_id']}: {e}")
