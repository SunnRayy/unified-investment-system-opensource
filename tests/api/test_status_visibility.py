"""No-invisible-states test for trade_logs verification_status UI filters.

Context: The P0 incident (2026-07-06) was caused by a trade_logs row whose
verification_status value was matched by ZERO of the three UI status filters
(pending | verified | all), creating a data black hole.  The trade appeared in
the DB but was invisible to every UI view.

This test enumerates every (status × verdict × verification_result) combination
that can occur in the trade lifecycle and asserts each row is visible in at
least one of the three filters — or is explicitly listed in ALLOWED_INVISIBLE
with a justification comment.  Silently excluding invisible states is not
allowed.

Canonical verification_status values (grep -rn verification_status= src/ was
used to build this list):
  pending           — default at ingest (_ingest.py:418)
  pending_window    — re-opens the verification window (connector.py:894/953)
  verified          — set by decision_scorer on outcome scoring (744/882)
  verification_blocked — set by decision_scorer when window expired (715/834)
  unmatched         — set by trade_linker when no matching transaction (246)

(decision_scorer.VERIFICATION_STATUSES covers the first four; 'unmatched' is
added here because it is written by trade_linker.py and must be visible.)
"""
from __future__ import annotations

import duckdb
import pytest

# ── Import the filter map from the route module (module-level constant) ───────
# _PENDING_VERIFICATION_STATUS_MAP is a module-level dict in ai_advisor.py;
# no refactoring needed because it already lives at module scope (added in the
# T4 filter commit).  Importing the live object means the test always reflects
# the current production filter logic.
from src.api.routes.ai_advisor import _PENDING_VERIFICATION_STATUS_MAP  # noqa: PLC2701

# ── Canonical state space ─────────────────────────────────────────────────────
ALL_STATUSES = (
    "pending",
    "pending_window",
    "verified",
    "verification_blocked",
    "unmatched",
)

VERDICTS = (None, "good_call")              # NULL vs a concrete verdict value
VERIFICATION_RESULTS = (None, "note text")  # NULL vs a non-empty narrative


# ── States intentionally hidden from all three UI filters ─────────────────────
# Each entry: (verification_status, verdict, verification_result) → reason string
# These are NOT bugs — they are documented design exclusions.  Any new entry here
# must include a justifying comment.  Do not silently exclude.
ALLOWED_INVISIBLE: dict[tuple, str] = {
    # T4 filter (adopted in the trade-log visibility fix, 2026-07):
    # verified rows with no verdict AND no narrative (verification_result) are
    # hidden from all three tabs.  These represent 2000+ bulk-imported reader
    # ledger rows that would flood the history UI with all "—" entries.
    # A verified row becomes visible once the owner assigns a verdict OR writes
    # a narrative in verification_result.
    ("verified", None, None): (
        "T4 filter: verified rows with no verdict and no narrative are "
        "intentionally hidden — they are bulk reader ledger rows (~2000+) "
        "that would pollute history with all '—' entries."
    ),
}


