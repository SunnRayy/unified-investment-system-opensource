"""Market sentiment API routes."""

import logging
import os
from datetime import datetime
from pathlib import Path

import yaml
from fastapi import APIRouter, Depends

from src.api.dependencies import get_db
from src.database.connector import DatabaseConnector
from src.financial_analysis.macro_analyzer import MacroAnalyzer
from src.services.metric_governance import require_methodology
from src.storage.gcs_flush import mark_dirty

# Governance metric_key (metric_catalog) that gates ingestion of any
# indicator whose indicator_key starts with "buffett_" — PRD F4.3: ingestion
# rejects series without a methodology tag for methodology-sensitive metrics.
_BUFFETT_GOVERNANCE_METRIC_KEY = "buffett_indicator"


def _open_writable(db: DatabaseConnector) -> DatabaseConnector:
    if getattr(db, "read_only", False):
        db_path = db.db_path
        db.close()
        return DatabaseConnector(db_path, read_only=False)
    return db

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/market", tags=["sentiment"])

def _load_fred_key() -> str:
    """Load FRED API key from environment, .env file, or settings.yaml."""
    # 1. Shell environment variable (highest priority)
    if key := os.environ.get("FRED_API_KEY", ""):
        return key
    # 2. .env file (gitignored local secrets file)
    try:
        with open(".env") as f:
            for line in f:
                line = line.strip()
                if line.startswith("FRED_API_KEY="):
                    return line.split("=", 1)[1].strip()
    except FileNotFoundError:
        pass
    # 3. settings.yaml literal value (if not a placeholder)
    try:
        # Resolved through src.config rather than raw-opened, so a fresh clone
        # reads the .example template. Semantics are unchanged: any failure
        # here is still swallowed below and the caller gets no key.
        from src.config import _resolve_config_file  # noqa: PLC0415

        with open(_resolve_config_file(Path("config/settings.yaml"))) as f:
            config = yaml.safe_load(f)
        key = config.get("external_data", {}).get("fred", {}).get("api_key", "")
        if key and key != "${FRED_API_KEY}":
            return key
    except Exception:
        pass
    return ""

def ensure_sentiment_table(db: DatabaseConnector):
    """Ensure the cache table and its columns exist.

    Pass D: table + 3 newer columns (is_stale, last_refresh_attempt, error_detail) are
    now created by bootstrap_database() via Migration 13/14 before the server serves any
    traffic. This function is kept for safety and is a no-op on bootstrapped databases.
    """
    db.execute("""
        CREATE TABLE IF NOT EXISTS market_sentiment_cache (
            indicator_key VARCHAR PRIMARY KEY,
            section VARCHAR,
            indicator_name VARCHAR,
            value DOUBLE,
            display_value VARCHAR,
            zone VARCHAR,
            zone_color VARCHAR,
            description VARCHAR,
            raw_json VARCHAR,
            updated_at TIMESTAMP,
            is_stale BOOLEAN DEFAULT FALSE,
            last_refresh_attempt TIMESTAMP,
            error_detail VARCHAR
        )
    """)

@router.get("/sentiment")
async def get_sentiment(db: DatabaseConnector = Depends(get_db)):
    """Return cached sentiment data from DuckDB."""
    # Do NOT call ensure_sentiment_table() here — it runs DDL (CREATE TABLE / ALTER TABLE)
    # which requires a writable connection but get_db() yields read_only=True.
    # Table setup happens in the POST /sentiment/refresh endpoint which opens a writable conn.
    try:
        rows = db.execute(
            "SELECT indicator_key, section, indicator_name, value, display_value, "
            "zone, zone_color, description, raw_json, updated_at, "
            "is_stale, last_refresh_attempt, error_detail, methodology, data_source "
            "FROM market_sentiment_cache ORDER BY section, indicator_key"
        ).fetchall()
    except Exception:
        return {"indicators": [], "last_updated": None}

    indicators = []
    last_updated = None

    for row in rows:
        # PRD F4.3: every dashboard metric carries source/methodology/as_of;
        # as_of mirrors updated_at (the cache row's last-written timestamp).
        indicator = {
            "indicator_key": row[0],
            "section": row[1],
            "indicator_name": row[2],
            "value": row[3],
            "display_value": row[4],
            "zone": row[5],
            "zone_color": row[6],
            "description": row[7],
            "raw_json": row[8],
            "updated_at": row[9],
            "is_stale": row[10] if row[10] is not None else False,
            "last_refresh_attempt": row[11],
            "error_detail": row[12],
            "methodology": row[13],
            "data_source": row[14],
            "as_of": row[9],
        }
        indicators.append(indicator)

        ts = row[9]
        if not last_updated or ts > last_updated:
            last_updated = ts

    return {
        "indicators": indicators,
        "last_updated": last_updated
    }

