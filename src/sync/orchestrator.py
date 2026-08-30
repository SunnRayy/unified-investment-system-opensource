"""Orchestrate full v3 sync workflow."""

from datetime import date, datetime
import logging
import os
import time
from pathlib import Path
from typing import Dict, Any, Optional

import pandas as pd

from src.database.connector import DatabaseConnector
from src.classification.auto_tagger import AutoTagger


# Import sync functions
# NOTE: pis_sqlite_sync (sync_pis_transactions, sync_target_allocations, sync_tier_assignments),
#       pis_sync (sync_holdings_with_cost_basis), and aia_sync (sync_aia_holdings) are intentionally
#       NOT imported here — removed Phase 9 (superseded by 6 source readers) and Phase 4 (AIA deprecation).
#       See: docs/decisions/ADR-003-phase9-pis-deprecation.md
# NOTE: dsa_sync.sync_market_data (historical DSA SQLite ingest) is intentionally NOT
#       imported here — removed in Phase A2 (data layer transformation program). Live
#       prices come solely from P3 (MarketDataService.refresh_portfolio_prices).
#       One-off historical backfill remains available via `python main.py --sync-market`.
from src.sync.position_delta_detector import capture_pre_sync_snapshot, detect_and_persist_deltas
from src.sync.allocation_sync import sync_current_allocations
from src.sync.identity_sync import sync_asset_registry
from src.sync.schwab_sync import sync_schwab
from src.sync.cn_fund_sync import sync_cn_fund
from src.sync.gold_sync import sync_gold
from src.sync.insurance_sync import sync_insurance
from src.sync.rsu_sync import sync_rsu
from src.sync.financial_summary_sync import sync_financial_summary
from src.sync.ibkr_sync import sync_ibkr
from src.sync.trade_linker import backfill_trade_logs_from_transactions, link_trade_logs_to_transactions
from src.services.decision_scorer import score_all_trades
from src.import_adapters.sync import (
    get_approved_adapter_source_systems,
    sync_approved_import_adapters,
)

# Import backup utility
from src.database.backup import create_backup

# Import validation functions
from src.validation.cost_basis_validator import validate_cost_basis
from src.validation.allocation_validator import validate_allocations
from src.validation.data_integrity_gate import run_integrity_checks

# Import classification schema (Phase 2)
from src.classification.schema import create_classification_tables

# Declarative pipeline manifest — phase order + docs (single source of truth)
from src.sync.phases.manifest import PIPELINE_MANIFEST, PhaseContext

logger = logging.getLogger(__name__)

# ═══════════════════════════════════════════════════════
# Phase module re-exports — all helpers live in src/sync/phases/
# These re-imports preserve backward-compat for:
#   • src/import_adapters/sync.py lazy imports of _normalize_*, _upsert_*, _replace_*
#   • test files importing private helpers from src.sync.orchestrator
#   • patch('src.sync.orchestrator.X') in all test files (helpers stay in orch namespace)
# ═══════════════════════════════════════════════════════
from src.sync.phases._common import (  # noqa: F401 — intentional re-exports (see block comment)
    HOLDINGS_INSERT_COLUMNS, TRANSACTIONS_INSERT_COLUMNS, READER_ID_MIGRATION_KEY,  # noqa: F401
    LEGACY_HOLDING_SOURCES, READER_HOLDING_SOURCES, HISTORICAL_HOLDING_SOURCES,  # noqa: F401
    STALE_READER_SHADOW_DAYS, LEGACY_PREFIX_RENAMES, NON_TRADEABLE_PREFIXES, INSURANCE_PREFIXES,  # noqa: F401
    StepResult, SyncResult,  # noqa: F401
    _is_missing, _to_date, _to_decimal, _to_text, _df_len, _db_param,  # noqa: F401
    _default_account, _default_currency, _infer_asset_type, _coerce_json_value,  # noqa: F401
    _log_sync_event, _record_step, _capture_sync_summary, _compute_sync_diff,  # noqa: F401
    _is_no_change_sync,  # noqa: F401
)
from src.sync.phases._ingest import (  # noqa: F401 — intentional re-exports (see block comment)
    _normalize_holdings_df, _aggregate_gold_holdings, _normalize_transactions_df,  # noqa: F401
    _upsert_holdings, _replace_transactions, _ensure_financial_summary_tables,  # noqa: F401
    _extract_date_from_record, _persist_financial_summary,  # noqa: F401
)
from src.sync.phases._pre_reader import (
    _run_reader_id_migration_once, _normalize_legacy_prefixes,
)
from src.sync.phases._shadow import (
    _shadow_stale_reader_holdings, _shadow_stale_non_tradable_holdings,
    _shadow_stale_historical_holdings, _shadow_legacy_holdings,
    _shadow_coauthority_tombstone, _consolidate_coauthority_holdings,
    _tombstone_empty_verified_sources,
)
from src.sync.phases._post_reader import (
    _backfill_fifo_cost_basis, _set_insurance_cost_from_premiums,
    _zero_pl_for_non_tradeable_assets, _update_rsu_prices_from_external_sources,
    _load_adapter_authority_rules,
)


# ═══════════════════════════════════════════════════════
# HELPERS THAT CALL PATCHABLE SYMBOLS — defined here, not in phases/
# _auto_register_new_assets uses AutoTagger (patchable); tests patch
# src.sync.orchestrator.AutoTagger, so it must be called from this namespace.
# ═══════════════════════════════════════════════════════

def _auto_register_new_assets(connector: DatabaseConnector) -> int:
    """Automatically register newly seen assets from holdings and transactions in asset_registry.

    This ensures that when a new asset is imported via reader files (e.g. Schwab, CN Fund, Insurance),
    it is registered so that it shows up in the classification/audit pages and can be classified.
    """
    # 1. Query holdings for assets not in asset_registry (excluding UNKNOWN_)
    holdings_assets = connector.execute("""
        SELECT DISTINCT asset_id, asset_name, source_system
        FROM holdings
        WHERE asset_id NOT IN (SELECT canonical_id FROM asset_registry)
          AND asset_id NOT LIKE 'UNKNOWN_%'
    """).fetchall()

    # 2. Query transactions for assets not in asset_registry (excluding UNKNOWN_)
    txn_assets = connector.execute("""
        SELECT DISTINCT asset_id, asset_name, source_system
        FROM transactions
        WHERE asset_id NOT IN (SELECT canonical_id FROM asset_registry)
          AND asset_id NOT LIKE 'UNKNOWN_%'
    """).fetchall()

    # Combine them, prioritizing holdings info if present
    missing_assets = {}
    for aid, name, src in holdings_assets + txn_assets:
        if aid not in missing_assets or (name and not missing_assets[aid]['name']):
            missing_assets[aid] = {'name': name, 'source': src}

    if not missing_assets:
        return 0

    registered_count = 0

    for asset_id, info in missing_assets.items():
        name = info['name'] or asset_id
        source = info['source']

        # Determine base currency
        if asset_id.startswith(('US_', 'RSU_')) or asset_id == 'CASH_USD':
            base_currency = 'USD'
        else:
            base_currency = 'CNY'

        # Insert into asset_registry as pending
        connector.execute("""
            INSERT INTO asset_registry (
                canonical_id, display_name, base_currency, is_active, is_pending, created_at, updated_at
            ) VALUES (?, ?, ?, TRUE, TRUE, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
            ON CONFLICT (canonical_id) DO NOTHING
        """, (asset_id, name, base_currency))

        # Insert into asset_source_mappings
        connector.execute("""
            INSERT INTO asset_source_mappings (
                canonical_id, source_system, source_id, mapping_type, created_at
            ) VALUES (?, ?, ?, 'auto', CURRENT_TIMESTAMP)
            ON CONFLICT (source_system, source_id) DO NOTHING
        """, (asset_id, source, asset_id))

        registered_count += 1

    # Run auto-tagger to classify newly registered assets if rules match
    try:
        tagger = AutoTagger(connector)
        tagger.classify_registry(connector)
    except Exception as e:
        logger.warning(f"AutoTagger failed after auto-registration: {e}")

    return registered_count


# ═══════════════════════════════════════════════════════
# PHASE SUB-FUNCTIONS
# All calls to patchable sibling-module symbols stay in this file so that
# patch('src.sync.orchestrator.X') in tests continues to intercept them.
# ═══════════════════════════════════════════════════════

def _run_phase0_backup_and_setup(connector: DatabaseConnector, dry_run: bool, result: SyncResult) -> None:
    """PHASE 0: PRE-SYNC BACKUP + VALIDATION"""
    # 0.0 Create backup before any mutations (Decision 7)
    # Suppressed in dry-run: the caller already operates on a tmp copy.
    # In cloud mode (UIS_GCS_BUCKET set) local file backups are redundant —
    # every GCS flush already uploads a timestamped backup (gcs.py:67).
    if not dry_run:
        if os.getenv("UIS_GCS_BUCKET"):
            result.info_messages.append(
                "Cloud mode: local file backup skipped"
                " — GCS uploads a timestamped backup on every flush"
            )
        else:
            try:
                backup_path = create_backup(reason="pre-sync-v3")
                result.info_messages.append(f"Backup created: {backup_path}")
            except Exception as e:
                result.warnings.append(f"Backup warning: {str(e)}")

    # 0.0.5 Create Phase 2 classification tables (idempotent)
    try:
        create_classification_tables(connector)
    except Exception as e:
        result.warnings.append(f"Classification schema warning: {str(e)}")


def _run_phase1_identity(connector: DatabaseConnector, config: Dict[str, Any], result: SyncResult) -> None:
    """PHASE 1: IDENTITY SYNC"""
    # 1.1 Taxonomy sync removed — asset_taxonomy table is no longer synced from PIS YAML.
    #     taxonomy_classes is the authoritative taxonomy (seeded once, UI-managed).
    #     See: Plan "Remove PIS Taxonomy YAML Dependency" (2026-03-10)

    # 1.2 Sync Asset Registry
    try:
        id_result = sync_asset_registry(connector, config)
        result.assets_registered = id_result.get('registry_inserted', 0)
    except Exception as e:
        result.warnings.append(f"Identity sync error: {str(e)}")


