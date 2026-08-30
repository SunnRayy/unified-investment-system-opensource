"""Tests for src/services/value_trap.py (PRD 2026-07-07 F2.1/F2.2, Batch B3).

Uses an in-memory DuckDB initialized from the real schema.sql (never a bare,
schema-less connector — see CLAUDE.md Database Safety Rules).
Fixture asset ids are chosen to exercise config/verification.yaml's default
bucket_map: 'RSU_AMZN'/'900009' -> compliance, 'GOLD'/'ALTS_Paper_Gold'/
'IBIT'/'FBTC' -> ratio, everything else -> value (see
tests/services/test_rule_buckets.py for the same patterns).
"""
from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Optional

from src.database.connector import DatabaseConnector
from src.database.schema import initialize_schema
from src.services.value_trap import scan_value_traps
from src.services.verification_config import (
    BucketMapEntry,
    ValueTrapSection,
    VerificationConfig,
)


def _make_db() -> DatabaseConnector:
    conn = DatabaseConnector(":memory:")
    initialize_schema(conn)
    return conn


def _insert_holding(
    conn: DatabaseConnector,
    asset_id: str,
    name: str,
    loss_pct: float,
    *,
    # Dynamic near-today default — a hardcoded date would age past the 7-day
    # staleness window and silently flip tests to deferred_unreliable.
    snapshot_date: Optional[str] = None,
    cost_price_unit: float = 10.0,
    quantity: float = 1000.0,
    currency: str = "CNY",
    price_updated_at: Optional[str] = None,
) -> None:
    """Insert one non-shadow holdings row whose CNY market_value implies the
    given lifetime unrealized loss_pct against cost_price_unit * quantity.

    ``price_updated_at`` accepts an ISO datetime string (or None) to test
    the F4.4 valuation-freshness logic — staleness is keyed on
    MAX(snapshot_date, price_updated_at).
    """
    if snapshot_date is None:
        snapshot_date = (date.today() - timedelta(days=1)).isoformat()
    market_value = round(quantity * cost_price_unit * (1 + loss_pct / 100.0), 2)
    market_price_unit = market_value / quantity if quantity else 0.0
    conn.execute(
        """
        INSERT INTO holdings
            (snapshot_date, asset_id, asset_name, quantity, cost_price_unit,
             market_price_unit, market_value, currency, source_system, is_shadow,
             price_updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'test', FALSE, ?)
        """,
        [snapshot_date, asset_id, name, quantity, cost_price_unit,
         market_price_unit, market_value, currency, price_updated_at],
    )


def _open_asset_ids(conn: DatabaseConnector) -> set:
    rows = conn.execute(
        "SELECT asset_id FROM value_trap_reviews WHERE status = 'open'"
    ).fetchall()
    return {r[0] for r in rows}


# Bucket map pinned by the tests that assert on bucket-exclusion counts, so
# they describe their own fixtures rather than whichever config/verification*.yaml
# is present. Mirrors the default map in src/services/verification_config.py.
_PINNED_BUCKET_CFG = VerificationConfig(
    bucket_map={
        "compliance": (
            BucketMapEntry("RSU_AMZN", ("sell",)),
            BucketMapEntry("900009", ("sell",)),
        ),
        "ratio": (
            BucketMapEntry("GOLD", ("buy", "sell")),
            BucketMapEntry("ALTS_Paper_Gold", ("buy", "sell")),
            BucketMapEntry("IBIT", ("buy", "sell")),
            BucketMapEntry("FBTC", ("buy", "sell")),
        ),
        "liquidity": (BucketMapEntry("SGOV", ("buy", "sell")),),
    }
)


# ── Test 1: default -25% threshold ──────────────────────────────────────────

