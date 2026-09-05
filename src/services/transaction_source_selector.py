"""Shared transaction source selection logic.

Extracted from src/api/routes/performance.py to avoid cross-layer imports.
Used by: performance.py, orchestrator.py, and Phase 5 analytics modules.
"""
from src.database.connector import DatabaseConnector

LEGACY_TRANSACTION_SOURCES = {"PIS_SQLite", "PIS", "PIS_Excel", "AIA"}

REALIZED_PNL_EXEMPT_PREFIXES = ("Pension_", "Property_", "INS_", "Ins_", "CASH_", "UNKNOWN_")
REALIZED_PNL_EXEMPT_ASSET_CLASSES = {
    "货币市场", "Money Market", "活期存款", "定期存款", "现金", "Cash", "Cash (现金)",
    "Bank Wealth",  # 银行理财 — cash-equivalent; PIS phantom adjustments create spurious realized P&L
}


def is_realized_pnl_exempt(db: DatabaseConnector, asset_id: str) -> bool:
    """Return True when realized P&L should be suppressed for display/business rules."""
    if not asset_id:
        return False
    if asset_id.startswith(REALIZED_PNL_EXEMPT_PREFIXES):
        return True
    try:
        row = db.execute(
            "SELECT asset_class FROM asset_registry WHERE canonical_id = ? LIMIT 1",
            (asset_id,),
        ).fetchone()
    except Exception:
        row = None
    if not row or not row[0]:
        return False
    return str(row[0]) in REALIZED_PNL_EXEMPT_ASSET_CLASSES


def select_transaction_sources(db: DatabaseConnector, asset_id: str, resolver=None) -> list[str]:
    """Select authoritative transaction source(s) for an asset to avoid double-counting.

    For co-authority assets (e.g. US_STK_*/US_ETF_* → Schwab_CSV + Broker_IBKR), the cost
    basis is a lifetime MERGED-ledger FIFO per asset_id.  We therefore return ALL of the
    authority rule's declared sources that have transactions — resolved via the authority RULE,
    NOT the latest-holding source.  Without this, an asset transferred Schwab→IBKR would
    return IBKR only (the surviving holding source), dropping Schwab's buy lots → cost basis $0.
    (C3.3 RISK-1.)
    """
    tx_source_rows = db.execute(
        "SELECT DISTINCT source_system FROM transactions WHERE asset_id = ? AND source_system IS NOT NULL",
        (asset_id,),
    ).fetchall()
    tx_sources = {row[0] for row in tx_source_rows if row and row[0]}
    if not tx_sources:
        return []

    # Co-authority assets (rule declares >=2 authorities, e.g. US_STK_* → Schwab_CSV + Broker_IBKR):
    # the cost basis is a lifetime MERGED-ledger FIFO per asset_id, so we must return ALL of the
    # rule's authority sources that have transactions — resolved via the authority RULE, NOT the
    # latest-holding source. Without this, an asset transferred Schwab→IBKR returns IBKR only,
    # dropping Schwab's buy lots → cost basis $0. (C3.3 RISK-1.)
    from src.identity.authority_resolver import AuthorityResolver
    if resolver is None:
        resolver = AuthorityResolver()
    rule_authorities = resolver.resolve_authorities(asset_id)  # declared set, no available filter
    if len(rule_authorities) >= 2:
        merged = tx_sources & set(rule_authorities)
        if merged:
            return sorted(merged)
        # else fall through to existing logic (no co-authority tx sources present)

    authority_rows = db.execute(
        """
        WITH latest_snap AS (
            SELECT MAX(snapshot_date) AS max_date
            FROM holdings WHERE is_shadow = FALSE AND asset_id = ?
        )
        SELECT DISTINCT h.source_system
        FROM holdings h JOIN latest_snap ls ON h.snapshot_date = ls.max_date
        WHERE h.asset_id = ? AND h.is_shadow = FALSE AND h.source_system IS NOT NULL
        """,
        (asset_id, asset_id),
    ).fetchall()
    authority_sources = {row[0] for row in authority_rows if row and row[0]}

    if authority_sources:
        matching = tx_sources & authority_sources
        if matching:
            return sorted(matching)

    has_legacy = any(s in LEGACY_TRANSACTION_SOURCES for s in tx_sources)
    non_legacy = {s for s in tx_sources if s not in LEGACY_TRANSACTION_SOURCES}
    if has_legacy and non_legacy:
        return sorted(non_legacy)

    return sorted(tx_sources)


def build_source_filter_clauses(db: DatabaseConnector, asset_ids: list = None) -> tuple[str, list]:
    """Build a SQL WHERE clause fragment and params for deduplicated transactions.

    Args:
        db: DatabaseConnector
        asset_ids: Optional list of asset IDs. If None, queries all active assets
                   that have transactions.

    Returns:
        A tuple of (sql_fragment, params)
        Example: ("((asset_id = ? AND source_system IN (?)) OR (asset_id = ?))", ['A1', 'Sys1', 'A2'])
    """
    if asset_ids is None:
        rows = db.execute("SELECT DISTINCT asset_id FROM transactions WHERE is_provisional = FALSE").fetchall()
        asset_ids = sorted({r[0] for r in rows if r and r[0]})

    if not asset_ids:
        return "1=0", []  # No assets match

    clauses = []
    params = []

    from src.identity.authority_resolver import AuthorityResolver
    resolver = AuthorityResolver()

    for aid in asset_ids:
        sources = select_transaction_sources(db, aid, resolver=resolver)
        if sources:
            placeholders = ", ".join(["?"] * len(sources))
            clauses.append(f"(asset_id = ? AND source_system IN ({placeholders}))")
            params.extend([aid] + sources)
        else:
            # Fallback if no sources defined but we still want to match the asset (e.g. legacy DB without source_system)
            clauses.append("(asset_id = ?)")
            params.append(aid)
            
    if not clauses:
        return "1=0", []
        
    return "(" + " OR ".join(clauses) + ")", params
