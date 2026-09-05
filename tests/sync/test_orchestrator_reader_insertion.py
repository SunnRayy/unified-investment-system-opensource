"""Tests for reader DataFrame insertion in orchestrator."""

from contextlib import ExitStack
from datetime import date
from unittest.mock import patch

import pandas as pd
import pytest

pytestmark = pytest.mark.pipeline


from src.database.connector import DatabaseConnector
from src.database.schema import initialize_schema
from src.sync.orchestrator import _replace_transactions, run_full_sync_v3
from src.sync.trade_linker import link_trade_logs_to_transactions


@pytest.fixture
def connector():
    conn = DatabaseConnector(":memory:")
    initialize_schema(conn)
    yield conn
    conn.close()


@pytest.fixture
def base_config():
    return {
        "sources": {"pis": {}},
        "validation": {
            "freshness": {"enabled": False},
            "taxonomy": {"enabled": False},
            "cost_basis": {"threshold_pct": 1.0},
            "allocations": {"drift_threshold_pct": 5.0},
        },
        "source_registry": {
            "schwab": {"enabled": False},
            "cn_fund": {"enabled": False},
            "gold": {"enabled": False},
            "insurance": {"enabled": False},
            "rsu": {"enabled": False},
            "financial_summary": {"enabled": False},
        },
    }


def _patch_baseline():
    """Patch all orchestrator dependencies that are not under test.

    Phase 9 note: sync_pis_transactions, sync_holdings_with_cost_basis,
    sync_target_allocations, and sync_tier_assignments are no longer imported in the
    orchestrator (removed Phase 9, superseded by 6 source readers) and must NOT be patched.
    """
    _mock_refresh_result = {
        "refreshed": 0, "skipped": 0, "errors": 0, "holdings_updated": 0,
        "fx_rates": {}, "refreshed_assets": [], "skipped_assets": [], "error_assets": [],
    }
    stack = ExitStack()
    stack.enter_context(patch("src.sync.orchestrator.create_backup", return_value="/tmp/mock.duckdb"))
    stack.enter_context(patch("src.sync.orchestrator.create_classification_tables"))
    stack.enter_context(patch("src.sync.orchestrator.sync_asset_registry", return_value={"registry_inserted": 0}))
    stack.enter_context(patch("src.sync.orchestrator.sync_current_allocations", return_value={"synced": 0}))
    stack.enter_context(patch("src.sync.orchestrator.validate_cost_basis", return_value=[]))
    stack.enter_context(patch("src.sync.orchestrator.validate_allocations", return_value=[]))
    # Mock supplemental live price refresh so tests are not affected by network calls
    stack.enter_context(patch(
        "src.market_data.service.MarketDataService.refresh_portfolio_prices",
        return_value=_mock_refresh_result,
    ))
    return stack


