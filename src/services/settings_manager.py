"""Shared settings manager — loads and writes config/settings.yaml.

Extracted from src/api/routes/ai_advisor.py so both the ai_advisor router
and the new settings router share the same YAML read/write logic.
"""

from __future__ import annotations

import fcntl
import logging
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from fastapi import HTTPException

logger = logging.getLogger(__name__)

SETTINGS_PATH = Path(__file__).parents[2] / "config" / "settings.yaml"
SETTINGS_LOCK_PATH = SETTINGS_PATH.with_suffix(".lock")


def _settings_read_path() -> Path:
    """Where to READ settings from — never where to write them.

    Reads may fall back to the committed `.example` template so a fresh clone
    works (Program OSR untracked the real config); in cloud mode the resolver
    refuses that fallback and raises.

    **Writes must never use this.** Every writer in this module is a
    read-modify-write that ends in `os.replace(tmp, SETTINGS_PATH)`, and one
    of them uploads `SETTINGS_PATH` to GCS. If the write target resolved to the
    template, the first settings change on a fresh clone would overwrite the
    committed `settings.example.yaml` — and on cloud, publish the template as
    the production config. So writes stay pinned to SETTINGS_PATH, which
    materialises a real settings.yaml seeded from the template on first save.
    That is the documented quickstart flow ("copy the template, then edit"),
    just performed by the app instead of by hand.
    """
    from src.config import _resolve_config_file  # noqa: PLC0415

    return _resolve_config_file(SETTINGS_PATH)


def _apply_finance_dir_override(settings: dict) -> dict:
    """Apply UIS_FINANCE_DIR source path override when running in cloud mode."""
    finance_dir_override = os.environ.get("UIS_FINANCE_DIR")
    if not finance_dir_override or not isinstance(settings, dict):
        return settings

    source_registry = settings.get("source_registry", {})
    if isinstance(source_registry, dict):
        for reader_name, reader_cfg in source_registry.items():
            if isinstance(reader_cfg, dict):
                reader_cfg["data_dir"] = os.path.join(finance_dir_override, reader_name)

    settings["finance_dir"] = finance_dir_override
    return settings


def load_settings() -> dict:
    """Load config/settings.yaml. Raises HTTP 500 on failure."""
    import yaml  # noqa: PLC0415
    try:
        with open(_settings_read_path()) as f:
            settings = yaml.safe_load(f) or {}
        return _apply_finance_dir_override(settings)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Could not load settings.yaml: {e}")


def save_settings(settings: dict) -> None:
    """Write settings back to YAML, preserving comments if ruamel.yaml is available.

    Replaces only the 'llm' key in the file; all other sections are untouched.
    Uses SETTINGS_LOCK_PATH for exclusive locking (shared with save_prompts) and
    an atomic temp-file + os.replace write to prevent concurrent corruption.
    """
    try:
        from ruamel.yaml import YAML  # noqa: PLC0415
        yaml_rt = YAML()
        yaml_rt.preserve_quotes = True

        lock_fd = open(SETTINGS_LOCK_PATH, "w")  # noqa: WPS515
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_EX)
            with open(_settings_read_path()) as f:
                data = yaml_rt.load(f)
            data["llm"] = settings["llm"]
            tmp_fd, tmp_path = tempfile.mkstemp(dir=SETTINGS_PATH.parent, suffix=".tmp")
            try:
                with os.fdopen(tmp_fd, "w") as tmp_f:
                    yaml_rt.dump(data, tmp_f)
                    tmp_f.flush()
                    os.fsync(tmp_f.fileno())
                os.replace(tmp_path, SETTINGS_PATH)
            except Exception:
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass
                raise
        finally:
            fcntl.flock(lock_fd, fcntl.LOCK_UN)
            lock_fd.close()
    except ImportError:
        logger.warning(
            "ruamel.yaml not available; rewriting settings.yaml with pyyaml (comments will be lost)"
        )
        import yaml  # noqa: PLC0415
        with open(SETTINGS_PATH, "w") as f:
            yaml.dump(settings, f, allow_unicode=True, default_flow_style=False)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Could not write settings.yaml: {e}")