def test_default_threshold_900014_triggers_only(monkeypatch):
    conn = _make_db()
    monkeypatch.setattr("src.services.value_trap.get_today_usd_cny_rate", lambda: 7.2)
    try:
        _insert_holding(conn, "CN_FUND_900014", "Fund 900014", -40.9)
        _insert_holding(conn, "CN_FUND_900009", "Fund 900009 (Meridian)", -30.0)  # compliance
        _insert_holding(conn, "US_STK_FBTC", "FBTC", -30.0)  # ratio
        _insert_holding(conn, "US_STK_IBIT", "IBIT", -30.0)  # ratio
        _insert_holding(conn, "ALTS_Paper_Gold", "Gold", -30.0)  # ratio
        _insert_holding(conn, "CN_FUND_900011", "Fund 900011", -15.4)  # below default threshold
        _insert_holding(conn, "US_STK_MSFT", "MSFT", 5.0)  # healthy

        # Pin the bucket_map instead of inheriting whatever config happens to be
        # on disk. This assertion counts assets excluded by bucket, so it was
        # silently coupled to config/verification.yaml carrying the '900009'
        # compliance entry — an owner-specific fund code that Program OSR
        # scrubbed from verification.example.yaml. On a fresh clone (and in CI,
        # which has only the templates) the count came back 3, not 4.
        summary = scan_value_traps(conn, cfg=_PINNED_BUCKET_CFG)

        assert summary["scanned"] == 7
        assert summary["skipped_bucket"] == 4
        assert summary["skipped_no_cost"] == 0
        assert summary["hits"] == 1
        assert summary["opened"] == 1
        assert summary["refreshed"] == 0
        assert _open_asset_ids(conn) == {"CN_FUND_900014"}
    finally:
        conn.close()


# ── Test 2: lowering the config threshold to -15% ───────────────────────────

def test_config_threshold_minus15_adds_900011_and_900010_gold_still_excluded(monkeypatch):
    conn = _make_db()
    monkeypatch.setattr("src.services.value_trap.get_today_usd_cny_rate", lambda: 7.2)
    try:
        _insert_holding(conn, "CN_FUND_900014", "Fund 900014", -40.9)
        _insert_holding(conn, "ALTS_Paper_Gold", "Gold", -30.0)  # ratio, excluded regardless
        _insert_holding(conn, "CN_FUND_900011", "Fund 900011", -15.4)
        _insert_holding(conn, "CN_FUND_900010", "Fund 900010", -17.6)
        _insert_holding(conn, "US_STK_MSFT", "MSFT", 5.0)

        cfg = VerificationConfig(
            value_trap=ValueTrapSection(
                trigger_threshold_pct=-15.0, escalation_step_pp=10.0, overdue_alert_days=14
            )
        )
        summary = scan_value_traps(conn, cfg=cfg)

        assert summary["hits"] == 3
        open_ids = _open_asset_ids(conn)
        assert open_ids == {"CN_FUND_900014", "CN_FUND_900011", "CN_FUND_900010"}
        assert "ALTS_Paper_Gold" not in open_ids
    finally:
        conn.close()


# ── Test 3: escalation ladder — hold at -25 re-arms at -35, decline reopens ─

