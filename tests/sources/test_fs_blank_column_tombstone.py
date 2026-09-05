"""Regression tests for the FS blank-column phantom holding (P1, 2026-08-01).

Defect: ``melt_financial_summary_holdings`` dropped every null/zero cell "to keep
the holdings table lean".  A mapped Financial-Summary column that went blank
therefore emitted no row at all, so the asset's last non-zero row remained the
latest snapshot and kept counting in net worth forever.  Live instance:
``CASH_Deposit_BOC_USD`` carried ~¥149K at 2026-07-01 (written by an earlier
sync, ``created_at`` 2026-07-19) while the workbook had ``美元存款_中行`` blank for
both 2026-06 and 2026-07 — the money had moved to ``Bond_CMB_USD``, so it was
counted twice.

``_shadow_stale_reader_holdings`` cannot catch this: it keys on a row's age
relative to that reader's own latest snapshot, and 2026-07-01 IS the FS reader's
latest snapshot.  Only the melt knows the column is present-in-the-sheet-but-blank.

Everything here runs the REAL melt hook against a REAL DataFrame — nothing is
mocked — and the DB assertions run against an in-memory DuckDB.
"""
from datetime import date

import pandas as pd
import pytest

pytestmark = pytest.mark.pipeline

from src.database.connector import DatabaseConnector
from src.database.schema import initialize_schema
from src.sources.reader_hooks import melt_financial_summary_holdings
from src.sync.phases._ingest import _upsert_holdings
from src.sync.phases._shadow import _shadow_stale_historical_holdings

# A representative phantom value (order-of-magnitude matches the live incident;
# any distinct non-zero value would exercise the same code path).
PHANTOM_ASSET = "CASH_Deposit_BOC_USD"
PHANTOM_VALUE = 145000.00
PHANTOM_DATE = date(2026, 7, 1)
# The stale prior-month row the same defect left behind one month earlier.
PHANTOM_PRIOR_VALUE = 144000.00
PHANTOM_PRIOR_DATE = date(2026, 6, 1)

# Mirrors the live reader_mappings fs_column set (column → (id, name, currency)).
MAPPINGS = {
    "美元存款_中行": (PHANTOM_ASSET, "中行存款 (USD)", "CNY"),
    "投资资产_美元债券_招行": ("Bond_CMB_USD", "招行美元债券 (USD)", "CNY"),
    "RMB存款_中行": ("CASH_Deposit_BOC_CNY", "中行存款 (CNY)", "CNY"),
    "固定资产_房产_阳光花园": ("Property_阳光花园", "阳光花园房产", "CNY"),
    "投资资产_银行理财_招行": ("Wealth_CMB", "招行理财", "CNY"),
}


def _sheet(rows):
    """Build a balance sheet in the shape the hook receives.

    The hook re-applies the legacy reader trim: ``dropna(how="all")`` then, when
    there are more than 3 rows, drop the first 3 (the grouping-label rows that
    sit under ``header=3``).  Prepending three filler rows here means the test
    exercises that trim rather than side-stepping it.
    """
    filler = [{"日期": pd.NaT, **{c: None for c in MAPPINGS}} for _ in range(3)]
    return pd.DataFrame(filler + rows)


def _live_shaped_rows():
    """The real workbook's trailing months for the affected columns."""
    return [
        {
            "日期": pd.Timestamp("2026-05-01"),
            "美元存款_中行": 80988.00,
            "投资资产_美元债券_招行": None,
            "RMB存款_中行": 60222.86,
            "固定资产_房产_阳光花园": 2600000.0,
            "投资资产_银行理财_招行": 150000.0,
        },
        {
            "日期": pd.Timestamp("2026-06-01"),
            # blank — the owner moved this balance into the USD bond column
            "美元存款_中行": None,
            "投资资产_美元债券_招行": 148613.337794,
            "RMB存款_中行": 74218.45,
            "固定资产_房产_阳光花园": 2600000.0,
            "投资资产_银行理财_招行": 150000.0,
        },
        {
            "日期": pd.Timestamp("2026-07-01"),
            "美元存款_中行": None,
            "投资资产_美元债券_招行": 190352.992248,
            "RMB存款_中行": 79922.84,
            "固定资产_房产_阳光花园": 2600000.0,
            "投资资产_银行理财_招行": 50201.68,
        },
    ]


@pytest.fixture
def connector():
    conn = DatabaseConnector(":memory:")
    initialize_schema(conn)
    yield conn
    conn.close()


def _net_worth(connector) -> float:
    """Canonical per-asset-latest net worth (AGENTS.md Rule 5 query 1)."""
    row = connector.execute(
        """
        WITH latest AS (
            SELECT asset_id, MAX(snapshot_date) AS max_date
            FROM holdings WHERE is_shadow = FALSE GROUP BY asset_id
        )
        SELECT COALESCE(SUM(h.market_value), 0)
        FROM holdings h
        JOIN latest l ON h.asset_id = l.asset_id AND h.snapshot_date = l.max_date
        WHERE h.is_shadow = FALSE AND h.market_value > 0
        """
    ).fetchone()
    return float(row[0])


