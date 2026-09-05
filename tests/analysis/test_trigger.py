"""Tests for analysis trigger logic."""
from __future__ import annotations
import json
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

from src.analysis.trigger import should_trigger_analysis, STALE_DAYS


def test_trigger_on_first_run():
    """No prior analysis → always trigger."""
    conn = MagicMock()
    conn.execute.return_value.fetchone.return_value = None
    triggered, reason = should_trigger_analysis("AMZN", db_conn=conn)
    assert triggered is True
    assert "first" in reason.lower() or "no prior" in reason.lower()


def test_trigger_when_stale():
    """Analysis older than STALE_DAYS → trigger. Valuation branch not reached (early return)."""
    old_dt = datetime.now() - timedelta(days=STALE_DAYS + 1)  # naive, like actual stored values
    conn = MagicMock()
    conn.execute.return_value.fetchone.return_value = (
        old_dt.isoformat(), json.dumps({}), json.dumps({})
    )
    triggered, reason = should_trigger_analysis("AMZN", db_conn=conn)
    assert triggered is True
    assert "stale" in reason.lower() or "days" in reason.lower()


def test_no_trigger_when_fresh_no_signal_change():
    """Recent analysis, no signal change → do not trigger."""
    recent_dt = datetime.now(timezone.utc) - timedelta(days=5)
    conn = MagicMock()
    conn.execute.return_value.fetchone.side_effect = [
        (recent_dt.isoformat(), json.dumps({}), json.dumps({"valuation_signal": "FAIR"})),
        ("FAIR",),  # current signal same
    ]
    triggered, reason = should_trigger_analysis("AMZN", db_conn=conn)
    assert triggered is False


def test_trigger_on_signal_change():
    """Same age but signal changed FAIR→LOW → trigger."""
    recent_dt = datetime.now(timezone.utc) - timedelta(days=5)
    conn = MagicMock()
    conn.execute.return_value.fetchone.side_effect = [
        (recent_dt.isoformat(), json.dumps({}), json.dumps({"valuation_signal": "FAIR"})),
        ("LOW",),  # signal changed
    ]
    triggered, reason = should_trigger_analysis("AMZN", db_conn=conn)
    assert triggered is True
    assert "signal" in reason.lower()


def test_stale_constant_is_30():
    assert STALE_DAYS == 30


def test_trigger_when_table_missing():
    """Missing asset_analyses table → treated as first run, not an error."""
    conn = MagicMock()
    conn.execute.side_effect = Exception("Table asset_analyses does not exist")
    triggered, reason = should_trigger_analysis("AMZN", db_conn=conn)
    assert triggered is True
    assert "first" in reason.lower() or "no prior" in reason.lower()
