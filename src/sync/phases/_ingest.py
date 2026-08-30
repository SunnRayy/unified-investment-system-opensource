import json
import logging
import re
from datetime import date, datetime
from typing import Any, Dict, Optional

import pandas as pd

from src.database.connector import DatabaseConnector
from src.sync.phases._common import (
    HOLDINGS_INSERT_COLUMNS,
    TRANSACTIONS_INSERT_COLUMNS,
    _to_date, _to_decimal, _to_text, _db_param, _default_account, _default_currency, _infer_asset_type, _coerce_json_value,
)

logger = logging.getLogger(__name__)

# Sources whose sheet is the COMPLETE maintained transaction log → it is safe to
# delete ALL existing rows for the source and re-insert (self-cleaning on corrections).
# Date-range sources (Schwab_CSV, Broker_IBKR) must NOT be added here — they are
# incremental downloads that do not carry full history, so full-replace would lose data.
_FULL_REPLACE_SOURCES = frozenset({"RSU_Excel"})


def _normalize_holdings_df(
    holdings_df: pd.DataFrame,
    source_system: str,
) -> pd.DataFrame:
    if not isinstance(holdings_df, pd.DataFrame) or holdings_df.empty:
        return pd.DataFrame(columns=HOLDINGS_INSERT_COLUMNS)

    df = holdings_df.copy()

    if source_system == "Schwab_CSV":
        if "asset_id" in df.columns:
            df["asset_id"] = (
                df["asset_id"].astype(str).str.replace("US_ETF_", "US_STK_", regex=False)
            )
        if "cost_basis" in df.columns and "cost_price_unit" not in df.columns:
            df = df.rename(columns={"cost_basis": "cost_price_unit"})
    if source_system == "Gold_Excel" and "cost_price" in df.columns:
        df = df.rename(columns={"cost_price": "cost_price_unit"})
    if source_system == "Insurance_Excel":
        if "asset_name" not in df.columns and "product_name" in df.columns:
            df["asset_name"] = df["product_name"]
        if "asset_type" not in df.columns and "product_type" in df.columns:
            df["asset_type"] = df["product_type"]
        if "quantity" not in df.columns:
            df["quantity"] = 0.0
        if "unit" not in df.columns:
            df["unit"] = "policy"

    for col in ("gain_dollar", "gain_percent"):
        if col in df.columns:
            df = df.drop(columns=[col])

    if "asset_name" not in df.columns:
        df["asset_name"] = df.get("asset_id")
    if "asset_type" not in df.columns:
        df["asset_type"] = None
    if "quantity" not in df.columns:
        df["quantity"] = None
    if "unit" not in df.columns:
        df["unit"] = None
    if "cost_price_unit" not in df.columns:
        df["cost_price_unit"] = None
    if "market_price_unit" not in df.columns:
        df["market_price_unit"] = None
    if "market_value" not in df.columns:
        df["market_value"] = None
    if "snapshot_date" not in df.columns:
        logger.warning(
            "snapshot_date missing from %s holdings DataFrame — "
            "falling back to date.today(). "
            "After Phase 1-3 this should not happen for main sources.",
            source_system,
        )
        df["snapshot_date"] = date.today()

    df["source_system"] = source_system
    df["currency"] = _default_currency(source_system)
    if "account" not in df.columns:
        df["account"] = _default_account(source_system)

    rows = []
    for _, row in df.iterrows():
        asset_id = _to_text(row.get("asset_id"))
        if not asset_id:
            continue
        _raw_snap = row.get("snapshot_date")
        snapshot_date = _to_date(_raw_snap)
        if snapshot_date is None:
            logger.warning(
                "Per-row snapshot_date fallback: source=%s asset_id=%s raw_value=%r — using date.today()",
                source_system, row.get("asset_id"), _raw_snap,
            )
            snapshot_date = date.today()
        asset_name = _to_text(row.get("asset_name")) or asset_id
        asset_type = _to_text(row.get("asset_type")) or _infer_asset_type(asset_id, source_system)
        account = _to_text(row.get("account")) or _default_account(source_system)
        currency = _to_text(row.get("currency")) or _default_currency(source_system)
        unit = _to_text(row.get("unit"))
        if unit is None:
            if source_system == "Insurance_Excel":
                unit = "policy"
            elif source_system in {"Schwab_CSV", "CN_Fund_Excel", "RSU_Excel"}:
                unit = "share"

        rows.append(
            {
                "snapshot_date": snapshot_date,
                "asset_id": asset_id,
                "asset_name": asset_name,
                "asset_type": asset_type,
                "quantity": _to_decimal(row.get("quantity"), 8),
                "unit": unit,
                "cost_price_unit": _to_decimal(row.get("cost_price_unit"), 8),
                "market_price_unit": _to_decimal(row.get("market_price_unit"), 8),
                "market_value": _to_decimal(row.get("market_value"), 2),
                "currency": currency,
                "account": account,
                "source_system": source_system,
            }
        )

    return pd.DataFrame(rows, columns=HOLDINGS_INSERT_COLUMNS)


