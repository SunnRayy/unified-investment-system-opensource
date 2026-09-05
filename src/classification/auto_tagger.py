"""AutoTagger — priority cascade classification engine."""
import re
import logging
from typing import Optional, List, Tuple
from src.database.connector import DatabaseConnector
from src.classification.models import ClassificationResult

logger = logging.getLogger(__name__)


class AutoTagger:
    """Classifies assets using a priority cascade: exact_id → exact_name → regex → unclassified."""
    
    def __init__(self, connector: DatabaseConnector):
        self.connector = connector

    def classify(self, asset_id: str, asset_name: str) -> ClassificationResult:
        """Classify a single asset using priority cascade.
        
        Priority:
        1. exact_id match (priority=10): classification_rules WHERE rule_type='exact_id' AND pattern=asset_id
        2. exact_name match (priority=20): classification_rules WHERE rule_type='exact_name' AND pattern=asset_name
        3. regex match (priority=50+, ordered): Iterate through regex rules in priority order
        4. id_regex match: same, but against the asset ID rather than the name
        5. No match → class_id=NULL, method="auto_unclassified"
        """
        # Step 1: Exact ID match
        result = self._try_exact_id(asset_id)
        if result:
            return result

        # Step 2: Exact name match
        result = self._try_exact_name(asset_name)
        if result:
            result.asset_id = asset_id  # Fill in the asset_id
            return result

        # Step 3: Regex match (ordered by priority)
        result = self._try_regex(asset_name)
        if result:
            result.asset_id = asset_id  # Fill in the asset_id
            return result

        # Step 4: Asset-ID prefix fallback.
        #
        # Steps 1–3 all match on things a *person* entered: specific asset IDs
        # they own, or display names they recognise. A database that has never
        # been curated has none of them, so before this step a fresh install
        # classified almost nothing — every US ticker, every fund code and every
        # insurance policy fell through to Unclassified, and the allocation and
        # attribution views came up empty on a first sync.
        #
        # Asset IDs are not user data: the readers mint them to a fixed
        # convention (`US_STK_`, `CN_FUND_`, `INS_`, …) documented in
        # docs/adding-a-source.md. That convention is knowledge the project
        # already has and can classify from without anyone configuring anything.
        #
        # Deliberately last. It cannot override a curated rule, only catch what
        # they all missed, so an established database behaves exactly as before.
        result = self._try_id_regex(asset_id)
        if result:
            result.asset_id = asset_id
            return result

        # Step 5: Unclassified
        return ClassificationResult(
            asset_id=asset_id,
            class_id=None,
            tier_id=None,
            method="auto_unclassified",
            confidence=0.0
        )

    def classify_batch(self, assets: List[Tuple[str, str]]) -> List[ClassificationResult]:
        """Classify multiple assets. Input: [(asset_id, asset_name), ...]"""
        return [self.classify(aid, aname) for aid, aname in assets]
    
    def _try_exact_id(self, asset_id: str) -> Optional[ClassificationResult]:
        """Try to match by exact asset ID."""
        row = self.connector.execute("""
            SELECT class_id, tier_id FROM classification_rules 
            WHERE rule_type = 'exact_id' AND pattern = ?
        """, [asset_id]).fetchone()
        
        if row:
            return ClassificationResult(
                asset_id=asset_id,
                class_id=row[0],
                tier_id=row[1],
                method="auto_exact_id",
                confidence=1.0
            )
        return None
    
    def _try_exact_name(self, asset_name: str) -> Optional[ClassificationResult]:
        """Try to match by exact asset name."""
        row = self.connector.execute("""
            SELECT class_id, tier_id FROM classification_rules 
            WHERE rule_type = 'exact_name' AND pattern = ?
        """, [asset_name]).fetchone()
        
        if row:
            return ClassificationResult(
                asset_id="",  # Will be filled by caller
                class_id=row[0],
                tier_id=row[1],
                method="auto_exact_name",
                confidence=1.0
            )
        return None
    
    def _try_regex(self, asset_name: str) -> Optional[ClassificationResult]:
        """Try to match using regex rules in priority order.
        
        IMPORTANT: Fetch ALL regex rules and test in Python, not SQL REGEXP.
        Return on FIRST match (priority-ordered).
        """
        rows = self.connector.execute("""
            SELECT pattern, class_id, tier_id FROM classification_rules 
            WHERE rule_type = 'regex' 
            ORDER BY priority ASC
        """).fetchall()
        
        for row in rows:
            pattern = row[0]
            try:
                if re.search(pattern, asset_name, re.IGNORECASE):
                    return ClassificationResult(
                        asset_id="",  # Will be filled by caller
                        class_id=row[1],
                        tier_id=row[2],
                        method="auto_regex",
                        confidence=0.8
                    )
            except re.error as e:
                logger.warning(f"Invalid regex pattern '{pattern}': {e}")
                continue

        return None

    def _try_id_regex(self, asset_id: str) -> Optional[ClassificationResult]:
        """Match the asset ID against `id_regex` rules, in priority order.

        Same shape as `_try_regex` but tested against the ID. Confidence is
        lower (0.6): an ID prefix says what *kind* of thing this is, which is
        weaker evidence than a name or an explicitly curated mapping.
        """
        rows = self.connector.execute("""
            SELECT pattern, class_id, tier_id FROM classification_rules
            WHERE rule_type = 'id_regex'
            ORDER BY priority ASC
        """).fetchall()

        for pattern, class_id, tier_id in rows:
            try:
                if re.search(pattern, asset_id, re.IGNORECASE):
                    return ClassificationResult(
                        asset_id="",  # Will be filled by caller
                        class_id=class_id,
                        tier_id=tier_id,
                        method="auto_id_regex",
                        confidence=0.6,
                    )
            except re.error as e:
                logger.warning(f"Invalid id_regex pattern '{pattern}': {e}")
                continue

        return None

    def classify_registry(self, connector: DatabaseConnector) -> dict:
        """Classify all assets in asset_registry using rules.
        Updates asset_registry.asset_class with the sub-class name.
        Logs changes to classification_audit_log.
        Returns: {classified: int, unclassified: int}
        """
        # 1. Fetch all assets from asset_registry
        assets = connector.execute("""
            SELECT canonical_id, display_name, asset_class
            FROM asset_registry
        """).fetchall()
        
        classified_count = 0
        unclassified_count = 0
        
        for row in assets:
            canonical_id = row[0]
            display_name = row[1]
            old_class = row[2]
            
            # 2. Classify the asset
            result = self.classify(canonical_id, display_name or "")
            
            # 3. If classified (class_id is not None):
            if result.class_id is not None:
                # Look up class name from taxonomy_classes
                class_row = connector.execute(
                    "SELECT name FROM taxonomy_classes WHERE id = ?",
                    [result.class_id]
                ).fetchone()
                
                if class_row:
                    class_name = class_row[0]

                    # UPDATE asset_registry SET asset_class = class_name
                    connector.execute("""
                        UPDATE asset_registry
                        SET asset_class = ?
                        WHERE canonical_id = ?
                    """, [class_name, canonical_id])

                    # Also update tier if the matching rule has a tier_id
                    if result.tier_id:
                        tier_row = connector.execute(
                            "SELECT name FROM asset_tiers WHERE id = ?",
                            [result.tier_id]
                        ).fetchone()
                        if tier_row:
                            connector.execute("""
                                UPDATE asset_registry
                                SET tier = ?
                                WHERE canonical_id = ?
                            """, [tier_row[0], canonical_id])

                    # INSERT into classification_audit_log with auto-generated id
                    connector.execute("""
                        INSERT INTO classification_audit_log
                        (id, asset_id, old_class_id, new_class_id, old_tier_id, new_tier_id, method, changed_by, notes)
                        VALUES (
                            (SELECT COALESCE(MAX(id), 0) + 1 FROM classification_audit_log),
                            ?, NULL, ?, NULL, ?, ?, 'system', ?
                        )
                    """, [canonical_id, result.class_id, result.tier_id, result.method,
                          f"Auto-classified from '{old_class}' to '{class_name}'"])

                    classified_count += 1
            else:
                unclassified_count += 1
        
        return {'classified': classified_count, 'unclassified': unclassified_count}
