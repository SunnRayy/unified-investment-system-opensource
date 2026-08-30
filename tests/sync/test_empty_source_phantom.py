"""Regression tests for the empty-source phantom snapshot (task #16, 2026-08-02).

Defect: ``_shadow_stale_non_tradable_holdings`` shadows rows whose
``snapshot_date < MAX(snapshot_date)`` for their source. When a source emits **no
rows at all** — total liquidation, empty workbook — ``MAX`` never advances, the
previous rows sit exactly *at* that date, ``<`` is false, and the whole last
snapshot stays active forever. Same "invisible states" class as the V7.8.1
Financial-Summary blank-column phantom: absence is indistinguishable from
"no update".

Two directions, and the second matters more: (1) a source that RAN and
legitimately yields zero must stop counting, net worth dropping by exactly that
snapshot's value; (2) a MISSING / DISABLED / VALIDATION-FAILED / raised source
must NOT be zeroed — zeroing a live portfolio because someone's OneDrive was
still syncing is the far more damaging error.

All three non-tradable sources are covered, against an in-memory DuckDB and the
real functions — nothing is mocked except the reader boundary.
"""
from datetime import date
from unittest.mock import patch

import pandas as pd
import pytest

pytestmark = pytest.mark.pipeline

from src.database.connector import DatabaseConnector
from src.database.schema import initialize_schema
from src.sources.base import (
    READ_STATUS_DISABLED, READ_STATUS_KEY, READ_STATUS_OK, READ_STATUS_READ_ERROR,
    READ_STATUS_SOURCE_MISSING, READ_STATUS_VALIDATION_FAILED, read_status_of,
)
from src.sync.orchestrator import (
    _record_empty_source_signal, _run_gold_reader, _run_insurance_reader, _run_rsu_reader,
)
from src.sync.phases._common import SyncResult
from src.sync.phases._shadow import (
    _shadow_stale_non_tradable_holdings, _shadow_stale_reader_holdings,
    _tombstone_empty_verified_sources,
)

# The last snapshot each source left behind before it went quiet, and the
# deterministic tombstone date. Rows: (asset_id, name, type, unit, qty, market_value).
LAST_SNAPSHOT = date(2026, 7, 1)
TOMBSTONE_DATE = date(2026, 7, 20)
SOURCE_FIXTURES = {
    "Gold_Excel": [
        ("ALTS_Paper_Gold", "纸黄金", "Alternative", "gram", 293.3157, 266263.00),
        ("GOLD_ETF_518880", "黄金ETF", "Alternative", "share", 1000.0, 12000.00),
    ],
    "Insurance_Excel": [
        ("INS_Policy_A", "重疾险 A", "Insurance", "policy", 0.0, 41713.00),
        ("INS_Policy_B", "年金险 B", "Insurance", "policy", 0.0, 12000.00),
    ],
    "RSU_Excel": [
        ("RSU_AMZN", "Amazon RSU", "Equity Compensation", "share", 7.15, 126723.57),
    ],
}


@pytest.fixture
def connector():
    conn = DatabaseConnector(":memory:")
    initialize_schema(conn)
    yield conn
    conn.close()


def _seed(conn, source, rows, snapshot_date=LAST_SNAPSHOT):
    for asset_id, name, asset_type, unit, qty, mv in rows:
        conn.execute(
            """
            INSERT INTO holdings (
                snapshot_date, asset_id, asset_name, asset_type,
                quantity, unit, cost_price_unit, market_price_unit, market_value,
                currency, account, source_system, is_shadow
            ) VALUES (?, ?, ?, ?, ?, ?, 0, 0, ?, 'CNY', 'Test', ?, FALSE)
            """,
            (snapshot_date, asset_id, name, asset_type, qty, unit, mv, source),
        )


def _net_worth(conn):
    """Production net-worth shape: per-asset latest, active, positive value."""
    row = conn.execute(
        """
        WITH latest_per_asset AS (
            SELECT asset_id, MAX(snapshot_date) AS max_date
            FROM holdings WHERE is_shadow = FALSE GROUP BY asset_id
        )
        SELECT COALESCE(SUM(h.market_value), 0)
        FROM holdings h
        JOIN latest_per_asset l ON h.asset_id = l.asset_id AND h.snapshot_date = l.max_date
        WHERE h.is_shadow = FALSE AND h.market_value > 0
        """
    ).fetchone()
    return float(row[0])


def _sweep(conn, empty_verified=None):
    """The P4 order: tombstone first, then both staleness sweeps."""
    written = _tombstone_empty_verified_sources(conn, empty_verified, as_of_date=TOMBSTONE_DATE)
    _shadow_stale_reader_holdings(conn, empty_verified)
    _shadow_stale_non_tradable_holdings(conn, empty_verified)
    return written


