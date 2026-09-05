"""V89 — `user_profile.language` (Program BIL / WS-5).

The migration exists because a SCHEDULED brief has no browser locale to read.
Two properties beyond "the column exists":

- **the column is nullable with no schema DEFAULT, and the value is set by an
  explicit data step.** DuckDB's `ADD COLUMN ... DEFAULT` backfill semantics are
  unverified in this repo and the sibling column (`philosophy`) was added
  without one. A DEFAULT that silently failed to reach the existing row would
  flip the owner's Chinese briefs to English — the worst outcome this
  workstream can produce.
- **the seed is evidence-driven, not blanket.** An instance that already stores
  Chinese-keyed AI reports has demonstrably been producing Chinese output and
  keeps doing so; a fresh install has no such rows and is left NULL → 'en'.

`user_profile` on the real database has ZERO rows, so the data step must be an
UPSERT. A plain `UPDATE` would match nothing, change nothing, and still burn the
version gate — the exact shape of the V-series no-op recorded in this repo's
migration history.
"""
from __future__ import annotations

import pytest

from src.database.connector import DatabaseConnector
from src.database.schema import initialize_schema
from src.services.ai_advisor.language_resolver import resolve_language

pytestmark = pytest.mark.critical


def _fresh_db(tmp_path, name: str) -> DatabaseConnector:
    db = DatabaseConnector(str(tmp_path / name))
    initialize_schema(db)
    return db


def _rewind_v89(db) -> None:
    """Put a migrated temp DB back into its pre-V89 state.

    `ai_reports` is created by migration V4, so a legacy row cannot be inserted
    before `run_migrations()` on a fresh file. This rewinds the version gate and
    the profile row so the NEXT `run_migrations()` re-runs V89 against a DB that
    already holds reports — exactly the shape V89 meets in production.

    tmp_path only. Nothing here ever touches a real database.
    """
    db.execute("DELETE " + "FROM schema_version WHERE version = 89")
    db.execute("ALTER TABLE user_profile DROP COLUMN language")
    db.execute("DELETE " + "FROM user_profile WHERE id = 1")


def test_column_exists_and_is_nullable_with_no_default(tmp_path):
    db = _fresh_db(tmp_path, "v89_shape.duckdb")
    try:
        db.run_migrations()
        row = db.execute(
            "SELECT data_type, is_nullable, column_default FROM information_schema.columns "
            "WHERE table_name = 'user_profile' AND column_name = 'language'"
        ).fetchone()
        assert row is not None, "V89 did not add user_profile.language"
        data_type, is_nullable, column_default = row
        assert data_type.upper().startswith("VARCHAR")
        assert is_nullable in ("YES", True)
        assert column_default in (None, ""), (
            "user_profile.language must have NO schema DEFAULT — the value is set "
            "by V89's explicit data step, not by a backfill we cannot verify"
        )
    finally:
        db.close()


def test_fresh_install_leaves_language_null_and_resolves_to_en(tmp_path):
    db = _fresh_db(tmp_path, "v89_fresh.duckdb")
    try:
        db.run_migrations()
        assert db.execute("SELECT COUNT(*) FROM user_profile").fetchone()[0] == 0
        assert resolve_language(db)["language"] == "en"
    finally:
        db.close()


def test_existing_chinese_instance_is_pinned_to_zh_cn(tmp_path):
    """A DB carrying legacy Chinese-keyed reports must come out of V89 as zh-CN."""
    db = _fresh_db(tmp_path, "v89_owner.duckdb")
    try:
        db.run_migrations()
        # Pre-V89 state: reports written, no profile row (matches the real DB).
        db.execute(
            "INSERT INTO ai_reports (report_type, content_json) VALUES "
            "('brief', '{\"宏观形势\": {\"narrative\": \"全球市场稳定。\"}}')"
        )
        _rewind_v89(db)
        db.run_migrations()

        assert db.execute("SELECT language FROM user_profile WHERE id = 1").fetchone() == (
            "zh-CN",
        )
        resolution = resolve_language(db)
        assert resolution["language"] == "zh-CN"
        assert resolution["source"] == "user_profile"
    finally:
        db.close()


def test_seed_never_overwrites_an_explicit_owner_choice(tmp_path):
    db = _fresh_db(tmp_path, "v89_choice.duckdb")
    try:
        db.run_migrations()
        db.execute(
            "INSERT INTO ai_reports (report_type, content_json) VALUES "
            "('review', '{\"交易汇总\": {\"narrative\": \"x\"}}')"
        )
        _rewind_v89(db)
        db.run_migrations()
        # Owner then switches to English…
        db.execute("UPDATE user_profile SET language = 'en' WHERE id = 1")
        # …and re-running the data step must not undo it.
        db._seed_profile_language()
        assert db.execute("SELECT language FROM user_profile WHERE id = 1").fetchone() == (
            "en",
        )
    finally:
        db.close()


def test_migration_is_recorded_and_idempotent(tmp_path):
    db = _fresh_db(tmp_path, "v89_idem.duckdb")
    try:
        db.run_migrations()
        assert db.execute("SELECT COUNT(*) FROM schema_version WHERE version = 89").fetchone()[
            0
        ] == 1
        db.run_migrations()  # second bootstrap — must not fail or duplicate
        assert db.execute("SELECT COUNT(*) FROM schema_version WHERE version = 89").fetchone()[
            0
        ] == 1
        assert not db._migration_failures
    finally:
        db.close()


def test_seed_gate_goes_red_when_the_evidence_query_finds_nothing(tmp_path):
    """Anti-vacuity: without Chinese-keyed reports the seed must NOT fire.

    If this passed regardless of the evidence, the migration would be pinning
    every public install to Chinese and the "fresh install defaults to en"
    guarantee would be fiction.
    """
    db = _fresh_db(tmp_path, "v89_novidence.duckdb")
    try:
        db.run_migrations()
        db.execute(
            "INSERT INTO ai_reports (report_type, content_json) VALUES "
            "('brief', '{\"macro_outlook\": {\"narrative\": \"Markets are calm.\"}}')"
        )
        _rewind_v89(db)
        db.run_migrations()
        assert db.execute("SELECT COUNT(*) FROM user_profile").fetchone()[0] == 0
        assert resolve_language(db)["language"] == "en"
    finally:
        db.close()