def _aggregate_gold_holdings(holdings_df: pd.DataFrame) -> tuple[pd.DataFrame, Dict[str, Any]]:
    if holdings_df.empty or "asset_id" not in holdings_df.columns:
        return holdings_df, {}

    df = holdings_df.copy()
    mask = df["asset_id"].astype(str).str.startswith("GOLD_PAPER_")
    if not mask.any():
        return df, {}

    paper_df = df[mask].copy()
    other_df = df[~mask].copy()

    total_quantity = 0.0
    total_market_value = 0.0
    cost_numerator = 0.0
    cost_has_data = False
    breakdown: Dict[str, Any] = {}

    for _, row in paper_df.iterrows():
        quantity = _to_decimal(row.get("quantity"), 8) or 0.0
        market_value = _to_decimal(row.get("market_value"), 2) or 0.0
        cost_price = _to_decimal(row.get("cost_price_unit"), 8)
        account = _to_text(row.get("account")) or "Unknown"
        asset_id = _to_text(row.get("asset_id")) or "GOLD_PAPER_UNKNOWN"

        breakdown[account] = {
            "asset_id": asset_id,
            "quantity": quantity,
            "market_value": market_value,
        }
        total_quantity += quantity
        total_market_value += market_value
        if cost_price is not None:
            cost_numerator += quantity * cost_price
            cost_has_data = True

    combined_cost = round(cost_numerator / total_quantity, 8) if cost_has_data and total_quantity else None
    combined_market_price = round(total_market_value / total_quantity, 8) if total_quantity else None
    accounts = sorted(breakdown.keys())
    unit = _to_text(paper_df.iloc[0].get("unit")) if len(paper_df) else None
    snapshot_date = _to_date(paper_df.iloc[0].get("snapshot_date")) if len(paper_df) else None

    combined_row = {
        "snapshot_date": snapshot_date or date.today(),
        "asset_id": "ALTS_Paper_Gold",
        "asset_name": "Paper Gold",
        "asset_type": "Alternative",
        "quantity": round(total_quantity, 8),
        "unit": unit,
        "cost_price_unit": combined_cost,
        "market_price_unit": combined_market_price,
        "market_value": round(total_market_value, 2),
        "currency": "CNY",
        "account": "+".join(accounts) if accounts else "Gold",
        "source_system": "Gold_Excel",
    }

    combined_df = pd.concat(
        [other_df, pd.DataFrame([combined_row])],
        ignore_index=True,
        sort=False,
    )
    return combined_df, breakdown


