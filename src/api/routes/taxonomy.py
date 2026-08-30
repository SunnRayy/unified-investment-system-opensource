"""Taxonomy management API endpoints."""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from typing import Optional
from src.database.connector import DatabaseConnector
from src.api.dependencies import get_db
from src.classification.taxonomy_manager import TaxonomyManager
from src.classification.tier_manager import TierManager
from src.classification.auto_tagger import AutoTagger
from src.storage.gcs_flush import mark_dirty

router = APIRouter(prefix="/taxonomy", tags=["taxonomy"])


# --- Request/Response Models ---

class CreateClassRequest(BaseModel):
    name: str
    name_cn: Optional[str] = None
    parent_id: Optional[int] = None
    level: int = 0
    sort_order: int = 0
    is_rebalanceable: bool = True
    description: Optional[str] = None


class UpdateClassRequest(BaseModel):
    name: Optional[str] = None
    name_cn: Optional[str] = None
    level: Optional[int] = None
    parent_id: Optional[int] = None
    sort_order: Optional[int] = None
    is_rebalanceable: Optional[bool] = None
    description: Optional[str] = None


class CreateRuleRequest(BaseModel):
    rule_type: str  # "exact_id", "exact_name", "regex"
    pattern: str
    class_id: int
    tier_id: Optional[str] = None
    priority: int = 50
    source: str = "manual"


# --- Class Hierarchy Endpoints ---

@router.get("/classes")
async def get_classes(db: DatabaseConnector = Depends(get_db)):
    """Get full taxonomy class hierarchy."""
    mgr = TaxonomyManager(db)
    top_classes = mgr.get_top_level_classes()
    result = []
    for tc in top_classes:
        children = mgr.get_children(tc.id)
        result.append({
            "id": tc.id,
            "name": tc.name,
            "name_cn": tc.name_cn,
            "parent_id": tc.parent_id,
            "level": tc.level,
            "sort_order": tc.sort_order,
            "is_rebalanceable": tc.is_rebalanceable,
            "description": tc.description,
            "children": [
                {
                    "id": c.id,
                    "name": c.name,
                    "name_cn": c.name_cn,
                    "parent_id": c.parent_id,
                    "level": c.level,
                    "sort_order": c.sort_order,
                    "is_rebalanceable": c.is_rebalanceable,
                    "description": c.description,
                }
                for c in children
            ],
        })
    return {"classes": result}


@router.post("/classes")
async def create_class(req: CreateClassRequest):
    """Create a new taxonomy class."""
    connector = DatabaseConnector()
    try:
        mgr = TaxonomyManager(connector)
        class_id = mgr.create_class(
            name=req.name,
            name_cn=req.name_cn,
            parent_id=req.parent_id,
            level=req.level,
            sort_order=req.sort_order,
            is_rebalanceable=req.is_rebalanceable,
            description=req.description,
        )
        mark_dirty()
        return {"id": class_id, "message": f"Class '{req.name}' created"}
    finally:
        connector.close()


@router.put("/classes/{class_id}")
async def update_class(class_id: int, req: UpdateClassRequest):
    """Update an existing taxonomy class."""
    connector = DatabaseConnector()
    try:
        mgr = TaxonomyManager(connector)
        existing = mgr.get_class_by_id(class_id)
        if not existing:
            raise HTTPException(status_code=404, detail=f"Class {class_id} not found")
        updates = {k: v for k, v in req.model_dump().items() if v is not None}
        if not updates:
            raise HTTPException(status_code=400, detail="No fields to update")
        mgr.update_class(class_id, **updates)
        mark_dirty()
        return {"message": f"Class {class_id} updated"}
    finally:
        connector.close()


@router.delete("/classes/{class_id}")
async def delete_class(class_id: int):
    """Delete a taxonomy class (only if no assets use it)."""
    connector = DatabaseConnector()
    try:
        mgr = TaxonomyManager(connector)
        existing = mgr.get_class_by_id(class_id)
        if not existing:
            raise HTTPException(status_code=404, detail=f"Class {class_id} not found")
        # Safety: check if any assets reference this class
        result = connector.execute(
            "SELECT COUNT(*) as cnt FROM classification_rules WHERE class_id = ?",
            [class_id],
        )
        if result[0]["cnt"] > 0:
            raise HTTPException(
                status_code=409,
                detail=f"Cannot delete: {result[0]['cnt']} classification rules reference this class",
            )
        connector.execute("DELETE FROM taxonomy_classes WHERE id = ?", [class_id])
        mark_dirty()
        return {"message": f"Class {class_id} deleted"}
    finally:
        connector.close()


