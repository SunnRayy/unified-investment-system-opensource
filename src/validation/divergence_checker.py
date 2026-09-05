
"""
Divergence Checker Module
Checks for significant discrepancies between authoritative and shadow records.
"""

import json
import logging
from typing import List, Dict
from datetime import datetime, date

from src.database.connector import DatabaseConnector
from src.reports.reconciliation import generate_reconciliation_report

logger = logging.getLogger(__name__)

class DivergenceChecker:
    def __init__(self, connector: DatabaseConnector):
        """
        Initialize DivergenceChecker.
        
        Args:
            connector: Database connector
        """
        self.connector = connector
        
    def check_divergence(self, threshold_pct: float = 10.0) -> List[Dict]:
        """
        Check for divergences between Authority and Shadow records exceeding threshold.
        Logs significant divergences to audit log.
        
        Args:
            threshold_pct: Percentage threshold for warning (default 10%)
            
        Returns:
            List of divergent records found
        """
        timestamp = datetime.now()
        report = generate_reconciliation_report(self.connector, date.today())
        
        divergences = [r for r in report if r['diverence_pct'] > threshold_pct]
        
        if not divergences:
            return []
            
        logger.warning(f"Found {len(divergences)} holdings with divergence > {threshold_pct}%")

        for item in divergences:
            msg = (f"Holdings divergence: {item['canonical_id']} "
                   f"Auth({item['auth_source']})={item['auth_value']} vs "
                   f"Shadow({item['shadow_source']})={item['shadow_value']} "
                   f"({item['diverence_pct']:.1f}%)")
            
            logger.warning(msg)
            
            # Log to sync_audit_logs
            try:
                self.connector.execute("""
                    INSERT INTO sync_audit_logs (
                        sync_timestamp, source_system, target_table, record_key,
                        conflict_type, source_value, target_value, resolution
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    timestamp,
                    item['auth_source'],
                    'holdings',
                    item['canonical_id'],
                    'holdings_divergence',
                    json.dumps({'value': item['auth_value']}),
                    json.dumps({'value': item['shadow_value'], 'source': item['shadow_source']}),
                    f"divergence_{item['diverence_pct']:.1f}%"
                ))
            except Exception as e:
                logger.error(f"Failed to log audit for {item['canonical_id']}: {e}")
            
        return divergences
