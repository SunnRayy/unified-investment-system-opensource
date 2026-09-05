"""Shared constants, leaf utilities, and result dataclasses for the sync pipeline.

Extracted verbatim from src/sync/orchestrator.py.  Import from here rather than
duplicating; orchestrator.py re-exports these names for backward compatibility.
"""
from dataclasses import dataclass, field
from datetime import date, datetime
import json
import logging
import math
from typing import Dict, Any, List, Optional

import pandas as pd

from src.database.connector import DatabaseConnector
from src.sources.registry import get_registry

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Module-level constants (extracted from orchestrator.py lines 56-104)
# ---------------------------------------------------------------------------

HOLDINGS_INSERT_COLUMNS = [
    "snapshot_date",
    "asset_id",
    "asset_name",
    "asset_type",
    "quantity",
    "unit",
    "cost_price_unit",
    "market_price_unit",
    "market_value",
    "currency",
    "account",
    "source_system",
    "price_source",
]

TRANSACTIONS_INSERT_COLUMNS = [
    "transaction_date",
    "asset_id",
    "asset_name",
    "transaction_type",
    "quantity",
    "price_unit",
    "amount_gross",
    "amount_net",
    "commission_fee",
    "currency",
    "account",
    "memo",
    "source_system",
]

READER_ID_MIGRATION_KEY = "ins_rsu_prefix_remap_v1"
# LEGACY_HOLDING_SOURCES: PIS family — stays hardcoded (not reader-derived).
LEGACY_HOLDING_SOURCES = {"PIS", "PIS_SQLite", "PIS_Excel", "PIS_Historical"}
# Derived from registry — same name, same value (set of 5, no Financial_Summary_Excel).
READER_HOLDING_SOURCES: set = set(get_registry().holding_source_systems())
# Derived from registry — same name, same value ({Financial_Summary_Excel}).
HISTORICAL_HOLDING_SOURCES: set = get_registry().historical_source_systems()
STALE_READER_SHADOW_DAYS = 7
# Sources with no regular buy/sell transaction stream, so the transaction-based
# liquidation signal in _shadow_stale_reader_holdings cannot be used for them.
# Each file is a COMPLETE snapshot of that source's portfolio.
NON_TRADABLE_HOLDING_SOURCES: set = {"Insurance_Excel", "RSU_Excel", "Gold_Excel"}
LEGACY_PREFIX_RENAMES = (
    ("Ins_", "INS_"),
    ("RSU_RSU_", "RSU_"),
)
# Assets whose cost is forced to equal market value so unrealized P&L reads zero.
# `Pension_` was REMOVED 2026-08-09 (owner ruling): stamping cost = market_value on
# every sync made 个人养老金 report a manufactured "+¥0.00" — a fake measurement where
# a dash would honestly admit the cost is unknown. It is now balance-only until the
# owner logs a cost (#7). Property_ keeps the behaviour: it carries a real cost basis
# from the Financial-Summary sheet, and the guard only fires when cost is NULL/0.
NON_TRADEABLE_PREFIXES = ("Property_",)
INSURANCE_PREFIXES = ("INS_", "Ins_")

# Attribution & Flows WS-3.3 — tiered net-worth-move alert.
# The pre-existing `diff["alert"]` (>30%) is reserved for likely-corrupt-sync
# scenarios (partial snapshot / currency bug). This lower 2% threshold is an
# advisory "review before trusting downstream reports" signal for single-run
# moves that are plausible (market movement, a large contribution/withdrawal)
# but still worth a human glance — distinct warning string so it doesn't get
# lost next to the perpetual allocation-drift warning (alert-fatigue fix).
NET_WORTH_MOVE_ALERT_PCT = 2.0


# ---------------------------------------------------------------------------
# Leaf utility functions (extracted from orchestrator.py lines 107-234)
# ---------------------------------------------------------------------------

def _is_missing(value: Any) -> bool:
    if value is None:
        return True
    try:
        return bool(pd.isna(value))
    except Exception:
        return False


def _to_date(value: Any) -> Optional[date]:
    if _is_missing(value):
        return None
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    if isinstance(value, datetime):
        return value.date()
    # Schwab F1 fix: MoneyLink cash transfers carry a composite date string
    # "MM/DD/YYYY as of MM/DD/YYYY" (posting date " as of " effective date) that
    # pd.to_datetime cannot parse, silently dropping ~16 external cash-flow rows
    # (~$141K) needed for XIRR. Take the posting date (the part before " as of ").
    if isinstance(value, str) and " as of " in value:
        value = value.split(" as of ")[0].strip()
    try:
        return pd.to_datetime(value).date()
    except Exception:
        return None


def _to_decimal(value: Any, digits: int) -> Optional[float]:
    if _is_missing(value):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if math.isnan(number) or math.isinf(number):
        return None
    return round(number, digits)


def _to_text(value: Any) -> Optional[str]:
    if _is_missing(value):
        return None
    text = str(value).strip()
    return text if text else None


