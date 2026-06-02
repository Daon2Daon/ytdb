"""그룹 잡 로그 조회 API."""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select

from app.models.control.group import Group
from app.models.pg.job_log import JobLog
from app.routers.deps import get_group_or_404
from app.services.db_engine import data_plane_engine_manager as dpm

router = APIRouter(prefix="/api/groups/{slug}/logs", tags=["logs"])


class JobLogOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    log_pk: int
    job_type: str
    channel_pk: Optional[int]
    video_pk: Optional[int]
    status: str
    message: Optional[str]
    duration_ms: Optional[int]
    started_at: datetime


@router.get("", response_model=list[JobLogOut])
async def list_logs(
    group: Group = Depends(get_group_or_404),
    limit: int = Query(50, ge=1, le=200),
) -> list[JobLog]:
    async with dpm.group_session(group) as session:
        result = await session.execute(
            select(JobLog).order_by(JobLog.log_pk.desc()).limit(limit)
        )
        return list(result.scalars().all())
