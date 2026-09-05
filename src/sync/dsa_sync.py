
# src/sync/dsa_sync.py
"""Sync market data from Daily Stock Analysis and other sources to unified database."""

import logging
import sqlite3
import pandas as pd
from datetime import date
from pathlib import Path
from typing import Dict, Any

from src.database.connector import DatabaseConnector
from src.config import get_subsystem_path
from src.utils.asset_id import AssetIdNormalizer
from src.market_data.service import MarketDataService

logger = logging.getLogger(__name__)


def sync_market_data(connector: DatabaseConnector, config: Dict[str, Any]) -> int:
    """
    Sync daily market data from all sources to unified database.

    .. deprecated::
        This function syncs historical market_daily data from DSA SQLite. It is kept
        for historical data ingest only. Live prices are refreshed by
        MarketDataService.refresh_portfolio_prices() which is called separately in the
        orchestrator after reader inserts. Do NOT remove this function.

    Sources:
    1. DSA SQLite (Stocks, ETFs)
    2. CN Fund Scraper (Mutual Funds)

    Args:
        connector: DuckDB connector
        config: Application configuration

    Returns:
        Total number of records synced
    """
    logger.warning(
        "sync_market_data: this function provides historical DSA SQLite ingest only. "
        "Live prices are refreshed by MarketDataService.refresh_portfolio_prices() "
        "called after reader inserts."
    )
    total_synced = 0
    
    # 1. Sync from DSA SQLite
    total_synced += _sync_dsa_sqlite(connector, config)
    
    # 2. Sync CN Funds
    total_synced += _sync_cn_funds(connector)
    
    return total_synced


def _sync_dsa_sqlite(connector: DatabaseConnector, config: Dict[str, Any]) -> int:
    """Sync from legacy Daily Stock Analysis SQLite DB."""
    dsa_path = get_subsystem_path(config, 'daily_stock_analysis')
    if not dsa_path:
        # logger.warning("DSA path not configured") # Reduce noise if not configured
        return 0

    db_file = config['subsystems']['daily_stock_analysis'].get(
        'data_sources', {}
    ).get('market_db', 'data/stock_analysis.db')

    full_path = Path(dsa_path) / db_file

    if not full_path.exists():
        # logger.warning(f"DSA DB not found at {full_path}")
        return 0

    conn = sqlite3.connect(str(full_path))
    try:
        df = pd.read_sql_query("""
            SELECT
                code, date, open, high, low, close,
                volume, amount, pct_chg, data_source
            FROM stock_daily
            ORDER BY date
        """, conn)
    finally:
        conn.close()

    if df.empty:
        return 0

    # Normalize stock codes (DSA already uses 6-digit, but ensure consistency)
    normalizer = AssetIdNormalizer()
    df = normalizer.normalize_column(df, 'code')

    count = 0
    # Use executemany or bulk insert if possible, but row-by-row for safety/conflict handling
    # For massive data, we should optimize this. For now, keep existing logic.
    for _, row in df.iterrows():
        try:
            connector.execute("""
                INSERT INTO market_daily (
                    code, date, open, high, low, close,
                    volume, amount, pct_chg, data_source
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT (code, date) DO UPDATE SET
                    open = EXCLUDED.open,
                    high = EXCLUDED.high,
                    low = EXCLUDED.low,
                    close = EXCLUDED.close,
                    volume = EXCLUDED.volume,
                    amount = EXCLUDED.amount,
                    pct_chg = EXCLUDED.pct_chg
            """, (
                row['code'],
                row['date'],
                row['open'],
                row['high'],
                row['low'],
                row['close'],
                row['volume'],
                row['amount'],
                row['pct_chg'],
                row.get('data_source') or 'DSA'
            ))
            count += 1
        except Exception as e:
            logger.warning(f"Could not insert DSA market data: {e}")

    return count


def _sync_cn_funds(connector: DatabaseConnector) -> int:
    """Sync CN Fund market data using MarketDataService."""
    count = 0
    try:
        # 1. Start Service
        service = MarketDataService()
        
        # 2. Get active CN Funds from registry
        # We look for assets starting with CN_FUND_ that are active
        # Assuming asset_registry has 'status' column or we just sync all
        # To be safe, let's sync all CN_FUND assets in registry.
        
        # Check if asset_registry exists first (it should)
        
        # Note: connector.execute returns a relation object in DuckDB python client, 
        # which can be iterated or fetched.
        active_funds = connector.execute("""
            SELECT canonical_id FROM asset_registry
            WHERE canonical_id LIKE 'CN_FUND_%'
        """).fetchall()
        
        if not active_funds:
            return 0
            
        today = date.today()
        # Start date: For now, let's default to a reasonable history (e.g. 1 year) 
        # or we could check max date in DB.
        # For this implementation, let's fetch last 30 days to avoid massive overhead 
        # or maybe we should optimize to fetch only missing?
        # Optimization: Check max date for each fund.
        # But for Batch 2, let's keep it simple: Fetch YTD or fix range.
        # Let's use 2024-01-01 as hardcoded start for now, or dynamic.
        start_date = date(2025, 1, 1) 
        
        for (canonical_id,) in active_funds:
            fund_code = canonical_id.replace("CN_FUND_", "")
            try:
                # Optimization: Get max date in market_daily for this asset
                max_date_row = connector.execute(
                    "SELECT MAX(date) FROM market_daily WHERE code = ?",
                    (fund_code,)
                ).fetchone()
                
                fs_start = start_date
                if max_date_row and max_date_row[0]:
                    # Start from next day
                    # But scraper fetch_history is inclusive, so maybe max_date is fine?
                    # Let's just fetch from max_date to ensure no gaps (upsert handles duplicates)
                    # Convert string/timestamp to date if needed
                    # DuckDB returns datetime.date or similar
                    last_date = max_date_row[0]
                    # If last_date is recent (today/yesterday), skip
                    if last_date >= today:
                        continue
                    fs_start = last_date
                
                df = service.get_market_data(canonical_id, start_date=fs_start, end_date=today)
                
                if df.empty:
                    continue
                    
                # Insert into DB
                for _, row in df.iterrows():
                    connector.execute("""
                        INSERT INTO market_daily (
                            code, date, close, data_source
                        ) VALUES (?, ?, ?, ?)
                        ON CONFLICT (code, date) DO UPDATE SET
                            close = EXCLUDED.close,
                            data_source = EXCLUDED.data_source
                    """, (
                        fund_code,
                        row['date'],
                        row['close'],
                        'CN_Fund_Scraper'
                    ))
                    count += 1
                    
            except Exception as e:
                logger.error(f"Failed to sync market data for {canonical_id}: {e}")
                
    except Exception as e:
        logger.error(f"Error in _sync_cn_funds: {e}")
        
    return count


