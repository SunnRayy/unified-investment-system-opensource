"""AnalysisGenerator: public facade for single-asset analysis pipeline."""
from __future__ import annotations

from src.analysis.pipeline import AnalysisResult, AssetAnalysisPipeline


class AnalysisGenerator:
    """Generate portfolio-contextualized single-asset analysis."""

    def analyze(
        self,
        asset_code: str,
        triggered_by: str = "user",
        db_path: str = "data/unified.duckdb",
        days: int = 60,
    ) -> AnalysisResult:
        pipeline = AssetAnalysisPipeline()
        return pipeline.analyze(
            asset_code=asset_code,
            triggered_by=triggered_by,
            db_path=db_path,
            days=days,
        )
