
"""Identity sync module for Phase 1."""

from typing import Dict, Any
import logging

from src.database.connector import DatabaseConnector

logger = logging.getLogger(__name__)


def sync_asset_registry(connector: DatabaseConnector, config: Dict[str, Any]) -> Dict[str, int]:
    """
    Sync asset registry and source mappings.
    
    Returns:
        Dict with counts 'registry_inserted', 'mappings_inserted'
    """
    results = {'registry_inserted': 0, 'mappings_inserted': 0}

    # 1.5 Financial Summary Manual Assets (Phase 27 Refinement)
    # These assets (Wealth_*, Property_*, etc.) are only in Excel, not PIS/AIA.
    from src.sources.reader_hooks import FS_ASSET_MAPPING
    for excel_col, (canonical_id, display_name, currency) in FS_ASSET_MAPPING.items():
        try:
            # Determine asset class from common prefixes (Program OSR WS-2
            # step 5: Wealth_ generalized from an exact-match hardcode of
            # Ray's own asset_id "Wealth_CMB" — a self-hoster's own bank
            # wealth product, e.g. "Wealth_ICBC", now classifies the same
            # way without a code edit).
            asset_class = None
            if canonical_id.startswith("Wealth_"):
                asset_class = "Bank Wealth"
            elif canonical_id.startswith("CASH_"):
                asset_class = "Cash Checking"
            elif canonical_id.startswith("Property_"):
                asset_class = "Property"
            elif canonical_id.startswith("Pension_"):
                asset_class = "Pension"
            
            connector.execute("""
                INSERT INTO asset_registry (canonical_id, display_name, asset_class, base_currency)
                VALUES (?, ?, ?, ?)
                ON CONFLICT (canonical_id) DO UPDATE 
                SET display_name = EXCLUDED.display_name,
                    asset_class = COALESCE(asset_registry.asset_class, EXCLUDED.asset_class),
                    base_currency = COALESCE(asset_registry.base_currency, EXCLUDED.base_currency)
            """, (canonical_id, display_name, asset_class, currency))
            
            results['registry_inserted'] += 1
        except Exception as e:
            logger.warning(f"Failed to sync Financial Summary asset {canonical_id}: {e}")

    # 1.6 Register reader-created synthetic assets
    synthetic_assets = [
        ("CASH_USD", "Cash (USD Schwab)", "Cash Checking", "USD"),
    ]
    for canonical_id, display_name, asset_class, currency in synthetic_assets:
        try:
            connector.execute("""
                INSERT INTO asset_registry (canonical_id, display_name, asset_class, base_currency)
                VALUES (?, ?, ?, ?)
                ON CONFLICT (canonical_id) DO UPDATE
                SET asset_class = COALESCE(asset_registry.asset_class, EXCLUDED.asset_class)
            """, (canonical_id, display_name, asset_class, currency))
            results['registry_inserted'] += 1
        except Exception as e:
            logger.warning(f"Failed to register synthetic asset {canonical_id}: {e}")

    return results
