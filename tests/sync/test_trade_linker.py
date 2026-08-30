import pytest

pytestmark = pytest.mark.pipeline

from datetime import datetime, timedelta
from pathlib import Path

import duckdb


def _new_conn():
    conn = duckdb.connect(":memory:")
    schema_sql = Path("src/database/schema.sql").read_text(encoding="utf-8")
    conn.execute(schema_sql)
    conn.execute(
        "ALTER TABLE trade_logs ADD COLUMN IF NOT EXISTS verification_status VARCHAR(20) DEFAULT 'pending'"
    )
    return conn


def test_link_trade_logs_to_transactions_matches_ambiguous_and_timeout():
    from src.sync.trade_linker import link_trade_logs_to_transactions
    import src.sync.trade_linker as trade_linker_mod

    conn = _new_conn()
    now = datetime.now()
    old_created_at = now - timedelta(days=20)

    # Backdate the feature gate so the timeout logic fires on our old test trade.
    original_feature_date = trade_linker_mod._VERIFICATION_FEATURE_DATE
    trade_linker_mod._VERIFICATION_FEATURE_DATE = datetime(2000, 1, 1)
    try:
        # Unique, clearly best match -> should verify.
        conn.execute(
            """
            INSERT INTO trade_logs (
                log_date, asset_id, action, quantity, price, amount,
                suggestion_source, verification_status, created_at
            ) VALUES (
                ?, 'US_STK_AAPL', 'Buy', 10, 100, 1000,
                'manual', 'pending', ?
            )
            """,
            [now.date(), now],
        )
        aapl_tx_id = conn.execute(
            """
            INSERT INTO transactions (
                transaction_date, asset_id, asset_name, transaction_type,
                quantity, price_unit, amount_gross, source_system
            ) VALUES (
                ?, 'US_STK_AAPL', 'AAPL', 'BUY',
                10, 100, 1000, 'Schwab_CSV'
            )
            RETURNING id
            """,
            [now.date()],
        ).fetchone()[0]

        # Ambiguous candidates -> should stay pending.
        conn.execute(
            """
            INSERT INTO trade_logs (
                log_date, asset_id, action, quantity, price, amount,
                suggestion_source, verification_status, created_at
            ) VALUES (
                ?, 'US_STK_AMBIG', 'Buy', 10, 100, 1000,
                'manual', 'pending', ?
            )
            """,
            [now.date(), now],
        )
        conn.execute(
            """
            INSERT INTO transactions (
                transaction_date, asset_id, asset_name, transaction_type,
                quantity, price_unit, amount_gross, source_system
            ) VALUES
                (?, 'US_STK_AMBIG', 'AMBIG', 'BUY', 10, 100, 1000, 'Schwab_CSV'),
                (?, 'US_STK_AMBIG', 'AMBIG', 'BUY', 10, 100, 1000, 'Schwab_CSV')
            """,
            [now.date(), now.date()],
        )

        # Old pending trade with no match -> should become unmatched after successful sync.
        conn.execute(
            """
            INSERT INTO trade_logs (
                log_date, asset_id, action, quantity, price, amount,
                suggestion_source, verification_status, created_at
            ) VALUES (
                ?, 'US_STK_TIMEOUT', 'Sell', 5, 50, 250,
                'manual', 'pending', ?
            )
            """,
            [(now - timedelta(days=20)).date(), old_created_at],
        )
        conn.execute(
            """
            INSERT INTO sync_audit_reports (
                id, created_at, report_type, integrity_passed, integrity_total,
                reader_counts, warnings, info_messages
            ) VALUES (
                'sync-1', ?, 'sync', 12, 12,
                '{"transactions_synced": 4, "holdings_synced": 10}',
                '[]',
                '["Trade log sync: parsed=1, inserted=1, updated=0, unchanged=0, skipped=0", "Verification sync: verifications=1, grades=1, scored=1"]'
            )
            """,
            [now],
        )

        summary = link_trade_logs_to_transactions(conn)

        rows = conn.execute(
            """
            SELECT asset_id, verification_status, linked_transaction_id
            FROM trade_logs
            ORDER BY asset_id
            """
        ).fetchall()
        by_asset = {row[0]: row[1:] for row in rows}

        # AAPL has suggestion_source='manual' (owner-recorded) → pending_window.
        assert by_asset["US_STK_AAPL"][0] == "pending_window"
        assert by_asset["US_STK_AAPL"][1] == aapl_tx_id
        assert by_asset["US_STK_AMBIG"][0] == "pending"
        assert by_asset["US_STK_AMBIG"][1] is None
        assert by_asset["US_STK_TIMEOUT"][0] == "unmatched"
        assert by_asset["US_STK_TIMEOUT"][1] is None

        assert summary["verified"] == 1
        assert summary["ambiguous"] == 1
        assert summary["unmatched"] == 1
    finally:
        trade_linker_mod._VERIFICATION_FEATURE_DATE = original_feature_date


