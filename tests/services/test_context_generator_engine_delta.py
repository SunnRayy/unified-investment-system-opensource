"""Documented-delta gate for the P&L-engine migration of ``context_generator``.

Release 1 / Step 6 of the P&L unification (docs/plans/2026-08-02-pnl-unification-
and-manual-cost.md §B.3). The AI markdown export's three P&L sites (tier table,
performance-summary cost map, holdings-detail by-class) now derive per-asset
cost/unrealized/realized from ``compute_portfolio_pnl`` instead of the local
``_cost_or_balance_only`` helper (deleted).

Unlike the other six migrated surfaces, this one is a **documented correction, not
byte-parity**: the old export charged a balance-only asset at ``cost = market_value``
(0 gain but RETAINED in every cost/return denominator). The engine EXCLUDES
balance-only assets from the gain aggregates (cost/unrealized = None) — the V7.8.3
rule the dashboards already apply, now finally reaching the LLM export.

These tests assert the **expected delta** (not equality):

- a balance-only bond in a class KEEPS its market value / weight, but contributes
  0 cost and 0 unrealized and drops out of the cost/return denominator;
- a control class (all non-balance-only) is unchanged;
- a mutation guard flips the delta back to the old cost=value phantom, proving the
  assertions are not vacuous.

Fixture (all CNY so FX is irrelevant; FX pinned anyway for determinism):
    EQ_A         Equity/US Equity   traded        mv100000 cost80000 unreal+20000
    BOND_TRADED  Fixed Income/CN B  traded        mv 50000 cost48000 unreal +2000
    BOND_BAL     Fixed Income/CN B  balance-only  mv200000 cost  —   unreal   —   (delta driver)
    MM_CASH      Money Market       cash          mv 20000 cost20000 unreal     0
"""
import types

import duckdb
import pytest

from src.database.connector import DatabaseConnector
from src.services.context_generator import MarkdownContextGenerator

FIXED_FX = 7.1


def _seed(path):
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
        "CREATE TABLE asset_registry (canonical_id VARCHAR, display_name VARCHAR, "
        "asset_class VARCHAR, is_rebalanceable BOOLEAN, tier VARCHAR)"
    )
    conn.execute(
        "CREATE TABLE taxonomy_classes (id INTEGER, name VARCHAR, name_cn VARCHAR, "
        "parent_id INTEGER, is_rebalanceable BOOLEAN)"
    )
    conn.execute(
        "CREATE TABLE asset_tiers (name VARCHAR, target_pct DOUBLE, sort_order INTEGER)"
    )
    # Empty risk-profile tables so section 1's target-map query has something to hit.
    conn.execute("CREATE TABLE risk_profiles (id INTEGER, is_active BOOLEAN)")
    conn.execute(
        "CREATE TABLE risk_profile_allocations (profile_id INTEGER, class_id INTEGER, target_pct DOUBLE)"
    )
    conn.execute(
        """INSERT INTO taxonomy_classes VALUES
        (1,'Fixed Income','固定收益',NULL,TRUE),
        (2,'CN Bonds','中国债券',1,TRUE),
        (3,'Equity','股票',NULL,TRUE),
        (4,'US Equity','美股',3,TRUE),
        (10,'Money Market','货基',NULL,TRUE)"""
    )
    conn.execute(
        """INSERT INTO asset_registry VALUES
        ('EQ_A','美股基金','US Equity',TRUE,'Tier 1 Core'),
        ('BOND_TRADED','交易债','CN Bonds',TRUE,'Tier 2 Bonds'),
        ('BOND_BAL','招行固收债券','CN Bonds',TRUE,'Tier 2 Bonds'),
        ('MM_CASH','货币基金','Money Market',TRUE,'Tier 3 Cash')"""
    )
    conn.execute(
        """INSERT INTO asset_tiers VALUES
        ('Tier 1 Core', 50.0, 1),
        ('Tier 2 Bonds', 30.0, 2),
        ('Tier 3 Cash', 20.0, 3)"""
    )
    conn.execute(
        """INSERT INTO holdings VALUES
        ('EQ_A','美股基金','Schwab_CSV',100000.0,80.0,100.0,1000.0,'CNY',DATE '2026-07-01',FALSE),
        ('BOND_TRADED','交易债','CN_Fund_Excel',50000.0,48.0,50.0,1000.0,'CNY',DATE '2026-07-01',FALSE),
        ('BOND_BAL','招行固收债券','Financial_Summary_Excel',200000.0,NULL,200000.0,1.0,'CNY',DATE '2026-07-01',FALSE),
        ('MM_CASH','货币基金','CN_Fund_Excel',20000.0,1.0,1.0,20000.0,'CNY',DATE '2026-07-01',FALSE)"""
    )
    conn.execute(
        """INSERT INTO transactions VALUES
        ('EQ_A','美股基金','buy',1000.0,80.0,80000.0,'CNY',DATE '2025-01-15','Schwab_CSV',FALSE),
        ('BOND_TRADED','交易债','buy',1000.0,48.0,48000.0,'CNY',DATE '2025-02-10','CN_Fund_Excel',FALSE)"""
    )
    conn.close()


@pytest.fixture
def frozen_fx(monkeypatch):
    """Pin the USD→CNY rate everywhere the export touches it (deterministic)."""
    import src.services.pnl.engine as engine_mod
    import src.services.context_generator as cg_mod

    monkeypatch.setattr(engine_mod, "get_today_usd_cny_rate", lambda: FIXED_FX)
    monkeypatch.setattr(cg_mod, "get_today_usd_cny_rate", lambda: FIXED_FX)