def _df_len(value: Any) -> int:
    return len(value) if isinstance(value, pd.DataFrame) else 0


def _db_param(value: Any) -> Any:
    """Normalize DataFrame missing markers to SQL NULL for DuckDB bindings."""
    return None if _is_missing(value) else value


def _default_account(source_system: str) -> str:
    # Derived from registry — same values as before; CN_Fund_Excel→"CN Fund"
    # (account_name field), not "CN Funds" (display_name).
    return get_registry().default_account_names().get(source_system, "Unknown")


def _default_currency(source_system: str) -> str:
    # Broker_IBKR account base currency is USD (ACCT section); all current IBKR
    # holdings are USD-denominated, so they convert to CNY via _update_from_dsa
    # like Schwab. Without this, currency defaults to CNY and FX is skipped (×1.0),
    # leaving IBKR market_value in raw USD.
    return "USD" if source_system in {"Schwab_CSV", "RSU_Excel", "Broker_IBKR"} else "CNY"


def _infer_asset_type(asset_id: str, source_system: str) -> Optional[str]:
    if source_system == "Insurance_Excel":
        return "Insurance"
    if source_system == "Gold_Excel":
        return "Alternative"
    if source_system == "CN_Fund_Excel":
        return "Fund"
    if source_system == "RSU_Excel":
        return "Equity Compensation"
    if asset_id.startswith("CASH_"):
        return "Cash"
    if asset_id.startswith("US_STK_"):
        return "US Equity"
    if asset_id.startswith("CN_FUND_"):
        return "Fund"
    return None


def _coerce_json_value(value: Any) -> Any:
    if _is_missing(value):
        return None
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, pd.Timestamp):
        return value.isoformat()
    return value


def _log_sync_event(
    connector: DatabaseConnector,
    source_system: str,
    target_table: str,
    record_key: str,
    source_value: Dict[str, Any],
    conflict_type: str = "reader_insert",
    resolution: str = "inserted",
    notes: Optional[str] = None,
) -> None:
    connector.execute(
        """
        INSERT INTO sync_audit_logs (
            sync_timestamp, source_system, target_table, record_key,
            conflict_type, source_value, target_value, resolution,
            resolved_by, resolution_notes, is_resolved, resolved_at
        ) VALUES (
            CURRENT_TIMESTAMP, ?, ?, ?, ?, ?, ?, ?, ?, ?, TRUE, CURRENT_TIMESTAMP
        )
        """,
        (
            source_system,
            target_table,
            record_key,
            conflict_type,
            json.dumps(source_value, ensure_ascii=False, default=str),
            None,
            resolution,
            "orchestrator",
            notes,
        ),
    )


# ---------------------------------------------------------------------------
# Result dataclasses and sync observability helpers
# (extracted from orchestrator.py lines 1637-1815, excluding
#  _load_adapter_authority_rules which belongs in _post_reader.py)
# ---------------------------------------------------------------------------

@dataclass
class StepResult:
    """Per-step result for structured sync observability.

    Captured in SyncResult.steps for agents and CI to inspect.
    Critical steps failing sets SyncResult.success = False.
    Non-critical failures set SyncResult.degraded = True.
    """
    name: str
    status: str           # "ok" | "skipped" | "warning" | "failed"
    critical: bool        # critical=True failure → SyncResult.success = False
    error: Optional[str] = None
    duration_ms: int = 0


@dataclass
class SyncResult:
    """Result of full sync operation.

    Pass 1 additions (backward-compatible):
    - steps: per-step observability list (see StepResult)
    - degraded: True when a non-critical step failed but all critical steps ok
    - success semantics changed: now False only when a critical step fails
      (previously always initialized True and never changed)

    All existing fields kept. /api/sync callers reading result.success still work.
    """
    success: bool
    transactions_synced: int = 0
    holdings_synced: int = 0
    market_records_synced: int = 0
    allocations_synced: int = 0
    taxonomy_created: int = 0
    taxonomy_updated: int = 0
    assets_registered: int = 0
    cost_basis_discrepancies: int = 0
    allocation_drifts: int = 0
    live_price_holdings_updated: int = 0
    position_deltas_detected: int = 0
    integrity_checks_passed: int = 0
    integrity_checks_total: int = 0
    sync_audit_id: Optional[str] = None
    sync_diff: Optional[Dict[str, Any]] = None
    warnings: List[str] = field(default_factory=list)
    info_messages: List[str] = field(default_factory=list)
    error_message: Optional[str] = None
    # ── Pass 1: structured observability (additive) ──
    steps: List[StepResult] = field(default_factory=list)
    degraded: bool = False
    # ── Task #16: sources that RAN, were verified, and legitimately yielded zero
    # holdings this sync. Populated in P2, consumed by the P4 shadow phases. A
    # source is in here ONLY on affirmative evidence (READ_STATUS_OK + zero rows);
    # "file missing", "disabled", "validation failed" and "reader raised" all stay
    # out, so the ambiguous case can never zero a live portfolio.
    empty_verified_sources: set = field(default_factory=set)