def test_link_manual_source_fuzzy_match_becomes_pending_window():
    """Manual-source pending row matched by fuzzy pass → pending_window + linked id."""
    from src.sync.trade_linker import link_trade_logs_to_transactions

    conn = _new_conn()
    now = datetime.now()
    conn.execute(
        """
        INSERT INTO trade_logs (
            log_date, asset_id, action, quantity, price, amount,
            suggestion_source, verification_status
        ) VALUES (?, 'US_STK_NVDA', 'Buy', 5, 200, 1000, 'manual', 'pending')
        """,
        [now.date()],
    )
    tx_id = conn.execute(
        """
        INSERT INTO transactions (
            transaction_date, asset_id, asset_name, transaction_type,
            quantity, price_unit, amount_gross, source_system
        ) VALUES (?, 'US_STK_NVDA', 'NVDA', 'BUY', 5, 200, 1000, 'Schwab_CSV')
        RETURNING id
        """,
        [now.date()],
    ).fetchone()[0]

    summary = link_trade_logs_to_transactions(conn)

    row = conn.execute(
        "SELECT verification_status, linked_transaction_id FROM trade_logs WHERE asset_id = 'US_STK_NVDA'"
    ).fetchone()
    assert row[0] == "pending_window", f"manual-source should be pending_window, got {row[0]}"
    assert row[1] == tx_id
    assert summary["verified"] == 1  # counts all promotions regardless of target status


def test_link_imported_source_fuzzy_match_becomes_verified():
    """Imported-source pending row matched by fuzzy pass → verified + linked id."""
    from src.sync.trade_linker import link_trade_logs_to_transactions

    conn = _new_conn()
    now = datetime.now()
    conn.execute(
        """
        INSERT INTO trade_logs (
            log_date, asset_id, action, quantity, price, amount,
            suggestion_source, verification_status
        ) VALUES (?, 'US_STK_AMD', 'Buy', 3, 150, 450, 'imported', 'pending')
        """,
        [now.date()],
    )
    tx_id = conn.execute(
        """
        INSERT INTO transactions (
            transaction_date, asset_id, asset_name, transaction_type,
            quantity, price_unit, amount_gross, source_system
        ) VALUES (?, 'US_STK_AMD', 'AMD', 'BUY', 3, 150, 450, 'Schwab_CSV')
        RETURNING id
        """,
        [now.date()],
    ).fetchone()[0]

    link_trade_logs_to_transactions(conn)

    row = conn.execute(
        "SELECT verification_status, linked_transaction_id FROM trade_logs WHERE asset_id = 'US_STK_AMD'"
    ).fetchone()
    assert row[0] == "verified", f"imported-source should be verified, got {row[0]}"
    assert row[1] == tx_id


def test_link_null_source_pre_linked_first_pass_becomes_verified():
    """NULL-source pre-linked pending row (first pass) → verified."""
    from src.sync.trade_linker import link_trade_logs_to_transactions

    conn = _new_conn()
    now = datetime.now()
    tx_id = conn.execute(
        """
        INSERT INTO transactions (
            transaction_date, asset_id, asset_name, transaction_type,
            quantity, price_unit, amount_gross, source_system
        ) VALUES (?, 'US_STK_SPY', 'SPY', 'BUY', 10, 500, 5000, 'Schwab_CSV')
        RETURNING id
        """,
        [now.date()],
    ).fetchone()[0]
    conn.execute(
        """
        INSERT INTO trade_logs (
            log_date, asset_id, action, quantity, price, amount,
            suggestion_source, verification_status, linked_transaction_id
        ) VALUES (?, 'US_STK_SPY', 'Buy', 10, 500, 5000, NULL, 'pending', ?)
        """,
        [now.date(), tx_id],
    )

    link_trade_logs_to_transactions(conn)

    row = conn.execute(
        "SELECT verification_status FROM trade_logs WHERE asset_id = 'US_STK_SPY'"
    ).fetchone()
    assert row[0] == "verified", f"NULL-source pre-linked should be verified, got {row[0]}"


