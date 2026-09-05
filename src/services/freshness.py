"""Asset-level price freshness classification and staleness gate (R2-1).

Single source of truth consumed by:
  - src/services/value_trap.py  (trigger scan — replaces the inline staleness block)
  - src/api/routes/value_trap.py (context endpoint + ruling gate)

Design note on fast_days vs F4.4's 24h:
  PRD F4.4 specifies a 24-hour staleness threshold for 'fast'-class instruments.
  A 1-day threshold would mark any CN fund or US ETF as stale every Monday morning
  (T+1 settlement cycle) and on all market holidays — producing spurious deferrals
  and data_fix items on perfectly normal settlement cadences.  We use 3 calendar
  days as the default (config key staleness.fast_days), which covers weekends while
  still catching genuinely broken feeds (e.g. the 14-day NAV gap in R2-1).  The
  owner can tighten this to 1 via config/verification.yaml once the
  holiday/settlement-cadence edge case is handled in the scan.
  See config/verification.yaml staleness.fast_days comment for the ticket.

Freshness classes:
  'fast'  — market-priced instrument with a daily price feed (stocks, ETFs,
            CN funds, gold).  Stale after fast_days calendar days.
  'slow'  — snapshot-only reader (insurance, FS balance-sheet items, pension,
            property).  Updated on an infrequent cadence; no automated daily
            feed.  Stale after slow_days calendar days.  Genuinely stale 'slow'
            assets are deferred but do NOT auto-generate a data_fix (no feed
            to repair).
  'none'  — freshness class is indeterminate (unknown asset class or asset type
            with no defined feed).  is_fresh() always returns True for 'none'
            (no staleness check applies); freshness_verdict returns fresh=True.
            These assets are not deferred and do not generate data_fix items.

Cash-like exemption:
  Deposits, money-market funds, bank wealth products, and cash balances have no
  meaningful unrealized loss to evaluate.  is_cash_like() returns True for these
  assets; the scan skips them entirely as 'exempt_cash_like' — not as deferred.
"""
from __future__ import annotations

import logging
from datetime import date, datetime
from typing import Literal, Optional

from src.database.connector import DatabaseConnector
from src.services.verification_config import VerificationConfig, load_verification_config

logger = logging.getLogger(__name__)

FreshnessClass = Literal["fast", "slow", "none"]

# ---------------------------------------------------------------------------
# Code-level classification constants.
# These cover the known portfolio layout; config/verification.yaml
# cash_like_id_prefixes / cash_like_taxonomy_classes allow per-portfolio
# extensions without a code change.
# ---------------------------------------------------------------------------

# Asset-id prefixes → 'fast' (daily market price or daily NAV feed)
_FAST_ID_PREFIXES: tuple[str, ...] = (
    "CN_FUND_",
    "US_STK_",
    "US_ETF_",
    "ALTS_Paper_Gold",
    "ALTS_Gold",
    "ALTS_IBIT",
    "ALTS_FBTC",
)

# Taxonomy / asset_class values → 'fast'
_FAST_CLASSES: frozenset[str] = frozenset({
    "CN Equity",
    "CN Bonds",
    "US Equity",
    "Alternatives",
})

# Asset-id prefixes → 'slow' (snapshot-only; no automated daily feed)
_SLOW_ID_PREFIXES: tuple[str, ...] = (
    "Ins_",
    "Insurance_",
    "Pension_",
    "Property_",
)

# Taxonomy classes → 'slow'
_SLOW_CLASSES: frozenset[str] = frozenset({
    "Insurance Products",
    "Pension",
    "Property",
})

# Default cash-like asset-id prefixes (config may extend via cash_like_id_prefixes)
_DEFAULT_CASH_LIKE_ID_PREFIXES: tuple[str, ...] = (
    "CASH_",
    "Wealth_",
)

# Default cash-like taxonomy/asset_class values (config may extend via
# cash_like_taxonomy_classes)
_DEFAULT_CASH_LIKE_CLASSES: frozenset[str] = frozenset({
    "Cash",
    "Cash Checking",
    "Cash Deposit",
    "Bank Wealth",
    "Money Market",
    "货币市场",
})


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def freshness_class_for(
    asset_id: str,
    asset_class: str,
    cfg: Optional[VerificationConfig] = None,
) -> FreshnessClass:
    """Classify an asset as 'fast', 'slow', or 'none'.

    Classification priority: asset_id prefix match → asset_class match → 'none'.

    This function is config-driven: verification.yaml staleness section does
    not currently expose fast/slow prefix lists (kept as code constants for
    stability), but cash_like_* keys allow exemption extensions without a code
    change.
    """
    if cfg is None:
        cfg = load_verification_config()

    if any(asset_id.startswith(p) for p in _FAST_ID_PREFIXES):
        return "fast"
    # Slow ID prefix takes priority over fast asset_class — a Pension_/Ins_/Property_
    # asset whose asset_registry entry uses a fast taxonomy class (e.g. "CN Equity")
    # must still be classified 'slow'.  The ID prefix is more authoritative than the
    # asset_class column (which can be inherited from a generic classification).
    # Real-DB observed: Pension_Personal had asset_class="CN Equity" → was mis-classified
    # 'fast' when the fast-class check preceded the slow-prefix check.
    if any(asset_id.startswith(p) for p in _SLOW_ID_PREFIXES):
        return "slow"
    if asset_class in _FAST_CLASSES:
        return "fast"
    if asset_class in _SLOW_CLASSES:
        return "slow"

    return "none"


