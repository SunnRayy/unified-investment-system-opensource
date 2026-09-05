"""Tests for co-authority sync-time consolidation (C3.4).

Covers: `_consolidate_coauthority_holdings` (src/sync/phases/_shadow.py) — materializes one
merged `source_system='Consolidated'` holdings row per co-authority asset (qty=Σ for
securities, qty=1 sentinel for cash, cost from merged-lifetime-FIFO) and shadows the
contributing broker rows so existing `GROUP BY asset_id` queries sum correctly with zero
query changes.

Style mirrors tests/sync/test_shadow_coauthority_tombstone.py.

Pipeline-integration tests (TEST A / TEST B) also cover F1 and F3 regression fixes:
  F1: apply_authority_rules (P5) must NOT re-shadow an active Consolidated row.
  F3: _upsert_holdings ON CONFLICT must reset is_shadow=FALSE so same-day re-syncs are
      idempotent (broker rows that were shadowed by consolidation come back active after
      re-ingest, so the next P4 consolidation phase can pick them up again).
"""

import pytest
from datetime import date

import pandas as pd

pytestmark = pytest.mark.pipeline

from src.database.connector import DatabaseConnector
from src.database.schema import initialize_schema
from src.financial_analysis.cost_basis import CostBasisCalculator
from src.sync.phases._shadow import _consolidate_coauthority_holdings
from src.sync.holdings_aggregator import HoldingsAggregator
from src.identity.authority_resolver import AuthorityResolver
from src.sync.phases._ingest import _upsert_holdings

AS_OF = date(2026, 6, 16)


@pytest.fixture
def connector():
    conn = DatabaseConnector(":memory:")
    initialize_schema(conn)
    yield conn
    conn.close()


def _insert_holding(connector, *, snapshot_date, asset_id, asset_name, asset_type="ETF",
                    quantity=10.0, unit="share", cost_price_unit=100.0,
                    market_price_unit=110.0, market_value=1100.0,
                    currency="USD", account="Test", source_system, is_shadow=False):
    """Helper to insert a single holding row."""
    connector.execute(
        """
        INSERT INTO holdings (
            snapshot_date, asset_id, asset_name, asset_type,
            quantity, unit, cost_price_unit, market_price_unit, market_value,
            currency, account, source_system, is_shadow
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            snapshot_date, asset_id, asset_name, asset_type,
            quantity, unit, cost_price_unit, market_price_unit, market_value,
            currency, account, source_system, is_shadow,
        ),
    )


def _insert_transaction(connector, *, transaction_date, asset_id, transaction_type,
                        quantity=0.0, price_unit=0.0, amount_net=0.0,
                        currency="USD", source_system, account="Test"):
    """Helper to insert a single transaction row."""
    connector.execute(
        """
        INSERT INTO transactions (
            transaction_date, asset_id, transaction_type, quantity, price_unit,
            amount_net, currency, account, source_system
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            transaction_date, asset_id, transaction_type, quantity, price_unit,
            amount_net, currency, account, source_system,
        ),
    )


# ---------------------------------------------------------------------------
# Test 1: Dual SGOV (Schwab + IBKR) -> one Consolidated row, brokers shadowed
# ---------------------------------------------------------------------------