def test_orchestrator_inserts_all_reader_outputs(connector, base_config):
    config = base_config.copy()
    config["source_registry"] = {
        "schwab": {"enabled": True},
        "cn_fund": {"enabled": True},
        "gold": {"enabled": True},
        "insurance": {"enabled": True},
        "rsu": {"enabled": True},
        "financial_summary": {"enabled": True},
    }

    schwab_result = {
        "holdings": pd.DataFrame(
            [
                {
                    "asset_id": "US_ETF_IBIT",
                    "quantity": 2.0,
                    "market_price_unit": 50.0,
                    "market_value": 100.0,
                    "cost_basis": 40.0,
                    "snapshot_date": "2026-02-10",
                    "source_system": "Schwab_CSV",
                }
            ]
        ),
        "transactions": pd.DataFrame(
            [
                {
                    "asset_id": "US_ETF_IBIT",
                    "transaction_date": "2026-02-09",
                    "transaction_type": "buy",
                    "quantity": 2.0,
                    "price": 50.0,
                    "amount": 100.0,
                    "fees": 1.0,
                    "description": "test buy",
                    "source_system": "Schwab_CSV",
                }
            ]
        ),
    }

    cn_fund_result = {
        "holdings": pd.DataFrame(
            [
                {
                    "asset_id": "CN_FUND_000001",
                    "asset_name": "基金A",
                    "asset_type": "fund",
                    "quantity": 10.0,
                    "market_price_unit": 2.5,
                    "market_value": 25.0,
                    "snapshot_date": "2026-02-10",
                    "source_system": "CN_Fund_Excel",
                }
            ]
        ),
        "transactions": pd.DataFrame(
            [
                {
                    "asset_id": "CN_FUND_000001",
                    "transaction_date": "2026-02-08",
                    "transaction_type": "buy",
                    "quantity": 10.0,
                    "price": 2.0,
                    "amount": 20.0,
                    "fees": 0.2,
                    "memo": "fund buy",
                    "source_system": "CN_Fund_Excel",
                }
            ]
        ),
    }

    gold_result = {
        "holdings": pd.DataFrame(
            [
                {
                    "asset_id": "GOLD_PAPER_CMB",
                    "asset_name": "纸黄金",
                    "quantity": 10.0,
                    "unit": "克",
                    "cost_price": 420.0,
                    "market_price_unit": 500.0,
                    "market_value": 5000.0,
                    "account": "招行",
                    "source_system": "Gold_Excel",
                },
                {
                    "asset_id": "GOLD_PAPER_ICBC",
                    "asset_name": "纸黄金",
                    "quantity": 15.0,
                    "unit": "克",
                    "cost_price": 400.0,
                    "market_price_unit": 500.0,
                    "market_value": 7500.0,
                    "account": "工行",
                    "source_system": "Gold_Excel",
                },
            ]
        ),
        "transactions": pd.DataFrame(
            [
                {
                    "asset_id": "GOLD_PAPER_CMB",
                    "transaction_date": "2026-02-05",
                    "transaction_type": "buy",
                    "quantity": 1.0,
                    "price": 500.0,
                    "amount": 500.0,
                    "fees": 0.0,
                    "account": "招行",
                    "source_system": "Gold_Excel",
                }
            ]
        ),
    }

    insurance_result = {
        "holdings": pd.DataFrame(
            [
                {
                    "asset_id": "INS_安泰人生",
                    "product_name": "安泰人生",
                    "insurer": "平安",
                    "source_system": "Insurance_Excel",
                }
            ]
        ),
        "transactions": pd.DataFrame(
            [
                {
                    "asset_id": "INS_安泰人生",
                    "payment_date": "2026-01-01",
                    "transaction_type": "premium_payment",
                    "amount": 1000.0,
                    "source_system": "Insurance_Excel",
                }
            ]
        ),
    }

    rsu_result = {
        "holdings": pd.DataFrame(),
        "transactions": pd.DataFrame(
            [
                {
                    "asset_id": "RSU_AMZN",
                    "transaction_date": "2026-01-15",
                    "transaction_type": "vest",
                    "quantity": 5.0,
                    "price_usd": 200.0,
                    "amount_usd": 1000.0,
                    "fees_usd": 15.0,
                    "memo": "vesting",
                    "source_system": "RSU_Excel",
                }
            ]
        ),
    }

    fs_balance_sheet = pd.DataFrame(
        [{"asset_id": "BS_20260101", "Date": "2026-01-01", "Total Assets": 100000.0}]
    )
    fs_income_expense = pd.DataFrame(
        [{"asset_id": "IE_20260101", "Date": "2026-01-01", "Income": 12000.0}]
    )
    fs_result = {
        "holdings": fs_balance_sheet.copy(),
        "transactions": fs_income_expense.copy(),
        "balance_sheet": fs_balance_sheet,
        "income_expense": fs_income_expense,
    }

    with _patch_baseline() as stack:
        stack.enter_context(patch("src.sync.orchestrator.sync_schwab", return_value=schwab_result))
        stack.enter_context(patch("src.sync.orchestrator.sync_cn_fund", return_value=cn_fund_result))
        stack.enter_context(patch("src.sync.orchestrator.sync_gold", return_value=gold_result))
        stack.enter_context(patch("src.sync.orchestrator.sync_insurance", return_value=insurance_result))
        stack.enter_context(patch("src.sync.orchestrator.sync_rsu", return_value=rsu_result))
        stack.enter_context(
            patch("src.sync.orchestrator.sync_financial_summary", return_value=fs_result)
        )
        run_full_sync_v3(connector, config)

    schwab_holding = connector.execute(
        """
        SELECT asset_id, cost_price_unit, currency, account
        FROM holdings
        WHERE source_system = 'Schwab_CSV'
        """
    ).fetchone()
    assert schwab_holding[0] == "US_STK_IBIT"
    assert float(schwab_holding[1]) > 0
    assert schwab_holding[2:] == ("USD", "Schwab")

    gold_holding = connector.execute(
        """
        SELECT asset_id, quantity, market_value
        FROM holdings
        WHERE source_system = 'Gold_Excel'
        """
    ).fetchone()
    assert gold_holding == ("ALTS_Paper_Gold", 25.0, 12500.0)

    rsu_tx = connector.execute(
        """
        SELECT price_unit, amount_gross, commission_fee, amount_net, currency
        FROM transactions
        WHERE source_system = 'RSU_Excel'
        """
    ).fetchone()
    assert rsu_tx == (200.0, 1000.0, 15.0, 985.0, "USD")

    bs_count = connector.execute(
        "SELECT COUNT(*) FROM balance_sheet_monthly WHERE source_system = 'Financial_Summary_Excel'"
    ).fetchone()[0]
    ie_count = connector.execute(
        "SELECT COUNT(*) FROM income_expense_monthly WHERE source_system = 'Financial_Summary_Excel'"
    ).fetchone()[0]
    assert bs_count == 1
    assert ie_count == 1


