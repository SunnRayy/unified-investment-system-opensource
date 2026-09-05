"""#7 manual P&L overlay — the five precedence rules of plan §C.1, end to end.

These run the real engine against a small purpose-built DuckDB so the overlay is
exercised through `compute_portfolio_pnl`, not through a hand-built AssetPnL.

The rules under test (plan §C.1):
  1. a cash-equivalent keeps unrealized = 0 even with a logged cost;
  2. a logged cost on a non-cash asset yields unrealized = market - cost;
  3. a logged realized figure survives the cash/balance-only suppression;
  4. manual-realized-only counts by its realized amount, cost stays unknown;
  5. nothing logged -> base treatment untouched.

Plus the two rules that keep it honest: manual realized is ALL-TIME only (a
single cumulative row cannot yield a period delta), and an authoritative reader
ledger SUPERSEDES the override rather than adding to it.
"""
from __future__ import annotations

import duckdb
import pytest

from src.database.connector import DatabaseConnector
from src.services.pnl.engine import compute_portfolio_pnl
from src.services.pnl.models import Scope, Treatment

pytestmark = pytest.mark.critical

FIXED_FX = 7.0
SNAP = "2026-08-01"


@pytest.fixture
def db(tmp_path):
    """Minimal schema, same shape as the Release 1 parity fixture, plus the V86
    tables (whose DDL is itself tested in tests/database/test_v86_*)."""
    path = tmp_path / "overlay.duckdb"
    conn = duckdb.connect(str(path))
    conn.execute(
        """CREATE TABLE holdings (
            asset_id VARCHAR, asset_name VARCHAR, source_system VARCHAR,
            market_value DOUBLE, cost_price_unit DOUBLE, market_price_unit DOUBLE,
            quantity DOUBLE, currency VARCHAR, snapshot_date DATE, is_shadow BOOLEAN)"""
    )
    conn.execute(
        """CREATE TABLE transactions (
            asset_id VARCHAR, asset_name VARCHAR, transaction_type VARCHAR,
            quantity DOUBLE, price_unit DOUBLE, amount_net DOUBLE,
            currency VARCHAR, transaction_date DATE, source_system VARCHAR,
            is_provisional BOOLEAN)"""
    )
    conn.execute(
        """CREATE TABLE asset_registry (
            canonical_id VARCHAR, display_name VARCHAR, asset_class VARCHAR,
            is_rebalanceable BOOLEAN)"""
    )
    conn.execute(
        """CREATE TABLE taxonomy_classes (
            id INTEGER, name VARCHAR, name_cn VARCHAR, parent_id INTEGER,
            is_rebalanceable BOOLEAN)"""
    )
    conn.execute(
        """INSERT INTO taxonomy_classes VALUES
        (1,'Fixed Income','固定收益',NULL,TRUE),
        (2,'CN Bonds','中国债券',1,TRUE),
        (3,'Equity','股票',NULL,TRUE),
        (4,'US Equity','美股',3,TRUE),
        (5,'Cash','现金',NULL,TRUE),
        (6,'Money Market','货币市场',5,TRUE)"""
    )
    conn.execute(
        """CREATE TABLE manual_asset_pnl (
            asset_id VARCHAR PRIMARY KEY,
            cost_basis_cny DECIMAL(20,2), realized_pnl_cny DECIMAL(20,2),
            as_of_date DATE, memo VARCHAR,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)"""
    )
    conn.close()

    connector = DatabaseConnector(str(path))
    yield connector
    connector.close()


def _register(db, asset_id, name, asset_class, *, currency="CNY"):
    db.execute(
        "INSERT INTO asset_registry VALUES (?, ?, ?, TRUE)",
        [asset_id, name, asset_class],
    )


def _hold(db, asset_id, *, mv, qty=1.0, cost_unit=None, price_unit=None, currency="CNY",
          source="Financial_Summary_Excel"):
    db.execute(
        "INSERT INTO holdings VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, FALSE)",
        [asset_id, asset_id, source, mv, cost_unit,
         price_unit if price_unit is not None else mv / qty,
         qty, currency, SNAP],
    )


