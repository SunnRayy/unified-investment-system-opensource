# tests/api/test_performance_values.py
"""Validate performance endpoint values are from latest snapshot only."""
from fastapi.testclient import TestClient
from src.api.main import app

client = TestClient(app)

def test_summary_net_worth_not_inflated():
    """Net worth should be ~5.5M (latest snapshot), NOT ~22M (all snapshots)."""
    response = client.get("/performance/summary")
    assert response.status_code == 200
    data = response.json()

    net_worth = data["net_worth"]
    # Should be approximately 5.5M from latest snapshot
    # Must NOT be ~22M (4 snapshots summed)
    assert net_worth < 10_000_000, (
        f"Net worth {net_worth:,.2f} appears inflated. "
        f"Expected ~5.5M (latest snapshot), got value suggesting multiple snapshots summed."
    )
    assert net_worth > 1_000_000, f"Net worth {net_worth:,.2f} is suspiciously low"

def test_summary_snapshot_date_is_latest():
    """Snapshot date should be the most recent date."""
    response = client.get("/performance/summary")
    data = response.json()
    # Should be 2026-01-29 (latest sync)
    assert data["snapshot_date"] is not None
    assert data["snapshot_date"] >= "2026-01-28"

def test_gains_market_value_not_inflated():
    """Total market value in gains should match summary net worth."""
    summary = client.get("/performance/summary").json()
    gains = client.get("/performance/gains").json()

    # Both should report same total market value
    # Allow small precision diff
    assert abs(summary["net_worth"] - gains["total_market_value"]) < 1.0, (
        f"Summary net_worth ({summary['net_worth']:,.2f}) != "
        f"Gains total_market_value ({gains['total_market_value']:,.2f})"
    )

def test_by_class_weights_sum_to_100():
    """Class weights should sum to ~100%."""
    response = client.get("/performance/by-class")
    data = response.json()

    total_weight = sum(c["weight_pct"] for c in data["top_classes"])
    assert 99.0 <= total_weight <= 101.0, (
        f"Class weights sum to {total_weight:.1f}%, expected ~100%"
    )

def test_by_class_total_matches_summary():
    """By-class total should match summary net worth."""
    summary = client.get("/performance/summary").json()
    by_class = client.get("/performance/by-class").json()

    assert abs(summary["net_worth"] - by_class["total_market_value"]) < 1.0, (
        f"Summary net_worth ({summary['net_worth']:,.2f}) != "
        f"By-class total ({by_class['total_market_value']:,.2f})"
    )
