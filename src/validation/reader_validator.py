"""Pre-insertion validation report models for source readers."""

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Optional

import pandas as pd


@dataclass
class AssetComparison:
    """Comparison of one asset between reader output and DB snapshot."""

    asset_id: str
    reader_id: Optional[str]
    db_id: Optional[str]
    reader_quantity: Optional[float]
    db_quantity: Optional[float]
    reader_value: Optional[float]
    db_value: Optional[float]
    status: str
    notes: str

    @property
    def quantity_diff(self) -> float:
        if self.reader_quantity is None or self.db_quantity is None:
            return 0.0
        return self.reader_quantity - self.db_quantity

    @property
    def value_diff(self) -> float:
        if self.reader_value is None or self.db_value is None:
            return 0.0
        return self.reader_value - self.db_value


@dataclass
class SchemaGap:
    """Column mapping gap between transformer output and DB schema."""

    reader_name: str
    data_type: str
    transformer_column: str
    db_column: str
    gap_type: str
    notes: str


@dataclass
class IDConflict:
    """Canonical ID mismatch between reader and DB records."""

    asset_symbol: str
    reader_id: str
    db_id: str
    reader_name: str
    resolution: str
    notes: str


@dataclass
class ReaderValidationResult:
    """Validation result for one source reader."""

    reader_name: str
    source_file: str
    timestamp: datetime
    holdings_count: int
    transactions_count: int
    comparisons: list[AssetComparison] = field(default_factory=list)
    schema_issues: list[SchemaGap] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def match_count(self) -> int:
        return sum(1 for item in self.comparisons if item.status == "match")

    @property
    def mismatch_count(self) -> int:
        return sum(1 for item in self.comparisons if item.status != "match")

    @property
    def total_comparisons(self) -> int:
        return len(self.comparisons)


@dataclass
class FullValidationReport:
    """Validation report aggregate across all readers."""

    timestamp: datetime
    reader_results: dict[str, ReaderValidationResult]
    id_conflicts: list[IDConflict]
    schema_gaps: list[SchemaGap]
    overall_status: str

    def to_json(self) -> str:
        data = {
            "timestamp": self.timestamp.isoformat(),
            "overall_status": self.overall_status,
            "id_conflicts": [asdict(item) for item in self.id_conflicts],
            "schema_gaps": [asdict(item) for item in self.schema_gaps],
            "reader_results": {},
        }
        for reader_name, result in self.reader_results.items():
            data["reader_results"][reader_name] = {
                "source_file": result.source_file,
                "holdings_count": result.holdings_count,
                "transactions_count": result.transactions_count,
                "match_count": result.match_count,
                "mismatch_count": result.mismatch_count,
                "comparisons": [asdict(item) for item in result.comparisons],
                "schema_issues": [asdict(item) for item in result.schema_issues],
                "warnings": result.warnings,
            }
        return json.dumps(data, indent=2, ensure_ascii=False, default=str)

    def to_markdown(self) -> str:
        lines = [
            "# Pre-Insertion Data Validation Report",
            "",
            f"**Generated**: {self.timestamp.isoformat()}",
            f"**Overall Status**: {self.overall_status.upper()}",
            "",
        ]

        if self.reader_results:
            lines.extend(
                [
                    "## Reader Summary",
                    "",
                    "| Reader | File | Holdings | Transactions | Matches | Mismatches |",
                    "|--------|------|----------|--------------|---------|------------|",
                ]
            )
            for name, result in self.reader_results.items():
                lines.append(
                    f"| {name} | {result.source_file} | {result.holdings_count} | "
                    f"{result.transactions_count} | {result.match_count} | {result.mismatch_count} |"
                )
            lines.append("")

        if self.id_conflicts:
            lines.extend(
                [
                    "## Canonical ID Conflicts",
                    "",
                    "| Symbol | Reader ID | DB ID | Reader | Resolution |",
                    "|--------|-----------|-------|--------|------------|",
                ]
            )
            for conflict in self.id_conflicts:
                lines.append(
                    f"| {conflict.asset_symbol} | `{conflict.reader_id}` | `{conflict.db_id}` "
                    f"| {conflict.reader_name} | {conflict.resolution} |"
                )
            lines.append("")

        if self.schema_gaps:
            lines.extend(
                [
                    "## Schema Mapping Gaps",
                    "",
                    "| Reader | Type | Transformer Col | DB Col | Gap |",
                    "|--------|------|-----------------|--------|-----|",
                ]
            )
            for gap in self.schema_gaps:
                lines.append(
                    f"| {gap.reader_name} | {gap.data_type} | `{gap.transformer_column}` | "
                    f"`{gap.db_column}` | {gap.gap_type} |"
                )
            lines.append("")

        for name, result in self.reader_results.items():
            lines.extend([f"## {name.title()} Reader", ""])
            if result.comparisons:
                lines.extend(
                    [
                        "| Asset | Reader ID | DB ID | Reader Qty | DB Qty | Status |",
                        "|-------|-----------|-------|------------|--------|--------|",
                    ]
                )
                for comp in result.comparisons:
                    db_id = comp.db_id or "N/A"
                    reader_qty = "N/A" if comp.reader_quantity is None else f"{comp.reader_quantity}"
                    db_qty = "N/A" if comp.db_quantity is None else f"{comp.db_quantity}"
                    lines.append(
                        f"| {comp.asset_id} | `{comp.reader_id}` | `{db_id}` | {reader_qty} | {db_qty} | {comp.status} |"
                    )
                lines.append("")

            if result.warnings:
                lines.extend(["### Warnings"])
                for warning in result.warnings:
                    lines.append(f"- {warning}")
                lines.append("")

        return "\n".join(lines)