def test_reader_insertions_are_idempotent_for_transactions(connector, base_config):
    config = base_config.copy()
    config["source_registry"]["cn_fund"]["enabled"] = True

    cn_fund_result = {
        "holdings": pd.DataFrame(
            [
                {
                    "asset_id": "CN_FUND_000001",
                    "quantity": 10.0,
                    "market_price_unit": 2.5,
                    "market_value": 25.0,
                    "snapshot_date": "2026-02-10",
                    "source_system": "CN_Fund_Excel",
                }
            ]
        ),
        "transactions": pd.DataFrame(
            [
                {
                    "asset_id": "CN_FUND_000001",
                    "transaction_date": "2026-02-08",
                    "transaction_type": "buy",
                    "quantity": 10.0,
                    "price": 2.0,
                    "amount": 20.0,
                    "fees": 0.0,
                    "source_system": "CN_Fund_Excel",
                }
            ]
        ),
    }

    with _patch_baseline() as stack:
        stack.enter_context(patch("src.sync.orchestrator.sync_cn_fund", return_value=cn_fund_result))
        run_full_sync_v3(connector, config)
        run_full_sync_v3(connector, config)

    holdings_count = connector.execute(
        "SELECT COUNT(*) FROM holdings WHERE source_system = 'CN_Fund_Excel'"
    ).fetchone()[0]
    tx_count = connector.execute(
        "SELECT COUNT(*) FROM transactions WHERE source_system = 'CN_Fund_Excel'"
    ).fetchone()[0]

    assert holdings_count == 1
    assert tx_count == 1


def test_rsu_transaction_update_replaces_existing_row_when_amount_changes(connector, base_config):
    config = base_config.copy()
    config["source_registry"]["rsu"]["enabled"] = True

    first_rsu_result = {
        "holdings": pd.DataFrame(),
        "transactions": pd.DataFrame(
            [
                {
                    "asset_id": "RSU_AMZN",
                    "transaction_date": "2026-03-15",
                    "transaction_type": "vest",
                    "quantity": 192.0,
                    "price_usd": 207.0,
                    "amount_usd": 39744.0,
                    "fees_usd": 0.0,
                    "memo": "Vesting of 100 units",
                    "source_system": "RSU_Excel",
                }
            ]
        ),
    }
    second_rsu_result = {
        "holdings": pd.DataFrame(),
        "transactions": pd.DataFrame(
            [
                {
                    "asset_id": "RSU_AMZN",
                    "transaction_date": "2026-03-15",
                    "transaction_type": "vest",
                    "quantity": 192.0,
                    "price_usd": 209.304,
                    "amount_usd": 40186.368,
                    "fees_usd": 0.0,
                    "memo": "Vesting of 100 units",
                    "source_system": "RSU_Excel",
                }
            ]
        ),
    }

    with _patch_baseline() as stack:
        stack.enter_context(
            patch(
                "src.sync.orchestrator.sync_rsu",
                side_effect=[first_rsu_result, second_rsu_result],
            )
        )
        run_full_sync_v3(connector, config)
        run_full_sync_v3(connector, config)

    rows = connector.execute(
        """
        SELECT quantity, price_unit, amount_gross, amount_net
        FROM transactions
        WHERE source_system = 'RSU_Excel'
          AND asset_id = 'RSU_AMZN'
          AND transaction_date = DATE '2026-03-15'
          AND transaction_type = 'vest'
        ORDER BY id
        """
    ).fetchall()

    assert len(rows) == 1
    quantity, price_unit, amount_gross, amount_net = rows[0]
    assert float(quantity) == 192.0
    assert float(price_unit) == 209.304
    assert float(amount_gross) == 40186.37
    assert float(amount_net) == 40186.37


