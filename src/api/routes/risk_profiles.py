"""Risk profile management API endpoints."""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from typing import Optional
from src.database.connector import DatabaseConnector
from src.api.dependencies import get_db
from src.classification.risk_profile_manager import RiskProfileManager
from src.classification.taxonomy_manager import TaxonomyManager
from src.storage.gcs_flush import mark_dirty

router = APIRouter(prefix="/risk-profiles", tags=["risk-profiles"])


class CreateProfileRequest(BaseModel):
    name: str
    name_en: Optional[str] = None
    is_active: bool = False
    description: Optional[str] = None


class UpdateAllocationsRequest(BaseModel):
    allocations: dict[int, float]  # {class_id: target_pct}


@router.get("")
async def get_profiles(db: DatabaseConnector = Depends(get_db)):
    """List all risk profiles."""
    mgr = RiskProfileManager(db)
    profiles = mgr.get_all_profiles()
    return {
        "profiles": [
            {
                "id": p.id,
                "name": p.name,
                "name_en": p.name_en,
                "is_active": p.is_active,
                "description": p.description,
            }
            for p in profiles
        ]
    }


@router.post("")
async def create_profile(req: CreateProfileRequest):
    """Create a new risk profile."""
    connector = DatabaseConnector()
    try:
        mgr = RiskProfileManager(connector)
        profile_id = mgr.create_profile(
            name=req.name,
            name_en=req.name_en,
            is_active=req.is_active,
            description=req.description,
        )
        mark_dirty()
        return {"id": profile_id, "message": f"Profile '{req.name}' created"}
    finally:
        connector.close()


@router.get("/{profile_id}/allocations")
async def get_allocations(profile_id: int, db: DatabaseConnector = Depends(get_db)):
    """Get allocation targets for a risk profile."""
    mgr = RiskProfileManager(db)
    tax_mgr = TaxonomyManager(db)
    allocs = mgr.get_allocations(profile_id)
    # Enrich with class names
    result = []
    for class_id, target_pct in allocs.items():
        tc = tax_mgr.get_class_by_id(class_id)
        result.append({
            "class_id": class_id,
            "class_name": tc.name if tc else "Unknown",
            "class_name_cn": tc.name_cn if tc else None,
            "target_pct": target_pct,
        })
    return {"profile_id": profile_id, "allocations": result}


@router.put("/{profile_id}/allocations")
async def update_allocations(profile_id: int, req: UpdateAllocationsRequest):
    """Update allocation targets for a risk profile (replaces all)."""
    connector = DatabaseConnector()
    try:
        mgr = RiskProfileManager(connector)
        # Validate profile exists
        profiles = mgr.get_all_profiles()
        if not any(p.id == profile_id for p in profiles):
            raise HTTPException(status_code=404, detail=f"Profile {profile_id} not found")
        # Validate allocations sum to ~100%
        total = sum(req.allocations.values())
        if abs(total - 100.0) > 1.0:
            raise HTTPException(
                status_code=400,
                detail=f"Allocations sum to {total:.1f}%, expected ~100%",
            )
        mgr.set_allocations(profile_id, req.allocations)
        mark_dirty()
        return {"message": f"Profile {profile_id} allocations updated"}
    finally:
        connector.close()


@router.post("/{profile_id}/activate")
async def activate_profile(profile_id: int):
    """Set a risk profile as active (deactivates all others)."""
    connector = DatabaseConnector()
    try:
        mgr = RiskProfileManager(connector)
        profiles = mgr.get_all_profiles()
        if not any(p.id == profile_id for p in profiles):
            raise HTTPException(status_code=404, detail=f"Profile {profile_id} not found")
        mgr.activate_profile(profile_id)
        mark_dirty()
        return {"message": f"Profile {profile_id} activated"}
    finally:
        connector.close()
