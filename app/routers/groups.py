"""그룹 CRUD API."""

from __future__ import annotations

import secrets as _secrets

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.control_db import get_session
from app.models.control.group import Group
from app.routers.auth import CurrentUser, require_user
from app.routers.deps import get_group_or_404
from app.schemas.group import GroupCreate, GroupOut, GroupUpdate
from app.services.channel_registry_service import remove_group_subscriptions, resync_group
from app.services.default_settings import seed_default_settings
from app.services.quota_service import QuotaExceeded, check_group_quota

router = APIRouter(prefix="/api/groups", tags=["groups"])


@router.get("", response_model=list[GroupOut])
async def list_groups(
    user: CurrentUser = Depends(require_user),
    session: AsyncSession = Depends(get_session),
) -> list[Group]:
    stmt = select(Group).order_by(Group.group_id)
    if not user.is_admin:
        stmt = stmt.where(Group.owner_user_id == user.user_id)
    result = await session.execute(stmt)
    return list(result.scalars().all())


@router.post("", response_model=GroupOut, status_code=201)
async def create_group(
    payload: GroupCreate,
    user: CurrentUser = Depends(require_user),
    session: AsyncSession = Depends(get_session),
) -> Group:
    if user.is_admin:
        # 관리자: 기존과 동일하게 slug/schema_name 직접 지정.
        if not payload.slug:
            raise HTTPException(status_code=422, detail="slug는 필수입니다.")
        slug = payload.slug
        schema_name = payload.schema_name or f"youtube_{slug}"
    else:
        # 일반 사용자: slug/schema 자동 생성 (스펙 §2.8). 입력값은 무시.
        try:
            await check_group_quota(session, user.user_id)
        except QuotaExceeded as e:
            raise HTTPException(status_code=400, detail=e.detail)
        slug = f"u{user.user_id}_{_secrets.token_hex(3)}"
        schema_name = f"youtube_{slug}"
    group = Group(
        slug=slug,
        name=payload.name,
        schema_name=schema_name,
        description=payload.description,
        owner_user_id=user.user_id if user.user_id != 0 else None,
    )
    session.add(group)
    try:
        await session.commit()
    except IntegrityError:
        await session.rollback()
        raise HTTPException(
            status_code=409, detail="slug 또는 schema_name이 이미 존재합니다."
        )
    await session.refresh(group)
    # 사용자 편의: 추천 기본 설정값을 미리 채워 둔다(시크릿/접속정보 제외).
    await seed_default_settings(group.group_id)
    return group


@router.get("/{slug}", response_model=GroupOut)
async def get_group(group: Group = Depends(get_group_or_404)) -> Group:
    return group


@router.patch("/{slug}", response_model=GroupOut)
async def update_group(
    payload: GroupUpdate,
    group: Group = Depends(get_group_or_404),
    session: AsyncSession = Depends(get_session),
) -> Group:
    data = payload.model_dump(exclude_unset=True)
    was_active = group.is_active
    for field, value in data.items():
        setattr(group, field, value)
    await session.commit()
    await session.refresh(group)
    if "is_active" in data and group.is_active != was_active:
        if group.is_active:
            await resync_group(group)                      # 재활성 → 구독 복원 (스펙 §4)
        else:
            await remove_group_subscriptions(session, group.group_id)
            await session.commit()
    return group


@router.delete("/{slug}", status_code=204)
async def delete_group(
    group: Group = Depends(get_group_or_404),
    session: AsyncSession = Depends(get_session),
) -> None:
    await remove_group_subscriptions(session, group.group_id)
    await session.delete(group)
    await session.commit()