def extract_symbol(canonical_id: str) -> str:
    """Extract the raw symbol portion from a canonical ID.

    Special cases:
    - 'ALTS_Paper_Gold' → 'Gold'  (must match market_daily.code stored by GoldPriceFetcher)
    - 'GOLD_*' → 'Gold'           (legacy gold asset IDs)
    """
    # Gold special case — must return 'Gold' to match market_daily.code
    if canonical_id == "ALTS_Paper_Gold" or canonical_id.startswith("GOLD_"):
        return "Gold"

    parts = canonical_id.split("_")

    # Handle RSU_RSU_AMZN legacy double-prefix pattern.
    if len(parts) >= 3 and parts[0] == "RSU" and parts[1] == "RSU":
        return "_".join(parts[2:])

    # Handle market-style IDs such as US_STK_X / CN_FUND_X.
    if len(parts) >= 3 and parts[0] in ("US", "CN", "HK"):
        return "_".join(parts[2:])

    # Handle all other PREFIX_SYMBOL patterns.
    if len(parts) >= 2:
        return "_".join(parts[1:])

    return canonical_id


def build_symbol_map(reader_ids: list[str], db_ids: list[str]) -> dict[str, dict[str, str]]:
    """Build symbol-level mapping for IDs that differ between reader and DB."""
    reader_by_symbol = {extract_symbol(reader_id): reader_id for reader_id in reader_ids}
    db_by_symbol = {extract_symbol(db_id): db_id for db_id in db_ids}

    symbol_map: dict[str, dict[str, str]] = {}
    for symbol in set(reader_by_symbol.keys()) & set(db_by_symbol.keys()):
        if reader_by_symbol[symbol] != db_by_symbol[symbol]:
            symbol_map[symbol] = {
                "reader": reader_by_symbol[symbol],
                "db": db_by_symbol[symbol],
            }
    return symbol_map