def test_link_manual_source_pre_linked_first_pass_becomes_pending_window():
    """Manual-source pre-linked pending row (first pass) → pending_window."""
    from src.sync.trade_linker import link_trade_logs_to_transactions

    conn = _new_conn()
    now = datetime.now()
    tx_id = conn.execute(
        """
        INSERT INTO transactions (
            transaction_date, asset_id, asset_name, transaction_type,
            quantity, price_unit, amount_gross, source_system
        ) VALUES (?, 'US_STK_QQQ', 'QQQ', 'BUY', 2, 400, 800, 'Schwab_CSV')
        RETURNING id
        """,
        [now.date()],
    ).fetchone()[0]
    conn.execute(
        """
        INSERT INTO trade_logs (
            log_date, asset_id, action, quantity, price, amount,
            suggestion_source, verification_status, linked_transaction_id
        ) VALUES (?, 'US_STK_QQQ', 'Buy', 2, 400, 800, 'manual', 'pending', ?)
        """,
        [now.date(), tx_id],
    )

    link_trade_logs_to_transactions(conn)

    row = conn.execute(
        "SELECT verification_status FROM trade_logs WHERE asset_id = 'US_STK_QQQ'"
    ).fetchone()
    assert row[0] == "pending_window", f"manual-source pre-linked should be pending_window, got {row[0]}"


def test_link_unmatched_manual_row_re_linked_on_retry():
    """Previously unmatched manual row + matching transaction now present → pending_window."""
    from src.sync.trade_linker import link_trade_logs_to_transactions

    conn = _new_conn()
    now = datetime.now()
    # Seed a row already in 'unmatched' state (linker timed it out previously).
    conn.execute(
        """
        INSERT INTO trade_logs (
            log_date, asset_id, action, quantity, price, amount,
            suggestion_source, verification_status
        ) VALUES (?, 'US_STK_META', 'Buy', 4, 300, 1200, 'manual', 'unmatched')
        """,
        [now.date()],
    )
    # Now a matching transaction is present.
    tx_id = conn.execute(
        """
        INSERT INTO transactions (
            transaction_date, asset_id, asset_name, transaction_type,
            quantity, price_unit, amount_gross, source_system
        ) VALUES (?, 'US_STK_META', 'META', 'BUY', 4, 300, 1200, 'Schwab_CSV')
        RETURNING id
        """,
        [now.date()],
    ).fetchone()[0]

    summary = link_trade_logs_to_transactions(conn)

    row = conn.execute(
        "SELECT verification_status, linked_transaction_id FROM trade_logs WHERE asset_id = 'US_STK_META'"
    ).fetchone()
    assert row[0] == "pending_window", f"re-linked unmatched manual row should be pending_window, got {row[0]}"
    assert row[1] == tx_id
    assert summary["verified"] == 1


def test_link_unmatched_row_stays_unmatched_when_no_candidate():
    """Previously unmatched row with no new candidates stays unmatched — no flip-flop, no error."""
    from src.sync.trade_linker import link_trade_logs_to_transactions

    conn = _new_conn()
    now = datetime.now()
    # Row already unmatched, no transactions available for it.
    conn.execute(
        """
        INSERT INTO trade_logs (
            log_date, asset_id, action, quantity, price, amount,
            suggestion_source, verification_status
        ) VALUES (?, 'US_STK_NOBODY', 'Sell', 1, 50, 50, 'manual', 'unmatched')
        """,
        [now.date()],
    )

    summary = link_trade_logs_to_transactions(conn)

    row = conn.execute(
        "SELECT verification_status, linked_transaction_id FROM trade_logs WHERE asset_id = 'US_STK_NOBODY'"
    ).fetchone()
    # Should remain unmatched — _mark_stale_pending_as_unmatched only targets 'pending',
    # and the fuzzy pass found no candidates.
    assert row[0] == "unmatched", f"should remain unmatched, got {row[0]}"
    assert row[1] is None
    assert summary["no_candidate"] == 1


