"""Wizard (generic tabular onboarding) hooks (Program OSR WS-2 mechanical split).

Extracted verbatim from src/sources/reader_hooks.py (pre-split, 1,578 lines) —
see src/sources/hooks/__init__.py for the aggregation and
src/sources/reader_hooks.py for the backward-compatible re-export shim.

IMPORT CONSTRAINT (mirrors src.sources.registry — unchanged from the
pre-split module): stdlib + pandas only at module level. Lazy imports inside
a function body are allowed.
"""
from __future__ import annotations

import pandas as pd

# ---------------------------------------------------------------------------
# Wizard hooks (import-adapter convergence — A1)
# Generic tabular source ingestion mirroring import_adapters.service.stage_import_run
# ---------------------------------------------------------------------------

# Fields treated as numeric amounts (mirrors import_adapters/service.py)
_WIZARD_NUMERIC_FIELDS = frozenset({
    "market_value", "quantity", "market_price_unit", "price_unit",
    "amount_gross", "commission_fee", "cost_price_unit",
})

# Fields treated as dates
_WIZARD_DATE_FIELDS = frozenset({
    "snapshot_date", "transaction_date",
})


def wizard_holdings_from_sheet(
    sheet_df: pd.DataFrame,
    metadata: dict,
) -> pd.DataFrame:
    """Generic wizard-onboarded holdings: apply stored column_mapping + amount/date
    cleaning + FX, mirroring import_adapters.service.stage_import_run.

    Reads from metadata:
        wizard_column_mapping  : Dict[str, str]  — {dst_field: src_column}
        wizard_fx_rate         : Optional[float] — multiply non-CNY market_value
        wizard_import_type     : str             — should be "holdings" (informational)

    Behaviour:
      - Iterates column_mapping; applies clean_amount for numeric dst fields,
        parse_date for date dst fields, raw value otherwise.
      - Injects today's snapshot_date when "snapshot_date" is not in column_mapping.
      - FX: when fx_rate is set and the row's currency is non-CNY (and non-empty),
        multiplies market_value (and market_price_unit if present) by fx_rate.

    Hook signature: (sheet_df, metadata) -> pd.DataFrame.
    Lazy-imports src.import_adapters.file_reader (import constraint — no module-level
    src.* imports allowed in reader_hooks.py).
    """
    # lazy imports (module import constraint)
    from src.import_adapters.file_reader import clean_amount, parse_date  # noqa: PLC0415
    import pandas as _pd  # already imported at module level; alias for clarity
    from datetime import date as _date  # noqa: PLC0415

    mapping = metadata.get("wizard_column_mapping", {})   # {dst: src}
    fx_rate = metadata.get("wizard_fx_rate")

    if sheet_df is None or sheet_df.empty or not mapping:
        return _pd.DataFrame()

    rows = []
    for _, raw in sheet_df.iterrows():
        payload: dict = {}
        for dst, src in mapping.items():
            val = raw.get(src)
            if dst in _WIZARD_NUMERIC_FIELDS:
                payload[dst] = clean_amount(val)
            elif dst in _WIZARD_DATE_FIELDS:
                pd_ = parse_date(val)
                payload[dst] = str(pd_) if pd_ is not None else val
            else:
                payload[dst] = val

        # Inject today's snapshot_date for holdings when not mapped
        if "snapshot_date" not in mapping:
            payload["snapshot_date"] = str(_date.today())

        # FX: convert market_value (and market_price_unit) to CNY when applicable
        cur = str(payload.get("currency") or "").upper().strip()
        if fx_rate is not None and cur and cur != "CNY":
            mv = payload.get("market_value")
            if mv is not None:
                payload["market_value"] = mv * float(fx_rate)
            mpu = payload.get("market_price_unit")
            if mpu is not None:
                payload["market_price_unit"] = mpu * float(fx_rate)

        rows.append(payload)

    return _pd.DataFrame(rows)


def wizard_transactions_from_sheet(
    sheet_df: pd.DataFrame,
    metadata: dict,
) -> pd.DataFrame:
    """Generic wizard-onboarded transactions: apply stored column_mapping + amount/date
    cleaning + FX, mirroring import_adapters.service.stage_import_run.

    Reads from metadata:
        wizard_column_mapping  : Dict[str, str]  — {dst_field: src_column}
        wizard_fx_rate         : Optional[float] — multiply non-CNY amount_gross
        wizard_import_type     : str             — should be "transactions" (informational)

    Behaviour:
      - Iterates column_mapping; applies clean_amount for numeric dst fields,
        parse_date for date dst fields, raw value otherwise.
      - No snapshot_date injection (transactions use transaction_date as mapped).
      - FX: when fx_rate is set and the row's currency is non-CNY (and non-empty),
        multiplies amount_gross (and commission_fee if present) by fx_rate.

    Hook signature: (sheet_df, metadata) -> pd.DataFrame.
    Lazy-imports src.import_adapters.file_reader (import constraint — no module-level
    src.* imports allowed in reader_hooks.py).
    """
    # lazy imports (module import constraint)
    from src.import_adapters.file_reader import clean_amount, parse_date  # noqa: PLC0415
    import pandas as _pd  # already imported at module level; alias for clarity

    mapping = metadata.get("wizard_column_mapping", {})   # {dst: src}
    fx_rate = metadata.get("wizard_fx_rate")

    if sheet_df is None or sheet_df.empty or not mapping:
        return _pd.DataFrame()

    rows = []
    for _, raw in sheet_df.iterrows():
        payload: dict = {}
        for dst, src in mapping.items():
            val = raw.get(src)
            if dst in _WIZARD_NUMERIC_FIELDS:
                payload[dst] = clean_amount(val)
            elif dst in _WIZARD_DATE_FIELDS:
                pd_ = parse_date(val)
                payload[dst] = str(pd_) if pd_ is not None else val
            else:
                payload[dst] = val

        # FX: convert amount_gross (and commission_fee) to CNY when applicable
        cur = str(payload.get("currency") or "").upper().strip()
        if fx_rate is not None and cur and cur != "CNY":
            ag = payload.get("amount_gross")
            if ag is not None:
                payload["amount_gross"] = ag * float(fx_rate)
            cf = payload.get("commission_fee")
            if cf is not None:
                payload["commission_fee"] = cf * float(fx_rate)

        rows.append(payload)

    return _pd.DataFrame(rows)
