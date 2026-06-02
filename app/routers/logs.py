"""그룹 잡 로그 조회 API."""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, ConfigDict
from sqlalchemy import func, select

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


class PaginatedJobLogs(BaseModel):
    total: int
    page: int
    page_size: int
    items: list[JobLogOut]


@router.get("")
async def list_logs(
    group: Group = Depends(get_group_or_404),
    job_type: str | None = Query(None, description="job_type 필터"),
    status: str | None = Query(None, description="status 필터"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    paged: bool = Query(False, description="true면 {items,total,page,page_size} 반환"),
):
    async with dpm.group_session(group) as session:
        stmt = select(JobLog).order_by(JobLog.log_pk.desc()).limit(limit).offset(offset)
        if job_type:
            stmt = stmt.where(JobLog.job_type == job_type)
        if status:
            stmt = stmt.where(JobLog.status == status)
        rows = list((await session.execute(stmt)).scalars().all())

        total = None
        if paged:
            count_stmt = select(func.count()).select_from(JobLog)
            if job_type:
                count_stmt = count_stmt.where(JobLog.job_type == job_type)
            if status:
                count_stmt = count_stmt.where(JobLog.status == status)
            total = (await session.execute(count_stmt)).scalar_one()

    if not paged:
        return rows

    page = offset // limit + 1 if limit else 1
    return PaginatedJobLogs(
        total=total,
        page=page,
        page_size=limit,
        items=[JobLogOut.model_validate(r) for r in rows],
    )