def _record_empty_source_signal(
    connector: DatabaseConnector,
    result: SyncResult,
    source_system: str,
    read_status: str,
    holdings_row_count: int,
) -> None:
    """Classify a reader that produced zero holdings rows (task #16).

    "Reader produced no rows" is ambiguous between two opposite meanings:

      * genuine total liquidation / empty workbook → the holdings SHOULD go to zero;
      * file missing, reader disabled, validation failed, half-uploaded file →
        zeroing would destroy correct data.

    The second error is far more damaging, so only `READ_STATUS_OK` — artifact
    located, format validator passed, parse did not raise — buys the right to zero.
    Everything else is recorded as a loud warning and changes nothing, mirroring the
    V7.8.1 rule that a *blank* Financial-Summary cell is an affirmative zero while an
    *absent* column is merely a warning.

    No-op when the reader returned rows.
    """
    from src.sources.base import READ_STATUS_OK

    if holdings_row_count > 0:
        return

    if read_status == READ_STATUS_OK:
        result.empty_verified_sources.add(source_system)
        result.info_messages.append(
            f"{source_system}: source read OK and reported ZERO holdings — "
            "existing positions will be zeroed by a tombstone"
        )
        return

    # Ambiguous. Only worth shouting about when there is something to lose.
    try:
        row = connector.execute(
            """
            WITH latest_per_asset AS (
                SELECT asset_id, MAX(snapshot_date) AS max_date
                FROM holdings WHERE source_system = ? AND is_shadow = FALSE
                GROUP BY asset_id
            )
            SELECT COUNT(*), COALESCE(SUM(h.market_value), 0)
            FROM holdings h
            JOIN latest_per_asset l ON h.asset_id = l.asset_id AND h.snapshot_date = l.max_date
            WHERE h.source_system = ? AND h.is_shadow = FALSE AND h.market_value > 0
            """,
            (source_system, source_system),
        ).fetchone()
    except Exception as e:  # pragma: no cover - defensive
        result.warnings.append(f"{source_system} empty-source check error: {str(e)}")
        return

    active_count = int(row[0]) if row else 0
    active_value = float(row[1]) if row and row[1] is not None else 0.0
    if active_count == 0:
        return

    result.warnings.append(
        f"[EMPTY-SOURCE] {source_system} returned no holdings and could NOT be verified "
        f"(read_status={read_status}). {active_count} active holding(s) worth "
        f"{active_value:,.2f} CNY are being KEPT unchanged — they are NOT confirmed sold. "
        "Fix the source file / reader config; the pipeline will not zero an unverified source."
    )
    logger.warning(
        "Empty unverified source %s (read_status=%s) — keeping %d active holdings (%.2f CNY)",
        source_system, read_status, active_count, active_value,
    )


def _run_schwab_reader(connector: DatabaseConnector, config: Dict[str, Any], result: SyncResult):
    """Phase 2.4.5 — Schwab CSV reader. Returns (holdings_inserted, txns_inserted)."""
    schwab_config = config.get('source_registry', {}).get('schwab', {})
    if not schwab_config.get('enabled', False):
        return 0, 0
    try:
        # Capture pre-sync snapshot for position delta detection
        schwab_pre_snapshot: dict = {}
        try:
            schwab_pre_snapshot = capture_pre_sync_snapshot(connector, "Schwab_CSV")
        except Exception as e:
            result.warnings.append(f"Schwab pre-sync snapshot error: {str(e)}")

        # ADR-023 / WS-C: load UI-managed known_etf/symbol_norm/action_map
        # vocab from the sync's own connection (no second RW connection) and
        # inject via extra_metadata — mirrors the FS/WS-B wiring above.
        from src.services.reader_mappings import load_reader_mappings
        schwab_known_etf = load_reader_mappings(connector, "schwab", "known_etf")
        schwab_symbol_norm = load_reader_mappings(connector, "schwab", "symbol_norm")
        schwab_action_map = load_reader_mappings(connector, "schwab", "action_map")
        schwab_result = sync_schwab(config, extra_metadata={
            "schwab_known_etf": {k for k, v in schwab_known_etf.items() if v},
            "schwab_symbol_norm": schwab_symbol_norm,
            "schwab_action_map": schwab_action_map,
        })
        schwab_holdings = _df_len(schwab_result.get("holdings"))
        schwab_txns = _df_len(schwab_result.get("transactions"))
        holdings_df = _normalize_holdings_df(schwab_result.get("holdings", pd.DataFrame()), "Schwab_CSV")
        tx_df = _normalize_transactions_df(schwab_result.get("transactions", pd.DataFrame()), "Schwab_CSV")
        inserted_holdings = _upsert_holdings(connector, holdings_df)
        inserted_txns = _replace_transactions(connector, tx_df)
        _log_sync_event(
            connector=connector,
            source_system="Schwab_CSV",
            target_table="holdings",
            record_key=f"{date.today().isoformat()}:Schwab_CSV",
            source_value={"read_holdings": schwab_holdings, "inserted_holdings": inserted_holdings},
        )
        _log_sync_event(
            connector=connector,
            source_system="Schwab_CSV",
            target_table="transactions",
            record_key=f"{date.today().isoformat()}:Schwab_CSV",
            source_value={"read_transactions": schwab_txns, "inserted_transactions": inserted_txns},
        )
        result.info_messages.append(
            f"Schwab sync: {schwab_holdings} holdings, {schwab_txns} transactions "
            f"(inserted {inserted_holdings} holdings, {inserted_txns} transactions)"
        )

        # Detect and persist position deltas
        try:
            schwab_snap_date = date.today()
            if not holdings_df.empty and "snapshot_date" in holdings_df.columns:
                schwab_snap_date = holdings_df["snapshot_date"].max()
                if hasattr(schwab_snap_date, 'date'):
                    schwab_snap_date = schwab_snap_date.date()
            schwab_deltas = detect_and_persist_deltas(
                connector, "Schwab_CSV", schwab_pre_snapshot, schwab_snap_date
            )
            result.position_deltas_detected += len(schwab_deltas)
            if schwab_deltas:
                result.info_messages.append(
                    f"Schwab position deltas: {len(schwab_deltas)} change(s) detected"
                )
        except Exception as e:
            result.warnings.append(f"Schwab delta detection error: {str(e)}")

        return inserted_holdings, inserted_txns
    except Exception as e:
        result.warnings.append(f"Schwab sync error: {str(e)}")
        return 0, 0


def _run_cn_fund_reader(connector: DatabaseConnector, config: Dict[str, Any], result: SyncResult):
    """Phase 2.4.6 — CN Fund Excel reader. Returns (holdings_inserted, txns_inserted)."""
    cn_fund_config = config.get('source_registry', {}).get('cn_fund', {})
    if not cn_fund_config.get('enabled', False):
        return 0, 0
    try:
        # Capture pre-sync snapshot for position delta detection
        cn_fund_pre_snapshot: dict = {}
        try:
            cn_fund_pre_snapshot = capture_pre_sync_snapshot(connector, "CN_Fund_Excel")
        except Exception as e:
            result.warnings.append(f"CN Fund pre-sync snapshot error: {str(e)}")

        # ADR-023 / WS-C: load UI-managed 操作类型 -> transaction_type vocab
        # from the sync's own connection and inject via extra_metadata.
        from src.services.reader_mappings import load_reader_mappings
        cn_fund_type_map = load_reader_mappings(connector, "cn_fund", "type_map")
        cn_fund_result = sync_cn_fund(config, extra_metadata={"cn_fund_type_map": cn_fund_type_map})
        cn_fund_holdings = _df_len(cn_fund_result.get("holdings"))
        cn_fund_txns = _df_len(cn_fund_result.get("transactions"))
        holdings_df = _normalize_holdings_df(
            cn_fund_result.get("holdings", pd.DataFrame()),
            "CN_Fund_Excel",
        )
        tx_df = _normalize_transactions_df(
            cn_fund_result.get("transactions", pd.DataFrame()),
            "CN_Fund_Excel",
        )
        inserted_holdings = _upsert_holdings(connector, holdings_df)
        inserted_txns = _replace_transactions(connector, tx_df)
        _log_sync_event(
            connector=connector,
            source_system="CN_Fund_Excel",
            target_table="holdings",
            record_key=f"{date.today().isoformat()}:CN_Fund_Excel",
            source_value={"read_holdings": cn_fund_holdings, "inserted_holdings": inserted_holdings},
        )
        _log_sync_event(
            connector=connector,
            source_system="CN_Fund_Excel",
            target_table="transactions",
            record_key=f"{date.today().isoformat()}:CN_Fund_Excel",
            source_value={"read_transactions": cn_fund_txns, "inserted_transactions": inserted_txns},
        )
        result.info_messages.append(
            f"CN Fund sync: {cn_fund_holdings} holdings, {cn_fund_txns} transactions "
            f"(inserted {inserted_holdings} holdings, {inserted_txns} transactions)"
        )

        # Detect and persist position deltas
        try:
            cn_snap_date = date.today()
            if not holdings_df.empty and "snapshot_date" in holdings_df.columns:
                cn_snap_date = holdings_df["snapshot_date"].max()
                if hasattr(cn_snap_date, 'date'):
                    cn_snap_date = cn_snap_date.date()
            cn_deltas = detect_and_persist_deltas(
                connector, "CN_Fund_Excel", cn_fund_pre_snapshot, cn_snap_date
            )
            result.position_deltas_detected += len(cn_deltas)
            if cn_deltas:
                result.info_messages.append(
                    f"CN Fund position deltas: {len(cn_deltas)} change(s) detected"
                )
        except Exception as e:
            result.warnings.append(f"CN Fund delta detection error: {str(e)}")

        return inserted_holdings, inserted_txns
    except Exception as e:
        result.warnings.append(f"CN Fund sync error: {str(e)}")
        return 0, 0


def _run_gold_reader(connector: DatabaseConnector, config: Dict[str, Any], result: SyncResult):
    """Phase 2.4.7 — Gold Excel reader. Returns (holdings_inserted, txns_inserted)."""
    from src.sources.base import READ_STATUS_DISABLED, READ_STATUS_READ_ERROR, read_status_of
    gold_config = config.get('source_registry', {}).get('gold', {})
    if not gold_config.get('enabled', False):
        _record_empty_source_signal(connector, result, "Gold_Excel", READ_STATUS_DISABLED, 0)
        return 0, 0
    try:
        # ADR-023 / WS-B: load UI-managed asset_name/account id_field_map
        # from the sync's own connection (no second RW connection) and
        # inject via extra_metadata — mirrors the FS fs_column wiring below.
        from src.services.reader_mappings import load_id_field_maps
        gold_id_field_maps = load_id_field_maps(connector, "gold")
        gold_result = sync_gold(config, extra_metadata={"id_field_maps_override": gold_id_field_maps})
        gold_holdings = _df_len(gold_result.get("holdings"))
        gold_txns = _df_len(gold_result.get("transactions"))
        holdings_df = _normalize_holdings_df(gold_result.get("holdings", pd.DataFrame()), "Gold_Excel")
        holdings_df, breakdown = _aggregate_gold_holdings(holdings_df)
        _record_empty_source_signal(
            connector, result, "Gold_Excel", read_status_of(gold_result), len(holdings_df),
        )
        tx_df = _normalize_transactions_df(gold_result.get("transactions", pd.DataFrame()), "Gold_Excel")
        inserted_holdings = _upsert_holdings(connector, holdings_df)
        inserted_txns = _replace_transactions(connector, tx_df)
        _log_sync_event(
            connector=connector,
            source_system="Gold_Excel",
            target_table="holdings",
            record_key=f"{date.today().isoformat()}:Gold_Excel",
            source_value={"read_holdings": gold_holdings, "inserted_holdings": inserted_holdings},
        )
        _log_sync_event(
            connector=connector,
            source_system="Gold_Excel",
            target_table="transactions",
            record_key=f"{date.today().isoformat()}:Gold_Excel",
            source_value={"read_transactions": gold_txns, "inserted_transactions": inserted_txns},
        )
        if breakdown:
            _log_sync_event(
                connector=connector,
                source_system="Gold_Excel",
                target_table="holdings",
                record_key=f"{date.today().isoformat()}:ALTS_Paper_Gold",
                source_value={"account_breakdown": breakdown},
                conflict_type="gold_rollup",
                resolution="aggregated",
            )
        result.info_messages.append(
            f"Gold sync: {gold_holdings} holdings, {gold_txns} transactions "
            f"(inserted {inserted_holdings} holdings, {inserted_txns} transactions)"
        )
        return inserted_holdings, inserted_txns
    except Exception as e:
        result.warnings.append(f"Gold sync error: {str(e)}")
        _record_empty_source_signal(connector, result, "Gold_Excel", READ_STATUS_READ_ERROR, 0)
        return 0, 0


