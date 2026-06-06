"""라우터 공용 의존성."""

from __future__ import annotations

from fastapi import Depends, HTTPException, Path
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.control_db import get_session, get_sessionmaker
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


async def get_group_by_slug_or_404(slug: str) -> Group:
    """slug로 그룹을 조회한다(일반 async 함수, FastAPI Depends 아님).

    공개 엔드포인트처럼 Depends 체인 밖에서 호출할 때 사용한다.
    """
    async with get_sessionmaker()() as session:
        result = await session.execute(select(Group).where(Group.slug == slug))
        group = result.scalar_one_or_none()
    if group is None:
        raise HTTPException(status_code=404, detail=f"그룹을 찾을 수 없습니다: {slug}")
    return group