def test_hold_ruling_escalation_reopens_on_further_decline(monkeypatch):
    conn = _make_db()
    monkeypatch.setattr("src.services.value_trap.get_today_usd_cny_rate", lambda: 7.2)
    try:
        _insert_holding(conn, "CN_FUND_900014", "Fund 900014", -30.0)  # crosses default -25%
        summary1 = scan_value_traps(conn)
        assert summary1["opened"] == 1

        review_id = conn.execute(
            "SELECT id FROM value_trap_reviews WHERE asset_id = 'CN_FUND_900014'"
        ).fetchone()[0]

        # Simulate what PUT /reviews/value-trap/{id} does for a hold_with_thesis
        # ruling (F2.2): -25 - escalation_step_pp(10) = -35.
        conn.execute(
            """
            UPDATE value_trap_reviews
            SET status = 'ruled', ruling = 'hold_with_thesis', last_ruling = 'hold_with_thesis',
                last_reviewed_at = CURRENT_TIMESTAMP, next_trigger_threshold_pct = -35.0
            WHERE id = ?
            """,
            [review_id],
        )

        # A further decline to -30% (still above -35) must NOT reopen a review.
        conn.execute(
            "UPDATE holdings SET market_value = ? WHERE asset_id = 'CN_FUND_900014'",
            [1000 * 10 * (1 - 0.30)],
        )
        summary_no_hit = scan_value_traps(conn)
        assert summary_no_hit["hits"] == 0
        assert summary_no_hit["opened"] == 0

        # Decline further to -36% (crosses the re-armed -35% threshold) -> new row.
        conn.execute(
            "UPDATE holdings SET market_value = ? WHERE asset_id = 'CN_FUND_900014'",
            [1000 * 10 * (1 - 0.36)],
        )
        summary2 = scan_value_traps(conn)
        assert summary2["hits"] == 1
        assert summary2["opened"] == 1
        assert summary2["refreshed"] == 0

        rows = conn.execute(
            """
            SELECT status, trigger_threshold_pct FROM value_trap_reviews
            WHERE asset_id = 'CN_FUND_900014' ORDER BY id
            """
        ).fetchall()
        assert len(rows) == 2
        assert rows[0][0] == "ruled"
        assert rows[1][0] == "open"
        assert float(rows[1][1]) == -35.0
    finally:
        conn.close()


# ── Test 6: Rule 3 — per-asset latest snapshot, never a global MAX ─────────

def test_rule3_per_asset_latest_used_not_global_max(monkeypatch):
    conn = _make_db()
    monkeypatch.setattr("src.services.value_trap.get_today_usd_cny_rate", lambda: 7.2)
    try:
        # Asset A's latest snapshot is somewhat older than asset B's, but still
        # within the F4.4 'slow' staleness window (cfg.staleness.slow_days=7) —
        # this test targets Rule 3 (per-asset latest, never a global MAX), not
        # F4.4 staleness deferral (see test_stale_snapshot_deferred_unreliable
        # below for that). A query naively filtered to a single global maximum
        # snapshot date would silently drop asset A (no row on that date at all).
        older = (date.today() - timedelta(days=3)).isoformat()
        newer = date.today().isoformat()
        _insert_holding(conn, "CN_FUND_900014", "Fund 900014", -40.0, snapshot_date=older)
        _insert_holding(conn, "US_STK_MSFT", "MSFT", 5.0, snapshot_date=newer)

        summary = scan_value_traps(conn)

        assert summary["scanned"] == 2
        assert summary["hits"] == 1
        assert summary["deferred_unreliable"] == 0
        assert _open_asset_ids(conn) == {"CN_FUND_900014"}
    finally:
        conn.close()


# ── F4.4 — stale holdings snapshot deferred, not evaluated as a trigger ─────

def test_stale_snapshot_deferred_unreliable_and_event_logged(monkeypatch):
    conn = _make_db()
    monkeypatch.setattr("src.services.value_trap.get_today_usd_cny_rate", lambda: 7.2)
    try:
        stale = (date.today() - timedelta(days=8)).isoformat()
        fresh = date.today().isoformat()
        _insert_holding(conn, "CN_FUND_900014", "Fund 900014", -40.0, snapshot_date=stale)
        _insert_holding(conn, "US_STK_MSFT", "MSFT", -30.0, snapshot_date=fresh)

        summary = scan_value_traps(conn)

        assert summary["scanned"] == 2
        assert summary["deferred_unreliable"] == 1
        # Stale asset never evaluated as a trigger — no review opened for it.
        assert _open_asset_ids(conn) == {"US_STK_MSFT"}

        events = conn.execute(
            "SELECT metric_key, context FROM ruling_deferred_events"
        ).fetchall()
        assert len(events) == 1
        assert events[0][0] == "holdings_snapshot"
        assert events[0][1] == "value_trap:CN_FUND_900014"
    finally:
        conn.close()


# ── Test 7: cost <= 0 rows are skipped, not a fake -100% ────────────────────