def load_source_registry() -> dict:
    """Load the source_registry section from settings.yaml. Returns {} if not present."""
    settings = load_settings()
    return settings.get("source_registry", {})


def save_source_registry(updates: dict) -> dict:
    """
    Shallow-merge updates into source_registry in settings.yaml.

    For each key in updates:
    - If the source key exists in source_registry, merge only the provided fields
      (shallow merge — don't overwrite the whole source entry).
    - If the source key does NOT exist in source_registry, skip it (no new sources created).

    Uses the same atomic write + fcntl lock pattern as save_prompts().

    Returns the updated source_registry dict.
    """
    try:
        from ruamel.yaml import YAML  # noqa: PLC0415
        yaml_rt = YAML()
        yaml_rt.preserve_quotes = True

        lock_fd = open(SETTINGS_LOCK_PATH, "w")  # noqa: WPS515
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_EX)

            with open(_settings_read_path()) as f:
                data = yaml_rt.load(f)

            registry = data.get("source_registry") or {}
            for source_key, field_updates in updates.items():
                if source_key not in registry:
                    continue  # never create new sources
                if not isinstance(field_updates, dict):
                    logger.warning("save_source_registry: skipping non-dict update for %r", source_key)
                    continue
                for field, value in field_updates.items():
                    registry[source_key][field] = value
            data["source_registry"] = registry

            tmp_fd, tmp_path = tempfile.mkstemp(dir=SETTINGS_PATH.parent, suffix=".tmp")
            try:
                with os.fdopen(tmp_fd, "w") as tmp_f:
                    yaml_rt.dump(data, tmp_f)
                    tmp_f.flush()
                    os.fsync(tmp_f.fileno())
                os.replace(tmp_path, SETTINGS_PATH)
            except Exception:
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass
                raise

            return dict(registry)
        finally:
            fcntl.flock(lock_fd, fcntl.LOCK_UN)
            lock_fd.close()

    except ImportError:
        logger.warning(
            "ruamel.yaml not available; rewriting settings.yaml with pyyaml (comments will be lost)"
        )
        import yaml  # noqa: PLC0415

        lock_fd = open(SETTINGS_LOCK_PATH, "w")  # noqa: WPS515
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_EX)

            with open(_settings_read_path()) as f:
                data = yaml.safe_load(f) or {}

            registry = data.get("source_registry") or {}
            for source_key, field_updates in updates.items():
                if source_key not in registry:
                    continue
                if not isinstance(field_updates, dict):
                    logger.warning("save_source_registry: skipping non-dict update for %r", source_key)
                    continue
                for field, value in field_updates.items():
                    registry[source_key][field] = value
            data["source_registry"] = registry

            tmp_fd, tmp_path = tempfile.mkstemp(dir=SETTINGS_PATH.parent, suffix=".tmp")
            try:
                with os.fdopen(tmp_fd, "w") as tmp_f:
                    yaml.dump(data, tmp_f, allow_unicode=True, default_flow_style=False)
                    tmp_f.flush()
                    os.fsync(tmp_f.fileno())
                os.replace(tmp_path, SETTINGS_PATH)
            except Exception:
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass
                raise

            return dict(registry)
        finally:
            fcntl.flock(lock_fd, fcntl.LOCK_UN)
            lock_fd.close()

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Could not write source_registry to settings.yaml: {e}")