def test_link_already_scored_row_after_full_replace_reset_becomes_verified():
    """Post-full-replace-reset shape: manual row with existing verdict + pending status
    + NULL linked_transaction_id must be promoted to 'verified' (not 'pending_window').

    _reset_trade_log_links resets verified rows to status='pending' WITHOUT clearing
    verdict/outcome_pct.  When the linker re-matches such a row, it must detect the
    non-NULL verdict and go straight back to 'verified' — not 'pending_window' which
    would leave the row stuck pending-visible forever (score_all_trades only processes
    rows with verdict IS NULL OR outcome_pct IS NULL).
    """
    from src.sync.trade_linker import link_trade_logs_to_transactions

    conn = _new_conn()
    now = datetime.now()

    # Seed the post-reset shape: owner-recorded, verdict already set, outcome_pct set,
    # status reset to 'pending', linked_transaction_id cleared (NULL).
    conn.execute(
        """
        INSERT INTO trade_logs (
            log_date, asset_id, action, quantity, price, amount,
            suggestion_source, verification_status, verdict, outcome_pct,
            linked_transaction_id
        ) VALUES (
            ?, 'US_STK_GOOG', 'Sell', 8, 175, 1400,
            'manual', 'pending', 'good_call', 12.5,
            NULL
        )
        """,
        [now.date()],
    )
    # Matching transaction (re-imported after full replace).
    tx_id = conn.execute(
        """
        INSERT INTO transactions (
            transaction_date, asset_id, asset_name, transaction_type,
            quantity, price_unit, amount_gross, source_system
        ) VALUES (?, 'US_STK_GOOG', 'GOOG', 'SELL', 8, 175, 1400, 'Schwab_CSV')
        RETURNING id
        """,
        [now.date()],
    ).fetchone()[0]

    summary = link_trade_logs_to_transactions(conn)

    row = conn.execute(
        "SELECT verification_status, linked_transaction_id, verdict FROM trade_logs WHERE asset_id = 'US_STK_GOOG'"
    ).fetchone()
    assert row[0] == "verified", (
        f"already-scored row after reset must promote to 'verified', got '{row[0]}'"
    )
    assert row[1] == tx_id, "linked_transaction_id must be set after re-link"
    assert row[2] == "good_call", "verdict must be preserved"
    assert summary["verified"] == 1


def test_backfill_trade_logs_from_transactions_creates_buy_sell_and_skips_operational_types():
    from src.sync.trade_linker import backfill_trade_logs_from_transactions

    conn = _new_conn()
    trade_day = datetime.now().date()

    tx_buy = conn.execute(
        """
        INSERT INTO transactions (
            transaction_date, asset_id, asset_name, transaction_type,
            quantity, price_unit, amount_gross, source_system, is_provisional
        ) VALUES (
            ?, 'US_STK_AAPL', 'AAPL', 'BUY',
            10, 100, 1000, 'Schwab_CSV', FALSE
        )
        RETURNING id
        """,
        [trade_day],
    ).fetchone()[0]
    tx_sell = conn.execute(
        """
        INSERT INTO transactions (
            transaction_date, asset_id, asset_name, transaction_type,
            quantity, price_unit, amount_gross, source_system, is_provisional
        ) VALUES (
            ?, 'US_STK_MSFT', 'MSFT', 'SELL',
            2, 300, 600, 'Schwab_CSV', FALSE
        )
        RETURNING id
        """,
        [trade_day],
    ).fetchone()[0]
    conn.execute(
        """
        INSERT INTO transactions (
            transaction_date, asset_id, asset_name, transaction_type,
            quantity, price_unit, amount_gross, source_system, is_provisional
        ) VALUES
            (?, 'US_STK_TSLA', 'TSLA', 'buy', 1, 180, 180, 'Schwab_CSV', FALSE),
            (?, 'US_STK_SCHD', 'SCHD', 'DIVIDEND', NULL, NULL, 30, 'Schwab_CSV', FALSE),
            (?, 'US_STK_SCHD', 'SCHD', 'FEE', NULL, NULL, 3, 'Schwab_CSV', FALSE),
            (?, 'RSU_AMZN', 'AMZN RSU', 'VEST', 5, 0, 0, 'RSU_Excel', FALSE)
        """,
        [trade_day, trade_day, trade_day, trade_day],
    )

    # Existing decision record near TSLA transaction should block auto-backfill for that tx.
    conn.execute(
        """
        INSERT INTO trade_logs (
            log_date, asset_id, action, amount, suggestion_source, verification_status
        ) VALUES (
            ?, 'US_STK_TSLA', 'Buy', 180, 'manual', 'pending'
        )
        """,
        [trade_day],
    )

    summary = backfill_trade_logs_from_transactions(conn)

    created = conn.execute(
        """
        SELECT asset_id, action, suggestion_source, verification_status, linked_transaction_id
        FROM trade_logs
        WHERE linked_transaction_id IS NOT NULL
        ORDER BY linked_transaction_id
        """
    ).fetchall()

    assert created == [
        ("US_STK_AAPL", "Buy", "imported", "verified", tx_buy),
        ("US_STK_MSFT", "Sell", "imported", "verified", tx_sell),
    ]
    assert summary["inserted"] == 2
    assert summary["skipped_existing"] >= 1
    # DIVIDEND/FEE/VEST are filtered by SQL WHERE clause before the loop,
    # so skipped_type counts only types that enter the loop but fail normalization
    assert summary["skipped_type"] == 0