def update_holdings_prices(
    connector: DatabaseConnector,
    fx_rates: dict = None,
) -> Dict[str, int]:
    """
    Update holdings market prices from all available sources.

    Sources:
    1. DSA market_daily table
    """
    if fx_rates is None:
        fx_rates = {"USD": 7.0, "HKD": 0.9}

    dsa_count = _update_from_dsa(connector, fx_rates)

    return {
        "dsa": dsa_count,
    }


def _update_from_dsa(connector: DatabaseConnector, fx_rates: dict) -> int:
    """
    Update holdings market prices from market_daily (DSA).

    Canonical IDs use the pattern MARKET_TYPE_CODE (e.g. CN_FUND_161725,
    US_STK_GOOG) while market_daily stores raw codes (900008, GOOG).
    We strip the prefix via REGEXP_EXTRACT to align them.

    IMPORTANT: market_daily stores prices in the asset's native currency (USD for
    US stocks, CNY for CN funds). market_value must always be stored in CNY (the
    portfolio base currency). The fx_rates dict provides conversion rates; if absent,
    falls back to the standard config defaults (USD→CNY=7.0, HKD→CNY=0.9).

    Args:
        connector: DuckDB connector
        fx_rates: Optional dict mapping currency codes to CNY rates,
                  e.g. {"USD": 7.0, "HKD": 0.9}. Defaults to config fallback rates.

    Returns:
        Number of holdings rows updated.
    """
    usd_rate = fx_rates.get("USD", 7.0)
    hkd_rate = fx_rates.get("HKD", 0.9)

    # market_value is always in CNY. Apply FX conversion based on the holding's
    # currency field. CN funds (currency=CNY) use rate 1.0; USD/HKD assets convert.
    query = f"""
    UPDATE holdings
    SET
        market_price_unit = md.close,
        market_value = holdings.quantity * md.close * CASE
            WHEN holdings.currency = 'USD' THEN {usd_rate}
            WHEN holdings.currency = 'HKD' THEN {hkd_rate}
            ELSE 1.0
        END,
        price_updated_at = md.date,
        price_source = md.data_source,
        updated_at = CURRENT_TIMESTAMP
    FROM market_daily md
    WHERE (
        -- 3-part canonical ID: CN_FUND_161725 → 161725
        REGEXP_EXTRACT(holdings.asset_id, '^[^_]+_[^_]+_(.+)$', 1) = md.code
        OR
        -- 2-part canonical ID: RSU_AMZN → AMZN
        (REGEXP_EXTRACT(holdings.asset_id, '^[^_]+_[^_]+_(.+)$', 1) = ''
         AND REGEXP_EXTRACT(holdings.asset_id, '^[^_]+_(.+)$', 1) = md.code)
    )
      AND md.date = (
          SELECT MAX(date) FROM market_daily md2 WHERE md2.code = md.code
      )
      AND (
          holdings.price_updated_at IS NULL
          OR CAST(holdings.price_updated_at AS DATE) < md.date
          OR md.close != holdings.market_price_unit
      )
      AND holdings.is_shadow = FALSE
      -- Reader-first authority: a reader-provided price embedded at snapshot_date is fresher
      -- than any older market_daily close; never regress.
      -- Observed: stale Jul-1 gold close overwrote a Jul-6 gold Excel price.
      AND md.date >= CAST(holdings.snapshot_date AS DATE)
    """
    try:
        # Count matching rows before update (DuckDB has no changes() function)
        count_query = """
        SELECT COUNT(*)
        FROM holdings h
        JOIN market_daily md ON (
            REGEXP_EXTRACT(h.asset_id, '^[^_]+_[^_]+_(.+)$', 1) = md.code
            OR (REGEXP_EXTRACT(h.asset_id, '^[^_]+_[^_]+_(.+)$', 1) = ''
                AND REGEXP_EXTRACT(h.asset_id, '^[^_]+_(.+)$', 1) = md.code)
        )
        WHERE md.date = (SELECT MAX(date) FROM market_daily md2 WHERE md2.code = md.code)
          AND (h.price_updated_at IS NULL
               OR CAST(h.price_updated_at AS DATE) < md.date
               OR md.close != h.market_price_unit)
          AND h.is_shadow = FALSE
          -- Reader-first authority: only count/update if market date is not older than snapshot.
          AND md.date >= CAST(h.snapshot_date AS DATE)
        """
        result = connector.execute(count_query).fetchone()
        updated = result[0] if result else 0
        connector.execute(query)
        logger.info(
            f"Updated {updated} holdings prices from DSA market data "
            f"(FX: USD={usd_rate}, HKD={hkd_rate})."
        )
        return updated
    except Exception as e:
        logger.error(f"Error updating holdings prices: {e}")
        return 0


