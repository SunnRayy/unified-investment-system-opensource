"""Tests for V5.8.0 scorer maturity gate and verification_blocked transition."""
from datetime import date, timedelta
from src.database.connector import DatabaseConnector
from src.services.decision_scorer import (
    compute_outcome_to_date,
    score_single_trade,
    derive_verdict_suggestion,
    VERDICT_GOOD_CALL,
    VERDICT_NEUTRAL,
    VERDICT_REGRET,
    VERDICT_BULLET_DODGED,
    VERDICT_MISSED_OPPORTUNITY,
    VERIFICATION_STATUSES,
)


def _setup_db() -> DatabaseConnector:
    """Create in-memory DB with required tables."""
    db = DatabaseConnector(":memory:")
    db.execute("CREATE SEQUENCE IF NOT EXISTS seq_trade_logs_id START 1")
    db.execute("""
        CREATE TABLE IF NOT EXISTS trade_logs (
            id INTEGER PRIMARY KEY DEFAULT nextval('seq_trade_logs_id'),
            log_date DATE NOT NULL,
            asset_id VARCHAR(50) NOT NULL,
            asset_name VARCHAR(200),
            action VARCHAR(20) NOT NULL,
            price DECIMAL(20,8),
            quantity DECIMAL(20,8),
            amount DECIMAL(20,2),
            pnl_pct DECIMAL(10,4),
            pnl_amount DECIMAL(20,2),
            decision_reason TEXT,
            ai_suggestion TEXT,
            suggestion_source VARCHAR(50),
            verification_date DATE,
            verification_result VARCHAR(100),
            verification_status VARCHAR(20) DEFAULT 'pending',
            verification_block_reason VARCHAR,
            verdict VARCHAR(50),
            outcome_pct DECIMAL(10,4),
            decision_grade VARCHAR(10),
            linked_transaction_id INTEGER,
            linked_memo_id INTEGER,
            user_notes TEXT,
            vote_breakdown JSON,
            currency VARCHAR(10),
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    db.execute("""
        CREATE TABLE IF NOT EXISTS market_daily (
            code VARCHAR NOT NULL,
            date DATE NOT NULL,
            close DOUBLE,
            PRIMARY KEY (code, date)
        )
    """)
    db.execute("""
        CREATE TABLE IF NOT EXISTS insights (
            id INTEGER PRIMARY KEY,
            insight_date DATE,
            ai_model VARCHAR,
            content TEXT,
            title VARCHAR(200),
            adopted INTEGER,
            category VARCHAR
        )
    """)
    db.execute("""
        CREATE TABLE IF NOT EXISTS strategy_memos (
            id INTEGER PRIMARY KEY,
            memo_date DATE,
            title VARCHAR,
            key_directives TEXT,
            content TEXT
        )
    """)
    # Create verdict_audit table
    db.execute("CREATE SEQUENCE IF NOT EXISTS seq_verdict_audit_id START 1")
    db.execute("""
        CREATE TABLE IF NOT EXISTS verdict_audit (
            id INTEGER PRIMARY KEY DEFAULT nextval('seq_verdict_audit_id'),
            trade_id INTEGER NOT NULL,
            suggested_from_threshold VARCHAR,
            keyword_derived VARCHAR,
            final_verdict VARCHAR,
            mismatch BOOLEAN,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    return db


def test_skips_unmatured_trades():
    """A trade with log_date only 10 days ago should NOT be scored even if verification_result is set."""
    db = _setup_db()
    log_date = date.today() - timedelta(days=10)

    db.execute("""
        INSERT INTO trade_logs (log_date, asset_id, action, verification_result,
                                verification_status, decision_reason)
        VALUES (?, 'EQ_AAPL', 'buy', '买对了 +3%', 'pending_window', 'test')
    """, (log_date,))

    trade_id = db.execute("SELECT MAX(id) FROM trade_logs").fetchone()[0]
    score_single_trade(db, trade_id)

    # Should NOT have been scored (not matured)
    row = db.execute("SELECT verdict, outcome_pct, verification_status FROM trade_logs WHERE id = ?", (trade_id,)).fetchone()
    assert row[0] is None, "verdict should stay NULL for unmatured trade"
    assert row[2] == 'pending_window', "status should stay pending_window"
    db.close()


def test_scores_matured_trade_with_prices():
    """A matured trade (31+ days) with market prices should be scored and status set to 'verified'."""
    db = _setup_db()
    log_date = date.today() - timedelta(days=35)

    db.execute("""
        INSERT INTO trade_logs (log_date, asset_id, action, verification_result,
                                verification_status, decision_reason)
        VALUES (?, 'EQ_AAPL', 'buy', '买对了', 'pending_window', 'test')
    """, (log_date,))

    # Insert market prices at log_date and log_date+30
    db.execute("INSERT INTO market_daily (code, date, close) VALUES (?, ?, ?)",
               ('AAPL', log_date, 180.0))
    db.execute("INSERT INTO market_daily (code, date, close) VALUES (?, ?, ?)",
               ('AAPL', log_date + timedelta(days=30), 190.0))

    trade_id = db.execute("SELECT MAX(id) FROM trade_logs").fetchone()[0]
    score_single_trade(db, trade_id)

    row = db.execute("""
        SELECT verdict, outcome_pct, verification_status
        FROM trade_logs WHERE id = ?
    """, (trade_id,)).fetchone()

    assert row[0] is not None, "verdict should be set for matured trade with prices"
    assert row[1] is not None, "outcome_pct should be set for matured trade with prices"
    assert row[2] == 'verified', f"status should be 'verified', got '{row[2]}'"

    # Verify audit log was created
    audit = db.execute("SELECT COUNT(*) FROM verdict_audit WHERE trade_id = ?", (trade_id,)).fetchone()
    assert audit[0] > 0, "verdict_audit row should be created"
    db.close()


def test_matured_trade_with_narrative_stays_pending_when_no_prices():
    """A matured trade with a user narrative must NOT be blocked when no market price exists.

    Behaviour change (Issue #12): the old code auto-blocked ANY matured trade missing
    a +30d price, including trades where the user had submitted a verification narrative
    but chose "Let backend decide" for the verdict. This caused user-verified trades to
    silently flip to 'verification_blocked', appearing lost.

    New rule: only block when the user has provided ZERO human input (no narrative, no
    verdict). Since the scorer's WHERE clause requires verification_result IS NOT NULL,
    every row it processes already has a narrative — meaning verification_blocked is
    essentially never set by score_single_trade for user-interacted rows. The trade stays
    in pending_window so the user can return and select a verdict manually.
    """
    db = _setup_db()
    log_date = date.today() - timedelta(days=35)

    db.execute("""
        INSERT INTO trade_logs (log_date, asset_id, action, verification_result,
                                verification_status, decision_reason)
        VALUES (?, 'EQ_UNKN', 'buy', '不确定，需要再观察', 'pending_window', 'test')
    """, (log_date,))

    # No market_daily data for UNKN
    trade_id = db.execute("SELECT MAX(id) FROM trade_logs").fetchone()[0]
    score_single_trade(db, trade_id)

    row = db.execute("""
        SELECT verification_status, verification_block_reason
        FROM trade_logs WHERE id = ?
    """, (trade_id,)).fetchone()

    # Trade has a narrative → must NOT be blocked, must stay pending_window
    assert row[0] == 'pending_window', (
        f"Trade with narrative should stay pending_window (not blocked); got '{row[0]}'"
    )
    assert row[1] is None, "block_reason should NOT be set when trade has a narrative"
    db.close()


def test_derive_verdict_suggestion_buy_positive():
    """derive_verdict_suggestion for buy with positive outcome -> good_call."""
    result = derive_verdict_suggestion('buy', 6.0)
    assert result == VERDICT_GOOD_CALL


def test_derive_verdict_suggestion_buy_negative():
    """derive_verdict_suggestion for buy with negative outcome -> regret."""
    result = derive_verdict_suggestion('buy', -6.0)
    assert result == VERDICT_REGRET


def test_derive_verdict_suggestion_sell_positive():
    """derive_verdict_suggestion for sell with positive outcome (price dropped) -> bullet_dodged."""
    result = derive_verdict_suggestion('sell', 6.0)
    assert result == VERDICT_BULLET_DODGED


def test_derive_verdict_suggestion_sell_negative():
    """derive_verdict_suggestion for sell with negative outcome (price rose) -> missed_opportunity."""
    result = derive_verdict_suggestion('sell', -6.0)
    assert result == VERDICT_MISSED_OPPORTUNITY


def test_derive_verdict_suggestion_inconclusive():
    """derive_verdict_suggestion with small outcome -> None."""
    result = derive_verdict_suggestion('buy', 2.0)
    assert result is None


def test_derive_verdict_suggestion_none_outcome():
    """derive_verdict_suggestion with None outcome -> None."""
    result = derive_verdict_suggestion('buy', None)
    assert result is None


def test_verification_statuses_constant():
    """VERIFICATION_STATUSES has all 4 states."""
    assert 'pending' in VERIFICATION_STATUSES
    assert 'pending_window' in VERIFICATION_STATUSES
    assert 'verified' in VERIFICATION_STATUSES
    assert 'verification_blocked' in VERIFICATION_STATUSES


# ---------------------------------------------------------------------------
# Issue #7 regression — scorer must not overwrite user-set verdicts
# ---------------------------------------------------------------------------

from src.services.decision_scorer import score_all_trades  # noqa: E402


def _insert_matured_trade_with_verdict(db, verdict: str, action: str = "buy") -> int:
    """Insert a matured trade that already has a user-set verdict and verification_result.

    Uses narrative "买对了" which is a GOOD_CALL keyword — so the text classifier
    would derive 'good_call', while prices give a 15% gain (also → good_call via
    derive_verdict_suggestion). This gives the scorer every reason to overwrite a
    non-good_call verdict, making the guard test meaningful.
    """
    log_date = date.today() - timedelta(days=35)
    db.execute("""
        INSERT INTO trade_logs (log_date, asset_id, action, verification_result,
                                verification_status, verdict, decision_reason)
        VALUES (?, 'EQ_AAPL', ?, '买对了，止损成功', 'pending_window', ?, 'test')
    """, (log_date, action, verdict))
    trade_id = db.execute("SELECT MAX(id) FROM trade_logs").fetchone()[0]
    # Insert prices that would auto-derive a DIFFERENT verdict (buy + 15% → good_call)
    db.execute("INSERT INTO market_daily (code, date, close) VALUES (?, ?, ?)",
               ('AAPL', log_date, 180.0))
    db.execute("INSERT INTO market_daily (code, date, close) VALUES (?, ?, ?)",
               ('AAPL', log_date + timedelta(days=30), 207.0))
    return trade_id


def test_score_single_trade_does_not_overwrite_user_verdict():
    """score_single_trade must NOT overwrite a verdict already set by the user.

    Regression for Issue #7 Bug 3: scorer used (verdict IS NULL OR outcome_pct IS NULL)
    so it ran even when user had set verdict, then overwrote it.
    """
    db = _setup_db()
    trade_id = _insert_matured_trade_with_verdict(db, verdict="missed_opportunity", action="buy")

    score_single_trade(db, trade_id)

    row = db.execute(
        "SELECT verdict, outcome_pct FROM trade_logs WHERE id = ?", (trade_id,)
    ).fetchone()
    assert row[0] == "missed_opportunity", (
        f"Scorer must not overwrite user-set verdict; expected 'missed_opportunity', got '{row[0]}'"
    )
    # outcome_pct SHOULD still be computed (it's a metric, not a user choice)
    assert row[1] is not None, "scorer should still fill outcome_pct even when verdict is preserved"
    db.close()


def test_score_all_trades_does_not_overwrite_user_verdict():
    """score_all_trades must NOT overwrite a verdict already set by the user."""
    db = _setup_db()
    trade_id = _insert_matured_trade_with_verdict(db, verdict="regret", action="buy")

    score_all_trades(db)

    row = db.execute(
        "SELECT verdict FROM trade_logs WHERE id = ?", (trade_id,)
    ).fetchone()
    assert row[0] == "regret", (
        f"score_all_trades must not overwrite user-set verdict; expected 'regret', got '{row[0]}'"
    )


def test_score_single_trade_does_not_block_when_user_set_verdict():
    """score_single_trade must NOT set verification_blocked when the user already set a verdict.

    Regression for Issue #7 (persisting): when no market price exists at log_date+30d but the
    user has explicitly set a verdict, the scorer was marking the trade 'verification_blocked'.
    This overrode the user's 'verified' status even though the trade was intentionally verified.
    RSU vest trades (no price data for 'vest' action + asset) are the common trigger.
    """
    db = _setup_db()
    log_date = date.today() - timedelta(days=35)

    # Insert a matured trade that the user has already verified (verdict + verification_result set)
    # but outcome_pct is NULL (reopen cleared it) and there are NO market prices.
    db.execute("""
        INSERT INTO trade_logs (log_date, asset_id, action, verification_result,
                                verification_status, verdict, decision_reason)
        VALUES (?, 'RSU_AMZN', 'vest', 'Missed the run-up', 'verified', 'missed_opportunity', 'RSU vest')
    """, (log_date,))
    trade_id = db.execute("SELECT MAX(id) FROM trade_logs").fetchone()[0]
    # No market_daily rows inserted — simulates AMZN not in price DB

    score_single_trade(db, trade_id)

    row = db.execute(
        "SELECT verdict, verification_status FROM trade_logs WHERE id = ?", (trade_id,)
    ).fetchone()
    assert row[0] == "missed_opportunity", (
        f"Scorer must not clear user-set verdict; got '{row[0]}'"
    )
    assert row[1] == "verified", (
        f"Scorer must not change status to verification_blocked when user already set verdict; got '{row[1]}'"
    )
    db.close()


def test_score_single_trade_does_not_block_narrative_submitted_trade():
    """score_single_trade must NOT set verification_blocked when the user has written a narrative.

    Regression for Issue #12: when a user submits a narrative but leaves verdict as
    "Let backend decide" (no explicit verdict), score_single_trade was blocking the trade
    with 'verification_blocked' because existing_verdict IS NULL — the same condition that
    the Issue #7 fix guards against, but only for explicit verdicts, not for narratives.

    The fix: only auto-block when NEITHER verdict NOR narrative was provided (the trade has
    zero human input). If a narrative exists, leave the trade in pending_window so the user
    can return and select a verdict manually.
    """
    db = _setup_db()
    log_date = date.today() - timedelta(days=35)

    # Matured trade: user submitted a narrative but chose "Let backend decide" (no verdict).
    # No market prices inserted — simulates asset not in market_daily.
    db.execute("""
        INSERT INTO trade_logs (log_date, asset_id, action, verification_result,
                                verification_status, verdict, decision_reason)
        VALUES (?, 'US_STK_AMZN', 'Buy',
                'AMZN Buy was well-timed given the macro setup',
                'pending_window', NULL, 'test')
    """, (log_date,))
    trade_id = db.execute("SELECT MAX(id) FROM trade_logs").fetchone()[0]
    # No market_daily rows → compute_outcome_pct_from_prices returns None

    score_single_trade(db, trade_id)

    row = db.execute(
        "SELECT verdict, verification_status FROM trade_logs WHERE id = ?", (trade_id,)
    ).fetchone()
    assert row[1] != "verification_blocked", (
        f"Scorer must NOT block a trade that has a user narrative; "
        f"expected pending_window, got '{row[1]}'"
    )
    assert row[1] == "pending_window", (
        f"Trade with narrative but no verdict should stay pending_window; got '{row[1]}'"
    )
    assert row[0] is None, (
        f"Verdict should remain NULL (no explicit selection); got '{row[0]}'"
    )
    db.close()


def test_score_single_trade_within_band_gets_neutral_verdict():
    """Addendum neutral verdict: matured trade with within-band outcome gets VERDICT_NEUTRAL.

    Scenario: matured trade (35 days), prices exist but outcome_pct is tiny (~1%) — below
    the 5% default band. Both classify_verdict_from_text (narrative '持仓观察中' has no
    keywords) and derive_verdict_suggestion return None. The neutral fallback fires and
    the row is marked verified with verdict='neutral'.

    Updated from the prior test 'test_score_single_trade_does_not_set_verified_without_verdict'
    which expected None verdict and pending status. That behaviour is superseded: within-band
    rows now close the loop via VERDICT_NEUTRAL, satisfying Rule B (verified ⇒ verdict present).
    """
    db = _setup_db()
    log_date = date.today() - timedelta(days=35)

    db.execute("""
        INSERT INTO trade_logs (log_date, asset_id, action, verification_result,
                                verification_status, decision_reason)
        VALUES (?, 'EQ_TEST', 'buy', '持仓观察中', 'pending_window', 'test')
    """, (log_date,))

    # Prices: tiny 1% move → outcome_pct ~1% → derive_verdict returns None → neutral fires
    db.execute("INSERT INTO market_daily (code, date, close) VALUES (?, ?, ?)",
               ('TEST', log_date, 100.0))
    db.execute("INSERT INTO market_daily (code, date, close) VALUES (?, ?, ?)",
               ('TEST', log_date + timedelta(days=30), 101.0))

    trade_id = db.execute("SELECT MAX(id) FROM trade_logs").fetchone()[0]
    score_single_trade(db, trade_id)

    row = db.execute(
        "SELECT verdict, verification_status FROM trade_logs WHERE id = ?", (trade_id,)
    ).fetchone()

    assert row[0] == VERDICT_NEUTRAL, (
        f"Within-band matured trade must get VERDICT_NEUTRAL; got '{row[0]}'"
    )
    assert row[1] == 'verified', (
        f"Neutral verdict satisfies Rule B; status must be 'verified'; got '{row[1]}'"
    )
    db.close()


# ---------------------------------------------------------------------------
# T1 — compute_outcome_to_date
# ---------------------------------------------------------------------------

def test_compute_outcome_to_date_buy_sign():
    """Buy trade: positive price movement → positive pct + asof date."""
    db = _setup_db()
    log_date = date.today() - timedelta(days=10)
    later_date = date.today() - timedelta(days=1)

    db.execute("INSERT INTO market_daily (code, date, close) VALUES (?, ?, ?)",
               ('AAPL', log_date, 100.0))
    db.execute("INSERT INTO market_daily (code, date, close) VALUES (?, ?, ?)",
               ('AAPL', later_date, 112.0))

    result = compute_outcome_to_date(db, 'EQ_AAPL', 'Buy', log_date)

    assert result is not None, "Expected a result tuple"
    pct, asof = result
    assert round(pct, 2) == 12.0, f"Expected +12%, got {pct}"
    assert asof == later_date
    db.close()


def test_compute_outcome_to_date_sell_sign():
    """Sell trade: price dropped after sell → outcome_pct positive (bullet dodged convention)."""
    db = _setup_db()
    log_date = date.today() - timedelta(days=10)
    later_date = date.today() - timedelta(days=1)

    db.execute("INSERT INTO market_daily (code, date, close) VALUES (?, ?, ?)",
               ('AAPL', log_date, 100.0))
    db.execute("INSERT INTO market_daily (code, date, close) VALUES (?, ?, ?)",
               ('AAPL', later_date, 90.0))

    result = compute_outcome_to_date(db, 'EQ_AAPL', 'Sell', log_date)

    assert result is not None
    pct, asof = result
    # price dropped 10% after sell → sign-flipped → +10% (bullet dodged)
    assert round(pct, 2) == 10.0, f"Expected +10% for sell, got {pct}"
    assert asof == later_date
    db.close()


def test_compute_outcome_to_date_no_baseline():
    """No price at log_date → returns None."""
    db = _setup_db()
    log_date = date.today() - timedelta(days=10)

    # Only a recent price, no baseline at log_date
    db.execute("INSERT INTO market_daily (code, date, close) VALUES (?, ?, ?)",
               ('AAPL', date.today() - timedelta(days=1), 110.0))

    result = compute_outcome_to_date(db, 'EQ_AAPL', 'Buy', log_date)

    assert result is None, "Expected None when baseline price is missing"
    db.close()


def test_compute_outcome_to_date_no_later_close():
    """Only baseline price available (latest_date == log_date) → returns None."""
    db = _setup_db()
    log_date = date.today() - timedelta(days=10)

    # Only the baseline; no price after log_date
    db.execute("INSERT INTO market_daily (code, date, close) VALUES (?, ?, ?)",
               ('AAPL', log_date, 100.0))

    result = compute_outcome_to_date(db, 'EQ_AAPL', 'Buy', log_date)

    assert result is None, "Expected None when no price strictly newer than log_date"
    db.close()


# ---------------------------------------------------------------------------
# T2 — score_all_trades narrative-optional
# ---------------------------------------------------------------------------

def test_score_all_trades_narrative_optional_auto_verified():
    """Matured trade with prices but NO narrative → auto-verdict + verified + auto marker."""
    db = _setup_db()
    log_date = date.today() - timedelta(days=35)

    db.execute("""
        INSERT INTO trade_logs (log_date, asset_id, action,
                                verification_status, decision_reason)
        VALUES (?, 'EQ_AAPL', 'buy', 'pending', 'test')
    """, (log_date,))

    # 10% gain → above _DEFAULT_BAND (5%) → good_call
    db.execute("INSERT INTO market_daily (code, date, close) VALUES (?, ?, ?)",
               ('AAPL', log_date, 100.0))
    db.execute("INSERT INTO market_daily (code, date, close) VALUES (?, ?, ?)",
               ('AAPL', log_date + timedelta(days=30), 110.0))

    score_all_trades(db)

    row = db.execute("""
        SELECT verdict, verification_status, verification_date, verification_result, outcome_pct
        FROM trade_logs
    """).fetchone()

    assert row[0] == 'good_call', f"Expected good_call verdict, got '{row[0]}'"
    assert row[1] == 'verified', f"Expected verified status, got '{row[1]}'"
    assert row[2] is not None, "verification_date should be set"
    assert row[3] == 'auto: price-based verdict at +30d', (
        f"Expected auto marker in verification_result, got '{row[3]}'"
    )
    assert row[4] is not None, "outcome_pct should be set"
    db.close()


def test_score_all_trades_no_price_no_narrative_blocked():
    """Matured trade with no prices AND no narrative AND no verdict → verification_blocked."""
    db = _setup_db()
    log_date = date.today() - timedelta(days=35)

    db.execute("""
        INSERT INTO trade_logs (log_date, asset_id, action,
                                verification_status, decision_reason)
        VALUES (?, 'EQ_UNKN', 'buy', 'pending', 'test')
    """, (log_date,))
    # No market_daily rows

    score_all_trades(db)

    row = db.execute("""
        SELECT verification_status, verification_block_reason
        FROM trade_logs
    """).fetchone()

    assert row[0] == 'verification_blocked', (
        f"Expected verification_blocked, got '{row[0]}'"
    )
    assert row[1] is not None, "verification_block_reason should be set"
    db.close()


def test_score_all_trades_existing_verdict_untouched_no_narrative():
    """Pre-existing human verdict is never overwritten even when narrative is absent."""
    db = _setup_db()
    log_date = date.today() - timedelta(days=35)

    db.execute("""
        INSERT INTO trade_logs (log_date, asset_id, action,
                                verification_status, verdict, decision_reason)
        VALUES (?, 'EQ_AAPL', 'buy', 'verified', 'regret', 'test')
    """, (log_date,))

    # Prices that would derive good_call if auto-scoring ran
    db.execute("INSERT INTO market_daily (code, date, close) VALUES (?, ?, ?)",
               ('AAPL', log_date, 100.0))
    db.execute("INSERT INTO market_daily (code, date, close) VALUES (?, ?, ?)",
               ('AAPL', log_date + timedelta(days=30), 115.0))

    score_all_trades(db)

    row = db.execute("SELECT verdict, verification_result FROM trade_logs").fetchone()

    assert row[0] == 'regret', (
        f"Scorer must not overwrite human verdict 'regret'; got '{row[0]}'"
    )
    assert row[1] != 'auto: price-based verdict at +30d', (
        "Auto marker must not be written when human verdict was already present"
    )
    db.close()


def test_score_all_trades_pre_maturity_untouched():
    """Trade < 30 days old must not be scored (maturity gate unchanged)."""
    db = _setup_db()
    log_date = date.today() - timedelta(days=15)

    db.execute("""
        INSERT INTO trade_logs (log_date, asset_id, action,
                                verification_status, decision_reason)
        VALUES (?, 'EQ_AAPL', 'buy', 'pending', 'test')
    """, (log_date,))

    db.execute("INSERT INTO market_daily (code, date, close) VALUES (?, ?, ?)",
               ('AAPL', log_date, 100.0))
    db.execute("INSERT INTO market_daily (code, date, close) VALUES (?, ?, ?)",
               ('AAPL', log_date + timedelta(days=15), 112.0))

    score_all_trades(db)

    row = db.execute("SELECT verdict, verification_status FROM trade_logs").fetchone()

    assert row[0] is None, "Pre-maturity trade must not receive a verdict"
    assert row[1] == 'pending', "Pre-maturity trade must stay in pending status"
    db.close()


# ---------------------------------------------------------------------------
# T2 regression tests — Lead review fixes (re-processing loop, status demotion,
# blocked recovery, audit no-dup)
# ---------------------------------------------------------------------------

def _audit_count(db, trade_id: int) -> int:
    return db.execute(
        "SELECT COUNT(*) FROM verdict_audit WHERE trade_id = ?", (trade_id,)
    ).fetchone()[0]


def test_score_all_trades_does_not_reprocess_blocked_rows():
    """An already-blocked row (matured, no price, no narrative, no verdict) must NOT be
    re-blocked/re-audited by subsequent runs — verdict_audit would otherwise grow
    unboundedly since verdict/outcome stay NULL and the row remains in scope."""
    db = _setup_db()
    log_date = date.today() - timedelta(days=35)

    db.execute("""
        INSERT INTO trade_logs (log_date, asset_id, action,
                                verification_status, decision_reason)
        VALUES (?, 'EQ_UNKN', 'buy', 'pending', 'test')
    """, (log_date,))
    trade_id = db.execute("SELECT MAX(id) FROM trade_logs").fetchone()[0]
    # No market_daily rows

    # First run: pending → verification_blocked, one audit row
    score_all_trades(db)
    row = db.execute(
        "SELECT verification_status FROM trade_logs WHERE id = ?", (trade_id,)
    ).fetchone()
    assert row[0] == 'verification_blocked', "First run must block the row"
    count_after_first = _audit_count(db, trade_id)
    assert count_after_first == 1, f"Expected 1 audit row after first run, got {count_after_first}"

    # Second run: row already blocked → NO new audit rows, status unchanged
    score_all_trades(db)
    row = db.execute(
        "SELECT verification_status FROM trade_logs WHERE id = ?", (trade_id,)
    ).fetchone()
    assert row[0] == 'verification_blocked', "Row must stay blocked on second run"
    count_after_second = _audit_count(db, trade_id)
    assert count_after_second == count_after_first, (
        f"Second run must add ZERO audit rows; count went "
        f"{count_after_first} → {count_after_second}"
    )
    db.close()


def test_score_all_trades_does_not_demote_verified_status():
    """A display-scoped row with status='verified', verdict NULL, no narrative, no price
    must NOT be flipped to verification_blocked (status-demotion guard)."""
    db = _setup_db()
    log_date = date.today() - timedelta(days=35)

    db.execute("""
        INSERT INTO trade_logs (log_date, asset_id, action,
                                verification_status, decision_reason)
        VALUES (?, 'EQ_UNKN', 'buy', 'verified', 'test')
    """, (log_date,))
    trade_id = db.execute("SELECT MAX(id) FROM trade_logs").fetchone()[0]
    # No market_daily rows, no narrative, no verdict

    score_all_trades(db)

    row = db.execute(
        "SELECT verification_status, verification_block_reason FROM trade_logs WHERE id = ?",
        (trade_id,),
    ).fetchone()
    assert row[0] == 'verified', (
        f"Scorer must never demote a 'verified' row to blocked; got '{row[0]}'"
    )
    assert row[1] is None, "block_reason must not be set on a verified row"
    db.close()


def test_score_all_trades_blocked_row_recovers_when_prices_arrive():
    """A verification_blocked row that LATER gains market prices (P9 a0 price continuity)
    must be scored: outcome_pct + verdict set, status → 'verified', block_reason cleared."""
    db = _setup_db()
    log_date = date.today() - timedelta(days=35)

    db.execute("""
        INSERT INTO trade_logs (log_date, asset_id, action,
                                verification_status, decision_reason)
        VALUES (?, 'EQ_AAPL', 'buy', 'pending', 'test')
    """, (log_date,))
    trade_id = db.execute("SELECT MAX(id) FROM trade_logs").fetchone()[0]

    # First run: no prices → blocked
    score_all_trades(db)
    row = db.execute(
        "SELECT verification_status, verification_block_reason FROM trade_logs WHERE id = ?",
        (trade_id,),
    ).fetchone()
    assert row[0] == 'verification_blocked'
    assert row[1] is not None

    # Prices arrive between runs (10% gain → good_call band)
    db.execute("INSERT INTO market_daily (code, date, close) VALUES (?, ?, ?)",
               ('AAPL', log_date, 100.0))
    db.execute("INSERT INTO market_daily (code, date, close) VALUES (?, ?, ?)",
               ('AAPL', log_date + timedelta(days=30), 110.0))

    # Second run: recovery path
    score_all_trades(db)
    row = db.execute("""
        SELECT verdict, outcome_pct, verification_status, verification_block_reason,
               verification_result
        FROM trade_logs WHERE id = ?
    """, (trade_id,)).fetchone()

    assert row[0] == 'good_call', f"Recovered row must get a verdict; got '{row[0]}'"
    assert row[1] is not None, "Recovered row must get outcome_pct"
    assert row[2] == 'verified', f"Recovered row must flip to 'verified'; got '{row[2]}'"
    assert row[3] is None, "verification_block_reason must be cleared (NULL) on recovery"
    assert row[4] == 'auto: price-based verdict at +30d', (
        f"Recovered no-narrative row must carry the auto marker; got '{row[4]}'"
    )
    db.close()


def test_score_all_trades_no_audit_rows_when_updates_empty():
    """A matured row with an EXISTING human verdict but no computable outcome (no price,
    narrative without a parseable pct) produces NO verdict_audit rows — updates are empty
    so the row is skipped entirely. Running twice must not accumulate audit rows."""
    db = _setup_db()
    log_date = date.today() - timedelta(days=35)

    # Human verdict present, outcome_pct NULL (stays in scope); narrative has no
    # percentage so text fallback also yields None; no market prices.
    db.execute("""
        INSERT INTO trade_logs (log_date, asset_id, action, verification_result,
                                verification_status, verdict, decision_reason)
        VALUES (?, 'EQ_UNKN', 'buy', '持仓观察中，无明确结论', 'verified', 'regret', 'test')
    """, (log_date,))
    trade_id = db.execute("SELECT MAX(id) FROM trade_logs").fetchone()[0]

    score_all_trades(db)
    score_all_trades(db)

    assert _audit_count(db, trade_id) == 0, (
        "No verdict_audit rows may be written for a row with empty updates "
        "(existing verdict, no computable outcome)"
    )
    row = db.execute(
        "SELECT verdict, outcome_pct, verification_status FROM trade_logs WHERE id = ?",
        (trade_id,),
    ).fetchone()
    assert row[0] == 'regret', "Human verdict untouched"
    assert row[1] is None, "outcome_pct stays NULL (nothing computable)"
    assert row[2] == 'verified', "Status untouched"
    db.close()


# ---------------------------------------------------------------------------
# Code-review fixes — KPI protection, no-op idempotence, lookback parity
# ---------------------------------------------------------------------------

def test_score_all_trades_never_verdicts_imported_verified_rows():
    """KPI protection: a narrative-less display-scoped row at status='verified'
    (reader-imported ledger row: NULL verdict, NULL narrative, linked_transaction_id set)
    must NOT be processed even when market prices exist. 2,318 such rows sit in the
    prod DB — mass-verdicting them would corrupt Review Center KPIs, which count
    verdicts unscoped."""
    db = _setup_db()
    log_date = date.today() - timedelta(days=35)

    # Mimic a reader-imported ledger row: passes display scope via linked_transaction_id,
    # no narrative, no verdict, status already 'verified'.
    db.execute("""
        INSERT INTO trade_logs (log_date, asset_id, action,
                                verification_status, linked_transaction_id)
        VALUES (?, 'EQ_AAPL', 'buy', 'verified', 42)
    """, (log_date,))
    trade_id = db.execute("SELECT MAX(id) FROM trade_logs").fetchone()[0]

    # Prices EXIST (15% gain) — the status gate, not price absence, must protect the row.
    db.execute("INSERT INTO market_daily (code, date, close) VALUES (?, ?, ?)",
               ('AAPL', log_date, 100.0))
    db.execute("INSERT INTO market_daily (code, date, close) VALUES (?, ?, ?)",
               ('AAPL', log_date + timedelta(days=30), 115.0))

    score_all_trades(db)

    row = db.execute("""
        SELECT verdict, outcome_pct, verification_status, verification_result
        FROM trade_logs WHERE id = ?
    """, (trade_id,)).fetchone()

    assert row[0] is None, f"Imported verified row must NOT get a verdict; got '{row[0]}'"
    assert row[1] is None, "Imported verified row must NOT get outcome_pct"
    assert row[2] == 'verified', "Status untouched"
    assert row[3] is None, "No auto marker may be written"
    assert _audit_count(db, trade_id) == 0, "No verdict_audit rows for imported verified rows"
    db.close()


def test_score_all_trades_within_band_gets_neutral_and_leaves_scope():
    """Addendum neutral verdict: within-band pending row gets VERDICT_NEUTRAL on first run
    and leaves scope on the second run (verdict IS NOT NULL → excluded by WHERE clause).

    Updated from the prior test 'test_score_all_trades_below_band_row_is_noop_after_first_fill'
    which expected verdict=None, status='pending' after the first run. That below-band no-op
    behavior is superseded by the neutral fallback: within-band rows now get a verdict on the
    first scoring run and leave scope — the loop always closes at maturity.

    Second run: the row has verdict='neutral' → excluded by the outer WHERE
    (verdict IS NULL OR outcome_pct IS NULL) → no audit rows added, status unchanged.
    """
    db = _setup_db()
    log_date = date.today() - timedelta(days=35)

    db.execute("""
        INSERT INTO trade_logs (log_date, asset_id, action,
                                verification_status, decision_reason)
        VALUES (?, 'EQ_AAPL', 'buy', 'pending', 'test')
    """, (log_date,))
    trade_id = db.execute("SELECT MAX(id) FROM trade_logs").fetchone()[0]

    # 2% move — below the 5% default band → both classifiers return None → neutral fires
    db.execute("INSERT INTO market_daily (code, date, close) VALUES (?, ?, ?)",
               ('AAPL', log_date, 100.0))
    db.execute("INSERT INTO market_daily (code, date, close) VALUES (?, ?, ?)",
               ('AAPL', log_date + timedelta(days=30), 102.0))

    # First run: neutral verdict + verified + outcome_pct set
    score_all_trades(db)
    row = db.execute(
        "SELECT verdict, outcome_pct, verification_status FROM trade_logs WHERE id = ?",
        (trade_id,),
    ).fetchone()
    assert row[0] == VERDICT_NEUTRAL, (
        f"Within-band row must get VERDICT_NEUTRAL on first run; got '{row[0]}'"
    )
    assert row[1] is not None and abs(float(row[1]) - 2.0) < 0.01, (
        f"outcome_pct should be ~2%, got {row[1]}"
    )
    assert row[2] == 'verified', (
        f"Row must flip to 'verified' after neutral verdict; got '{row[2]}'"
    )
    audit_after_first = _audit_count(db, trade_id)
    assert audit_after_first == 1, f"Exactly one audit row after first run; got {audit_after_first}"

    # Second run: row has verdict='neutral' → excluded from scope → full no-op
    score_all_trades(db)
    row2 = db.execute(
        "SELECT verdict, outcome_pct, verification_status FROM trade_logs WHERE id = ?",
        (trade_id,),
    ).fetchone()
    assert row2 == row, "Second run must not change the row at all (row left scope)"
    assert _audit_count(db, trade_id) == audit_after_first, (
        "Second run must add ZERO audit rows (row left scope after neutral verdict)"
    )
    db.close()


def test_score_all_trades_narrative_no_price_does_not_get_neutral():
    """Lead review guard: neutral requires a COMPUTED outcome.

    A matured row WITH a narrative but NO price data (and no parseable pct in the
    text) flows past the blocked path (blocked is no-narrative-only). Without the
    outcome_pct guard it would get verdict='neutral' + verified with outcome NULL —
    an integrity check #19 Rule B violation and a verdict asserting an outcome that
    was never measured. It must stay 'pending' untouched, with zero audit rows.
    """
    db = _setup_db()
    log_date = date.today() - timedelta(days=35)

    db.execute("""
        INSERT INTO trade_logs (log_date, asset_id, action,
                                verification_status, decision_reason,
                                verification_result)
        VALUES (?, 'EQ_NOPX', 'buy', 'pending', 'test', '持仓观察中，无明确结论')
    """, (log_date,))
    trade_id = db.execute("SELECT MAX(id) FROM trade_logs").fetchone()[0]
    # No market_daily rows for NOPX at all.

    score_all_trades(db)
    score_all_trades(db)

    row = db.execute(
        "SELECT verdict, outcome_pct, verification_status FROM trade_logs WHERE id = ?",
        (trade_id,),
    ).fetchone()
    assert row[0] is None, f"No-price narrative row must NOT get a verdict; got '{row[0]}'"
    assert row[1] is None, f"outcome_pct must stay NULL; got {row[1]}"
    assert row[2] == 'pending', f"Status must stay 'pending'; got '{row[2]}'"
    assert _audit_count(db, trade_id) == 0, "No audit rows for a no-op row"
    db.close()


def test_score_all_trades_scores_with_baseline_5_days_before_log_date():
    """Lookback parity: a trade whose nearest baseline close sits 5 days BEFORE
    log_date (within the 7-day lookback, outside the old 3-day one) must score at
    maturity instead of blocking — matching compute_outcome_to_date's window."""
    db = _setup_db()
    log_date = date.today() - timedelta(days=35)

    db.execute("""
        INSERT INTO trade_logs (log_date, asset_id, action,
                                verification_status, decision_reason)
        VALUES (?, 'EQ_AAPL', 'buy', 'pending', 'test')
    """, (log_date,))
    trade_id = db.execute("SELECT MAX(id) FROM trade_logs").fetchone()[0]

    # Baseline 5 days before log_date; +30d close present (10% gain → good_call)
    db.execute("INSERT INTO market_daily (code, date, close) VALUES (?, ?, ?)",
               ('AAPL', log_date - timedelta(days=5), 100.0))
    db.execute("INSERT INTO market_daily (code, date, close) VALUES (?, ?, ?)",
               ('AAPL', log_date + timedelta(days=30), 110.0))

    score_all_trades(db)

    row = db.execute(
        "SELECT verdict, outcome_pct, verification_status FROM trade_logs WHERE id = ?",
        (trade_id,),
    ).fetchone()
    assert row[2] != 'verification_blocked', (
        "Trade with baseline 5 days before log_date must NOT block (7d lookback parity)"
    )
    assert row[0] == 'good_call', f"Expected good_call from 10% gain; got '{row[0]}'"
    assert row[1] is not None, "outcome_pct must be computed with 7d baseline lookback"
    assert row[2] == 'verified'
    db.close()


# ---------------------------------------------------------------------------
# Addendum 2026-07-05 — VERDICT_NEUTRAL tests
# ---------------------------------------------------------------------------

def test_score_single_trade_neutral_within_band_no_keywords():
    """score_single_trade: matured pending row, price outcome +4.03% (within default 5.0 band),
    narrative without keywords, existing verdict None → verdict='neutral', status='verified'.

    This is the canonical BRK-B DCA pattern that triggered the addendum bug report.
    """
    db = _setup_db()
    log_date = date.today() - timedelta(days=35)

    # Narrative without any verdict keywords → keyword classifier returns None
    db.execute("""
        INSERT INTO trade_logs (log_date, asset_id, action, verification_result,
                                verification_status, decision_reason)
        VALUES (?, 'US_STK_BRK', 'buy', '按计划买入，DCA策略', 'pending_window', 'test')
    """, (log_date,))

    # +4.03% price outcome — within the 5% default band
    db.execute("INSERT INTO market_daily (code, date, close) VALUES (?, ?, ?)",
               ('BRK', log_date, 100.0))
    db.execute("INSERT INTO market_daily (code, date, close) VALUES (?, ?, ?)",
               ('BRK', log_date + timedelta(days=30), 104.03))

    trade_id = db.execute("SELECT MAX(id) FROM trade_logs").fetchone()[0]
    score_single_trade(db, trade_id)

    row = db.execute(
        "SELECT verdict, verification_status, outcome_pct FROM trade_logs WHERE id = ?", (trade_id,)
    ).fetchone()

    assert row[0] == VERDICT_NEUTRAL, (
        f"score_single_trade: within-band, no-keyword matured trade must get VERDICT_NEUTRAL; got '{row[0]}'"
    )
    assert row[1] == 'verified', (
        f"Neutral verdict satisfies Rule B; status must be 'verified'; got '{row[1]}'"
    )
    assert row[2] is not None, "outcome_pct must be set"

    # Audit row should exist
    audit = db.execute(
        "SELECT COUNT(*) FROM verdict_audit WHERE trade_id = ?", (trade_id,)
    ).fetchone()
    assert audit[0] > 0, "verdict_audit row must be created for neutral verdict"
    db.close()


def test_score_all_trades_neutral_no_narrative_auto_marker():
    """score_all_trades: matured pending row with NO narrative, price outcome within band →
    verdict='neutral', status='verified', auto marker set in verification_result.
    """
    db = _setup_db()
    log_date = date.today() - timedelta(days=35)

    # No verification_result (narrative-optional path)
    db.execute("""
        INSERT INTO trade_logs (log_date, asset_id, action,
                                verification_status, decision_reason)
        VALUES (?, 'US_STK_BRK', 'buy', 'pending', 'test')
    """, (log_date,))

    # +3.5% — within 5% band → neutral
    db.execute("INSERT INTO market_daily (code, date, close) VALUES (?, ?, ?)",
               ('BRK', log_date, 100.0))
    db.execute("INSERT INTO market_daily (code, date, close) VALUES (?, ?, ?)",
               ('BRK', log_date + timedelta(days=30), 103.5))

    score_all_trades(db)

    row = db.execute("""
        SELECT verdict, verification_status, verification_date, verification_result, outcome_pct
        FROM trade_logs
    """).fetchone()

    assert row[0] == VERDICT_NEUTRAL, f"Expected neutral verdict, got '{row[0]}'"
    assert row[1] == 'verified', f"Expected verified status, got '{row[1]}'"
    assert row[2] is not None, "verification_date should be set"
    assert row[3] == 'auto: price-based verdict at +30d', (
        f"Expected auto marker in verification_result, got '{row[3]}'"
    )
    assert row[4] is not None, "outcome_pct should be set"
    db.close()


def test_score_single_trade_neutral_does_not_overwrite_explicit_verdict():
    """Explicit human verdict is never overwritten even when outcome is within band."""
    db = _setup_db()
    log_date = date.today() - timedelta(days=35)

    db.execute("""
        INSERT INTO trade_logs (log_date, asset_id, action, verification_result,
                                verification_status, verdict, decision_reason)
        VALUES (?, 'US_STK_BRK', 'buy', '已手动评定为regret', 'verified', 'regret', 'test')
    """, (log_date,))

    # Within-band outcome — would trigger neutral if no existing verdict
    db.execute("INSERT INTO market_daily (code, date, close) VALUES (?, ?, ?)",
               ('BRK', log_date, 100.0))
    db.execute("INSERT INTO market_daily (code, date, close) VALUES (?, ?, ?)",
               ('BRK', log_date + timedelta(days=30), 102.0))

    trade_id = db.execute("SELECT MAX(id) FROM trade_logs").fetchone()[0]
    score_single_trade(db, trade_id)

    row = db.execute(
        "SELECT verdict FROM trade_logs WHERE id = ?", (trade_id,)
    ).fetchone()
    assert row[0] == 'regret', (
        f"Explicit human verdict must not be overwritten by neutral; got '{row[0]}'"
    )
    db.close()


def test_score_all_trades_out_of_band_gets_directional_verdict():
    """Out-of-band outcomes still get directional verdicts (neutral must not interfere)."""
    db = _setup_db()
    log_date = date.today() - timedelta(days=35)

    db.execute("""
        INSERT INTO trade_logs (log_date, asset_id, action,
                                verification_status, decision_reason)
        VALUES (?, 'US_STK_BRK', 'buy', 'pending', 'test')
    """, (log_date,))

    # +10% gain — above default 5% band → good_call (not neutral)
    db.execute("INSERT INTO market_daily (code, date, close) VALUES (?, ?, ?)",
               ('BRK', log_date, 100.0))
    db.execute("INSERT INTO market_daily (code, date, close) VALUES (?, ?, ?)",
               ('BRK', log_date + timedelta(days=30), 110.0))

    score_all_trades(db)

    row = db.execute("SELECT verdict FROM trade_logs").fetchone()
    assert row[0] == VERDICT_GOOD_CALL, (
        f"Out-of-band 10% gain must still yield VERDICT_GOOD_CALL, not neutral; got '{row[0]}'"
    )
    db.close()
