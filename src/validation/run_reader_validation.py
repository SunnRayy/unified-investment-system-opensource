"""Run pre-insertion validation for all source readers.

This module is read-only with respect to DuckDB.
"""

from __future__ import annotations

import copy
import glob
from datetime import datetime
from pathlib import Path

import duckdb
import pandas as pd
from src.config import load_config as _load_config
from src.validation.reader_validator import (
    AssetComparison,
    FullValidationReport,
    IDConflict,
    ReaderValidationResult,
    compare_gold_holdings,
    compare_holdings,
    compare_transaction_counts,
    validate_schema_mapping,
)


HOLDINGS_DB_COLUMNS = [
    "asset_id",
    "snapshot_date",
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
]

TRANSACTIONS_DB_COLUMNS = [
    "asset_id",
    "transaction_date",
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

READER_DB_PATTERNS = {
    "schwab": ["US_STK_%", "US_ETF_%", "US_BND_%", "CASH_USD%"],
    "cn_fund": ["CN_FUND_%"],
    "gold": ["GOLD_%", "ALTS_Paper_Gold%", "ALTS_%Gold%"],
    "insurance": ["INS_%", "Ins_%"],
    "rsu": ["RSU_%"],
    "financial_summary": [],
}


def load_config() -> dict:
    """Load runtime config through the ONE loader in src.config.

    This used to raw-`open()` "config/settings.yaml" itself, which bypassed
    `src.config._resolve_config_file` and so had neither the `.example`
    fallback nor the cloud fail-fast guard. Since Program OSR untracked the
    real config, that raw open raised FileNotFoundError on every fresh clone —
    a 500 from the on-demand-audit endpoint (`src/api/routes/audit_v2.py`),
    which imports this function.
    """
    return _load_config()


def collect_db_holdings_by_prefix(
    conn: duckdb.DuckDBPyConnection, patterns: list[str], source_system: str = "PIS"
) -> pd.DataFrame:
    """Fetch latest holdings from DB by ID prefix patterns."""
    if not patterns:
        return pd.DataFrame(columns=["asset_id", "quantity", "market_value"])

    where_clause = " OR ".join([f"asset_id LIKE '{pattern}'" for pattern in patterns])
    query = f"""
        SELECT asset_id, quantity, market_price_unit, market_value,
               snapshot_date, source_system
        FROM holdings
        WHERE ({where_clause})
          AND source_system = '{source_system}'
          AND snapshot_date = (
              SELECT MAX(snapshot_date) FROM holdings
              WHERE source_system = '{source_system}'
          )
    """
    return conn.sql(query).fetchdf()


def collect_db_transactions_by_prefix(
    conn: duckdb.DuckDBPyConnection, patterns: list[str]
) -> pd.DataFrame:
    """Fetch transactions from DB by ID prefix patterns."""
    if not patterns:
        return pd.DataFrame()

    where_clause = " OR ".join([f"asset_id LIKE '{pattern}'" for pattern in patterns])
    query = f"""
        SELECT asset_id, transaction_date, transaction_type, quantity,
               price_unit, amount_gross, commission_fee, source_system
        FROM transactions
        WHERE ({where_clause})
    """
    return conn.sql(query).fetchdf()


def _find_source_file(config: dict, reader_name: str) -> str:
    registry = config.get("source_registry", {}).get(reader_name, {})
    data_dir = registry.get("data_dir")

    if not data_dir:
        data_dir = config.get("finance_dir", "")

    patterns = registry.get("file_patterns", {})
    if patterns:
        first_pattern = list(patterns.values())[0]
        matches = glob.glob(str(Path(data_dir) / first_pattern))
        if matches:
            return str(sorted(matches)[-1])

    return data_dir or ""


def _enable_reader_for_validation(config: dict, reader_name: str) -> dict:
    validation_config = copy.deepcopy(config)
    registry = validation_config.setdefault("source_registry", {})
    reader_config = registry.setdefault(reader_name, {})
    reader_config["enabled"] = True
    return validation_config


def validate_single_reader(
    reader_name: str,
    config: dict,
    db_holdings: pd.DataFrame | None = None,
    db_transactions: pd.DataFrame | None = None,
) -> ReaderValidationResult | None:
    """Run one reader and compare output to DB snapshots."""
    registry = config.get("source_registry", {}).get(reader_name, {})
    if not registry.get("enabled", False) and db_holdings is None:
        return None

    timestamp = datetime.now()
    warnings: list[str] = []
    source_file = ""
    comparisons = []
    schema_issues = []
    holdings_count = 0
    transactions_count = 0

    try:
        if reader_name == "schwab":
            from src.sync.schwab_sync import sync_schwab

            result = sync_schwab(config)
        elif reader_name == "cn_fund":
            from src.sync.cn_fund_sync import sync_cn_fund

            result = sync_cn_fund(config)
        elif reader_name == "gold":
            from src.sync.gold_sync import sync_gold

            result = sync_gold(config)
        elif reader_name == "insurance":
            from src.sync.insurance_sync import sync_insurance

            result = sync_insurance(config)
        elif reader_name == "rsu":
            from src.sync.rsu_sync import sync_rsu

            result = sync_rsu(config)
        elif reader_name == "financial_summary":
            from src.sync.financial_summary_sync import sync_financial_summary

            result = sync_financial_summary(config)
        else:
            warnings.append(f"Unknown reader: {reader_name}")
            return ReaderValidationResult(
                reader_name=reader_name,
                source_file="",
                timestamp=timestamp,
                holdings_count=0,
                transactions_count=0,
                warnings=warnings,
            )

        source_file = _find_source_file(config, reader_name)
        reader_holdings = result.get("holdings", pd.DataFrame())
        reader_transactions = result.get("transactions", pd.DataFrame())

        holdings_count = len(reader_holdings) if not reader_holdings.empty else 0
        transactions_count = len(reader_transactions) if not reader_transactions.empty else 0

        if db_holdings is not None and not reader_holdings.empty:
            if reader_name == "gold" and not db_holdings.empty:
                comparisons = [compare_gold_holdings(reader_holdings, db_holdings)]
                for _, row in reader_holdings.iterrows():
                    comparisons.append(
                        AssetComparison(
                            asset_id=row["asset_id"],
                            reader_id=row["asset_id"],
                            db_id=None,
                            reader_quantity=float(row["quantity"]),
                            db_quantity=None,
                            reader_value=float(row["market_value"]),
                            db_value=None,
                            status="reader_only",
                            notes="Per-account detail (part of combined gold position)",
                        )
                    )
            elif "asset_id" in reader_holdings.columns and "asset_id" in db_holdings.columns:
                comparisons = compare_holdings(
                    reader_holdings,
                    db_holdings,
                    id_column="asset_id",
                    value_column="market_value",
                    quantity_column="quantity",
                )
            else:
                warnings.append(
                    "Skipping holdings comparison: missing 'asset_id' column in reader or DB data."
                )

        if db_transactions is not None and not reader_transactions.empty:
            txn_summary = compare_transaction_counts(reader_transactions, db_transactions)
            warnings.append(
                f"Transactions: reader={txn_summary['reader_total']}, db={txn_summary['db_total']}"
            )

        if not reader_holdings.empty:
            schema_issues.extend(
                validate_schema_mapping(
                    reader_name, "holdings", list(reader_holdings.columns), HOLDINGS_DB_COLUMNS
                )
            )

        if not reader_transactions.empty:
            schema_issues.extend(
                validate_schema_mapping(
                    reader_name,
                    "transactions",
                    list(reader_transactions.columns),
                    TRANSACTIONS_DB_COLUMNS,
                )
            )
    except Exception as exc:  # pragma: no cover - defensive, validated in integration
        warnings.append(f"Reader error: {exc}")

    return ReaderValidationResult(
        reader_name=reader_name,
        source_file=source_file,
        timestamp=timestamp,
        holdings_count=holdings_count,
        transactions_count=transactions_count,
        comparisons=comparisons,
        schema_issues=schema_issues,
        warnings=warnings,
    )


def detect_id_conflicts(reader_results: dict[str, ReaderValidationResult]) -> list[IDConflict]:
    """Convert id_mismatch comparisons to explicit IDConflict objects."""
    conflicts = []
    for reader_name, result in reader_results.items():
        for comp in result.comparisons:
            if comp.status == "id_mismatch" and comp.reader_id and comp.db_id:
                conflicts.append(
                    IDConflict(
                        asset_symbol=comp.asset_id,
                        reader_id=comp.reader_id,
                        db_id=comp.db_id,
                        reader_name=reader_name,
                        resolution="needs_decision",
                        notes=comp.notes,
                    )
                )
    return conflicts


def run_full_validation(
    config: dict | None = None,
    db_path: str = "data/unified.duckdb",
) -> FullValidationReport:
    """Run validation across all readers against DB snapshots."""
    if config is None:
        config = load_config()

    conn = duckdb.connect(db_path, read_only=True)
    reader_results: dict[str, ReaderValidationResult] = {}
    all_schema_gaps = []

    readers = ["schwab", "cn_fund", "gold", "insurance", "rsu", "financial_summary"]
    for reader_name in readers:
        patterns = READER_DB_PATTERNS.get(reader_name, [])
        db_holdings = collect_db_holdings_by_prefix(conn, patterns)
        db_transactions = collect_db_transactions_by_prefix(conn, patterns)
        validation_config = _enable_reader_for_validation(config, reader_name)
        result = validate_single_reader(
            reader_name,
            validation_config,
            db_holdings=db_holdings,
            db_transactions=db_transactions,
        )
        if result is not None:
            reader_results[reader_name] = result
            all_schema_gaps.extend(result.schema_issues)

    conn.close()

    id_conflicts = detect_id_conflicts(reader_results)
    try:
        from src.validation.reader_validator import annotate_known_conflicts

        id_conflicts = annotate_known_conflicts(id_conflicts)
    except ImportError:
        pass

    has_errors = any(
        "error" in warning.lower()
        for result in reader_results.values()
        for warning in result.warnings
    )
    has_id_conflicts = len(id_conflicts) > 0
    has_schema_gaps = any(gap.gap_type == "rename_needed" for gap in all_schema_gaps)

    if has_errors:
        overall_status = "fail"
    elif has_id_conflicts or has_schema_gaps:
        overall_status = "warn"
    else:
        overall_status = "pass"

    return FullValidationReport(
        timestamp=datetime.now(),
        reader_results=reader_results,
        id_conflicts=id_conflicts,
        schema_gaps=all_schema_gaps,
        overall_status=overall_status,
    )
