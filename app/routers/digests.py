"""그룹 주간 리뷰(digests) API."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select

from app.models.control.group import Group
from app.models.pg.digest import Digest
from app.routers.deps import get_group_or_404
from app.schemas.digest import DigestGenerateRequest, DigestOut
from app.services.db_engine import DBNotConfiguredError, data_plane_engine_manager as dpm
from app.services.digest_service import generate_digest_for_group
from app.services.settings_manager import get_settings_manager

router = APIRouter(prefix="/api/groups/{slug}/digests", tags=["digests"])


@router.get("", response_model=list[DigestOut])
async def list_digests(group: Group = Depends(get_group_or_404)) -> list[Digest]:
    try:
        async with dpm.group_session(group) as session:
            rows = await session.execute(
                select(Digest).order_by(Digest.period_end.desc(), Digest.digest_pk.desc()).limit(100)
            )
            return list(rows.scalars().all())
    except DBNotConfiguredError:
        return []


@router.get("/{digest_pk}", response_model=DigestOut)
async def get_digest(digest_pk: int, group: Group = Depends(get_group_or_404)) -> Digest:
    async with dpm.group_session(group) as session:
        digest = (
            await session.execute(select(Digest).where(Digest.digest_pk == digest_pk))
        ).scalar_one_or_none()
        if digest is None:
            raise HTTPException(status_code=404, detail="주간 리뷰를 찾을 수 없습니다.")
        return digest


@router.delete("/{digest_pk}", status_code=204)
async def delete_digest(digest_pk: int, group: Group = Depends(get_group_or_404)) -> None:
    async with dpm.group_session(group) as session:
        async with session.begin():
            digest = (
                await session.execute(select(Digest).where(Digest.digest_pk == digest_pk))
            ).scalar_one_or_none()
            if digest is None:
                raise HTTPException(status_code=404, detail="주간 리뷰를 찾을 수 없습니다.")
            await session.delete(digest)


@router.post("/generate", response_model=DigestOut, status_code=201)
async def generate_digest(
    payload: DigestGenerateRequest, group: Group = Depends(get_group_or_404)
) -> Digest:
    cfg = await get_settings_manager().get_digest(group.group_id)
    digest = await generate_digest_for_group(
        group=group,
        digest_cfg=cfg,
        period_weeks=payload.period_weeks,
        category=payload.category,
        save=payload.save,
    )
    return digest