def compare_holdings(
    reader_df: pd.DataFrame,
    db_df: pd.DataFrame,
    id_column: str = "asset_id",
    value_column: str = "market_value",
    quantity_column: str = "quantity",
    symbol_map: Optional[dict[str, dict[str, str]]] = None,
    tolerance_pct: float = 5.0,
) -> list[AssetComparison]:
    """Compare reader holdings against DB holdings."""
    comparisons: list[AssetComparison] = []

    if symbol_map is None:
        reader_ids = reader_df[id_column].tolist() if not reader_df.empty else []
        db_ids = db_df[id_column].tolist() if not db_df.empty else []
        symbol_map = build_symbol_map(reader_ids, db_ids)

    reader_by_id = {}
    if not reader_df.empty:
        for _, row in reader_df.iterrows():
            reader_by_id[row[id_column]] = row

    db_by_id = {}
    if not db_df.empty:
        for _, row in db_df.iterrows():
            db_by_id[row[id_column]] = row

    matched_reader = set()
    matched_db = set()

    # Pass 1: exact canonical ID match.
    for reader_id, reader_row in reader_by_id.items():
        if reader_id in db_by_id:
            db_row = db_by_id[reader_id]
            reader_qty = float(reader_row.get(quantity_column, 0) or 0)
            db_qty = float(db_row.get(quantity_column, 0) or 0)
            reader_val = float(reader_row.get(value_column, 0) or 0)
            db_val = float(db_row.get(value_column, 0) or 0)

            value_tolerance = max(abs(reader_val * tolerance_pct / 100), 1.0)
            if reader_qty == db_qty and abs(reader_val - db_val) < value_tolerance:
                status = "match"
                notes = ""
            else:
                status = "value_mismatch"
                notes = f"qty diff: {reader_qty - db_qty}, value diff: {reader_val - db_val}"

            comparisons.append(
                AssetComparison(
                    asset_id=reader_id,
                    reader_id=reader_id,
                    db_id=reader_id,
                    reader_quantity=reader_qty,
                    db_quantity=db_qty,
                    reader_value=reader_val,
                    db_value=db_val,
                    status=status,
                    notes=notes,
                )
            )
            matched_reader.add(reader_id)
            matched_db.add(reader_id)

    # Pass 2: symbol-level matches to detect ID convention mismatches.
    for symbol, mapped_ids in symbol_map.items():
        reader_id = mapped_ids["reader"]
        db_id = mapped_ids["db"]
        if reader_id in matched_reader or db_id in matched_db:
            continue
        if reader_id in reader_by_id and db_id in db_by_id:
            reader_row = reader_by_id[reader_id]
            db_row = db_by_id[db_id]
            reader_qty = float(reader_row.get(quantity_column, 0) or 0)
            db_qty = float(db_row.get(quantity_column, 0) or 0)
            reader_val = float(reader_row.get(value_column, 0) or 0)
            db_val = float(db_row.get(value_column, 0) or 0)

            comparisons.append(
                AssetComparison(
                    asset_id=symbol,
                    reader_id=reader_id,
                    db_id=db_id,
                    reader_quantity=reader_qty,
                    db_quantity=db_qty,
                    reader_value=reader_val,
                    db_value=db_val,
                    status="id_mismatch",
                    notes=f"Reader uses {reader_id}, DB uses {db_id}",
                )
            )
            matched_reader.add(reader_id)
            matched_db.add(db_id)

    # Pass 3: reader-only records.
    for reader_id, reader_row in reader_by_id.items():
        if reader_id not in matched_reader:
            reader_qty = float(reader_row.get(quantity_column, 0) or 0)
            reader_val = float(reader_row.get(value_column, 0) or 0)
            comparisons.append(
                AssetComparison(
                    asset_id=reader_id,
                    reader_id=reader_id,
                    db_id=None,
                    reader_quantity=reader_qty,
                    db_quantity=None,
                    reader_value=reader_val,
                    db_value=None,
                    status="reader_only",
                    notes="Not found in DB",
                )
            )

    # Pass 4: DB-only records.
    for db_id, db_row in db_by_id.items():
        if db_id not in matched_db:
            db_qty = float(db_row.get(quantity_column, 0) or 0)
            db_val = float(db_row.get(value_column, 0) or 0)
            comparisons.append(
                AssetComparison(
                    asset_id=db_id,
                    reader_id=None,
                    db_id=db_id,
                    reader_quantity=None,
                    db_quantity=db_qty,
                    reader_value=None,
                    db_value=db_val,
                    status="db_only",
                    notes="Not found in reader output",
                )
            )

    return comparisons


def compare_transaction_counts(reader_df: pd.DataFrame, db_df: pd.DataFrame) -> dict:
    """Compare transaction counts between reader and DB snapshots."""
    result = {
        "reader_total": len(reader_df) if not reader_df.empty else 0,
        "db_total": len(db_df) if not db_df.empty else 0,
        "reader_by_type": {},
        "db_by_type": {},
    }

    if not reader_df.empty and "transaction_type" in reader_df.columns:
        result["reader_by_type"] = (
            reader_df["transaction_type"].str.lower().value_counts().to_dict()
        )

    if not db_df.empty and "transaction_type" in db_df.columns:
        result["db_by_type"] = db_df["transaction_type"].str.lower().value_counts().to_dict()

    return result


COLUMN_RENAME_MAP = {
    "holdings": {
        "cost_basis": "cost_price_unit",
    },
    "transactions": {
        "price": "price_unit",
        "amount": "amount_gross",
        "fees": "commission_fee",
        "description": "memo",
        "payment_date": "transaction_date",
        "price_usd": "price_unit",
        "amount_usd": "amount_gross",
        "fees_usd": "commission_fee",
    },
}


