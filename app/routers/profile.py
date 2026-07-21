"""그룹 프로필 조회·재생성 API."""

from __future__ import annotations

from fastapi import APIRouter, Depends

from app.models.control.group import Group
from app.routers.deps import get_group_or_404
from app.services.bootstrap_service import bootstrap_profile
from app.services.settings_manager import get_settings_manager

router = APIRouter(prefix="/api/groups/{slug}/profile", tags=["profile"])


@router.get("")
async def get_profile(group: Group = Depends(get_group_or_404)) -> dict:
    p = await get_settings_manager().get_profile(group.group_id)
    return {
        "persona": p.persona,
        "digest_sections": p.digest_sections,
        "bootstrap_status": p.bootstrap_status,
        "bootstrap_at": p.bootstrap_at,
    }


@router.post("/regenerate")
async def regenerate_profile(group: Group = Depends(get_group_or_404)) -> dict:
    await bootstrap_profile(group, force=True)
    p = await get_settings_manager().get_profile(group.group_id)
    return {
        "persona": p.persona,
        "digest_sections": p.digest_sections,
        "bootstrap_status": p.bootstrap_status,
    }