def test_cn_fund_nan_decimal_fields_insert_as_null(connector, base_config):
    config = base_config.copy()
    config["source_registry"]["cn_fund"]["enabled"] = True

    cn_fund_result = {
        "holdings": pd.DataFrame(),
        "transactions": pd.DataFrame(
            [
                {
                    "asset_id": "CN_FUND_900012",
                    "transaction_date": "2026-02-09",
                    "transaction_type": "buy",
                    "quantity": 10.0,
                    "price": 2.1,
                    "amount": 21.0,
                    "fees": 0.0,
                    "memo": "Fund buy",
                    "source_system": "CN_Fund_Excel",
                },
                {
                    "asset_id": "CN_FUND_900012",
                    "transaction_date": "2026-02-10",
                    "transaction_type": "dividend_cash",
                    "quantity": None,
                    "price": float("nan"),
                    "amount": 3323.20,
                    "fees": 0.0,
                    "memo": "Cash Dividend",
                    "source_system": "CN_Fund_Excel",
                }
            ]
        ),
    }

    with _patch_baseline() as stack:
        stack.enter_context(patch("src.sync.orchestrator.sync_cn_fund", return_value=cn_fund_result))
        result = run_full_sync_v3(connector, config)

    assert not any("CN Fund sync error" in warning for warning in result.warnings)

    row = connector.execute(
        """
        SELECT quantity, price_unit, amount_gross
        FROM transactions
        WHERE source_system = 'CN_Fund_Excel'
        ORDER BY transaction_date
        """
    ).fetchall()
    assert len(row) == 2
    assert float(row[0][0]) == 10.0
    assert float(row[0][1]) == 2.1
    assert float(row[0][2]) == 21.0
    assert row[1][0] is None
    assert row[1][1] is None
    assert float(row[1][2]) == 3323.2