def is_cash_like(
    asset_id: str,
    asset_class: str,
    cfg: Optional[VerificationConfig] = None,
) -> bool:
    """Return True if the asset is exempt from the value-trap scan.

    Cash-like assets (deposits, money-market funds, stable-NAV bank wealth
    products, cash balances) have no meaningful unrealized loss to evaluate.
    They are counted as 'exempt_cash_like' in the scan summary — not deferred
    and not evaluated.

    Config keys staleness.cash_like_id_prefixes and
    staleness.cash_like_taxonomy_classes may extend the built-in lists.
    """
    if cfg is None:
        cfg = load_verification_config()

    all_prefixes = (*_DEFAULT_CASH_LIKE_ID_PREFIXES, *cfg.staleness.cash_like_id_prefixes)
    all_classes = _DEFAULT_CASH_LIKE_CLASSES | frozenset(cfg.staleness.cash_like_taxonomy_classes)

    if any(asset_id.startswith(p) for p in all_prefixes):
        return True
    return asset_class in all_classes


def is_fresh(
    price_date: Optional[date],
    freshness_class: FreshnessClass,
    today: Optional[date] = None,
    cfg: Optional[VerificationConfig] = None,
) -> bool:
    """Return True if price_date is within the class staleness threshold.

    'none'           → always True (no feed is defined; staleness is inapplicable).
    'fast'           → age <= cfg.staleness.fast_days (default 3 calendar days).
    'slow' or other  → age <= cfg.staleness.slow_days (default 7 calendar days).
    None price_date  → always False (no price date = unknown, treat as stale).

    See module docstring for the rationale for fast_days=3 rather than 24h.
    """
    if freshness_class == "none":
        return True
    if price_date is None:
        return False
    if cfg is None:
        cfg = load_verification_config()
    today = today or date.today()
    age_days = (today - price_date).days
    if freshness_class == "fast":
        return age_days <= cfg.staleness.fast_days
    # 'slow' (or any unrecognised class falls to slow)
    return age_days <= cfg.staleness.slow_days


def freshness_verdict(
    db: DatabaseConnector,
    asset_id: str,
    cfg: Optional[VerificationConfig] = None,
) -> dict:
    """Per-asset freshness verdict read from holdings.

    Uses per-asset CTE (never global MAX(snapshot_date) — AGENTS.md Rule 3).
    price_date = GREATEST(snapshot_date, CAST(price_updated_at AS DATE)).

    Returns:
        {
            "price_date": date | None,
            "price": float | None,       # market_price_unit
            "freshness_class": str,      # 'fast' | 'slow' | 'none'
            "fresh": bool,
        }
    """
    if cfg is None:
        cfg = load_verification_config()

    row = db.execute(
        """
        WITH latest_per_asset AS (
            SELECT asset_id, MAX(snapshot_date) AS latest_date
            FROM holdings
            WHERE is_shadow = FALSE AND asset_id = ?
            GROUP BY asset_id
        )
        SELECT
            MAX(h.snapshot_date)                              AS snapshot_date,
            MAX(h.price_updated_at)                          AS price_updated_at,
            MAX(h.market_price_unit)                         AS market_price_unit,
            COALESCE(MAX(r.asset_class), 'Unknown')          AS asset_class
        FROM holdings h
        JOIN latest_per_asset lpa
          ON h.asset_id = lpa.asset_id AND h.snapshot_date = lpa.latest_date
        LEFT JOIN asset_registry r ON h.asset_id = r.canonical_id
        WHERE h.is_shadow = FALSE AND h.asset_id = ?
        """,
        [asset_id, asset_id],
    ).fetchone()

    # DuckDB aggregate queries over 0 rows return a single (NULL, NULL, ...) tuple,
    # NOT Python None.  Since holdings.snapshot_date is NOT NULL, a None snapshot_date
    # in the result means no holdings rows matched (INNER JOIN with empty CTE produced
    # 0 rows, and the ungrouped MAX collapsed to NULL).  Treat this as "no data".
    if row is None or row[0] is None:
        # No holdings data found for this asset.  'none' freshness class means
        # no defined feed → is_fresh always returns True for 'none'.  The ruling
        # gate must not block when we simply have no holdings on record.
        return {
            "price_date": None,
            "price": None,
            "freshness_class": "none",
            "fresh": True,
        }

    snapshot_date_raw, price_updated_at_raw, market_price_unit, asset_class = row

    def _as_date(v: object) -> Optional[date]:
        if v is None:
            return None
        return v.date() if isinstance(v, datetime) else v  # type: ignore[return-value]

    sd = _as_date(snapshot_date_raw)
    pu = _as_date(price_updated_at_raw)
    candidates = [d for d in (sd, pu) if d is not None]
    price_date = max(candidates) if candidates else None

    fc = freshness_class_for(asset_id, str(asset_class or "Unknown"), cfg=cfg)
    fresh = is_fresh(price_date, fc, cfg=cfg)

    return {
        "price_date": price_date,
        "price": float(market_price_unit) if market_price_unit is not None else None,
        "freshness_class": fc,
        "fresh": fresh,
    }