def test_dual_sgov_consolidates_and_shadows_brokers(connector):
    """SGOV held at both Schwab and IBKR -> one Consolidated row with qty=Σqty,
    mv=Σmv; both broker rows shadowed; GROUP BY asset_id MAX(date) picks the
    Consolidated row (it is dated >= both broker dates).
    """
    _insert_holding(
        connector,
        snapshot_date="2026-06-14",
        asset_id="US_STK_SGOV",
        asset_name="iShares 0-3 Month Treasury Bond ETF",
        quantity=453.122,
        market_price_unit=100.0,
        market_value=45312.2,
        source_system="Schwab_CSV",
    )
    _insert_holding(
        connector,
        snapshot_date="2026-06-14",
        asset_id="US_STK_SGOV",
        asset_name="iShares 0-3 Month Treasury Bond ETF",
        quantity=200.0,
        market_price_unit=100.0,
        market_value=20000.0,
        source_system="Broker_IBKR",
    )

    # Lifetime FIFO transactions across both brokers (buy at Schwab; ACAT transfer to IBKR)
    _insert_transaction(
        connector,
        transaction_date="2026-01-01",
        asset_id="US_STK_SGOV",
        transaction_type="buy",
        quantity=653.122,
        price_unit=100.0,
        amount_net=65312.2,
        source_system="Schwab_CSV",
    )
    _insert_transaction(
        connector,
        transaction_date="2026-05-01",
        asset_id="US_STK_SGOV",
        transaction_type="transfer_out",
        quantity=200.0,
        price_unit=0.0,
        amount_net=0.0,
        source_system="Schwab_CSV",
    )
    _insert_transaction(
        connector,
        transaction_date="2026-05-01",
        asset_id="US_STK_SGOV",
        transaction_type="transfer_in",
        quantity=200.0,
        price_unit=0.0,
        amount_net=0.0,
        source_system="Broker_IBKR",
    )

    shadowed = _consolidate_coauthority_holdings(connector, as_of_date=AS_OF)

    assert shadowed == 2, f"Expected 2 broker rows shadowed, got {shadowed}"

    # Consolidated row exists at AS_OF with summed qty/mv
    cons = connector.execute(
        """
        SELECT quantity, market_value, source_system, authority_source, account,
               is_shadow, cost_price_unit
        FROM holdings
        WHERE asset_id='US_STK_SGOV' AND source_system='Consolidated' AND snapshot_date=?
        """,
        (AS_OF,),
    ).fetchone()
    assert cons is not None, "Consolidated SGOV row should exist"
    qty, mv, source_system, authority_source, account, is_shadow, cost = cons
    qty, mv, cost = float(qty), float(mv), float(cost)
    assert qty == pytest.approx(653.122), f"Expected summed qty 653.122, got {qty}"
    assert mv == pytest.approx(65312.2), f"Expected summed mv 65312.2, got {mv}"
    assert source_system == "Consolidated"
    assert authority_source == "Consolidated"
    assert account == "Multi-broker"
    assert is_shadow is False
    # Merged-FIFO cost: total_cost(65312.2)/remaining_qty(653.122) == 100.0 (no realizing sells)
    assert cost == pytest.approx(100.0, rel=1e-3), f"Expected merged-FIFO cost ~100.0, got {cost}"

    # Both broker rows shadowed
    schwab_row = connector.execute(
        "SELECT is_shadow FROM holdings WHERE asset_id='US_STK_SGOV' AND source_system='Schwab_CSV'"
    ).fetchone()
    ibkr_row = connector.execute(
        "SELECT is_shadow FROM holdings WHERE asset_id='US_STK_SGOV' AND source_system='Broker_IBKR'"
    ).fetchone()
    assert schwab_row[0] is True
    assert ibkr_row[0] is True

    # GROUP BY asset_id MAX(snapshot_date) picks the Consolidated row (production query pattern)
    picked = connector.execute(
        """
        WITH latest_per_asset AS (
            SELECT asset_id, MAX(snapshot_date) AS latest_date
            FROM holdings WHERE is_shadow = FALSE GROUP BY asset_id
        )
        SELECT h.source_system, h.quantity, h.market_value
        FROM holdings h
        JOIN latest_per_asset lpa ON h.asset_id = lpa.asset_id AND h.snapshot_date = lpa.latest_date
        WHERE h.asset_id = 'US_STK_SGOV' AND h.is_shadow = FALSE
        """
    ).fetchone()
    assert picked is not None
    assert picked[0] == "Consolidated"
    assert float(picked[1]) == pytest.approx(653.122)
    assert float(picked[2]) == pytest.approx(65312.2)


# ---------------------------------------------------------------------------
# Test 2: Dual CASH_USD -> qty=1 sentinel, mv=Σ
# ---------------------------------------------------------------------------