@pytest.fixture
def gen(tmp_path, frozen_fx, monkeypatch):
    import src.services.context_generator as cg_mod

    # Keep section 2.3 (TWR/XIRR/risk) off the fixture — it needs market_daily.
    monkeypatch.setattr(cg_mod, "calculate_portfolio_twr",
                        lambda *a, **k: {"cumulative": 0.0, "annualized": 0.0})
    monkeypatch.setattr(cg_mod, "calculate_portfolio_xirr", lambda *a, **k: 0.0)
    monkeypatch.setattr(cg_mod, "calculate_portfolio_metrics", lambda *a, **k: {})

    db_path = tmp_path / "ctx_delta.duckdb"
    _seed(db_path)
    db = DatabaseConnector(str(db_path))
    generator = MarkdownContextGenerator(db)
    try:
        yield generator
    finally:
        db.close()


# ── The crux: engine cost excludes the balance-only bond ──────────────────────
def test_engine_cost_excludes_balance_only_bond(gen):
    # Balance-only bond: cost is UNKNOWN (None) — not charged at market value.
    assert gen._engine_cost("BOND_BAL") is None
    # Everything else keeps its real cost, identical to the pre-engine value.
    assert gen._engine_cost("BOND_TRADED") == pytest.approx(48000.0)
    assert gen._engine_cost("EQ_A") == pytest.approx(80000.0)
    assert gen._engine_cost("MM_CASH") == pytest.approx(20000.0)  # cash: cost == value


# ── Section 2.1 — total cost basis drops the bond's ¥200,000 ──────────────────
def test_section_2_1_total_cost_basis_excludes_bond(gen):
    section = gen._section_2_performance()
    # Value still counts the bond; cost basis does NOT.
    assert "| Market Value | ¥370,000 | ¥370,000 |" in section
    assert "| Cost Basis | ¥148,000 | ¥148,000 |" in section       # 80k+48k+20k, NOT +200k
    assert "| Unrealized P&L | ¥22,000 | ¥22,000 |" in section     # unchanged by the delta
    # The old cost=value phantom (148k + 200k) must be gone.
    assert "¥348,000" not in section


# ── Section 2.2 — Fixed-Income return uses the reduced denominator ────────────
def test_section_2_2_fixed_income_denominator_excludes_bond(gen):
    section = gen._section_2_performance()
    # Fixed Income: value + unrealized unchanged, but Lifetime Return % is now
    # 2000/48000 = 4.17% (bond excluded from the denominator), NOT 2000/248000 = 0.81%.
    assert "| Fixed Income | ¥250,000 | 67.57% | ¥2,000 | ¥0 | ¥2,000 | 4.17% | 2 |" in section
    assert "0.81%" not in section
    # Control class (no balance-only asset) is entirely unchanged.
    assert "| Equity | ¥100,000 | 27.03% | ¥20,000 | ¥0 | ¥20,000 | 25.00% | 1 |" in section


# ── Section 4 — bond shows real value but no phantom gain ─────────────────────
def test_section_4_bond_shows_value_no_phantom_gain(gen):
    section = gen._section_4_holdings_detail()
    # Per-asset: real market value, but cost / unrealized / return are "—".
    assert "| 招行固收债券 | BOND_BAL | CN Bonds | ¥200,000 | — | — | ¥0 | ¥0 | — | 54.05% |" in section
    # Class header: denominator excludes the bond → 4.17%, not the old 0.81%.
    assert (
        "*Market Value: ¥250,000 | Weight: 67.57% | Unrealized: ¥2,000 | "
        "Realized: ¥0 | Lifetime: ¥2,000 (4.17%)*" in section
    )
    # Control: the traded assets are unchanged (real cost + gain shown).
    assert "| 交易债 | BOND_TRADED | CN Bonds | ¥50,000 | ¥48,000 | ¥2,000 | ¥0 | ¥2,000 | 4.17% | 13.51% |" in section
    assert "| 美股基金 | EQ_A | US Equity | ¥100,000 | ¥80,000 | ¥20,000 | ¥0 | ¥20,000 | 25.00% | 27.03% |" in section


# ── Section 1.4 tier — balance-only bond adds value but 0 unrealized ──────────
def test_section_1_4_tier_not_inflated_by_balance_only_bond(gen):
    section = gen._section_1_portfolio_state()
    # Tier 2 holds BOND_TRADED (+2000) and BOND_BAL (excluded). Its Unrealized P&L
    # is ¥2,000 — NOT ¥202,000 (which a "charge balance-only at cost=0" regression
    # would produce). Value still reflects the full ¥250,000.
    assert "| Tier 2 Bonds | ¥250,000 |" in section
    assert "| ¥2,000 | 2 |" in section
    assert "¥202,000" not in section


# ── Mutation guard — the delta assertions are not vacuous ─────────────────────
def test_mutation_guard_charging_bond_restores_phantom(gen):
    """Force ``_engine_cost`` back to the OLD cost=value behavior for the bond and
    confirm the export re-inflates: the cost basis returns to ¥348,000 and the
    Fixed-Income denominator swells again. If the migration ever silently reverted
    to charging balance-only at market value, the real tests above would still be
    green only because THIS guard proves they move when the behavior moves."""
    orig = MarkdownContextGenerator._engine_cost

    def phantom(self, aid):
        if aid == "BOND_BAL":
            return 200000.0  # the pre-engine cost = market_value phantom
        return orig(self, aid)

    gen._engine_cost = types.MethodType(phantom, gen)
    section = gen._section_2_performance()
    # The phantom denominator is back — proving the exclusion is what drives the delta.
    assert "| Cost Basis | ¥348,000 | ¥348,000 |" in section
    assert "| Cost Basis | ¥148,000 | ¥148,000 |" not in section
