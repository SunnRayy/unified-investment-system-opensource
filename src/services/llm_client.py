"""
LLM Client — wraps litellm with model fallback chain, JSON parsing, and fire-and-forget usage logging.

Usage:
    client = LLMClient()
    response = client.complete(system_prompt="...", user_prompt="...", expect_json=True, report_type="brief")
    if response.success:
        data = response.content_json  # dict, if expect_json=True
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import yaml

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Per-token pricing constants (USD per 1,000 tokens)
# ---------------------------------------------------------------------------
_PRICING: dict[str, dict[str, float]] = {
    # Gemini 2.5 Flash
    "gemini/gemini-2.5-flash": {"input": 0.000075, "output": 0.0003},
    "gemini-2.5-flash": {"input": 0.000075, "output": 0.0003},
    # Claude Sonnet (various version suffixes)
    "anthropic/claude-sonnet-4-20250514": {"input": 0.003, "output": 0.015},
    "claude-sonnet-4-20250514": {"input": 0.003, "output": 0.015},
    "anthropic/claude-3-5-sonnet": {"input": 0.003, "output": 0.015},
    "claude-3-5-sonnet": {"input": 0.003, "output": 0.015},
    # DeepSeek Chat
    "deepseek/deepseek-chat": {"input": 0.00014, "output": 0.00028},
    "deepseek-chat": {"input": 0.00014, "output": 0.00028},
}

# Environment variable names to check for availability
_API_KEY_VARS = ["GEMINI_API_KEY", "ANTHROPIC_API_KEY", "DEEPSEEK_API_KEY"]
_PROVIDER_API_KEY_VARS = {
    "gemini": "GEMINI_API_KEY",
    "anthropic": "ANTHROPIC_API_KEY",
    "deepseek": "DEEPSEEK_API_KEY",
}


# ---------------------------------------------------------------------------
# Response dataclass
# ---------------------------------------------------------------------------
@dataclass
class LLMResponse:
    content: str                        # raw text response
    content_json: Optional[dict]        # parsed JSON if expect_json=True, else None
    model_used: str                     # which model actually responded
    usage: dict                         # {prompt_tokens, completion_tokens, total_tokens}
    success: bool
    error: Optional[str]


class LLMAllModelsFailedError(RuntimeError):
    """Raised when every configured LLM model attempt fails."""


# ---------------------------------------------------------------------------
# LLM Client
# ---------------------------------------------------------------------------
class LLMClient:
    """Stateless LLM client with model fallback chain and usage logging."""

    def __init__(self, settings_path: str = "config/settings.yaml"):
        try:
            from dotenv import load_dotenv
            load_dotenv()
        except ImportError:
            pass

        # Resolve through src.config so a fresh clone (no real settings.yaml)
        # falls back to the committed .example template instead of raising, and
        # a cloud deployment still fails loudly rather than running on it.
        from src.config import _resolve_config_file  # noqa: PLC0415

        with open(_resolve_config_file(Path(settings_path))) as f:
            settings = yaml.safe_load(f)

        llm_cfg = settings.get("llm", {})
        self._primary = llm_cfg.get("primary_model", "gemini/gemini-2.5-flash")
        self._fallbacks: list[str] = llm_cfg.get("fallback_models", [])
        self._temperature: float = float(llm_cfg.get("temperature", 0.7))
        self._max_tokens: int = int(llm_cfg.get("max_output_tokens", 4096))

        db_cfg = settings.get("database", {})
        # Deferred import — keep module-level imports light; llm_client intentionally
        # defers its duckdb import as well.  resolve_db_path honours UIS_DB_PATH so
        # the relative "data/unified.duckdb" from settings.yaml maps to the correct
        # absolute path on Cloud Run (/tmp/data/unified.duckdb).
        from src.database.connector import resolve_db_path as _resolve_db_path
        self._db_path: str = _resolve_db_path(db_cfg.get("path", "data/unified.duckdb"))

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def is_available(self) -> bool:
        """Return True if at least one LLM API key environment variable is set."""
        return any(os.environ.get(k) for k in _API_KEY_VARS)

    def complete(
        self,
        system_prompt: str,
        user_prompt: str,
        expect_json: bool = False,
        report_type: str = "unknown",
    ) -> LLMResponse:
        """
        Call the LLM with a fallback chain.

        Args:
            system_prompt: System-level instructions for the model.
            user_prompt: User turn content.
            expect_json: If True, attempt to parse the response as JSON using
                         json_repair before json.loads.
            report_type: Label stored in llm_usage ('brief', 'review', etc.).

        Returns:
            LLMResponse with success=True on the first model that responds.

        Raises:
            RuntimeError: If all models in the fallback chain fail.
        """
        import litellm  # deferred import — not available in test environments without it

        models_to_try = [self._primary] + self._fallbacks
        available_models = self._filter_models_with_available_keys(models_to_try)
        last_error: Optional[Exception] = None

        if not available_models:
            last_error = RuntimeError("No configured LLM models have available API keys")

        for model in available_models:
            try:
                raw = litellm.completion(
                    model=model,
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_prompt},
                    ],
                    temperature=self._temperature,
                    max_tokens=self._max_tokens,
                    timeout=120,
                )

                content_str: str = raw.choices[0].message.content or ""

                usage_obj = getattr(raw, "usage", None)
                prompt_tokens = int(getattr(usage_obj, "prompt_tokens", 0) or 0)
                completion_tokens = int(getattr(usage_obj, "completion_tokens", 0) or 0)
                total_tokens = int(getattr(usage_obj, "total_tokens", 0) or 0)
                usage_dict = {
                    "prompt_tokens": prompt_tokens,
                    "completion_tokens": completion_tokens,
                    "total_tokens": total_tokens,
                }

                content_json: Optional[dict] = None
                if expect_json:
                    content_json = _parse_json(content_str)

                result = LLMResponse(
                    content=content_str,
                    content_json=content_json,
                    model_used=model,
                    usage=usage_dict,
                    success=True,
                    error=None,
                )

            except Exception as exc:  # noqa: BLE001
                logger.warning("LLM model %s failed: %s", model, exc)
                last_error = exc
                continue

            # Successful response — log usage outside the try/except so logging
            # failures cannot be misinterpreted as model failures.
            try:
                self._log_usage(
                    report_type=report_type,
                    model_used=model,
                    prompt_tokens=prompt_tokens,
                    completion_tokens=completion_tokens,
                    total_tokens=total_tokens,
                    success=True,
                    error_message=None,
                )
            except Exception as log_exc:  # noqa: BLE001
                logger.warning("LLM usage logging failed (non-fatal): %s", log_exc)

            return result

        # All models failed — attempt to log, then raise
        error_msg = str(last_error)
        try:
            self._log_usage(
                report_type=report_type,
                model_used=self._primary,
                prompt_tokens=0,
                completion_tokens=0,
                total_tokens=0,
                success=False,
                error_message=error_msg,
            )
        except Exception as log_exc:  # noqa: BLE001
            logger.warning("LLM usage logging failed (non-fatal): %s", log_exc)
        raise LLMAllModelsFailedError(f"All LLM models failed. Last error: {last_error}")

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _estimate_cost(self, model: str, prompt_tokens: int, completion_tokens: int) -> float:
        """Estimate cost in USD using per-token pricing constants."""
        pricing = _PRICING.get(model)
        if pricing is None:
            # Try partial match (e.g. strip provider prefix)
            short = model.split("/")[-1]
            pricing = _PRICING.get(short)
        if pricing is None:
            return 0.0
        return (prompt_tokens / 1000.0) * pricing["input"] + (completion_tokens / 1000.0) * pricing["output"]

    def _log_usage(
        self,
        report_type: str,
        model_used: str,
        prompt_tokens: int,
        completion_tokens: int,
        total_tokens: int,
        success: bool,
        error_message: Optional[str],
    ) -> None:
        """Fire-and-forget usage logging to DuckDB llm_usage table. Never raises."""
        try:
            import duckdb  # noqa: PLC0415

            cost = self._estimate_cost(model_used, prompt_tokens, completion_tokens)

            conn = duckdb.connect(self._db_path)
            try:
                conn.execute(
                    """
                    INSERT INTO llm_usage
                        (report_type, model_used, prompt_tokens, completion_tokens,
                         total_tokens, cost_estimate_usd, success, error_message)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    [
                        report_type,
                        model_used,
                        prompt_tokens,
                        completion_tokens,
                        total_tokens,
                        cost,
                        success,
                        error_message,
                    ],
                )
            finally:
                conn.close()

        except Exception as exc:  # noqa: BLE001
            logger.warning("LLM usage logging failed (non-fatal): %s", exc)

    def _filter_models_with_available_keys(self, models: list[str]) -> list[str]:
        """Skip providers missing API keys when some other provider keys are available."""
        if not self.is_available():
            return models

        filtered: list[str] = []
        for model in models:
            env_var = self._api_key_var_for_model(model)
            if env_var and not os.environ.get(env_var):
                logger.info("Skipping LLM model %s because %s is not set", model, env_var)
                continue
            filtered.append(model)
        return filtered

    def _api_key_var_for_model(self, model: str) -> Optional[str]:
        provider, _, _name = model.partition("/")
        if not provider:
            return None
        return _PROVIDER_API_KEY_VARS.get(provider)


# ---------------------------------------------------------------------------
# JSON parsing helper
# ---------------------------------------------------------------------------

def _parse_json(text: str) -> Optional[dict]:
    """
    Parse JSON from LLM output.

    Strategy:
    1. Try json.loads on the raw text (fast path).
    2. Strip markdown code fences if present and retry.
    3. Use json_repair.repair_json() to fix common LLM JSON errors.
    4. Return None if all strategies fail.
    """
    # Fast path
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # Strip markdown fences
    stripped = text.strip()
    if stripped.startswith("```"):
        lines = stripped.splitlines()
        # Remove first line (```json or ```) and last line (```)
        inner = "\n".join(lines[1:-1]) if lines[-1].strip() == "```" else "\n".join(lines[1:])
        try:
            return json.loads(inner)
        except json.JSONDecodeError:
            pass
        stripped = inner

    # json_repair fallback
    try:
        from json_repair import repair_json  # noqa: PLC0415

        repaired = repair_json(stripped)
        return json.loads(repaired)
    except Exception:  # noqa: BLE001
        pass

    logger.warning("Could not parse LLM response as JSON")
    return None