def test_dual_cash_usd_consolidates_with_qty_sentinel(connector):
    """CASH_USD held at both Schwab and IBKR -> Consolidated row with qty=1 (sentinel,
    NOT summed) and market_value = Σ market_value; market_price_unit = Σ per-row balances.
    """
    _insert_holding(
        connector,
        snapshot_date="2026-06-14",
        asset_id="CASH_USD",
        asset_name="USD Cash",
        asset_type="Cash",
        quantity=1.0,
        market_price_unit=5000.0,
        market_value=35000.0,
        source_system="Schwab_CSV",
    )
    _insert_holding(
        connector,
        snapshot_date="2026-06-14",
        asset_id="CASH_USD",
        asset_name="USD Cash",
        asset_type="Cash",
        quantity=1.0,
        market_price_unit=1000.0,
        market_value=7000.0,
        source_system="Broker_IBKR",
    )

    shadowed = _consolidate_coauthority_holdings(connector, as_of_date=AS_OF)
    assert shadowed == 2

    cons = connector.execute(
        """
        SELECT quantity, market_value, market_price_unit, cost_price_unit
        FROM holdings
        WHERE asset_id='CASH_USD' AND source_system='Consolidated' AND snapshot_date=?
        """,
        (AS_OF,),
    ).fetchone()
    assert cons is not None
    qty, mv, price, cost = cons
    qty, mv, price, cost = float(qty), float(mv), float(price), float(cost)
    assert qty == 1.0, f"Cash qty must be sentinel 1.0, got {qty}"
    assert mv == pytest.approx(42000.0), f"Expected summed mv 42000.0, got {mv}"
    assert price == pytest.approx(6000.0), f"Expected summed price (balance) 6000.0, got {price}"
    assert cost == pytest.approx(price), "Cash cost should equal price (P&L always 0)"

    # Both broker cash rows shadowed
    for src in ("Schwab_CSV", "Broker_IBKR"):
        row = connector.execute(
            "SELECT is_shadow FROM holdings WHERE asset_id='CASH_USD' AND source_system=?",
            (src,),
        ).fetchone()
        assert row[0] is True, f"{src} CASH_USD row should be shadowed"


# ---------------------------------------------------------------------------
# Test 3: VOO/IEF (Schwab tombstoned, IBKR-only) -> NO Consolidated row
# ---------------------------------------------------------------------------

def test_single_broker_after_tombstone_no_consolidation(connector):
    """After C3.2 tombstones Schwab's stale VOO row, only IBKR's active VOO row remains.
    A single active broker row is not a co-authority consolidation candidate — no
    Consolidated row should be written, and the IBKR row stays active/unshadowed.
    """
    # Schwab VOO is tombstoned (zero-qty, is_shadow=TRUE) -- simulates post-C3.2 state
    _insert_holding(
        connector,
        snapshot_date=AS_OF,
        asset_id="US_STK_VOO",
        asset_name="Vanguard S&P 500 ETF",
        quantity=0.0,
        market_value=0.0,
        source_system="Schwab_CSV",
        is_shadow=True,
    )
    # IBKR VOO is the sole active row
    _insert_holding(
        connector,
        snapshot_date="2026-06-14",
        asset_id="US_STK_VOO",
        asset_name="Vanguard S&P 500 ETF",
        quantity=172.0,
        market_price_unit=550.0,
        market_value=94600.0,
        source_system="Broker_IBKR",
    )

    shadowed = _consolidate_coauthority_holdings(connector, as_of_date=AS_OF)
    assert shadowed == 0, f"Expected 0 rows shadowed (single active broker), got {shadowed}"

    cons_count = connector.execute(
        "SELECT COUNT(*) FROM holdings WHERE asset_id='US_STK_VOO' AND source_system='Consolidated'"
    ).fetchone()[0]
    assert cons_count == 0, "No Consolidated row should be written for a single-broker asset"

    ibkr_row = connector.execute(
        "SELECT is_shadow FROM holdings WHERE asset_id='US_STK_VOO' AND source_system='Broker_IBKR'"
    ).fetchone()
    assert ibkr_row[0] is False, "IBKR VOO should remain active (not shadowed)"


# ---------------------------------------------------------------------------
# Test 4: Single-broker CN_FUND untouched (not co-authority)
# ---------------------------------------------------------------------------

def test_single_authority_cn_fund_untouched(connector):
    """CN_Fund_Excel is single-authority — never a consolidation candidate."""
    _insert_holding(
        connector,
        snapshot_date="2026-06-14",
        asset_id="CN_FUND_X",
        asset_name="Test CN Fund",
        asset_type="Fund",
        currency="CNY",
        account="CN Fund",
        source_system="CN_Fund_Excel",
    )

    shadowed = _consolidate_coauthority_holdings(connector, as_of_date=AS_OF)
    assert shadowed == 0

    row = connector.execute(
        "SELECT is_shadow FROM holdings WHERE asset_id='CN_FUND_X' AND source_system='CN_Fund_Excel'"
    ).fetchone()
    assert row[0] is False, "CN fund row should remain untouched"

    cons_count = connector.execute(
        "SELECT COUNT(*) FROM holdings WHERE source_system='Consolidated'"
    ).fetchone()[0]
    assert cons_count == 0