def test_id_migration_runs_once(connector, base_config):
    config = base_config.copy()
    config["source_registry"]["insurance"]["enabled"] = True
    config["source_registry"]["rsu"]["enabled"] = True

    connector.execute(
        """
        INSERT INTO holdings (snapshot_date, asset_id, source_system)
        VALUES (?, ?, ?), (?, ?, ?)
        """,
        (
            date(2026, 2, 10),
            "Ins_安泰人生",
            "PIS",
            date(2026, 2, 10),
            "RSU_RSU_AMZN",
            "PIS",
        ),
    )
    connector.execute(
        """
        INSERT INTO transactions (
            transaction_date, asset_id, transaction_type, amount_gross, source_system
        ) VALUES (?, ?, ?, ?, ?), (?, ?, ?, ?, ?)
        """,
        (
            date(2026, 1, 1),
            "Ins_安泰人生",
            "premium_payment",
            1000.0,
            "PIS",
            date(2026, 1, 2),
            "RSU_RSU_AMZN",
            "vest",
            2000.0,
            "PIS",
        ),
    )
    connector.execute(
        """
        INSERT INTO asset_registry (canonical_id, display_name)
        VALUES (?, ?), (?, ?)
        """,
        ("Ins_安泰人生", "安泰人生", "RSU_RSU_AMZN", "Amazon RSU"),
    )
    connector.execute(
        """
        INSERT INTO asset_source_mappings (canonical_id, source_system, source_id)
        VALUES (?, ?, ?), (?, ?, ?)
        """,
        ("Ins_安泰人生", "PIS", "ins-id", "RSU_RSU_AMZN", "PIS", "rsu-id"),
    )

    empty_result = {"holdings": pd.DataFrame(), "transactions": pd.DataFrame()}

    with _patch_baseline() as stack:
        stack.enter_context(patch("src.sync.orchestrator.sync_insurance", return_value=empty_result))
        stack.enter_context(patch("src.sync.orchestrator.sync_rsu", return_value=empty_result))
        run_full_sync_v3(connector, config)
        run_full_sync_v3(connector, config)

    legacy_count = connector.execute(
        """
        SELECT COUNT(*) FROM (
            SELECT asset_id AS id FROM holdings
            UNION ALL
            SELECT asset_id AS id FROM transactions
            UNION ALL
            SELECT canonical_id AS id FROM asset_registry
            UNION ALL
            SELECT canonical_id AS id FROM asset_source_mappings
        ) t
        WHERE id LIKE 'Ins_%' OR id LIKE 'RSU_RSU_%'
        """
    ).fetchone()[0]
    assert legacy_count == 0

    migration_log_count = connector.execute(
        """
        SELECT COUNT(*) FROM sync_audit_logs
        WHERE source_system = 'Migration'
          AND target_table = 'canonical_id'
          AND record_key = 'ins_rsu_prefix_remap_v1'
        """
    ).fetchone()[0]
    assert migration_log_count == 1


def test_replace_transactions_clears_trade_log_links_before_reinsert(connector):
    original_tx_id = connector.execute(
        """
        INSERT INTO transactions (
            transaction_date, asset_id, asset_name, transaction_type,
            quantity, price_unit, amount_gross, amount_net, source_system
        ) VALUES (
            ?, 'US_STK_AAPL', 'AAPL', 'buy',
            10, 100, 1000, 1000, 'Schwab_CSV'
        )
        RETURNING id
        """,
        (date(2026, 2, 9),),
    ).fetchone()[0]

    connector.execute(
        """
        INSERT INTO trade_logs (
            log_date, asset_id, action, quantity, price, amount,
            suggestion_source, linked_transaction_id, verification_status
        ) VALUES (
            ?, 'US_STK_AAPL', 'Buy', 10, 100, 1000,
            'manual', ?, 'verified'
        )
        """,
        (date(2026, 2, 9), original_tx_id),
    )

    tx_df = pd.DataFrame(
        [
            {
                "transaction_date": "2026-02-09",
                "asset_id": "US_STK_AAPL",
                "asset_name": "AAPL",
                "transaction_type": "buy",
                "quantity": 10.0,
                "price_unit": 100.0,
                "amount_gross": 1000.0,
                "amount_net": 1000.0,
                "commission_fee": 0.0,
                "currency": "CNY",
                "account": None,
                "memo": None,
                "source_system": "Schwab_CSV",
            }
        ]
    )

    _replace_transactions(connector, tx_df)

    trade_log_row = connector.execute(
        """
        SELECT linked_transaction_id, verification_status
        FROM trade_logs
        WHERE asset_id = 'US_STK_AAPL'
        """
    ).fetchone()
    assert trade_log_row == (None, "pending")

    tx_rows = connector.execute(
        """
        SELECT id
        FROM transactions
        WHERE asset_id = 'US_STK_AAPL'
        ORDER BY id
        """
    ).fetchall()
    assert len(tx_rows) == 1
    assert tx_rows[0][0] != original_tx_id

    summary = link_trade_logs_to_transactions(connector)
    relinked = connector.execute(
        """
        SELECT linked_transaction_id, verification_status
        FROM trade_logs
        WHERE asset_id = 'US_STK_AAPL'
        """
    ).fetchone()
    # suggestion_source='manual' (owner-recorded) → re-linked to pending_window.
    assert relinked == (tx_rows[0][0], "pending_window")
    assert summary["verified"] == 1  # counts all promotions regardless of target status
