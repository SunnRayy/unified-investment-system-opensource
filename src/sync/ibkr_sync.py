"""IBKR sync module — reads IBKR Flex Query CSV files and syncs to Huinsight.

Config-driven engine (Workstream C1 — NON-AUTHORITATIVE reader).
Format validation runs inside sync_config_source via the identity.validator
field in ibkr.yaml.
"""
from pathlib import Path
from typing import Dict, Any
import pandas as pd
import logging

from src.sources.config_driven_reader import sync_config_source
from src.sources.reader_config import load_reader_config

logger = logging.getLogger(__name__)

# Path to the ibkr reader YAML config
_IBKR_YAML = Path(__file__).parent.parent.parent / "config" / "readers" / "ibkr.yaml"


def sync_ibkr(
    config: Dict[str, Any],
    extra_metadata: "Dict[str, Any] | None" = None,
) -> Dict[str, pd.DataFrame]:
    """Sync IBKR Flex Query data to Huinsight format.

    Args:
        config: Full config dict with source_registry.ibkr section
        extra_metadata: forwarded to sync_config_source — ADR-023 WS-C: the
            orchestrator passes {"schwab_symbol_norm": ...} here (IBKR is
            co-authority with Schwab and reuses the same symbol normalizer
            function — see src.sources.reader_hooks.ibkr_holdings_from_flex /
            ibkr_transactions_from_flex).

    Returns:
        Dict with 'holdings' and 'transactions' DataFrames in Huinsight schema
    """
    ibkr_config = config.get('source_registry', {}).get('ibkr', {})

    if not ibkr_config.get('enabled', False):
        logger.info("IBKR sync disabled")
        return {
            'holdings': pd.DataFrame(),
            'transactions': pd.DataFrame(),
        }

    logger.info("IBKR sync: using config-driven engine")
    return sync_config_source(config, load_reader_config(_IBKR_YAML), extra_metadata=extra_metadata)