# ---------------------------------------------------------------------------
# 1. The phantom is gone — a verified-empty source stops counting
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("source", sorted(SOURCE_FIXTURES))
def test_verified_empty_source_no_longer_leaves_its_last_snapshot_active(connector, source):
    rows = SOURCE_FIXTURES[source]
    _seed(connector, source, rows)
    phantom_value = sum(r[5] for r in rows)

    before = _net_worth(connector)
    assert before == pytest.approx(phantom_value)

    _sweep(connector, {source})

    after = _net_worth(connector)
    assert after == pytest.approx(before - phantom_value), (
        f"net worth must fall by exactly the phantom {phantom_value} for {source}"
    )
    assert after == 0.0


def test_net_worth_drops_by_exactly_the_phantom_with_other_sources_untouched(connector):
    """Only the empty source moves; every other source's value is preserved."""
    _seed(connector, "Gold_Excel", SOURCE_FIXTURES["Gold_Excel"])
    _seed(connector, "Insurance_Excel", SOURCE_FIXTURES["Insurance_Excel"])
    _seed(connector, "RSU_Excel", SOURCE_FIXTURES["RSU_Excel"])
    _seed(
        connector, "Schwab_CSV",
        [("US_STK_SGOV", "SGOV", "US Equity", "share", 100.0, 700000.00)],
    )

    before = _net_worth(connector)
    gold_value = sum(r[5] for r in SOURCE_FIXTURES["Gold_Excel"])

    _sweep(connector, {"Gold_Excel"})

    assert _net_worth(connector) == pytest.approx(before - gold_value)
    # 278,263.00 is the reproduction figure from the task write-up.
    assert gold_value == pytest.approx(278263.00)


def test_tombstone_is_the_latest_active_snapshot_and_carries_zero(connector):
    _seed(connector, "Gold_Excel", SOURCE_FIXTURES["Gold_Excel"])
    _sweep(connector, {"Gold_Excel"})

    row = connector.execute(
        """
        SELECT snapshot_date, quantity, market_value, is_shadow, price_source
        FROM holdings
        WHERE asset_id = 'ALTS_Paper_Gold' AND source_system = 'Gold_Excel'
          AND snapshot_date = (
              SELECT MAX(snapshot_date) FROM holdings
              WHERE asset_id = 'ALTS_Paper_Gold' AND source_system = 'Gold_Excel'
          )
        """
    ).fetchone()
    assert row[0] == TOMBSTONE_DATE
    assert float(row[1]) == 0.0
    assert float(row[2]) == 0.0
    assert row[3] is False, "the tombstone must be ACTIVE — it is the source's current truth"
    assert row[4] == "empty_source_tombstone"


def test_empty_source_sweep_is_idempotent(connector):
    """A source that stays empty must not accrue one tombstone row per sync."""
    _seed(connector, "Insurance_Excel", SOURCE_FIXTURES["Insurance_Excel"])

    first = _sweep(connector, {"Insurance_Excel"})
    after_first = _net_worth(connector)
    second = _tombstone_empty_verified_sources(
        connector, {"Insurance_Excel"}, as_of_date=date(2026, 7, 21)
    )

    assert first == 2
    assert second == 0, "already-zeroed assets must not be tombstoned again"
    assert _net_worth(connector) == pytest.approx(after_first)


def test_tombstone_zeroes_a_same_day_row_rather_than_colliding(connector):
    """RSU stamps snapshot_date = today, so the tombstone can land on an existing row."""
    _seed(connector, "RSU_Excel", SOURCE_FIXTURES["RSU_Excel"], snapshot_date=TOMBSTONE_DATE)

    written = _tombstone_empty_verified_sources(
        connector, {"RSU_Excel"}, as_of_date=TOMBSTONE_DATE
    )

    assert written == 1
    rows = connector.execute(
        "SELECT quantity, market_value FROM holdings WHERE source_system = 'RSU_Excel'"
    ).fetchall()
    assert len(rows) == 1, "ON CONFLICT must update in place, not duplicate the row"
    assert float(rows[0][0]) == 0.0 and float(rows[0][1]) == 0.0
    assert _net_worth(connector) == 0.0


