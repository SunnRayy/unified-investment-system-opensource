
"""
Discrepancy Reporter Module
Generates human-readable markdown reports for system discrepancies.
"""

import os
import logging
from typing import List, Dict
from datetime import datetime

logger = logging.getLogger(__name__)

class DiscrepancyReporter:
    def __init__(self, output_dir: str = "output/discrepancy_report"):
        self.output_dir = output_dir
        
    def generate_report(self, divergences: List[Dict], threshold_pct: float) -> str:
        """
        Generate a markdown report for the given divergences.
        
        Args:
            divergences: List of divergence records
            threshold_pct: The threshold used for detection
            
        Returns:
            Path to the generated report file
        """
        if not divergences:
            logger.info("No divergences to report.")
            return None
            
        # Ensure output dict exists
        os.makedirs(self.output_dir, exist_ok=True)
        
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"{timestamp}_divergence_report.md"
        filepath = os.path.join(self.output_dir, filename)
        
        with open(filepath, 'w') as f:
            # Header
            f.write(f"# System Discrepancy Report\n\n")
            f.write(f"**Generated:** {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
            f.write(f"**Threshold:** > {threshold_pct}%\n\n")
            
            # Summary
            f.write(f"## 1. Summary\n\n")
            f.write(f"Found **{len(divergences)}** assets with significant divergence between Authoritative and Shadow sources.\n\n")
            
            # Stats
            sources = set()
            max_div = 0.0
            max_asset = ""
            for d in divergences:
                sources.add(d['auth_source'])
                div = d.get('diverence_pct', 0)
                if div > max_div:
                    max_div = div
                    max_asset = d['canonical_id']
            
            f.write(f"- **Affected Sources:** {', '.join(sources)}\n")
            f.write(f"- **Max Divergence:** {max_div:.1f}% ({max_asset})\n\n")
            
            # Appendix / Details
            f.write(f"## 2. Detailed Comparison (Appendix)\n\n")
            f.write("| Status | Asset ID | Authority Source | Auth Value | Shadow Source | Shadow Value | Diff % | Comment |\n")
            f.write("| :--- | :--- | :--- | ---: | :--- | ---: | ---: | :--- |\n")
            
            for d in divergences:
                # Determine status icon
                pct = d['diverence_pct']
                status = "⚠️ Mismatch"
                if pct > 20: status = "🚨 Critical"
                
                asset = d['canonical_id']
                auth_src = d['auth_source']
                auth_val = d['auth_value']
                shadow_src = d['shadow_source']
                shadow_val = d['shadow_value']
                
                # Try to auto-comment
                comment = ""
                if shadow_val == 0:
                    comment = "Missing in Shadow"
                elif auth_val == 0:
                    comment = "Missing in Authority"
                
                f.write(f"| {status} | `{asset}` | {auth_src} | {auth_val:,.2f} | {shadow_src} | {shadow_val:,.2f} | {pct:.1f}% | {comment} |\n")
                
        logger.info(f"Discrepancy report generated: {filepath}")
        return filepath