# ---------------------------------------------------------------------------
# Test 5: Idempotent re-run — no duplicate, self-corrects stale Consolidated
# ---------------------------------------------------------------------------

def test_idempotent_rerun_no_duplicate_and_self_corrects(connector):
    """Running the phase twice with the same as_of_date and unchanged broker data must not
    create a duplicate Consolidated row. Additionally, if broker data CHANGES between runs
    (e.g. Schwab's qty drops), the next run must self-correct: shadow the stale Consolidated
    row and rebuild it from the new broker data.
    """
    _insert_holding(
        connector,
        snapshot_date="2026-06-14",
        asset_id="US_STK_SGOV",
        asset_name="iShares 0-3 Month Treasury Bond ETF",
        quantity=453.122,
        market_price_unit=100.0,
        market_value=45312.2,
        source_system="Schwab_CSV",
    )
    _insert_holding(
        connector,
        snapshot_date="2026-06-14",
        asset_id="US_STK_SGOV",
        asset_name="iShares 0-3 Month Treasury Bond ETF",
        quantity=200.0,
        market_price_unit=100.0,
        market_value=20000.0,
        source_system="Broker_IBKR",
    )

    # First run
    shadowed1 = _consolidate_coauthority_holdings(connector, as_of_date=AS_OF)
    assert shadowed1 == 2

    count_after_first = connector.execute(
        "SELECT COUNT(*) FROM holdings WHERE asset_id='US_STK_SGOV' AND source_system='Consolidated'"
    ).fetchone()[0]
    assert count_after_first == 1

    # Second run, same data, same as_of_date — no duplicate row.
    # (Broker rows are already shadowed, so the second run finds 0 active broker rows
    # for SGOV and does not touch it further — Consolidated row stays exactly 1.)
    shadowed2 = _consolidate_coauthority_holdings(connector, as_of_date=AS_OF)
    assert shadowed2 == 0, f"Second run should shadow 0 new broker rows, got {shadowed2}"

    count_after_second = connector.execute(
        "SELECT COUNT(*) FROM holdings WHERE asset_id='US_STK_SGOV' AND source_system='Consolidated'"
    ).fetchone()[0]
    assert count_after_second == 1, "Must not duplicate the Consolidated row"

    # Self-correction: simulate a NEW Schwab+IBKR snapshot pair at a later date with different qty,
    # both unshadowed (as a fresh sync would produce), then re-run consolidation.
    _insert_holding(
        connector,
        snapshot_date="2026-06-20",
        asset_id="US_STK_SGOV",
        asset_name="iShares 0-3 Month Treasury Bond ETF",
        quantity=500.0,
        market_price_unit=101.0,
        market_value=50500.0,
        source_system="Schwab_CSV",
    )
    _insert_holding(
        connector,
        snapshot_date="2026-06-20",
        asset_id="US_STK_SGOV",
        asset_name="iShares 0-3 Month Treasury Bond ETF",
        quantity=200.0,
        market_price_unit=101.0,
        market_value=20200.0,
        source_system="Broker_IBKR",
    )

    LATER = date(2026, 6, 20)
    shadowed3 = _consolidate_coauthority_holdings(connector, as_of_date=LATER)
    assert shadowed3 == 2, f"Expected 2 new broker rows shadowed on rebuild, got {shadowed3}"

    # The stale (2026-06-16) Consolidated row must now be shadowed (self-correction step 2)
    stale_cons = connector.execute(
        "SELECT is_shadow FROM holdings WHERE asset_id='US_STK_SGOV' AND source_system='Consolidated' AND snapshot_date=?",
        (AS_OF,),
    ).fetchone()
    assert stale_cons is not None
    assert stale_cons[0] is True, "Stale Consolidated row must be shadowed after self-correction"

    # The new (2026-06-20) Consolidated row must be active with the new summed values
    new_cons = connector.execute(
        "SELECT quantity, market_value, is_shadow FROM holdings WHERE asset_id='US_STK_SGOV' AND source_system='Consolidated' AND snapshot_date=?",
        (LATER,),
    ).fetchone()
    assert new_cons is not None
    qty, mv, is_shadow = new_cons
    assert float(qty) == pytest.approx(700.0)
    assert float(mv) == pytest.approx(70700.0)
    assert is_shadow is False

    # Re-running again at the SAME later date must not duplicate the new row.
    shadowed4 = _consolidate_coauthority_holdings(connector, as_of_date=LATER)
    assert shadowed4 == 0
    final_count = connector.execute(
        "SELECT COUNT(*) FROM holdings WHERE asset_id='US_STK_SGOV' AND source_system='Consolidated'"
    ).fetchone()[0]
    assert final_count == 2, "Exactly 2 Consolidated rows total (one stale-shadowed, one active)"