def test_empty_source_history_is_frozen_so_check6_stays_satisfied(connector):
    """Integrity check #6 (BLOCKING) inspects the source's newest QTY-BEARING row.

    Shadowing that row rather than superseding it with a zero is what
    `shadow_mutual_exclusion` reads as "reader data is invisible", so the
    staleness sweeps must leave a verified-empty source's history alone.
    """
    _seed(connector, "Gold_Excel", SOURCE_FIXTURES["Gold_Excel"])
    # A prior sell, i.e. the transaction-signal sweep would otherwise bite.
    connector.execute(
        """
        INSERT INTO transactions (
            transaction_date, asset_id, asset_name, transaction_type,
            quantity, price_unit, amount_gross, amount_net, commission_fee,
            currency, account, memo, source_system, is_provisional
        ) VALUES ('2026-07-15', 'ALTS_Paper_Gold', '纸黄金', 'sell',
                  293.3157, 907.77, 266263, 266263, 0, 'CNY', 'Test', NULL,
                  'Gold_Excel', FALSE)
        """
    )

    _sweep(connector, {"Gold_Excel"})

    violations = connector.execute(
        """
        WITH latest_source_sync AS (
            SELECT source_system, MAX(snapshot_date) AS latest_date
            FROM holdings WHERE COALESCE(quantity, 0) != 0 GROUP BY source_system
        )
        SELECT COUNT(*)
        FROM holdings h
        JOIN latest_source_sync lss ON h.source_system = lss.source_system
             AND h.snapshot_date = lss.latest_date
        WHERE h.source_system = 'Gold_Excel'
          AND h.is_shadow = TRUE
          AND COALESCE(h.quantity, 0) != 0
        """
    ).fetchone()[0]
    assert violations == 0, "shadow_mutual_exclusion (BLOCKING) would fail"


def test_normal_staleness_sweep_still_works_when_nothing_is_empty(connector):
    """The pre-existing behaviour must be untouched when no source reported zero."""
    _seed(connector, "Gold_Excel", SOURCE_FIXTURES["Gold_Excel"], snapshot_date=date(2026, 6, 1))
    _seed(connector, "Gold_Excel", SOURCE_FIXTURES["Gold_Excel"], snapshot_date=LAST_SNAPSHOT)

    shadowed = _shadow_stale_non_tradable_holdings(connector)

    assert shadowed == 2, "older snapshot rows must still be shadowed"
    assert _net_worth(connector) == pytest.approx(278263.00)


# ---------------------------------------------------------------------------
# 2. The dangerous direction — unverified emptiness must change NOTHING
# ---------------------------------------------------------------------------

UNVERIFIED_STATUSES = [
    READ_STATUS_DISABLED,
    READ_STATUS_SOURCE_MISSING,
    READ_STATUS_VALIDATION_FAILED,
    READ_STATUS_READ_ERROR,
    "some_status_added_next_year",  # fail-closed on unknown values
]


@pytest.mark.parametrize("source", sorted(SOURCE_FIXTURES))
@pytest.mark.parametrize("status", UNVERIFIED_STATUSES)
def test_unverified_empty_source_is_never_zeroed(connector, source, status):
    _seed(connector, source, SOURCE_FIXTURES[source])
    before = _net_worth(connector)
    result = SyncResult(success=True)

    _record_empty_source_signal(connector, result, source, status, holdings_row_count=0)
    _sweep(connector, result.empty_verified_sources)

    assert result.empty_verified_sources == set(), (
        f"{source} with read_status={status} must NOT be licensed to zero itself"
    )
    assert _net_worth(connector) == pytest.approx(before)
    assert any("[EMPTY-SOURCE]" in w for w in result.warnings), (
        "the ambiguous case must be LOUD, not silent"
    )


_LEGACY_PAYLOAD = {"holdings": pd.DataFrame(), "transactions": pd.DataFrame()}
_INVALID_PAYLOAD = dict(_LEGACY_PAYLOAD, **{READ_STATUS_KEY: READ_STATUS_VALIDATION_FAILED})

# (id, source_system, runner, source config, patch target, patch kwargs)
END_TO_END_CASES = [
    ("gold_workbook_missing", "Gold_Excel", _run_gold_reader,
     {"enabled": True, "data_dir": "/nonexistent-uis"}, "sync_gold", None),
    ("gold_disabled", "Gold_Excel", _run_gold_reader, {"enabled": False}, "sync_gold", None),
    ("insurance_disabled", "Insurance_Excel", _run_insurance_reader,
     {"enabled": False}, "sync_insurance", None),
    ("rsu_disabled", "RSU_Excel", _run_rsu_reader, {"enabled": False}, "sync_rsu", None),
    ("rsu_reader_raised", "RSU_Excel", _run_rsu_reader, {"enabled": True}, "sync_rsu",
     {"side_effect": RuntimeError("boom")}),
    ("insurance_validation_failed", "Insurance_Excel", _run_insurance_reader,
     {"enabled": True}, "sync_insurance", {"return_value": _INVALID_PAYLOAD}),
    # A reader written before this contract omits the key entirely — fail closed.
    ("gold_legacy_payload_no_status", "Gold_Excel", _run_gold_reader,
     {"enabled": True}, "sync_gold", {"return_value": _LEGACY_PAYLOAD}),
]


