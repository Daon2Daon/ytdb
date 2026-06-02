"""그룹 순회 모니터 서비스.

- 채널 폴링(수집): 활성 그룹을 순회하며 그룹별 엔진/설정으로 due 채널을 폴링하고
  신규 영상을 그룹 스키마에 pending으로 적재한다.
- 미분석 분석: 활성 그룹을 순회하며 그룹별로 pending 1건을 선점(claim)하고
  그룹 AI 컨텍스트로 분석·저장한다.

그룹 격리는 schema_translate_map으로 바인딩된 세션으로 달성한다. claim은 raw 스키마
SQL 대신 ORM의 FOR UPDATE SKIP LOCKED를 사용해 translate map을 그대로 존중한다.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from typing import Callable, List, Optional, Sequence

from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app.control_db import get_sessionmaker
from app.models.control.group import Group
from app.models.pg.channel import Channel
from app.models.pg.deleted_video import DeletedVideo
from app.models.pg.video import Video
from app.models.pg.video_analysis import VideoAnalysis
from app.services.analyzer import build_analysis_pipeline
from app.services.db_engine import DBNotConfiguredError, data_plane_engine_manager as dpm
from app.services.job_logger import (
    JOB_TYPE_CHANNEL_POLL,
    JOB_TYPE_NOTIFY,
    JOB_TYPE_VIDEO_ANALYZE,
    STATUS_FAIL,
    STATUS_SKIP,
    STATUS_SUCCESS,
    JobTimer,
    write_job_log,
)
from app.services.notify_service import notify_video
from app.services.settings_manager import get_settings_manager
from app.services.settings_types import PollingSettings
from app.services.youtube_api import (
    PlaylistItemMeta,
    YouTubeAPIClient,
    YouTubeQuotaExceededError,
)

STALE_PROCESSING_RESET_MINUTES = 180
FAILED_RETRY_MAX = 3
FAILED_RETRY_WAIT_MINUTES = 30

MakeSession = Callable[[], AsyncSession]


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


# ── 그룹 스코프 단위 작업 ──────────────────────────────────────────────────────


class MonitorService:
    """단일 그룹(바인딩된 세션) 범위의 폴링 로직."""

    def __init__(self, polling: PollingSettings) -> None:
        self.polling = polling

    async def list_due_channels(self, session: AsyncSession) -> List[Channel]:
        now = datetime.now(timezone.utc)
        channels = (
            await session.execute(select(Channel).where(Channel.is_active.is_(True)))
        ).scalars().all()
        due: List[Channel] = []
        for ch in channels:
            if ch.last_checked_at is None:
                due.append(ch)
                continue
            lc = ch.last_checked_at
            if lc.tzinfo is None:
                lc = lc.replace(tzinfo=timezone.utc)
            interval = timedelta(
                minutes=int(ch.poll_interval_min or self.polling.default_channel_interval_min)
            )
            if now - lc >= interval:
                due.append(ch)
        return due

    async def process_channel(
        self,
        channel: Channel,
        session: AsyncSession,
        api_client: YouTubeAPIClient,
    ) -> List[int]:
        """채널 폴링 → 신규 영상 INSERT → 새 video_pk 목록 반환.

        polling.window_hours(최신 영상 수집 범위) 안의 영상을 모두 수집한다.
        """
        now = datetime.now(timezone.utc)
        cutoff = now - timedelta(hours=int(self.polling.window_hours or 24))
        items: Sequence[PlaylistItemMeta] = await api_client.get_latest_playlist_items(
            channel.upload_playlist_id,
            published_after=cutoff,
        )
        if not items:
            await self._update_last_checked(session, channel, now)
            return []

        new_ids = await self._filter_new_videos(session, [it.video_id for it in items])
        if not new_ids:
            await self._update_last_checked(session, channel, now, items[0].video_id)
            return []

        metas = await api_client.get_video_details(new_ids)
        metas = [v for v in metas if _parse_iso(v.published_at) >= cutoff]
        if not metas:
            await self._update_last_checked(session, channel, now, items[0].video_id)
            return []

        seq_start = await self._next_sequence(session, channel.channel_pk)
        inserted: List[int] = []
        for idx, vm in enumerate(metas):
            stmt = (
                pg_insert(Video)
                .values(
                    channel_pk=channel.channel_pk,
                    video_id=vm.video_id,
                    video_url=vm.video_url,
                    title=vm.title,
                    description=vm.description,
                    thumbnail_url=vm.thumbnail_url,
                    published_at=_parse_iso(vm.published_at),
                    duration_seconds=_parse_duration(vm.duration),
                    view_count=vm.view_count,
                    like_count=vm.like_count,
                    sequence_in_channel=seq_start + idx,
                    analysis_status="pending",
                    retry_count=0,
                )
                .on_conflict_do_nothing(index_elements=["video_id"])
                .returning(Video.video_pk)
            )
            pk = (await session.execute(stmt)).scalar()
            if pk:
                inserted.append(pk)

        await session.flush()
        await self._update_last_checked(session, channel, now, items[0].video_id)
        return inserted

    async def _filter_new_videos(
        self, session: AsyncSession, video_ids: List[str]
    ) -> List[str]:
        if not video_ids:
            return []
        existing = set(
            (
                await session.execute(
                    select(Video.video_id).where(Video.video_id.in_(video_ids))
                )
            ).scalars()
        )
        deleted = set(
            (
                await session.execute(
                    select(DeletedVideo.video_id).where(DeletedVideo.video_id.in_(video_ids))
                )
            ).scalars()
        )
        return [v for v in video_ids if v not in existing and v not in deleted]

    async def _next_sequence(self, session: AsyncSession, channel_pk: int) -> int:
        max_seq = (
            await session.execute(
                select(func.max(Video.sequence_in_channel)).where(
                    Video.channel_pk == channel_pk
                )
            )
        ).scalar()
        return 1 if max_seq is None else int(max_seq) + 1

    async def _update_last_checked(
        self,
        session: AsyncSession,
        channel: Channel,
        now: datetime,
        last_video_id: Optional[str] = None,
    ) -> None:
        values: dict = {"last_checked_at": now}
        if last_video_id:
            values["last_video_id"] = last_video_id
        await session.execute(
            update(Channel).where(Channel.channel_pk == channel.channel_pk).values(**values)
        )


async def claim_pending_video_pks(session: AsyncSession, limit: int) -> List[int]:
    """pending 행을 FOR UPDATE SKIP LOCKED로 선점하고 processing으로 전환."""
    if limit < 1:
        return []
    picked = (
        await session.execute(
            select(Video.video_pk)
            .where(Video.analysis_status == "pending")
            .order_by(Video.published_at.asc(), Video.video_pk.asc())
            .limit(limit)
            .with_for_update(skip_locked=True)
        )
    ).scalars().all()
    if not picked:
        return []
    await session.execute(
        update(Video).where(Video.video_pk.in_(picked)).values(analysis_status="processing")
    )
    return list(picked)


async def reset_stale_processing_videos(session: AsyncSession, stale_minutes: int) -> int:
    if stale_minutes < 1:
        return 0
    cutoff = datetime.now(timezone.utc) - timedelta(minutes=stale_minutes)
    result = await session.execute(
        update(Video)
        .where(Video.analysis_status == "processing", Video.updated_at < cutoff)
        .values(
            analysis_status="pending",
            analysis_error="[자동복구] 분석 상태가 비정상적으로 지속되어 대기열로 복구",
        )
    )
    return int(result.rowcount or 0)


async def reset_eligible_failed_videos(session: AsyncSession) -> int:
    cutoff = datetime.now(timezone.utc) - timedelta(minutes=FAILED_RETRY_WAIT_MINUTES)
    result = await session.execute(
        update(Video)
        .where(
            Video.analysis_status == "failed",
            Video.retry_count < FAILED_RETRY_MAX,
            Video.updated_at < cutoff,
        )
        .values(analysis_status="pending", analysis_error=None)
    )
    return int(result.rowcount or 0)


# ── 그룹 순회 엔트리포인트 ─────────────────────────────────────────────────────


async def _active_groups() -> List[Group]:
    sf = get_sessionmaker()
    async with sf() as session:
        return list(
            (await session.execute(select(Group).where(Group.is_active.is_(True)))).scalars().all()
        )


def _make_session_factory(engine: AsyncEngine, schema: str) -> MakeSession:
    return lambda: dpm.session_for_group(engine, schema)


async def _poll_group(group: Group) -> None:
    mgr = get_settings_manager()
    polling = await mgr.get_polling(group.group_id)
    if not polling.youtube_api_key:
        print(f"[{group.slug}] YouTube API 키 미설정 - 폴링 SKIP")
        return
    try:
        await dpm.ensure_schema(group)
        engine = await dpm.get_engine_for_group(group)
    except DBNotConfiguredError:
        print(f"[{group.slug}] DB 미설정 - 폴링 SKIP")
        return

    make_session = _make_session_factory(engine, group.schema_name)
    service = MonitorService(polling=polling)

    async with make_session() as session:
        due = await service.list_due_channels(session)
    if not due:
        return

    print(f"[{group.slug}] 폴링 시작: {len(due)}개 채널")
    sem = asyncio.Semaphore(int(polling.max_concurrent_channels or 5))
    api_client = YouTubeAPIClient(polling)

    async def _one(channel: Channel) -> None:
        async with sem:
            timer = JobTimer()
            with timer:
                try:
                    async with make_session() as sess:
                        async with sess.begin():
                            new_pks = await service.process_channel(channel, sess, api_client)
                    await write_job_log(
                        make_session,
                        job_type=JOB_TYPE_CHANNEL_POLL,
                        status=STATUS_SUCCESS,
                        message=f"신규 영상 {len(new_pks)}건 수집" if new_pks else "신규 영상 없음",
                        duration_ms=timer.elapsed_ms,
                        channel_pk=channel.channel_pk,
                    )
                except YouTubeQuotaExceededError as e:
                    await write_job_log(
                        make_session,
                        job_type=JOB_TYPE_CHANNEL_POLL,
                        status=STATUS_SKIP,
                        message=f"쿼터 초과: {e}",
                        duration_ms=timer.elapsed_ms,
                        channel_pk=channel.channel_pk,
                    )
                except Exception as e:
                    print(f"[{group.slug}] 채널 처리 실패 (channel_pk={channel.channel_pk}): {e}")
                    await write_job_log(
                        make_session,
                        job_type=JOB_TYPE_CHANNEL_POLL,
                        status=STATUS_FAIL,
                        message=str(e),
                        duration_ms=timer.elapsed_ms,
                        channel_pk=channel.channel_pk,
                    )

    try:
        await asyncio.gather(*[_one(ch) for ch in due], return_exceptions=True)
    finally:
        await api_client.aclose()


async def run_master_poll_once() -> None:
    """전역 마스터 폴링: 활성 그룹 순회 → 채널 폴링."""
    groups = await _active_groups()
    if not groups:
        return
    for group in groups:
        try:
            await _poll_group(group)
        except Exception as e:
            print(f"[{group.slug}] 폴링 그룹 처리 오류: {e}")


async def _notify_after_analysis(
    group: Group,
    make_session: MakeSession,
    video_pk: int,
    channel_pk: Optional[int],
) -> None:
    """분석 트랜잭션 커밋 후 호출. 그룹 알림이 설정돼 있으면 발송한다.

    네트워크 호출이 DB 트랜잭션을 잡지 않도록 커밋 이후 별도로 수행한다.
    chat_id 미설정/비활성이면 조용히 건너뛴다(데이터만 기록).
    """
    notif = await get_settings_manager().get_notification(group.group_id)
    if not notif.is_sendable:
        return
    async with make_session() as sess:
        channel = None
        if channel_pk is not None:
            channel = (
                await sess.execute(select(Channel).where(Channel.channel_pk == channel_pk))
            ).scalar_one_or_none()
        video = (
            await sess.execute(select(Video).where(Video.video_pk == video_pk))
        ).scalar_one_or_none()
        analysis = (
            await sess.execute(select(VideoAnalysis).where(VideoAnalysis.video_pk == video_pk))
        ).scalar_one_or_none()
    if channel is not None and not channel.notify_enabled:
        await write_job_log(
            make_session,
            job_type=JOB_TYPE_NOTIFY,
            status=STATUS_SKIP,
            message="채널 알림 비활성(notify_enabled)",
            channel_pk=channel_pk,
            video_pk=video_pk,
        )
        return
    if not video or not analysis:
        return

    timer = JobTimer()
    try:
        with timer:
            sent = await notify_video(notif, video, analysis)
        async with make_session() as sess:
            async with sess.begin():
                await sess.execute(
                    update(Video)
                    .where(Video.video_pk == video_pk)
                    .values(notified_at=datetime.now(timezone.utc))
                )
        await write_job_log(
            make_session,
            job_type=JOB_TYPE_NOTIFY,
            status=STATUS_SUCCESS,
            message=f"{sent}개 채널 발송",
            duration_ms=timer.elapsed_ms,
            channel_pk=channel_pk,
            video_pk=video_pk,
        )
    except Exception as e:
        print(f"[{group.slug}] 알림 발송 실패 (video_pk={video_pk}): {e}")
        await write_job_log(
            make_session,
            job_type=JOB_TYPE_NOTIFY,
            status=STATUS_FAIL,
            message=str(e),
            duration_ms=timer.elapsed_ms,
            channel_pk=channel_pk,
            video_pk=video_pk,
        )


async def _analyze_group(group: Group) -> None:
    mgr = get_settings_manager()
    polling = await mgr.get_polling(group.group_id)
    try:
        await dpm.ensure_schema(group)
        engine = await dpm.get_engine_for_group(group)
    except DBNotConfiguredError:
        return

    make_session = _make_session_factory(engine, group.schema_name)

    async with make_session() as sess:
        async with sess.begin():
            await reset_stale_processing_videos(sess, STALE_PROCESSING_RESET_MINUTES)
            await reset_eligible_failed_videos(sess)
            claimed = await claim_pending_video_pks(sess, 1)
    if not claimed:
        return

    video_pk = claimed[0]
    pipeline = await build_analysis_pipeline(group.group_id)
    timer = JobTimer()
    title: Optional[str] = None
    channel_pk: Optional[int] = None
    try:
        with timer:
            async with make_session() as sess:
                async with sess.begin():
                    video = (
                        await sess.execute(select(Video).where(Video.video_pk == video_pk))
                    ).scalar_one_or_none()
                    if not video:
                        return
                    title, channel_pk = video.title, video.channel_pk
                    channel = (
                        await sess.execute(
                            select(Channel).where(Channel.channel_pk == video.channel_pk)
                        )
                    ).scalar_one_or_none()
                    await pipeline.run_and_save(
                        session=sess,
                        video_pk=video_pk,
                        video_url=video.video_url,
                        channel_name=channel.channel_name if channel else "",
                        published_at_str=video.published_at.isoformat(),
                    )
        await write_job_log(
            make_session,
            job_type=JOB_TYPE_VIDEO_ANALYZE,
            status=STATUS_SUCCESS,
            message=f"분석 완료 - {title}" if title else "분석 완료",
            duration_ms=timer.elapsed_ms,
            channel_pk=channel_pk,
            video_pk=video_pk,
        )
        await _notify_after_analysis(group, make_session, video_pk, channel_pk)
    except Exception as e:
        print(f"[{group.slug}] 분석 실패 (video_pk={video_pk}): {e}")
        # 메인 트랜잭션이 롤백되므로 별도 세션으로 실패 상태/재시도 횟수를 기록한다.
        try:
            async with make_session() as fs:
                async with fs.begin():
                    await fs.execute(
                        update(Video)
                        .where(Video.video_pk == video_pk)
                        .values(
                            analysis_status="failed",
                            analysis_error=str(e)[:500],
                            retry_count=Video.retry_count + 1,
                        )
                    )
        except Exception as upd:
            print(f"[{group.slug}] 실패 상태 기록 오류 (video_pk={video_pk}): {upd}")
        await write_job_log(
            make_session,
            job_type=JOB_TYPE_VIDEO_ANALYZE,
            status=STATUS_FAIL,
            message=str(e),
            duration_ms=timer.elapsed_ms,
            channel_pk=channel_pk,
            video_pk=video_pk,
        )
    finally:
        await pipeline._llm.aclose()


async def run_pending_analysis_once() -> None:
    """전역 미분석 분석: 활성 그룹 순회 → 그룹별 pending 1건 분석."""
    groups = await _active_groups()
    if not groups:
        return
    for group in groups:
        try:
            await _analyze_group(group)
        except Exception as e:
            print(f"[{group.slug}] 분석 그룹 처리 오류: {e}")


async def poll_group(group: Group) -> None:
    """단일 그룹 채널 폴링(수동 트리거용)."""
    await _poll_group(group)


async def analyze_group(group: Group) -> None:
    """단일 그룹 pending 1건 분석(수동 트리거용)."""
    await _analyze_group(group)


async def poll_single_channel(group: Group, channel_pk: int) -> None:
    """단일 채널을 즉시 폴링한다(수동 트리거용). _poll_group의 1채널 버전."""
    mgr = get_settings_manager()
    polling = await mgr.get_polling(group.group_id)
    if not polling.youtube_api_key:
        print(f"[{group.slug}] YouTube API 키 미설정 - 단건 폴링 SKIP")
        return
    try:
        await dpm.ensure_schema(group)
        engine = await dpm.get_engine_for_group(group)
    except DBNotConfiguredError:
        print(f"[{group.slug}] DB 미설정 - 단건 폴링 SKIP")
        return

    make_session = _make_session_factory(engine, group.schema_name)
    service = MonitorService(polling=polling)

    async with make_session() as session:
        channel = (
            await session.execute(select(Channel).where(Channel.channel_pk == channel_pk))
        ).scalar_one_or_none()
    if channel is None:
        print(f"[{group.slug}] 채널 없음(channel_pk={channel_pk}) - 단건 폴링 SKIP")
        return

    api_client = YouTubeAPIClient(polling)
    timer = JobTimer()
    try:
        with timer:
            async with make_session() as sess:
                async with sess.begin():
                    new_pks = await service.process_channel(channel, sess, api_client)
        await write_job_log(
            make_session,
            job_type=JOB_TYPE_CHANNEL_POLL,
            status=STATUS_SUCCESS,
            message=f"신규 영상 {len(new_pks)}건 수집" if new_pks else "신규 영상 없음",
            duration_ms=timer.elapsed_ms,
            channel_pk=channel.channel_pk,
        )
    except YouTubeQuotaExceededError as e:
        await write_job_log(
            make_session,
            job_type=JOB_TYPE_CHANNEL_POLL,
            status=STATUS_SKIP,
            message=f"쿼터 초과: {e}",
            duration_ms=timer.elapsed_ms,
            channel_pk=channel.channel_pk,
        )
    except Exception as e:  # noqa: BLE001
        await write_job_log(
            make_session,
            job_type=JOB_TYPE_CHANNEL_POLL,
            status=STATUS_FAIL,
            message=str(e),
            duration_ms=timer.elapsed_ms,
            channel_pk=channel.channel_pk,
        )
    finally:
        await api_client.aclose()


async def analyze_specific_video(group: Group, video_pk: int) -> None:
    """단일 그룹에서 특정 영상 1건을 즉시 분석한다(수동 등록용)."""
    try:
        await dpm.ensure_schema(group)
        engine = await dpm.get_engine_for_group(group)
    except DBNotConfiguredError:
        return

    make_session = _make_session_factory(engine, group.schema_name)
    pipeline = await build_analysis_pipeline(group.group_id)
    timer = JobTimer()
    title: Optional[str] = None
    channel_pk: Optional[int] = None
    try:
        with timer:
            async with make_session() as sess:
                async with sess.begin():
                    video = (
                        await sess.execute(select(Video).where(Video.video_pk == video_pk))
                    ).scalar_one_or_none()
                    if not video:
                        return
                    title, channel_pk = video.title, video.channel_pk
                    channel = (
                        await sess.execute(
                            select(Channel).where(Channel.channel_pk == video.channel_pk)
                        )
                    ).scalar_one_or_none()
                    await pipeline.run_and_save(
                        session=sess,
                        video_pk=video_pk,
                        video_url=video.video_url,
                        channel_name=channel.channel_name if channel else "",
                        published_at_str=video.published_at.isoformat(),
                    )
        await write_job_log(
            make_session,
            job_type=JOB_TYPE_VIDEO_ANALYZE,
            status=STATUS_SUCCESS,
            message=f"즉시 분석 완료 - {title}" if title else "즉시 분석 완료",
            duration_ms=timer.elapsed_ms,
            channel_pk=channel_pk,
            video_pk=video_pk,
        )
        await _notify_after_analysis(group, make_session, video_pk, channel_pk)
    except Exception as e:
        print(f"[{group.slug}] 즉시 분석 실패 (video_pk={video_pk}): {e}")
        try:
            async with make_session() as fs:
                async with fs.begin():
                    await fs.execute(
                        update(Video)
                        .where(Video.video_pk == video_pk)
                        .values(
                            analysis_status="failed",
                            analysis_error=str(e)[:500],
                            retry_count=Video.retry_count + 1,
                        )
                    )
        except Exception as upd:
            print(f"[{group.slug}] 즉시 분석 실패 상태 기록 오류 (video_pk={video_pk}): {upd}")
        await write_job_log(
            make_session,
            job_type=JOB_TYPE_VIDEO_ANALYZE,
            status=STATUS_FAIL,
            message=str(e),
            duration_ms=timer.elapsed_ms,
            channel_pk=channel_pk,
            video_pk=video_pk,
        )
    finally:
        await pipeline._llm.aclose()
