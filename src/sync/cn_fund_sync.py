"""CN Fund sync module - reads CN Fund Excel and syncs to Huinsight.

Config-driven engine only (B5 — legacy reader/transformer deleted).
The pre_read_hook in cn_fund.yaml runs process_all inside sync_config_source.
"""
from pathlib import Path
from typing import Dict, Any
import pandas as pd
import logging

from src.sources.config_driven_reader import sync_config_source
from src.sources.reader_config import load_reader_config

logger = logging.getLogger(__name__)

# Path to the CN Fund reader YAML config
_CN_FUND_READER_YAML = Path(__file__).parent.parent.parent / "config" / "readers" / "cn_fund.yaml"


def sync_cn_fund(
    config: Dict[str, Any],
    extra_metadata: "Dict[str, Any] | None" = None,
) -> Dict[str, pd.DataFrame]:
    """Sync CN Fund Excel data to Huinsight format.

    Args:
        extra_metadata: forwarded to sync_config_source — ADR-023 WS-C: the
            orchestrator passes {"cn_fund_type_map": ...} here (the merged
            UI-managed 操作类型 -> transaction_type vocabulary, from
            src.services.reader_mappings.load_reader_mappings), consumed by
            cn_fund_transactions_from_sheet at transform time.
    """
    cn_fund_config = config.get('source_registry', {}).get('cn_fund', {})

    if not cn_fund_config.get('enabled', False):
        logger.info("CN Fund sync disabled")
        return {'holdings': pd.DataFrame(), 'transactions': pd.DataFrame()}

    logger.info("CN Fund sync: using config-driven engine")
    return sync_config_source(config, load_reader_config(_CN_FUND_READER_YAML), extra_metadata=extra_metadata)