# --- Tier Endpoints ---

@router.get("/tiers")
async def get_tiers(db: DatabaseConnector = Depends(get_db)):
    """Get all asset tiers."""
    mgr = TierManager(db)
    tiers = mgr.get_all_tiers()
    return {
        "tiers": [
            {
                "id": t.id,
                "name": t.name,
                "name_en": t.name_en,
                "target_pct": t.target_pct,
                "description": t.description,
                "color": t.color,
                "sort_order": t.sort_order,
            }
            for t in tiers
        ]
    }


# --- Classification Rules Endpoints ---

@router.get("/rules")
async def get_rules(db: DatabaseConnector = Depends(get_db)):
    """Get all classification rules."""
    result = db.execute(
        """SELECT r.id, r.rule_type, r.pattern, r.class_id, r.tier_id,
                  r.priority, r.source, r.created_at,
                  tc.name as class_name, tc.name_cn as class_name_cn,
                  atiers.name as tier_name
           FROM classification_rules r
           LEFT JOIN taxonomy_classes tc ON r.class_id = tc.id
           LEFT JOIN asset_tiers atiers ON r.tier_id = atiers.id
           ORDER BY r.priority, r.rule_type, r.pattern"""
    )
    rows = result.fetchall()
    return {"rules": [
        {
            "id": r[0], "rule_type": r[1], "pattern": r[2], "class_id": r[3],
            "tier_id": r[4], "priority": r[5], "source": r[6],
            "created_at": str(r[7]) if r[7] else None,
            "class_name": r[8], "class_name_cn": r[9],
            "tier_name": r[10],
        }
        for r in rows
    ]}


@router.post("/rules")
async def create_rule(req: CreateRuleRequest):
    """Create a new classification rule."""
    if req.rule_type not in ("exact_id", "exact_name", "regex"):
        raise HTTPException(status_code=400, detail="rule_type must be exact_id, exact_name, or regex")
    connector = DatabaseConnector()
    try:
        # Check class exists
        mgr = TaxonomyManager(connector)
        if not mgr.get_class_by_id(req.class_id):
            raise HTTPException(status_code=404, detail=f"Class {req.class_id} not found")
        # id is INTEGER PRIMARY KEY (no auto-increment) — follow seed.py pattern
        next_id = connector.execute(
            "SELECT COALESCE(MAX(id), 0) + 1 FROM classification_rules"
        ).fetchone()[0]
        connector.execute(
            """INSERT INTO classification_rules (id, rule_type, pattern, class_id, tier_id, priority, source)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            [next_id, req.rule_type, req.pattern, req.class_id, req.tier_id, req.priority, req.source],
        )
        try:
            tagger = AutoTagger(connector)
            tagger.classify_registry(connector)
        except Exception as tag_err:
            import logging
            logging.getLogger(__name__).warning(f"Auto-tagger failed after rule creation: {tag_err}")
        mark_dirty()
        return {"message": f"Rule created: {req.rule_type} '{req.pattern}'"}
    except Exception as e:
        if "Constraint" in str(e) or "unique" in str(e).lower():
            raise HTTPException(status_code=409, detail=f"A rule for (type='{req.rule_type}', pattern='{req.pattern}') already exists.")
        raise
    finally:
        connector.close()


@router.put("/rules")
async def upsert_rule(req: CreateRuleRequest):
    """Upsert a classification rule by (rule_type, pattern). Updates if exists, creates if not.
    Also updates asset_registry.asset_class for the matched asset."""
    if req.rule_type not in ("exact_id", "exact_name", "regex"):
        raise HTTPException(status_code=400, detail="rule_type must be exact_id, exact_name, or regex")
    connector = DatabaseConnector()
    try:
        mgr = TaxonomyManager(connector)
        tc = mgr.get_class_by_id(req.class_id)
        if not tc:
            raise HTTPException(status_code=404, detail=f"Class {req.class_id} not found")

        # Check if rule with same (rule_type, pattern) already exists
        existing = connector.execute(
            "SELECT id FROM classification_rules WHERE rule_type = ? AND pattern = ?",
            [req.rule_type, req.pattern],
        )
        rows = existing.fetchall() if hasattr(existing, 'fetchall') else (existing or [])
        if rows:
            rule_id = rows[0][0]
            connector.execute(
                "UPDATE classification_rules SET class_id = ?, priority = ?, source = ? WHERE id = ?",
                [req.class_id, req.priority, req.source, rule_id],
            )
            action = "updated"
        else:
            next_id = connector.execute(
                "SELECT COALESCE(MAX(id), 0) + 1 FROM classification_rules"
            ).fetchone()[0]
            connector.execute(
                """INSERT INTO classification_rules (id, rule_type, pattern, class_id, tier_id, priority, source)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                [next_id, req.rule_type, req.pattern, req.class_id, req.tier_id, req.priority, req.source],
            )
            action = "created"

        # Update asset_registry.asset_class directly for exact_id rules
        if req.rule_type == "exact_id":
            connector.execute(
                "UPDATE asset_registry SET asset_class = ? WHERE canonical_id = ?",
                [tc.name, req.pattern],
            )

        try:
            tagger = AutoTagger(connector)
            tagger.classify_registry(connector)
        except Exception as tag_err:
            import logging
            logging.getLogger(__name__).warning(f"Auto-tagger failed after rule upsert: {tag_err}")

        mark_dirty()
        return {"message": f"Rule {action}: {req.rule_type} '{req.pattern}' → '{tc.name}'", "action": action}
    finally:
        connector.close()


