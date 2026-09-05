"""V86 — `manual_asset_pnl` + `manual_asset_pnl_audit` (#7, plan §C.1/C.3).

Owner-entered P&L for bank-bought assets. Two properties matter beyond "the
tables exist":

- the audit timestamp is **`changed_at`, not `at`** — DuckDB reserves `at`, and
  `reader_mapping_audit` already has to quote it (connector.py:1100). A test
  pins the column name so the next audit table doesn't inherit the quoting tax;
- the table is **owner data no sync may write**. That is what makes it
  re-sync-safe, so it is asserted structurally (no writer exists in the sync or
  reader packages) rather than by hoping a future `--sync-v3` behaves.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from src.database.connector import DatabaseConnector
from src.database.schema import initialize_schema

pytestmark = pytest.mark.critical

REPO_ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def migrated_db(tmp_path):
    db = DatabaseConnector(str(tmp_path / "v86.duckdb"))
    initialize_schema(db)
    db.run_migrations()
    yield db
    db.close()


def _columns(db, table: str) -> set[str]:
    rows = db.execute(
        "SELECT column_name FROM information_schema.columns WHERE table_name = ?",
        [table],
    ).fetchall()
    return {r[0] for r in rows}


def test_manual_asset_pnl_table_shape(migrated_db):
    cols = _columns(migrated_db, "manual_asset_pnl")
    assert cols == {
        "asset_id",
        "cost_basis_cny",
        "realized_pnl_cny",
        "as_of_date",
        "memo",
        "created_at",
        "updated_at",
        # V87: the balance this figure was entered against, so a later buy/sell
        # can be surfaced as "your cost is out of date" rather than silently
        # turning new principal into phantom profit.
        "market_value_at_log",
    }


def test_v87_adds_market_value_at_log(migrated_db):
    """V87 is a separate gate, so a DB that already applied V86 still gets it."""
    assert "market_value_at_log" in _columns(migrated_db, "manual_asset_pnl")
    recorded = migrated_db.execute(
        "SELECT COUNT(*) FROM schema_version WHERE version = 87"
    ).fetchone()[0]
    assert recorded == 1


def test_v88_clears_a_manufactured_pension_cost_but_not_a_real_one(tmp_path):
    """Owner ruling 2026-08-09: 个人养老金's "+¥0.00" was manufactured by
    `_zero_pl_for_non_tradeable_assets` stamping cost = market_value every sync —
    a fake measurement where a dash honestly says the cost is unknown.

    V88 clears it by the fake-zero *signature* (cost == market value within a
    cent), never by an exact decimal equality — an exact literal match is how a
    past data-fix silently touched 0 rows while burning its version gate. A
    pension row carrying a genuinely different cost must survive.
    """
    db = DatabaseConnector(str(tmp_path / "v88.duckdb"))
    initialize_schema(db)
    try:
        db.execute(
            """INSERT INTO holdings
               (asset_id, snapshot_date, quantity, market_value, cost_price_unit,
                currency, source_system, is_shadow)
               VALUES
               ('Pension_Personal', DATE '2026-07-01', 1.0, 25000.00, 25000.00,
                'CNY', 'Financial_Summary_Excel', FALSE),
               ('Pension_Other',    DATE '2026-07-01', 1.0, 50000.00, 42000.00,
                'CNY', 'Financial_Summary_Excel', FALSE),
               ('Property_阳光花园',     DATE '2026-07-01', 1.0, 2600000.00, 2820000.00,
                'CNY', 'Financial_Summary_Excel', FALSE)"""
        )
        db.run_migrations()

        def cost(aid):
            return db.execute(
                "SELECT cost_price_unit FROM holdings WHERE asset_id = ?", [aid]
            ).fetchone()[0]

        assert cost("Pension_Personal") is None, "the manufactured cost was not cleared"
        assert float(cost("Pension_Other")) == pytest.approx(42000.00), \
            "a real pension cost must survive"
        assert float(cost("Property_阳光花园")) == pytest.approx(2820000.00), \
            "Property_ keeps its real cost — only Pension_ was descoped"

        # Market value untouched: net worth cannot move.
        mv = db.execute(
            "SELECT market_value FROM holdings WHERE asset_id = 'Pension_Personal'"
        ).fetchone()[0]
        assert float(mv) == pytest.approx(25000.00)
    finally:
        db.close()


def test_pension_no_longer_gets_a_manufactured_zero_cost():
    """The prefix change behind V88 — pinned so a future edit re-adding `Pension_`
    has to face the ruling rather than silently restore the fake zero."""
    from src.sync.phases._common import NON_TRADEABLE_PREFIXES

    assert "Pension_" not in NON_TRADEABLE_PREFIXES
    assert "Property_" in NON_TRADEABLE_PREFIXES


def test_audit_table_uses_changed_at_not_the_reserved_at(migrated_db):
    cols = _columns(migrated_db, "manual_asset_pnl_audit")
    assert "changed_at" in cols
    assert "at" not in cols, "`at` is reserved in DuckDB — use changed_at (plan §C.3)"
    assert cols == {"id", "asset_id", "action", "old_value", "new_value", "changed_at"}


def test_changed_at_needs_no_quoting(migrated_db):
    """The point of the rename: the column is usable unquoted."""
    migrated_db.execute(
        "INSERT INTO manual_asset_pnl_audit (asset_id, action, old_value, new_value) "
        "VALUES ('Bond_CMB_CNY', 'upsert', NULL, '{\"realized_pnl_cny\": 4200.0}')"
    )
    row = migrated_db.execute(
        "SELECT asset_id, action, changed_at FROM manual_asset_pnl_audit ORDER BY changed_at DESC"
    ).fetchone()
    assert row[0] == "Bond_CMB_CNY"
    assert row[1] == "upsert"
    assert row[2] is not None


def test_asset_id_is_the_primary_key(migrated_db):
    """One row per asset — a second insert for the same asset must conflict."""
    migrated_db.execute(
        "INSERT INTO manual_asset_pnl (asset_id, realized_pnl_cny) VALUES ('Bond_CMB_CNY', 4200.00)"
    )
    with pytest.raises(Exception):
        migrated_db.execute(
            "INSERT INTO manual_asset_pnl (asset_id, realized_pnl_cny) VALUES ('Bond_CMB_CNY', 99.00)"
        )


def test_decimal_precision_is_two_places(migrated_db):
    """DECIMAL(20,2) — pinned because a scale mismatch silently no-ops data-fix
    migrations that key on an exact price (memory: migration-decimal-precision-noop)."""
    migrated_db.execute(
        "INSERT INTO manual_asset_pnl (asset_id, cost_basis_cny, realized_pnl_cny) "
        "VALUES ('Bond_CMB_USD', 185000.00, 4200.55)"
    )
    row = migrated_db.execute(
        "SELECT cost_basis_cny, realized_pnl_cny FROM manual_asset_pnl WHERE asset_id = 'Bond_CMB_USD'"
    ).fetchone()
    assert float(row[0]) == pytest.approx(185000.00)
    assert float(row[1]) == pytest.approx(4200.55)


def test_migration_is_idempotent_and_records_v86_once(migrated_db):
    migrated_db.execute(
        "INSERT INTO manual_asset_pnl (asset_id, realized_pnl_cny) VALUES ('Bond_CMB_CNY', 4200.00)"
    )
    migrated_db.run_migrations()  # second pass must not raise or wipe
    migrated_db.run_migrations()  # third, for good measure

    count = migrated_db.execute(
        "SELECT COUNT(*) FROM schema_version WHERE version = 86"
    ).fetchone()[0]
    assert count == 1, "V86 recorded more than once"

    surviving = migrated_db.execute(
        "SELECT realized_pnl_cny FROM manual_asset_pnl WHERE asset_id = 'Bond_CMB_CNY'"
    ).fetchone()
    assert float(surviving[0]) == pytest.approx(4200.00), "re-running migrations dropped owner data"


def test_no_sync_or_reader_module_writes_manual_asset_pnl():
    """Re-sync safety, asserted structurally.

    `manual_asset_pnl` is owner data. If any sync phase or reader ever writes it,
    a sync could silently overwrite figures the owner typed — the failure this
    guard exists to prevent. Scanning for a writer is durable in a way that
    "we ran a sync once and the row survived" is not.
    """
    write_pattern = re.compile(
        r"(INSERT\s+INTO|UPDATE|DELETE\s+FROM|CREATE\s+OR\s+REPLACE\s+TABLE)\s+manual_asset_pnl",
        re.IGNORECASE,
    )
    offenders = []
    for package in ("src/sync", "src/sources", "src/fetchers"):
        root = REPO_ROOT / package
        if not root.exists():
            continue
        for path in root.rglob("*.py"):
            if write_pattern.search(path.read_text(encoding="utf-8")):
                offenders.append(str(path.relative_to(REPO_ROOT)))

    assert not offenders, (
        "manual_asset_pnl is owner data and must never be written by a sync/reader path; "
        f"found writes in: {offenders}"
    )