def _run_insurance_reader(connector: DatabaseConnector, config: Dict[str, Any], result: SyncResult):
    """Phase 2.4.8 — Insurance Excel reader. Returns (holdings_inserted, txns_inserted)."""
    from src.sources.base import READ_STATUS_DISABLED, READ_STATUS_READ_ERROR, read_status_of
    ins_config = config.get('source_registry', {}).get('insurance', {})
    if not ins_config.get('enabled', False):
        _record_empty_source_signal(connector, result, "Insurance_Excel", READ_STATUS_DISABLED, 0)
        return 0, 0
    try:
        # ADR-023 / WS-B: load UI-managed product_name/policy_name
        # id_field_map (empty by default — insurance.yaml declares none
        # today) from the sync's own connection and inject via extra_metadata.
        from src.services.reader_mappings import load_id_field_maps
        ins_id_field_maps = load_id_field_maps(connector, "insurance")
        ins_result = sync_insurance(config, extra_metadata={"id_field_maps_override": ins_id_field_maps})
        ins_holdings = _df_len(ins_result.get("holdings"))
        ins_txns = _df_len(ins_result.get("transactions"))
        holdings_df = _normalize_holdings_df(ins_result.get("holdings", pd.DataFrame()), "Insurance_Excel")
        _record_empty_source_signal(
            connector, result, "Insurance_Excel", read_status_of(ins_result), len(holdings_df),
        )
        tx_df = _normalize_transactions_df(ins_result.get("transactions", pd.DataFrame()), "Insurance_Excel")
        inserted_holdings = _upsert_holdings(connector, holdings_df)
        inserted_txns = _replace_transactions(connector, tx_df)
        _log_sync_event(
            connector=connector,
            source_system="Insurance_Excel",
            target_table="holdings",
            record_key=f"{date.today().isoformat()}:Insurance_Excel",
            source_value={"read_holdings": ins_holdings, "inserted_holdings": inserted_holdings},
        )
        _log_sync_event(
            connector=connector,
            source_system="Insurance_Excel",
            target_table="transactions",
            record_key=f"{date.today().isoformat()}:Insurance_Excel",
            source_value={"read_transactions": ins_txns, "inserted_transactions": inserted_txns},
        )
        result.info_messages.append(
            f"Insurance sync: {ins_holdings} holdings, {ins_txns} transactions "
            f"(inserted {inserted_holdings} holdings, {inserted_txns} transactions)"
        )
        return inserted_holdings, inserted_txns
    except Exception as e:
        result.warnings.append(f"Insurance sync error: {str(e)}")
        _record_empty_source_signal(connector, result, "Insurance_Excel", READ_STATUS_READ_ERROR, 0)
        return 0, 0


def _run_rsu_reader(connector: DatabaseConnector, config: Dict[str, Any], result: SyncResult):
    """Phase 2.4.9 — RSU YAML reader. Returns (holdings_inserted, txns_inserted)."""
    from src.sources.base import READ_STATUS_DISABLED, READ_STATUS_READ_ERROR, read_status_of
    rsu_config = config.get('source_registry', {}).get('rsu', {})
    if not rsu_config.get('enabled', False):
        _record_empty_source_signal(connector, result, "RSU_Excel", READ_STATUS_DISABLED, 0)
        return 0, 0
    try:
        # ADR-023 / WS-B: load UI-managed asset_name id_field_map from the
        # sync's own connection and inject via extra_metadata.
        from src.services.reader_mappings import load_id_field_maps
        rsu_id_field_maps = load_id_field_maps(connector, "rsu")
        rsu_result = sync_rsu(config, extra_metadata={"id_field_maps_override": rsu_id_field_maps})
        rsu_holdings = _df_len(rsu_result.get("holdings"))
        rsu_txns = _df_len(rsu_result.get("transactions"))
        holdings_df = _normalize_holdings_df(rsu_result.get("holdings", pd.DataFrame()), "RSU_Excel")
        _record_empty_source_signal(
            connector, result, "RSU_Excel", read_status_of(rsu_result), len(holdings_df),
        )
        tx_df = _normalize_transactions_df(rsu_result.get("transactions", pd.DataFrame()), "RSU_Excel")
        inserted_holdings = _upsert_holdings(connector, holdings_df)
        inserted_txns = _replace_transactions(connector, tx_df)
        _log_sync_event(
            connector=connector,
            source_system="RSU_Excel",
            target_table="holdings",
            record_key=f"{date.today().isoformat()}:RSU_Excel",
            source_value={"read_holdings": rsu_holdings, "inserted_holdings": inserted_holdings},
        )
        _log_sync_event(
            connector=connector,
            source_system="RSU_Excel",
            target_table="transactions",
            record_key=f"{date.today().isoformat()}:RSU_Excel",
            source_value={"read_transactions": rsu_txns, "inserted_transactions": inserted_txns},
        )
        result.info_messages.append(
            f"RSU sync: {rsu_holdings} holdings, {rsu_txns} transactions "
            f"(inserted {inserted_holdings} holdings, {inserted_txns} transactions)"
        )
        return inserted_holdings, inserted_txns
    except Exception as e:
        result.warnings.append(f"RSU sync error: {str(e)}")
        _record_empty_source_signal(connector, result, "RSU_Excel", READ_STATUS_READ_ERROR, 0)
        return 0, 0


def _run_financial_summary_reader(connector: DatabaseConnector, config: Dict[str, Any], result: SyncResult):
    """Phase 2.4.10 — Financial Summary Excel reader. Returns (holdings_inserted, 0)."""
    fs_config = config.get('source_registry', {}).get('financial_summary', {})
    if not fs_config.get('enabled', False):
        return 0, 0
    try:
        fs_result = sync_financial_summary(config)
        fs_holdings = _df_len(fs_result.get("holdings"))
        inserted_bs, inserted_ie = _persist_financial_summary(connector, fs_result)

        # Extract discrete assets from balance sheet (Property, Cash, Pension, etc.)
        # Config-driven engine only (B5 — legacy path deleted).
        from src.sources.config_driven_reader import sync_config_source
        from src.sources.reader_config import load_reader_config
        from src.services.reader_mappings import load_reader_mappings
        _FS_YAML = Path("config/readers/financial_summary.yaml")
        # ADR-023 / WS-A: load UI-managed column→asset mappings from the sync's
        # own connection (no second RW connection) and inject via metadata so
        # the hook (stdlib+pandas only, no DB access) can use DB overrides.
        fs_asset_mappings = load_reader_mappings(connector, "financial_summary", "fs_column")
        fs_melted_holdings = sync_config_source(
            config,
            load_reader_config(_FS_YAML),
            extra_metadata={"fs_asset_mappings": fs_asset_mappings},
        ).get("holdings", pd.DataFrame())
        inserted_fs_holdings = _upsert_holdings(connector, fs_melted_holdings)

        _log_sync_event(
            connector=connector,
            source_system="Financial_Summary_Excel",
            target_table="holdings",
            record_key=f"{date.today().isoformat()}:Financial_Summary_Excel",
            source_value={"read_rows": len(fs_melted_holdings), "inserted_rows": inserted_fs_holdings},
        )

        result.info_messages.append(
            f"Financial Summary sync: {fs_holdings} metadata rows, "
            f"{inserted_fs_holdings} discrete holdings extracted"
        )
        return inserted_fs_holdings, 0
    except Exception as e:
        result.warnings.append(f"Financial Summary sync error: {str(e)}")
        return 0, 0


def _run_ibkr_reader(connector: DatabaseConnector, config: Dict[str, Any], result: SyncResult):
    """Phase 2.4.11b — IBKR Flex Query reader (NON-AUTHORITATIVE). Returns (holdings_inserted, txns_inserted)."""
    ibkr_config = config.get('source_registry', {}).get('ibkr', {})
    if not ibkr_config.get('enabled', False):
        return 0, 0
    try:
        # Capture pre-sync snapshot for position delta detection
        ibkr_pre_snapshot: dict = {}
        try:
            ibkr_pre_snapshot = capture_pre_sync_snapshot(connector, "Broker_IBKR")
        except Exception as e:
            result.warnings.append(f"IBKR pre-sync snapshot error: {str(e)}")

        # ADR-023 / WS-C: IBKR is co-authority with Schwab and reuses the
        # same symbol normalizer function — the SAME merged schwab
        # symbol_norm vocab must reach it (reader_key='schwab', not 'ibkr').
        from src.services.reader_mappings import load_reader_mappings
        ibkr_symbol_norm = load_reader_mappings(connector, "schwab", "symbol_norm")
        ibkr_result = sync_ibkr(config, extra_metadata={"schwab_symbol_norm": ibkr_symbol_norm})
        ibkr_holdings = _df_len(ibkr_result.get("holdings"))
        ibkr_txns = _df_len(ibkr_result.get("transactions"))
        holdings_df = _normalize_holdings_df(ibkr_result.get("holdings", pd.DataFrame()), "Broker_IBKR")
        tx_df = _normalize_transactions_df(ibkr_result.get("transactions", pd.DataFrame()), "Broker_IBKR")
        inserted_holdings = _upsert_holdings(connector, holdings_df)
        inserted_txns = _replace_transactions(connector, tx_df)
        _log_sync_event(
            connector=connector,
            source_system="Broker_IBKR",
            target_table="holdings",
            record_key=f"{date.today().isoformat()}:Broker_IBKR",
            source_value={"read_holdings": ibkr_holdings, "inserted_holdings": inserted_holdings},
        )
        _log_sync_event(
            connector=connector,
            source_system="Broker_IBKR",
            target_table="transactions",
            record_key=f"{date.today().isoformat()}:Broker_IBKR",
            source_value={"read_transactions": ibkr_txns, "inserted_transactions": inserted_txns},
        )
        result.info_messages.append(
            f"IBKR sync: {ibkr_holdings} holdings, {ibkr_txns} transactions "
            f"(inserted {inserted_holdings} holdings, {inserted_txns} transactions)"
        )

        # Detect and persist position deltas
        try:
            ibkr_snap_date = date.today()
            if not holdings_df.empty and "snapshot_date" in holdings_df.columns:
                ibkr_snap_date = holdings_df["snapshot_date"].max()
                if hasattr(ibkr_snap_date, 'date'):
                    ibkr_snap_date = ibkr_snap_date.date()
            ibkr_deltas = detect_and_persist_deltas(
                connector, "Broker_IBKR", ibkr_pre_snapshot, ibkr_snap_date
            )
            result.position_deltas_detected += len(ibkr_deltas)
            if ibkr_deltas:
                result.info_messages.append(
                    f"IBKR position deltas: {len(ibkr_deltas)} change(s) detected"
                )
        except Exception as e:
            result.warnings.append(f"IBKR delta detection error: {str(e)}")

        return inserted_holdings, inserted_txns
    except Exception as e:
        result.warnings.append(f"IBKR sync error: {str(e)}")
        return 0, 0


