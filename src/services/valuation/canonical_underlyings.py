"""Canonical-underlying signal dedup for the valuation serving path.

PRD 2026-07-07 §F4.2: VOO and S&P500 emitted conflicting valuation signals
(HIGH vs FAIR/69%) for the same underlying exposure because each ticker is
collected and scored as an independent series, even though an index ETF
tracks its benchmark ~1:1. This module maps an instrument ticker to a
canonical underlying id (config/canonical_underlyings.yaml) and, when the
canonical underlying's own signal series exists in the current result set,
overrides the instrument's *displayed* signal fields with the canonical
series' values — one underlying, one signal.

This is a read/serving-path transform only. It never touches how raw series
are ingested or stored (both VOO's own series and the S&P500 tracked-index
series remain in valuation_snapshots / valuation_history untouched).
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger(__name__)

DEFAULT_CONFIG_PATH = "config/canonical_underlyings.yaml"

# Fallback seed — used only if the config file is missing/unreadable, so the
# dedup never silently goes fully dark just because a file didn't ship.
_DEFAULT_CANONICAL_UNDERLYINGS: dict[str, str] = {
    "VOO": "SP500",
    "IVV": "SP500",
    "SPY": "SP500",
}
_DEFAULT_CANONICAL_SERIES: dict[str, str] = {
    "SP500": "S&P500",
}

# Fields mirrored from the canonical source-series row onto every mapped
# instrument row. Only signal-classification fields are copied — the
# instrument's own raw metrics (pe_ttm, pb_ratio, etc.) are left untouched
# per the "do not change ingestion" constraint.
_SIGNAL_FIELDS = (
    "valuation_signal",
    "signal_basis",
    "percentile_value",
    "percentile_metric",
    "pct_years",
)


def load_canonical_underlyings(
    config_path: str = DEFAULT_CONFIG_PATH,
) -> tuple[dict[str, str], dict[str, str]]:
    """Load (instrument_ticker -> canonical_id, canonical_id -> source_ticker) maps.

    Falls back to the built-in seed defaults if the file is missing or
    malformed — the dedup feature must never block the /valuation/snapshot
    endpoint from serving a response.
    """
    path = Path(config_path)
    if not path.is_file():
        logger.warning(
            "canonical_underlyings config not found at %s; using built-in defaults",
            config_path,
        )
        return dict(_DEFAULT_CANONICAL_UNDERLYINGS), dict(_DEFAULT_CANONICAL_SERIES)

    try:
        with path.open("r", encoding="utf-8") as f:
            raw = yaml.safe_load(f) or {}
        underlyings = {
            str(k): str(v) for k, v in (raw.get("canonical_underlyings") or {}).items()
        }
        series = {str(k): str(v) for k, v in (raw.get("canonical_series") or {}).items()}
        if not underlyings:
            raise ValueError("canonical_underlyings section is empty")
        return underlyings, series
    except Exception as exc:
        logger.warning(
            "Failed to parse %s (%s); using built-in defaults", config_path, exc
        )
        return dict(_DEFAULT_CANONICAL_UNDERLYINGS), dict(_DEFAULT_CANONICAL_SERIES)


def apply_canonical_signal_dedup(
    rows: list[dict[str, Any]],
    config_path: str = DEFAULT_CONFIG_PATH,
) -> list[dict[str, Any]]:
    """Return *rows* with mapped instruments' signal fields mirrored from their
    canonical underlying's source series (when that source series is present
    in *rows*). Adds `canonical_underlying` and `signal_source_series` to
    every row (None when the ticker has no canonical mapping).

    Never raises — on any lookup failure the row is returned unmodified with
    both new fields set to None, so a config problem degrades gracefully
    rather than breaking the endpoint (Rule 12: no silent 200 with an
    exception swallowed *without* a safe fallback — this fallback is the
    documented safe default, not error-hiding).
    """
    underlying_map, series_map = load_canonical_underlyings(config_path)

    rows_by_ticker: dict[str, dict[str, Any]] = {}
    for row in rows:
        ticker = row.get("ticker")
        if ticker is not None:
            rows_by_ticker[str(ticker)] = row

    for row in rows:
        row.setdefault("canonical_underlying", None)
        row.setdefault("signal_source_series", None)

        ticker = row.get("ticker")
        if ticker is None:
            continue
        canonical_id = underlying_map.get(str(ticker))
        if not canonical_id:
            continue

        row["canonical_underlying"] = canonical_id
        source_ticker = series_map.get(canonical_id)
        if not source_ticker or source_ticker == ticker:
            continue

        source_row = rows_by_ticker.get(source_ticker)
        if source_row is None:
            # Canonical source series hasn't been collected yet — leave the
            # instrument's own (possibly stale/independent) signal as-is.
            continue

        row["signal_source_series"] = source_ticker
        for field_name in _SIGNAL_FIELDS:
            if field_name in source_row:
                row[field_name] = source_row[field_name]

    return rows