@router.get("/sentiment/diagnostics")
async def sentiment_diagnostics():
    """Return which external API keys are configured (names only, never values).

    Used to diagnose silent failures where indicators show 'Unavailable'
    because a key is missing from the environment.
    """
    keys_to_check = {
        "FRED_API_KEY": "FRED (VIX, US10Y, Buffett indicators)",
        "FMP_API_KEY": "FMP (US stock PE history backfill)",
        "GEMINI_API_KEY": "Gemini AI (advisor)",
        "DEEPSEEK_API_KEY": "DeepSeek AI (advisor)",
    }
    configured = []
    missing = []
    for env_var, description in keys_to_check.items():
        val = os.environ.get(env_var, "")
        if not val:
            # Also check .env file
            try:
                with open(".env") as f:
                    for line in f:
                        if line.strip().startswith(f"{env_var}="):
                            val = line.split("=", 1)[1].strip()
                            break
            except FileNotFoundError:
                pass
        if val:
            configured.append(f"{env_var} ({description})")
        else:
            missing.append(f"{env_var} ({description})")
    return {"keys_configured": configured, "keys_missing": missing}


@router.post("/sentiment/refresh")
async def refresh_sentiment(db: DatabaseConnector = Depends(get_db)):
    """Fetch fresh data from all external APIs and store in DuckDB.

    Last-good-value preservation
    ────────────────────────────
    When a fresh fetch fails (value=None / Unavailable), we keep the
    previously-cached good value intact and mark the row is_stale=TRUE.
    This prevents a transient FRED timeout from wiping the cached us10y
    that the Valuation page uses, and stops the whole-page "Unavailable"
    cascade.  Only successful fetches (value is not None) overwrite the
    prior cache row.
    """
    writable = _open_writable(db)
    try:
        ensure_sentiment_table(writable)

        # Load existing cache once (key → row dict) so we can do last-good-value lookup.
        existing: dict[str, dict] = {}
        rows = writable.execute(
            "SELECT indicator_key, value, display_value, zone, zone_color, "
            "description, raw_json, updated_at, methodology, data_source "
            "FROM market_sentiment_cache"
        ).fetchall()
        for row in rows:
            existing[row[0]] = {
                "value": row[1],
                "display_value": row[2],
                "zone": row[3],
                "zone_color": row[4],
                "description": row[5],
                "raw_json": row[6],
                "updated_at": row[7],
                "methodology": row[8],
                "data_source": row[9],
            }

        fred_key = _load_fred_key()
        analyzer = MacroAnalyzer(fred_api_key=fred_key)
        fresh_indicators = analyzer.fetch_all()
        now_str = datetime.utcnow().replace(microsecond=0).isoformat()

        merged_indicators = []
        skipped_ungoverned = 0
        for ind in fresh_indicators:
            key = ind["indicator_key"]
            fetch_succeeded = ind.get("value") is not None
            prior = existing.get(key)

            if fetch_succeeded:
                # F4.3: methodology-sensitive series (Buffett indicator variants)
                # must carry a methodology tag before being persisted — an
                # untagged write is skipped (never silently stored ambiguous),
                # PRD 2026-07-07 defect(c).
                if key.startswith("buffett"):
                    try:
                        require_methodology(
                            writable, _BUFFETT_GOVERNANCE_METRIC_KEY, ind.get("methodology")
                        )
                    except ValueError as exc:
                        logger.warning(
                            "Skipping ingestion for %s: %s", key, exc
                        )
                        skipped_ungoverned += 1
                        continue

                # Fresh data — write everything, clear stale flag.
                writable.execute("""
                    INSERT INTO market_sentiment_cache (
                        indicator_key, section, indicator_name, value, display_value,
                        zone, zone_color, description, raw_json, updated_at,
                        is_stale, last_refresh_attempt, error_detail,
                        methodology, data_source
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, FALSE, ?, NULL, ?, ?)
                    ON CONFLICT (indicator_key) DO UPDATE SET
                        section = excluded.section,
                        indicator_name = excluded.indicator_name,
                        value = excluded.value,
                        display_value = excluded.display_value,
                        zone = excluded.zone,
                        zone_color = excluded.zone_color,
                        description = excluded.description,
                        raw_json = excluded.raw_json,
                        updated_at = excluded.updated_at,
                        is_stale = FALSE,
                        last_refresh_attempt = excluded.last_refresh_attempt,
                        error_detail = NULL,
                        methodology = excluded.methodology,
                        data_source = excluded.data_source
                """, (
                    key, ind["section"], ind["indicator_name"], ind["value"],
                    ind["display_value"], ind["zone"], ind["zone_color"],
                    ind["description"], ind["raw_json"], ind["updated_at"], now_str,
                    ind.get("methodology"), ind.get("data_source"),
                ))
                merged_indicators.append({**ind, "is_stale": False})
            elif prior and prior.get("value") is not None:
                # Fetch failed but we have a prior good value — keep it, set stale.
                error_msg = ind.get("description", "fetch failed")
                writable.execute("""
                    INSERT INTO market_sentiment_cache (
                        indicator_key, section, indicator_name, value, display_value,
                        zone, zone_color, description, raw_json, updated_at,
                        is_stale, last_refresh_attempt, error_detail
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, TRUE, ?, ?)
                    ON CONFLICT (indicator_key) DO UPDATE SET
                        is_stale = TRUE,
                        last_refresh_attempt = excluded.last_refresh_attempt,
                        error_detail = excluded.error_detail
                """, (
                    key, ind["section"], ind["indicator_name"],
                    prior["value"], prior["display_value"], prior["zone"],
                    prior["zone_color"], prior["description"], prior["raw_json"],
                    prior["updated_at"], now_str, error_msg,
                ))
                merged_indicators.append({
                    **ind,
                    "value": prior["value"],
                    "display_value": prior["display_value"],
                    "zone": prior["zone"],
                    "zone_color": prior["zone_color"],
                    "description": prior["description"],
                    "is_stale": True,
                    "error_detail": error_msg,
                })
            else:
                # No prior good value either — write the Unavailable placeholder.
                error_msg = ind.get("description", "fetch failed")
                writable.execute("""
                    INSERT INTO market_sentiment_cache (
                        indicator_key, section, indicator_name, value, display_value,
                        zone, zone_color, description, raw_json, updated_at,
                        is_stale, last_refresh_attempt, error_detail
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, TRUE, ?, ?)
                    ON CONFLICT (indicator_key) DO UPDATE SET
                        section = excluded.section,
                        indicator_name = excluded.indicator_name,
                        value = excluded.value,
                        display_value = excluded.display_value,
                        zone = excluded.zone,
                        zone_color = excluded.zone_color,
                        description = excluded.description,
                        raw_json = excluded.raw_json,
                        updated_at = excluded.updated_at,
                        is_stale = TRUE,
                        last_refresh_attempt = excluded.last_refresh_attempt,
                        error_detail = excluded.error_detail
                """, (
                    key, ind["section"], ind["indicator_name"], ind["value"],
                    ind["display_value"], ind["zone"], ind["zone_color"],
                    ind["description"], ind["raw_json"], ind["updated_at"],
                    now_str, error_msg,
                ))
                merged_indicators.append({**ind, "is_stale": True, "error_detail": error_msg})

        mark_dirty()
        return {
            "indicators": merged_indicators,
            "last_updated": now_str,
            "skipped_ungoverned": skipped_ungoverned,
        }
    finally:
        if writable is not db:
            writable.close()
