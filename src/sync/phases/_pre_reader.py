import logging

from src.database.connector import DatabaseConnector
from src.sync.phases._common import (
    READER_ID_MIGRATION_KEY,
    LEGACY_PREFIX_RENAMES,
    _log_sync_event,
)

logger = logging.getLogger(__name__)


def _run_reader_id_migration_once(connector: DatabaseConnector) -> bool:
    already_done = connector.execute(
        """
        SELECT COUNT(*) FROM sync_audit_logs
        WHERE source_system = 'Migration'
          AND target_table = 'canonical_id'
          AND record_key = ?
        """,
        (READER_ID_MIGRATION_KEY,),
    ).fetchone()[0]
    if already_done:
        return False

    statements = [
        "UPDATE holdings SET asset_id = REPLACE(asset_id, 'Ins_', 'INS_') WHERE asset_id LIKE 'Ins_%'",
        "UPDATE transactions SET asset_id = REPLACE(asset_id, 'Ins_', 'INS_') WHERE asset_id LIKE 'Ins_%'",
        "UPDATE asset_registry SET canonical_id = REPLACE(canonical_id, 'Ins_', 'INS_') WHERE canonical_id LIKE 'Ins_%'",
        "UPDATE asset_source_mappings SET canonical_id = REPLACE(canonical_id, 'Ins_', 'INS_') WHERE canonical_id LIKE 'Ins_%'",
        "UPDATE holdings SET asset_id = REPLACE(asset_id, 'RSU_RSU_', 'RSU_') WHERE asset_id LIKE 'RSU_RSU_%'",
        "UPDATE transactions SET asset_id = REPLACE(asset_id, 'RSU_RSU_', 'RSU_') WHERE asset_id LIKE 'RSU_RSU_%'",
        "UPDATE asset_registry SET canonical_id = REPLACE(canonical_id, 'RSU_RSU_', 'RSU_') WHERE canonical_id LIKE 'RSU_RSU_%'",
        "UPDATE asset_source_mappings SET canonical_id = REPLACE(canonical_id, 'RSU_RSU_', 'RSU_') WHERE canonical_id LIKE 'RSU_RSU_%'",
    ]
    for statement in statements:
        connector.execute(statement)

    _log_sync_event(
        connector=connector,
        source_system="Migration",
        target_table="canonical_id",
        record_key=READER_ID_MIGRATION_KEY,
        source_value={"applied": True, "statements": len(statements)},
        conflict_type="prefix_remap",
        resolution="applied",
        notes="Normalized Ins_/RSU_RSU_ legacy prefixes for reader insertion.",
    )
    return True


def _normalize_legacy_prefixes(connector: DatabaseConnector) -> int:
    """Normalize legacy canonical ID prefixes on every sync run.

    This catches rows re-inserted by legacy PIS syncs:
    - Ins_* -> INS_*
    - RSU_RSU_* -> RSU_*
    """
    affected = 0

    for old_prefix, new_prefix in LEGACY_PREFIX_RENAMES:
        # SQL fragments are built only from internal constant prefixes, not user input.
        old_holdings = connector.execute(
            f"SELECT COUNT(*) FROM holdings WHERE asset_id LIKE '{old_prefix}%'",
        ).fetchone()[0]
        old_transactions = connector.execute(
            f"SELECT COUNT(*) FROM transactions WHERE asset_id LIKE '{old_prefix}%'",
        ).fetchone()[0]

        # Avoid unique-key conflicts on (snapshot_date, asset_id, source_system) during rename.
        connector.execute(
            f"""
            DELETE FROM holdings
            WHERE asset_id LIKE '{old_prefix}%'
              AND (
                snapshot_date,
                REPLACE(asset_id, '{old_prefix}', '{new_prefix}'),
                source_system
              ) IN (
                SELECT snapshot_date, asset_id, source_system
                FROM holdings
                WHERE asset_id LIKE '{new_prefix}%'
              )
            """,
        )
        connector.execute(
            f"""
            UPDATE holdings
            SET asset_id = REPLACE(asset_id, '{old_prefix}', '{new_prefix}')
            WHERE asset_id LIKE '{old_prefix}%'
            """,
        )

        connector.execute(
            f"""
            UPDATE transactions
            SET asset_id = REPLACE(asset_id, '{old_prefix}', '{new_prefix}')
            WHERE asset_id LIKE '{old_prefix}%'
            """,
        )

        try:
            old_registry = connector.execute(
                f"SELECT COUNT(*) FROM asset_registry WHERE canonical_id LIKE '{old_prefix}%'",
            ).fetchone()[0]
            connector.execute(
                f"""
                DELETE FROM asset_registry
                WHERE canonical_id LIKE '{old_prefix}%'
                  AND REPLACE(canonical_id, '{old_prefix}', '{new_prefix}') IN (
                    SELECT canonical_id
                    FROM asset_registry
                    WHERE canonical_id LIKE '{new_prefix}%'
                  )
                """,
            )
            connector.execute(
                f"""
                UPDATE asset_registry
                SET canonical_id = REPLACE(canonical_id, '{old_prefix}', '{new_prefix}')
                WHERE canonical_id LIKE '{old_prefix}%'
                """,
            )
        except Exception:
            old_registry = 0

        try:
            old_mappings = connector.execute(
                f"SELECT COUNT(*) FROM asset_source_mappings WHERE canonical_id LIKE '{old_prefix}%'",
            ).fetchone()[0]
            connector.execute(
                f"""
                UPDATE asset_source_mappings
                SET canonical_id = REPLACE(canonical_id, '{old_prefix}', '{new_prefix}')
                WHERE canonical_id LIKE '{old_prefix}%'
                """,
            )
        except Exception:
            old_mappings = 0

        affected += old_holdings + old_transactions + old_registry + old_mappings

    return affected
