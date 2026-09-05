"""Data models for classification engine."""
from dataclasses import dataclass
from typing import Optional


@dataclass
class TaxonomyClass:
    """Represents a taxonomy class in the hierarchy (top-level or sub-class)."""
    id: int
    name: str
    name_cn: Optional[str] = None
    parent_id: Optional[int] = None
    level: int = 0
    sort_order: int = 0
    is_rebalanceable: bool = True
    description: Optional[str] = None


@dataclass
class AssetTier:
    """Represents a portfolio tier (tier_1_core, tier_2_diversification, tier_3_trading)."""
    id: str  # "tier_1_core", "tier_2_diversification", "tier_3_trading"
    name: str
    name_en: Optional[str] = None
    target_pct: float = 0.0
    description: Optional[str] = None
    color: Optional[str] = None
    sort_order: int = 0


@dataclass
class RiskProfile:
    """Represents an investor risk profile (保守型, 均衡型, 成长型, 进取型)."""
    id: int
    name: str
    name_en: Optional[str] = None
    is_active: bool = False
    description: Optional[str] = None


@dataclass
class ClassificationRule:
    """Represents a classification rule (exact_id, exact_name, or regex pattern)."""
    id: int
    rule_type: str  # "exact_id", "exact_name", "regex"
    pattern: str
    class_id: Optional[int] = None
    tier_id: Optional[str] = None
    priority: int = 100
    source: str = "manual"


@dataclass
class ClassificationResult:
    """Result of auto-tagger classification attempt."""
    asset_id: str
    class_id: Optional[int] = None
    tier_id: Optional[str] = None
    method: str = "auto_unclassified"
    confidence: float = 0.0