# ═══════════════════════════════════════════════════════
# Phase-2 reader dispatch table
# Specialized functions preserve per-reader behaviour (Schwab/CN-Fund/IBKR delta
# detection, Gold aggregation, FS melt). The order below is the historical P2 order
# and MUST be preserved. New config-driven readers (identity.category == "reader")
# with no specialized function auto-run via _run_config_reader.
# See ADR-018 / docs/plans/2026-06-20-import-adapter-config-convergence.md.
# ═══════════════════════════════════════════════════════
_PHASE2_READER_ORDER = ["schwab", "cn_fund", "gold", "insurance", "rsu", "financial_summary", "ibkr"]
_PHASE2_READER_FUNCS = {
    "schwab": _run_schwab_reader,
    "cn_fund": _run_cn_fund_reader,
    "gold": _run_gold_reader,
    "insurance": _run_insurance_reader,
    "rsu": _run_rsu_reader,
    "financial_summary": _run_financial_summary_reader,
    "ibkr": _run_ibkr_reader,
}


def _run_config_reader(connector: DatabaseConnector, config: Dict[str, Any], result: SyncResult, reader_key: str):
    """Generic P2 ingest for a config-driven reader that has no specialized function.

    Used for sources onboarded after the built-ins (e.g. wizard-generated readers).
    Mirrors the plain skeleton of _run_insurance_reader:
      enabled-check → sync → normalize → upsert/replace → log events → info message → return counts.
    """
    from src.sources.registry import get_registry
    from src.sources.config_driven_reader import sync_config_source
    from src.sources.reader_config import load_reader_config
    cfg = config.get('source_registry', {}).get(reader_key, {})
    if not cfg.get('enabled', False):
        return 0, 0
    system = get_registry().key_to_system().get(reader_key, reader_key)
    try:
        res = sync_config_source(config, load_reader_config(Path(f"config/readers/{reader_key}.yaml")))
        read_h = _df_len(res.get("holdings"))
        read_t = _df_len(res.get("transactions"))
        holdings_df = _normalize_holdings_df(res.get("holdings", pd.DataFrame()), system)
        tx_df = _normalize_transactions_df(res.get("transactions", pd.DataFrame()), system)
        inserted_holdings = _upsert_holdings(connector, holdings_df)
        inserted_txns = _replace_transactions(connector, tx_df)
        _log_sync_event(
            connector=connector,
            source_system=system,
            target_table="holdings",
            record_key=f"{date.today().isoformat()}:{system}",
            source_value={"read_holdings": read_h, "inserted_holdings": inserted_holdings},
        )
        _log_sync_event(
            connector=connector,
            source_system=system,
            target_table="transactions",
            record_key=f"{date.today().isoformat()}:{system}",
            source_value={"read_transactions": read_t, "inserted_transactions": inserted_txns},
        )
        result.info_messages.append(
            f"{system} sync: {read_h} holdings, {read_t} transactions "
            f"(inserted {inserted_holdings} holdings, {inserted_txns} transactions)"
        )
        return inserted_holdings, inserted_txns
    except Exception as e:
        result.warnings.append(f"{system} sync error: {str(e)}")
        return 0, 0


def _dispatch_phase2_readers(
    connector: DatabaseConnector,
    config: Dict[str, Any],
    result: SyncResult,
) -> tuple:
    """Drive the registry-ordered P2 reader dispatch.

    Calls each of the 7 specialized _run_<key>_reader functions in their
    historical order, then any NEW config-driven reader keys (category=="reader")
    that have no specialized function, via _run_config_reader.

    Returns (total_holdings_inserted, total_transactions_inserted).
    """
    from src.sources.registry import get_registry
    _reg = get_registry()
    _extra_reader_keys = sorted(
        k for k in _reg.reader_keys()
        if k not in _PHASE2_READER_FUNCS
        and _reg._config_for_key(k) is not None
        and _reg._config_for_key(k).identity.category == "reader"
    )
    total_h = 0
    total_t = 0
    for _key in _PHASE2_READER_ORDER + _extra_reader_keys:
        _fn = _PHASE2_READER_FUNCS.get(_key)
        if _fn is not None:
            h, t = _fn(connector, config, result)
        else:
            h, t = _run_config_reader(connector, config, result, _key)
        total_h += h
        total_t += t
    return total_h, total_t


def _run_phase2_ingest(
    connector: DatabaseConnector,
    config: Dict[str, Any],
    dry_run: bool,
    result: SyncResult,
) -> None:
    """P2: READER & ADAPTER INGEST — 6 source readers + import adapters + auto-register.

    Removed paths (kept as history pointers):
    - PIS transactions/holdings, AIA holdings, PIS allocations/tiers — Phase 9
      (ADR-003, superseded by the 6 source readers).
    - DSA SQLite market-data ingest (old step 2.3) — Phase A2; live prices are
      P3's job, historical backfill via `python main.py --sync-market`.
    """
    source_registry = config.get("source_registry", {})
    reader_names = ["schwab", "cn_fund", "gold", "insurance", "rsu", "financial_summary", "ibkr"]
    reader_enabled = any(source_registry.get(name, {}).get("enabled", False) for name in reader_names)
    reader_holdings_ingested = 0
    reader_transactions_ingested = 0

    pre_reader_backup_path: Optional[Path] = None
    if reader_enabled and not dry_run:
        if os.getenv("UIS_GCS_BUCKET"):
            # GCS flush already uploads a timestamped backup on every flush — skip
            # the local copy (memory-hostile on Cloud Run /tmp tmpfs).
            result.info_messages.append(
                "Cloud mode: local file backup skipped"
                " — GCS uploads a timestamped backup on every flush"
            )
            # pre_reader_backup_path stays None → cleanup block below is a no-op
        else:
            try:
                pre_reader_backup_path = create_backup(reason="pre-reader-insertion")
                result.info_messages.append(f"Reader-insertion backup created: {pre_reader_backup_path}")
            except Exception as e:
                result.warnings.append(f"Reader-insertion backup warning: {str(e)}")

    if reader_enabled:
        try:
            normalized = _normalize_legacy_prefixes(connector)
            if normalized:
                result.info_messages.append(f"Legacy prefix normalization: {normalized} rows fixed")
        except Exception as e:
            result.warnings.append(f"Legacy prefix normalization error: {str(e)}")

        try:
            if _run_reader_id_migration_once(connector):
                result.info_messages.append("Applied one-time INS_/RSU_ canonical ID migration")
        except Exception as e:
            result.warnings.append(f"Reader ID migration warning: {str(e)}")

    # 2.4.5 – 2.4.10: per-reader ingests (registry-driven dispatch — ADR-018)
    _dh, _dt = _dispatch_phase2_readers(connector, config, result)
    reader_holdings_ingested += _dh
    reader_transactions_ingested += _dt

    # 2.4.11 Import adapter sync (approved adapters only)
    try:
        adapter_counts = sync_approved_import_adapters(connector, config)
        adapter_holdings = int(adapter_counts.get("holdings", 0))
        adapter_transactions = int(adapter_counts.get("transactions", 0))
        reader_holdings_ingested += adapter_holdings
        reader_transactions_ingested += adapter_transactions
        if adapter_holdings or adapter_transactions:
            result.info_messages.append(
                f"Import adapters sync: {adapter_holdings} holdings, {adapter_transactions} transactions",
            )
    except Exception as e:
        result.warnings.append(f"Import adapter sync error: {str(e)}")

    # 2.4.11a Auto-register newly discovered assets from holdings/transactions
    try:
        registered = _auto_register_new_assets(connector)
        if registered:
            result.info_messages.append(f"Auto-registry: Registered {registered} new assets")
            result.assets_registered += registered
    except Exception as e:
        result.warnings.append(f"Auto-registry error: {str(e)}")

    result.holdings_synced = reader_holdings_ingested
    result.transactions_synced = reader_transactions_ingested

    # Remove pre-reader-insertion backup when sync was a no-op:
    # pre-sync-v3 already covers this DB state, so the extra copy is wasteful.
    if (pre_reader_backup_path is not None
            and reader_holdings_ingested == 0
            and reader_transactions_ingested == 0):
        try:
            pre_reader_backup_path.unlink(missing_ok=True)
            result.info_messages.append(
                "Reader-insertion backup removed (no changes — pre-sync-v3 sufficient)"
            )
        except Exception as e:
            result.info_messages.append(f"Reader-insertion backup cleanup note: {str(e)}")

    # Post-reader sanity check
    if reader_enabled and reader_holdings_ingested == 0 and reader_transactions_ingested == 0:
        result.warnings.append(
            "ALERT: All readers enabled but 0 holdings and 0 transactions synced. "
            "Check source file paths, file freshness, and reader logs above."
        )
        logger.warning("Zero data ingested despite readers being enabled")


