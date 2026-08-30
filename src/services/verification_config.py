"""Typed loader for config/verification.yaml (PRD 2026-07-07 process-verification
program, Cross-Cutting Requirement 2: config over constants).

Every field has a documented default that mirrors the committed
config/verification.yaml exactly. A missing config file degrades to those
defaults rather than raising — this feature ships flag-off by default
(process_verification.enabled=false) and must never block server startup on a
missing config file.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

import yaml

from src.config import _resolve_config_file

logger = logging.getLogger(__name__)

DEFAULT_CONFIG_PATH = "config/verification.yaml"

# Buckets that can be assigned by the bucket_map (compliance/ratio/liquidity).
# 'value' is never an entry in bucket_map — it is the fallback for anything
# unmatched (PRD F1.1: "value = everything else").
_NON_VALUE_BUCKETS = ("compliance", "ratio", "liquidity")
VALUE_BUCKET = "value"


@dataclass(frozen=True)
class ProcessVerificationSection:
    enabled: bool = False
    outcome_window_days: int = 180


@dataclass(frozen=True)
class ValueTrapSection:
    trigger_threshold_pct: float = -25.0
    escalation_step_pp: float = 10.0
    overdue_alert_days: int = 14


@dataclass(frozen=True)
class StalenessSection:
    fast_hours: int = 24
    # Asset-level freshness gate uses days, not hours: a 1-day (24h) threshold
    # would flag any CN fund as stale on Monday morning (T+1 settlement) and on
    # market holidays.  3 calendar days covers weekends while still catching
    # genuinely broken feeds. See src/services/freshness.py for the full rationale.
    fast_days: int = 3
    slow_days: int = 7
    # Cash-like assets are exempt from the value-trap scan (never deferred).
    # Code-level defaults live in src/services/freshness.py; these config lists
    # allow per-portfolio extensions without a code change.
    cash_like_id_prefixes: tuple[str, ...] = field(default_factory=tuple)
    cash_like_taxonomy_classes: tuple[str, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class ContrarianSection:
    drawdown_window_trading_days: int = 10
    drawdown_threshold_pct: float = 5.0
    manual_alert_rate_pct: float = 30.0
    manual_alert_monthly_count: int = 3


@dataclass(frozen=True)
class NorthStarSection:
    """F3 North Star panel thresholds (PRD 2026-07-07, Batch B6)."""
    target_net_worth_cny: float = 20_000_000.0
    tim_trailing_months: int = 24
    tim_band_pp: float = 10.0
    glide_horizons_years: tuple[int, ...] = (10, 15, 20)


@dataclass(frozen=True)
class BalanceSheetSection:
    """Program OSR WS-5b: rebalanceable_filter.py's non-rebalanceable-history
    substring markers used to be a hardcoded tuple that included one of the
    owner's real product names. The generic markers (real estate/property/
    insurance/ins + the Chinese equivalents) stay as code defaults — they're
    category words, not identifying — but a self-hoster's own product-name
    columns need their own extension, the same cash_like_id_prefixes idiom
    StalenessSection already uses. Additive only: this list is UNIONed onto
    the code defaults in rebalanceable_filter.py, never replaces them.
    """
    non_rebalanceable_history_markers: tuple[str, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class BucketMapEntry:
    asset_pattern: str
    actions: tuple[str, ...]


def _default_bucket_map() -> dict[str, tuple[BucketMapEntry, ...]]:
    return {
        "compliance": (
            BucketMapEntry("RSU_AMZN", ("sell",)),
            BucketMapEntry("900009", ("sell",)),
        ),
        "ratio": (
            BucketMapEntry("GOLD", ("buy", "sell")),
            BucketMapEntry("ALTS_Paper_Gold", ("buy", "sell")),
            BucketMapEntry("IBIT", ("buy", "sell")),
            BucketMapEntry("FBTC", ("buy", "sell")),
        ),
        "liquidity": (
            BucketMapEntry("SGOV", ("buy", "sell")),
        ),
    }


@dataclass(frozen=True)
class VerificationConfig:
    process_verification: ProcessVerificationSection = field(default_factory=ProcessVerificationSection)
    value_trap: ValueTrapSection = field(default_factory=ValueTrapSection)
    staleness: StalenessSection = field(default_factory=StalenessSection)
    contrarian: ContrarianSection = field(default_factory=ContrarianSection)
    north_star: NorthStarSection = field(default_factory=NorthStarSection)
    balance_sheet: BalanceSheetSection = field(default_factory=BalanceSheetSection)
    bucket_map: dict[str, tuple[BucketMapEntry, ...]] = field(default_factory=_default_bucket_map)


# Module-level cache, keyed by resolved config path string. Small, load-once
# config; explicit clear_cache() hook exists for tests that swap the file.
_cache: dict[str, VerificationConfig] = {}


def _parse_bucket_map(raw: Optional[dict[str, Any]]) -> dict[str, tuple[BucketMapEntry, ...]]:
    if not raw:
        return _default_bucket_map()
    parsed: dict[str, tuple[BucketMapEntry, ...]] = {}
    for bucket in _NON_VALUE_BUCKETS:
        entries = raw.get(bucket) or []
        parsed[bucket] = tuple(
            BucketMapEntry(
                asset_pattern=str(entry["asset_pattern"]),
                actions=tuple(str(a).lower() for a in entry.get("actions", [])),
            )
            for entry in entries
        )
    return parsed


def load_verification_config(
    config_path: str = DEFAULT_CONFIG_PATH, *, force_reload: bool = False
) -> VerificationConfig:
    """Load config/verification.yaml into a typed, immutable VerificationConfig.

    Cached by resolved path after the first successful load. Pass
    force_reload=True (tests) to bypass the cache and re-read from disk.
    Missing file falls back to the committed verification.example.yaml
    template (src.config._resolve_config_file — Program OSR WS-4b); if
    neither exists, falls back further to the documented dataclass
    defaults (never raises).
    """
    resolved = str(Path(config_path))
    if not force_reload and resolved in _cache:
        return _cache[resolved]

    try:
        config_file = _resolve_config_file(Path(config_path))
    except FileNotFoundError:
        logger.warning(
            "verification.yaml not found at %s (no .example template either) — "
            "using documented defaults", config_path,
        )
        cfg = VerificationConfig()
        _cache[resolved] = cfg
        return cfg

    with open(config_file, "r") as f:
        raw = yaml.safe_load(f) or {}

    pv_raw = raw.get("process_verification") or {}
    vt_raw = raw.get("value_trap") or {}
    st_raw = raw.get("staleness") or {}
    ct_raw = raw.get("contrarian") or {}
    ns_raw = raw.get("north_star") or {}
    bs_raw = raw.get("balance_sheet") or {}

    cfg = VerificationConfig(
        process_verification=ProcessVerificationSection(
            enabled=bool(pv_raw.get("enabled", False)),
            outcome_window_days=int(pv_raw.get("outcome_window_days", 180)),
        ),
        value_trap=ValueTrapSection(
            trigger_threshold_pct=float(vt_raw.get("trigger_threshold_pct", -25.0)),
            escalation_step_pp=float(vt_raw.get("escalation_step_pp", 10.0)),
            overdue_alert_days=int(vt_raw.get("overdue_alert_days", 14)),
        ),
        staleness=StalenessSection(
            fast_hours=int(st_raw.get("fast_hours", 24)),
            fast_days=int(st_raw.get("fast_days", 3)),
            slow_days=int(st_raw.get("slow_days", 7)),
            cash_like_id_prefixes=tuple(
                str(p) for p in (st_raw.get("cash_like_id_prefixes") or [])
            ),
            cash_like_taxonomy_classes=tuple(
                str(c) for c in (st_raw.get("cash_like_taxonomy_classes") or [])
            ),
        ),
        contrarian=ContrarianSection(
            drawdown_window_trading_days=int(ct_raw.get("drawdown_window_trading_days", 10)),
            drawdown_threshold_pct=float(ct_raw.get("drawdown_threshold_pct", 5.0)),
            manual_alert_rate_pct=float(ct_raw.get("manual_alert_rate_pct", 30.0)),
            manual_alert_monthly_count=int(ct_raw.get("manual_alert_monthly_count", 3)),
        ),
        north_star=NorthStarSection(
            target_net_worth_cny=float(ns_raw.get("target_net_worth_cny", 20_000_000.0)),
            tim_trailing_months=int(ns_raw.get("tim_trailing_months", 24)),
            tim_band_pp=float(ns_raw.get("tim_band_pp", 10.0)),
            glide_horizons_years=tuple(
                int(y) for y in ns_raw.get("glide_horizons_years", (10, 15, 20))
            ),
        ),
        balance_sheet=BalanceSheetSection(
            non_rebalanceable_history_markers=tuple(
                str(m) for m in (bs_raw.get("non_rebalanceable_history_markers") or [])
            ),
        ),
        bucket_map=_parse_bucket_map(raw.get("bucket_map")),
    )
    _cache[resolved] = cfg
    return cfg


def clear_cache() -> None:
    """Test hook: clear the module-level config cache so a subsequent
    load_verification_config() call re-reads from disk (or re-applies defaults)."""
    _cache.clear()