# ---------------------------------------------------------------------------
# Test 6 (clause B, unit test not live gate): merged-FIFO get_current_position()
# == Σ broker qty, AND merged cost != 0 for dual-SGOV with Schwab+IBKR transactions.
# ---------------------------------------------------------------------------

def test_merged_fifo_open_qty_matches_broker_sum_and_cost_nonzero():
    """Deterministic unit test (per locked spec Deliverable 3 — NOT a live integrity gate
    clause, since tx-derived position can legitimately diverge from reader-reported holdings
    for many assets). Schwab buys 653.122 shares; 200 ACAT-transfer to IBKR (non-realizing).
    Merged-ledger FIFO over BOTH legs must show:
      - get_current_position() == 653.122 (== Σ broker-reported qty: Schwab 453.122 + IBKR 200)
      - get_total_cost_basis() / get_current_position() != 0 (IBKR's transferred lot carries
        the original Schwab cost, NOT $0 — this is the RISK-1 fix from C3.3)
    """
    import pandas as pd

    tx_rows = [
        ("buy", 653.122, 100.0, 65312.2, "USD", "2026-01-01"),
        ("transfer_out", 200.0, 0.0, 0.0, "USD", "2026-05-01"),
        ("transfer_in", 200.0, 0.0, 0.0, "USD", "2026-05-01"),
    ]
    df = pd.DataFrame(
        tx_rows,
        columns=["transaction_type", "quantity", "price_unit", "amount_net", "currency", "transaction_date"],
    )
    df["transaction_date"] = pd.to_datetime(df["transaction_date"])
    df.set_index("transaction_date", inplace=True)

    calc = CostBasisCalculator("US_STK_SGOV")
    calc.process_transactions(df)

    remaining_qty = calc.get_current_position()
    total_cost = calc.get_total_cost_basis()

    assert remaining_qty == pytest.approx(653.122), (
        f"Merged-FIFO open qty should equal Σ broker qty (453.122+200=653.122), got {remaining_qty}"
    )
    assert total_cost != 0, "Merged-FIFO cost must not be $0 (RISK-1 regression)"
    cost_per_unit = total_cost / remaining_qty
    assert cost_per_unit == pytest.approx(100.0, rel=1e-6), (
        f"Cost per unit should be the original Schwab buy price 100.0, got {cost_per_unit}"
    )


