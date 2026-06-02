"""그룹 영상/분석 조회·관리 API."""

from __future__ import annotations

import re
from datetime import datetime, timezone
from urllib.parse import parse_qs, urlparse

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.models.control.group import Group
from app.models.pg.channel import Channel
from app.models.pg.deleted_video import DeletedVideo
from app.models.pg.tag import Tag, VideoTag
from app.models.pg.video import Video
from app.models.pg.video_analysis import VideoAnalysis
from app.routers.deps import get_group_or_404
from app.schemas.stats import PaginatedVideos
from app.schemas.video import (
    AnalysisOut,
    InstantAnalyzeRequest,
    InstantAnalyzeResponse,
    VideoDetail,
    VideoListItem,
)
from app.services.db_engine import data_plane_engine_manager as dpm
from app.services.monitor_service import analyze_specific_video
from app.services.settings_manager import get_settings_manager
from app.services.youtube_api import YouTubeAPIClient, YouTubeAPIError

router = APIRouter(prefix="/api/groups/{slug}/videos", tags=["videos"])
_VIDEO_ID_RE = re.compile(r"^[A-Za-z0-9_-]{11}$")
_INSTANT_CHANNEL_ID = "__instant__"


def _extract_video_id(raw: str) -> str | None:
    s = (raw or "").strip()
    if not s:
        return None
    if _VIDEO_ID_RE.match(s):
        return s
    if not s.startswith(("http://", "https://")):
        return None
    p = urlparse(s)
    host = (p.netloc or "").lower()
    path = p.path or ""
    if "youtu.be" in host:
        part = path.strip("/").split("/")[0]
        return part or None
    if path.startswith("/watch"):
        v = parse_qs(p.query).get("v", [""])[0]
        return v or None
    if path.startswith("/shorts/"):
        return path.split("/shorts/")[1].split("?")[0] or None
    if path.startswith("/embed/"):
        return path.split("/embed/")[1].split("?")[0] or None
    return None


def _parse_iso(dt_str: str | None) -> datetime:
    if not dt_str:
        return datetime.now(timezone.utc)
    try:
        return datetime.fromisoformat(dt_str.replace("Z", "+00:00"))
    except Exception:
        return datetime.now(timezone.utc)


def _parse_duration(iso_duration: str | None) -> int | None:
    if not iso_duration:
        return None
    try:
        import isodate

        return int(isodate.parse_duration(iso_duration).total_seconds())
    except Exception:
        return None


async def _ensure_instant_channel(session, default_interval_min: int) -> Channel:
    row = (
        await session.execute(select(Channel).where(Channel.channel_id == _INSTANT_CHANNEL_ID))
    ).scalar_one_or_none()
    if row is not None:
        return row
    ch = Channel(
        channel_id=_INSTANT_CHANNEL_ID,
        channel_name="Instant Analyze",
        channel_handle=None,
        upload_playlist_id=_INSTANT_CHANNEL_ID,
        thumbnail_url=None,
        description="URL 단건 분석용 가상 채널",
        category="instant",
        poll_interval_min=default_interval_min,
        is_active=False,
        notify_enabled=False,
    )
    session.add(ch)
    await session.flush()
    return ch


def _page_number(limit: int, offset: int) -> int:
    if limit <= 0:
        return 1
    return offset // limit + 1