EXTRA_COLUMNS_OK = {
    "holdings": {
        "gain_dollar",
        "gain_percent",
        "cost_price",
        "unit",
        "account",
        "product_name",
        "insurer",
        "product_type",
        "annual_premium",
        "coverage",
        "coverage_scope",
        "status",
        "asset_name",
        "asset_type",
    },
    "transactions": {
        "account",
        "currency",
        "memo",
    },
}


KNOWN_ID_CONFLICTS = [
    {
        "reader": "schwab",
        "symbol": "IEF",
        "reader_id": "US_ETF_IEF",
        "db_id": "US_STK_IEF",
        "resolution": "use_db",
        "reason": "Maintain backward compatibility with existing US_STK_ IDs.",
    },
    {
        "reader": "schwab",
        "symbol": "SGOV",
        "reader_id": "US_ETF_SGOV",
        "db_id": "US_STK_SGOV",
        "resolution": "use_db",
        "reason": "Maintain backward compatibility with existing US_STK_ IDs.",
    },
    {
        "reader": "schwab",
        "symbol": "AGG",
        "reader_id": "US_ETF_AGG",
        "db_id": "US_STK_AGG",
        "resolution": "use_db",
        "reason": "Maintain backward compatibility with existing US_STK_ IDs.",
    },
    {
        "reader": "schwab",
        "symbol": "IBIT",
        "reader_id": "US_ETF_IBIT",
        "db_id": "US_STK_IBIT",
        "resolution": "use_db",
        "reason": "Maintain backward compatibility with existing US_STK_ IDs.",
    },
    {
        "reader": "schwab",
        "symbol": "FBTC",
        "reader_id": "US_ETF_FBTC",
        "db_id": "US_STK_FBTC",
        "resolution": "use_db",
        "reason": "Maintain backward compatibility with existing US_STK_ IDs.",
    },
    {
        "reader": "schwab",
        "symbol": "IEF",
        "reader_id": "US_FUND_IEF",
        "db_id": "US_STK_IEF",
        "resolution": "use_db",
        "reason": "Maintain backward compatibility with existing US_STK_ IDs.",
    },
    {
        "reader": "schwab",
        "symbol": "SGOV",
        "reader_id": "US_FUND_SGOV",
        "db_id": "US_STK_SGOV",
        "resolution": "use_db",
        "reason": "Maintain backward compatibility with existing US_STK_ IDs.",
    },
    {
        "reader": "schwab",
        "symbol": "AGG",
        "reader_id": "US_FUND_AGG",
        "db_id": "US_STK_AGG",
        "resolution": "use_db",
        "reason": "Maintain backward compatibility with existing US_STK_ IDs.",
    },
    {
        "reader": "schwab",
        "symbol": "IBIT",
        "reader_id": "US_FUND_IBIT",
        "db_id": "US_STK_IBIT",
        "resolution": "use_db",
        "reason": "Maintain backward compatibility with existing US_STK_ IDs.",
    },
    {
        "reader": "schwab",
        "symbol": "FBTC",
        "reader_id": "US_FUND_FBTC",
        "db_id": "US_STK_FBTC",
        "resolution": "use_db",
        "reason": "Maintain backward compatibility with existing US_STK_ IDs.",
    },
    {
        "reader": "gold",
        "symbol": "Paper_Gold",
        "reader_id": "GOLD_PAPER_CMB",
        "db_id": "ALTS_Paper_Gold",
        "resolution": "needs_decision",
        "reason": "Reader tracks per-account gold; DB currently stores one combined position.",
    },
    {
        "reader": "gold",
        "symbol": "Paper_Gold",
        "reader_id": "GOLD_PAPER_ICBC",
        "db_id": "ALTS_Paper_Gold",
        "resolution": "needs_decision",
        "reason": "Reader tracks per-account gold; DB currently stores one combined position.",
    },
    {
        "reader": "insurance",
        "symbol": "安泰人生",
        "reader_id": "INS_安泰人生",
        "db_id": "INS_安泰人生",
        "resolution": "resolved",
        "reason": "Normalizer now converts Ins_→INS_. DB and reader both use INS_ prefix.",
    },
    {
        "reader": "insurance",
        "symbol": "公司团险",
        "reader_id": "INS_公司团险",
        "db_id": "INS_公司团险",
        "resolution": "resolved",
        "reason": "Normalizer now converts Ins_→INS_. DB and reader both use INS_ prefix.",
    },
    {
        "reader": "insurance",
        "symbol": "互联网保险",
        "reader_id": "INS_互联网保险",
        "db_id": "INS_互联网保险",
        "resolution": "resolved",
        "reason": "Normalizer now converts Ins_→INS_. DB and reader both use INS_ prefix.",
    },
    {
        "reader": "rsu",
        "symbol": "AMZN",
        "reader_id": "RSU_AMZN",
        "db_id": "RSU_AMZN",
        "resolution": "resolved",
        "reason": "Normalizer now returns RSU_* as-is. Double-prefix RSU_RSU_ no longer created.",
    },
]


