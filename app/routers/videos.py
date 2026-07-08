"""그룹 영상/분석 조회·관리 API."""

from __future__ import annotations

import re
from dataclasses import replace
from datetime import datetime, timezone
from typing import Optional
from urllib.parse import parse_qs, urlparse

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
from pydantic import BaseModel
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
from app.services.global_settings import resolve_youtube_key
from app.services.job_logger import JOB_TYPE_NOTIFY, STATUS_SKIP, write_job_log
from app.services.monitor_service import analyze_specific_video
from app.services.notify_service import (
    NOTIFY_SOURCE_TELEGRAM,
    NOTIFY_SOURCE_WEB,
    mark_video_notified,
    notify_video,
)
from app.services.settings_manager import get_settings_manager
from app.services.youtube_api import YouTubeAPIClient, YouTubeAPIError
from app.services.yt_parsing import parse_duration_seconds, parse_iso_datetime

router = APIRouter(prefix="/api/groups/{slug}/videos", tags=["videos"])
_VIDEO_ID_RE = re.compile(r"^[A-Za-z0-9_-]{11}$")
_INSTANT_CHANNEL_ID = "__instant__"


class AnalyzeNowRequest(BaseModel):
    custom_prompt: Optional[str] = None


class NotifyRequest(BaseModel):
    force: bool = False


class VideoNotifyResponse(BaseModel):
    success: bool
    message: str
    notified_at: Optional[datetime] = None
    notify_source: Optional[str] = None


class NotifyPreviewResponse(BaseModel):
    text: str


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


async def _resolve_channel_name(session, video) -> str:
    """발송 메시지 헤더용 채널명.

    추가(instant) 영상은 가상 채널명("Instant Analyze") 대신 실제 채널명이 담긴
    source_channel_name을 우선 쓰고, 모니터링 영상은 source가 비어 있으므로 실제
    채널명으로 폴백한다.
    """
    name = getattr(video, "source_channel_name", "") or ""
    if not name and video.channel_pk is not None:
        channel = (
            await session.execute(select(Channel).where(Channel.channel_pk == video.channel_pk))
        ).scalar_one_or_none()
        name = getattr(channel, "channel_name", "") or ""
    return name


async def _build_video_detail(session, video, *, analysis=None, tags: list[str] | None = None) -> VideoDetail:
    detail = VideoDetail.model_validate(video)
    detail.tags = tags or []
    if analysis is not None:
        detail.analysis = AnalysisOut.model_validate(analysis)
    detail.channel_name = await _resolve_channel_name(session, video) or None
    return detail


