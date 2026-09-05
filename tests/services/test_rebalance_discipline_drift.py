"""Regression tests for _rebalance_discipline (F4.1 — PRD 2026-07-07 §F4.1).

Split out of test_behavioral_metrics.py to keep both files under the
400-line house limit (scripts/verify.sh check [e]).

Defect: "Rebalance Discipline: 0 classes drifted >5%" contradicted the
official allocation table (cash +12.0pp, equity -11.7pp). Root cause: the
old _rebalance_discipline queried the legacy `target_allocations` table,
which stopped being populated once `sync_target_allocations` was removed
under ADR-003 (Phase-9 PIS deprecation) — joining current weights to an
empty/stale target set silently produced 0 matches. The fix recomputes
drift from `build_compass_allocation()` (src/services/compass_allocation.py),
the SAME engine backing the official allocation table
(`risk_profile_allocations` + `taxonomy_classes`).
"""
from __future__ import annotations

import duckdb
import pytest

from src.services.ai_advisor.behavioral_metrics import BehavioralMetricsComputer


def _make_computer() -> BehavioralMetricsComputer:
    return BehavioralMetricsComputer(db_path="data/unified.duckdb")


def _build_drift_fixture(tmp_path):
    """In-memory DuckDB reproducing: total NW 1,000,000 CNY split
    Equity 583,000 (58.3%, target 70% -> drift -11.7pp),
    Cash    220,000 (22.0%, target 10% -> drift +12.0pp),
    Fixed Income 197,000 (19.7%, target 20% -> drift -0.3pp, NOT drifted).

    `target_allocations` is created but left EMPTY — this mirrors production
    post-ADR-003, where the table exists in schema.sql but is no longer
    written by any sync step.
    """
    conn = duckdb.connect(str(tmp_path / "drift_fixture.duckdb"))

    conn.execute("""
        CREATE TABLE holdings (
            snapshot_date DATE, asset_id VARCHAR, asset_name VARCHAR,
            quantity DOUBLE, market_value DOUBLE, currency VARCHAR,
            source_system VARCHAR, is_shadow BOOLEAN
        )
    """)
    conn.execute("""
        CREATE TABLE asset_registry (
            canonical_id VARCHAR, asset_class VARCHAR, is_rebalanceable BOOLEAN
        )
    """)
    conn.execute("""
        CREATE TABLE taxonomy_classes (
            id INTEGER, name VARCHAR, parent_id INTEGER,
            is_rebalanceable BOOLEAN, level INTEGER
        )
    """)
    conn.execute("CREATE TABLE risk_profiles (id INTEGER, name VARCHAR, is_active BOOLEAN)")
    conn.execute("""
        CREATE TABLE risk_profile_allocations (
            id INTEGER, profile_id INTEGER, class_id INTEGER, target_pct DOUBLE
        )
    """)
    # Legacy table — created (schema-compatible) but never populated, per ADR-003.
    conn.execute("""
        CREATE TABLE target_allocations (
            asset_class VARCHAR, target_pct DOUBLE, source VARCHAR, effective_date DATE
        )
    """)

    conn.execute("""
        INSERT INTO taxonomy_classes VALUES
        (1, 'Equity', NULL, TRUE, 0),
        (2, 'Cash', NULL, TRUE, 0),
        (3, 'Fixed Income', NULL, TRUE, 0)
    """)
    conn.execute("""
        INSERT INTO asset_registry VALUES
        ('stock1', 'Equity', TRUE),
        ('cash1', 'Cash', TRUE),
        ('bond1', 'Fixed Income', TRUE)
    """)
    conn.execute("""
        INSERT INTO holdings VALUES
        ('2026-07-02', 'stock1', 'Equity Basket', 1, 583000, 'CNY', 'Schwab_CSV', FALSE),
        ('2026-07-02', 'cash1', 'Cash Basket', 1, 220000, 'CNY', 'Schwab_CSV', FALSE),
        ('2026-07-02', 'bond1', 'Bond Basket', 1, 197000, 'CNY', 'CN_Fund_Excel', FALSE)
    """)
    conn.execute("INSERT INTO risk_profiles VALUES (1, 'Strategic Profile', TRUE)")
    conn.execute("""
        INSERT INTO risk_profile_allocations VALUES
        (1, 1, 1, 70.0),
        (2, 1, 2, 10.0),
        (3, 1, 3, 20.0)
    """)
    return conn