def test_cost_zero_rows_skipped_and_counted(monkeypatch):
    conn = _make_db()
    monkeypatch.setattr("src.services.value_trap.get_today_usd_cny_rate", lambda: 7.2)
    try:
        # Deliberately not matching any bucket_map pattern, so this exercises
        # the cost guard specifically (not the bucket exclusion).
        conn.execute(
            """
            INSERT INTO holdings
                (snapshot_date, asset_id, asset_name, quantity, cost_price_unit,
                 market_price_unit, market_value, currency, source_system, is_shadow)
            VALUES (?, 'US_STK_ZEROCOST', 'Zero Cost Test Asset',
                    100.0, 0.0, 50.0, 5000.0, 'CNY', 'test', FALSE)
            """,
            [(date.today() - timedelta(days=1)).isoformat()],
        )
        _insert_holding(conn, "US_STK_MSFT", "MSFT", 5.0)

        summary = scan_value_traps(conn)

        assert summary["skipped_no_cost"] == 1
        assert summary["hits"] == 0
        total_reviews = conn.execute("SELECT COUNT(*) FROM value_trap_reviews").fetchone()[0]
        assert total_reviews == 0
    finally:
        conn.close()


# ── F4.4 valuation-freshness: fresh price_updated_at overrides stale snapshot ─

def test_fresh_price_updated_at_overrides_stale_snapshot_date(monkeypatch):
    """Asset with a 20-day-old snapshot_date but a today price_updated_at must
    be *evaluated*, not deferred.  The scan trigger depends on market_price_unit;
    DSA refreshes that field without updating snapshot_date, so price_updated_at
    is the correct freshness key (F4.4 fix)."""
    conn = _make_db()
    monkeypatch.setattr("src.services.value_trap.get_today_usd_cny_rate", lambda: 7.2)
    try:
        stale_snapshot = (date.today() - timedelta(days=20)).isoformat()
        # price_updated_at = now (simulates a DSA refresh today on an old snapshot)
        fresh_price_ts = datetime.now().isoformat()
        _insert_holding(
            conn, "CN_FUND_900014", "Fund 900014", -40.0,
            snapshot_date=stale_snapshot,
            price_updated_at=fresh_price_ts,
        )

        summary = scan_value_traps(conn)

        # Price is fresh → not deferred → evaluated → hits threshold
        assert summary["deferred_unreliable"] == 0, "should not be deferred when price_updated_at is today"
        assert summary["evaluated"] == 1
        assert summary["hits"] == 1
        assert _open_asset_ids(conn) == {"CN_FUND_900014"}
    finally:
        conn.close()


def test_stale_snapshot_and_stale_price_updated_at_both_deferred(monkeypatch):
    """Asset with both snapshot_date and price_updated_at older than slow_days
    must be deferred — valuation freshness (max of the two) is still stale."""
    conn = _make_db()
    monkeypatch.setattr("src.services.value_trap.get_today_usd_cny_rate", lambda: 7.2)
    try:
        stale_dt = (datetime.now() - timedelta(days=20)).isoformat()
        _insert_holding(
            conn, "CN_FUND_900014", "Fund 900014", -40.0,
            snapshot_date=(date.today() - timedelta(days=20)).isoformat(),
            price_updated_at=stale_dt,
        )

        summary = scan_value_traps(conn)

        # Both dates stale → valuation freshness is stale → deferred
        assert summary["deferred_unreliable"] == 1
        assert summary["evaluated"] == 0
        assert summary["hits"] == 0
        assert _open_asset_ids(conn) == set()
    finally:
        conn.close()


# ── R2-1: deferred_assets list + auto data_fix ──────────────────────────────