# ── In-memory DB fixture ───────────────────────────────────────────────────────
@pytest.fixture()
def _conn():
    """Minimal in-memory DuckDB with just the columns the filter SQL needs."""
    conn = duckdb.connect(":memory:")
    conn.execute("""
        CREATE TABLE trade_logs (
            id                   INTEGER PRIMARY KEY,
            log_date             DATE NOT NULL,
            asset_id             VARCHAR NOT NULL,
            action               VARCHAR NOT NULL,
            verification_status  VARCHAR DEFAULT 'pending',
            verdict              VARCHAR,
            verification_result  VARCHAR,
            outcome_pct          DOUBLE,
            updated_at           TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    yield conn
    conn.close()


# ── Stable row-id encoding ─────────────────────────────────────────────────────
def _row_id(status: str, verdict: str | None, vresult: str | None) -> int:
    """Encode (status, verdict, vresult) into a small, stable integer id."""
    s_idx = list(ALL_STATUSES).index(status)
    v_idx = 0 if verdict is None else 1
    r_idx = 0 if vresult is None else 1
    return s_idx * 4 + v_idx * 2 + r_idx + 1  # 1-based, range 1-20


# ── Tests ─────────────────────────────────────────────────────────────────────
def test_no_invisible_states(_conn: duckdb.DuckDBPyConnection) -> None:
    """Every (status × verdict × verification_result) row must be visible via
    at least one of the three UI status filters (pending | verified | all).

    A row matched by ZERO filters is a data black hole — the exact class of bug
    that caused the P0 incident (2026-07-06).  The only exception is rows
    listed in ALLOWED_INVISIBLE with an explicit justification.
    """
    # Insert one row per combination
    combinations: list[tuple] = []
    for status in ALL_STATUSES:
        for verdict in VERDICTS:
            for vresult in VERIFICATION_RESULTS:
                combinations.append((status, verdict, vresult))
                _conn.execute(
                    "INSERT INTO trade_logs "
                    "(id, log_date, asset_id, action, "
                    " verification_status, verdict, verification_result) "
                    "VALUES (?, '2026-01-01', 'TEST_ASSET', 'BUY', ?, ?, ?)",
                    [_row_id(status, verdict, vresult), status, verdict, vresult],
                )

    # Collect all row ids matched by any filter
    visible_ids: set[int] = set()
    for _filter_name, where_clause in _PENDING_VERIFICATION_STATUS_MAP.items():
        matched = _conn.execute(
            f"SELECT id FROM trade_logs WHERE {where_clause}"
        ).fetchall()
        for (row_id,) in matched:
            visible_ids.add(row_id)

    # Every combination must be visible OR explicitly allowed to be invisible
    invisible_found: list[str] = []
    for status, verdict, vresult in combinations:
        row_id = _row_id(status, verdict, vresult)
        if row_id not in visible_ids:
            combo_key = (status, verdict, vresult)
            if combo_key in ALLOWED_INVISIBLE:
                # Expected invisible state — justified by design
                continue
            invisible_found.append(
                f"  status={status!r}, verdict={verdict!r}, "
                f"verification_result={vresult!r}  "
                f"[row_id={row_id}] — not matched by pending|verified|all"
            )

    assert not invisible_found, (
        f"Found {len(invisible_found)} invisible state(s) — "
        "trade_logs rows matched by ZERO UI filters:\n"
        + "\n".join(invisible_found)
        + "\n\nFix: update _PENDING_VERIFICATION_STATUS_MAP in "
        "src/api/routes/ai_advisor.py to cover these states, OR add them to "
        "ALLOWED_INVISIBLE in this test with a justification comment."
    )


def test_allowed_invisible_entries_are_actually_invisible(
    _conn: duckdb.DuckDBPyConnection,
) -> None:
    """Sanity check: every ALLOWED_INVISIBLE entry must genuinely be invisible.

    If a future filter fix makes a previously-invisible state visible, the
    stale ALLOWED_INVISIBLE entry would cause confusion — this test catches it
    and forces the entry to be removed.
    """
    for (status, verdict, vresult), _reason in ALLOWED_INVISIBLE.items():
        _conn.execute(
            "INSERT INTO trade_logs "
            "(id, log_date, asset_id, action, "
            " verification_status, verdict, verification_result) "
            "VALUES (?, '2026-01-01', 'TEST_ASSET', 'BUY', ?, ?, ?)",
            [_row_id(status, verdict, vresult), status, verdict, vresult],
        )

    for filter_name, where_clause in _PENDING_VERIFICATION_STATUS_MAP.items():
        matched_ids = {
            r[0]
            for r in _conn.execute(
                f"SELECT id FROM trade_logs WHERE {where_clause}"
            ).fetchall()
        }
        for (status, verdict, vresult), reason in ALLOWED_INVISIBLE.items():
            row_id = _row_id(status, verdict, vresult)
            assert row_id not in matched_ids, (
                f"ALLOWED_INVISIBLE entry "
                f"({status!r}, verdict={verdict!r}, "
                f"verification_result={vresult!r}) "
                f"is now visible via filter={filter_name!r}. "
                "The filter was fixed — remove this entry from ALLOWED_INVISIBLE. "
                f"Original reason: {reason}"
            )


def test_filter_map_covers_all_expected_status_keys() -> None:
    """_PENDING_VERIFICATION_STATUS_MAP must have exactly the three keys the UI
    relies on: pending, verified, all.  An extra or missing key is a contract
    break with the frontend."""
    assert set(_PENDING_VERIFICATION_STATUS_MAP.keys()) == {
        "pending",
        "verified",
        "all",
    }, (
        f"Filter map keys changed: {set(_PENDING_VERIFICATION_STATUS_MAP.keys())}. "
        "Frontend relies on exactly pending|verified|all."
    )