def _per_asset_latest(connector) -> dict:
    """{asset_id: market_value} over the same per-asset-latest, positive-value set."""
    return {
        asset_id: float(value)
        for asset_id, value in connector.execute(
            """
            WITH latest AS (
                SELECT asset_id, MAX(snapshot_date) AS max_date
                FROM holdings WHERE is_shadow = FALSE GROUP BY asset_id
            )
            SELECT h.asset_id, h.market_value FROM holdings h
            JOIN latest l ON h.asset_id = l.asset_id AND h.snapshot_date = l.max_date
            WHERE h.is_shadow = FALSE AND h.market_value > 0
            """
        ).fetchall()
    }


def _sync_once(connector, sheet_df) -> None:
    """One FS holdings sync: melt → upsert → historical shadow re-derivation."""
    melted = melt_financial_summary_holdings(sheet_df, {"fs_asset_mappings": MAPPINGS})
    _upsert_holdings(connector, melted)
    _shadow_stale_historical_holdings(connector)


# ---------------------------------------------------------------------------
# 1. The melt contract
# ---------------------------------------------------------------------------


def test_blanked_column_emits_zero_tombstone_at_latest_row():
    """A mapped column blank after its last non-zero value must emit a zero row."""
    melted = melt_financial_summary_holdings(
        _sheet(_live_shaped_rows()), {"fs_asset_mappings": MAPPINGS}
    )
    phantom_rows = melted[melted["asset_id"] == PHANTOM_ASSET].set_index("snapshot_date")

    # The tombstone must exist at the LATEST sheet row — that is the only date
    # that can displace the phantom as the asset's per-asset MAX snapshot.
    latest = phantom_rows.loc[pd.Timestamp("2026-07-01")]
    assert latest["market_value"] == 0.0
    assert latest["market_price_unit"] == 0.0
    assert latest["quantity"] == 0.0

    # ...and at every earlier row of the same trailing blank run, so
    # point-in-time history for 2026-06 stops double-counting too.
    prior = phantom_rows.loc[pd.Timestamp("2026-06-01")]
    assert prior["market_value"] == 0.0

    # The real value before the run is untouched.
    assert phantom_rows.loc[pd.Timestamp("2026-05-01")]["market_value"] == 80988.00


def test_reader_rows_are_never_shadowed_by_the_melt():
    """AGENTS.md Rule 4 — the tombstone is a market_value=0 row, never is_shadow.

    ``_upsert_holdings`` forces ``is_shadow = FALSE`` on ingest by design, so a
    hook that tried to express "gone" via the shadow flag would be silently
    overwritten. The melt must not emit an is_shadow column at all.
    """
    melted = melt_financial_summary_holdings(
        _sheet(_live_shaped_rows()), {"fs_asset_mappings": MAPPINGS}
    )
    assert "is_shadow" not in melted.columns


def test_interior_blank_emits_no_row():
    """Blanks BETWEEN values are "not recorded", not "zero" — still filtered.

    The lean-table filter exists for a reason: the live workbook has 13 interior
    blanks in this one column alone, and each is followed by a real value, so the
    column self-corrects without a tombstone. Emitting them would add ~1000 junk
    rows and rewrite history.
    """
    rows = [
        {"日期": pd.Timestamp("2026-05-01"), "美元存款_中行": 80988.00},
        {"日期": pd.Timestamp("2026-06-01"), "美元存款_中行": None},  # interior
        {"日期": pd.Timestamp("2026-07-01"), "美元存款_中行": 12345.00},
    ]
    melted = melt_financial_summary_holdings(
        pd.DataFrame(rows), {"fs_asset_mappings": {"美元存款_中行": MAPPINGS["美元存款_中行"]}}
    )
    dates = set(melted["snapshot_date"])
    assert pd.Timestamp("2026-06-01") not in dates
    assert len(melted) == 2


def test_column_never_filled_emits_no_tombstones():
    """A mapped column with no non-zero value in its history must stay silent.

    Otherwise a newly-mapped column with years of leading blanks would emit one
    junk row per historical month.
    """
    rows = [
        {"日期": pd.Timestamp(f"2026-0{m}-01"), "美元存款_中行": None} for m in (5, 6, 7)
    ]
    melted = melt_financial_summary_holdings(
        pd.DataFrame(rows), {"fs_asset_mappings": {"美元存款_中行": MAPPINGS["美元存款_中行"]}}
    )
    assert melted.empty


def test_explicit_zero_in_the_workbook_emits_a_row():
    """Typing 0 into the workbook is the owner's manual "this is empty" lever.

    Before the fix it was dropped by the same filter as NaN, so entering 0 did
    not clear the phantom either.
    """
    rows = [
        {"日期": pd.Timestamp("2026-06-01"), "美元存款_中行": 80988.00},
        {"日期": pd.Timestamp("2026-07-01"), "美元存款_中行": 0},
    ]
    melted = melt_financial_summary_holdings(
        pd.DataFrame(rows), {"fs_asset_mappings": {"美元存款_中行": MAPPINGS["美元存款_中行"]}}
    )
    latest = melted[melted["snapshot_date"] == pd.Timestamp("2026-07-01")].iloc[0]
    assert latest["market_value"] == 0
    assert latest["quantity"] == 0.0


