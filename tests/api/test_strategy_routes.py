import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.classification.schema import create_classification_tables
from src.database.connector import DatabaseConnector
from src.database.schema import initialize_schema
from src.api.routes.strategy import router as strategy_router


app = FastAPI()
app.include_router(strategy_router)


@pytest.fixture
def strategy_client():
    from src.api.dependencies import get_db

    conn = DatabaseConnector(":memory:")
    initialize_schema(conn)
    create_classification_tables(conn)
    # V4.5 migration: add content column so GET /memos works
    conn.execute("ALTER TABLE strategy_memos ADD COLUMN IF NOT EXISTS content TEXT")

    def override_get_db():
        return conn

    app.dependency_overrides[get_db] = override_get_db
    yield TestClient(app)
    app.dependency_overrides.clear()
    conn.close()


def test_get_strategy_alignment_and_memos(strategy_client):
    from src.api.dependencies import get_db

    conn = app.dependency_overrides[get_db]()
    conn.execute(
        """
        INSERT INTO strategy_review_reports (
            review_date, allocation_alignment, trading_frequency, contrarian_score,
            contrarian_details, profile_discrepancies, overall_alignment
        ) VALUES (
            '2026-03-16', ?, ?, 88.8, ?, ?, 'aligned'
        )
        """,
        (
            json.dumps({
                "report_version": "2026-05-21-scope-v3",
                "target_scope_alignment": {"US Equity": {"actual_pct": 30.0, "target_pct": 30.0, "drift_pct": 0.0, "status": "aligned"}},
                "uis_scope_alignment": {"Equity": {"actual_pct": 60.0, "target_pct": 65.0, "drift_pct": -5.0, "status": "aligned"}},
                "target_scope_summary": {"included_classes": ["US Equity"], "excluded_classes": ["Commodity"], "coverage_note": "Strategic note"},
                "uis_scope_summary": {"included_classes": ["Equity"], "coverage_note": "Huinsight note"},
                "target_scope_alignment_status": "aligned",
                "uis_scope_alignment_status": "aligned"
            }),
            json.dumps({"period_30d": 2}),
            json.dumps({"sell_count": 1, "status": "ok"}),
            json.dumps({"target_only": [], "uis_only": [], "both": ["US Equity"]}),
        ),
    )
    conn.execute(
        """
        INSERT INTO strategy_memos (memo_date, title, strategic_bias, key_directives, source_file)
        VALUES ('2026-03-09', 'Weekly', 'defensive', ?, '/tmp/a.md')
        """,
        (json.dumps(["Reduce risk"]),),
    )

    r1 = strategy_client.get("/strategy/alignment")
    r2 = strategy_client.get("/strategy/memos")

    assert r1.status_code == 200
    assert "allocation_alignment" not in r1.json()["report"]
    assert r1.json()["report"]["target_scope_alignment_status"] == "aligned"
    assert r1.json()["report"]["uis_scope_alignment"]["Equity"]["target_pct"] == 65.0
    assert r2.status_code == 200
    assert len(r2.json()["memos"]) == 1


