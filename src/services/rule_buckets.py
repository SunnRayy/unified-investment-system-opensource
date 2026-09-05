"""Bucket classification for process-based trade verification (F1.1, D1).

Per-trade `trade_logs.rule_bucket` is authoritative once explicitly set; this
module supplies the *default* classification from config/verification.yaml's
bucket_map, used by the backfill script (scripts/backfill_rule_buckets.py) and
by any later code that needs to resolve a bucket for an asset that has not yet
been explicitly tagged (F2 holdings-level exclusion, F4.5 dashboard
suppression).

Classification is bucket-priority ordered (compliance, ratio, liquidity) with
'value' as the catch-all default — PRD F1.1: "value = everything else". This
is why an RSU_AMZN *buy* (vest) is 'value' while an RSU_AMZN *sell* is
'compliance': the bucket_map's compliance entry for RSU_AMZN only lists the
'sell' action, so a buy simply does not match any non-value entry and falls
through to the default.
"""
from __future__ import annotations

from typing import Optional

from src.services.verification_config import (
    VALUE_BUCKET,
    VerificationConfig,
    load_verification_config,
)

# Priority order when multiple bucket_map sections could theoretically match
# the same asset_pattern — compliance and ratio/liquidity rules are meant to
# be mutually exclusive by construction, but an explicit order keeps behavior
# deterministic if a future config edit introduces overlap.
_BUCKET_PRIORITY = ("compliance", "ratio", "liquidity")


def _matches_pattern(asset_id: str, pattern: str) -> bool:
    """Case-insensitive substring match of `pattern` against `asset_id`."""
    if not asset_id or not pattern:
        return False
    return pattern.lower() in asset_id.lower()


def classify_trade_bucket(
    asset_id: str, action: str, cfg: Optional[VerificationConfig] = None
) -> str:
    """Classify a single trade (asset_id, action) into its rule_bucket.

    Returns 'compliance' | 'ratio' | 'liquidity' | 'value'. A bucket_map entry
    matches only when BOTH its asset_pattern is a case-insensitive substring of
    asset_id AND `action` (case-insensitive) is in that entry's actions list.
    Unmatched trades default to 'value'.
    """
    if cfg is None:
        cfg = load_verification_config()
    action_lower = (action or "").strip().lower()
    for bucket in _BUCKET_PRIORITY:
        for entry in cfg.bucket_map.get(bucket, ()):
            if action_lower in entry.actions and _matches_pattern(asset_id, entry.asset_pattern):
                return bucket
    return VALUE_BUCKET


def classify_asset_bucket(
    asset_id: str, cfg: Optional[VerificationConfig] = None
) -> Optional[str]:
    """Classify an asset independent of action, for exclusion/suppression use.

    Returns the bucket if ANY bucket_map entry's asset_pattern matches the
    asset, regardless of that entry's actions list — used by F2's holdings-level
    exclusion and F4.5's dashboard suppression, which key off the asset, not a
    single trade. Assets matching no compliance/ratio/liquidity pattern return
    'value' (the well-defined default bucket) — this function never returns
    None in practice, but the Optional[str] signature documents that 'value' is
    itself a real classification, not merely "no match found".
    """
    if cfg is None:
        cfg = load_verification_config()
    for bucket in _BUCKET_PRIORITY:
        for entry in cfg.bucket_map.get(bucket, ()):
            if _matches_pattern(asset_id, entry.asset_pattern):
                return bucket
    return VALUE_BUCKET
