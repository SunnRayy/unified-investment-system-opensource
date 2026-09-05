"""Shared fixtures for tests/sync/.

The `_mock_create_backup` autouse fixture that used to live here was hoisted to
the root `tests/conftest.py` (2026-08-02) so it protects EVERY test, not just
`tests/sync/` — real production-DB backups were still being written by
orchestrator callers under `tests/integration/` and `tests/api/`. Nothing
sync-specific is needed here anymore.
"""