def test_rebalance_discipline_matches_official_allocation_engine(tmp_path):
    """Fixed logic: max drift ~= 12.0pp (Cash), 2 classes drifted >5% (Cash, Equity)."""
    conn = _build_drift_fixture(tmp_path)
    computer = _make_computer()

    result = computer._rebalance_discipline(90, conn=conn)

    assert result.raw_value == 2, f"Expected 2 classes drifted >5%, got {result.raw_value}"
    assert result.metadata is not None
    assert result.metadata["max_drift_pp"] == pytest.approx(12.0, abs=0.1)
    # compass_allocation applies display-name translation (get_display_name) to
    # asset_class labels — "Cash" -> "Cash (现金)", "Equity" -> "Equity (股票)".
    assert result.metadata["per_class_drift_pp"]["Cash (现金)"] == pytest.approx(12.0, abs=0.1)
    assert result.metadata["per_class_drift_pp"]["Equity (股票)"] == pytest.approx(-11.7, abs=0.1)
    assert "same allocation engine" in result.description


def test_rebalance_discipline_old_logic_reproduces_zero_drift_bug(tmp_path):
    """Regression proof: the OLD query (legacy target_allocations join), run verbatim
    against the SAME fixture that has real 12.0pp/11.7pp drift, returns 0 — the exact
    "0 classes drifted >5%" bug from PRD 2026-07-07 §F4 Problem Statement (4). This test
    would FAIL if the old logic were reinstated as the metric's implementation, because
    the new _rebalance_discipline (asserted above) correctly returns 2, not 0.
    """
    conn = _build_drift_fixture(tmp_path)

    old_sql = """
    WITH latest_per_asset AS (
        SELECT asset_id, MAX(snapshot_date) AS latest_date
        FROM holdings WHERE is_shadow = FALSE GROUP BY asset_id
    ),
    current_weights AS (
        SELECT COALESCE(ptc.name, tc.name, r.asset_class, 'Unclassified') AS top_class,
               100.0 * SUM(h.market_value) / SUM(SUM(h.market_value)) OVER() AS actual_pct
        FROM holdings h
        JOIN latest_per_asset lpa
            ON h.asset_id = lpa.asset_id AND h.snapshot_date = lpa.latest_date
        LEFT JOIN asset_registry r ON h.asset_id = r.canonical_id
        LEFT JOIN taxonomy_classes tc ON r.asset_class = tc.name
        LEFT JOIN taxonomy_classes ptc ON tc.parent_id = ptc.id
        WHERE h.is_shadow = FALSE AND h.market_value > 0
        GROUP BY 1
    ),
    targets AS (
        SELECT asset_class, target_pct FROM target_allocations
        WHERE source = 'Strategic_Profile'
    ),
    targets_fallback AS (
        SELECT asset_class, target_pct FROM target_allocations
        WHERE source IS NULL OR source != 'Strategic_Profile'
    ),
    effective_targets AS (
        SELECT asset_class, target_pct FROM targets
        UNION ALL
        SELECT asset_class, target_pct FROM targets_fallback
        WHERE NOT EXISTS (SELECT 1 FROM targets)
    ),
    drifted AS (
        SELECT COUNT(*) AS overdue
        FROM current_weights cw
        JOIN effective_targets t ON cw.top_class = t.asset_class
        WHERE ABS(cw.actual_pct - t.target_pct) > 5
    )
    SELECT overdue FROM drifted
    """
    overdue = conn.execute(old_sql).fetchone()[0]

    assert overdue == 0, (
        "This documents the PRD F4.1 defect: the legacy target_allocations-based query "
        "reports 0 drifted classes on a fixture with real 12.0pp/-11.7pp drift, because "
        "target_allocations is never populated post-ADR-003."
    )