def test_14_day_stale_cn_fund_lands_in_deferred_assets_with_data_fix(monkeypatch):
    """CN fund (fast class) with 14-day-old price → deferred_assets entry + data_fix."""
    conn = _make_db()
    monkeypatch.setattr("src.services.value_trap.get_today_usd_cny_rate", lambda: 7.2)
    try:
        stale = (date.today() - timedelta(days=14)).isoformat()
        _insert_holding(conn, "CN_FUND_900014", "Fund 900014", -40.0,
                        snapshot_date=stale)

        summary = scan_value_traps(conn)

        assert summary["deferred_unreliable"] == 1
        assert len(summary["deferred_assets"]) == 1
        entry = summary["deferred_assets"][0]
        assert entry["asset_id"] == "CN_FUND_900014"
        assert entry["freshness_class"] == "fast"
        assert entry["price_date"] == stale
        # data_fix must have been created (fast-class assets get a data_fix)
        assert entry["data_fix_id"] is not None and entry["data_fix_id"] > 0

        # Verify data_fix row in DB
        df_row = conn.execute(
            "SELECT title, status FROM data_fixes WHERE id = ?",
            [entry["data_fix_id"]],
        ).fetchone()
        assert df_row is not None
        assert df_row[0] == "stale price feed: CN_FUND_900014"
        assert df_row[1] == "open"
    finally:
        conn.close()


def test_deferred_data_fix_is_idempotent_on_second_scan(monkeypatch):
    """Running the scan twice must not create a second data_fix row."""
    conn = _make_db()
    monkeypatch.setattr("src.services.value_trap.get_today_usd_cny_rate", lambda: 7.2)
    try:
        stale = (date.today() - timedelta(days=14)).isoformat()
        _insert_holding(conn, "CN_FUND_900014", "Fund 900014", -40.0,
                        snapshot_date=stale)

        summary1 = scan_value_traps(conn)
        fix_id1 = summary1["deferred_assets"][0]["data_fix_id"]

        summary2 = scan_value_traps(conn)
        fix_id2 = summary2["deferred_assets"][0]["data_fix_id"]

        assert fix_id1 == fix_id2, "Second scan must reuse existing open data_fix"
        total_fixes = conn.execute(
            "SELECT COUNT(*) FROM data_fixes WHERE title = 'stale price feed: CN_FUND_900014'"
        ).fetchone()[0]
        assert total_fixes == 1, "Only one data_fix row should exist after two scans"
    finally:
        conn.close()


def test_cash_like_deposit_is_exempt_not_deferred(monkeypatch):
    """CASH_Deposit_X is cash-like → exempt_cash_like, NOT in deferred_assets."""
    conn = _make_db()
    monkeypatch.setattr("src.services.value_trap.get_today_usd_cny_rate", lambda: 7.2)
    try:
        # Stale date — if not exempt, this would be deferred
        stale = (date.today() - timedelta(days=30)).isoformat()
        _insert_holding(conn, "CASH_Deposit_CMB_CNY", "CMB Deposit", -1.0,
                        snapshot_date=stale)
        _insert_holding(conn, "CN_FUND_900014", "Fund 900014", -40.0)  # normal asset

        summary = scan_value_traps(conn)

        assert summary["exempt_cash_like"] == 1
        assert summary["deferred_unreliable"] == 0
        # The cash deposit must NOT appear in deferred_assets
        deferred_ids = {d["asset_id"] for d in summary["deferred_assets"]}
        assert "CASH_Deposit_CMB_CNY" not in deferred_ids
        # skipped_bucket and exempt_cash_like are separate counts
        assert summary["skipped_bucket"] == 0
    finally:
        conn.close()


def test_cash_like_wealth_cmb_is_exempt(monkeypatch):
    """Wealth_CMB (CMB wealth product) is cash-like → exempt."""
    conn = _make_db()
    monkeypatch.setattr("src.services.value_trap.get_today_usd_cny_rate", lambda: 7.2)
    try:
        _insert_holding(conn, "Wealth_CMB", "CMB Wealth", 0.5)
        summary = scan_value_traps(conn)
        assert summary["exempt_cash_like"] == 1
        assert summary["skipped_bucket"] == 0
    finally:
        conn.close()


