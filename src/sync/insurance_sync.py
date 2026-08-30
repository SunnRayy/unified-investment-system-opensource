"""Insurance sync module - reads Insurance Excel and returns DataFrames.

Config-driven engine only (B5 — legacy reader/transformer deleted).
Format validation runs inside sync_config_source via the identity.validator
field in insurance.yaml.
"""
from pathlib import Path
from typing import Dict, Any
import pandas as pd
import logging

from src.sources.base import READ_STATUS_DISABLED, READ_STATUS_KEY
from src.sources.config_driven_reader import sync_config_source
from src.sources.reader_config import load_reader_config

logger = logging.getLogger(__name__)

# Path to the insurance reader YAML config
_INSURANCE_READER_YAML = (
    Path(__file__).parent.parent.parent / "config" / "readers" / "insurance.yaml"
)


def sync_insurance(
    config: Dict[str, Any],
    extra_metadata: "Dict[str, Any] | None" = None,
) -> Dict[str, pd.DataFrame]:
    """Sync Insurance Excel data to Huinsight format.

    Args:
        extra_metadata: forwarded to sync_config_source — ADR-023 WS-B: the
            orchestrator passes {"id_field_maps_override": ...} here (the
            merged UI-managed product_name/policy_name id_field_map, from
            src.services.reader_mappings.load_id_field_maps — empty by
            default since insurance.yaml declares no id_field_maps today).
    """
    type_config = config.get('source_registry', {}).get('insurance', {})

    if not type_config.get('enabled', False):
        logger.info("Insurance sync disabled")
        return {
            'holdings': pd.DataFrame(),
            'transactions': pd.DataFrame(),
            READ_STATUS_KEY: READ_STATUS_DISABLED,
        }

    logger.info("Insurance sync: using config-driven engine")
    return sync_config_source(config, load_reader_config(_INSURANCE_READER_YAML), extra_metadata=extra_metadata)