def _log_manual(db, asset_id, *, cost=None, realized=None, memo=None):
    db.execute(
        "INSERT INTO manual_asset_pnl (asset_id, cost_basis_cny, realized_pnl_cny, memo) "
        "VALUES (?, ?, ?, ?)",
        [asset_id, cost, realized, memo],
    )


def _by_id(db, **scope_kwargs):
    result = compute_portfolio_pnl(db, scope=Scope(today_fx=FIXED_FX, **scope_kwargs))
    return {a.asset_id: a for a in result.assets}, result


# ── Rule 5: nothing logged -> base treatment untouched ──────────────────────────

def test_rule5_no_override_leaves_balance_only_unknown(db):
    _register(db, "Bond_CMB_CNY", "招行债券", "Fixed Income")
    _hold(db, "Bond_CMB_CNY", mv=190_353.00)

    assets, _ = _by_id(db)
    bond = assets["Bond_CMB_CNY"]
    assert bond.treatment is Treatment.balance_only
    assert bond.has_manual_data is False
    assert bond.cost_basis_cny is None
    assert bond.unrealized_cny is None
    assert bond.lifetime_cny is None          # "—" in the UI
    assert bond.has_known_cost is False


# ── Rule 2: logged cost on a non-cash asset ────────────────────────────────────

def test_rule2_logged_cost_makes_a_bond_measurable(db):
    _register(db, "Bond_CMB_CNY", "招行债券", "Fixed Income")
    _hold(db, "Bond_CMB_CNY", mv=190_353.00)
    _log_manual(db, "Bond_CMB_CNY", cost=185_000.00)

    assets, _ = _by_id(db)
    bond = assets["Bond_CMB_CNY"]
    assert bond.treatment is Treatment.manual
    assert bond.has_manual_data is True
    assert bond.cost_basis_cny == pytest.approx(185_000.00)
    assert bond.unrealized_cny == pytest.approx(5_353.00)
    assert bond.return_pct == pytest.approx(5_353.00 / 185_000.00 * 100.0)
    assert bond.lifetime_cny == pytest.approx(5_353.00)
    assert bond.has_known_cost is True


# ── Rule 4: manual-realized-only ───────────────────────────────────────────────

def test_rule4_realized_only_shows_profit_but_cost_stays_unknown(db):
    _register(db, "Bond_CMB_CNY", "招行债券", "Fixed Income")
    _hold(db, "Bond_CMB_CNY", mv=190_353.00)
    _log_manual(db, "Bond_CMB_CNY", realized=4_200.00)

    assets, result = _by_id(db)
    bond = assets["Bond_CMB_CNY"]
    assert bond.treatment is Treatment.manual
    assert bond.has_manual_data is True
    assert bond.realized_cny == pytest.approx(4_200.00)
    assert bond.lifetime_cny == pytest.approx(4_200.00)
    # Cost genuinely unknown — this is the half that must NOT become phantom profit.
    assert bond.cost_basis_cny is None
    assert bond.unrealized_cny is None
    assert bond.has_known_cost is False

    # ...and the aggregate agrees: value in net worth, nothing in the denominators.
    assert result.net_worth == pytest.approx(190_353.00)
    assert result.total_cost_basis == pytest.approx(0.0)
    assert result.measurable_value == pytest.approx(0.0)
    assert result.total_unrealized == pytest.approx(0.0)
    assert result.total_realized == pytest.approx(4_200.00)


def test_rule4_realized_native_is_cny(db):
    """No currency column: the logged figure is CNY by definition."""
    _register(db, "Bond_CMB_USD", "招行美元债", "Fixed Income", currency="USD")
    _hold(db, "Bond_CMB_USD", mv=190_353.00, currency="USD")
    _log_manual(db, "Bond_CMB_USD", realized=4_200.00)

    assets, _ = _by_id(db)
    bond = assets["Bond_CMB_USD"]
    assert bond.realized_cny == pytest.approx(4_200.00)
    assert bond.realized_native == pytest.approx(4_200.00)
    assert bond.realized_currency == "CNY"


# ── Rules 1 + 3: cash-equivalent ───────────────────────────────────────────────

