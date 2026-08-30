"""Declarative pipeline manifest — the single source of truth for sync phases.

Each PhaseSpec names an orchestrator runner function and documents what the
phase reads and writes. `run_full_sync_v3()` executes phases in this order;
the pipeline diagram (Phase A3) is generated from this file. Keep this module
import-light: no DB or pandas imports, so documentation tooling can import it
without pulling in the full pipeline.

History: replaces the comment-based "Phase 2.4.13"-style numbering
(duplicated labels, missing Phase 5). The deprecated DSA SQLite market-data
ingest (old step 2.3) was removed in Phase A2 of the data layer transformation
program — live prices come solely from P3 (MarketDataService); historical DSA
backfill remains available via `python main.py --sync-market`.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Optional, Tuple


@dataclass
class PhaseContext:
    """Uniform arguments handed to every phase runner."""
    connector: Any                       # DatabaseConnector
    config: Dict[str, Any]
    dry_run: bool
    result: Any                          # SyncResult
    pre_sync_summary: Optional[Dict[str, Any]] = None
    extras: Dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class PhaseSpec:
    phase_id: str                        # "P0".."P8"
    name: str
    runner: str                          # function name on src.sync.orchestrator
    description: str
    tables_read: Tuple[str, ...] = ()
    tables_written: Tuple[str, ...] = ()


PIPELINE_MANIFEST: Tuple[PhaseSpec, ...] = (
    PhaseSpec(
        "P0", "Backup & schema setup", "_run_phase0_backup_and_setup",
        "Full DB backup (pre-sync-v3) unless dry-run; idempotent creation of "
        "classification tables.",
        tables_written=("classification tables",),
    ),
    PhaseSpec(
        "P1", "Identity sync", "_run_phase1_identity",
        "Register canonical asset IDs from config into asset_registry.",
        tables_read=("config/settings.yaml",),
        tables_written=("asset_registry",),
    ),
    PhaseSpec(
        "P2", "Reader & adapter ingest", "_run_phase2_ingest",
        "Pre-reader backup, legacy prefix normalization, the 6 source readers "
        "(Schwab, CN Fund, Gold, Insurance, RSU, Financial Summary), approved "
        "import adapters, auto-registration of new assets, position deltas, "
        "no-op backup cleanup and zero-ingest alert.",
        tables_read=("source files", "import_adapter_staged_rows"),
        tables_written=(
            "holdings", "transactions", "balance_sheet_monthly",
            "income_expense_monthly", "asset_registry", "sync_audit_logs",
        ),
    ),
    PhaseSpec(
        "P3", "Live price refresh", "_run_phase3_price_refresh",
        "Fetch live quotes (yfinance / akshare / SGE) and live FX (USDCNY=X, "
        "fallback 7.0) for all active holdings; update market_daily and "
        "holdings price columns. The ONLY price path since A2.",
        tables_read=("holdings",),
        tables_written=("market_daily", "holdings"),
    ),
    PhaseSpec(
        "P4", "Shadow pipeline & post-ingest normalization", "_run_phase4_shadow_cleanup",
        "is_shadow writer #1 (staleness archival): stale-reader, non-tradable "
        "older snapshots, Financial Summary older snapshots, legacy PIS. Then "
        "UNKNOWN_/GOLD_PAPER_/BRK cleanup, FIFO cost-basis backfill, insurance "
        "cost from premiums, zero-P&L for non-tradeables, RSU price update.",
        tables_read=("holdings", "transactions"),
        tables_written=("holdings", "transactions"),
    ),
    PhaseSpec(
        "P5", "Authority resolution", "_run_phase5_authority",
        "is_shadow writer #2 (source conflicts): per (asset, snapshot_date) "
        "pick the authoritative source per config/source_authority.yaml; "
        "shadow the losers and stamp authority_source.",
        tables_read=("holdings", "config/source_authority.yaml"),
        tables_written=("holdings",),
    ),
    PhaseSpec(
        "P6", "Derived data", "_run_phase6_derived",
        "Recompute current allocation percentages from active holdings.",
        tables_read=("holdings",),
        tables_written=("current_allocations",),
    ),
    PhaseSpec(
        "P7", "Validation & decision layer", "_run_phase7_validation",
        "Cost-basis (1%) / allocation (5%) / divergence (10%) warnings; trade "
        "log linking, backfill from transactions, and scoring.",
        tables_read=("holdings", "transactions", "trade_logs"),
        tables_written=("trade_logs",),
    ),
    PhaseSpec(
        "P8", "Sync diff & integrity gate", "_run_phase8_audit",
        "Before/after net-worth diff (alert >30%), the 14-check integrity "
        "gate (5 blocking), and sync audit persistence.",
        tables_read=("holdings", "transactions"),
        tables_written=("sync_audit_reports",),
    ),
    PhaseSpec(
        "P9", "Insights continuity", "_run_phase9_insights_continuity",
        "ADVISORY post-sync refresh of the insights loop (never blocks sync). "
        "Five isolated sub-tasks sharing the orchestrator write connection: "
        "(a) bridge qualifying ai_insights → Decision Hub; "
        "(b) score_all_trades verdict/outcome backfill; "
        "(c) recompute_auto_links insight↔trade attribution; "
        "(d) compute_verification_report if stale >24 h; "
        "(e) behavioral-metrics compute (window_days=90). "
        "Each sub-task runs in its own try/except; failure logs WARNING only.",
        tables_read=(
            "ai_insights", "insights", "trade_logs", "insight_trade_links",
            "verification_logs", "transactions", "holdings",
        ),
        tables_written=(
            "insights", "trade_logs", "insight_trade_links",
            "verification_logs", "ai_behavioral_log",
        ),
    ),
)
