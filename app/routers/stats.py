"""그룹 스코프 대시보드 통계."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import func, select

from app.models.control.group import Group
from app.models.pg.channel import Channel
from app.models.pg.tag import Tag
from app.models.pg.video import Video
from app.routers.deps import get_group_or_404
from app.schemas.stats import StatsOut
from app.services.db_engine import data_plane_engine_manager as dpm

router = APIRouter(prefix="/api/groups/{slug}/stats", tags=["stats"])


@router.get("", response_model=StatsOut)
async def get_stats(group: Group = Depends(get_group_or_404)) -> StatsOut:
    async with dpm.group_session(group) as session:
        total_channels = (
            await session.execute(select(func.count()).select_from(Channel))
        ).scalar_one()
        active_channels = (
            await session.execute(
                select(func.count()).select_from(Channel).where(Channel.is_active.is_(True))
            )
        ).scalar_one()
        total_videos = (
            await session.execute(select(func.count()).select_from(Video))
        ).scalar_one()
        analyzed_videos = (
            await session.execute(
                select(func.count()).select_from(Video).where(Video.analysis_status == "done")
            )
        ).scalar_one()
        pending_videos = (
            await session.execute(
                select(func.count()).select_from(Video).where(Video.analysis_status == "pending")
            )
        ).scalar_one()
        failed_videos = (
            await session.execute(
                select(func.count()).select_from(Video).where(Video.analysis_status == "failed")
            )
        ).scalar_one()
        notified_videos = (
            await session.execute(
                select(func.count()).select_from(Video).where(Video.notified_at.is_not(None))
            )
        ).scalar_one()
        total_tags = (
            await session.execute(select(func.count()).select_from(Tag))
        ).scalar_one()

    return StatsOut(
        total_channels=int(total_channels),
        active_channels=int(active_channels),
        total_videos=int(total_videos),
        analyzed_videos=int(analyzed_videos),
        pending_videos=int(pending_videos),
        failed_videos=int(failed_videos),
        notified_videos=int(notified_videos),
        total_tags=int(total_tags),
    )