def _run_phase3_price_refresh(connector: DatabaseConnector, result: SyncResult) -> None:
    """P3: LIVE PRICE REFRESH — the only price path.

    Fetches live quotes via yfinance/akshare/gold_sge for all active holdings
    plus live FX (USDCNY=X, fallback 7.0); updates market_daily and holdings
    price columns. Runs after all reader inserts, before the shadow pipeline.
    """
    try:
        import time as _time
        _lpr_start = _time.monotonic()
        from src.market_data.service import MarketDataService as _MarketDataService
        refresh_result = _MarketDataService().refresh_portfolio_prices(connector)
        result.live_price_holdings_updated = int(refresh_result.get("holdings_updated", 0) or 0)
        result.info_messages.append(
            f"Live price refresh: {refresh_result.get('refreshed', 0)} refreshed, "
            f"{refresh_result.get('skipped', 0)} skipped, "
            f"{refresh_result.get('errors', 0)} errors, "
            f"{refresh_result.get('holdings_updated', 0)} holdings updated"
        )
        _record_step(result, "live_price_refresh", critical=False, status="ok",
                     duration_ms=int((_time.monotonic() - _lpr_start) * 1000))
    except Exception as e:
        result.warnings.append(f"Live price refresh error: {str(e)}")
        _record_step(result, "live_price_refresh", critical=False, status="failed", error=str(e))


def _cleanup_unknown_and_gold_holdings(connector: DatabaseConnector, result: SyncResult) -> None:
    """Phase 2.4.15 — Cleanup UNKNOWN_ assets, migrate GOLD_PAPER_, normalize BRK/CASH/property."""
    try:
        res = connector.execute(
            """
            DELETE FROM holdings
            WHERE asset_id LIKE 'UNKNOWN_%'
              AND (market_value IS NULL OR market_value = 0)
            RETURNING asset_id
            """
        ).fetchall()
        if res:
             result.info_messages.append(f"Cleanup: Deleted {len(res)} metadata UNKNOWN_ holdings")

        res_reg = connector.execute(
            """
            DELETE FROM asset_registry
            WHERE canonical_id LIKE 'UNKNOWN_%'
              AND canonical_id NOT IN (SELECT DISTINCT asset_id FROM holdings)
            RETURNING canonical_id
            """
        ).fetchall()
        if res_reg:
             result.info_messages.append(f"Cleanup: Deleted {len(res_reg)} metadata UNKNOWN_ registry entries")

        # Also clean up UNKNOWN_ transactions (PIS phantom adjustments)
        res_txn = connector.execute("""
            DELETE FROM transactions
            WHERE asset_id LIKE 'UNKNOWN_%'
            RETURNING asset_id
        """).fetchall()
        if res_txn:
            result.info_messages.append(f"Cleanup: Deleted {len(res_txn)} UNKNOWN_ transactions")

        # Migrate legacy GOLD_PAPER_ transactions to combined ALTS_Paper_Gold
        res_gold = connector.execute("""
            UPDATE transactions
            SET asset_id = 'ALTS_Paper_Gold'
            WHERE asset_id LIKE 'GOLD_PAPER_%'
            RETURNING asset_id
        """).fetchall()
        if res_gold:
            result.info_messages.append(f"Cleanup: Migrated {len(res_gold)} GOLD_PAPER_ transactions to ALTS_Paper_Gold")

        # Normalize BRK symbol variants to NYSE standard (BRK-B / BRK-A).
        # Schwab positions CSV emits BRK/B; transactions CSV emits BRKB (no separator).
        # Both must resolve to the same canonical ID for price matching and P&L to work.
        #
        # DuckDB FK quirk: UPDATE on a parent table row that a child FK references is
        # treated as DELETE+INSERT internally and fires the FK constraint even when we
        # are only changing a non-PK column.  Work-around: null the trade_log links
        # before touching the transactions table.
        _brk_variants = [
            ('US_STK_BRK/B', 'US_STK_BRK-B'), ('US_STK_BRKB',  'US_STK_BRK-B'),
            ('US_STK_BRK/A', 'US_STK_BRK-A'), ('US_STK_BRKA',  'US_STK_BRK-A'),
        ]
        _brk_rename_count = 0
        for bad_id, good_id in _brk_variants:
            # 1. Holdings: delete conflicting rows (reader already inserted correct ID for
            #    current snapshot); rename surviving historical snapshots.
            connector.execute(
                """
                DELETE FROM holdings
                WHERE asset_id = ?
                  AND EXISTS (
                      SELECT 1 FROM holdings h2
                      WHERE h2.asset_id = ?
                        AND h2.snapshot_date = holdings.snapshot_date
                        AND h2.source_system = holdings.source_system
                  )
                """,
                (bad_id, good_id),
            )
            rows = connector.execute(
                "UPDATE holdings SET asset_id = ? WHERE asset_id = ? RETURNING asset_id",
                (good_id, bad_id),
            ).fetchall()
            _brk_rename_count += len(rows)

            # 2. Transactions: reader re-inserted them with the correct ID on this sync,
            #    so old-variant rows are now duplicates.  Null FK links first (DuckDB
            #    constraint), then delete the stale rows.
            bad_tx_ids = [r[0] for r in connector.execute(
                "SELECT id FROM transactions WHERE asset_id = ?", (bad_id,)
            ).fetchall()]
            if bad_tx_ids:
                ph = ", ".join("?" * len(bad_tx_ids))
                connector.execute(
                    f"UPDATE trade_logs SET linked_transaction_id = NULL "
                    f"WHERE linked_transaction_id IN ({ph})",
                    bad_tx_ids,
                )
                connector.execute(
                    f"DELETE FROM transactions WHERE id IN ({ph})",
                    bad_tx_ids,
                )
                _brk_rename_count += len(bad_tx_ids)

            # 3. trade_logs: rename asset_id so the log entry still references the right asset.
            rows = connector.execute(
                "UPDATE trade_logs SET asset_id = ? WHERE asset_id = ? RETURNING asset_id",
                (good_id, bad_id),
            ).fetchall()
            _brk_rename_count += len(rows)

            # 4. Registry: delete stale entry so auto-registry rebuilds with correct display_name.
            connector.execute("DELETE FROM asset_registry WHERE canonical_id = ?", (bad_id,))

        if _brk_rename_count:
            result.info_messages.append(
                f"Cleanup: Normalized {_brk_rename_count} BRK symbol variant rows → BRK-B/BRK-A"
            )

        # Normalize CASH_ asset_class from Chinese "现金" to English "Cash Checking"
        # Financial Summary reader registers these with Chinese names; normalize for taxonomy joins
        res_cash = connector.execute("""
            UPDATE asset_registry
            SET asset_class = 'Cash Checking'
            WHERE canonical_id LIKE 'CASH_%' AND asset_class = '现金'
            RETURNING canonical_id
        """).fetchall()
        if res_cash:
            result.info_messages.append(f"Cleanup: Normalized {len(res_cash)} CASH_ asset_class from '现金' to 'Cash Checking'")

        # Normalize Chinese property class "住宅地产" to English "Property" for taxonomy_classes join
        # taxonomy_classes has "Property" (id=15, parent=Real Estate) but no Chinese equivalent
        res_property = connector.execute("""
            UPDATE asset_registry
            SET asset_class = 'Property'
            WHERE asset_class = '住宅地产'
            RETURNING canonical_id
        """).fetchall()
        if res_property:
            result.info_messages.append(f"Cleanup: Normalized {len(res_property)} asset_class from '住宅地产' to 'Property'")
    except Exception as e:
        result.warnings.append(f"Cleanup error: {str(e)}")


def _run_phase4_shadow_cleanup(
    connector: DatabaseConnector,
    config: Dict[str, Any],
    result: SyncResult,
) -> None:
    """P4: shadow pipeline (is_shadow writer #1), UNKNOWN cleanup, FIFO, insurance, non-tradeable, RSU price."""
    # 2.4.11.9 Zero-tombstone sources that verifiably reported nothing (task #16).
    # Must run BEFORE the staleness sweeps below, which are told to leave those
    # sources' history alone. Only sources with affirmative evidence of emptiness
    # (READ_STATUS_OK + zero rows) are ever in this set.
    empty_verified = set(getattr(result, "empty_verified_sources", set()) or ())
    try:
        tombstoned_empty = _tombstone_empty_verified_sources(connector, empty_verified)
        if tombstoned_empty:
            result.warnings.append(
                f"[EMPTY-SOURCE] {sorted(empty_verified)} read OK but reported zero holdings — "
                f"{tombstoned_empty} holding(s) zeroed via tombstone"
            )
    except Exception as e:
        result.warnings.append(f"Empty-source tombstone error: {str(e)}")

    # 2.4.12 Shadow stale reader holdings (freshness pruning)
    try:
        shadowed_stale = _shadow_stale_reader_holdings(connector, empty_verified)
        if shadowed_stale:
            result.info_messages.append(f"Reader shadowing: {shadowed_stale} stale holdings pruned")
    except Exception as e:
        result.warnings.append(f"Reader shadowing error: {str(e)}")

    # 2.4.12.6 Co-authority tombstone — prune broker assets dropped via ACAT transfer
    try:
        tombstoned = _shadow_coauthority_tombstone(connector)
        if tombstoned:
            result.info_messages.append(f"Co-authority tombstone: {tombstoned} stale broker holdings pruned")
    except Exception as e:
        result.warnings.append(f"Co-authority tombstone error: {str(e)}")

    # 2.4.12.5 Shadow stale non-tradable holdings (Insurance, RSU, Gold)
    try:
        shadowed_non_tradable = _shadow_stale_non_tradable_holdings(connector, empty_verified)
        if shadowed_non_tradable:
            result.info_messages.append(f"Non-tradable shadowing: {shadowed_non_tradable} stale holdings pruned")
    except Exception as e:
        result.warnings.append(f"Non-tradable shadowing error: {str(e)}")

    # 2.4.13 Shadow stale historical holdings (keep only latest per asset)
    try:
        shadowed_hist = _shadow_stale_historical_holdings(connector)
        if shadowed_hist:
            result.info_messages.append(f"Historical shadowing: {shadowed_hist} older snapshots pruned")
    except Exception as e:
        result.warnings.append(f"Historical shadowing error: {str(e)}")

    # 2.4.14 Shadow legacy holdings superseded by reader sources
    try:
        # PIS is shadowed by both real-time readers and historical baseline (FS)
        shadowed = _shadow_legacy_holdings(
            connector,
            reader_sources=READER_HOLDING_SOURCES | HISTORICAL_HOLDING_SOURCES
        )
        if shadowed:
            result.info_messages.append(
                f"Legacy shadow migration: {shadowed} PIS holdings marked as shadow",
            )
    except Exception as e:
        result.warnings.append(f"Legacy shadow migration error: {str(e)}")

    # 2.4.15 Cleanup metadata UNKNOWN_ assets, GOLD_PAPER_, BRK variants, CASH_/住宅地产 classes
    _cleanup_unknown_and_gold_holdings(connector, result)

    # 2.4.16 Backfill FIFO cost basis for reader holdings with NULL cost
    try:
        backfilled = _backfill_fifo_cost_basis(connector)
        if backfilled:
            result.info_messages.append(f"FIFO cost backfill: {backfilled} holdings updated")
    except Exception as e:
        result.warnings.append(f"FIFO cost backfill error: {str(e)}")

    # 2.4.13 Set insurance cost basis from premium payments
    try:
        ins_updated = _set_insurance_cost_from_premiums(connector)
        if ins_updated:
            result.info_messages.append(f"Insurance cost update: {ins_updated} holdings updated")
    except Exception as e:
        result.warnings.append(f"Insurance cost update error: {str(e)}")

    # 2.4.14 Zero P&L for non-tradeable PIS-only assets
    try:
        nt_updated = _zero_pl_for_non_tradeable_assets(connector)
        if nt_updated:
            result.info_messages.append(f"Non-tradeable P&L zeroed: {nt_updated} holdings")
    except Exception as e:
        result.warnings.append(f"Non-tradeable P&L error: {str(e)}")

    # 2.4.15 Shadow stale PIS holdings for fully-sold assets with no reader coverage
    # Handles CN_FUND_519674-type case: asset fully sold, PIS phantom Adjustment_Buy
    # transactions re-created a holding row, but no reader source exists for it anymore.
    # Since no reader row exists, the normal shadow pipeline (step 2.4.11) can't shadow it.
    try:
        stale_result = connector.execute("""
            UPDATE holdings SET is_shadow = TRUE
            WHERE source_system = 'PIS'
              AND is_shadow = FALSE
              AND asset_id NOT IN (
                  SELECT DISTINCT asset_id FROM holdings
                  WHERE source_system IN (
                      'Schwab_CSV', 'CN_Fund_Excel', 'Gold_Excel',
                      'Insurance_Excel', 'RSU_Excel'
                  )
                  AND is_shadow = FALSE
              )
              AND asset_id IN (
                  SELECT asset_id FROM transactions
                  WHERE LOWER(transaction_type) IN ('sell', 'adjustment_sell')
                  GROUP BY asset_id
              )
        """)
        stale_shadowed = stale_result.rowcount if hasattr(stale_result, 'rowcount') else 0
        if stale_shadowed:
            result.info_messages.append(
                f"Stale PIS shadow: {stale_shadowed} sold-asset holdings shadowed (no reader coverage)"
            )
    except Exception as e:
        result.warnings.append(f"Stale PIS shadow error: {str(e)}")

    # 2.4.16 Update RSU prices from external sources (yfinance/Financial Summary)
    try:
        if _update_rsu_prices_from_external_sources(connector, config):
            result.info_messages.append("RSU price updated from external sources (yfinance/FS)")
    except Exception as e:
        result.warnings.append(f"RSU price update error: {str(e)}")

    # 2.4.17 Consolidate co-authority broker holdings into one merged Consolidated row (C3.4)
    try:
        consolidated_shadowed = _consolidate_coauthority_holdings(connector)
        if consolidated_shadowed:
            result.info_messages.append(
                f"Co-authority consolidation: {consolidated_shadowed} broker holdings merged into Consolidated rows"
            )
    except Exception as e:
        result.warnings.append(f"Co-authority consolidation error: {str(e)}")