def _capture_sync_summary(connector: DatabaseConnector) -> Dict[str, Any]:
    """Capture a snapshot of key financial metrics for before/after diff reporting."""
    try:
        nw_row = connector.execute("""
            WITH latest_per_asset AS (
                SELECT asset_id, MAX(snapshot_date) AS max_date
                FROM holdings WHERE is_shadow = FALSE GROUP BY asset_id
            )
            SELECT SUM(h.market_value) AS net_worth, COUNT(*) AS asset_count
            FROM holdings h
            JOIN latest_per_asset l ON h.asset_id=l.asset_id AND h.snapshot_date=l.max_date
            WHERE h.is_shadow=FALSE AND h.market_value > 0
        """).fetchone()

        by_source = connector.execute("""
            WITH latest_per_asset AS (
                SELECT asset_id, MAX(snapshot_date) AS max_date
                FROM holdings WHERE is_shadow = FALSE GROUP BY asset_id
            )
            SELECT h.source_system, COUNT(*) AS cnt, SUM(h.market_value) AS total_value
            FROM holdings h
            JOIN latest_per_asset l ON h.asset_id=l.asset_id AND h.snapshot_date=l.max_date
            WHERE h.is_shadow=FALSE
            GROUP BY h.source_system
            ORDER BY total_value DESC
        """).fetchall()

        return {
            "net_worth": float(nw_row[0]) if nw_row and nw_row[0] else 0.0,
            "asset_count": int(nw_row[1]) if nw_row and nw_row[1] else 0,
            "by_source": {r[0]: {"count": r[1], "value": float(r[2]) if r[2] else 0.0} for r in by_source},
        }
    except Exception as e:
        return {"error": str(e), "net_worth": 0.0, "asset_count": 0, "by_source": {}}


def _compute_sync_diff(pre: Dict[str, Any], post: Dict[str, Any]) -> Dict[str, Any]:
    """Compute before/after diff for sync reporting.

    ``net_worth_move_warning`` (WS-3.3): a fully-formed, distinctly-named warning
    string when |net_worth_change_pct| exceeds NET_WORTH_MOVE_ALERT_PCT (2%), else
    None. This is separate from ``alert`` (>30%, likely-corrupt-sync signal) — the
    2% tier is meant to be seen, not just logged, so the caller that populates
    SyncResult.warnings should append this string verbatim when present (kept
    distinct from the perpetual allocation-drift warning so it doesn't get lost —
    alert-fatigue fix).
    """
    pre_nw = pre.get("net_worth", 0.0)
    post_nw = post.get("net_worth", 0.0)
    nw_change_pct = ((post_nw - pre_nw) / pre_nw * 100) if pre_nw > 0 else 0.0
    nw_change_pct = round(nw_change_pct, 2)

    net_worth_move_warning = None
    if abs(nw_change_pct) > NET_WORTH_MOVE_ALERT_PCT:
        net_worth_move_warning = (
            f"[NET-WORTH-MOVE] single-run net worth change {nw_change_pct:+.2f}% "
            f"({pre_nw:,.0f} → {post_nw:,.0f} CNY) exceeds 2% — "
            "review before trusting downstream reports"
        )

    return {
        "net_worth_before": pre_nw,
        "net_worth_after": post_nw,
        "net_worth_change_pct": nw_change_pct,
        "asset_count_before": pre.get("asset_count", 0),
        "asset_count_after": post.get("asset_count", 0),
        "by_source_before": pre.get("by_source", {}),
        "by_source_after": post.get("by_source", {}),
        "alert": abs(nw_change_pct) > 30,
        "net_worth_move_warning": net_worth_move_warning,
    }


def _is_no_change_sync(diff: Dict[str, Any], result: SyncResult) -> bool:
    """Treat repeat syncs as no-change when the authoritative portfolio state is identical."""
    if abs(diff.get("net_worth_change_pct", 0.0)) >= 0.001:
        return False
    if diff.get("asset_count_before", 0) != diff.get("asset_count_after", 0):
        return False
    if diff.get("by_source_before", {}) != diff.get("by_source_after", {}):
        return False
    if result.live_price_holdings_updated > 0:
        return False
    if result.position_deltas_detected > 0:
        return False
    return True


def _record_step(
    result: "SyncResult",
    name: str,
    critical: bool,
    status: str,
    error: Optional[str] = None,
    duration_ms: int = 0,
) -> None:
    """Append a StepResult and update result.success / result.degraded accordingly.

    Pass 1 observability helper. Scope: purely additive — does not affect step
    ordering, pipeline semantics, or any existing warning/info_message patterns.
    Called at the end of a bounded phase; the phase's try/except still owns error
    handling.
    """
    result.steps.append(StepResult(
        name=name,
        status=status,
        critical=critical,
        error=error,
        duration_ms=duration_ms,
    ))
    if status == "failed":
        if critical:
            result.success = False
        else:
            result.degraded = True