@pytest.mark.parametrize(
    "case_id,source_system,runner,src_cfg,patch_name,patch_kwargs",
    END_TO_END_CASES,
    ids=[c[0] for c in END_TO_END_CASES],
)
def test_end_to_end_unverified_reader_never_zeroes(
    connector, case_id, source_system, runner, src_cfg, patch_name, patch_kwargs
):
    """The real reader path, every ambiguous shape — nothing may be zeroed."""
    reader_key = {"Gold_Excel": "gold", "Insurance_Excel": "insurance",
                  "RSU_Excel": "rsu"}[source_system]
    _seed(connector, source_system, SOURCE_FIXTURES[source_system])
    before = _net_worth(connector)
    result = SyncResult(success=True)
    config = {"source_registry": {reader_key: src_cfg}}

    if patch_kwargs:
        with patch(f"src.sync.orchestrator.{patch_name}", **patch_kwargs):
            runner(connector, config, result)
    else:
        runner(connector, config, result)
    _sweep(connector, result.empty_verified_sources)

    assert result.empty_verified_sources == set(), f"{case_id} must not license a zeroing"
    assert _net_worth(connector) == pytest.approx(before)
    assert any("[EMPTY-SOURCE]" in w for w in result.warnings), "must be LOUD, not silent"


def test_read_status_of_fails_closed(connector):
    assert read_status_of({"holdings": pd.DataFrame()}) == READ_STATUS_READ_ERROR
    assert read_status_of(None) == READ_STATUS_READ_ERROR
    assert read_status_of({READ_STATUS_KEY: READ_STATUS_OK}) == READ_STATUS_OK


def test_signal_is_silent_when_there_is_nothing_to_say(connector):
    """No-op on rows returned; and no alert-fatigue warning with nothing to lose."""
    result = SyncResult(success=True)
    _record_empty_source_signal(connector, result, "Gold_Excel", READ_STATUS_OK, 3)
    _record_empty_source_signal(connector, result, "Gold_Excel", READ_STATUS_DISABLED, 0)
    assert result.empty_verified_sources == set()
    assert result.warnings == []


# ---------------------------------------------------------------------------
# 3. Reader-boundary contract — the status must survive the real reader path
# ---------------------------------------------------------------------------

def test_real_gold_reader_distinguishes_empty_from_missing_from_disabled(tmp_path):
    """The whole fix rests on these three being told apart at the reader boundary."""
    import openpyxl

    from src.sync.gold_sync import sync_gold

    def _read(**cfg):
        return sync_gold({"source_registry": {"gold": cfg}})

    # (a) missing workbook — data_dir exists, file does not
    missing = _read(enabled=True, data_dir=str(tmp_path))
    assert missing["holdings"].empty
    assert read_status_of(missing) == READ_STATUS_SOURCE_MISSING

    # (b) disabled
    assert read_status_of(_read(enabled=False)) == READ_STATUS_DISABLED

    # (c) a well-formed workbook with a header row and no data — an affirmative zero
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "黄金持仓"
    ws.append(["资产类别", "标的名称", "持有数量", "单位", "平均成本价",
               "单价", "当前市值", "未实现盈亏", "交易账户"])
    ws_t = wb.create_sheet("黄金交易记录")
    ws_t.append(["交易日期", "资产类别", "标的名称", "交易类型", "金额",
                 "数量", "价格", "手续费", "交易账户"])
    wb.save(tmp_path / "Gold_transactions.xlsx")

    empty = _read(enabled=True, data_dir=str(tmp_path),
                  file_patterns={"workbook": "Gold_transactions.xlsx"})
    assert empty["holdings"].empty
    assert read_status_of(empty) == READ_STATUS_OK


def test_real_insurance_and_rsu_readers_report_disabled():
    from src.sync.insurance_sync import sync_insurance
    from src.sync.rsu_sync import sync_rsu

    assert read_status_of(
        sync_insurance({"source_registry": {"insurance": {"enabled": False}}})
    ) == READ_STATUS_DISABLED
    assert read_status_of(
        sync_rsu({"source_registry": {"rsu": {"enabled": False}}})
    ) == READ_STATUS_DISABLED