def test_get_strategy_alignment_includes_behavioral_summary(strategy_client):
    from src.api.dependencies import get_db

    conn = app.dependency_overrides[get_db]()
    conn.execute(
        """
        CREATE TABLE ai_behavioral_log (
            id INTEGER,
            dimension TEXT,
            score DOUBLE,
            raw_value DOUBLE,
            computation_window_days INTEGER,
            metadata_json TEXT,
            computed_at TIMESTAMP
        )
        """
    )
    conn.execute(
        """
        INSERT INTO ai_behavioral_log
            (id, dimension, score, raw_value, computation_window_days, metadata_json, computed_at)
        VALUES
            (1, 'contrarian_tendency', 0.25, 25.0, 90, ?, '2026-03-19 10:00:00'),
            (2, 'contrarian_tendency', 0.85, 85.0, 90, ?, '2026-03-20 10:00:00'),
            (3, 'position_sizing_discipline', 0.60, 60.0, 90, ?, '2026-03-20 09:30:00'),
            (4, 'decision_speed', 0.70, 70.0, 90, ?, '2026-03-20 09:31:00'),
            (5, 'loss_tolerance', 0.40, 40.0, 90, ?, '2026-03-20 09:32:00'),
            (6, 'strategy_compliance', 0.90, 90.0, 90, ?, '2026-03-20 09:33:00'),
            (7, 'rebalance_discipline', 0.55, 55.0, 90, ?, '2026-03-20 09:34:00')
        """
        ,
        (
            json.dumps({"label": "Older contrarian label", "description": "older"}),
            json.dumps({"label": "Latest contrarian label", "description": "latest"}),
            json.dumps({"label": "Sizing label", "description": "sizing"}),
            json.dumps({"label": "Speed label", "description": "speed"}),
            json.dumps({"label": "Loss label", "description": "loss"}),
            json.dumps({"label": "Strategy label", "description": "strategy"}),
            json.dumps({"label": "Rebalance label", "description": "rebalance"}),
        ),
    )
    conn.execute(
        """
        INSERT INTO strategy_review_reports (
            review_date, allocation_alignment, trading_frequency, contrarian_score,
            contrarian_details, profile_discrepancies, overall_alignment
        ) VALUES (
            '2026-03-20', ?, ?, 88.8, ?, ?, 'aligned'
        )
        """,
        (
            json.dumps({
                "report_version": "2026-05-21-scope-v3",
                "target_scope_alignment": {"US Equity": {"actual_pct": 30.0, "target_pct": 30.0, "drift_pct": 0.0, "status": "aligned"}},
                "uis_scope_alignment": {"Equity": {"actual_pct": 60.0, "target_pct": 65.0, "drift_pct": -5.0, "status": "aligned"}},
                "target_scope_summary": {"included_classes": ["US Equity"], "excluded_classes": ["Commodity"], "coverage_note": "Strategic note"},
                "uis_scope_summary": {"included_classes": ["Equity"], "coverage_note": "Huinsight note"},
                "target_scope_alignment_status": "aligned",
                "uis_scope_alignment_status": "aligned"
            }),
            json.dumps({"period_30d": 2}),
            json.dumps({"sell_count": 1, "status": "ok"}),
            json.dumps({"target_only": [], "uis_only": [], "both": ["US Equity"]}),
        ),
    )

    response = strategy_client.get("/strategy/alignment")

    assert response.status_code == 200
    report = response.json()["report"]
    assert "behavioral_summary" in report
    assert report["behavioral_summary"]["contrarian_tendency"]["label"] == "Latest contrarian label"
    assert report["behavioral_summary"]["contrarian_tendency"]["score"] == 0.85
    assert len(report["behavioral_summary"]) == 6


def test_get_strategy_alignment_refreshes_stale_semantic_version(strategy_client, monkeypatch):
    from src.api.dependencies import get_db
    from src.services import strategy_reviewer

    conn = app.dependency_overrides[get_db]()
    conn.execute(
        """
        INSERT INTO strategy_review_reports (
            review_date, allocation_alignment, trading_frequency, contrarian_score,
            contrarian_details, profile_discrepancies, overall_alignment
        ) VALUES (
            '2026-03-18', ?, ?, 100.0, ?, ?, 'aligned'
        )
        """,
        (
            json.dumps({
                "report_version": "2026-03-19-scope-v1",
                "target_scope_alignment": {"US Equity": {"actual_pct": 10.0, "target_pct": 30.0, "drift_pct": -20.0, "status": "drifting"}},
                "uis_scope_alignment": {"Equity": {"actual_pct": 10.0, "target_pct": 65.0, "drift_pct": -55.0, "status": "drifting"}},
                "target_scope_summary": {"included_classes": ["US Equity"], "excluded_classes": [], "coverage_note": "old"},
                "uis_scope_summary": {"included_classes": ["Equity"], "coverage_note": "old"},
                "target_scope_alignment_status": "drifting",
                "uis_scope_alignment_status": "drifting",
            }),
            json.dumps({"period_30d": 99}),
            json.dumps({"sell_count": 50, "status": "insufficient_market_context"}),
            json.dumps({"target_only": ["US Equity"], "uis_only": ["Equity"], "both": []}),
        ),
    )

    refreshed_report = {
        "review_date": "2026-03-19",
        "target_scope_alignment": {"US Equity": {"actual_pct": 30.0, "target_pct": 30.0, "drift_pct": 0.0, "status": "aligned"}},
        "uis_scope_alignment": {"Equity": {"actual_pct": 60.0, "target_pct": 65.0, "drift_pct": -5.0, "status": "aligned"}},
        "target_scope_summary": {"included_classes": ["US Equity"], "excluded_classes": ["Commodity"], "coverage_note": "fresh"},
        "uis_scope_summary": {"included_classes": ["Equity"], "coverage_note": "fresh"},
        "target_scope_alignment_status": "aligned",
        "uis_scope_alignment_status": "aligned",
        "trading_frequency": {"period_30d": 5, "period_60d": 22, "period_90d": 23},
        "contrarian_score": None,
        "contrarian_details": {"status": "insufficient_market_context", "sell_count": 10},
        "profile_discrepancies": {"target_only": [], "uis_only": ["Alternative", "Commodity"], "both": ["Cash", "Equity", "Fixed Income"]},
    }

    monkeypatch.setattr(strategy_reviewer, "generate_strategy_report", lambda db: refreshed_report)

    response = strategy_client.get("/strategy/alignment")

    assert response.status_code == 200
    assert response.json()["report"]["trading_frequency"]["period_60d"] == 22
    assert response.json()["report"]["profile_discrepancies"]["target_only"] == []
    assert response.json()["report"]["target_scope_summary"]["coverage_note"] == "fresh"