def _normalize_transactions_df(
    tx_df: pd.DataFrame,
    source_system: str,
) -> pd.DataFrame:
    if not isinstance(tx_df, pd.DataFrame) or tx_df.empty:
        return pd.DataFrame(columns=TRANSACTIONS_INSERT_COLUMNS)

    df = tx_df.copy()
    rename_map: Dict[str, str] = {}
    if source_system == "Schwab_CSV":
        if "asset_id" in df.columns:
            df["asset_id"] = (
                df["asset_id"].astype(str).str.replace("US_ETF_", "US_STK_", regex=False)
            )
        rename_map = {
            "price": "price_unit",
            "amount": "amount_gross",
            "fees": "commission_fee",
            "description": "memo",
        }
    elif source_system == "CN_Fund_Excel":
        rename_map = {
            "price": "price_unit",
            "amount": "amount_gross",
            "fees": "commission_fee",
        }
    elif source_system == "Gold_Excel":
        # Rename GOLD_PAPER_* → ALTS_Paper_Gold BEFORE _replace_transactions so the
        # DELETE can find existing rows (post-sync cleanup already renamed them on prior
        # syncs). Without this, DELETE finds 0 rows and each sync accumulates duplicates.
        if "asset_id" in df.columns:
            df["asset_id"] = df["asset_id"].astype(str).str.replace(
                r"^GOLD_PAPER_.*$", "ALTS_Paper_Gold", regex=True
            )
        rename_map = {
            "price": "price_unit",
            "amount": "amount_gross",
            "fees": "commission_fee",
        }
    elif source_system == "Insurance_Excel":
        rename_map = {
            "payment_date": "transaction_date",
            "amount": "amount_gross",
        }
    elif source_system == "RSU_Excel":
        rename_map = {
            "price_usd": "price_unit",
            "amount_usd": "amount_gross",
            "fees_usd": "commission_fee",
        }
    df = df.rename(columns=rename_map)

    if "asset_name" not in df.columns:
        if source_system == "Insurance_Excel" and "policy_name" in df.columns:
            df["asset_name"] = df["policy_name"]
        else:
            df["asset_name"] = df.get("asset_id")
    if "transaction_date" not in df.columns:
        df["transaction_date"] = None
    if "transaction_type" not in df.columns:
        df["transaction_type"] = "other"
    if "quantity" not in df.columns:
        df["quantity"] = 0.0
    if "price_unit" not in df.columns:
        df["price_unit"] = None
    if "amount_gross" not in df.columns:
        df["amount_gross"] = None
    if "commission_fee" not in df.columns:
        df["commission_fee"] = 0.0
    if "memo" not in df.columns:
        df["memo"] = None
    if "account" not in df.columns:
        df["account"] = _default_account(source_system)

    df["source_system"] = source_system
    df["currency"] = _default_currency(source_system)

    rows = []
    for _, row in df.iterrows():
        asset_id = _to_text(row.get("asset_id"))
        if not asset_id:
            continue
        transaction_date = _to_date(row.get("transaction_date"))
        if transaction_date is None:
            continue
        transaction_type = _to_text(row.get("transaction_type")) or "other"
        commission_fee = _to_decimal(row.get("commission_fee"), 4) or 0.0
        amount_gross = _to_decimal(row.get("amount_gross"), 2)
        amount_for_net = amount_gross or 0.0
        amount_net = round(amount_for_net - commission_fee, 2)
        currency = _to_text(row.get("currency")) or _default_currency(source_system)
        account = _to_text(row.get("account")) or _default_account(source_system)
        asset_name = _to_text(row.get("asset_name")) or asset_id

        rows.append(
            {
                "transaction_date": transaction_date,
                "asset_id": asset_id,
                "asset_name": asset_name,
                "transaction_type": transaction_type,
                "quantity": _to_decimal(row.get("quantity"), 8),
                "price_unit": _to_decimal(row.get("price_unit"), 8),
                "amount_gross": amount_gross,
                "amount_net": amount_net,
                "commission_fee": commission_fee,
                "currency": currency,
                "account": account,
                "memo": _to_text(row.get("memo")),
                "source_system": source_system,
            }
        )

    out_df = pd.DataFrame(rows, columns=TRANSACTIONS_INSERT_COLUMNS)
    if out_df.empty:
        return out_df

    if source_system == "RSU_Excel":
        dedup_cols = [
            "transaction_date",
            "asset_id",
            "transaction_type",
            "quantity",
            "memo",
            "source_system",
        ]
    elif source_system == "Gold_Excel":
        # Gold transactions from different bank accounts on the same day with the
        # same amount are distinct purchases — include account to prevent collapse.
        dedup_cols = [
            "transaction_date",
            "asset_id",
            "transaction_type",
            "amount_gross",
            "account",
            "source_system",
        ]
    else:
        dedup_cols = [
            "transaction_date",
            "asset_id",
            "transaction_type",
            "amount_gross",
            "source_system",
        ]
    return out_df.drop_duplicates(subset=dedup_cols, keep="last")