def seed_missing_readers() -> list[str]:
    """Seed any reader keys known to the source registry that are absent from
    settings.yaml ``source_registry``.  ADDITIVE ONLY — never modifies or removes
    existing reader entries (preserves user's enabled/data_dir/file_patterns choices).

    Intended to be called once at startup, AFTER ``download_settings_from_gcs``
    has restored the persisted settings, so newly-introduced readers (e.g. 'ibkr'
    added in Workstream C) propagate to GCS-persisted settings automatically.

    If any readers were added and ``UIS_GCS_BUCKET`` is set, the updated
    settings.yaml is uploaded to GCS (best-effort — failure logs a warning but
    never raises).

    Returns the list of reader keys that were actually seeded.
    Idempotent: a second call with the same settings adds nothing.
    """
    import yaml as _yaml  # noqa: PLC0415

    try:
        from src.sources.registry import get_registry  # noqa: PLC0415
        reg = get_registry()
    except Exception as exc:
        logger.warning("seed_missing_readers: could not load source registry: %s", exc)
        return []

    known_keys: list[str] = reg.key_known_list()
    all_prefixes: dict = reg.asset_prefixes()

    # Read the raw file without env-var overrides (load_settings applies
    # _apply_finance_dir_override which we must NOT persist back to disk).
    try:
        with open(_settings_read_path()) as _f:
            data: dict = _yaml.safe_load(_f) or {}
    except Exception as exc:
        logger.warning("seed_missing_readers: could not read settings.yaml: %s", exc)
        return []

    source_registry: dict = data.get("source_registry") or {}
    seeded: list[str] = []

    for key in known_keys:
        if key in source_registry:
            continue  # already present — never touch it

        # Derive file_patterns from the ReaderConfig (best-effort, UI-only field)
        file_patterns: dict = {}
        cfg = reg._config_for_key(key)
        if cfg and cfg.parsing:
            fmt = cfg.parsing.format
            holdings_sheet = next(
                (s for s in cfg.parsing.sheets if s.target == "holdings"), None
            )
            if fmt == "flex_csv":
                if holdings_sheet and holdings_sheet.file_glob:
                    file_patterns = {"flexquery": holdings_sheet.file_glob}
            elif fmt == "csv":
                if holdings_sheet and holdings_sheet.file_glob:
                    file_patterns = {"positions": holdings_sheet.file_glob}
            # excel: workbook name is not in ReaderConfig → leave as {} (best-effort)

        source_registry[key] = {
            "enabled": True,
            "reader": f"{key}_reader",
            "asset_prefixes": all_prefixes.get(key, []),
            "data_dir": None,
            "file_patterns": file_patterns,
        }
        seeded.append(key)

    if not seeded:
        return []

    data["source_registry"] = source_registry

    # Atomic write — same lock + temp-file pattern as save_source_registry
    try:
        lock_fd = open(SETTINGS_LOCK_PATH, "w")  # noqa: WPS515
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_EX)
            tmp_fd, tmp_path = tempfile.mkstemp(dir=SETTINGS_PATH.parent, suffix=".tmp")
            try:
                with os.fdopen(tmp_fd, "w") as tmp_f:
                    _yaml.dump(data, tmp_f, allow_unicode=True, default_flow_style=False)
                    tmp_f.flush()
                    os.fsync(tmp_f.fileno())
                os.replace(tmp_path, SETTINGS_PATH)
            except Exception:
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass
                raise
        finally:
            fcntl.flock(lock_fd, fcntl.LOCK_UN)
            lock_fd.close()
    except Exception as exc:
        logger.warning("seed_missing_readers: could not write settings.yaml: %s", exc)
        return []

    logger.info("seed_missing_readers: seeded reader(s) %r into settings.yaml", seeded)

    # Best-effort GCS upload so the seeded entries survive the next restart
    if os.getenv("UIS_GCS_BUCKET"):
        try:
            from src.storage.gcs import upload_settings_to_gcs  # noqa: PLC0415
            upload_settings_to_gcs(os.getenv("UIS_GCS_BUCKET"), str(SETTINGS_PATH))
            logger.info("seed_missing_readers: uploaded updated settings.yaml to GCS")
        except Exception as exc:
            logger.warning(
                "seed_missing_readers: could not upload settings.yaml to GCS: %s", exc
            )

    return seeded


def get_configured_language() -> str | None:
    """Deployment-default language from settings.yaml, or None if unset/invalid.

    This is step 3 of `resolve_language()`'s precedence — below the persisted
    `user_profile.language` and above the hardcoded 'en'. It exists so a public
    deployment can pin a language in config without a database write.

    Returns None (never a guess) when the key is absent or holds an unsupported
    value, so the resolver can label WHY it fell through.
    """
    try:
        settings = load_settings()
    except Exception as e:  # HTTPException included — never raise out of a resolver path
        logger.warning("get_configured_language: settings load failed: %s", e)
        return None

    raw = settings.get("language")
    if raw is None and isinstance(settings.get("profile"), dict):
        raw = settings["profile"].get("language")
    if raw is None:
        return None

    from src.services.ai_advisor.language_resolver import canonical_language  # noqa: PLC0415

    canonical = canonical_language(raw)
    if canonical is None:
        logger.warning(
            "settings.yaml `language: %r` is not a supported language — ignoring", raw
        )
    return canonical