def test_rule1_cash_keeps_zero_unrealized_even_with_a_logged_cost(db):
    """A cash balance has no price basis, so a logged cost cannot mean market-cost."""
    _register(db, "CASH_MMF", "货币基金", "货币市场")
    _hold(db, "CASH_MMF", mv=100_000.00)
    _log_manual(db, "CASH_MMF", cost=90_000.00)     # ignored for unrealized

    assets, _ = _by_id(db)
    cash = assets["CASH_MMF"]
    assert cash.treatment is Treatment.cash, "base cash classification must survive the overlay"
    assert cash.has_manual_data is False, "a cash cost override applies nothing"
    assert cash.cost_basis_cny == pytest.approx(100_000.00)   # cost == value
    assert cash.unrealized_cny == pytest.approx(0.0)


def test_rule3_cash_realized_passes_through_the_zero_suppression(db):
    """The money-market / 理财 yield channel (plan §C.2), owner-approved."""
    _register(db, "CASH_MMF", "货币基金", "货币市场")
    _hold(db, "CASH_MMF", mv=100_000.00)
    _log_manual(db, "CASH_MMF", realized=1_850.00)

    assets, result = _by_id(db)
    cash = assets["CASH_MMF"]
    assert cash.treatment is Treatment.manual
    assert cash.realized_cny == pytest.approx(1_850.00)
    assert cash.unrealized_cny == pytest.approx(0.0), "still NAV~1.0 — no price gain"
    assert cash.lifetime_cny == pytest.approx(1_850.00)
    # Cash cost == value, so it stays measurable and contributes 0 unrealized.
    assert result.total_unrealized == pytest.approx(0.0)
    assert result.total_realized == pytest.approx(1_850.00)


# ── Period semantics: ALL-TIME only ────────────────────────────────────────────

def test_manual_realized_is_ignored_by_period_scopes(db):
    """One cumulative figure cannot yield a month delta, so a 1m/12m window must
    not show it (plan §C.1). Cost, which is not period-scoped, still applies."""
    _register(db, "Bond_CMB_CNY", "招行债券", "Fixed Income")
    _hold(db, "Bond_CMB_CNY", mv=190_353.00)
    _log_manual(db, "Bond_CMB_CNY", cost=185_000.00, realized=4_200.00)

    all_time, _ = _by_id(db)
    assert all_time["Bond_CMB_CNY"].realized_cny == pytest.approx(4_200.00)

    period, _ = _by_id(db, start_date="2026-07-01")
    bond = period["Bond_CMB_CNY"]
    assert bond.realized_cny == pytest.approx(0.0), "lifetime realized leaked into a period view"
    # The cost overlay is a position basis, not a period flow — it still applies.
    assert bond.cost_basis_cny == pytest.approx(185_000.00)
    assert bond.has_manual_data is True


# ── Supersession: an authoritative reader ledger wins ──────────────────────────

def _add_txn(db, asset_id, source, *, date="2025-03-01", qty=10.0, price=100.0, ttype="buy"):
    db.execute(
        "INSERT INTO transactions VALUES (?, ?, ?, ?, ?, ?, 'CNY', ?, ?, FALSE)",
        [asset_id, asset_id, ttype, qty, price, qty * price, date, source],
    )


def test_reader_transactions_supersede_the_override(db, caplog):
    """Otherwise the owner's cumulative profit double-counts the reader's."""
    _register(db, "US_STK_VOO", "VOO", "US Equity")
    _hold(db, "US_STK_VOO", mv=100_000.00, qty=100.0, cost_unit=800.0, source="Schwab_CSV")
    _add_txn(db, "US_STK_VOO", "Schwab_CSV")
    _log_manual(db, "US_STK_VOO", cost=50_000.00, realized=9_999.00)

    with caplog.at_level("WARNING"):
        assets, _ = _by_id(db)

    voo = assets["US_STK_VOO"]
    assert voo.has_manual_data is False, "reader-fed asset must ignore the override"
    assert voo.treatment is not Treatment.manual
    assert voo.cost_basis_cny != pytest.approx(50_000.00), "manual cost overrode the reader FIFO"
    assert voo.realized_cny != pytest.approx(9_999.00)
    assert "[MANUAL-SUPERSEDED]" in caplog.text
    assert "US_STK_VOO" in caplog.text