def _upsert_holdings(connector: DatabaseConnector, holdings_df: pd.DataFrame) -> int:
    if holdings_df.empty:
        return 0

    rows = [
        (
            _db_param(row["snapshot_date"]),
            _db_param(row["asset_id"]),
            _db_param(row["asset_name"]),
            _db_param(row["asset_type"]),
            _db_param(row["quantity"]),
            _db_param(row["unit"]),
            _db_param(row["cost_price_unit"]),
            _db_param(row["market_price_unit"]),
            _db_param(row["market_value"]),
            _db_param(row["currency"]),
            _db_param(row["account"]),
            _db_param(row["source_system"]),
            "file",  # price_source: file-based reader is always 'file' at insert time
        )
        for _, row in holdings_df.iterrows()
    ]
    connector.executemany(
        """
        INSERT INTO holdings (
            snapshot_date, asset_id, asset_name, asset_type,
            quantity, unit, cost_price_unit, market_price_unit,
            market_value, currency, account, source_system, price_source
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT (snapshot_date, asset_id, source_system) DO UPDATE SET
            asset_name = EXCLUDED.asset_name,
            asset_type = EXCLUDED.asset_type,
            quantity = EXCLUDED.quantity,
            unit = EXCLUDED.unit,
            cost_price_unit = EXCLUDED.cost_price_unit,
            market_price_unit = EXCLUDED.market_price_unit,
            market_value = EXCLUDED.market_value,
            currency = EXCLUDED.currency,
            account = EXCLUDED.account,
            price_source = 'file',
            -- Shadow state is re-derived every sync by P4 shadow phases and P5 authority phase.
            -- Re-ingesting a current-snapshot reader row must return it to active; otherwise a
            -- row shadowed by a prior same-day consolidation run stays shadowed, the asset
            -- disappears on the next sync, and the idempotency invariant breaks.
            -- Invariant: ingest produces active current-snapshot rows; all shadowing is derived downstream.
            is_shadow = FALSE
        """,
        rows,
    )
    return len(rows)