def _run_phase5_authority(
    connector: DatabaseConnector,
    config: Dict[str, Any],
    result: SyncResult,
) -> None:
    """P5: AUTHORITY RESOLUTION (is_shadow writer #2 — source conflicts)"""
    try:
        from src.identity.authority_resolver import AuthorityResolver
        from src.sync.holdings_aggregator import HoldingsAggregator

        # Initialize components
        resolver = AuthorityResolver(config=config)

        # Inject dynamic rules from approved import adapters (ADR-004)
        adapter_rules = _load_adapter_authority_rules(connector)
        if adapter_rules:
            resolver.rules.extend(adapter_rules)
            resolver.rules.sort(key=lambda x: x.get('priority', 100))
            result.info_messages.append(f"Authority: injected {len(adapter_rules)} rule(s) from import adapters")

        aggregator = HoldingsAggregator(resolver)

        # Apply rules for all distinct reader snapshot dates, plus today
        dynamic_reader_sources = set(READER_HOLDING_SOURCES) | get_approved_adapter_source_systems(connector)
        reader_list_str = ", ".join(f"'{s}'" for s in dynamic_reader_sources)
        date_rows = connector.execute(f"SELECT DISTINCT snapshot_date FROM holdings WHERE source_system IN ({reader_list_str})").fetchall()
        authority_dates = {date.today()}
        for r in date_rows:
            if r[0]:
                d_val = _to_date(r[0])
                if d_val:
                    authority_dates.add(d_val)

        for auth_date in sorted(list(authority_dates)):
            aggregator.apply_authority_rules(connector, auth_date)

    except Exception as e:
        result.warnings.append(f"Authority resolution error: {str(e)}")


def _run_phase6_derived(connector: DatabaseConnector, result: SyncResult) -> None:
    """P6: DERIVED DATA"""
    # P6.1 Current allocations
    try:
        alloc_result = sync_current_allocations(connector)
        result.allocations_synced = alloc_result.get('synced', 0)
    except Exception as e:
        result.warnings.append(f"Allocation sync error: {str(e)}")


def _run_phase7_validation(
    connector: DatabaseConnector,
    config: Dict[str, Any],
    result: SyncResult,
) -> None:
    """P7: POST-SYNC VALIDATION + decision layer"""
    validation_config = config.get('validation', {})

    # 4.1 Cost basis validation
    try:
        cost_threshold = validation_config.get('cost_basis', {}).get('threshold_pct', 1.0)
        discrepancies = validate_cost_basis(connector, threshold_pct=cost_threshold)
        result.cost_basis_discrepancies = len(discrepancies)

        if discrepancies:
            result.warnings.append(f"Found {len(discrepancies)} cost basis discrepancies")
    except Exception as e:
        result.warnings.append(f"Cost basis validation error: {str(e)}")

    # 4.2 Allocation drift validation
    try:
        drift_threshold = validation_config.get('allocations', {}).get('drift_threshold_pct', 5.0)
        drifts = validate_allocations(connector, threshold_pct=drift_threshold)
        result.allocation_drifts = len(drifts)

        if drifts:
            result.warnings.append(f"Found {len(drifts)} allocation drifts exceeding {drift_threshold}%")
    except Exception as e:
        result.warnings.append(f"Allocation validation error: {str(e)}")

    # 4.3 Divergence Check (reader vs shadow/PIS)
    try:
        from src.validation.divergence_checker import DivergenceChecker
        from src.reports.discrepancy_reporter import DiscrepancyReporter

        div_checker = DivergenceChecker(connector)
        div_threshold = validation_config.get('divergence', {}).get('threshold_pct', 10.0)

        divergences = div_checker.check_divergence(threshold_pct=div_threshold)

        if divergences:
             result.warnings.append(f"Found {len(divergences)} authoritative/shadow divergences > {div_threshold}%")

             # Generate Report
             reporter = DiscrepancyReporter()
             report_path = reporter.generate_report(divergences, div_threshold)
             if report_path:
                 result.warnings.append(f"Discrepancy Report generated: {report_path}")

    except Exception as e:
        result.warnings.append(f"Divergence check error: {str(e)}")

    # 5.5 Decision layer: link trade logs, backfill, and score
    try:
        linked_summary = link_trade_logs_to_transactions(connector)
        backfill_summary = backfill_trade_logs_from_transactions(connector)
        scored = score_all_trades(connector)
        result.info_messages.append(
            "Decision sync: "
            f"linked_matched={linked_summary.get('verified', 0)}, "
            f"linked_ambiguous={linked_summary.get('ambiguous', 0)}, "
            f"linked_unmatched={linked_summary.get('unmatched', 0)}, "
            f"backfilled={backfill_summary.get('inserted', 0)}, "
            f"backfill_attributed={backfill_summary.get('attributed', 0)}, "
            f"scored={scored}"
        )
    except Exception as e:
        result.warnings.append(f"Decision sync error: {str(e)}")