def test_legacy_pis_transactions_do_not_supersede(db):
    """Legacy/PIS rows exist for many assets; they must NOT trigger supersession
    (the check is authority-aware, not a raw "has any transaction")."""
    _register(db, "Wealth_CMB", "招行理财", "Fixed Income")
    _hold(db, "Wealth_CMB", mv=300_000.00)
    _add_txn(db, "Wealth_CMB", "PIS")           # legacy only
    _log_manual(db, "Wealth_CMB", realized=7_500.00)

    assets, _ = _by_id(db)
    wealth = assets["Wealth_CMB"]
    assert wealth.has_manual_data is True, "a legacy PIS row wrongly superseded the override"
    assert wealth.realized_cny == pytest.approx(7_500.00)


# ── Net worth is untouched by construction ─────────────────────────────────────

def test_wealthos_formatter_shows_the_override(db):
    """Regression: the WealthOS row formatter re-derives its treatment from RAW
    inputs (`is_balance_only_holding`, and a keyword cash check), neither of which
    can see an override. Before the fix the engine computed the owner's figures and
    the formatter threw them away — the KPI total moved while the row still read
    "—", which is exactly the kind of two-sources disagreement this engine exists
    to prevent."""
    from src.services.pnl.wealthos import build_wealthos_assets

    _register(db, "Bond_CMB_CNY", "招行债券", "CN Bonds")
    _hold(db, "Bond_CMB_CNY", mv=200_108.77)
    _log_manual(db, "Bond_CMB_CNY", cost=195_000.00, realized=4_200.00)

    payload = build_wealthos_assets(db, include_non_rebalanceable=True)
    row = next(
        r for rows in payload.values() if isinstance(rows, list)
        for r in rows if r["code"] == "Bond_CMB_CNY"
    )

    assert row["has_manual_data"] is True
    assert row["invested"] == pytest.approx(195_000.00)
    assert row["pl"] == pytest.approx(9_308.77)          # 5,108.77 unrealized + 4,200
    # WealthOS's Return % column means lifetime gain over what was invested — a
    # different convention from the engine's unrealized/cost return_pct.
    assert row["ret"] == pytest.approx(9_308.77 / 195_000.00 * 100, abs=0.01)


def test_wealthos_formatter_realized_only_keeps_cost_unknown(db):
    """The other half: profit logged, no cost. The row shows the profit but must
    still report an unknown cost rather than implying it was invested at zero."""
    from src.services.pnl.wealthos import build_wealthos_assets

    _register(db, "Bond_CMB_USD", "招行美元债", "US Bonds")
    _hold(db, "Bond_CMB_USD", mv=190_352.99)
    _log_manual(db, "Bond_CMB_USD", realized=4_200.00)

    payload = build_wealthos_assets(db, include_non_rebalanceable=True)
    row = next(
        r for rows in payload.values() if isinstance(rows, list)
        for r in rows if r["code"] == "Bond_CMB_USD"
    )
    assert row["invested"] is None, "an unknown cost must not be reported as a number"
    assert row["pl"] == pytest.approx(4_200.00)
    assert row["ret"] is None, "no cost -> no meaningful return %"


# ── Which assets may be logged at all ─────────────────────────────────────────