def _replace_transactions(connector: DatabaseConnector, tx_df: pd.DataFrame) -> int:
    if tx_df.empty:
        return 0

    def _trade_logs_has_verification_status() -> bool:
        columns = connector.execute("PRAGMA table_info('trade_logs')").fetchall()
        return any(str(col[1]).lower() == "verification_status" for col in columns)

    def _find_matched_transaction_ids(query: str, rows: list[tuple]) -> set[int]:
        matched_ids: set[int] = set()
        for params in rows:
            results = connector.execute(query, params).fetchall()
            matched_ids.update(int(row[0]) for row in results if row and row[0] is not None)
        return matched_ids

    def _reset_trade_log_links(tx_ids: set[int]) -> None:
        if not tx_ids:
            return
        placeholders = ", ".join("?" for _ in tx_ids)
        params = [int(tx_id) for tx_id in sorted(tx_ids)]
        if _trade_logs_has_verification_status():
            connector.execute(
                f"""
                UPDATE trade_logs
                SET linked_transaction_id = NULL,
                    verification_status = 'pending'
                WHERE linked_transaction_id IN ({placeholders})
                """,
                params,
            )
        else:
            connector.execute(
                f"""
                UPDATE trade_logs
                SET linked_transaction_id = NULL
                WHERE linked_transaction_id IN ({placeholders})
                """,
                params,
            )

    full_replace_rows = tx_df[tx_df["source_system"].isin(_FULL_REPLACE_SOURCES)]
    other_rows = tx_df[~tx_df["source_system"].isin(_FULL_REPLACE_SOURCES)]

    # Self-heal (V7.1.7): CN Fund 卖基金 / 超级转换份额调减 transactions were
    # imported as transaction_type='other' before those labels were added to the
    # type maps. The corrected reader now yields 'sell' / 'transfer_out', but the
    # incremental delete below keys on transaction_type, so it cannot supersede a
    # stale 'other' twin → a duplicate would be inserted. Purge the stale rows
    # first, identified by the original Chinese label preserved in memo. Runs
    # before re-insert; idempotent (a no-op once no such rows remain).
    if "CN_Fund_Excel" in set(other_rows["source_system"].unique()):
        stale_ids = _find_matched_transaction_ids(
            """
            SELECT id FROM transactions
            WHERE source_system = 'CN_Fund_Excel'
              AND transaction_type = 'other'
              AND memo IN ('卖基金', '买基金', '超级转换份额调减')
            """,
            [()],
        )
        if stale_ids:
            _reset_trade_log_links(stale_ids)
            placeholders = ", ".join("?" for _ in stale_ids)
            connector.execute(
                f"DELETE FROM transactions WHERE id IN ({placeholders})",
                [int(i) for i in sorted(stale_ids)],
            )

    # Self-heal (WS-B3, 2026-08-01): IBKR trades imported before the reader hook
    # emitted the ingest-contract column names landed with amount_gross IS NULL
    # (the hook said 'amount'/'price'/'fees', _normalize_transactions_df had no
    # Broker_IBKR rename branch, so the money columns were dropped silently).
    # The incremental delete below keys on COALESCE(amount_gross, 0), so a stale
    # NULL row cannot be superseded by the corrected non-zero row → a duplicate
    # would be inserted, double-counting FIFO cost basis. Purge the stale rows
    # first. Scoped to buy/sell: ACATS transfer rows are legitimately 0.0 and DO
    # heal in place (NULL and 0.0 both COALESCE to 0). A post-fix trade can never
    # be NULL here — the hook always computes a numeric amount — so this cannot
    # match a good row. Idempotent (a no-op once no such rows remain).
    if "Broker_IBKR" in set(other_rows["source_system"].unique()):
        stale_ids = _find_matched_transaction_ids(
            """
            SELECT id FROM transactions
            WHERE source_system = 'Broker_IBKR'
              AND transaction_type IN ('buy', 'sell')
              AND amount_gross IS NULL
            """,
            [()],
        )
        if stale_ids:
            _reset_trade_log_links(stale_ids)
            placeholders = ", ".join("?" for _ in stale_ids)
            connector.execute(
                f"DELETE FROM transactions WHERE id IN ({placeholders})",
                [int(i) for i in sorted(stale_ids)],
            )

    if not other_rows.empty:
        delete_rows = [
            (
                _db_param(row["transaction_date"]),
                _db_param(row["asset_id"]),
                _db_param(row["transaction_type"]),
                _db_param(row["amount_gross"]),
                _db_param(row["source_system"]),
            )
            for _, row in other_rows.iterrows()
        ]
        _reset_trade_log_links(
            _find_matched_transaction_ids(
                """
                SELECT id
                FROM transactions
                WHERE transaction_date = ?
                  AND asset_id = ?
                  AND transaction_type = ?
                  AND COALESCE(amount_gross, 0) = COALESCE(?, 0)
                  AND source_system = ?
                """,
                delete_rows,
            )
        )
        connector.executemany(
            """
            DELETE FROM transactions
            WHERE transaction_date = ?
              AND asset_id = ?
              AND transaction_type = ?
              AND COALESCE(amount_gross, 0) = COALESCE(?, 0)
              AND source_system = ?
            """,
            delete_rows,
        )

    # Full-replace sources: the sheet is the complete maintained log, so delete ALL
    # existing rows for the source and re-insert.  This is self-cleaning: any orphan
    # left by a prior value-correction (e.g. sell +5.85 → sell -5.85) is purged here
    # because the DELETE matches by source_system, not by the corrected field values.
    for source in full_replace_rows["source_system"].unique():
        existing_ids = _find_matched_transaction_ids(
            "SELECT id FROM transactions WHERE source_system = ?",
            [(source,)],
        )
        _reset_trade_log_links(existing_ids)
        connector.execute(
            "DELETE FROM transactions WHERE source_system = ?",
            (source,),
        )

    insert_rows = [
        (
            _db_param(row["transaction_date"]),
            _db_param(row["asset_id"]),
            _db_param(row["asset_name"]),
            _db_param(row["transaction_type"]),
            _db_param(row["quantity"]),
            _db_param(row["price_unit"]),
            _db_param(row["amount_gross"]),
            _db_param(row["amount_net"]),
            _db_param(row["commission_fee"]),
            _db_param(row["currency"]),
            _db_param(row["account"]),
            _db_param(row["memo"]),
            _db_param(row["source_system"]),
        )
        for _, row in tx_df.iterrows()
    ]
    connector.executemany(
        """
        INSERT INTO transactions (
            transaction_date, asset_id, asset_name, transaction_type,
            quantity, price_unit, amount_gross, amount_net, commission_fee,
            currency, account, memo, source_system
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        insert_rows,
    )
    return len(insert_rows)


def _ensure_financial_summary_tables(connector: DatabaseConnector) -> None:
    connector.execute("CREATE SEQUENCE IF NOT EXISTS seq_balance_sheet_monthly_id START 1")
    connector.execute("CREATE SEQUENCE IF NOT EXISTS seq_income_expense_monthly_id START 1")
    connector.execute(
        """
        CREATE TABLE IF NOT EXISTS balance_sheet_monthly (
            id INTEGER PRIMARY KEY DEFAULT nextval('seq_balance_sheet_monthly_id'),
            record_key VARCHAR(120) NOT NULL,
            snapshot_date DATE,
            payload JSON NOT NULL,
            source_system VARCHAR(50) NOT NULL DEFAULT 'Financial_Summary_Excel',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(record_key, source_system)
        )
        """
    )
    connector.execute(
        """
        CREATE TABLE IF NOT EXISTS income_expense_monthly (
            id INTEGER PRIMARY KEY DEFAULT nextval('seq_income_expense_monthly_id'),
            record_key VARCHAR(120) NOT NULL,
            transaction_date DATE,
            payload JSON NOT NULL,
            source_system VARCHAR(50) NOT NULL DEFAULT 'Financial_Summary_Excel',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(record_key, source_system)
        )
        """
    )


def _extract_date_from_record(record: Dict[str, Any], record_key: str) -> Optional[date]:
    candidate_columns = [
        "snapshot_date",
        "transaction_date",
        "Date",
        "date",
        "Month",
        "month",
        "日期",
        "月份",
    ]
    for col in candidate_columns:
        parsed = _to_date(record.get(col))
        if parsed:
            return parsed

    match = re.search(r"(\d{8})", record_key)
    if not match:
        return None
    try:
        return datetime.strptime(match.group(1), "%Y%m%d").date()
    except ValueError:
        return None


def _persist_financial_summary(
    connector: DatabaseConnector,
    fs_result: Dict[str, pd.DataFrame],
) -> tuple[int, int]:
    _ensure_financial_summary_tables(connector)
    bs_df = fs_result.get("balance_sheet")
    if not isinstance(bs_df, pd.DataFrame):
        bs_df = fs_result.get("holdings", pd.DataFrame())
    ie_df = fs_result.get("income_expense")
    if not isinstance(ie_df, pd.DataFrame):
        ie_df = fs_result.get("transactions", pd.DataFrame())

    bs_rows = []
    if isinstance(bs_df, pd.DataFrame) and not bs_df.empty:
        for idx, row in bs_df.iterrows():
            record = {k: _coerce_json_value(v) for k, v in row.to_dict().items()}
            record_key = _to_text(record.get("asset_id")) or f"BS_ROW_{idx + 1}"
            snapshot_date = _extract_date_from_record(record, record_key)
            bs_rows.append(
                (
                    record_key,
                    snapshot_date,
                    json.dumps(record, ensure_ascii=False, default=str),
                    "Financial_Summary_Excel",
                )
            )
    if bs_rows:
        connector.executemany(
            """
            INSERT INTO balance_sheet_monthly (
                record_key, snapshot_date, payload, source_system
            ) VALUES (?, ?, ?, ?)
            ON CONFLICT (record_key, source_system) DO UPDATE SET
                snapshot_date = EXCLUDED.snapshot_date,
                payload = EXCLUDED.payload
            """,
            bs_rows,
        )

    ie_rows = []
    if isinstance(ie_df, pd.DataFrame) and not ie_df.empty:
        for idx, row in ie_df.iterrows():
            record = {k: _coerce_json_value(v) for k, v in row.to_dict().items()}
            record_key = _to_text(record.get("asset_id")) or f"IE_ROW_{idx + 1}"
            transaction_date = _extract_date_from_record(record, record_key)
            ie_rows.append(
                (
                    record_key,
                    transaction_date,
                    json.dumps(record, ensure_ascii=False, default=str),
                    "Financial_Summary_Excel",
                )
            )
    if ie_rows:
        connector.executemany(
            """
            INSERT INTO income_expense_monthly (
                record_key, transaction_date, payload, source_system
            ) VALUES (?, ?, ?, ?)
            ON CONFLICT (record_key, source_system) DO UPDATE SET
                transaction_date = EXCLUDED.transaction_date,
                payload = EXCLUDED.payload
            """,
            ie_rows,
        )

    return len(bs_rows), len(ie_rows)