def test_transfer_legs_are_non_realizing_regression():
    """Regression guard: both transfer_in and transfer_out legs must remain non-realizing
    (no-op) in CostBasisCalculator. If either becomes lot-consuming in the future, cost would
    attach to the wrong quantity (this is the failure mode consolidated_equals_sum's
    merged-FIFO clause was designed to catch — see ADR-016 / RISK-2). This test catches the
    regression deterministically at the unit level instead.
    """
    import pandas as pd

    # ONLY a buy + transfer_out (no transfer_in counterpart) — if transfer_out were
    # lot-consuming, this would reduce remaining_qty. It must NOT.
    tx_rows = [
        ("buy", 100.0, 50.0, 5000.0, "USD", "2026-01-01"),
        ("transfer_out", 40.0, 0.0, 0.0, "USD", "2026-02-01"),
    ]
    df = pd.DataFrame(
        tx_rows,
        columns=["transaction_type", "quantity", "price_unit", "amount_net", "currency", "transaction_date"],
    )
    df["transaction_date"] = pd.to_datetime(df["transaction_date"])
    df.set_index("transaction_date", inplace=True)

    calc = CostBasisCalculator("US_STK_TEST")
    calc.process_transactions(df)

    assert calc.get_current_position() == pytest.approx(100.0), (
        "transfer_out must be non-realizing (no-op) — position should be unaffected"
    )
    assert calc.get_total_cost_basis() == pytest.approx(5000.0), (
        "transfer_out must not consume any lot cost"
    )
    assert calc.realized_pnl == pytest.approx(0.0), (
        "transfer_out must not realize any P&L"
    )

    # Symmetric check: a lone transfer_in (no prior buy) must not fabricate a lot/cost either.
    calc2 = CostBasisCalculator("US_STK_TEST2")
    tx_rows2 = [
        ("transfer_in", 40.0, 0.0, 0.0, "USD", "2026-02-01"),
    ]
    df2 = pd.DataFrame(
        tx_rows2,
        columns=["transaction_type", "quantity", "price_unit", "amount_net", "currency", "transaction_date"],
    )
    df2["transaction_date"] = pd.to_datetime(df2["transaction_date"])
    df2.set_index("transaction_date", inplace=True)
    calc2.process_transactions(df2)

    assert calc2.get_current_position() == pytest.approx(0.0), (
        "transfer_in alone (no offsetting buy) must not fabricate a lot"
    )
    assert calc2.get_total_cost_basis() == pytest.approx(0.0)


# ---------------------------------------------------------------------------
# TEST A (F1 regression): P5 apply_authority_rules must NOT re-shadow a
# Consolidated row that P4 _consolidate_coauthority_holdings just made active.
# ---------------------------------------------------------------------------

def test_f1_apply_authority_rules_does_not_shadow_consolidated(connector):
    """F1 pipeline-integration test.

    Steps mirror the orchestrator sequence for a same-day sync where SGOV is
    held at both Schwab and IBKR:
      1. Insert active Schwab_CSV + Broker_IBKR rows dated today.
      2. Run _consolidate_coauthority_holdings (P4 step) → Consolidated row written,
         broker rows shadowed.
      3. Run HoldingsAggregator.apply_authority_rules (P5) → must NOT flip the
         Consolidated row to is_shadow=TRUE.

    FAILS before F1 fix (apply_authority_rules shadows Consolidated because
    'Consolidated' is not a member of the authority set for the asset).
    PASSES after: new WHEN source_system='Consolidated' THEN FALSE clause.
    """
    today = date.today()

    _insert_holding(
        connector,
        snapshot_date=today,
        asset_id="US_STK_SGOV",
        asset_name="iShares 0-3 Month Treasury Bond ETF",
        quantity=453.122,
        market_price_unit=100.0,
        market_value=45312.2,
        source_system="Schwab_CSV",
    )
    _insert_holding(
        connector,
        snapshot_date=today,
        asset_id="US_STK_SGOV",
        asset_name="iShares 0-3 Month Treasury Bond ETF",
        quantity=200.0,
        market_price_unit=100.0,
        market_value=20000.0,
        source_system="Broker_IBKR",
    )

    # P4: write Consolidated, shadow brokers
    shadowed = _consolidate_coauthority_holdings(connector, as_of_date=today)
    assert shadowed == 2, f"Expected 2 broker rows shadowed by P4, got {shadowed}"

    # Pre-condition: Consolidated row is active before P5
    cons_pre = connector.execute(
        "SELECT is_shadow FROM holdings WHERE asset_id='US_STK_SGOV' AND source_system='Consolidated' AND snapshot_date=?",
        (today,),
    ).fetchone()
    assert cons_pre is not None and cons_pre[0] is False, "Consolidated row must be active before P5"

    # P5: apply_authority_rules — THE BUG: this shadows Consolidated because it's not in the
    # resolved authority set {Schwab_CSV, Broker_IBKR}. After F1 fix it must leave it active.
    HoldingsAggregator(AuthorityResolver()).apply_authority_rules(connector, today)

    # ASSERT A1: Consolidated row is STILL active (is_shadow=FALSE)
    cons_post = connector.execute(
        "SELECT is_shadow FROM holdings WHERE asset_id='US_STK_SGOV' AND source_system='Consolidated' AND snapshot_date=?",
        (today,),
    ).fetchone()
    assert cons_post is not None, "Consolidated row must still exist after P5"
    assert cons_post[0] is False, (
        "F1 regression: apply_authority_rules shadowed the Consolidated row — "
        "fix: add WHEN source_system='Consolidated' THEN FALSE to is_shadow CASE"
    )

    # ASSERT A2: both broker rows remain shadowed
    for src in ("Schwab_CSV", "Broker_IBKR"):
        row = connector.execute(
            "SELECT is_shadow FROM holdings WHERE asset_id='US_STK_SGOV' AND source_system=? AND snapshot_date=?",
            (src, today),
        ).fetchone()
        assert row is not None and row[0] is True, f"{src} row must stay shadowed after P5"

    # ASSERT A3: production query `MAX(snapshot_date) WHERE is_shadow=FALSE GROUP BY asset_id`
    # picks the Consolidated row with the correct summed market_value
    picked = connector.execute(
        """
        WITH latest_per_asset AS (
            SELECT asset_id, MAX(snapshot_date) AS latest_date
            FROM holdings WHERE is_shadow = FALSE GROUP BY asset_id
        )
        SELECT h.source_system, h.market_value
        FROM holdings h
        JOIN latest_per_asset lpa ON h.asset_id = lpa.asset_id AND h.snapshot_date = lpa.latest_date
        WHERE h.asset_id = 'US_STK_SGOV' AND h.is_shadow = FALSE
        """
    ).fetchone()
    assert picked is not None, "SGOV must be visible via production GROUP BY query after P5"
    assert picked[0] == "Consolidated", (
        f"Production query must pick Consolidated row, got {picked[0]}"
    )
    assert float(picked[1]) == pytest.approx(65312.2), (
        f"Consolidated market_value must equal broker sum 65312.2, got {picked[1]}"
    )


