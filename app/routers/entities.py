"""엔티티 사전: 병합 보류 후보 조회·승인·거절 (Phase 3 승인 큐)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select, update

from app.models.control.group import Group
from app.models.pg.entity import Entity
from app.models.pg.job_log import JobLog
from app.routers.deps import get_group_or_404
from app.services.db_engine import data_plane_engine_manager as dpm
from app.services.entity_service import apply_merge_cluster

router = APIRouter(prefix="/api/groups/{slug}/entities", tags=["entities"])


@router.get("/merge-candidates")
async def list_merge_candidates(group: Group = Depends(get_group_or_404)) -> list[dict]:
    async with dpm.group_session(group) as session:
        rows = (await session.execute(select(Entity))).scalars().all()
        out = []
        for e in rows:
            cands = list((e.attrs or {}).get("merge_candidates") or [])
            if cands:
                out.append({
                    "entity_pk": e.entity_pk,
                    "canonical_name": e.canonical_name,
                    "candidates": cands,
                    "mention_count": e.mention_count,
                })
    return out


class MergeAction(BaseModel):
    alias: str


async def _pop_candidate(session, entity_pk: int, alias: str) -> Entity:
    """후보 목록에서 alias 제거 후 대상 엔티티 반환. 없으면 404."""
    crow = (await session.execute(
        select(Entity).where(Entity.entity_pk == entity_pk)
    )).scalars().first()
    if crow is None:
        raise HTTPException(status_code=404, detail="엔티티가 없습니다")
    attrs = dict(crow.attrs or {})
    cands = [c for c in (attrs.get("merge_candidates") or []) if c != alias]
    if cands:
        attrs["merge_candidates"] = cands
    else:
        attrs.pop("merge_candidates", None)
    await session.execute(
        update(Entity).where(Entity.entity_pk == entity_pk).values(attrs=attrs))
    return crow


@router.post("/{entity_pk}/merge")
async def approve_merge(
    entity_pk: int, body: MergeAction, group: Group = Depends(get_group_or_404)
) -> dict:
    """보류 후보 승인 — 배치 병합과 동일 코드(apply_merge_cluster) 사용."""
    alias = body.alias.strip()
    if not alias:
        raise HTTPException(status_code=400, detail="alias가 비어 있습니다")
    async with dpm.group_session(group) as session:
        async with session.begin():
            crow = await _pop_candidate(session, entity_pk, alias)
            merged = await apply_merge_cluster(
                session, {"canonical": crow.canonical_name, "aliases": [alias]})
            for a in merged:
                session.add(JobLog(
                    job_type="entity_merge", status="success",
                    message=f"{a} → {crow.canonical_name} (수동 승인)"[:500],
                ))
    return {"merged": merged}


@router.post("/{entity_pk}/reject")
async def reject_merge(
    entity_pk: int, body: MergeAction, group: Group = Depends(get_group_or_404)
) -> dict:
    alias = body.alias.strip()
    if not alias:
        raise HTTPException(status_code=400, detail="alias가 비어 있습니다")
    async with dpm.group_session(group) as session:
        async with session.begin():
            await _pop_candidate(session, entity_pk, alias)
    return {"rejected": alias}