def get_profile() -> dict:
    """Return profile from DuckDB, falling back to settings.yaml for migration.

    Returns a dict with keys: display_name, avatar_url, philosophy, language.
    philosophy is a dict (may be empty) parsed from the JSON column.
    language is 'en' | 'zh-CN' | None (None = never set; see V89).
    """
    import json as _json  # noqa: PLC0415

    try:
        from src.database.connector import DatabaseConnector  # noqa: PLC0415

        db = DatabaseConnector()
        try:
            row = db.execute(
                "SELECT display_name, avatar_base64, philosophy, language "
                "FROM user_profile WHERE id = 1"
            ).fetchone()
        finally:
            db.close()

        if row and (row[0] or row[1] or row[2] or row[3]):
            philosophy: dict = {}
            if row[2]:
                try:
                    philosophy = _json.loads(row[2])
                except Exception:
                    pass
            return {
                "display_name": row[0] or "",
                "avatar_url": row[1] or None,
                "philosophy": philosophy,
                "language": row[3] or None,
            }
    except Exception as e:
        logger.warning("get_profile DB read failed, falling back to settings.yaml: %s", e)

    settings = load_settings()
    profile = settings.get("profile", {})
    return {
        "display_name": profile.get("display_name", ""),
        "avatar_url": profile.get("avatar_url"),
        "philosophy": {},
        "language": get_configured_language(),
    }


def save_profile(
    display_name: str,
    avatar_base64: str | None,
    philosophy: dict | None = None,
    language: str | None = None,
) -> None:
    """Persist profile to DuckDB.

    philosophy is serialised to JSON and stored in the philosophy column.
    Pass None to leave the existing philosophy unchanged.

    language is the persisted AI-advisor / UI language. Pass None to leave it
    unchanged — the column is deliberately nullable and a NULL means "never
    chosen", which is a different state from "chose English" (see V89).
    """
    import json as _json  # noqa: PLC0415

    try:
        from src.database.connector import DatabaseConnector  # noqa: PLC0415

        db = DatabaseConnector()
        try:
            if language is not None:
                from src.services.ai_advisor.language_resolver import (  # noqa: PLC0415
                    canonical_language,
                )

                canonical = canonical_language(language)
                if canonical is None:
                    raise HTTPException(
                        status_code=422, detail=f"Unsupported language: {language!r}"
                    )
                db.execute(
                    """
                    INSERT INTO user_profile (id, language, updated_at)
                    VALUES (1, ?, CURRENT_TIMESTAMP)
                    ON CONFLICT(id) DO UPDATE SET
                        language = excluded.language,
                        updated_at = excluded.updated_at
                    """,
                    [canonical],
                )
            if philosophy is not None:
                philosophy_json: str | None = _json.dumps(philosophy, ensure_ascii=False)
                db.execute(
                    """
                    INSERT INTO user_profile (id, display_name, avatar_base64, philosophy, updated_at)
                    VALUES (1, ?, ?, ?, CURRENT_TIMESTAMP)
                    ON CONFLICT(id) DO UPDATE SET
                        display_name = excluded.display_name,
                        avatar_base64 = excluded.avatar_base64,
                        philosophy = excluded.philosophy,
                        updated_at = excluded.updated_at
                    """,
                    [display_name, avatar_base64, philosophy_json],
                )
            else:
                db.execute(
                    """
                    INSERT INTO user_profile (id, display_name, avatar_base64, updated_at)
                    VALUES (1, ?, ?, CURRENT_TIMESTAMP)
                    ON CONFLICT(id) DO UPDATE SET
                        display_name = excluded.display_name,
                        avatar_base64 = excluded.avatar_base64,
                        updated_at = excluded.updated_at
                    """,
                    [display_name, avatar_base64],
                )
        finally:
            db.close()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Could not persist profile: {e}")