def _run_phase8_audit(
    connector: DatabaseConnector,
    pre_sync_summary: dict,
    result: SyncResult,
) -> None:
    """P8: SYNC DIFF REPORT + INTEGRITY GATE"""
    _p8_start = time.monotonic()
    # see ADR-006 (GCS persistence topology — flush after sync before success=True)
    # see docs/architecture/MAP.md §Large file warnings (do not edit without reading step ordering)

    diff: dict = {}

    # 6.1 Compute before/after financial diff
    try:
        post_sync_summary = _capture_sync_summary(connector)
        diff = _compute_sync_diff(pre_sync_summary, post_sync_summary)
        result.sync_diff = diff

        change_pct = diff.get("net_worth_change_pct", 0)
        pre_nw = diff.get("net_worth_before", 0)
        post_nw = diff.get("net_worth_after", 0)
        result.info_messages.append(
            f"Sync diff: net_worth {pre_nw:,.0f} -> {post_nw:,.0f} CNY "
            f"({change_pct:+.1f}%)"
        )
        if diff.get("alert"):
            result.warnings.append(
                f"WARNING: Net worth changed by {change_pct:.1f}% — "
                "exceeds 30% threshold. Verify no partial snapshot or currency issue."
            )
        if diff.get("net_worth_move_warning"):
            result.warnings.append(diff["net_worth_move_warning"])
    except Exception as e:
        result.warnings.append(f"Sync diff error: {str(e)}")

    # 6.2 Data integrity gate
    #
    # Blocking failures  → success=False  (corrupt sync output; cannot trust data)
    # Advisory failures  → degraded=True  (data-quality observations; sync is usable)
    #
    # _record_step semantics (verified orchestrator.py:1811-1815):
    #   status="failed", critical=True  → success=False
    #   status="failed", critical=False → degraded=True
    # status="degraded" is a silent no-op — advisory failures MUST use critical=False.
    integrity_report = None
    try:
        from src.validation.data_integrity_gate import is_blocking as _is_blocking_check
        import time as _time
        _ig_start = _time.monotonic()
        integrity_report = run_integrity_checks(connector)
        _ig_ms = int((_time.monotonic() - _ig_start) * 1000)
        result.integrity_checks_passed = integrity_report.passed_count
        result.integrity_checks_total = len(integrity_report.checks)

        if not integrity_report.all_passed:
            failed_checks = integrity_report.failed_checks
            blocking_failures = [c for c in failed_checks if _is_blocking_check(c.name)]
            advisory_failures = [c for c in failed_checks if not _is_blocking_check(c.name)]

            blocking_names = [c.name for c in blocking_failures]
            advisory_names = [c.name for c in advisory_failures]

            # Build a descriptive error that clearly labels each class.
            error_parts = []
            if blocking_names:
                error_parts.append(f"BLOCKING: {blocking_names}")
            if advisory_names:
                error_parts.append(f"ADVISORY: {advisory_names}")
            error_msg = (
                f"{len(failed_checks)} check(s) failed "
                f"({len(blocking_failures)} blocking, {len(advisory_failures)} advisory): "
                + "; ".join(error_parts)
            )

            result.warnings.append(
                f"Integrity gate: {integrity_report.passed_count}/{len(integrity_report.checks)} passed. "
                f"FAILED — {error_msg}"
            )

            if blocking_failures:
                # At least one blocking failure → sync output cannot be trusted.
                _record_step(result, "integrity_gate", critical=True, status="failed",
                             error=error_msg, duration_ms=_ig_ms)
            else:
                # Advisory-only failures → sync is usable but degraded.
                _record_step(result, "integrity_gate", critical=False, status="failed",
                             error=error_msg, duration_ms=_ig_ms)
        else:
            result.info_messages.append(
                f"Integrity gate: {integrity_report.passed_count}/{len(integrity_report.checks)} checks passed"
            )
            _record_step(result, "integrity_gate", critical=True, status="ok", duration_ms=_ig_ms)
    except Exception as e:
        integrity_report = None
        result.warnings.append(f"Integrity gate error: {str(e)}")
        _record_step(result, "integrity_gate", critical=True, status="failed", error=str(e))

    # 6.3 Persist Sync Audit Report
    try:
        from src.validation.sync_audit import SyncAuditReport, persist_sync_audit
        from src.validation.data_integrity_gate import is_blocking as _is_blocking_check
        import uuid

        diff_dict = diff  # set above in 6.1 (defaults to {} if 6.1 failed)

        # Reader counts reflect rows reprocessed on every sync, not necessarily rows whose
        # persisted state changed. Use the before/after portfolio diff plus explicit
        # Phase C/D change signals to classify repeat no-op syncs.
        is_no_change = _is_no_change_sync(diff_dict, result)

        audit_report = SyncAuditReport(
            sync_id=str(uuid.uuid4()),
            timestamp=datetime.now().isoformat(),
            net_worth_before=diff_dict.get("net_worth_before", 0.0),
            net_worth_after=diff_dict.get("net_worth_after", 0.0),
            net_worth_change_pct=diff_dict.get("net_worth_change_pct", 0.0),
            asset_count_before=diff_dict.get("asset_count_before", 0),
            asset_count_after=diff_dict.get("asset_count_after", 0),
            by_source_before=diff_dict.get("by_source_before", {}),
            by_source_after=diff_dict.get("by_source_after", {}),
            integrity_passed=result.integrity_checks_passed,
            integrity_total=result.integrity_checks_total,
            integrity_checks=[
                {
                    "name": c.name,
                    "passed": c.passed,
                    "actual_value": str(c.actual_value),
                    "threshold": str(c.threshold) if c.threshold else "",
                    "details": c.details,
                    "blocking": _is_blocking_check(c.name),
                } for c in integrity_report.checks
            ] if integrity_report else [],
            reader_counts={
                "transactions_synced": result.transactions_synced,
                "holdings_synced": result.holdings_synced,
                "market_records_synced": result.market_records_synced,
                "allocations_synced": result.allocations_synced,
                "live_price_holdings_updated": result.live_price_holdings_updated,
                "position_deltas_detected": result.position_deltas_detected,
            },
            warnings=result.warnings,
            info_messages=result.info_messages,
            is_no_change=is_no_change,
            alert=diff_dict.get("alert", False),
            # P8's loop-level step is recorded only after this persistence runs,
            # so a synthetic P8 entry (duration = phase work up to persistence)
            # is appended to keep the persisted phase rail complete (review F1).
            steps=[
                {
                    "name": s.name,
                    "status": s.status,
                    "critical": s.critical,
                    "error": s.error,
                    "duration_ms": s.duration_ms,
                }
                for s in result.steps
            ] + [
                {
                    "name": "P8",
                    "status": "ok",
                    "critical": False,
                    "error": None,
                    "duration_ms": int((time.monotonic() - _p8_start) * 1000),
                }
            ],
        )
        persist_sync_audit(connector, audit_report)
        result.sync_audit_id = audit_report.sync_id
    except Exception as e:
        result.warnings.append(f"Failed to persist sync audit report: {str(e)}")


def _run_phase9_insights_continuity(
    connector: DatabaseConnector,
    config: Dict[str, Any],
    result: SyncResult,
) -> None:
    """P9: INSIGHTS CONTINUITY — advisory post-sync refresh of the insights loop.

    ADVISORY: any sub-task failure logs a WARNING and never sets sync failure.
    All five sub-tasks share the orchestrator's existing write connection so that
    no second connection is opened against the same DB file (V7.0.0 DuckDB model).

    Enabled by default; disable via config key ``insights_continuity.enabled: false``.

    Sub-tasks (each in its own try/except so one failure cannot stop the others):
        (a) bridge_ai_insights_to_decision_hub  — reconcile qualifying ai_insights
            rows into the Decision Hub insights table.
        (b) score_all_trades                    — backfill verdict / outcome_pct
            for matured trade_logs rows.
        (c) recompute_auto_links                — upsert insight↔trade attribution
            links from the ±3-day source-match heuristic.
        (d) compute_verification_report         — freshness-gated: only runs when
            the newest verification_logs.created_at is older than 24 hours (or the
            table is empty).  SQL gate:
              SELECT COUNT(*) FROM verification_logs
              WHERE created_at >= CURRENT_TIMESTAMP - INTERVAL '24' HOUR
            If count > 0 → fresh → skip with INFO log.
        (e) BehavioralMetricsComputer           — compute all 6 behavioral
            dimensions (window_days=90) and persist to ai_behavioral_log.
    """
    import time as _time

    insights_cfg = config.get("insights_continuity", {})
    if not insights_cfg.get("enabled", True):
        logger.info("P9 insights_continuity: disabled by config — skipping")
        result.info_messages.append("Insights continuity (P9): disabled by config")
        return

    # ── (a0) Price continuity for pending-verification assets ────────────────
    # Refresh market prices for pending/pending_window trade_logs assets so that
    # sold assets (no longer in holdings) keep getting price data for the +30d
    # outcome window.  Root cause 3 of the review-verification outcome loop.
    # verification_blocked assets are included on a wider 120-day window so that
    # blocked rows can actually RECOVER (score_all_trades re-scores them once
    # prices exist) instead of staying blocked forever.
    try:
        from src.market_data.service import MarketDataService as _MarketDataService
        _t = _time.monotonic()
        _pending_rows = connector.execute(
            """
            SELECT DISTINCT asset_id
            FROM trade_logs
            WHERE (
                    (verification_status IN ('pending', 'pending_window')
                     AND log_date >= CURRENT_DATE - INTERVAL '45' DAY)
                 OR (verification_status = 'verification_blocked'
                     AND log_date >= CURRENT_DATE - INTERVAL '120' DAY)
              )
              AND asset_id IS NOT NULL
            """
        ).fetchall()
        _pending_asset_ids = [r[0] for r in _pending_rows if r[0]]
        _n_refreshed = _MarketDataService().refresh_prices_for_asset_ids(connector, _pending_asset_ids)
        result.info_messages.append(
            f"Insights continuity (a0) price continuity: {_n_refreshed} asset(s) refreshed"
        )
        _record_step(
            result, "P9.a0_price_continuity", critical=False, status="ok",
            duration_ms=int((_time.monotonic() - _t) * 1000),
        )
        logger.info("P9 (a0) price continuity: %d asset(s) refreshed", _n_refreshed)
    except Exception as _exc:
        result.warnings.append(f"Insights continuity (a0) price continuity failed: {_exc}")
        _record_step(result, "P9.a0_price_continuity", critical=False, status="failed",
                     error=str(_exc))
        logger.warning("P9 (a0) price continuity failed: %s", _exc)

    # ── (a0b) Historical price backfill for verification windows ─────────────
    # Fetches historical closes (CN-fund scraper or yfinance wide window) for
    # pending/blocked trade_logs rows that are missing the baseline or end-window
    # close needed to compute outcome_pct.  Root cause: sold assets have no
    # holding row so P3 never fetches them; P9 (a0) only gets realtime quotes.
    # Together (a0)+(a0b) ensure price coverage across the full +30d window.
    # Display-scoped so only Decision Hub rows with computable outcome are targeted.
    try:
        from src.market_data.service import MarketDataService as _MarketDataService2
        from src.services.decision_scorer import (
            build_trade_display_scope_sql as _scope_sql2,
        )
        _t = _time.monotonic()
        _scope_clause2 = _scope_sql2("tl")
        _backfill_rows = connector.execute(
            f"""
            SELECT tl.asset_id, tl.log_date
            FROM trade_logs tl
            WHERE tl.outcome_pct IS NULL
              AND (
                   tl.verification_status IN ('pending', 'pending_window', 'verification_blocked')
                   OR (tl.verification_status = 'verified' AND tl.verdict IS NOT NULL)
              )
              AND {_scope_clause2}
              AND tl.log_date >= CURRENT_DATE - INTERVAL '400' DAY
            """
        ).fetchall()
        _trades_to_backfill = [(r[0], r[1]) for r in _backfill_rows if r[0]]
        _n_backfilled = _MarketDataService2().backfill_trade_window_prices(
            connector, _trades_to_backfill
        )
        result.info_messages.append(
            f"Insights continuity (a0b) historical backfill: {_n_backfilled} asset(s) fetched"
        )
        _record_step(
            result, "P9.a0b_historical_backfill", critical=False, status="ok",
            duration_ms=int((_time.monotonic() - _t) * 1000),
        )
        logger.info("P9 (a0b) historical backfill: %d asset(s) fetched", _n_backfilled)
    except Exception as _exc:
        result.warnings.append(f"Insights continuity (a0b) historical backfill failed: {_exc}")
        _record_step(result, "P9.a0b_historical_backfill", critical=False, status="failed",
                     error=str(_exc))
        logger.warning("P9 (a0b) historical backfill failed: %s", _exc)

    # ── (a) Bridge ai_insights → Decision Hub ───────────────────────────────
    try:
        from src.services.ai_advisor.insight_manager import bridge_ai_insights_to_decision_hub
        _t = _time.monotonic()
        bridged = bridge_ai_insights_to_decision_hub(connector)
        result.info_messages.append(
            f"Insights continuity (a) bridge: {bridged} row(s) bridged to Decision Hub"
        )
        _record_step(
            result, "P9.a_insights_bridge", critical=False, status="ok",
            duration_ms=int((_time.monotonic() - _t) * 1000),
        )
        logger.info("P9 (a) bridge_ai_insights_to_decision_hub: %d bridged", bridged)
    except Exception as _exc:
        result.warnings.append(f"Insights continuity (a) bridge failed: {_exc}")
        _record_step(result, "P9.a_insights_bridge", critical=False, status="failed",
                     error=str(_exc))
        logger.warning("P9 (a) bridge_ai_insights_to_decision_hub failed: %s", _exc)

    # ── (b) Score all trades ─────────────────────────────────────────────────
    try:
        _t = _time.monotonic()
        scored = score_all_trades(connector)
        result.info_messages.append(
            f"Insights continuity (b) score_all_trades: {scored} trade(s) scored"
        )
        _record_step(
            result, "P9.b_score_trades", critical=False, status="ok",
            duration_ms=int((_time.monotonic() - _t) * 1000),
        )
        logger.info("P9 (b) score_all_trades: %d scored", scored)
    except Exception as _exc:
        result.warnings.append(f"Insights continuity (b) score_all_trades failed: {_exc}")
        _record_step(result, "P9.b_score_trades", critical=False, status="failed",
                     error=str(_exc))
        logger.warning("P9 (b) score_all_trades failed: %s", _exc)

    # ── (c) Recompute auto links ─────────────────────────────────────────────
    try:
        from src.services.decision_links import recompute_auto_links
        _t = _time.monotonic()
        links_added = recompute_auto_links(connector)
        result.info_messages.append(
            f"Insights continuity (c) recompute_auto_links: {links_added} link(s) inserted"
        )
        _record_step(
            result, "P9.c_auto_links", critical=False, status="ok",
            duration_ms=int((_time.monotonic() - _t) * 1000),
        )
        logger.info("P9 (c) recompute_auto_links: %d new links", links_added)
    except Exception as _exc:
        result.warnings.append(f"Insights continuity (c) recompute_auto_links failed: {_exc}")
        _record_step(result, "P9.c_auto_links", critical=False, status="failed",
                     error=str(_exc))
        logger.warning("P9 (c) recompute_auto_links failed: %s", _exc)

    # ── (d) Verification report — freshness-gated (>24 h stale or empty) ────
    try:
        from src.services.verification_service import compute_verification_report
        _t = _time.monotonic()
        # Freshness gate SQL: count rows created in the last 24 hours.
        # If count > 0 the table is fresh → skip; if 0 (empty or all stale) → run.
        _fresh_count_row = connector.execute(
            """
            SELECT COUNT(*)
            FROM verification_logs
            WHERE created_at >= CURRENT_TIMESTAMP - INTERVAL '24' HOUR
            """
        ).fetchone()
        _fresh_count = int(_fresh_count_row[0]) if _fresh_count_row else 0

        if _fresh_count > 0:
            result.info_messages.append(
                "Insights continuity (d) verification_report: skipped (fresh within 24 h)"
            )
            _record_step(
                result, "P9.d_verification", critical=False, status="ok",
                duration_ms=int((_time.monotonic() - _t) * 1000),
            )
            logger.info("P9 (d) compute_verification_report: skipped (fresh, count=%d)", _fresh_count)
        else:
            compute_verification_report(connector)
            result.info_messages.append(
                "Insights continuity (d) verification_report: computed (stale or empty)"
            )
            _record_step(
                result, "P9.d_verification", critical=False, status="ok",
                duration_ms=int((_time.monotonic() - _t) * 1000),
            )
            logger.info("P9 (d) compute_verification_report: computed")
    except Exception as _exc:
        result.warnings.append(
            f"Insights continuity (d) compute_verification_report failed: {_exc}"
        )
        _record_step(result, "P9.d_verification", critical=False, status="failed",
                     error=str(_exc))
        logger.warning("P9 (d) compute_verification_report failed: %s", _exc)

    # ── (e) Behavioral metrics (window_days=90) ──────────────────────────────
    try:
        from src.services.ai_advisor.behavioral_metrics import BehavioralMetricsComputer
        _t = _time.monotonic()
        _computer = BehavioralMetricsComputer()
        _metrics = _computer.compute_all(window_days=90, conn=connector)
        _computer.save_to_db(_metrics, conn=connector)
        result.info_messages.append(
            f"Insights continuity (e) behavioral_metrics: {len(_metrics)} dimension(s) computed"
        )
        _record_step(
            result, "P9.e_behavioral_metrics", critical=False, status="ok",
            duration_ms=int((_time.monotonic() - _t) * 1000),
        )
        logger.info("P9 (e) BehavioralMetricsComputer: %d dimensions", len(_metrics))
    except Exception as _exc:
        result.warnings.append(
            f"Insights continuity (e) behavioral_metrics failed: {_exc}"
        )
        _record_step(result, "P9.e_behavioral_metrics", critical=False, status="failed",
                     error=str(_exc))
        logger.warning("P9 (e) behavioral_metrics failed: %s", _exc)


