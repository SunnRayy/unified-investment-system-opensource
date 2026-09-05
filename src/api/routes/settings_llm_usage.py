"""LLM usage aggregation — extracted so tests can import without the full
settings router (which requires python-multipart for file upload routes)."""

from __future__ import annotations

import logging
import os

import duckdb as _duckdb
from pydantic import BaseModel
from typing import List, Optional

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------


class LLMUsageRow(BaseModel):
    model_used: str
    calls: int
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    cost_usd: float
    success_calls: int
    failure_calls: int
    last_used: Optional[str] = None


class LLMUsageResponse(BaseModel):
    models: List[LLMUsageRow]
    total_calls: int
    total_tokens: int
    total_cost_usd: float


# ---------------------------------------------------------------------------
# Core aggregation (importable by tests directly)
# ---------------------------------------------------------------------------


def _db_path() -> str:
    """DB path: UIS_DB_PATH env var → settings-relative fallback.

    Mirrors _history_db_path() in settings.py without importing that module.
    """
    env_override = os.environ.get("UIS_DB_PATH")
    if env_override:
        return env_override
    # Lazy import to avoid circular deps at module load time
    from src.services import settings_manager  # noqa: PLC0415
    return str(settings_manager.SETTINGS_PATH.parent.parent / "data" / "unified.duckdb")


def aggregate_llm_usage() -> LLMUsageResponse:
    """Aggregate llm_usage by model. Empty/missing table → valid empty response.

    On a real DB/query exception this propagates the exception to the caller,
    who is responsible for mapping it to an API error via api_error_response.
    """
    with _duckdb.connect(_db_path(), read_only=True) as conn:
        exists = conn.execute(
            "SELECT 1 FROM information_schema.tables WHERE table_name='llm_usage'"
        ).fetchone()
        if not exists:
            return LLMUsageResponse(models=[], total_calls=0, total_tokens=0, total_cost_usd=0.0)

        rows = conn.execute(
            """
            SELECT
                model_used,
                COUNT(*)                                                  AS calls,
                COALESCE(SUM(prompt_tokens), 0)                           AS prompt_tokens,
                COALESCE(SUM(completion_tokens), 0)                       AS completion_tokens,
                COALESCE(SUM(total_tokens), 0)                            AS total_tokens,
                COALESCE(SUM(cost_estimate_usd), 0.0)                     AS cost_usd,
                COALESCE(SUM(CASE WHEN success THEN 1 ELSE 0 END), 0)     AS success_calls,
                MAX(created_at)                                            AS last_used
            FROM llm_usage
            GROUP BY model_used
            ORDER BY COUNT(*) DESC
            """
        ).fetchall()

    model_rows = [
        LLMUsageRow(
            model_used=r[0],
            calls=int(r[1]),
            prompt_tokens=int(r[2]),
            completion_tokens=int(r[3]),
            total_tokens=int(r[4]),
            cost_usd=float(r[5]),
            success_calls=int(r[6]),
            failure_calls=int(r[1]) - int(r[6]),
            last_used=r[7].isoformat() if r[7] is not None else None,
        )
        for r in rows
    ]

    return LLMUsageResponse(
        models=model_rows,
        total_calls=sum(m.calls for m in model_rows),
        total_tokens=sum(m.total_tokens for m in model_rows),
        total_cost_usd=sum(m.cost_usd for m in model_rows),
    )
