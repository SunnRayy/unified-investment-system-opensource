
"""
Holdings Aggregator Module
Responsible for aggregating holdings from multiple sources and applying conflict resolution rules.
"""

import logging
from typing import List, Dict
from datetime import date

from src.database.connector import DatabaseConnector

logger = logging.getLogger(__name__)

class HoldingsAggregator:
    def __init__(self, authority_resolver):
        """
        Initialize Aggregator with an AuthorityResolver instance.
        
        Args:
            authority_resolver: Instance of AuthorityResolver
        """
        self.resolver = authority_resolver

    def apply_authority_rules(self, connector: DatabaseConnector, snapshot_date: date) -> None:
        """
        Apply authority rules to holdings for a specific date.
        Updates is_shadow and authority_source columns for all holdings on that date.

        Authority semantics (C3b — authority-set):
          - ``authority_source`` is stamped with the PRIMARY authority (first declared,
            first available) — unchanged from pre-C3b.
          - ``is_shadow`` is now set by membership in the FULL authority set.  Any
            source_system NOT IN the resolved set is shadowed.  PIS sources are never
            members of any authority set (source_authority.yaml has no PIS entries),
            so they are always shadowed — identical outcome to the legacy PIS branch
            that existed before C3b.

        Args:
            connector: Database connector
            snapshot_date: Date to process
        """
        try:
            # 1. Fetch all holdings for date
            query = """
                SELECT asset_id, source_system
                FROM holdings
                WHERE snapshot_date = ?
            """
            rows = connector.execute(query, (snapshot_date,)).fetchall()

            if not rows:
                logger.info(f"No holdings found for {snapshot_date}")
                return

            # 2. Group by asset_id
            assets: Dict[str, List[str]] = {}
            for row in rows:
                asset_id = row[0]
                source = row[1]
                if asset_id not in assets:
                    assets[asset_id] = []
                assets[asset_id].append(source)

            # 3. Resolve and Update
            update_count = 0
            for asset_id, sources in assets.items():
                # primary → used for authority_source stamping (unchanged behaviour)
                primary = self.resolver.resolve(asset_id, available_sources=sources)
                # resolved_set → used for is_shadow membership check (C3b)
                resolved_set = self.resolver.resolve_authorities(asset_id, available_sources=sources)

                if not resolved_set or not primary:
                    logger.warning(f"No authority found for {asset_id}. Skipping authority update.")
                    continue

                # Build a parameterised NOT IN clause from the resolved set.
                # Sort for deterministic SQL/params; use ? placeholders (not string interpolation).
                sorted_set = sorted(resolved_set)
                placeholders = ", ".join("?" * len(sorted_set))

                # Note: PIS sources ('PIS', 'PIS_SQLite', etc.) are never members of any
                # authority set in source_authority.yaml, so they are always covered by the
                # NOT IN branch and remain shadowed — same outcome as the old explicit PIS
                # branch, now unified.
                update_query = f"""
                    UPDATE holdings
                    SET
                        authority_source = ?,
                        is_shadow = CASE
                            -- Already-shadow rows stay shadow (e.g. stale older snapshot)
                            WHEN is_shadow = TRUE THEN TRUE
                            -- Consolidated rows are the synthetic authority output of C3.4
                            -- (_consolidate_coauthority_holdings). They are never members of
                            -- any reader authority set in source_authority.yaml, so without
                            -- this guard the ELSE branch would shadow them immediately after
                            -- P4 writes them. We preserve them as active unconditionally.
                            WHEN source_system = 'Consolidated' THEN FALSE
                            -- Shadow anything outside the authority set (PIS, non-authority sources)
                            ELSE (source_system NOT IN ({placeholders}))
                        END
                    WHERE snapshot_date = ?
                      AND asset_id = ?
                """
                # Params order: primary, *sorted_set, snapshot_date, asset_id
                connector.execute(update_query, (primary, *sorted_set, snapshot_date, asset_id))
                update_count += 1

            logger.info(f"Applied authority rules to {update_count} assets for {snapshot_date}")

        except Exception as e:
            logger.error(f"Error applying authority rules: {e}")
            raise