@router.delete("/rules/{rule_id}")
async def delete_rule(rule_id: int):
    """Delete a classification rule."""
    connector = DatabaseConnector()
    try:
        result = connector.execute(
            "SELECT id FROM classification_rules WHERE id = ?", [rule_id]
        )
        if not result:
            raise HTTPException(status_code=404, detail=f"Rule {rule_id} not found")
        connector.execute("DELETE FROM classification_rules WHERE id = ?", [rule_id])
        mark_dirty()
        return {"message": f"Rule {rule_id} deleted"}
    finally:
        connector.close()


# --- Auto-Tagger Endpoint ---

@router.post("/auto-tag")
async def run_auto_tagger():
    """Run auto-tagger on all unclassified assets in the registry."""
    connector = DatabaseConnector()
    try:
        tagger = AutoTagger(connector)
        stats = tagger.classify_registry(connector)
        mark_dirty()
        return {
            "message": "Auto-tagging complete",
            "classified": stats.get("classified", 0),
            "unclassified": stats.get("unclassified", 0),
        }
    finally:
        connector.close()


# --- Asset Audit Endpoint ---

@router.get("/audit")
async def get_asset_audit(db: DatabaseConnector = Depends(get_db)):
    """Get all assets with their current classifications for audit review."""
    result = db.execute(
            """WITH ranked_holdings AS (
                 SELECT
                     asset_id,
                     market_value AS market_value_cny,
                     quantity,
                     snapshot_date,
                     source_system,
                     market_price_unit,
                     currency,
                     price_updated_at,
                     CASE
                         WHEN REGEXP_EXTRACT(asset_id, '^[^_]+_[^_]+_(.+)$', 1) <> ''
                             THEN REGEXP_EXTRACT(asset_id, '^[^_]+_[^_]+_(.+)$', 1)
                         ELSE REGEXP_EXTRACT(asset_id, '^[^_]+_(.+)$', 1)
                     END AS native_code,
                     ROW_NUMBER() OVER (
                         PARTITION BY asset_id
                         ORDER BY
                             COALESCE(CAST(price_updated_at AS TIMESTAMP), CAST(snapshot_date AS TIMESTAMP)) DESC,
                             snapshot_date DESC,
                             source_system DESC
                     ) AS rn
                 FROM holdings
                 WHERE COALESCE(is_shadow, FALSE) = FALSE
               ),
               latest_market_prices AS (
                 SELECT
                     code,
                     date,
                     close,
                     data_source,
                     ROW_NUMBER() OVER (
                         PARTITION BY code
                         ORDER BY date DESC, id DESC
                     ) AS rn
                 FROM market_daily
               )
               SELECT
                 ar.canonical_id AS asset_id,
                 ar.display_name AS asset_name,
                 ar.asset_class,
                 ar.tier,
                 tc.name as class_name,
                 tc.name_cn as class_name_cn,
                 tc_parent.name as parent_class_name,
                 tc_parent.name_cn as parent_class_name_cn,
                 h.market_value_cny,
                 h.quantity,
                 h.snapshot_date,
                 h.source_system,
                 CASE
                     WHEN md.close IS NOT NULL THEN md.close
                     WHEN COALESCE(h.currency, ar.base_currency, 'CNY') = 'CNY' THEN h.market_price_unit
                     ELSE NULL
                 END AS market_price,
                 COALESCE(h.currency, ar.base_currency, 'CNY') AS price_currency,
                 CASE
                     WHEN md.data_source IS NOT NULL THEN md.data_source
                     WHEN COALESCE(h.currency, ar.base_currency, 'CNY') = 'CNY'
                          AND h.market_price_unit IS NOT NULL THEN h.source_system
                     ELSE NULL
                 END AS price_source,
                 tc.is_rebalanceable
               FROM asset_registry ar
               LEFT JOIN taxonomy_classes tc ON ar.asset_class = tc.name
               LEFT JOIN taxonomy_classes tc_parent ON tc.parent_id = tc_parent.id
               LEFT JOIN ranked_holdings h
                 ON ar.canonical_id = h.asset_id
                AND h.rn = 1
               LEFT JOIN latest_market_prices md
                 ON h.native_code = md.code
                AND (
                    (h.price_updated_at IS NOT NULL AND CAST(h.price_updated_at AS DATE) = md.date)
                    OR (h.price_updated_at IS NULL AND md.rn = 1)
                 )
               WHERE COALESCE(ar.is_active, TRUE) = TRUE
               ORDER BY tc_parent.sort_order NULLS LAST, tc.sort_order NULLS LAST, ar.display_name"""
    )
    rows = result.fetchall()
    return {"assets": [
        {
            "asset_id": r[0], "asset_name": r[1], "asset_class": r[2],
            "tier": r[3],
            "class_name": r[4], "class_name_cn": r[5],
            "parent_class_name": r[6], "parent_class_name_cn": r[7],
            "market_value_cny": float(r[8]) if r[8] is not None else None,
            "quantity": float(r[9]) if r[9] is not None else None,
            "snapshot_date": str(r[10]) if r[10] else None,
            "source_system": r[11],
            "market_price": float(r[12]) if r[12] is not None else None,
            "price_currency": r[13],
            "price_source": r[14],
            "is_rebalanceable": r[15] if r[15] is not None else True,
        }
        for r in rows
    ], "total": len(rows)}