def test_get_strategy_targets(strategy_client):
    from src.api.dependencies import get_db

    conn = app.dependency_overrides[get_db]()
    conn.execute(
        """
        INSERT INTO target_allocations (asset_class, target_pct, tolerance_pct, taxonomy_type, source, effective_date)
        VALUES
          ('US Equity', 35, 5, 'Asset Class', 'Strategic_Profile', '2026-03-01'),
          ('US Equity', 50, 5, 'Asset Class', NULL, '2026-03-02')
        """
    )

    resp = strategy_client.get("/strategy/targets")
    data = resp.json()
    assert resp.status_code == 200
    assert len(data["strategic_profile"]) == 1
    assert len(data["uis_profile"]) == 1


def test_post_strategy_review(strategy_client):
    from src.api.dependencies import get_db

    conn = app.dependency_overrides[get_db]()
    conn.execute("INSERT INTO taxonomy_classes (id, name, parent_id, level, sort_order) VALUES (1, 'Equity', NULL, 0, 1), (2, 'Fixed Income', NULL, 0, 2), (3, 'Cash', NULL, 0, 3), (4, 'Alternative', NULL, 0, 4), (5, 'CN Equity', 1, 1, 1), (6, 'US Equity', 1, 1, 2), (7, 'US Bonds', 2, 1, 1), (8, 'Cash Checking', 3, 1, 1), (9, 'Crypto', 4, 1, 1)")
    conn.execute("INSERT INTO risk_profiles (id, name, is_active) VALUES (1, '均衡型', TRUE)")
    conn.execute("INSERT INTO risk_profile_allocations (id, profile_id, class_id, target_pct) VALUES (1, 1, 5, 35), (2, 1, 6, 20), (3, 1, 7, 15), (4, 1, 8, 2), (5, 1, 9, 7)")
    conn.execute(
        """
        INSERT INTO asset_registry (canonical_id, display_name, asset_class)
        VALUES
          ('US_STK_AAPL', 'Apple', 'US Equity'),
          ('US_STK_IBIT', 'iShares Bitcoin ETF', 'Crypto'),
          ('US_STK_SGOV', 'SGOV', 'US Bonds'),
          ('CASH_USD', 'Cash', 'Cash Checking')
        """
    )
    conn.execute(
        """
        INSERT INTO holdings (snapshot_date, asset_id, market_value, source_system, is_shadow)
        VALUES
          ('2026-03-10', 'US_STK_AAPL', 100, 'Schwab_CSV', FALSE),
          ('2026-03-10', 'US_STK_IBIT', 50, 'Schwab_CSV', FALSE),
          ('2026-03-10', 'US_STK_SGOV', 60, 'Schwab_CSV', FALSE),
          ('2026-03-10', 'CASH_USD', 20, 'Schwab_CSV', FALSE)
        """
    )
    conn.execute(
        """
        INSERT INTO target_allocations (asset_class, target_pct, tolerance_pct, taxonomy_type, source, effective_date)
        VALUES ('US Equity', 35, 5, 'Asset Class', 'Strategic_Profile', '2026-03-01')
        """
    )
    conn.execute(
        """
        INSERT INTO target_allocations (asset_class, target_pct, tolerance_pct, taxonomy_type, source, effective_date)
        VALUES ('US Equity', 40, 5, 'Asset Class', NULL, '2026-03-02')
        """
    )

    resp = strategy_client.post("/strategy/review")
    assert resp.status_code == 200
    assert "overall_alignment" not in resp.json()["report"]
    assert "target_scope_alignment" in resp.json()["report"]
    assert "uis_scope_alignment" in resp.json()["report"]
    assert "target_scope_alignment_status" in resp.json()["report"]