@router.get("", response_model=list[VideoListItem] | PaginatedVideos)
async def list_videos(
    group: Group = Depends(get_group_or_404),
    status: str | None = Query(None, description="analysis_status 필터"),
    tag: str | None = Query(None, description="태그명 필터"),
    channel_pk: int | None = Query(None, description="채널 PK 필터"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    paged: bool = Query(False, description="true면 {items,total,page,page_size} 반환"),
):
    async with dpm.group_session(group) as session:
        stmt = (
            select(Video, VideoAnalysis.headline, VideoAnalysis.one_line)
            .outerjoin(VideoAnalysis, VideoAnalysis.video_pk == Video.video_pk)
            .order_by(Video.published_at.desc(), Video.video_pk.desc())
            .limit(limit)
            .offset(offset)
        )
        if status:
            stmt = stmt.where(Video.analysis_status == status)
        if channel_pk is not None:
            stmt = stmt.where(Video.channel_pk == channel_pk)
        if tag:
            stmt = (
                stmt.join(VideoTag, VideoTag.video_pk == Video.video_pk)
                .join(Tag, Tag.tag_pk == VideoTag.tag_pk)
                .where(Tag.name == tag)
            )
        rows = (await session.execute(stmt)).all()

        total = None
        if paged:
            count_stmt = select(func.count()).select_from(Video)
            if status:
                count_stmt = count_stmt.where(Video.analysis_status == status)
            if channel_pk is not None:
                count_stmt = count_stmt.where(Video.channel_pk == channel_pk)
            if tag:
                count_stmt = (
                    count_stmt.join(VideoTag, VideoTag.video_pk == Video.video_pk)
                    .join(Tag, Tag.tag_pk == VideoTag.tag_pk)
                    .where(Tag.name == tag)
                )
            total = (await session.execute(count_stmt)).scalar_one()

    items: list[VideoListItem] = []
    for video, headline, one_line in rows:
        item = VideoListItem.model_validate(video)
        item.headline = headline
        item.one_line = one_line
        items.append(item)

    if not paged:
        return items

    return PaginatedVideos(
        total=total,
        page=_page_number(limit, offset),
        page_size=limit,
        items=items,
    )


@router.get("/{video_pk}", response_model=VideoDetail)
async def get_video(
    video_pk: int, group: Group = Depends(get_group_or_404)
) -> VideoDetail:
    async with dpm.group_session(group) as session:
        video = (
            await session.execute(select(Video).where(Video.video_pk == video_pk))
        ).scalar_one_or_none()
        if video is None:
            raise HTTPException(status_code=404, detail="영상을 찾을 수 없습니다.")
        analysis = (
            await session.execute(
                select(VideoAnalysis).where(VideoAnalysis.video_pk == video_pk)
            )
        ).scalar_one_or_none()
        tags = list(
            (
                await session.execute(
                    select(Tag.name)
                    .join(VideoTag, VideoTag.tag_pk == Tag.tag_pk)
                    .where(VideoTag.video_pk == video_pk)
                    .order_by(VideoTag.weight.desc().nullslast(), Tag.name.asc())
                )
            ).scalars().all()
        )

    detail = VideoDetail.model_validate(video)
    detail.tags = tags
    if analysis is not None:
        detail.analysis = AnalysisOut.model_validate(analysis)
    return detail


@router.post("/{video_pk}/reanalyze", response_model=VideoDetail)
async def reanalyze_video(
    video_pk: int, group: Group = Depends(get_group_or_404)
) -> VideoDetail:
    async with dpm.group_session(group) as session:
        async with session.begin():
            result = await session.execute(
                update(Video)
                .where(Video.video_pk == video_pk)
                .values(analysis_status="pending", analysis_error=None, retry_count=0)
            )
            if (result.rowcount or 0) == 0:
                raise HTTPException(status_code=404, detail="영상을 찾을 수 없습니다.")
        video = (
            await session.execute(select(Video).where(Video.video_pk == video_pk))
        ).scalar_one()
    return VideoDetail.model_validate(video)


@router.post("/{video_pk}/analyze-now", status_code=202)
async def analyze_video_now(
    video_pk: int,
    background: BackgroundTasks,
    group: Group = Depends(get_group_or_404),
) -> dict:
    """영상 1건을 스케줄 대기 없이 백그라운드에서 즉시 분석한다."""
    async with dpm.group_session(group) as session:
        async with session.begin():
            result = await session.execute(
                update(Video)
                .where(Video.video_pk == video_pk)
                .values(analysis_status="pending", analysis_error=None, retry_count=0)
            )
            if (result.rowcount or 0) == 0:
                raise HTTPException(status_code=404, detail="영상을 찾을 수 없습니다.")
    background.add_task(analyze_specific_video, group, video_pk)
    return {"status": "started", "video_pk": video_pk}


@router.delete("/{video_pk}", status_code=204)
async def delete_video(
    video_pk: int, group: Group = Depends(get_group_or_404)
) -> None:
    """영상 삭제 + 재수집 방지를 위해 deleted_videos 블록리스트에 등록."""
    async with dpm.group_session(group) as session:
        async with session.begin():
            video = (
                await session.execute(select(Video).where(Video.video_pk == video_pk))
            ).scalar_one_or_none()
            if video is None:
                raise HTTPException(status_code=404, detail="영상을 찾을 수 없습니다.")
            await session.execute(
                pg_insert(DeletedVideo)
                .values(video_id=video.video_id)
                .on_conflict_do_nothing(index_elements=["video_id"])
            )
            await session.delete(video)


@router.post("/instant", response_model=InstantAnalyzeResponse, status_code=202)
async def instant_analyze_video(
    payload: InstantAnalyzeRequest,
    background: BackgroundTasks,
    group: Group = Depends(get_group_or_404),
) -> InstantAnalyzeResponse:
    video_id = _extract_video_id(payload.video_url)
    if not video_id:
        raise HTTPException(status_code=400, detail="유효한 YouTube 영상 URL/ID가 아닙니다.")

    polling = await get_settings_manager().get_polling(group.group_id)
    if not polling.youtube_api_key:
        raise HTTPException(
            status_code=400, detail="YouTube API 키(polling.youtube_api_key)를 먼저 설정하세요."
        )

    async with dpm.group_session(group) as session:
        async with session.begin():
            existing = (
                await session.execute(select(Video).where(Video.video_id == video_id))
            ).scalar_one_or_none()
            if existing is not None:
                await session.execute(
                    update(Video)
                    .where(Video.video_pk == existing.video_pk)
                    .values(analysis_status="pending", analysis_error=None, retry_count=0)
                )
                video_pk = existing.video_pk
                background.add_task(analyze_specific_video, group, video_pk)
                return InstantAnalyzeResponse(
                    video_pk=video_pk, video_id=video_id, existing=True, queued=True
                )

            api = YouTubeAPIClient(polling)
            try:
                metas = await api.get_video_details([video_id])
            except YouTubeAPIError as e:
                await api.aclose()
                raise HTTPException(status_code=400, detail=f"영상 메타 조회 실패: {e}") from e
            finally:
                await api.aclose()

            if not metas:
                raise HTTPException(status_code=404, detail="해당 영상을 찾을 수 없습니다.")
            vm = metas[0]

            channel = (
                await session.execute(select(Channel).where(Channel.channel_id == vm.channel_id))
            ).scalar_one_or_none()
            if channel is None:
                channel = await _ensure_instant_channel(
                    session, polling.default_channel_interval_min or 720
                )

            stmt = (
                pg_insert(Video)
                .values(
                    channel_pk=channel.channel_pk,
                    video_id=vm.video_id,
                    video_url=vm.video_url,
                    title=vm.title or vm.video_id,
                    description=vm.description,
                    thumbnail_url=vm.thumbnail_url,
                    published_at=_parse_iso(vm.published_at),
                    duration_seconds=_parse_duration(vm.duration),
                    view_count=vm.view_count,
                    like_count=vm.like_count,
                    sequence_in_channel=None,
                    analysis_status="pending",
                    retry_count=0,
                    source_channel_name=vm.channel_title,
                )
                .on_conflict_do_nothing(index_elements=["video_id"])
                .returning(Video.video_pk)
            )
            inserted_pk = (await session.execute(stmt)).scalar_one_or_none()
            if inserted_pk is None:
                found = (
                    await session.execute(select(Video).where(Video.video_id == video_id))
                ).scalar_one()
                video_pk = found.video_pk
            else:
                video_pk = inserted_pk

    background.add_task(analyze_specific_video, group, video_pk)
    return InstantAnalyzeResponse(video_pk=video_pk, video_id=video_id, existing=False, queued=True)