def test_loggability_is_not_inferred_from_an_empty_looking_figure(db):
    """The affordance covers every asset no reader ledger owns — not merely the
    ones displaying "—".

    Bank wealth (招行理财) and pension holdings show a real-looking +¥0.00 because
    they classify as cash/traded with cost == value, so a UI keyed on "pl is null"
    silently skips exactly the assets the owner most wants to annotate.
    """
    _register(db, "Bond_CMB_CNY", "招行债券", "Fixed Income")     # shows "—"
    _hold(db, "Bond_CMB_CNY", mv=200_108.77)
    _register(db, "Wealth_CMB", "招行理财", "Money Market")        # shows +¥0.00
    _hold(db, "Wealth_CMB", mv=50_202.00)
    _register(db, "Pension_Personal", "个人养老金", "CN Equity")    # shows +¥0.00
    _hold(db, "Pension_Personal", mv=37_900.00, qty=1.0, cost_unit=37_900.00)
    _register(db, "US_STK_VOO", "VOO", "US Equity")               # real ledger
    _hold(db, "US_STK_VOO", mv=100_000.00, qty=100.0, cost_unit=800.0, source="Schwab_CSV")
    _add_txn(db, "US_STK_VOO", "Schwab_CSV")

    _register(db, "CASH_Deposit_TEST_CNY", "测试存款", "Cash Checking")   # not an investment
    _hold(db, "CASH_Deposit_TEST_CNY", mv=79_923.00)

    assets, _ = _by_id(db)
    assert assets["Bond_CMB_CNY"].can_log_manual_pnl is True
    assert assets["Wealth_CMB"].can_log_manual_pnl is True, "bank wealth must be loggable"
    assert assets["Pension_Personal"].can_log_manual_pnl is True, "pension must be loggable"
    # ...but never where a broker ledger already owns the P&L: an override there
    # would be superseded, so offering it would invite a figure that gets ignored.
    assert assets["US_STK_VOO"].can_log_manual_pnl is False
    # ...nor on cash/deposits, which are not investments (owner ruling 2026-08-09).
    assert assets["CASH_Deposit_TEST_CNY"].can_log_manual_pnl is False


def test_cash_insurance_and_property_are_not_manually_loggable():
    """Owner ruling 2026-08-09: logging is for investments bought through a bank.

    A checking balance earning nothing is honestly ¥0, not an unlogged position;
    insurance has its own reader and cash-value semantics; property is not a bank
    product and already carries a real cost. Exclusion list, not an allowlist —
    a bank product bought next year is loggable by default.
    """
    from src.services.pnl.manual import is_manually_loggable

    for aid in ("CASH_Deposit_ICBC_CNY", "CASH_Cash_CNY", "CASH_USD",
                "INS_安泰人生18", "Ins_Legacy", "Property_阳光花园"):
        assert is_manually_loggable(aid, has_reader_transactions=False) is False, aid

    for aid in ("Bond_CMB_CNY", "Bond_CMB_USD", "Wealth_CMB", "Pension_Personal",
                "Bond_ICBC_CNY"):   # a product bought next year: loggable by default
        assert is_manually_loggable(aid, has_reader_transactions=False) is True, aid

    # A reader ledger still wins over everything.
    assert is_manually_loggable("Bond_CMB_CNY", has_reader_transactions=True) is False


def test_legacy_only_transactions_stay_loggable(db):
    """PIS rows are a historical baseline (ADR-003), not a live ledger."""
    _register(db, "Wealth_CMB", "招行理财", "Money Market")
    _hold(db, "Wealth_CMB", mv=50_202.00)
    _add_txn(db, "Wealth_CMB", "PIS")

    assets, _ = _by_id(db)
    assert assets["Wealth_CMB"].can_log_manual_pnl is True


def test_loggability_agrees_with_supersession(db):
    """The affordance and the engine must not disagree: anything offered for
    logging must actually have its override honoured."""
    _register(db, "Wealth_CMB", "招行理财", "Money Market")
    _hold(db, "Wealth_CMB", mv=50_202.00)
    _log_manual(db, "Wealth_CMB", realized=1_500.00)

    assets, _ = _by_id(db)
    w = assets["Wealth_CMB"]
    assert w.can_log_manual_pnl is True
    assert w.has_manual_data is True, "offered for logging but the override was ignored"


def test_override_never_moves_net_worth(db):
    """#7 changes gain attribution only. Market value — hence net worth — is
    never read from the override, so logging figures cannot move it."""
    _register(db, "Bond_CMB_CNY", "招行债券", "Fixed Income")
    _hold(db, "Bond_CMB_CNY", mv=190_353.00)
    _register(db, "CASH_MMF", "货币基金", "货币市场")
    _hold(db, "CASH_MMF", mv=100_000.00)

    _, before = _by_id(db)
    _log_manual(db, "Bond_CMB_CNY", cost=185_000.00, realized=4_200.00)
    _log_manual(db, "CASH_MMF", realized=1_850.00)
    _, after = _by_id(db)

    assert after.net_worth == pytest.approx(before.net_worth)
    assert after.net_worth == pytest.approx(290_353.00)
    assert after.asset_count == before.asset_count
