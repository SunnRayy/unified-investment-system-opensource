"""Gold sync module - reads Gold Excel and returns DataFrames.

Config-driven engine only (B5 — legacy reader/transformer deleted).
Format validation runs inside sync_config_source via the identity.validator
field in gold.yaml.
"""
from pathlib import Path
from typing import Dict, Any
import pandas as pd
import logging

from src.sources.base import READ_STATUS_DISABLED, READ_STATUS_KEY
from src.sources.config_driven_reader import sync_config_source
from src.sources.reader_config import load_reader_config

logger = logging.getLogger(__name__)

# Path to the gold reader YAML config
_GOLD_READER_YAML = Path(__file__).parent.parent.parent / "config" / "readers" / "gold.yaml"


def sync_gold(
    config: Dict[str, Any],
    extra_metadata: "Dict[str, Any] | None" = None,
) -> Dict[str, pd.DataFrame]:
    """Sync Gold Excel data to Huinsight format.

    Args:
        extra_metadata: forwarded to sync_config_source — ADR-023 WS-B: the
            orchestrator passes {"id_field_maps_override": ...} here (the
            merged UI-managed asset_name/account id_field_map, from
            src.services.reader_mappings.load_id_field_maps).
    """
    type_config = config.get('source_registry', {}).get('gold', {})

    if not type_config.get('enabled', False):
        logger.info("Gold sync disabled")
        return {
            'holdings': pd.DataFrame(),
            'transactions': pd.DataFrame(),
            READ_STATUS_KEY: READ_STATUS_DISABLED,
        }

    logger.info("Gold sync: using config-driven engine")
    return sync_config_source(config, load_reader_config(_GOLD_READER_YAML), extra_metadata=extra_metadata)
