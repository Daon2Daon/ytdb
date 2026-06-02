"""그룹 CRUD API."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.control_db import get_session
from app.models.control.group import Group
from app.routers.deps import get_group_or_404
from app.schemas.group import GroupCreate, GroupOut, GroupUpdate

router = APIRouter(prefix="/api/groups", tags=["groups"])


@router.get("", response_model=list[GroupOut])
async def list_groups(session: AsyncSession = Depends(get_session)) -> list[Group]:
    result = await session.execute(select(Group).order_by(Group.group_id))
    return list(result.scalars().all())


@router.post("", response_model=GroupOut, status_code=201)
async def create_group(
    payload: GroupCreate, session: AsyncSession = Depends(get_session)
) -> Group:
    schema_name = payload.schema_name or f"youtube_{payload.slug}"
    group = Group(
        slug=payload.slug,
        name=payload.name,
        schema_name=schema_name,
        description=payload.description,
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
    for field, value in data.items():
        setattr(group, field, value)
    await session.commit()
    await session.refresh(group)
    return group


@router.delete("/{slug}", status_code=204)
async def delete_group(
    group: Group = Depends(get_group_or_404),
    session: AsyncSession = Depends(get_session),
) -> None:
    await session.delete(group)
    await session.commit()