def get_source_health(db_path: str) -> dict:
    """Query sync_audit_reports for per-reader health metrics.

    Returns dict with keys:
      - last_sync_at: str | None (ISO datetime of most recent sync report)
      - reader_counts: dict (raw reader_counts JSON from report — aggregate counters)
      - by_source_after: dict (raw by_source_after JSON — keys like "Schwab_CSV", values {"count": int, "value": float})
    Returns empty structure on any error (health endpoint must not crash if DB missing).
    """
    try:
        import duckdb  # noqa: PLC0415
        import json as _json  # noqa: PLC0415
        conn = duckdb.connect(db_path)
        try:
            row = conn.execute(
                """
                SELECT created_at, reader_counts, by_source_after
                FROM sync_audit_reports
                WHERE report_type = 'sync'
                ORDER BY created_at DESC
                LIMIT 1
                """
            ).fetchone()
            if not row:
                return {"last_sync_at": None, "reader_counts": {}, "by_source_after": {}, "db_available": True}
            created_at, reader_counts_raw, by_source_raw = row

            def _parse_json(raw):
                if isinstance(raw, str):
                    try:
                        result = _json.loads(raw)
                        return result if isinstance(result, dict) else {}
                    except Exception:
                        return {}
                return raw if isinstance(raw, dict) else {}

            reader_counts = _parse_json(reader_counts_raw)
            by_source = _parse_json(by_source_raw)
            last_sync_str = created_at.isoformat() if hasattr(created_at, "isoformat") else str(created_at)
            return {
                "last_sync_at": last_sync_str,
                "reader_counts": reader_counts,
                "by_source_after": by_source,
                "db_available": True,
            }
        finally:
            conn.close()
    except Exception as e:
        logger.warning("get_source_health failed: %s", e)
        return {"last_sync_at": None, "reader_counts": {}, "by_source_after": {}, "db_available": False}


def _get_default_prompt_block(key: str) -> dict:
    """Return default prompt block for a given key using hardcoded defaults."""
    # Deferred import to prevent circular dependency: settings_manager <-> prompts.py
    from src.services.ai_advisor.prompts import (  # noqa: PLC0415
        _DEFAULT_SHARED_PERSONA_EDITABLE,
        _DEFAULT_BRIEF_INSTRUCTIONS,
        _DEFAULT_REVIEW_INSTRUCTIONS,
        _DEFAULT_REVIEW_QUESTIONS,
    )
    defaults = {
        "shared_persona": _DEFAULT_SHARED_PERSONA_EDITABLE,
        "brief_instructions": _DEFAULT_BRIEF_INSTRUCTIONS,
        "review_instructions": _DEFAULT_REVIEW_INSTRUCTIONS,
        "review_questions": _DEFAULT_REVIEW_QUESTIONS,
    }
    return {
        "text": defaults[key],
        "version": 0,
        "updated_at": None,
    }