@router.get("", response_model=list[VideoListItem] | PaginatedVideos)
async def list_videos(
    group: Group = Depends(get_group_or_404),
    status: str | None = Query(None, description="analysis_status 필터"),
    tag: str | None = Query(None, description="태그명 필터"),
    channel_pk: int | None = Query(None, description="채널 PK 필터"),
    notified: str | None = Query(None, description="발송 필터: 'yes'=발송완료, 'no'=미발송"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    paged: bool = Query(False, description="true면 {items,total,page,page_size} 반환"),
):
    # 본 쿼리와 count 쿼리에 동일하게 적용할 필터를 한 번만 정의한다.
    conditions = []
    if status:
        conditions.append(Video.analysis_status == status)
    if channel_pk is not None:
        conditions.append(Video.channel_pk == channel_pk)
    if notified == "yes":
        conditions.append(Video.notified_at.is_not(None))
    elif notified == "no":
        conditions.append(Video.notified_at.is_(None))

    def _apply_tag_join(q):
        return (
            q.join(VideoTag, VideoTag.video_pk == Video.video_pk)
            .join(Tag, Tag.tag_pk == VideoTag.tag_pk)
            .where(Tag.name == tag)
        )

    async with dpm.group_session(group) as session:
        stmt = select(Video, VideoAnalysis.headline, VideoAnalysis.one_line).outerjoin(
            VideoAnalysis, VideoAnalysis.video_pk == Video.video_pk
        )
        if tag:
            stmt = _apply_tag_join(stmt)
        stmt = (
            stmt.where(*conditions)
            .order_by(Video.published_at.desc(), Video.video_pk.desc())
            .limit(limit)
            .offset(offset)
        )
        rows = (await session.execute(stmt)).all()

        total = None
        if paged:
            count_stmt = select(func.count()).select_from(Video)
            if tag:
                count_stmt = _apply_tag_join(count_stmt)
            count_stmt = count_stmt.where(*conditions)
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


@router.post("/reset-failed")
async def reset_failed_videos(group: Group = Depends(get_group_or_404)) -> dict:
    """실패(failed) 영상을 전부 pending으로 되돌리고 retry_count를 초기화한다.

    영구 failed(retry_count >= 한도)를 포함해 다시 분석 대기열에 올린다.
    retry_count를 0으로 리셋해야 새로 재시도 기회를 받는다.
    """
    async with dpm.group_session(group) as session:
        async with session.begin():
            result = await session.execute(
                update(Video)
                .where(Video.analysis_status == "failed")
                .values(analysis_status="pending", analysis_error=None, retry_count=0)
            )
    return {"reset": int(result.rowcount or 0)}


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

        return await _build_video_detail(session, video, analysis=analysis, tags=tags)


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
        return await _build_video_detail(session, video)


@router.post("/{video_pk}/analyze-now", status_code=202)
async def analyze_video_now(
    video_pk: int,
    background: BackgroundTasks,
    payload: AnalyzeNowRequest | None = None,
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
    custom = payload.custom_prompt if payload else None
    background.add_task(analyze_specific_video, group, video_pk, custom)
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


@router.post("/{video_pk}/notify", response_model=VideoNotifyResponse)
async def notify_video_now(
    video_pk: int,
    payload: NotifyRequest | None = None,
    group: Group = Depends(get_group_or_404),
) -> VideoNotifyResponse:
    force = payload.force if payload else False
    notif = await get_settings_manager().get_notification(group.group_id)
    async with dpm.group_session(group) as session:
        video = (
            await session.execute(select(Video).where(Video.video_pk == video_pk))
        ).scalar_one_or_none()
        if video is None:
            raise HTTPException(status_code=404, detail="영상을 찾을 수 없습니다.")
        if video.analysis_status != "done":
            raise HTTPException(status_code=400, detail="분석이 완료된 영상만 발송할 수 있습니다.")
        analysis = (
            await session.execute(select(VideoAnalysis).where(VideoAnalysis.video_pk == video_pk))
        ).scalar_one_or_none()
        if analysis is None:
            raise HTTPException(status_code=400, detail="분석 결과가 없어 발송할 수 없습니다.")
        if video.notified_at is not None and not force:
            return VideoNotifyResponse(
                success=False,
                message="이미 발송된 영상입니다. 재발송하려면 force=true로 요청하세요.",
                notified_at=video.notified_at,
                notify_source=video.notify_source,
            )
        channel_name = await _resolve_channel_name(session, video)

    # 네트워크 발송은 트랜잭션 밖에서 수행한다(읽기 세션은 위에서 이미 닫힘).
    from app.services.notify_service import _fetch_video_tags
    make_session = lambda: dpm.group_session(group)
    try:
        tags = await _fetch_video_tags(make_session, video_pk)
    except Exception:
        tags = []
    try:
        sent = await notify_video(
            notif, video, analysis,
            channel_name=channel_name,
            tags=tags, template=notif.message_template,
            group_slug=group.slug,
        )
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=f"발송 실패: {e}") from e
    if sent == 0:
        raise HTTPException(status_code=400, detail="발송된 메시지가 없습니다. 알림 설정(봇 토큰/Chat ID)을 확인하세요.")

    now = datetime.now(timezone.utc)
    async with dpm.group_session(group) as write_session:
        async with write_session.begin():
            await mark_video_notified(write_session, video_pk, NOTIFY_SOURCE_TELEGRAM, now=now)
    return VideoNotifyResponse(
        success=True,
        message=f"{sent}개 대상에 발송했습니다.",
        notified_at=now,
        notify_source=NOTIFY_SOURCE_TELEGRAM,
    )


@router.post("/{video_pk}/ack-notify", response_model=VideoNotifyResponse)
async def ack_notify_video(
    video_pk: int,
    group: Group = Depends(get_group_or_404),
) -> VideoNotifyResponse:
    """Telegram 발송 없이 웹에서 확인 처리한다. 자동 발송 대상에서 제외된다."""
    make_session = lambda: dpm.group_session(group)
    async with dpm.group_session(group) as session:
        video = (
            await session.execute(select(Video).where(Video.video_pk == video_pk))
        ).scalar_one_or_none()
        if video is None:
            raise HTTPException(status_code=404, detail="영상을 찾을 수 없습니다.")
        if video.analysis_status != "done":
            raise HTTPException(
                status_code=400, detail="분석이 완료된 영상만 확인 처리할 수 있습니다."
            )
        if video.notified_at is not None:
            return VideoNotifyResponse(
                success=True,
                message="이미 처리된 영상입니다.",
                notified_at=video.notified_at,
                notify_source=video.notify_source,
            )

    now = datetime.now(timezone.utc)
    async with dpm.group_session(group) as write_session:
        async with write_session.begin():
            await mark_video_notified(write_session, video_pk, NOTIFY_SOURCE_WEB, now=now)
    await write_job_log(
        make_session,
        job_type=JOB_TYPE_NOTIFY,
        status=STATUS_SKIP,
        message="웹 확인으로 발송 생략",
        video_pk=video_pk,
    )
    return VideoNotifyResponse(
        success=True,
        message="웹에서 확인 처리했습니다. Telegram 발송은 생략됩니다.",
        notified_at=now,
        notify_source=NOTIFY_SOURCE_WEB,
    )


@router.get("/{video_pk}/notify-preview", response_model=NotifyPreviewResponse)
async def notify_preview(
    video_pk: int, group: Group = Depends(get_group_or_404)
) -> NotifyPreviewResponse:
    """현재 그룹의 메시지 템플릿으로 실제 발송될 텔레그램 본문(HTML)을 그대로 렌더한다.

    실발송 경로(build_message)와 동일한 빌더·렌더러를 사용하므로 미리보기와 실발송이
    일치한다. 분석 결과가 없으면 빈 문자열을 반환한다.
    """
    from app.services.notify_service import _fetch_video_tags, build_message

    notif = await get_settings_manager().get_notification(group.group_id)
    async with dpm.group_session(group) as session:
        video = (
            await session.execute(select(Video).where(Video.video_pk == video_pk))
        ).scalar_one_or_none()
        if video is None:
            raise HTTPException(status_code=404, detail="영상을 찾을 수 없습니다.")
        analysis = (
            await session.execute(select(VideoAnalysis).where(VideoAnalysis.video_pk == video_pk))
        ).scalar_one_or_none()
        channel_name = await _resolve_channel_name(session, video)

    if analysis is None:
        return NotifyPreviewResponse(text="")

    make_session = lambda: dpm.group_session(group)
    try:
        tags = await _fetch_video_tags(make_session, video_pk)
    except Exception:
        tags = []
    text = build_message(
        video, analysis, notif.low_confidence_threshold,
        channel_name=channel_name, tags=tags,
        template=notif.message_template, group_slug=group.slug,
    )
    return NotifyPreviewResponse(text=text)


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
    api_key = await resolve_youtube_key(group.group_id)
    if not api_key:
        raise HTTPException(
            status_code=400,
            detail="YouTube API 키가 없습니다. 그룹 polling 설정 또는 시스템 전역 키를 설정하세요.",
        )
    polling = replace(polling, youtube_api_key=api_key)

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
                    published_at=parse_iso_datetime(vm.published_at),
                    duration_seconds=parse_duration_seconds(vm.duration),
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