def test_absent_column_is_reported_not_tombstoned(caplog):
    """A mapped column missing from the sheet is ambiguous (rename vs deletion).

    We surface it rather than zeroing a live asset on that signal.
    """
    rows = [{"日期": pd.Timestamp("2026-07-01"), "RMB存款_中行": 79922.84}]
    with caplog.at_level("WARNING"):
        melted = melt_financial_summary_holdings(
            pd.DataFrame(rows), {"fs_asset_mappings": MAPPINGS}
        )
    assert PHANTOM_ASSET not in set(melted["asset_id"])
    assert "美元存款_中行" in caplog.text


# ---------------------------------------------------------------------------
# 2. The financial outcome (AGENTS.md Rule 1 — verify the number, not the rows)
# ---------------------------------------------------------------------------


def test_net_worth_drops_by_exactly_the_phantom(connector):
    """Net worth must fall by exactly PHANTOM_VALUE and by nothing else."""
    # Pre-fix DB state: two syncs' worth of FS rows, including the two phantom
    # rows written back when the column still carried a value.
    pre_fix_rows = [
        (PHANTOM_PRIOR_DATE, PHANTOM_ASSET, PHANTOM_PRIOR_VALUE),
        (PHANTOM_DATE, PHANTOM_ASSET, PHANTOM_VALUE),
        (PHANTOM_PRIOR_DATE, "Bond_CMB_USD", 148613.34),
        (PHANTOM_DATE, "Bond_CMB_USD", 190352.99),
        (PHANTOM_DATE, "CASH_Deposit_BOC_CNY", 79922.84),
        (PHANTOM_DATE, "Property_阳光花园", 2600000.0),
        (PHANTOM_DATE, "Wealth_CMB", 50201.68),
    ]
    for snap, asset_id, value in pre_fix_rows:
        connector.execute(
            """
            INSERT INTO holdings (
                snapshot_date, asset_id, asset_name, asset_type, quantity, unit,
                market_price_unit, market_value, currency, account, source_system
            ) VALUES (?, ?, ?, 'cash', 1.0, 'unit', ?, ?, 'CNY',
                      'Financial_Summary', 'Financial_Summary_Excel')
            """,
            (snap, asset_id, asset_id, value, value),
        )
    _shadow_stale_historical_holdings(connector)

    before = _net_worth(connector)
    per_asset_before = _per_asset_latest(connector)
    assert per_asset_before[PHANTOM_ASSET] == pytest.approx(PHANTOM_VALUE)

    _sync_once(connector, _sheet(_live_shaped_rows()))

    after = _net_worth(connector)
    assert before - after == pytest.approx(PHANTOM_VALUE, abs=0.01), (
        f"net worth must fall by exactly the phantom: {before} -> {after}"
    )

    # ...and nothing else moved. Every other asset keeps its value; the phantom
    # is gone from the positive-value set entirely.
    per_asset_after = _per_asset_latest(connector)
    assert PHANTOM_ASSET not in per_asset_after
    assert per_asset_after == {
        k: v for k, v in per_asset_before.items() if k != PHANTOM_ASSET
    }


def test_blanked_column_does_not_resurrect_on_a_later_sync(connector):
    """Re-syncing the same (still blank) workbook must keep the asset at zero.

    ``_upsert_holdings`` deliberately resets ``is_shadow = FALSE`` on every
    re-ingest, so an approach that relied on the shadow flag would resurrect the
    phantom here.
    """
    connector.execute(
        """
        INSERT INTO holdings (
            snapshot_date, asset_id, asset_name, asset_type, quantity, unit,
            market_price_unit, market_value, currency, account, source_system
        ) VALUES (?, ?, ?, 'cash', 1.0, 'unit', ?, ?, 'CNY',
                  'Financial_Summary', 'Financial_Summary_Excel')
        """,
        (PHANTOM_DATE, PHANTOM_ASSET, PHANTOM_ASSET, PHANTOM_VALUE, PHANTOM_VALUE),
    )

    sheet = _sheet(_live_shaped_rows())
    for _ in range(3):
        _sync_once(connector, sheet)

        active = connector.execute(
            """
            WITH latest AS (
                SELECT asset_id, MAX(snapshot_date) AS max_date
                FROM holdings WHERE is_shadow = FALSE AND asset_id = ?
                GROUP BY asset_id
            )
            SELECT h.market_value, h.quantity FROM holdings h
            JOIN latest l ON h.asset_id = l.asset_id AND h.snapshot_date = l.max_date
            WHERE h.is_shadow = FALSE
            """,
            (PHANTOM_ASSET,),
        ).fetchall()
        assert len(active) == 1
        assert float(active[0][0]) == 0.0
        assert float(active[0][1]) == 0.0

    # Idempotent: three syncs must not accumulate duplicate rows.
    total = connector.execute(
        "SELECT COUNT(*) FROM holdings WHERE asset_id = ?", (PHANTOM_ASSET,)
    ).fetchone()[0]
    assert total == 3  # 2026-05 real value + 2026-06 and 2026-07 tombstones