def save_prompts(updates: dict, reset_keys: list[str] | None = None) -> dict:
    """
    Save prompt block updates to settings.yaml.

    Args:
        updates: dict mapping block key -> new text (str). Only keys present are updated.
                 None value for a key is ignored.
        reset_keys: if provided, these keys are reset to hardcoded defaults before applying updates.

    Returns the full updated prompts dict (all 4 blocks with version/updated_at).

    Uses atomic write (temp file + os.replace) and fcntl.flock for file locking.
    Max 10,000 chars per block enforced (raises ValueError with block name).
    """
    VALID_KEYS = {"shared_persona", "brief_instructions", "review_instructions", "review_questions"}
    MAX_CHARS = 10_000

    # Validate lengths upfront
    for k, v in updates.items():
        if v is not None and len(v) > MAX_CHARS:
            raise ValueError(f"Block '{k}' exceeds maximum length of {MAX_CHARS} characters")

    try:
        from ruamel.yaml import YAML  # noqa: PLC0415
        yaml_rt = YAML()
        yaml_rt.preserve_quotes = True

        # Open (or create) the lock file and hold EX lock across the entire read-modify-write
        lock_fd = open(SETTINGS_LOCK_PATH, "w")  # noqa: WPS515
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_EX)  # blocks until exclusive lock acquired

            # READ
            with open(_settings_read_path()) as f:
                data = yaml_rt.load(f)

            now = datetime.now(timezone.utc).isoformat(timespec="seconds")
            prompts = data.get("prompts") or {}

            # Ensure all 4 keys exist with defaults
            for key in VALID_KEYS:
                if key not in prompts:
                    default = _get_default_prompt_block(key)
                    prompts[key] = {
                        "text": default["text"],
                        "version": default["version"],
                        "updated_at": default["updated_at"],
                    }

            # Apply resets first
            for key in (reset_keys or []):
                if key in VALID_KEYS:
                    default = _get_default_prompt_block(key)
                    current_version = int(prompts[key].get("version", 0) or 0)
                    prompts[key] = {
                        "text": default["text"],
                        "version": current_version + 1,
                        "updated_at": now,
                    }

            # Apply updates
            for key, text in updates.items():
                if key in VALID_KEYS and text is not None:
                    current_version = int(prompts[key].get("version", 0) or 0)
                    prompts[key] = {
                        "text": text,
                        "version": current_version + 1,
                        "updated_at": now,
                    }

            data["prompts"] = prompts

            # WRITE (atomic): temp file in same dir → os.replace
            tmp_fd, tmp_path = tempfile.mkstemp(
                dir=SETTINGS_PATH.parent, suffix=".tmp"
            )
            try:
                with os.fdopen(tmp_fd, "w") as tmp_f:
                    yaml_rt.dump(data, tmp_f)
                    tmp_f.flush()
                    os.fsync(tmp_f.fileno())
                os.replace(tmp_path, SETTINGS_PATH)
            except Exception:
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass
                raise

        finally:
            fcntl.flock(lock_fd, fcntl.LOCK_UN)
            lock_fd.close()

        return dict(prompts)

    except ImportError:
        logger.warning(
            "ruamel.yaml not available; rewriting settings.yaml with pyyaml (comments will be lost)"
        )
        import yaml  # noqa: PLC0415

        lock_fd = open(SETTINGS_LOCK_PATH, "w")  # noqa: WPS515
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_EX)

            with open(_settings_read_path()) as f:
                data = yaml.safe_load(f) or {}

            now = datetime.now(timezone.utc).isoformat(timespec="seconds")
            prompts = data.get("prompts") or {}

            for key in VALID_KEYS:
                if key not in prompts:
                    default = _get_default_prompt_block(key)
                    prompts[key] = {
                        "text": default["text"],
                        "version": default["version"],
                        "updated_at": default["updated_at"],
                    }

            for key in (reset_keys or []):
                if key in VALID_KEYS:
                    default = _get_default_prompt_block(key)
                    current_version = int(prompts[key].get("version", 0) or 0)
                    prompts[key] = {
                        "text": default["text"],
                        "version": current_version + 1,
                        "updated_at": now,
                    }

            for key, text in updates.items():
                if key in VALID_KEYS and text is not None:
                    current_version = int(prompts[key].get("version", 0) or 0)
                    prompts[key] = {
                        "text": text,
                        "version": current_version + 1,
                        "updated_at": now,
                    }

            data["prompts"] = prompts

            tmp_fd, tmp_path = tempfile.mkstemp(dir=SETTINGS_PATH.parent, suffix=".tmp")
            try:
                with os.fdopen(tmp_fd, "w") as tmp_f:
                    yaml.dump(data, tmp_f, allow_unicode=True, default_flow_style=False)
                    tmp_f.flush()
                    os.fsync(tmp_f.fileno())
                os.replace(tmp_path, SETTINGS_PATH)
            except Exception:
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass
                raise
        finally:
            fcntl.flock(lock_fd, fcntl.LOCK_UN)
            lock_fd.close()

        return dict(prompts)
    except ValueError:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Could not write prompts to settings.yaml: {e}")
