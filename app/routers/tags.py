"""그룹 태그 조회 API."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select

from app.models.control.group import Group
from app.models.pg.tag import Tag
from app.routers.deps import get_group_or_404
from app.schemas.tag import TagOut
from app.services.db_engine import data_plane_engine_manager as dpm

router = APIRouter(prefix="/api/groups/{slug}/tags", tags=["tags"])


@router.get("", response_model=list[TagOut])
async def list_tags(
    group: Group = Depends(get_group_or_404),
    min_count: int = Query(1, ge=1),
    limit: int = Query(200, ge=1, le=500),
) -> list[Tag]:
    async with dpm.group_session(group) as session:
        rows = await session.execute(
            select(Tag)
            .where(Tag.video_count >= min_count)
            .order_by(Tag.video_count.desc(), Tag.name.asc())
            .limit(limit)
        )
        return list(rows.scalars().all())