# ---------------------------------------------------------------------------
# TEST B (F3 regression): same-day re-sync idempotency — ON CONFLICT must
# reset is_shadow=FALSE so re-ingested broker rows are active for the next P4.
# ---------------------------------------------------------------------------

def _make_broker_holdings_df(snapshot_date, schwab_qty=453.122, ibkr_qty=200.0):
    """Build a minimal holdings DataFrame for Schwab+IBKR SGOV rows."""
    rows = [
        {
            "snapshot_date": snapshot_date,
            "asset_id": "US_STK_SGOV",
            "asset_name": "iShares 0-3 Month Treasury Bond ETF",
            "asset_type": "ETF",
            "quantity": schwab_qty,
            "unit": "share",
            "cost_price_unit": 100.0,
            "market_price_unit": 100.0,
            "market_value": round(schwab_qty * 100.0, 4),
            "currency": "USD",
            "account": "Schwab Account",
            "source_system": "Schwab_CSV",
        },
        {
            "snapshot_date": snapshot_date,
            "asset_id": "US_STK_SGOV",
            "asset_name": "iShares 0-3 Month Treasury Bond ETF",
            "asset_type": "ETF",
            "quantity": ibkr_qty,
            "unit": "share",
            "cost_price_unit": 100.0,
            "market_price_unit": 100.0,
            "market_value": round(ibkr_qty * 100.0, 4),
            "currency": "USD",
            "account": "IBKR Account",
            "source_system": "Broker_IBKR",
        },
    ]
    return pd.DataFrame(rows)


