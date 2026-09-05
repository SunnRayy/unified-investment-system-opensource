"""Schwab sync module - reads Schwab CSV files and syncs to Huinsight.

Config-driven engine only (B5 — legacy reader/transformer deleted).
Format validation runs inside sync_config_source via the identity.validator
field in schwab.yaml.
"""
from pathlib import Path
from typing import Dict, Any
import pandas as pd
import logging

from src.sources.config_driven_reader import sync_config_source
from src.sources.reader_config import load_reader_config

logger = logging.getLogger(__name__)

# Path to the schwab reader YAML config
_SCHWAB_YAML = Path(__file__).parent.parent.parent / "config" / "readers" / "schwab.yaml"


def sync_schwab(
    config: Dict[str, Any],
    extra_metadata: "Dict[str, Any] | None" = None,
) -> Dict[str, pd.DataFrame]:
    """Sync Schwab CSV data to Huinsight format.

    Args:
        config: Full config dict with source_registry.schwab section
        extra_metadata: forwarded to sync_config_source — ADR-023 WS-C: the
            orchestrator passes the merged UI-managed known_etf/symbol_norm/
            action_map vocabularies here (src.services.reader_mappings.
            load_reader_mappings), consumed by schwab_holdings_from_csv /
            schwab_transactions_from_csv at transform time.

    Returns:
        Dict with 'holdings' and 'transactions' DataFrames in Huinsight schema
    """
    schwab_config = config.get('source_registry', {}).get('schwab', {})

    if not schwab_config.get('enabled', False):
        logger.info("Schwab sync disabled")
        return {
            'holdings': pd.DataFrame(),
            'transactions': pd.DataFrame(),
        }

    logger.info("Schwab sync: using config-driven engine")
    return sync_config_source(config, load_reader_config(_SCHWAB_YAML), extra_metadata=extra_metadata)
