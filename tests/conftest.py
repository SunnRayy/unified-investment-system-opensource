import os
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest
from src.database.connector import DatabaseConnector

# Repo root = parent of tests/
_REPO_ROOT = Path(__file__).resolve().parent.parent
# Tracked config files that the import-adapter approve flow (ADR-018 convergence)
# writes to using real default paths. Without protection, approve-flow tests
# (test_import_adapter_routes, test_service, test_import_adapter_sync) mutate
# these shared files and break later registry-golden / co-authority tests
# (order-dependent failures) and leave the working tree dirty.
_PROTECTED_CONFIG = [
    _REPO_ROOT / "config" / "source_authority.yaml",
    _REPO_ROOT / "config" / "settings.yaml",
]
_READERS_DIR = _REPO_ROOT / "config" / "readers"


@pytest.fixture(scope="session", autouse=True)
def _uis_seed_profile_example():
    """Run the whole test session under $UIS_SEED_PROFILE=example (Program
    OSR WS-3b, refinement 3, session-scoped autouse per the architect's
    request — the alternative was per-test opt-in, but seeds/example's
    vocab is byte-identical to the legacy hardcoded defaults for every
    reader except financial_summary (see seeds/README.md / WS-3a), so
    flipping it globally is a no-op for every non-FS test and is what
    unblocks the synthetic Financial Summary fixture's persona-renamed
    columns (固定资产_房产_阳光花园 etc.) to melt correctly wherever a test
    routes through src.services.reader_mappings.load_reader_mappings.

    Production is unaffected: this only sets the env var inside the test
    process; Cloud Run has no UIS_SEED_PROFILE set and stays on
    _legacy_defaults() (see reader_mappings.py's _get_defaults()).
    """
    os.environ["UIS_SEED_PROFILE"] = "example"
    yield
    os.environ.pop("UIS_SEED_PROFILE", None)


@pytest.fixture(autouse=True)
def _protect_real_config():
    """Snapshot shared config before each test; restore after.

    Keeps tests hermetic w.r.t. the real config/ tree: restores any mutated
    tracked config file and removes stray reader YAMLs a test created.
    """
    before = {p: p.read_bytes() for p in _PROTECTED_CONFIG if p.exists()}
    readers_before = set(_READERS_DIR.glob("*.yaml")) if _READERS_DIR.exists() else set()
    try:
        yield
    finally:
        for p, data in before.items():
            if not p.exists() or p.read_bytes() != data:
                # Atomic restore (temp file + os.replace). A plain write_bytes()
                # truncates in place, and under pytest-xdist another worker
                # reading the file mid-write (e.g. load_config()) sees torn
                # YAML → nondeterministic ParserError (broke deploy CI 2026-07-06).
                fd, tmp_path = tempfile.mkstemp(dir=str(p.parent), prefix=f".{p.name}.")
                try:
                    with os.fdopen(fd, "wb") as fh:
                        fh.write(data)
                    os.replace(tmp_path, p)
                except BaseException:
                    if os.path.exists(tmp_path):
                        os.unlink(tmp_path)
                    raise
        if _READERS_DIR.exists():
            for f in _READERS_DIR.glob("*.yaml"):
                if f not in readers_before:
                    f.unlink()


@pytest.fixture(autouse=True)
def _mock_create_backup():
    """Never write a real production-DB backup during any test.

    `run_full_sync_v3` calls `create_backup(reason="pre-sync-v3")`, and
    `create_backup` resolves DEFAULT_DB_PATH (`data/unified.duckdb`) regardless of
    the `:memory:`/temp connector the test passed in — so every test that drives
    the real orchestrator copied the ~99 MB production DB into `data/backups/`.
    A full suite run wrote ~200 MB per run (verified 2026-08-02: integration +
    api/pipeline tests, outside `tests/sync/` where the local guard didn't reach).

    Patches only the orchestrator's reference, so `tests/database/test_backup.py`
    (which imports `create_backup` directly and uses `tmp_path`) is unaffected, and
    backup-behavior tests that re-`patch()` it inside their own with-block still win.
    """
    with patch(
        "src.sync.orchestrator.create_backup",
        return_value="/tmp/mock-backup-noop.duckdb",
    ):
        yield


@pytest.fixture
def clean_db():
    """In-memory DuckDB with minimal schema for integrity tests."""
    db = DatabaseConnector(":memory:")

    # Create the tables needed by integrity checks
    db.execute("""
        CREATE TABLE holdings (
            id INTEGER PRIMARY KEY,
            asset_id VARCHAR(100) NOT NULL,
            asset_name VARCHAR(200),
            source_system VARCHAR(50),
            snapshot_date DATE NOT NULL,
            quantity DOUBLE,
            market_price_unit DOUBLE,
            market_value DOUBLE,
            cost_price_unit DOUBLE,
            currency VARCHAR(10) DEFAULT 'CNY',
            is_shadow BOOLEAN DEFAULT FALSE,
            is_provisional BOOLEAN DEFAULT FALSE
        )
    """)

    db.execute("""
        CREATE TABLE transactions (
            id INTEGER PRIMARY KEY,
            asset_id VARCHAR(100) NOT NULL,
            source_system VARCHAR(50),
            transaction_date DATE NOT NULL,
            transaction_type VARCHAR(50),
            quantity DOUBLE,
            price_unit DOUBLE,
            amount_net DOUBLE,
            currency VARCHAR(10) DEFAULT 'CNY',
            is_provisional BOOLEAN DEFAULT FALSE
        )
    """)

    db.execute("""
        CREATE TABLE sync_audit_logs (
            id INTEGER PRIMARY KEY,
            sync_timestamp TIMESTAMP,
            source_system VARCHAR(50),
            target_table VARCHAR(50),
            record_key VARCHAR(200),
            conflict_type VARCHAR(50),
            source_value VARCHAR(500),
            target_value VARCHAR(500),
            resolution VARCHAR(50),
            resolution_notes VARCHAR(500),
            is_resolved BOOLEAN DEFAULT FALSE,
            resolved_at TIMESTAMP,
            resolved_by VARCHAR(50)
        )
    """)

    db.execute("""
        CREATE TABLE asset_registry (
            id INTEGER PRIMARY KEY,
            canonical_id VARCHAR(100) NOT NULL,
            asset_name VARCHAR(200),
            source_system VARCHAR(50),
            asset_class VARCHAR(100),
            is_rebalanceable BOOLEAN DEFAULT TRUE
        )
    """)

    db.execute("""
        CREATE TABLE taxonomy_classes (
            id INTEGER PRIMARY KEY,
            name VARCHAR(100) NOT NULL,
            parent_id INTEGER,
            is_rebalanceable BOOLEAN DEFAULT TRUE
        )
    """)

    # asset_taxonomy was dropped in Migration 16 (Pass F) — no longer created here

    db.execute("""
        CREATE TABLE balance_sheet_monthly (
            id INTEGER PRIMARY KEY,
            snapshot_date DATE NOT NULL,
            payload JSON
        )
    """)

    yield db
    db.close()

@pytest.fixture
def mock_db():
    pass
