"""라우터 공용 의존성."""

from __future__ import annotations

from fastapi import Depends, HTTPException, Path
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.control_db import get_session
from app.models.control.group import Group


async def get_group_or_404(
    slug: str = Path(..., description="그룹 slug"),
    session: AsyncSession = Depends(get_session),
) -> Group:
    result = await session.execute(select(Group).where(Group.slug == slug))
    group = result.scalar_one_or_none()
    if group is None:
        raise HTTPException(status_code=404, detail=f"그룹을 찾을 수 없습니다: {slug}")
    return group