# ═══════════════════════════════════════════════════════
# PUBLIC ENTRY POINT — thin coordinator
# see ADR-006 (GCS persistence topology — flush after sync before success=True)
# see docs/architecture/MAP.md §Large file warnings (do not edit without reading step ordering)
# ═══════════════════════════════════════════════════════

_PHASE_DISPATCH = {
    # manifest runner name → adapter binding the runner's actual signature
    "_run_phase0_backup_and_setup": lambda ctx: _resolve_runner("_run_phase0_backup_and_setup")(ctx.connector, ctx.dry_run, ctx.result),
    "_run_phase1_identity": lambda ctx: _resolve_runner("_run_phase1_identity")(ctx.connector, ctx.config, ctx.result),
    "_run_phase2_ingest": lambda ctx: _resolve_runner("_run_phase2_ingest")(ctx.connector, ctx.config, ctx.dry_run, ctx.result),
    "_run_phase3_price_refresh": lambda ctx: _resolve_runner("_run_phase3_price_refresh")(ctx.connector, ctx.result),
    "_run_phase4_shadow_cleanup": lambda ctx: _resolve_runner("_run_phase4_shadow_cleanup")(ctx.connector, ctx.config, ctx.result),
    "_run_phase5_authority": lambda ctx: _resolve_runner("_run_phase5_authority")(ctx.connector, ctx.config, ctx.result),
    "_run_phase6_derived": lambda ctx: _resolve_runner("_run_phase6_derived")(ctx.connector, ctx.result),
    "_run_phase7_validation": lambda ctx: _resolve_runner("_run_phase7_validation")(ctx.connector, ctx.config, ctx.result),
    "_run_phase8_audit": lambda ctx: _resolve_runner("_run_phase8_audit")(ctx.connector, ctx.pre_sync_summary, ctx.result),
    "_run_phase9_insights_continuity": lambda ctx: _resolve_runner("_run_phase9_insights_continuity")(ctx.connector, ctx.config, ctx.result),
}


def _resolve_runner(name: str):
    """Resolve a phase runner at call time so tests can patch module attributes."""
    return globals()[name]


def run_full_sync_v3(
    connector: DatabaseConnector,
    config: Dict[str, Any],
    dry_run: bool = False,
) -> SyncResult:
    """
    Run full v3 sync workflow with pre/post validation.

    Phase order and documentation live in src/sync/phases/manifest.py
    (PIPELINE_MANIFEST, P0–P8) — the single source of truth this function
    iterates. Summary: P0 backup → P1 identity → P2 reader/adapter ingest →
    P3 live price refresh → P4 shadow pipeline → P5 authority resolution →
    P6 derived → P7 validation/decision layer → P8 diff + integrity gate.

    Args:
        connector: Active database connector (may point to a tmp copy for dry-run).
        config:    Loaded settings dict.
        dry_run:   When True, suppresses all backup side-effects (the caller already
                   holds a tmp DB copy — no backup of the production DB is needed).
    """
    result = SyncResult(success=True)

    # Capture pre-sync financial summary for diff reporting (input to P8)
    pre_sync_summary = _capture_sync_summary(connector)

    ctx = PhaseContext(
        connector=connector,
        config=config,
        dry_run=dry_run,
        result=result,
        pre_sync_summary=pre_sync_summary,
    )
    for spec in PIPELINE_MANIFEST:
        logger.info("=== %s %s ===", spec.phase_id, spec.name)
        _start = time.monotonic()
        try:
            _PHASE_DISPATCH[spec.runner](ctx)
            _record_step(
                result, spec.phase_id, critical=False, status="ok",
                duration_ms=int((time.monotonic() - _start) * 1000),
            )
        except Exception as e:
            # Phases handle their own errors internally; reaching here means the
            # dispatch itself crashed. Record it and continue — matches the
            # pre-manifest behavior where no phase could abort its successors.
            _record_step(
                result, spec.phase_id, critical=False, status="failed",
                error=str(e),
                duration_ms=int((time.monotonic() - _start) * 1000),
            )
            result.warnings.append(f"{spec.phase_id} {spec.name} crashed: {e}")
            logger.exception("%s %s crashed", spec.phase_id, spec.name)

    # POST-P8: Export reference sheet (non-blocking convenience export).
    # Runs AFTER the integrity gate so values are already validated.
    # A failure here MUST NOT fail the sync — log a warning and continue.
    if not dry_run:
        try:
            from src.sync.reference_export import export_reference_sheet
            ref_path = export_reference_sheet(connector, config)
            result.info_messages.append(f"Reference sheet exported: {ref_path}")
            logger.info("Reference sheet exported: %s", ref_path)

            # Publish it to GCS so it can reach the owner's machine.
            #
            # Without this the export is write-only on Cloud Run: finance_dir is
            # overridden to /tmp/sources, so every refresh landed in ephemeral
            # container storage and was discarded on the next revision. Syncs are
            # cloud-only by policy, so the workbook beside the owner's
            # spreadsheet silently froze at the cloud migration — five weeks
            # stale before it was noticed. scripts/maint_db.py --pull-cloud
            # fetches it back.
            _bucket = os.environ.get("UIS_GCS_BUCKET")
            if _bucket:
                from src.storage.gcs import upload_reference_data_to_gcs
                if upload_reference_data_to_gcs(_bucket, str(ref_path)):
                    result.info_messages.append(
                        "Reference sheet published to GCS (pull-cloud delivers it)"
                    )
                else:
                    # Generated but not publishable — the owner would never see
                    # it, so say so rather than reporting a clean sync.
                    result.warnings.append(
                        f"Reference sheet generated but NOT published to GCS "
                        f"(nothing at {ref_path}) — it will not reach your machine"
                    )
        except Exception as _ref_exc:
            result.warnings.append(f"Reference sheet export warning: {_ref_exc}")
            logger.warning("Reference sheet export failed (non-fatal): %s", _ref_exc)

        # POST-P8: Recompute monthly attribution (current + previous month).
        # Advisory only — same non-blocking slot pattern as the reference
        # export above. A failure here MUST NOT fail the sync.
        try:
            from src.services.attribution import compute_month

            _today = date.today().replace(day=1)
            _prev = date(_today.year - 1, 12, 1) if _today.month == 1 else date(_today.year, _today.month - 1, 1)
            for _m in (_prev, _today):
                compute_month(connector, _m)
            result.info_messages.append("Attribution monthly recomputed (current + previous month)")
        except Exception as _attr_exc:
            result.warnings.append(f"Attribution recompute warning: {_attr_exc}")
            logger.warning("Attribution monthly recompute failed (non-fatal): %s", _attr_exc)

    return result