def test_fresh_cn_fund_is_evaluated(monkeypatch):
    """CN fund with today's snapshot → evaluated, not deferred."""
    conn = _make_db()
    monkeypatch.setattr("src.services.value_trap.get_today_usd_cny_rate", lambda: 7.2)
    try:
        _insert_holding(conn, "CN_FUND_900011", "Fund 900011", -15.0)  # below threshold
        summary = scan_value_traps(conn)
        assert summary["deferred_unreliable"] == 0
        assert summary["evaluated"] == 1
        assert summary["hits"] == 0
    finally:
        conn.close()


def test_slow_class_asset_deferred_without_data_fix(monkeypatch):
    """Insurance asset (slow class) with stale snapshot → deferred but NO data_fix."""
    conn = _make_db()
    monkeypatch.setattr("src.services.value_trap.get_today_usd_cny_rate", lambda: 7.2)
    try:
        stale = (date.today() - timedelta(days=20)).isoformat()
        _insert_holding(conn, "Ins_Pacific_001", "Insurance", -30.0,
                        snapshot_date=stale)

        summary = scan_value_traps(conn)

        assert summary["deferred_unreliable"] == 1
        deferred = summary["deferred_assets"]
        assert len(deferred) == 1
        entry = deferred[0]
        assert entry["freshness_class"] == "slow"
        # 'slow'-class assets get no data_fix (no automated feed to repair)
        assert entry["data_fix_id"] is None
    finally:
        conn.close()


def test_fresh_scan_auto_closes_stale_data_fix(monkeypatch):
    """When a previously-deferred fast-class asset passes the freshness gate on a
    later scan, its open 'stale price feed' data_fix is auto-closed (status='done').
    """
    conn = _make_db()
    monkeypatch.setattr("src.services.value_trap.get_today_usd_cny_rate", lambda: 7.2)
    try:
        # Scan 1: 14-day-old snapshot → deferred, data_fix opened
        stale = (date.today() - timedelta(days=14)).isoformat()
        _insert_holding(conn, "CN_FUND_900014", "Fund 900014", -40.0, snapshot_date=stale)
        summary1 = scan_value_traps(conn)
        assert summary1["deferred_unreliable"] == 1
        fix_id = summary1["deferred_assets"][0]["data_fix_id"]
        assert fix_id is not None
        assert conn.execute(
            "SELECT status FROM data_fixes WHERE id = ?", [fix_id]
        ).fetchone()[0] == "open"

        # Update holding to today → passes freshness gate on next scan
        today = date.today().isoformat()
        conn.execute(
            "UPDATE holdings SET snapshot_date = ? WHERE asset_id = 'CN_FUND_900014'",
            [today],
        )

        # Scan 2: fresh → data_fix must be auto-closed
        summary2 = scan_value_traps(conn)
        assert summary2["deferred_unreliable"] == 0
        assert conn.execute(
            "SELECT status FROM data_fixes WHERE id = ?", [fix_id]
        ).fetchone()[0] == "done"
    finally:
        conn.close()


def test_deferred_assets_include_due_at(monkeypatch):
    """Each fast-class deferred_assets entry includes a non-null data_fix_due_at."""
    conn = _make_db()
    monkeypatch.setattr("src.services.value_trap.get_today_usd_cny_rate", lambda: 7.2)
    try:
        stale = (date.today() - timedelta(days=14)).isoformat()
        _insert_holding(conn, "CN_FUND_900014", "Fund 900014", -40.0, snapshot_date=stale)
        summary = scan_value_traps(conn)
        entry = summary["deferred_assets"][0]
        assert "data_fix_due_at" in entry
        # fast-class with a data_fix must have a non-null due_at
        assert entry["data_fix_due_at"] is not None
        # format: YYYY-MM-DD
        assert len(entry["data_fix_due_at"]) == 10
    finally:
        conn.close()


def test_summary_has_exempt_cash_like_key():
    """scan_value_traps summary always includes exempt_cash_like and deferred_assets."""
    conn = _make_db()
    try:
        from src.services.value_trap import scan_value_traps as _scan
        summary = _scan(conn)
        assert "exempt_cash_like" in summary
        assert "deferred_assets" in summary
        assert isinstance(summary["deferred_assets"], list)
    finally:
        conn.close()
