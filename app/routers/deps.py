"""라우터 공용 의존성."""

from __future__ import annotations

from fastapi import Depends, HTTPException, Path
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.control_db import get_session, get_sessionmaker
from app.models.control.group import Group
from app.routers.auth import CurrentUser, require_user


def can_access_group(owner_user_id: int | None, user: CurrentUser) -> bool:
    """admin은 전 그룹, 일반 사용자는 본인 소유 그룹만. owner 미지정(레거시)은 admin만."""
    if user.is_admin:
        return True
    return owner_user_id is not None and owner_user_id == user.user_id


async def get_group_or_404(
    slug: str = Path(..., description="그룹 slug"),
    user: CurrentUser = Depends(require_user),
    session: AsyncSession = Depends(get_session),
) -> Group:
    result = await session.execute(select(Group).where(Group.slug == slug))
    group = result.scalar_one_or_none()
    # 타인 그룹은 존재 여부를 노출하지 않도록 미존재와 동일하게 404.
    if group is None or not can_access_group(group.owner_user_id, user):
        raise HTTPException(status_code=404, detail=f"그룹을 찾을 수 없습니다: {slug}")
    return group


async def get_group_by_slug_or_404(slug: str) -> Group:
    """slug로 그룹을 조회한다(일반 async 함수, FastAPI Depends 아님).

    공개 공유 페이지처럼 인증 체인 밖에서 호출할 때 사용한다(소유권 미검사 —
    공유 페이지는 서명 토큰으로 접근이 통제된다).
    """
    async with get_sessionmaker()() as session:
        result = await session.execute(select(Group).where(Group.slug == slug))
        group = result.scalar_one_or_none()
    if group is None:
        raise HTTPException(status_code=404, detail=f"그룹을 찾을 수 없습니다: {slug}")
    return group
