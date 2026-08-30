"""Shared utility for filtering non-rebalanceable assets (RE + Insurance).

Canonical source of truth: taxonomy_classes.is_rebalanceable column.
Fallback: asset_registry.is_rebalanceable (unreliable — often TRUE for
Insurance/Property, but used when taxonomy join fails).

Pattern reference: src/services/context_generator.py
"""
from typing import Optional

from src.database.connector import DatabaseConnector
from src.services.verification_config import VerificationConfig, load_verification_config


# Generic category words — safe as code defaults, not identifying on their own.
_DEFAULT_NON_BALANCEABLE_HISTORY_MARKERS: tuple[str, ...] = (
    "real estate",
    "property",
    "insurance",
    "ins",
    "房地产",
    "房产",
    "保险",
)

HISTORY_SKIP_SUFFIXES = ("_USD", "(克)")


def _non_balanceable_history_markers(cfg: Optional[VerificationConfig] = None) -> tuple[str, ...]:
    """Program OSR WS-5b: markers are code defaults (generic category words)
    UNIONed with config/verification.yaml's balance_sheet.
    non_rebalanceable_history_markers — the same cash_like_id_prefixes
    extension idiom src/services/freshness.py uses. A self-hoster's own
    product-name balance-sheet columns (e.g. a specific insurance product)
    are supplied via config, not hardcoded here.
    """
    if cfg is None:
        cfg = load_verification_config()
    return (
        *_DEFAULT_NON_BALANCEABLE_HISTORY_MARKERS,
        *cfg.balance_sheet.non_rebalanceable_history_markers,
    )


def _has_column(db: DatabaseConnector, table_name: str, column_name: str) -> bool:
    try:
        rows = db.execute(f"PRAGMA table_info('{table_name}')").fetchall()
    except Exception:
        return False
    return any(len(row) > 1 and row[1] == column_name for row in rows)

def fetch_non_rebalanceable_asset_ids(db: DatabaseConnector) -> set[str]:
    """Return canonical_ids of assets classified as non-rebalanceable.

    Uses taxonomy_classes.is_rebalanceable as the authority,
    falling back to asset_registry.is_rebalanceable.
    """
    try:
        registry_is_rebalanceable = (
            "r.is_rebalanceable"
            if _has_column(db, "asset_registry", "is_rebalanceable")
            else "NULL"
        )
        rows = db.execute("""
            SELECT r.canonical_id
            FROM asset_registry r
            LEFT JOIN taxonomy_classes tc ON tc.name = r.asset_class
            LEFT JOIN taxonomy_classes parent_tc ON parent_tc.id = tc.parent_id
            WHERE COALESCE(tc.is_rebalanceable, parent_tc.is_rebalanceable, """ + registry_is_rebalanceable + """, TRUE) = FALSE
               OR r.canonical_id LIKE 'Property_%'
        """).fetchall()
        return {row[0] for row in rows if row[0]}
    except Exception as e:
        print(f"ERROR in fetch_non_rebalanceable_asset_ids: {e}")
        return set()

def adjust_balance_sheet_payload(payload: dict, cfg: Optional[VerificationConfig] = None) -> float:
    """Calculate the sum of non-rebalanceable items in a balance sheet payload.

    This is an approximation used for the Net Worth Trend chart and return metrics
    when filtered to rebalanceable-only view.
    """
    markers = _non_balanceable_history_markers(cfg)
    adjustment = 0.0
    for key, value in payload.items():
        if not isinstance(value, (int, float)):
            continue
        key_str = str(key)
        if any(key_str.endswith(suffix) for suffix in HISTORY_SKIP_SUFFIXES):
            continue
        lowered = key_str.lower()
        if any(marker in lowered for marker in markers):
            adjustment += float(value)
    return adjustment
