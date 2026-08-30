"""AI Advisor settings, context preview, and brief generation endpoints."""

from __future__ import annotations

import asyncio
import dataclasses
import json
import logging
import math
import os
from datetime import date, datetime
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional

import duckdb
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from src.database.connector import resolve_db_path as _resolve_db_path
from src.services import settings_manager
from src.services.ai_advisor.section_ids import adapt_stored_content_json
from src.services.ai_advisor.review_generator import generate_cross_check_audit, propose_memo_updates
from src.services.decision_scorer import (
    VERDICT_BULLET_DODGED,
    VERDICT_GOOD_CALL,
    VERDICT_MISSED_OPPORTUNITY,
    VERDICT_NEUTRAL,
    VERDICT_REGRET,
    compute_outcome_pct_from_prices,
    compute_outcome_to_date,
    derive_verdict_suggestion,
    score_single_trade,
)
from src.services.llm_client import LLMAllModelsFailedError
from src.services.process_scorer import set_process_checks
from src.services.verification_config import load_verification_config
from src.storage.gcs_flush import mark_dirty
from src.api.routes._errors import api_error_response

_VALID_VERDICTS = frozenset(
    [VERDICT_GOOD_CALL, VERDICT_REGRET, VERDICT_BULLET_DODGED, VERDICT_MISSED_OPPORTUNITY, VERDICT_NEUTRAL]
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/ai-advisor", tags=["ai-advisor"])

# Semaphore: allow at most 2 concurrent analysis runs
# Lazily initialized inside the endpoint to avoid binding to a stale event loop
# on Python 3.9 when the module is imported before uvicorn starts its loop.
_analyze_semaphore: Optional[asyncio.Semaphore] = None

# Aliases so the rest of this module is unchanged
_load_settings = settings_manager.load_settings
_save_settings = settings_manager.save_settings


def _resolved_request_language(request_language: Optional[str]) -> str:
    """Resolve the output language for a generation request.

    Resolved HERE rather than deeper down so the router owns the only place a
    request locale enters the pipeline, and so the resolved value is what the
    generator persists into `context_config_json`.
    """
    from src.services.ai_advisor.language_resolver import resolve_language  # noqa: PLC0415

    resolution = resolve_language(request_language=request_language)
    logger.info(
        "AI advisor output language: %s (source=%s%s)",
        resolution["language"],
        resolution["source"],
        f", reason={resolution['fallback_reason']}" if resolution["fallback_reason"] else "",
    )
    return resolution["language"]


def _resolved_db_language(conn) -> str:
    """Resolve the language using an already-open connection."""
    from src.services.ai_advisor.language_resolver import resolve_language  # noqa: PLC0415

    return resolve_language(conn)["language"]


def _raise_ai_advisor_error(exc: Exception) -> None:
    if isinstance(exc, LLMAllModelsFailedError):
        raise HTTPException(status_code=503, detail=str(exc))
    raise HTTPException(status_code=500, detail=str(exc))


# ------------------------------------------------------------------
# Request / Response models
# ------------------------------------------------------------------

class LLMConfig(BaseModel):
    primary_model: str
    fallback_models: List[str]
    temperature: float
    max_output_tokens: int


class CreateTradeRequest(BaseModel):
    log_date: str           # YYYY-MM-DD
    asset_id: str
    asset_name: Optional[str] = None
    action: str             # "Buy" or "Sell"
    price: Optional[float] = None
    quantity: Optional[float] = None
    amount: Optional[float] = None
    currency: Optional[str] = None
    decision_reason: Optional[str] = None
    memo_id: Optional[int] = None


class BriefGenerateRequest(BaseModel):
    context_config: Dict[str, Any]
    reviewed_context_text: Optional[str] = None
    #: Narrative output language for THIS generation (the frontend's current
    #: locale). None means resolve it — see language_resolver's precedence.
    #: Scheduled generation sends nothing, which is why a persisted value exists.
    language: Optional[str] = None


class AnalyzeRequest(BaseModel):
    asset_code: str
    analysis_type: Literal['full'] = 'full'  # validated, not silent


class AnalysisHistoryItem(BaseModel):
    id: int
    asset_code: str
    asset_name: Optional[str] = None
    timing_signal: Optional[str] = None
    confidence: Optional[float] = None
    created_at: str
    model_used: Optional[str] = None
    data_source: Optional[str] = None


class AnalyzableAssetSearchResult(BaseModel):
    code: str
    name: Optional[str] = None
    in_portfolio: bool
    position_pct: Optional[float] = None


class ContextRenderRequest(BaseModel):
    report_type: str
    context_config: Dict[str, Any]
    period_start: Optional[str] = None
    period_end: Optional[str] = None
    questions_answers: List[Dict[str, Any]] = Field(default_factory=list)


# V5.8.0 Decision Feedback Loop — request models

class VerifyTradeRequest(BaseModel):
    verification_result: str
    verification_date: Optional[str] = None   # YYYY-MM-DD; default today
    verdict: Optional[str] = None              # explicit override; must be in VALID_VERDICTS
    expected_updated_at: Optional[str] = None  # ISO 8601 microseconds for optimistic concurrency
    # F1.2 process checks (PRD 2026-07-07) — optional, additive, accepted regardless
    # of the process_verification flag so data entry can start before the flag flips.
    authorized: Optional[bool] = None
    params_ok: Optional[bool] = None
    data_verified: Optional[bool] = None
    notes: Optional[str] = None


class ReopenVerificationRequest(BaseModel):
    expected_updated_at: Optional[str] = None  # ISO 8601 microseconds for optimistic concurrency


class ProcessChecksRequest(BaseModel):
    """F1.2 process-check toggle payload — independent of verification_status/verdict."""
    authorized: Optional[bool] = None
    params_ok: Optional[bool] = None
    data_verified: Optional[bool] = None
    notes: Optional[str] = None


def _normalize_render_estimate_config(context_config: Dict[str, Any]) -> Dict[str, Any]:
    tiers = context_config.get("tiers", {}) or {}
    estimate_config: Dict[str, Any] = {}
    for tier_name in ("identity", "portfolio", "market", "strategy", "transactions"):
        tier_cfg = dict(tiers.get(tier_name, {}))
        estimate_config[tier_name] = {
            "enabled": bool(tier_cfg.get("enabled", False)),
            "detail": tier_cfg.get("detail", "summary"),
        }
        if tier_name == "transactions":
            estimate_config[tier_name]["timeframe"] = tier_cfg.get("timeframe", "14d")
    if "timeframe" in context_config:
        estimate_config["timeframe"] = context_config["timeframe"]
    return estimate_config


# ------------------------------------------------------------------
# Endpoints
# ------------------------------------------------------------------

@router.get("/settings/llm", response_model=LLMConfig)
async def get_llm_settings():
    """Return current LLM configuration from settings.yaml."""
    settings = _load_settings()
    llm = settings.get("llm", {})
    if not llm:
        raise HTTPException(status_code=404, detail="llm section not found in settings.yaml")
    return LLMConfig(
        primary_model=llm.get("primary_model", ""),
        fallback_models=llm.get("fallback_models", []),
        temperature=float(llm.get("temperature", 0.7)),
        max_output_tokens=int(llm.get("max_output_tokens", 4096)),
    )


@router.put("/settings/llm", response_model=LLMConfig)
async def update_llm_settings(body: LLMConfig):
    """Update LLM model selection in settings.yaml."""
    settings = _load_settings()
    settings["llm"] = {
        "primary_model": body.primary_model,
        "fallback_models": body.fallback_models,
        "temperature": body.temperature,
        "max_output_tokens": body.max_output_tokens,
    }
    _save_settings(settings)
    mark_dirty()
    return body


@router.get("/context/preview")
async def get_context_preview(
    tiers: Optional[str] = Query(default=None),
    detail_identity: Optional[str] = Query(default="summary"),
    detail_portfolio: Optional[str] = Query(default="summary"),
    detail_market: Optional[str] = Query(default="summary"),
    detail_strategy: Optional[str] = Query(default="summary"),
    detail_transactions: Optional[str] = Query(default=None),
    timeframe: Optional[str] = Query(default="14d"),
):
    """Return token estimates for each context tier given the requested detail levels."""
    try:
        from src.services.ai_advisor.context_builder import ContextBuilder
        cb = ContextBuilder()
        enabled_set = set(tiers.split(',')) if tiers else None
        transactions_detail = detail_transactions if detail_transactions is not None else "summary"
        config = {
            "identity":     {"enabled": enabled_set is None or "identity" in enabled_set, "detail": detail_identity},
            "portfolio":    {"enabled": enabled_set is None or "portfolio" in enabled_set, "detail": detail_portfolio},
            "market":       {"enabled": enabled_set is None or "market" in enabled_set, "detail": detail_market},
            "strategy":     {"enabled": enabled_set is None or "strategy" in enabled_set, "detail": detail_strategy},
            "transactions": {"enabled": enabled_set is None or "transactions" in enabled_set, "detail": transactions_detail, "timeframe": timeframe},
        }
        return cb.estimate_tokens(config)
    except Exception as e:
        logger.exception("context/preview failed")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/context/render")
async def render_context_preview(request: ContextRenderRequest):
    """Render the exact context text that would be sent to the LLM."""
    try:
        from src.services.ai_advisor import context_builder as context_builder_module
        from src.services.ai_advisor.review_generator import build_review_prompt_text

        cb = context_builder_module.ContextBuilder()
        context_text = context_builder_module.render_context(cb, request.context_config)
        if request.report_type == "review":
            trades_text = None
            if request.period_start and request.period_end:
                trades_text = cb.build_review_trade_summary(
                    request.period_start,
                    request.period_end,
                )
            context_text = build_review_prompt_text(
                context_section=context_text,
                period_start=request.period_start or "",
                period_end=request.period_end or "",
                questions_answers=request.questions_answers,
                trades_text=trades_text,
            )
        return {
            "report_type": request.report_type,
            "context_text": context_text,
            "token_estimate": cb.estimate_tokens(
                _normalize_render_estimate_config(request.context_config)
            ),
            "warnings": [],
        }
    except Exception as e:
        logger.exception("context/render failed")
        raise HTTPException(status_code=500, detail=str(e))


# ------------------------------------------------------------------
# DB helper
# ------------------------------------------------------------------

_DB_PATH = Path(_resolve_db_path("data/unified.duckdb"))


def _get_db_path() -> str:
    runtime_path = os.getenv("UIS_DB_PATH") or str(_DB_PATH)
    return _resolve_db_path(runtime_path)


def _table_has_column(conn: duckdb.DuckDBPyConnection, table_name: str, column_name: str) -> bool:
    try:
        columns = conn.execute(f"PRAGMA table_info('{table_name}')").fetchall()
    except Exception:
        return False
    return any(str(column[1]) == column_name for column in columns)


# ------------------------------------------------------------------
# Analyze endpoints (static routes first, dynamic last)
# ------------------------------------------------------------------

@router.post("/analyze")
async def run_analysis(body: AnalyzeRequest):
    """Run asset analysis pipeline: fetch market data -> technical analysis -> LLM -> persist."""
    from starlette.concurrency import run_in_threadpool
    from src.analysis.pipeline import AssetAnalysisPipeline
    from src.market_data.fetchers.base import (
        UnsupportedCodeError, NoDataError, InsufficientDataError, DataFetchError
    )

    global _analyze_semaphore
    if _analyze_semaphore is None:
        _analyze_semaphore = asyncio.Semaphore(2)
    async with _analyze_semaphore:
        try:
            # NOTE: wait_for(timeout=30s) enforces a client-side deadline only.
            # The worker thread may continue running after 504 is returned and may
            # persist the result to asset_analyses. This is an accepted V1 trade-off
            # (personal tool, 1 user). The history endpoint will show the result if
            # the background work completes. Do NOT interpret 504 as "analysis cancelled".
            result = await asyncio.wait_for(
                run_in_threadpool(
                    AssetAnalysisPipeline().analyze,
                    body.asset_code,
                    "user",
                    _get_db_path(),
                ),
                timeout=30.0,
            )
        except (UnsupportedCodeError, NoDataError, InsufficientDataError) as e:
            raise HTTPException(status_code=422, detail=str(e))
        except DataFetchError as e:
            raise HTTPException(status_code=503, detail=f"Market data provider unavailable: {e}")
        except asyncio.TimeoutError:
            raise HTTPException(status_code=504, detail="Analysis timed out after 30s")
        except Exception as e:
            logger.exception("analyze failed for %s", body.asset_code)
            raise HTTPException(status_code=500, detail=str(e))

    if result.id is None:
        raise HTTPException(status_code=500, detail="Analysis completed but failed to persist")

    return dataclasses.asdict(result)


@router.get("/analyze/search", response_model=List[AnalyzableAssetSearchResult])
async def search_analyzable_assets(q: str = Query(..., min_length=2, max_length=100)):
    """Search asset registry for analyzable assets."""
    # Escape LIKE wildcards
    q_escaped = q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    pattern = f"%{q_escaped}%"

    try:
        conn = duckdb.connect(_get_db_path(), read_only=True)
        try:
            # Latest-per-asset holdings CTE
            rows = conn.execute("""
                WITH latest_holdings AS (
                    SELECT h.asset_id, h.market_value
                    FROM holdings h
                    INNER JOIN (
                        SELECT asset_id, MAX(snapshot_date) AS max_date
                        FROM holdings
                        WHERE is_shadow = FALSE
                        GROUP BY asset_id
                    ) lh ON h.asset_id = lh.asset_id AND h.snapshot_date = lh.max_date
                    WHERE h.is_shadow = FALSE
                ),
                portfolio_total AS (
                    SELECT NULLIF(SUM(market_value), 0) AS total_mv FROM latest_holdings
                )
                SELECT
                    r.canonical_id AS code,
                    r.display_name AS name,
                    (lh.asset_id IS NOT NULL) AS in_portfolio,
                    CASE WHEN pt.total_mv IS NOT NULL THEN lh.market_value / pt.total_mv ELSE NULL END AS position_pct
                FROM asset_registry r
                CROSS JOIN portfolio_total pt
                LEFT JOIN latest_holdings lh ON r.canonical_id = lh.asset_id
                WHERE r.canonical_id ILIKE ? ESCAPE '\\' OR r.display_name ILIKE ? ESCAPE '\\'
                ORDER BY in_portfolio DESC, r.canonical_id
                LIMIT 20
            """, [pattern, pattern]).fetchall()
        finally:
            conn.close()

        results = [
            AnalyzableAssetSearchResult(
                code=row[0], name=row[1],
                in_portfolio=bool(row[2]),
                position_pct=float(row[3]) if row[3] is not None else None
            )
            for row in rows
        ]

        # Fallback: if no registry match, synthesize result for arbitrary ticker
        if not results:
            results = [AnalyzableAssetSearchResult(code=q.upper(), name=None, in_portfolio=False, position_pct=None)]

        return results
    except Exception as e:
        logger.exception("analyze/search failed")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/analyze/should-trigger")
async def analyze_should_trigger(asset_code: str = Query(...)):
    """Check whether a fresh analysis should be triggered for the given asset."""
    from src.analysis.trigger import should_trigger_analysis
    try:
        from starlette.concurrency import run_in_threadpool
        triggered, reason = await run_in_threadpool(
            should_trigger_analysis, asset_code, _get_db_path()
        )
    except Exception as e:
        logger.exception("should_trigger_analysis failed for %s", asset_code)
        raise HTTPException(status_code=500, detail=str(e))

    last_analyzed_at = None
    try:
        import duckdb as _duckdb
        conn = _duckdb.connect(_get_db_path(), read_only=True)
        try:
            row = conn.execute(
                "SELECT created_at FROM asset_analyses "
                "WHERE UPPER(TRIM(asset_code))=UPPER(TRIM(?)) "
                "ORDER BY created_at DESC LIMIT 1",
                [asset_code],
            ).fetchone()
            if row:
                last_analyzed_at = str(row[0])
        finally:
            conn.close()
    except Exception:
        pass

    return {"should_trigger": triggered, "reason": reason, "last_analyzed_at": last_analyzed_at}


@router.get("/analyze/history", response_model=List[AnalysisHistoryItem])
async def get_analysis_history(
    asset_code: Optional[str] = Query(default=None),
    limit: int = Query(10, ge=1, le=100),
):
    """Return analysis history, optionally filtered by asset_code."""
    try:
        conn = duckdb.connect(_get_db_path(), read_only=True)
        try:
            if asset_code:
                rows = conn.execute("""
                    SELECT id, asset_code, asset_name,
                           COALESCE(JSON_EXTRACT_STRING(llm_analysis, '$.operation_signal'), JSON_EXTRACT_STRING(llm_analysis, '$.timing_signal')) AS timing_signal,
                           TRY_CAST(JSON_EXTRACT_STRING(llm_analysis, '$.confidence') AS DOUBLE) AS confidence,
                           CAST(created_at AS VARCHAR) AS created_at,
                           model_used, data_source
                    FROM asset_analyses
                    WHERE asset_code = ?
                    ORDER BY created_at DESC
                    LIMIT ?
                """, [asset_code, limit]).fetchall()
            else:
                rows = conn.execute("""
                    SELECT id, asset_code, asset_name,
                           COALESCE(JSON_EXTRACT_STRING(llm_analysis, '$.operation_signal'), JSON_EXTRACT_STRING(llm_analysis, '$.timing_signal')) AS timing_signal,
                           TRY_CAST(JSON_EXTRACT_STRING(llm_analysis, '$.confidence') AS DOUBLE) AS confidence,
                           CAST(created_at AS VARCHAR) AS created_at,
                           model_used, data_source
                    FROM asset_analyses
                    ORDER BY created_at DESC
                    LIMIT ?
                """, [limit]).fetchall()
        finally:
            conn.close()

        return [
            AnalysisHistoryItem(
                id=row[0], asset_code=row[1], asset_name=row[2],
                timing_signal=row[3], confidence=row[4],
                created_at=row[5], model_used=row[6], data_source=row[7]
            )
            for row in rows
        ]
    except Exception as e:
        logger.exception("analyze/history failed")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/analyze/{analysis_id}")
async def get_analysis_by_id(analysis_id: int):
    """Return a specific analysis by ID."""
    try:
        conn = duckdb.connect(_get_db_path(), read_only=True)
        try:
            rows = conn.execute("""
                SELECT id, asset_code, asset_name, technical_signals, llm_analysis,
                       llm_analysis_markdown, portfolio_context, model_used,
                       data_source, triggered_by, CAST(created_at AS VARCHAR)
                FROM asset_analyses WHERE id = ?
            """, [analysis_id]).fetchall()
        finally:
            conn.close()

        if not rows:
            raise HTTPException(status_code=404, detail=f"Analysis {analysis_id} not found")

        row = rows[0]
        return {
            "id": row[0], "asset_code": row[1], "asset_name": row[2],
            "technical_signals": json.loads(row[3]) if row[3] else None,
            "llm_analysis": json.loads(row[4]) if row[4] else None,
            "llm_analysis_markdown": row[5],
            "portfolio_context": json.loads(row[6]) if row[6] else None,
            "model_used": row[7], "data_source": row[8],
            "triggered_by": row[9], "created_at": row[10],
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("analyze/%d failed", analysis_id)
        raise HTTPException(status_code=500, detail=str(e))


# ------------------------------------------------------------------
# Brief endpoints
# ------------------------------------------------------------------

@router.post("/brief/generate")
def generate_brief(request: BriefGenerateRequest):
    """Generate a new daily brief using the given context configuration."""
    try:
        from src.services.ai_advisor.brief_generator import BriefGenerator
        result = BriefGenerator().generate(
            context_config=request.context_config,
            db_path=_get_db_path(),
            reviewed_context_text=request.reviewed_context_text,
            language=_resolved_request_language(request.language),
        )
        mark_dirty()
        return dataclasses.asdict(result)
    except Exception as e:
        logger.exception("brief/generate failed")
        _raise_ai_advisor_error(e)


@router.get("/brief/latest")
async def get_latest_brief():
    """Return the most recent brief, or null if none exists."""
    try:
        conn = duckdb.connect(_get_db_path(), read_only=True)
        try:
            row = conn.execute(
                """
                SELECT id, report_type, title, model_used, content_json, content_markdown,
                       context_config_json, created_at, prompt_text, raw_response_text
                FROM ai_reports
                WHERE report_type = 'brief'
                ORDER BY created_at DESC
                LIMIT 1
                """
            ).fetchone()
        finally:
            conn.close()

        if row is None:
            return None

        import json as _json
        id_, report_type, title, model_used, content_json_str, content_markdown, context_cfg_str, created_at, prompt_text, raw_response_text = row
        return {
            "id": id_,
            "report_type": report_type,
            "title": title,
            "model_used": model_used,
            # Read-time adapter — rows written before Program BIL hold Chinese
            # section keys and Chinese enum values. Nothing is rewritten; they
            # are mapped to stable IDs on the way out. See section_ids.py.
            "content_json": adapt_stored_content_json(_json.loads(content_json_str))
            if content_json_str
            else None,
            "content_markdown": content_markdown,
            "context_config": _json.loads(context_cfg_str) if context_cfg_str else None,
            "created_at": str(created_at),
            "prompt_text": prompt_text,
            "raw_response_text": raw_response_text,
        }
    except Exception as e:
        logger.exception("brief/latest failed")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/brief/history")
async def get_brief_history(limit: int = 20):
    """Return brief history metadata (no content_json)."""
    try:
        conn = duckdb.connect(_get_db_path(), read_only=True)
        try:
            rows = conn.execute(
                """
                SELECT id, title, model_used, created_at
                FROM ai_reports
                WHERE report_type = 'brief'
                ORDER BY created_at DESC
                LIMIT ?
                """,
                [limit],
            ).fetchall()
        finally:
            conn.close()

        return [
            {"id": r[0], "title": r[1], "model_used": r[2], "created_at": str(r[3])}
            for r in rows
        ]
    except Exception as e:
        logger.exception("brief/history failed")
        raise HTTPException(status_code=500, detail=str(e))


# ------------------------------------------------------------------
# Review endpoints
# ------------------------------------------------------------------

class ReviewQuestionsRequest(BaseModel):
    period_start: str  # YYYY-MM-DD
    period_end: str    # YYYY-MM-DD
    language: Optional[str] = None


class ReviewGenerateRequest(BaseModel):
    questions_answers: List[Dict[str, Any]]
    period_start: str
    period_end: str
    context_config: Dict[str, Any]
    reviewed_context_text: Optional[str] = None
    language: Optional[str] = None


class UpdateReviewRequest(BaseModel):
    title: Optional[str] = None
    content_json: Optional[Dict[str, Any]] = None


@router.post("/review/questions")
def generate_review_questions(request: ReviewQuestionsRequest):
    """Generate guided review questions grounded in actual trades for the period."""
    try:
        from src.services.ai_advisor.review_generator import ReviewGenerator
        questions = ReviewGenerator().generate_questions(
            request.period_start,
            request.period_end,
            db_path=_get_db_path(),
            language=_resolved_request_language(request.language),
        )
        return {
            "questions": [
                {"id": q.id, "question": q.question, "context": q.context}
                for q in questions
            ]
        }
    except Exception as e:
        logger.exception("review/questions failed")
        _raise_ai_advisor_error(e)


@router.post("/review/generate")
def generate_review(request: ReviewGenerateRequest):
    """Generate a structured review report from Q&A responses."""
    try:
        import dataclasses as _dc
        from src.services.ai_advisor.review_generator import ReviewGenerator
        result = ReviewGenerator().generate_review(
            request.questions_answers,
            request.period_start,
            request.period_end,
            request.context_config,
            db_path=_get_db_path(),
            reviewed_context_text=request.reviewed_context_text,
            language=_resolved_request_language(request.language),
        )
        mark_dirty()
        return _dc.asdict(result)
    except Exception as e:
        logger.exception("review/generate failed")
        _raise_ai_advisor_error(e)


@router.get("/review/latest")
async def get_latest_review():
    """Return the most recent review, or null if none exists."""
    import json as _json
    try:
        conn = duckdb.connect(_get_db_path(), read_only=True)
        try:
            row = conn.execute(
                """
                SELECT id, title, model_used, created_at, content_json,
                       period_start, period_end, prompt_text, raw_response_text
                FROM ai_reports
                WHERE report_type = 'review'
                ORDER BY created_at DESC
                LIMIT 1
                """
            ).fetchone()
        finally:
            conn.close()

        if row is None:
            return None

        return {
            "id": row[0],
            "title": row[1],
            "model_used": row[2],
            "created_at": str(row[3]),
            # Read-time adapter — see section_ids.py.
            "content_json": adapt_stored_content_json(_json.loads(row[4])) if row[4] else {},
            "period_start": str(row[5]) if row[5] else None,
            "period_end": str(row[6]) if row[6] else None,
            "prompt_text": row[7],
            "raw_response_text": row[8],
        }
    except Exception as e:
        logger.exception("review/latest failed")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/review/history")
async def get_review_history(limit: int = 20):
    """Return review history metadata (no content_json)."""
    try:
        conn = duckdb.connect(_get_db_path(), read_only=True)
        try:
            rows = conn.execute(
                """
                SELECT id, title, model_used, created_at, period_start, period_end
                FROM ai_reports
                WHERE report_type = 'review'
                ORDER BY created_at DESC
                LIMIT ?
                """,
                [limit],
            ).fetchall()
        finally:
            conn.close()

        return [
            {
                "id": r[0],
                "title": r[1],
                "model_used": r[2],
                "created_at": str(r[3]),
                "period_start": str(r[4]) if r[4] else None,
                "period_end": str(r[5]) if r[5] else None,
            }
            for r in rows
        ]
    except Exception as e:
        logger.exception("review/history failed")
        raise HTTPException(status_code=500, detail=str(e))


@router.put("/review/{review_id}")
async def update_review(review_id: int, request: UpdateReviewRequest):
    """Update a saved review title and/or structured content."""
    from src.services.ai_advisor.review_generator import _build_content_markdown

    try:
        conn = duckdb.connect(_get_db_path())
        try:
            row = conn.execute(
                """
                SELECT id, title, model_used, created_at, content_json, content_markdown,
                       period_start, period_end, prompt_text, raw_response_text
                FROM ai_reports
                WHERE id = ? AND report_type = 'review'
                """,
                [review_id],
            ).fetchone()
            if row is None:
                raise HTTPException(status_code=404, detail=f"Review {review_id} not found")

            current_title = row[1]
            stored_content_json = json.loads(row[4]) if row[4] else {}
            next_title = request.title.strip() if request.title is not None else current_title
            if not next_title:
                raise HTTPException(status_code=422, detail="title cannot be empty")

            if request.content_json is not None and not isinstance(request.content_json, dict):
                raise HTTPException(status_code=422, detail="content_json must be an object")

            # Read-time adapter, applied to BOTH directions of this endpoint:
            # an edited payload arriving from the JSON editor may still carry
            # legacy Chinese keys, and the stored row certainly may.
            #
            # What we WRITE stays faithful: a title-only edit must not silently
            # rewrite a legacy row's keys (that would be the destructive
            # migration this design explicitly avoids). Only an explicit
            # content edit writes normalized keys.
            edited_content_json = (
                adapt_stored_content_json(request.content_json)
                if request.content_json is not None
                else None
            )
            content_json_to_store = (
                edited_content_json if edited_content_json is not None else stored_content_json
            )
            next_content_json = (
                edited_content_json
                if edited_content_json is not None
                else adapt_stored_content_json(stored_content_json)
            )

            next_content_markdown = (
                # Resolved off the connection this endpoint already holds, not a
                # fresh one — the row's own database is the authority, and it
                # keeps this path from reaching for the production DB.
                _build_content_markdown(next_content_json, _resolved_db_language(conn))
                if request.content_json is not None
                else row[5]
            )
            next_raw_response_text = (
                json.dumps(content_json_to_store, ensure_ascii=False, indent=2)
                if request.content_json is not None
                else row[9]
            )

            conn.execute(
                """
                UPDATE ai_reports
                SET title = ?, content_json = ?, content_markdown = ?, raw_response_text = ?
                WHERE id = ? AND report_type = 'review'
                """,
                [
                    next_title,
                    json.dumps(content_json_to_store, ensure_ascii=False),
                    next_content_markdown,
                    next_raw_response_text,
                    review_id,
                ],
            )
            mark_dirty()
        finally:
            conn.close()

        return {
            "id": row[0],
            "title": next_title,
            "model_used": row[2],
            "created_at": str(row[3]),
            "content_json": next_content_json,
            "content_markdown": next_content_markdown,
            "period_start": str(row[6]) if row[6] else None,
            "period_end": str(row[7]) if row[7] else None,
            "prompt_text": row[8],
            "raw_response_text": next_raw_response_text,
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("review/%d update failed", review_id)
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/review/{review_id}", status_code=204)
async def delete_review(review_id: int):
    """Delete a saved review report."""
    try:
        conn = duckdb.connect(_get_db_path())
        try:
            row = conn.execute(
                "SELECT id FROM ai_reports WHERE id = ? AND report_type = 'review'",
                [review_id],
            ).fetchone()
            if row is None:
                raise HTTPException(status_code=404, detail=f"Review {review_id} not found")

            conn.execute(
                "DELETE FROM ai_reports WHERE id = ? AND report_type = 'review'",
                [review_id],
            )
            mark_dirty()
        finally:
            conn.close()
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("review/%d delete failed", review_id)
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/review/{review_id}")
async def get_review(review_id: int):
    """Return a specific review with debug text and period metadata."""
    import json as _json
    try:
        conn = duckdb.connect(_get_db_path(), read_only=True)
        try:
            row = conn.execute(
                """
                SELECT id, title, model_used, created_at, content_json, content_markdown,
                       period_start, period_end, prompt_text, raw_response_text
                FROM ai_reports
                WHERE id = ? AND report_type = 'review'
                """,
                [review_id],
            ).fetchone()
        finally:
            conn.close()

        if row is None:
            raise HTTPException(status_code=404, detail=f"Review {review_id} not found")

        return {
            "id": row[0],
            "title": row[1],
            "model_used": row[2],
            "created_at": str(row[3]),
            # Read-time adapter — see section_ids.py.
            "content_json": adapt_stored_content_json(_json.loads(row[4])) if row[4] else {},
            "content_markdown": row[5],
            "period_start": str(row[6]) if row[6] else None,
            "period_end": str(row[7]) if row[7] else None,
            "prompt_text": row[8],
            "raw_response_text": row[9],
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("review/%d failed", review_id)
        raise HTTPException(status_code=500, detail=str(e))


# ------------------------------------------------------------------
# Behavioral metrics endpoints
# ------------------------------------------------------------------

@router.post("/behavioral-metrics/compute")
async def compute_behavioral_metrics(window_days: int = 90):
    """Compute and persist all 6 behavioral metric dimensions."""
    from src.services.ai_advisor.behavioral_metrics import BehavioralMetricsComputer
    computer = BehavioralMetricsComputer(_get_db_path())
    results = computer.compute_all(window_days)
    computer.save_to_db(results)
    mark_dirty()
    return {
        "window_days": window_days,
        "metrics": [
            {
                "dimension": r.dimension,
                "score": r.score,
                "raw_value": r.raw_value,
                "label": r.label,
                "description": r.description,
                "metadata": r.metadata,
            }
            for r in results
        ],
    }


@router.get("/behavioral-metrics/latest")
async def get_latest_behavioral_metrics():
    """Return the most recently computed value for each behavioral metric dimension."""
    try:
        conn = duckdb.connect(_get_db_path(), read_only=True)
        try:
            rows = conn.execute(
                """SELECT dimension, score, raw_value, computation_window_days, metadata_json, computed_at
                   FROM ai_behavioral_log
                   WHERE (dimension, computed_at) IN (
                       SELECT dimension, MAX(computed_at) FROM ai_behavioral_log GROUP BY dimension
                   )
                   ORDER BY dimension"""
            ).fetchall()
        finally:
            conn.close()
        results = []
        for r in rows:
            import json as _json
            meta = {}
            try:
                meta = _json.loads(r[4]) if r[4] else {}
            except Exception:
                pass
            results.append({
                "dimension": r[0],
                "score": r[1],
                "raw_value": r[2],
                "window_days": r[3],
                "label": meta.get("label", ""),
                "description": meta.get("description", ""),
                "metadata": meta.get("metadata"),
                "computed_at": str(r[5]),
            })
        return results
    except Exception as e:
        logger.exception("behavioral_metrics failed")
        return api_error_response(e, context="behavioral-metrics")


# ------------------------------------------------------------------
# Insight lifecycle endpoints
# ------------------------------------------------------------------

class ValidatedCaseRequest(BaseModel):
    """Body for POST /insights/{id}/validated-cases (PRD 2026-07-07 F6)."""
    link: str
    note: Optional[str] = None


class RuleLayerRequest(BaseModel):
    """Body for PUT /insights/{id}/rule-layer (PRD 2026-07-07 F6)."""
    rule_layer: str


class CitationRequest(BaseModel):
    """Body for POST /insights/{id}/citations (PRD 2026-07-07 F6)."""
    memo_id: str
    note: Optional[str] = None


def _insight_promote_eligibility(confidence: Optional[float], validated_cases: Optional[int]):
    """Return (promote_eligible, promote_blocked_reason) for the F6 promote gate.

    Wraps insight_manager.check_promotion_gate — same gate logic actually
    enforced by promote_insight(), so the list view's disabled-button state
    always agrees with what a POST /promote call would do.
    """
    from src.services.ai_advisor.insight_manager import check_promotion_gate
    try:
        check_promotion_gate(confidence, validated_cases)
        return True, None
    except ValueError as e:
        return False, str(e)


def _insight_to_dict(insight) -> Dict[str, Any]:
    """vars(insight) + F6 additive promote-eligibility fields (Rule additive-only)."""
    payload = vars(insight)
    eligible, reason = _insight_promote_eligibility(insight.confidence, insight.validated_cases)
    payload["promote_eligible"] = eligible
    payload["promote_blocked_reason"] = reason
    return payload


def _quarter_bounds(year: int, quarter: int) -> tuple[str, str]:
    """Return (start_iso, end_iso) half-open date range for a calendar quarter."""
    start_month = (quarter - 1) * 3 + 1
    start = date(year, start_month, 1)
    end = date(year + 1, 1, 1) if quarter == 4 else date(year, start_month + 3, 1)
    return start.isoformat(), end.isoformat()


@router.get("/insights")
async def list_insights(
    status: Optional[str] = None,
    category: Optional[str] = None,
    limit: int = 50,
):
    """List insights filtered by status and/or category.

    Additive F6 fields on every item: validated_cases, rule_layer,
    promote_eligible, promote_blocked_reason.
    """
    from src.services.ai_advisor.insight_manager import InsightManager
    insights = InsightManager(_get_db_path()).list_insights(
        status=status, category=category, limit=limit
    )
    return [_insight_to_dict(i) for i in insights]


@router.post("/insights/deduplicate")
async def deduplicate_insights():
    """Remove duplicate insights, keeping oldest per title+category."""
    from src.services.ai_advisor.insight_manager import InsightManager
    conn = duckdb.connect(_get_db_path())
    try:
        manager = InsightManager(_get_db_path())
        result = manager.deduplicate_all(conn)
        mark_dirty()
    finally:
        conn.close()
    return result


@router.put("/insights/{insight_id}")
async def update_insight(insight_id: int, updates: Dict[str, Any]):
    """Update allowed fields (status, tags, confidence, body, title) on an insight."""
    from src.services.ai_advisor.insight_manager import InsightManager
    result = InsightManager(_get_db_path()).update_insight(insight_id, updates)
    if result is None:
        raise HTTPException(status_code=404, detail="Insight not found")
    mark_dirty()
    return vars(result)


@router.post("/insights/{insight_id}/promote")
async def promote_insight(insight_id: int):
    """Advance insight status: raw → recurring → validated → principle.

    PRD 2026-07-07 F6: denied with 422 unless confidence >= 70% OR
    validated_cases >= 3 — see insight_manager.check_promotion_gate.
    """
    from src.services.ai_advisor.insight_manager import InsightManager
    try:
        result = InsightManager(_get_db_path()).promote_insight(insight_id)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    if result is None:
        raise HTTPException(status_code=404, detail="Insight not found")
    mark_dirty()
    return vars(result)


@router.post("/insights/{insight_id}/merge")
async def merge_insights(insight_id: int, duplicate_id: int):
    """Mark duplicate_id as deprecated and increment primary (insight_id) recurrence_count."""
    from src.services.ai_advisor.insight_manager import InsightManager
    result = InsightManager(_get_db_path()).merge_insights(insight_id, duplicate_id)
    if result is None:
        raise HTTPException(status_code=404, detail="Insight not found")
    mark_dirty()
    return vars(result)


@router.post("/insights/{insight_id}/validated-cases")
async def add_validated_case(insight_id: int, body: ValidatedCaseRequest):
    """Increment validated_cases and append {link, note, added_at} to validated_case_links.

    PRD 2026-07-07 F6: validated_cases is one of the two promote-gate paths
    (confidence >= 70% OR validated_cases >= 3) — each call records a link to
    the case being cited as evidence.
    """
    conn = duckdb.connect(_get_db_path())
    try:
        row = conn.execute(
            "SELECT validated_case_links FROM ai_insights WHERE id = ?", [insight_id]
        ).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="Insight not found")
        try:
            existing_links = json.loads(row[0]) if row[0] else []
            if not isinstance(existing_links, list):
                existing_links = []
        except (TypeError, ValueError):
            existing_links = []
        existing_links.append({
            "link": body.link,
            "note": body.note,
            "added_at": datetime.now().isoformat(),
        })
        conn.execute(
            """UPDATE ai_insights
               SET validated_cases = COALESCE(validated_cases, 0) + 1,
                   validated_case_links = ?,
                   updated_at = CURRENT_TIMESTAMP
               WHERE id = ?""",
            [json.dumps(existing_links), insight_id],
        )
        result_row = conn.execute(
            "SELECT validated_cases, validated_case_links FROM ai_insights WHERE id = ?",
            [insight_id],
        ).fetchone()
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("add_validated_case failed for insight_id=%s", insight_id)
        return api_error_response(e, context="add_validated_case")
    finally:
        conn.close()
    mark_dirty()
    return {
        "insight_id": insight_id,
        "validated_cases": result_row[0],
        "validated_case_links": json.loads(result_row[1]) if result_row[1] else [],
    }


@router.put("/insights/{insight_id}/rule-layer")
async def set_rule_layer(insight_id: int, body: RuleLayerRequest):
    """Set rule_layer to 'principle' or 'checklist_item'. 422 on any other value."""
    from src.services.ai_advisor.insight_manager import RULE_LAYER_VALUES
    if body.rule_layer not in RULE_LAYER_VALUES:
        raise HTTPException(
            status_code=422,
            detail=f"rule_layer must be one of {list(RULE_LAYER_VALUES)}, got {body.rule_layer!r}",
        )
    conn = duckdb.connect(_get_db_path())
    try:
        exists = conn.execute("SELECT 1 FROM ai_insights WHERE id = ?", [insight_id]).fetchone()
        if not exists:
            raise HTTPException(status_code=404, detail="Insight not found")
        conn.execute(
            "UPDATE ai_insights SET rule_layer = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            [body.rule_layer, insight_id],
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("set_rule_layer failed for insight_id=%s", insight_id)
        return api_error_response(e, context="set_rule_layer")
    finally:
        conn.close()
    mark_dirty()
    return {"insight_id": insight_id, "rule_layer": body.rule_layer}


@router.post("/insights/{insight_id}/citations")
async def add_citation(insight_id: int, body: CitationRequest):
    """Record a rule citation (v1 manual tick UI — PRD 2026-07-07 F6).

    quarter is derived from cited_at (now) at insert time, e.g. '2026-Q3', so
    the governance report can count citations per rule per quarter without
    re-deriving from a timestamp range on every read.
    """
    conn = duckdb.connect(_get_db_path())
    try:
        exists = conn.execute("SELECT 1 FROM ai_insights WHERE id = ?", [insight_id]).fetchone()
        if not exists:
            raise HTTPException(status_code=404, detail="Insight not found")
        now = datetime.now()
        quarter = f"{now.year}-Q{(now.month - 1) // 3 + 1}"
        conn.execute(
            """INSERT INTO rule_citations (insight_id, memo_id, cited_at, quarter, note)
               VALUES (?, ?, ?, ?, ?)""",
            [insight_id, body.memo_id, now, quarter, body.note],
        )
        citation_id = conn.execute(
            "SELECT id FROM rule_citations WHERE insight_id = ? ORDER BY id DESC LIMIT 1",
            [insight_id],
        ).fetchone()[0]
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("add_citation failed for insight_id=%s", insight_id)
        return api_error_response(e, context="add_citation")
    finally:
        conn.close()
    mark_dirty()
    return {
        "id": citation_id,
        "insight_id": insight_id,
        "memo_id": body.memo_id,
        "quarter": quarter,
        "note": body.note,
    }


@router.get("/insights/{insight_id}/citations")
async def list_citations(insight_id: int):
    """List all rule_citations rows for a given insight."""
    try:
        conn = duckdb.connect(_get_db_path(), read_only=True)
        try:
            rows = conn.execute(
                """SELECT id, insight_id, memo_id, cited_at, quarter, note
                   FROM rule_citations WHERE insight_id = ? ORDER BY cited_at DESC""",
                [insight_id],
            ).fetchall()
        finally:
            conn.close()
    except Exception as e:
        logger.exception("list_citations failed for insight_id=%s", insight_id)
        return api_error_response(e, context="list_citations")
    return [
        {
            "id": r[0], "insight_id": r[1], "memo_id": r[2],
            "cited_at": str(r[3]), "quarter": r[4], "note": r[5],
        }
        for r in rows
    ]


@router.get("/insights/governance-report")
async def insights_governance_report(year: Optional[int] = None, quarter: Optional[int] = None):
    """Quarterly one-in-one-out governance report (PRD 2026-07-07 F6).

    LIMITATION (see 'basis' field in the response): ai_insights has no
    status-transition timestamp log — updated_at is overwritten by ANY field
    edit (title/tags/etc.), not just a status change. 'promoted_this_quarter'
    therefore uses status='principle' AND updated_at within the quarter as the
    best available proxy; it is an upper-bound estimate, not an exact
    promotion-transition count.
    """
    now = datetime.now()
    year = year or now.year
    quarter = quarter or ((now.month - 1) // 3 + 1)
    if quarter not in (1, 2, 3, 4):
        raise HTTPException(status_code=422, detail="quarter must be 1-4")

    quarter_str = f"{year}-Q{quarter}"
    start_iso, end_iso = _quarter_bounds(year, quarter)

    try:
        conn = duckdb.connect(_get_db_path(), read_only=True)
        try:
            promoted_row = conn.execute(
                """SELECT COUNT(*) FROM ai_insights
                   WHERE status = 'principle' AND updated_at >= ? AND updated_at < ?""",
                [start_iso, end_iso],
            ).fetchone()
            promoted_this_quarter = promoted_row[0] if promoted_row else 0

            zero_citation_rows = conn.execute(
                """SELECT id, title FROM ai_insights ai
                   WHERE status = 'principle'
                     AND NOT EXISTS (
                         SELECT 1 FROM rule_citations rc
                         WHERE rc.insight_id = ai.id AND rc.quarter = ?
                     )
                   ORDER BY id""",
                [quarter_str],
            ).fetchall()
        finally:
            conn.close()
    except Exception as e:
        logger.exception("insights_governance_report failed")
        return api_error_response(e, context="insights_governance_report")

    zero_citation_rules = [{"id": r[0], "title": r[1]} for r in zero_citation_rows]
    return {
        "year": year,
        "quarter": quarter,
        "quarter_label": quarter_str,
        "promoted_this_quarter": promoted_this_quarter,
        "zero_citation_rules": zero_citation_rules,
        "pairing_warning": promoted_this_quarter > 0 and len(zero_citation_rules) > 0,
        "basis": (
            "promoted_this_quarter is approximated from status='principle' AND "
            "updated_at within the quarter — ai_insights has no dedicated "
            "status-transition log, and updated_at is also refreshed by "
            "unrelated field edits, so this is an upper-bound estimate"
        ),
    }


@router.get("/insights/checklist-export")
async def checklist_export():
    """Markdown checklist export grouping rule_layer='checklist_item' insights by category.

    PRD 2026-07-07 F6: "grouped by operation type (order placement, FX,
    redemption, etc.)" — category is the closest existing field to
    "operation type" on ai_insights (there is no dedicated operation-type
    field), so grouping uses category as-is.
    """
    try:
        conn = duckdb.connect(_get_db_path(), read_only=True)
        try:
            rows = conn.execute(
                """SELECT COALESCE(category, 'Uncategorized') AS category, title, body
                   FROM ai_insights
                   WHERE rule_layer = 'checklist_item' AND status != 'deprecated'
                   ORDER BY category, title"""
            ).fetchall()
        finally:
            conn.close()
    except Exception as e:
        logger.exception("checklist_export failed")
        return api_error_response(e, context="checklist_export")

    lines = ["# Insight Checklist Export", ""]
    current_category = None
    for category, title, body in rows:
        if category != current_category:
            lines.append(f"## {category}")
            lines.append("")
            current_category = category
        detail = f": {body}" if body and body != title else ""
        lines.append(f"- [ ] {title}{detail}")
    if len(lines) == 2:
        lines.append("_No checklist_item insights found._")
    markdown = "\n".join(lines) + "\n"

    import io as _io
    from fastapi.responses import StreamingResponse
    return StreamingResponse(_io.BytesIO(markdown.encode("utf-8")), media_type="text/markdown")


# ------------------------------------------------------------------
# Brief by ID
# ------------------------------------------------------------------

# ------------------------------------------------------------------
# Trade recording endpoints
# ------------------------------------------------------------------

@router.get("/assets/search")
async def search_assets(q: str = Query(default="")):
    """Search asset registry by id or display name. Min 2 chars."""
    if len(q) < 2:
        return {"assets": []}
    # Escape wildcards
    q_escaped = q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    try:
        conn = duckdb.connect(_get_db_path(), read_only=True)
        try:
            rows = conn.execute(
                """
                SELECT canonical_id, display_name, asset_class, base_currency
                FROM asset_registry
                WHERE canonical_id ILIKE '%' || ? || '%' ESCAPE '\\'
                   OR display_name ILIKE '%' || ? || '%' ESCAPE '\\'
                LIMIT 20
                """,
                [q_escaped, q_escaped],
            ).fetchall()
        finally:
            conn.close()
        return {
            "assets": [
                {
                    "asset_id": r[0],
                    "display_name": r[1],
                    "asset_class": r[2],
                    "base_currency": r[3],
                }
                for r in rows
            ]
        }
    except Exception as e:
        logger.exception("assets/search failed")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/trades", status_code=201)
async def create_trade(request: CreateTradeRequest):
    """Record a manual trade into trade_logs. Never touches transactions table."""
    from datetime import date as _date

    # Validate action
    if request.action not in ("Buy", "Sell"):
        raise HTTPException(status_code=422, detail="action must be 'Buy' or 'Sell'")

    # Validate date
    try:
        _date.fromisoformat(request.log_date)
    except ValueError:
        raise HTTPException(status_code=422, detail="log_date must be YYYY-MM-DD")

    # Reject non-finite numeric inputs
    for field_name, val in [("price", request.price), ("quantity", request.quantity), ("amount", request.amount)]:
        if val is not None and not math.isfinite(val):
            raise HTTPException(status_code=422, detail=f"{field_name} must be a finite number")

    # Validate amount OR (price AND quantity)
    has_amount = request.amount is not None and request.amount > 0
    has_price_qty = (
        request.price is not None and request.price > 0
        and request.quantity is not None and request.quantity > 0
    )
    if not has_amount and not has_price_qty:
        raise HTTPException(
            status_code=422,
            detail="Must provide amount > 0, or both price > 0 and quantity > 0",
        )
    # Validate all monetary values are positive
    for field_name, val in [("price", request.price), ("quantity", request.quantity), ("amount", request.amount)]:
        if val is not None and val <= 0:
            raise HTTPException(status_code=422, detail=f"{field_name} must be positive")

    linked_memo_id: Optional[int] = None

    try:
        conn = duckdb.connect(_get_db_path(), read_only=True)
        try:
            asset_row = conn.execute(
                "SELECT display_name, base_currency FROM asset_registry WHERE canonical_id = ?",
                [request.asset_id],
            ).fetchone()
            if request.memo_id is not None:
                try:
                    memo_row = conn.execute(
                        "SELECT id FROM strategy_memos WHERE id = ?",
                        [request.memo_id],
                    ).fetchone()
                except Exception:
                    memo_row = None
                if memo_row is not None:
                    linked_memo_id = int(memo_row[0])
        finally:
            conn.close()
    except Exception as e:
        logger.exception("trades POST: asset lookup failed")
        raise HTTPException(status_code=500, detail=str(e))

    if asset_row is None:
        asset_name = request.asset_name or request.asset_id
        currency = request.currency or "USD"
    else:
        asset_name = request.asset_name or asset_row[0]
        currency = request.currency or asset_row[1]

    # Compute amount if not provided
    amount = request.amount if has_amount else (request.price * request.quantity)
    has_linked_memo_id = False
    has_verification_status = False

    try:
        conn = duckdb.connect(_get_db_path())
        try:
            has_linked_memo_id = _table_has_column(conn, "trade_logs", "linked_memo_id")
            has_verification_status = _table_has_column(conn, "trade_logs", "verification_status")

            columns = [
                "log_date",
                "asset_id",
                "asset_name",
                "action",
                "price",
                "quantity",
                "amount",
                "currency",
                "decision_reason",
                "suggestion_source",
            ]
            values = [
                request.log_date,
                request.asset_id,
                asset_name,
                request.action,
                request.price,
                request.quantity,
                amount,
                currency,
                request.decision_reason,
                "manual",
            ]

            if has_linked_memo_id:
                columns.append("linked_memo_id")
                values.append(linked_memo_id)
            if has_verification_status:
                columns.append("verification_status")
                values.append("pending")

            placeholders = ", ".join("?" for _ in values)
            row = conn.execute(
                f"""
                INSERT INTO trade_logs ({", ".join(columns)})
                VALUES ({placeholders})
                RETURNING id
                """,
                values,
            ).fetchone()
            trade_id = row[0] if row else None
            if trade_id is not None:
                try:
                    score_single_trade(conn, trade_id)
                except Exception:
                    logger.exception("trades POST: score_single_trade failed for trade_id=%s", trade_id)
            mark_dirty()
        finally:
            conn.close()
    except Exception as e:
        logger.exception("trades POST: insert failed")
        raise HTTPException(status_code=500, detail=str(e))

    return {
        "id": trade_id,
        "log_date": request.log_date,
        "asset_id": request.asset_id,
        "asset_name": asset_name,
        "action": request.action,
        "price": request.price,
        "quantity": request.quantity,
        "amount": amount,
        "currency": currency,
        "decision_reason": request.decision_reason,
        "suggestion_source": "manual",
        "linked_memo_id": linked_memo_id if has_linked_memo_id else None,
        "verification_status": "pending" if has_verification_status else None,
    }


@router.get("/trades")
async def list_trades(limit: int = Query(default=50, ge=1, le=1000)):
    """List recent trade log entries."""
    try:
        conn = duckdb.connect(_get_db_path(), read_only=True)
        try:
            select_linked_memo = ", linked_memo_id" if _table_has_column(conn, "trade_logs", "linked_memo_id") else ", NULL AS linked_memo_id"
            select_verification_status = ", verification_status" if _table_has_column(conn, "trade_logs", "verification_status") else ", NULL AS verification_status"
            rows = conn.execute(
                f"""
                SELECT id, log_date, asset_id, asset_name, action, price, quantity,
                       amount, currency, decision_reason, suggestion_source{select_linked_memo}{select_verification_status}
                FROM trade_logs
                ORDER BY log_date DESC, id DESC
                LIMIT ?
                """,
                [limit],
            ).fetchall()
        finally:
            conn.close()
        return {
            "trades": [
                {
                    "id": r[0],
                    "log_date": str(r[1]),
                    "asset_id": r[2],
                    "asset_name": r[3],
                    "action": r[4],
                    "price": float(r[5]) if r[5] is not None else None,
                    "quantity": float(r[6]) if r[6] is not None else None,
                    "amount": float(r[7]) if r[7] is not None else None,
                    "currency": r[8],
                    "decision_reason": r[9],
                    "suggestion_source": r[10],
                    "linked_memo_id": int(r[11]) if r[11] is not None else None,
                    "verification_status": r[12] or "pending",
                }
                for r in rows
            ]
        }
    except Exception as e:
        logger.exception("trades GET failed")
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/trades/{trade_id}", status_code=204)
async def delete_trade(trade_id: int):
    """Delete a trade. Only allowed for manual/human/user-sourced trades."""
    try:
        conn = duckdb.connect(_get_db_path())
        try:
            # First check existence and source
            row = conn.execute(
                "SELECT id, suggestion_source FROM trade_logs WHERE id = ?",
                [trade_id],
            ).fetchone()
            if row is None:
                raise HTTPException(status_code=404, detail=f"Trade {trade_id} not found")
            suggestion_source = (row[1] or "").strip().lower()
            if suggestion_source not in ("manual", "human", "user"):
                raise HTTPException(
                    status_code=403,
                    detail=f"Cannot delete trade with suggestion_source='{row[1]}'. Only manual/human/user trades may be deleted.",
                )
            # Atomic delete with guard — protects against TOCTOU
            deleted = conn.execute(
                """DELETE FROM trade_logs
                   WHERE id = ?
                   AND LOWER(TRIM(COALESCE(suggestion_source, ''))) IN ('manual', 'human', 'user')
                   RETURNING id""",
                [trade_id],
            ).fetchone()
            if deleted is None:
                # Race: another request changed suggestion_source between check and delete
                raise HTTPException(status_code=403, detail="Trade is no longer deletable")
            mark_dirty()
        finally:
            conn.close()
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("trades DELETE: failed")
        raise HTTPException(status_code=500, detail=str(e))
    return None


# ------------------------------------------------------------------
# Brief by ID
# ------------------------------------------------------------------

@router.get("/brief/{brief_id}")
async def get_brief(brief_id: int):
    """Return a specific brief by ID."""
    try:
        conn = duckdb.connect(_get_db_path(), read_only=True)
        try:
            row = conn.execute(
                """
                SELECT id, report_type, title, model_used, content_json, content_markdown,
                       context_config_json, created_at, prompt_text, raw_response_text
                FROM ai_reports
                WHERE id = ? AND report_type = 'brief'
                """,
                [brief_id],
            ).fetchone()
        finally:
            conn.close()

        if row is None:
            raise HTTPException(status_code=404, detail=f"Brief {brief_id} not found")

        import json as _json
        id_, report_type, title, model_used, content_json_str, content_markdown, context_cfg_str, created_at, prompt_text, raw_response_text = row
        return {
            "id": id_,
            "report_type": report_type,
            "title": title,
            "model_used": model_used,
            # Read-time adapter — see section_ids.py.
            "content_json": adapt_stored_content_json(_json.loads(content_json_str))
            if content_json_str
            else None,
            "content_markdown": content_markdown,
            "context_config": _json.loads(context_cfg_str) if context_cfg_str else None,
            "created_at": str(created_at),
            "prompt_text": prompt_text,
            "raw_response_text": raw_response_text,
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("brief/%d failed", brief_id)
        raise HTTPException(status_code=500, detail=str(e))


# ------------------------------------------------------------------
# Position Deltas
# ------------------------------------------------------------------

@router.get("/position-deltas")
async def get_position_deltas():
    """Return unconfirmed position deltas detected between syncs."""
    try:
        conn = duckdb.connect(_get_db_path(), read_only=True)
        try:
            rows = conn.execute(
                """
                SELECT id, asset_id, old_qty, new_qty, delta_qty,
                       detected_at, source_system,
                       old_snapshot_date, new_snapshot_date, confirmed
                FROM position_deltas
                WHERE confirmed = FALSE
                ORDER BY detected_at DESC
                """
            ).fetchall()
        finally:
            conn.close()

        return [
            {
                "id": row[0],
                "asset_id": row[1],
                "old_qty": float(row[2]) if row[2] is not None else 0.0,
                "new_qty": float(row[3]) if row[3] is not None else 0.0,
                "delta_qty": float(row[4]),
                "detected_at": str(row[5]),
                "source_system": row[6],
                "old_snapshot_date": str(row[7]) if row[7] else None,
                "new_snapshot_date": str(row[8]) if row[8] else None,
                "confirmed": row[9],
            }
            for row in rows
        ]
    except Exception as e:
        logger.exception("position-deltas GET failed")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/position-deltas/{delta_id}/confirm")
async def confirm_position_delta(delta_id: int):
    """Mark a position delta as confirmed (reviewed). Does NOT create a trade log entry."""
    try:
        conn = duckdb.connect(_get_db_path(), read_only=False)
        try:
            result = conn.execute(
                "UPDATE position_deltas SET confirmed = TRUE WHERE id = ? RETURNING id",
                [delta_id],
            ).fetchone()
            if result is not None:
                mark_dirty()
        finally:
            conn.close()

        if result is None:
            raise HTTPException(status_code=404, detail=f"Position delta {delta_id} not found")

        return {"id": delta_id, "confirmed": True}
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("position-deltas/%d/confirm failed", delta_id)
        raise HTTPException(status_code=500, detail=str(e))


# ------------------------------------------------------------------
# V5.8.0 Decision Feedback Loop endpoints  (Batch B — Steps 3, 4, 5)
# ------------------------------------------------------------------

def _serialize_trade_row(row: tuple, col_names: list[str]) -> dict[str, Any]:
    """Convert a trade_logs row-tuple + column name list into a JSON-ready dict."""
    d: dict[str, Any] = {}
    for name, val in zip(col_names, row):
        if isinstance(val, datetime):
            d[name] = val.isoformat(timespec="microseconds")
        elif hasattr(val, "isoformat"):
            # date objects
            d[name] = val.isoformat()
        else:
            d[name] = val
    return d


def _fetch_trade_row(conn: duckdb.DuckDBPyConnection, trade_id: int) -> dict[str, Any] | None:
    """Return a single trade_logs row as a dict, or None if not found."""
    cols = [
        "id", "log_date", "asset_id", "asset_name", "action",
        "price", "quantity", "amount", "currency",
        "decision_reason", "suggestion_source",
        "verification_status", "verification_result", "verification_date",
        "verification_block_reason", "verdict", "outcome_pct", "updated_at",
    ]
    select_cols = ", ".join(cols)
    row = conn.execute(
        f"SELECT {select_cols} FROM trade_logs WHERE id = ?",
        [trade_id],
    ).fetchone()
    if row is None:
        return None
    return _serialize_trade_row(row, cols)


_PENDING_VERIFICATION_STATUS_MAP = {
    "pending": "verification_status IN ('pending', 'pending_window')",
    # T4: hide bulk-imported reader rows that have neither a verdict nor a narrative —
    # these are 2000+ ledger rows that pollute the history with all "—" entries.
    # verification_blocked rows are ALWAYS visible (the Reopen flow needs them even
    # though they carry no verdict and no narrative by definition).
    "verified": """(
        verification_status = 'verification_blocked'
        OR (
            verification_status = 'verified'
            AND (verdict IS NOT NULL OR COALESCE(verification_result,'') != '')
        )
    )""",
    # For status=all: pending + unmatched rows are shown unconditionally (unmatched was
    # visible under the previous 1=1 map); blocked rows always (Reopen flow); verified
    # rows require the display-scope filter above so history is clean while the pending
    # list is unchanged.
    "all": """(
        verification_status IN ('pending', 'pending_window', 'unmatched')
        OR verification_status = 'verification_blocked'
        OR (
            verification_status = 'verified'
            AND (verdict IS NOT NULL OR COALESCE(verification_result,'') != '')
        )
    )""",
}


@router.get("/trades/pending-verification")
async def list_pending_verification(
    since: str = Query(...),
    until: str = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    status: str = Query(default="pending"),
):
    """List trades by verification status within the date range.

    status=pending (default): pending/pending_window only.
    status=verified: verified/verification_blocked history (enables Reopen flow).
    status=all: all verification statuses.

    For matured pending rows computes outcome_pct_preview and suggested_verdict.
    Includes linked_insight_id where available.
    """
    if status not in _PENDING_VERIFICATION_STATUS_MAP:
        raise HTTPException(status_code=400, detail=f"Invalid status filter {status!r}; must be pending|verified|all")

    # Validate date params
    try:
        since_date = date.fromisoformat(since)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Invalid since date: {since!r}")

    until_str = until or date.today().isoformat()
    try:
        until_date = date.fromisoformat(until_str)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Invalid until date: {until_str!r}")

    status_where = _PENDING_VERIFICATION_STATUS_MAP[status]

    try:
        conn = duckdb.connect(_get_db_path(), read_only=True)
        try:
            rows = conn.execute(
                f"""
                SELECT id, log_date, asset_id, asset_name, action,
                       price, quantity, amount, currency,
                       decision_reason, suggestion_source,
                       verification_status, updated_at,
                       ai_suggestion,
                       verdict, outcome_pct, verification_result, verification_date
                FROM trade_logs
                WHERE {status_where}
                  AND (log_date BETWEEN ? AND ? OR verification_status = 'pending_window')
                ORDER BY log_date DESC
                LIMIT ?
                """,
                [since_date, until_date, limit],
            ).fetchall()
        finally:
            conn.close()
    except Exception as e:
        logger.exception("trades/pending-verification GET failed")
        raise HTTPException(status_code=500, detail=str(e))

    today = date.today()
    items: list[dict[str, Any]] = []

    # Open a second connection for compute_outcome_pct_from_prices and find_linked_insight
    try:
        conn2 = duckdb.connect(_get_db_path(), read_only=True)
    except Exception as e:
        logger.exception("trades/pending-verification: failed to open second connection")
        raise HTTPException(status_code=500, detail=str(e))

    try:
        from src.services.decision_intelligence import find_linked_insight

        for (
            row_id, log_date, asset_id, asset_name, action,
            price, quantity, amount, currency,
            decision_reason, suggestion_source,
            verification_status, updated_at,
            ai_suggestion,
            db_verdict, db_outcome_pct, verification_result, verification_date_raw,
        ) in rows:
            log_date_d: date = log_date if isinstance(log_date, date) else date.fromisoformat(str(log_date)[:10])
            age_days = (today - log_date_d).days
            is_matured = age_days >= 30

            outcome_pct_preview: float | None = None
            suggested_verdict: str | None = None

            if is_matured:
                try:
                    outcome_pct_preview = compute_outcome_pct_from_prices(
                        conn2, row_id, asset_id or "", action or "", log_date_d
                    )
                    suggested_verdict = derive_verdict_suggestion(action or "", outcome_pct_preview)
                    # Neutral suggestion: outcome computable but within band → no directional verdict.
                    # Mirrors the scorer's neutral fallback so the chip shows 中性 instead of "Set manually".
                    if outcome_pct_preview is not None and suggested_verdict is None:
                        suggested_verdict = VERDICT_NEUTRAL
                except Exception:
                    logger.debug("outcome preview failed for trade_id=%s", row_id)

            # T1: interim "outcome so far" — available for ALL pending rows (pre- and post-maturity).
            # Matured rows get both fields; pre-window rows get only the to-date pair.
            # Skipped for verified/blocked history rows: they display the stored outcome_pct,
            # and computing to-date for 100 history rows would add 2 price queries per row.
            outcome_to_date_pct: float | None = None
            outcome_to_date_asof: str | None = None
            if verification_status in ("pending", "pending_window"):
                try:
                    otd = compute_outcome_to_date(conn2, asset_id or "", action or "", log_date_d)
                    if otd is not None:
                        outcome_to_date_pct = otd[0]
                        outcome_to_date_asof = otd[1].isoformat()
                except Exception:
                    logger.debug("compute_outcome_to_date failed for trade_id=%s", row_id)

            linked_insight_id: int | None = None
            linked_insight_title: str | None = None
            try:
                linked = find_linked_insight(
                    conn2,
                    asset_id,
                    log_date_d,
                    ai_suggestion=ai_suggestion,
                    decision_reason=decision_reason,
                    suggestion_source=suggestion_source,
                )
                if linked and isinstance(linked.get("id"), int):
                    linked_insight_id = linked["id"]
                    linked_insight_title = linked.get("title")
            except Exception:
                logger.debug("find_linked_insight failed for trade_id=%s", row_id)

            updated_at_str: str | None = None
            if updated_at is not None:
                if isinstance(updated_at, datetime):
                    updated_at_str = updated_at.isoformat(timespec="microseconds")
                else:
                    updated_at_str = str(updated_at)

            vdate_str: str | None = None
            if verification_date_raw is not None:
                if isinstance(verification_date_raw, date):
                    vdate_str = verification_date_raw.isoformat()
                else:
                    vdate_str = str(verification_date_raw)[:10]

            items.append({
                "id": row_id,
                "log_date": log_date_d.isoformat(),
                "asset_id": asset_id,
                "asset_name": asset_name,
                "action": action,
                "price": float(price) if price is not None else None,
                "quantity": float(quantity) if quantity is not None else None,
                "amount_cny": float(amount) if amount is not None else None,
                "currency": currency,
                "decision_reason": decision_reason,
                "suggestion_source": suggestion_source,
                "verification_status": verification_status,
                "is_matured": is_matured,
                "outcome_pct_preview": outcome_pct_preview,
                "suggested_verdict": suggested_verdict,
                # T1: interim outcome fields — present for all pending rows regardless of maturity.
                "outcome_to_date_pct": outcome_to_date_pct,
                "outcome_to_date_asof": outcome_to_date_asof,
                "linked_insight_id": linked_insight_id,
                "linked_insight_title": linked_insight_title,
                "updated_at": updated_at_str,
                "verdict": db_verdict,
                "outcome_pct": float(db_outcome_pct) if db_outcome_pct is not None else None,
                "verification_result": verification_result,
                "verification_date": vdate_str,
            })
    finally:
        conn2.close()

    return {"items": items}


@router.post("/trades/{trade_id}/verify")
async def verify_trade(trade_id: int, body: VerifyTradeRequest):
    """Capture a verification narrative + optional verdict for a pending trade.

    Idempotent on pending_window: second call updates the narrative.
    Uses optimistic concurrency via expected_updated_at.
    Scoring is best-effort: never fails the endpoint on scorer error.
    """
    verification_result = body.verification_result.strip() if body.verification_result else ""
    if not verification_result:
        raise HTTPException(status_code=400, detail="verification_result_blank")

    if body.verdict is not None and body.verdict not in _VALID_VERDICTS:
        raise HTTPException(
            status_code=400,
            detail=f"invalid verdict '{body.verdict}'; must be one of {sorted(_VALID_VERDICTS)}",
        )

    verification_date_str = body.verification_date or date.today().isoformat()
    try:
        date.fromisoformat(verification_date_str)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Invalid verification_date: {verification_date_str!r}")

    try:
        conn = duckdb.connect(_get_db_path())
        try:
            # 1. Fetch current row
            existing = _fetch_trade_row(conn, trade_id)
            if existing is None:
                raise HTTPException(status_code=404, detail="trade not found")

            # 2. Optimistic concurrency check
            current_updated_at = existing.get("updated_at")
            if body.expected_updated_at is not None:
                if current_updated_at != body.expected_updated_at:
                    raise HTTPException(
                        status_code=412,
                        detail={
                            "detail": "stale_updated_at",
                            "current_updated_at": current_updated_at,
                        },
                    )

            # 3. Status guard
            current_status = existing.get("verification_status") or "pending"
            if current_status not in ("pending", "pending_window"):
                raise HTTPException(
                    status_code=409,
                    detail={
                        "detail": "cannot verify",
                        "current_status": current_status,
                        "hint": "use /reopen-verification first",
                    },
                )

            # 4. Fully atomic UPDATE — verdict merged in to eliminate the non-atomic step 5
            # When the user explicitly provides a verdict, treat it as final → 'verified'.
            # Without an explicit verdict the trade stays pending_window, waiting for scorer.
            # F1.2 (flag-gated): once process_verification is enabled, /verify must never
            # write an emotive verdict — process correctness is captured separately via
            # authorized/params_ok/data_verified below. The legacy verdict column (and the
            # 'verified' status jump it triggers) is only honored while the flag is off.
            process_flag_on = load_verification_config().process_verification.enabled
            apply_legacy_verdict = body.verdict is not None and not process_flag_on
            new_status = "verified" if apply_legacy_verdict else "pending_window"
            set_clauses = [
                "verification_result = ?",
                "verification_date = ?",
                f"verification_status = '{new_status}'",
                "updated_at = CURRENT_TIMESTAMP",
            ]
            update_params: list = [verification_result, verification_date_str]
            if apply_legacy_verdict:
                set_clauses.append("verdict = ?")
                update_params.append(body.verdict)
            update_params.extend([trade_id, current_updated_at])
            result = conn.execute(
                f"UPDATE trade_logs SET {', '.join(set_clauses)}"
                " WHERE id = ? AND verification_status IN ('pending', 'pending_window')"
                " AND updated_at = ? RETURNING id",
                update_params,
            ).fetchone()

            if result is None:
                # Race: someone changed status or updated_at between SELECT and UPDATE
                raise HTTPException(
                    status_code=409,
                    detail={
                        "detail": "cannot verify",
                        "current_status": "unknown",
                        "hint": "concurrent update — please retry",
                    },
                )

            # 5. Best-effort scoring
            try:
                score_single_trade(conn, trade_id)
            except Exception as exc:
                logger.warning(
                    "score_single_trade after /verify failed for trade_id=%s: %s", trade_id, exc
                )

            # 5b. F1.2 process checks — independent of the flag (data entry is allowed
            # before the flag flips, PRD Rollout step 2) and best-effort like scoring.
            if any(v is not None for v in (body.authorized, body.params_ok, body.data_verified, body.notes)):
                try:
                    set_process_checks(
                        conn, trade_id,
                        authorized=body.authorized,
                        params_ok=body.params_ok,
                        data_verified=body.data_verified,
                        notes=body.notes,
                    )
                except Exception as exc:
                    logger.warning(
                        "set_process_checks after /verify failed for trade_id=%s: %s", trade_id, exc
                    )

            mark_dirty()

            # 6. Re-fetch updated row
            updated = _fetch_trade_row(conn, trade_id)
        finally:
            conn.close()
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("trades/%d/verify failed", trade_id)
        raise HTTPException(status_code=500, detail=str(e))

    # F1.2 contract honesty: with the flag on, a legacy `verdict` in the body is
    # deliberately not written (emotive verdicts are retired). Say so in the
    # response instead of silently dropping it — pre-flag clients keep sending it.
    if body.verdict is not None and not apply_legacy_verdict:
        logger.warning(
            "trades/%d/verify: legacy verdict %r ignored (process_verification flag on)",
            trade_id, body.verdict,
        )
        if isinstance(updated, dict):
            updated["verdict_ignored"] = True

    return updated


@router.put("/trades/{trade_id}/process-checks")
async def update_process_checks(trade_id: int, body: ProcessChecksRequest):
    """Set F1.2 process checks (authorized/params_ok/data_verified/notes) for a trade.

    Independent of verification_status and the process_verification feature flag —
    data entry must be possible before the flag flips (PRD Rollout step 2: owner
    reviews the backfill CSV, then flips the flag; process checks need to already
    exist by then). Partial update: fields omitted (None) are left unchanged.
    An all-None body is rejected: set_process_checks always stamps
    process_checked_at, and a no-op call must not make the trade look reviewed.
    """
    if all(v is None for v in (body.authorized, body.params_ok, body.data_verified, body.notes)):
        raise HTTPException(
            status_code=422,
            detail="at least one of authorized/params_ok/data_verified/notes must be provided",
        )
    try:
        conn = duckdb.connect(_get_db_path())
        try:
            existing = _fetch_trade_row(conn, trade_id)
            if existing is None:
                raise HTTPException(status_code=404, detail="trade not found")

            set_process_checks(
                conn, trade_id,
                authorized=body.authorized,
                params_ok=body.params_ok,
                data_verified=body.data_verified,
                notes=body.notes,
            )
            mark_dirty()

            row = conn.execute(
                """
                SELECT id, rule_bucket, memo_id, process_authorized, process_params_ok,
                       process_data_verified, process_checked_at, process_notes, updated_at
                FROM trade_logs WHERE id = ?
                """,
                [trade_id],
            ).fetchone()
        finally:
            conn.close()
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("trades/%d/process-checks failed", trade_id)
        raise HTTPException(status_code=500, detail=str(e))

    cols = [
        "id", "rule_bucket", "memo_id", "process_authorized", "process_params_ok",
        "process_data_verified", "process_checked_at", "process_notes", "updated_at",
    ]
    return _serialize_trade_row(row, cols)


@router.post("/trades/{trade_id}/reopen-verification")
async def reopen_verification(trade_id: int, body: ReopenVerificationRequest):
    """Revert a verified/blocked/pending_window trade back to pending_window.

    Clears verdict, outcome_pct, and verification_block_reason.
    Idempotent: if already pending_window, returns 200 with current row.
    """
    try:
        conn = duckdb.connect(_get_db_path())
        try:
            # 1. Fetch current row
            existing = _fetch_trade_row(conn, trade_id)
            if existing is None:
                raise HTTPException(status_code=404, detail="trade not found")

            # 2. Optimistic concurrency check
            current_updated_at = existing.get("updated_at")
            if body.expected_updated_at is not None:
                if current_updated_at != body.expected_updated_at:
                    raise HTTPException(
                        status_code=412,
                        detail={
                            "detail": "stale_updated_at",
                            "current_updated_at": current_updated_at,
                        },
                    )

            # 3. Atomic UPDATE — AND updated_at = ? prevents silent overwrites on concurrent requests
            result = conn.execute(
                """
                UPDATE trade_logs
                SET verification_status = 'pending_window',
                    verdict = NULL,
                    outcome_pct = NULL,
                    verification_block_reason = NULL,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                  AND verification_status IN ('verified', 'verification_blocked', 'pending_window', 'pending')
                  AND updated_at = ?
                RETURNING id
                """,
                [trade_id, current_updated_at],
            ).fetchone()

            if result is None:
                # Race or already-pending — idempotent: return current row
                logger.debug("reopen-verification: trade_id=%s rowcount=0 — returning current row", trade_id)
            else:
                mark_dirty()

            # 4. Re-fetch updated row
            updated = _fetch_trade_row(conn, trade_id)
        finally:
            conn.close()
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("trades/%d/reopen-verification failed", trade_id)
        raise HTTPException(status_code=500, detail=str(e))

    return updated


# ------------------------------------------------------------------
# V5.8.0 Decision Feedback Loop endpoints  (Batch C — Steps 8, 9)
# ------------------------------------------------------------------

class CrossCheckRequest(BaseModel):
    """Request body for the cross-check audit endpoint."""

    period_start: str   # YYYY-MM-DD
    period_end: str     # YYYY-MM-DD
    model: Optional[str] = None


@router.post("/review/cross-check")
async def post_cross_check_audit(body: CrossCheckRequest):
    """Generate an LLM cross-check audit of insights vs. trade outcomes for a period.

    Validates date formats, caps window/insight/trade counts via build_cross_check_context,
    persists to ai_reports, and returns {audit_markdown, summary, model_used, generated_at, report_id}.
    """
    # Validate date format
    try:
        period_start = date.fromisoformat(body.period_start)
        period_end = date.fromisoformat(body.period_end)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"Invalid date format: {exc}")

    # Open a DatabaseConnector so context builder gets its expected interface
    from src.database.connector import DatabaseConnector as _DBConnector
    try:
        db = _DBConnector(_get_db_path())
        try:
            result = generate_cross_check_audit(
                db=db,
                period_start=period_start,
                period_end=period_end,
                model=body.model,
            )
        finally:
            db.close()
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("review/cross-check failed")
        raise HTTPException(status_code=500, detail=str(exc))

    mark_dirty()
    return result


@router.get("/diagnostics/verdict-mismatch-rate")
async def get_verdict_mismatch_rate(since: str = Query(..., description="YYYY-MM-DD cutoff date")):
    """Return threshold↔keyword verdict mismatch rate aggregated since the given date.

    Reads from verdict_audit (INSERT-only log written by score_single_trade).
    Returns {since, total_scored, threshold_keyword_mismatch_count, mismatch_rate_pct}.
    Returns 0/0/0.0 if no audit rows exist in the window.
    """
    try:
        conn = duckdb.connect(_get_db_path(), read_only=True)
        try:
            row = conn.execute(
                """
                SELECT
                    COUNT(*) AS total,
                    SUM(CASE WHEN mismatch THEN 1 ELSE 0 END) AS mismatch_count
                FROM verdict_audit
                WHERE created_at >= ?
                """,
                [since],
            ).fetchone()
        finally:
            conn.close()
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("diagnostics/verdict-mismatch-rate failed")
        raise HTTPException(status_code=500, detail=str(exc))

    total = int(row[0]) if row and row[0] is not None else 0
    mismatch_count = int(row[1]) if row and row[1] is not None else 0
    mismatch_rate = round((mismatch_count / total) * 100, 2) if total > 0 else 0.0

    return {
        "since": since,
        "total_scored": total,
        "threshold_keyword_mismatch_count": mismatch_count,
        "mismatch_rate_pct": mismatch_rate,
    }


# ── B2: Memo-update proposals ─────────────────────────────────────────────────

class MemoProposalRequest(BaseModel):
    audit_report_id: Optional[int] = None  # omit to use most recent cross_check_audit


@router.post("/memos/{memo_id}/propose-updates")
async def post_memo_propose_updates(
    memo_id: int,
    body: MemoProposalRequest = MemoProposalRequest(),
):
    """Generate LLM-proposed edits to a strategy memo grounded in the latest cross-check audit.

    Does NOT mutate strategy_memos — returns proposals for user review.
    The user accepts/rejects each clause and calls PUT /strategy/memos/{id} to apply.
    Returns {proposals, report_id, model_used, memo_id, generated_at}.
    """
    from src.database.connector import DatabaseConnector as _DBConn

    db = _DBConn(_get_db_path())
    try:
        result = propose_memo_updates(
            db=db,
            memo_id=memo_id,
            audit_report_id=body.audit_report_id,
        )
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("memos/%s/propose-updates failed", memo_id)
        raise HTTPException(status_code=500, detail=str(exc))
    finally:
        db.close()

    if "error" in result:
        status = 404 if "not found" in result["error"] else 422
        raise HTTPException(status_code=status, detail=result["error"])

    mark_dirty()
    return result


# ── A1: Insight-trade link CRUD ───────────────────────────────────────────────

class ManualLinkRequest(BaseModel):
    insight_id: int
    trade_id: int
    rationale: Optional[str] = None


@router.get("/insights/{insight_id}/links")
async def get_insight_links(insight_id: int):
    """List all insight_trade_links rows for a given insight."""
    try:
        conn = duckdb.connect(_get_db_path(), read_only=True)
        try:
            rows = conn.execute(
                """
                SELECT itl.id, itl.insight_id, itl.trade_id, itl.link_type,
                       itl.confidence, itl.rationale, itl.created_at,
                       tl.log_date, tl.asset_id, tl.action
                FROM insight_trade_links itl
                LEFT JOIN trade_logs tl ON tl.id = itl.trade_id
                WHERE itl.insight_id = ?
                ORDER BY itl.created_at DESC
                """,
                [insight_id],
            ).fetchall()
        finally:
            conn.close()
    except Exception as e:
        logger.exception("insights/%d/links GET failed", insight_id)
        raise HTTPException(status_code=500, detail=str(e))

    links = []
    for (link_id, ins_id, trade_id, link_type, confidence, rationale, created_at,
         log_date, asset_id, action) in rows:
        links.append({
            "id": link_id,
            "insight_id": ins_id,
            "trade_id": trade_id,
            "link_type": link_type,
            "confidence": float(confidence) if confidence is not None else None,
            "rationale": rationale,
            "created_at": created_at.isoformat() if hasattr(created_at, "isoformat") else str(created_at),
            "trade_log_date": log_date.isoformat() if hasattr(log_date, "isoformat") else (str(log_date)[:10] if log_date else None),
            "trade_asset_id": asset_id,
            "trade_action": action,
        })
    return {"links": links}


@router.post("/links", status_code=201)
async def post_manual_link(body: ManualLinkRequest):
    """Create a manual insight_trade_link. Idempotent on (insight_id, trade_id)."""
    from src.services.decision_links import add_manual_link
    from src.database.connector import DatabaseConnector as _DBConn

    db = _DBConn(_get_db_path())
    try:
        link_id = add_manual_link(db, body.insight_id, body.trade_id, body.rationale or "")
    except Exception as exc:
        logger.exception("POST /links failed")
        raise HTTPException(status_code=500, detail=str(exc))
    finally:
        db.close()

    if link_id is None:
        raise HTTPException(status_code=409, detail="Link already exists for this insight-trade pair")
    mark_dirty()
    return {"id": link_id, "insight_id": body.insight_id, "trade_id": body.trade_id, "link_type": "manual"}


@router.delete("/links/{link_id}", status_code=204)
async def delete_link(link_id: int):
    """Remove an insight_trade_link by id."""
    from src.services.decision_links import remove_link
    from src.database.connector import DatabaseConnector as _DBConn

    db = _DBConn(_get_db_path())
    try:
        remove_link(db, link_id)
    except Exception as exc:
        logger.exception("DELETE /links/%d failed", link_id)
        raise HTTPException(status_code=500, detail=str(exc))
    finally:
        db.close()

    mark_dirty()