def test_f3_same_day_resync_idempotency(connector):
    """F3 pipeline-integration test: same-day re-sync idempotency.

    Simulates two back-to-back sync runs at the SAME snapshot_date=today:
      Round 1: ingest → consolidate → broker rows become is_shadow=TRUE.
      Round 2: re-ingest (ON CONFLICT) → consolidate again.

    FAILS before F3 fix because ON CONFLICT does NOT reset is_shadow=FALSE —
    the broker rows stay shadowed, so the second _consolidate run finds 0 active
    broker rows and cannot rebuild the Consolidated row.

    PASSES after: ON CONFLICT adds `is_shadow = FALSE` to DO UPDATE SET, which
    restores broker rows to active so the next consolidation pass can pick them up.

    Final assertions (round 2 complete):
      - Exactly ONE active (is_shadow=FALSE) Consolidated row for SGOV.
      - Its market_value == sum of both broker market_values.
      - Both broker rows are is_shadow=TRUE.
      - SGOV visible via production GROUP BY query with summed market_value.
    """
    today = date.today()
    df = _make_broker_holdings_df(today)
    expected_broker_sum_mv = float(df["market_value"].sum())

    # ---- Round 1 ----
    _upsert_holdings(connector, df)
    shadowed_r1 = _consolidate_coauthority_holdings(connector, as_of_date=today)
    assert shadowed_r1 == 2, f"Round 1: expected 2 broker rows shadowed, got {shadowed_r1}"

    # Sanity: Consolidated row is active after round 1
    cons_r1 = connector.execute(
        "SELECT is_shadow, market_value FROM holdings WHERE asset_id='US_STK_SGOV' AND source_system='Consolidated' AND snapshot_date=?",
        (today,),
    ).fetchone()
    assert cons_r1 is not None and cons_r1[0] is False, "Consolidated must be active after round 1"

    # ---- Round 2: same snapshot_date, re-ingest same rows (ON CONFLICT path) ----
    _upsert_holdings(connector, df)  # ON CONFLICT fires — must reset is_shadow=FALSE

    # F3 BUG CHECK: if ON CONFLICT does NOT reset is_shadow, broker rows stay shadowed
    # and _consolidate finds 0 active broker rows. After fix, broker rows are active again.
    broker_shadow_states = connector.execute(
        "SELECT source_system, is_shadow FROM holdings WHERE asset_id='US_STK_SGOV' AND source_system IN ('Schwab_CSV', 'Broker_IBKR') AND snapshot_date=?",
        (today,),
    ).fetchall()
    # After fix, both must be is_shadow=FALSE (re-ingest restored them)
    for src, shadow in broker_shadow_states:
        assert shadow is False, (
            f"F3 regression: {src} broker row is still is_shadow=TRUE after re-ingest — "
            f"fix: add `is_shadow = FALSE` to ON CONFLICT DO UPDATE SET in _upsert_holdings"
        )

    shadowed_r2 = _consolidate_coauthority_holdings(connector, as_of_date=today)
    assert shadowed_r2 == 2, (
        f"Round 2: expected 2 broker rows re-shadowed by consolidation, got {shadowed_r2}"
    )

    # ---- Final assertions ----

    # ASSERT B1: exactly ONE active Consolidated row for SGOV today
    active_cons_count = connector.execute(
        "SELECT COUNT(*) FROM holdings WHERE asset_id='US_STK_SGOV' AND source_system='Consolidated' AND is_shadow=FALSE AND snapshot_date=?",
        (today,),
    ).fetchone()[0]
    assert active_cons_count == 1, (
        f"Must have exactly 1 active Consolidated row after round 2, got {active_cons_count}"
    )

    # ASSERT B2: market_value == broker sum
    cons_mv = float(connector.execute(
        "SELECT market_value FROM holdings WHERE asset_id='US_STK_SGOV' AND source_system='Consolidated' AND is_shadow=FALSE AND snapshot_date=?",
        (today,),
    ).fetchone()[0])
    assert cons_mv == pytest.approx(expected_broker_sum_mv), (
        f"Consolidated mv {cons_mv} must equal broker sum {expected_broker_sum_mv}"
    )

    # ASSERT B3: both broker rows are shadowed
    for src in ("Schwab_CSV", "Broker_IBKR"):
        row = connector.execute(
            "SELECT is_shadow FROM holdings WHERE asset_id='US_STK_SGOV' AND source_system=? AND snapshot_date=?",
            (src, today),
        ).fetchone()
        assert row is not None and row[0] is True, f"{src} must be shadowed after round 2"

    # ASSERT B4: production GROUP BY query returns Consolidated with summed mv
    picked = connector.execute(
        """
        WITH latest_per_asset AS (
            SELECT asset_id, MAX(snapshot_date) AS latest_date
            FROM holdings WHERE is_shadow = FALSE GROUP BY asset_id
        )
        SELECT h.source_system, h.market_value
        FROM holdings h
        JOIN latest_per_asset lpa ON h.asset_id = lpa.asset_id AND h.snapshot_date = lpa.latest_date
        WHERE h.asset_id = 'US_STK_SGOV' AND h.is_shadow = FALSE
        """
    ).fetchone()
    assert picked is not None, "SGOV must be visible via production GROUP BY after round 2"
    assert picked[0] == "Consolidated", f"Expected Consolidated, got {picked[0]}"
    assert float(picked[1]) == pytest.approx(expected_broker_sum_mv)