def validate_schema_mapping(
    reader_name: str,
    data_type: str,
    transformer_columns: list[str],
    db_columns: list[str],
) -> list[SchemaGap]:
    """Validate transformer output columns against DB schema columns."""
    gaps: list[SchemaGap] = []

    rename_map = COLUMN_RENAME_MAP.get(data_type, {})
    extra_ok = EXTRA_COLUMNS_OK.get(data_type, set())

    transformer_set = set(transformer_columns)
    db_set = set(db_columns)

    matched_transformer = set()
    matched_db = set()

    for column in transformer_set & db_set:
        matched_transformer.add(column)
        matched_db.add(column)

    for transformer_column, db_column in rename_map.items():
        if transformer_column in transformer_set and db_column in db_set:
            if transformer_column not in matched_transformer and db_column not in matched_db:
                gaps.append(
                    SchemaGap(
                        reader_name=reader_name,
                        data_type=data_type,
                        transformer_column=transformer_column,
                        db_column=db_column,
                        gap_type="rename_needed",
                        notes=f"Transformer produces '{transformer_column}', DB expects '{db_column}'",
                    )
                )
                matched_transformer.add(transformer_column)
                matched_db.add(db_column)

    for column in transformer_set - matched_transformer:
        if column in extra_ok:
            gaps.append(
                SchemaGap(
                    reader_name=reader_name,
                    data_type=data_type,
                    transformer_column=column,
                    db_column="",
                    gap_type="extra_in_transformer",
                    notes=f"Column '{column}' is not persisted in DB schema.",
                )
            )

    for column in db_set - matched_db:
        gaps.append(
            SchemaGap(
                reader_name=reader_name,
                data_type=data_type,
                transformer_column="",
                db_column=column,
                gap_type="missing_in_transformer",
                notes=f"DB column '{column}' is missing from transformer output.",
            )
        )

    return gaps


def annotate_known_conflicts(conflicts: list[IDConflict]) -> list[IDConflict]:
    """Apply resolution hints for known canonical ID conflicts."""
    known_lookup = {}
    for conflict in KNOWN_ID_CONFLICTS:
        known_lookup[(conflict["reader"], conflict["reader_id"])] = conflict

    annotated = []
    for conflict in conflicts:
        key = (conflict.reader_name, conflict.reader_id)
        if key in known_lookup:
            known = known_lookup[key]
            conflict.resolution = known["resolution"]
            conflict.notes = known["reason"]
        annotated.append(conflict)
    return annotated


def compare_gold_holdings(
    reader_df: pd.DataFrame,
    db_df: pd.DataFrame,
    tolerance_pct: float = 1.0,
) -> AssetComparison:
    """Compare per-account reader gold rows against combined DB gold position."""
    reader_total_qty = reader_df["quantity"].sum() if not reader_df.empty else 0.0
    reader_total_value = reader_df["market_value"].sum() if not reader_df.empty else 0.0

    db_qty = 0.0
    db_value = 0.0
    db_id = None
    if not db_df.empty:
        gold_rows = db_df[db_df["asset_id"].str.contains("Gold|gold|GOLD", na=False)]
        if not gold_rows.empty:
            db_qty = float(gold_rows["quantity"].iloc[0])
            db_value = float(gold_rows["market_value"].iloc[0])
            db_id = gold_rows["asset_id"].iloc[0]

    qty_diff = abs(reader_total_qty - db_qty)
    qty_pct_diff = (qty_diff / db_qty * 100) if db_qty > 0 else 0.0
    reader_accounts = ", ".join(reader_df["asset_id"].tolist()) if not reader_df.empty else "none"

    status = "match" if qty_pct_diff < tolerance_pct else "value_mismatch"
    return AssetComparison(
        asset_id="Gold (combined)",
        reader_id=reader_accounts,
        db_id=db_id,
        reader_quantity=reader_total_qty,
        db_quantity=db_qty,
        reader_value=reader_total_value,
        db_value=db_value,
        status=status,
        notes=(
            f"Reader has per-account breakdown ({reader_accounts}), DB has combined position. "
            f"Qty diff: {qty_diff:.4f}g ({qty_pct_diff:.2f}%)"
        ),
    )