# --- Asset Tier Assignment Endpoint ---

class SetAssetTierRequest(BaseModel):
    tier_id: Optional[str] = None


@router.put("/assets/{asset_id}/tier")
async def set_asset_tier(asset_id: str, req: SetAssetTierRequest):
    """Manually assign (or clear) a tier for an individual asset."""
    connector = DatabaseConnector()
    try:
        if req.tier_id is None:
            # Clear the tier
            connector.execute(
                "UPDATE asset_registry SET tier = NULL WHERE canonical_id = ?",
                [asset_id]
            )
            mark_dirty()
            return {"asset_id": asset_id, "tier": None}

        # Look up the tier name from asset_tiers
        tier_row = connector.execute(
            "SELECT name FROM asset_tiers WHERE id = ?",
            [req.tier_id]
        ).fetchone()
        if not tier_row:
            from fastapi import HTTPException
            raise HTTPException(status_code=404, detail=f"Tier '{req.tier_id}' not found")

        tier_name = tier_row[0]
        connector.execute(
            "UPDATE asset_registry SET tier = ? WHERE canonical_id = ?",
            [tier_name, asset_id]
        )
        mark_dirty()
        return {"asset_id": asset_id, "tier": tier_name}
    finally:
        connector.close()


@router.delete("/assets/{asset_id}")
async def deactivate_asset(asset_id: str):
    """Deactivate an asset in the registry and shadow all its holdings."""
    connector = DatabaseConnector()
    try:
        # Check if asset exists in registry
        existing = connector.execute(
            "SELECT canonical_id FROM asset_registry WHERE canonical_id = ?",
            [asset_id]
        ).fetchone()
        if not existing:
            raise HTTPException(status_code=404, detail=f"Asset {asset_id} not found")
        
        # Deactivate in registry
        connector.execute(
            "UPDATE asset_registry SET is_active = FALSE, is_pending = FALSE, updated_at = CURRENT_TIMESTAMP WHERE canonical_id = ?",
            [asset_id]
        )
        
        # Shadow in holdings
        connector.execute(
            "UPDATE holdings SET is_shadow = TRUE, updated_at = CURRENT_TIMESTAMP WHERE asset_id = ?",
            [asset_id]
        )
        
        mark_dirty()
        return {"message": f"Asset '{asset_id}' deactivated and holdings shadowed."}
    finally:
        connector.close()

