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
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from typing import Callable, List, Optional, Sequence

from sqlalchemy import func, or_, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app.control_db import get_sessionmaker
from app.models.control.group import Group
from app.models.control.user import User
from app.models.pg.channel import Channel
from app.models.pg.deleted_video import DeletedVideo
from app.models.pg.video import Video
from app.models.pg.video_analysis import VideoAnalysis
from app.services.analysis_cache_service import (
    claim_or_get_cached,
    complete_cached,
    fail_cached,
    record_delivery_for,
)
from app.services.ai_usage_service import budget_ok_for_group, record_usage
from app.services.analyzer import build_analysis_pipeline, result_from_cache, save_analysis_to_group
from app.services.db_engine import DBNotConfiguredError, data_plane_engine_manager as dpm
from app.services.records_extractor import run_records_extraction
from app.services.global_settings import resolve_ai_gateway, resolve_youtube_key
from app.services.job_logger import (
    JOB_TYPE_CHANNEL_POLL,
    JOB_TYPE_NOTIFY,
    JOB_TYPE_STATS,
    JOB_TYPE_VIDEO_ANALYZE,
    STATUS_FAIL,
    STATUS_SKIP,
    STATUS_SUCCESS,
    JobTimer,
    write_job_log,
)
from app.services.notify_service import (
    NOTIFY_SOURCE_TELEGRAM,
    NOTIFY_SOURCE_WEB,
    mark_video_notified,
    notify_video,
    resolve_notify_target,
)
from app.services.preset_service import ResolvedPrompts, resolve_prompts
from app.services.quota_service import (
    check_video_duration,
    count_daily_deliveries,
    limits_for_group_owner,
)
from app.services.settings_manager import get_settings_manager
from app.services.settings_types import PollingSettings
from app.services.yt_parsing import parse_duration_seconds, parse_iso_datetime
from app.services.youtube_api import (
    VideoMeta,
    YouTubeAPIClient,
    YouTubeQuotaExceededError,
)
from app.services.yt_quota_service import make_recorder

STALE_PROCESSING_RESET_MINUTES = 180
FAILED_RETRY_MAX = 3
FAILED_RETRY_WAIT_MINUTES = 30

MakeSession = Callable[[], AsyncSession]


# ── 그룹 스코프 단위 작업 ──────────────────────────────────────────────────────


async def fetch_channel_updates(
    api_client: YouTubeAPIClient, upload_playlist_id: str, cutoff: datetime
) -> List[VideoMeta]:
    """채널 업로드 목록 조회 → 상세 일괄 조회. 그룹 무관 — 중앙 폴러가 채널당 1회 호출.

    상세 조회는 window 내 전체 항목 대상(그룹별 '신규' 판정은 삽입 단계 몫).
    videos.list는 50개당 1유닛이라 쿼터 영향 무시 가능.
    반환은 published_at 내림차순(최신 먼저) — latest_video_id/채번이 응답 순서에 의존하지 않도록 보장.
    """
    items = await api_client.get_latest_playlist_items(
        upload_playlist_id, published_after=cutoff
    )
    if not items:
        return []
    metas = await api_client.get_video_details([it.video_id for it in items])
    metas = [m for m in metas if parse_iso_datetime(m.published_at) >= cutoff]
    metas.sort(key=lambda m: parse_iso_datetime(m.published_at), reverse=True)
    return metas


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
        metas = await fetch_channel_updates(api_client, channel.upload_playlist_id, cutoff)
        return await self.insert_group_videos(channel, session, metas, cutoff, now=now)

    async def insert_group_videos(
        self,
        channel: Channel,
        session: AsyncSession,
        metas: Sequence[VideoMeta],
        cutoff: datetime,
        now: Optional[datetime] = None,
    ) -> List[int]:
        """조회 결과를 이 그룹 스키마에 삽입한다 (그룹별 필터·채번·last_checked).

        중앙 폴러 팬아웃과 그룹 스코프 폴링이 공유하는 삽입 경로 — 필터 이중화 금지.
        cutoff: 이 그룹의 window_hours 컷 (중앙 폴러는 최대 윈도로 넓게 조회 후 재컷).
        """
        now = now or datetime.now(timezone.utc)
        latest_video_id = metas[0].video_id if metas else None
        metas = [m for m in metas if parse_iso_datetime(m.published_at) >= cutoff]
        if not metas:
            await self._update_last_checked(session, channel, now, latest_video_id)
            return []

        new_ids = await self._filter_new_videos(session, [m.video_id for m in metas])
        by_id = {m.video_id: m for m in metas}
        new_metas = [by_id[v] for v in new_ids]
        if not new_metas:
            await self._update_last_checked(session, channel, now, latest_video_id)
            return []

        seq_start = await self._next_sequence(session, channel.channel_pk)
        inserted: List[int] = []
        for idx, vm in enumerate(new_metas):
            stmt = (
                pg_insert(Video)
                .values(
                    channel_pk=channel.channel_pk,
                    video_id=vm.video_id,
                    video_url=vm.video_url,
                    title=vm.title,
                    description=vm.description,
                    thumbnail_url=vm.thumbnail_url,
                    published_at=parse_iso_datetime(vm.published_at),
                    duration_seconds=parse_duration_seconds(vm.duration),
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
        await self._update_last_checked(session, channel, now, latest_video_id)
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
        stmt = (
            select(Group)
            .outerjoin(User, User.user_id == Group.owner_user_id)
            .where(Group.is_active.is_(True))
            .where(or_(Group.owner_user_id.is_(None), User.status == "active"))
        )
        return list((await session.execute(stmt)).scalars().all())


def _make_session_factory(engine: AsyncEngine, schema: str) -> MakeSession:
    return lambda: dpm.session_for_group(engine, schema)


def _stats_window_cutoff(now: datetime, days: int) -> datetime:
    """stats 갱신 대상 cutoff: now - days일."""
    return now - timedelta(days=days)


def _build_stats_map(metas) -> dict[str, tuple]:
    """VideoMeta 리스트 → {video_id: (view_count, like_count)}."""
    return {m.video_id: (m.view_count, m.like_count) for m in metas if m.video_id}


async def _poll_group(group: Group) -> None:
    mgr = get_settings_manager()
    polling = await mgr.get_polling(group.group_id)
    try:
        api_key = await resolve_youtube_key(group.group_id)
    except YouTubeQuotaExceededError as e:
        print(f"[{group.slug}] 시스템 키 쿼터 소진 - SKIP: {e}")
        return
    if not api_key:
        print(f"[{group.slug}] YouTube API 키 미설정(그룹·시스템 모두) - 폴링 SKIP")
        return
    polling = replace(polling, youtube_api_key=api_key)
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
    api_client = YouTubeAPIClient(polling, recorder=make_recorder(polling.youtube_api_key))

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


def _passes_notify_baseline(notify_from: Optional[datetime], published_at: datetime) -> bool:
    """채널 알림 기준 시점 게이트. notify_from 이후 게시된 영상만 발송.

    notify_from이 None이면(기준 없음) 전부 발송한다(기존 채널 호환).
    """
    if notify_from is None:
        return True
    return published_at >= notify_from


def _passes_group_baseline(
    baseline: Optional[datetime], published_at: datetime
) -> bool:
    """그룹 발송 기준선 게이트. baseline 이후 게시된 영상만 자동 발송.

    채널용과 달리 baseline이 None이면 보류(False)한다. sendable인데 기준선이
    비어 있으면(트리거 누락 등) 과거 backlog가 한꺼번에 나가는 것을 막는다.
    """
    if baseline is None:
        return False
    return published_at >= baseline


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
    notif = await resolve_notify_target(group.owner_user_id, notif)
    if not notif.is_sendable:
        return

    # 예약 발송 모드: 즉시 발송하지 않고 보류(틱이 예약 시각에 일괄 발송).
    if notif.send_mode == "scheduled":
        await write_job_log(
            make_session,
            job_type=JOB_TYPE_NOTIFY,
            status=STATUS_SKIP,
            message="예약발송 대기(send_mode=scheduled)",
            channel_pk=channel_pk,
            video_pk=video_pk,
        )
        return

    # 즉시 발송 + 야간 제한: 보류(틱이 제한 종료 후 보정 발송).
    if notif.quiet_hours_enabled:
        from zoneinfo import ZoneInfo

        from app.services.quiet_hours import is_quiet_hours_now

        try:
            tz = ZoneInfo(notif.timezone)
        except Exception:
            tz = ZoneInfo("Asia/Seoul")
        if is_quiet_hours_now(
            notif.quiet_hours_enabled,
            notif.quiet_hours_start,
            notif.quiet_hours_end,
            tz=tz,
        ):
            await write_job_log(
                make_session,
                job_type=JOB_TYPE_NOTIFY,
                status=STATUS_SKIP,
                message="야간 보류(quiet hours)",
                channel_pk=channel_pk,
                video_pk=video_pk,
            )
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

    # 채널 알림 기준 시점 게이트: 기준 이전에 게시된(백로그) 영상은 자동 발송하지 않는다.
    if channel is not None and not _passes_notify_baseline(channel.notify_from, video.published_at):
        await write_job_log(
            make_session,
            job_type=JOB_TYPE_NOTIFY,
            status=STATUS_SKIP,
            message="기준 시점 이전 영상(알림 baseline)",
            channel_pk=channel_pk,
            video_pk=video_pk,
        )
        return

    # 그룹 발송 기준선 게이트: 발송 활성화 이전 게시분(backlog)은 자동 발송 안 함.
    if not _passes_group_baseline(notif.notify_baseline_at, video.published_at):
        await write_job_log(
            make_session,
            job_type=JOB_TYPE_NOTIFY,
            status=STATUS_SKIP,
            message="그룹 baseline 이전(자동발송 보류)",
            channel_pk=channel_pk,
            video_pk=video_pk,
        )
        return

    timer = JobTimer()
    try:
        with timer:
            from app.services.notify_service import _fetch_video_tags
            try:
                tags = await _fetch_video_tags(make_session, video_pk)
            except Exception:
                tags = []
            sent = await notify_video(
                notif, video, analysis, threshold=notif.low_confidence_threshold,
                channel_name=getattr(channel, "channel_name", "") or "",
                tags=tags, template=notif.message_template,
                group_slug=group.slug,
            )
        async with make_session() as sess:
            async with sess.begin():
                await mark_video_notified(sess, video_pk, NOTIFY_SOURCE_TELEGRAM)
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


async def _load_analysis_for_records(session, video_pk: int) -> dict | None:
    """저장된 분석을 records 추출 입력용 dict로 로드."""
    from sqlalchemy import select
    from app.models.pg.video_analysis import VideoAnalysis
    row = (await session.execute(
        select(
            VideoAnalysis.one_line, VideoAnalysis.analysis_sections,
            VideoAnalysis.insights, VideoAnalysis.key_points,
            VideoAnalysis.entities, VideoAnalysis.sentiment,
        ).where(VideoAnalysis.video_pk == video_pk)
    )).first()
    if row is None:
        return None
    return {
        "one_line": row[0], "analysis_sections": row[1], "insights": row[2],
        "key_points": row[3], "entities": row[4], "sentiment": row[5],
    }


async def _records_post_pass(*, group, make_session, video_pk: int) -> None:
    """분석 저장 후 records 추출을 best-effort 실행. 예외 삼킴."""
    try:
        async with make_session() as sess:
            analysis = await _load_analysis_for_records(sess, video_pk)
        if analysis is None:
            return
        await run_records_extraction(group=group, video_pk=video_pk, analysis=analysis)
    except Exception as e:  # noqa: BLE001
        print(f"[records] post-pass 실패 (video_pk={video_pk}): {e}")


def _should_use_cache(preset_id: Optional[int], custom_prompt: Optional[str]) -> bool:
    """프리셋 그룹만 공유 캐시에 참여. 직접 프롬프트/커스텀 오버라이드는 기존 경로."""
    return preset_id is not None and not custom_prompt


async def _run_analysis(
    group: Group,
    make_session: MakeSession,
    video_pk: int,
    *,
    custom_prompt: Optional[str] = None,
    label: str = "분석",
) -> None:
    """단일 영상 분석 실행 + 성공/실패 로깅 + 커밋 후 알림.

    프리셋 그룹은 공유 분석 캐시(§2.9)를 경유한다: 적중 시 LLM 호출 없이 복사,
    미스 시 선점 후 1회 분석 + 캐시 기록. 직접 프롬프트/커스텀 오버라이드는
    기존 경로 그대로.
    """
    resolved = await resolve_prompts(group.group_id)

    # 영상 메타 조회 (양 경로 공용)
    async with make_session() as sess:
        video = (
            await sess.execute(select(Video).where(Video.video_pk == video_pk))
        ).scalar_one_or_none()
        if not video:
            return
        channel = (
            await sess.execute(
                select(Channel).where(Channel.channel_pk == video.channel_pk)
            )
        ).scalar_one_or_none()
    title, channel_pk = video.title, video.channel_pk
    channel_name = channel.channel_name if channel else ""

    limits = await limits_for_group_owner(group)
    if not check_video_duration(limits, video.duration_seconds):
        assert limits is not None
        async with make_session() as sess:
            async with sess.begin():
                await sess.execute(
                    update(Video)
                    .where(Video.video_pk == video_pk)
                    .values(
                        analysis_status="skipped",
                        analysis_error=(
                            f"영상 길이 초과: {(video.duration_seconds or 0) // 60}분 "
                            f"> 플랜 한도 {limits.max_video_minutes}분"
                        ),
                    )
                )
        await write_job_log(
            make_session,
            job_type=JOB_TYPE_VIDEO_ANALYZE,
            status=STATUS_SKIP,
            message=f"영상 길이 초과(플랜 한도 {limits.max_video_minutes}분)",
            channel_pk=video.channel_pk,
            video_pk=video_pk,
        )
        return

    if _should_use_cache(resolved.preset_id, custom_prompt):
        await _run_analysis_cached(
            group, make_session, video, channel_name, resolved,
            channel_pk=channel_pk, label=label,
        )
        return

    # ── 기존 경로 (직접 프롬프트 / 커스텀 오버라이드) ──────────────────────────
    # 월 예산 게이트 (설계 §7 표 4행): 직접 프롬프트 분석은 owner 귀속 비용.
    # skipped는 재클레임되지 않아 핫루프 없음(duration 게이트와 동일 패턴).
    # 현재 직접 프롬프트는 admin 전용(§3.3)이라 실질 방어선이 아닌 방어적 완결성.
    b_ok, b_reason = await budget_ok_for_group(group)
    if not b_ok:
        async with make_session() as sess:
            async with sess.begin():
                await sess.execute(
                    update(Video)
                    .where(Video.video_pk == video_pk)
                    .values(analysis_status="skipped", analysis_error=b_reason)
                )
        await write_job_log(
            make_session,
            job_type=JOB_TYPE_VIDEO_ANALYZE,
            status=STATUS_SKIP,
            message=b_reason,
            channel_pk=channel_pk,
            video_pk=video_pk,
        )
        return

    pipeline = await build_analysis_pipeline(
        group.group_id, analysis_prompt_override=custom_prompt, resolved=resolved
    )
    timer = JobTimer()
    try:
        with timer:
            async with make_session() as sess:
                async with sess.begin():
                    result = await pipeline.run_and_save(
                        session=sess,
                        video_pk=video_pk,
                        video_url=video.video_url,
                        channel_name=channel_name,
                        published_at_str=video.published_at.isoformat(),
                        duration_seconds=video.duration_seconds,
                    )
        # 직접/커스텀 프롬프트 분석은 캐시 우회 = 그룹 owner 몫 (스펙 §4 표 1행)
        await record_usage(
            user_id=group.owner_user_id,
            group_id=group.group_id,
            purpose="analysis",
            model=result.model_name,
            input_tokens=result.input_tokens,
            output_tokens=result.output_tokens,
            video_pk=video_pk,
        )
        # 직접 경로도 '분석 전달' 사건 — 일일 쿼터·마이페이지 카운트에 포함(캐시 행 없음 = None)
        await _record_delivery_safe(group, None)
        await write_job_log(
            make_session,
            job_type=JOB_TYPE_VIDEO_ANALYZE,
            status=STATUS_SUCCESS,
            message=f"{label} 완료 - {title}" if title else f"{label} 완료",
            duration_ms=timer.elapsed_ms,
            channel_pk=channel_pk,
            video_pk=video_pk,
        )
        await _notify_after_analysis(group, make_session, video_pk, channel_pk)
        await _records_post_pass(group=group, make_session=make_session, video_pk=video_pk)
    except Exception as e:
        print(f"[{group.slug}] {label} 실패 (video_pk={video_pk}): {e}")
        await _mark_video_failed(group, make_session, video_pk, e, label)
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
        await pipeline.aclose()


async def _mark_video_failed(
    group: Group, make_session: MakeSession, video_pk: int, e: Exception, label: str
) -> None:
    """분석 실패 상태 기록 (기존 _run_analysis except 블록에서 추출)."""
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
        print(f"[{group.slug}] {label} 실패 상태 기록 오류 (video_pk={video_pk}): {upd}")


async def _run_analysis_cached(
    group: Group,
    make_session: MakeSession,
    video: Video,
    channel_name: str,
    resolved: ResolvedPrompts,
    *,
    channel_pk: Optional[int],
    label: str,
) -> None:
    """공유 캐시 경유 분석. 적중=복사, 선점=1회 분석+캐시 기록, 진행중=다음 틱 연기."""
    ai = await resolve_ai_gateway(group.group_id)
    video_pk = video.video_pk
    assert resolved.preset_id is not None

    outcome = await claim_or_get_cached(video.video_id, resolved.preset_id, ai.primary_model)

    if outcome.kind == "in_progress":
        # 다른 워커가 분석 중 — 영상을 pending으로 되돌려 다음 틱에 캐시 적중을 노린다.
        async with make_session() as sess:
            async with sess.begin():
                await sess.execute(
                    update(Video)
                    .where(Video.video_pk == video_pk)
                    .values(analysis_status="pending")
                )
        await write_job_log(
            make_session,
            job_type=JOB_TYPE_VIDEO_ANALYZE,
            status=STATUS_SKIP,
            message="공유 캐시 분석 진행 중 — 다음 틱 재시도",
            channel_pk=channel_pk,
            video_pk=video_pk,
        )
        return

    timer = JobTimer()
    if outcome.kind == "hit":
        try:
            with timer:
                result = result_from_cache(
                    outcome.analysis or {}, model_name=ai.primary_model, gateway_url=ai.base_url
                )
                async with make_session() as sess:
                    async with sess.begin():
                        await save_analysis_to_group(sess, video_pk, result)
            await _record_delivery_safe(group, outcome.cache_id)
            await write_job_log(
                make_session,
                job_type=JOB_TYPE_VIDEO_ANALYZE,
                status=STATUS_SUCCESS,
                message=f"{label} 완료(캐시 적중) - {video.title}",
                duration_ms=timer.elapsed_ms,
                channel_pk=channel_pk,
                video_pk=video_pk,
            )
            await _notify_after_analysis(group, make_session, video_pk, channel_pk)
            await _records_post_pass(group=group, make_session=make_session, video_pk=video_pk)
        except Exception as e:
            print(f"[{group.slug}] 캐시 복사 실패 (video_pk={video_pk}): {e}")
            await _mark_video_failed(group, make_session, video_pk, e, label)
            await write_job_log(
                make_session,
                job_type=JOB_TYPE_VIDEO_ANALYZE,
                status=STATUS_FAIL,
                message=f"캐시 복사 실패: {e}",
                duration_ms=timer.elapsed_ms,
                channel_pk=channel_pk,
                video_pk=video_pk,
            )
        return

    # outcome.kind == "claimed" — 이 워커가 분석 수행권을 가진다.
    pipeline = await build_analysis_pipeline(group.group_id, resolved=resolved)
    try:
        with timer:
            async with make_session() as sess:
                async with sess.begin():
                    result = await pipeline.run_and_save(
                        session=sess,
                        video_pk=video_pk,
                        video_url=video.video_url,
                        channel_name=channel_name,
                        published_at_str=video.published_at.isoformat(),
                        duration_seconds=video.duration_seconds,
                    )
        await complete_cached(
            outcome.cache_id, result.data,
            input_tokens=result.input_tokens, output_tokens=result.output_tokens,
        )
        # 캐시 미스 실호출은 시스템 몫(user_id=NULL) 1회 기록 (스펙 §2.4 귀속 원칙)
        await record_usage(
            user_id=None,
            group_id=group.group_id,
            purpose="analysis",
            model=ai.primary_model,
            input_tokens=result.input_tokens,
            output_tokens=result.output_tokens,
            video_pk=video_pk,
        )
        await _record_delivery_safe(group, outcome.cache_id)
        await write_job_log(
            make_session,
            job_type=JOB_TYPE_VIDEO_ANALYZE,
            status=STATUS_SUCCESS,
            message=f"{label} 완료(캐시 신규) - {video.title}",
            duration_ms=timer.elapsed_ms,
            channel_pk=channel_pk,
            video_pk=video_pk,
        )
        await _notify_after_analysis(group, make_session, video_pk, channel_pk)
        await _records_post_pass(group=group, make_session=make_session, video_pk=video_pk)
    except Exception as e:
        print(f"[{group.slug}] {label} 실패 (video_pk={video_pk}): {e}")
        try:
            await fail_cached(outcome.cache_id)
        except Exception as ce:
            print(f"[{group.slug}] 캐시 실패 기록 오류 (cache_id={outcome.cache_id}): {ce}")
        await _mark_video_failed(group, make_session, video_pk, e, label)
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
        await pipeline.aclose()


async def _record_delivery_safe(group: Group, cache_id: Optional[int]) -> None:
    """전달 원장 기록. 실패해도 분석 흐름을 깨지 않는다(원장은 쿼터/과금용 부가 데이터).

    cache_id=None은 직접 프롬프트 경로(캐시 미경유) — 그대로 기록한다.
    """
    if group.owner_user_id is None:
        return
    try:
        await record_delivery_for(group.owner_user_id, group.group_id, cache_id)
    except Exception as e:
        print(f"[{group.slug}] 전달 원장 기록 실패 (cache_id={cache_id}): {e}")


async def _daily_quota_ok(group: Group) -> tuple[bool, str]:
    """그룹 owner의 일일 분석 한도 검사. (통과 여부, 초과 사유)."""
    limits = await limits_for_group_owner(group)
    if limits is None:
        return True, ""
    async with get_sessionmaker()() as session:
        current = await count_daily_deliveries(session, group.owner_user_id)
    if current >= limits.max_analyses_per_day:
        return False, (
            f"일일 분석 한도 초과: 오늘 {current}건 / 한도 "
            f"{limits.max_analyses_per_day}건 (KST 자정 초기화)"
        )
    return True, ""


async def _analyze_group(group: Group) -> None:
    try:
        await dpm.ensure_schema(group)
        engine = await dpm.get_engine_for_group(group)
    except DBNotConfiguredError:
        return

    make_session = _make_session_factory(engine, group.schema_name)

    # 일일 한도 초과 그룹은 클레임 자체를 건너뛴다 — 매 틱 claim→pending 되돌림 churn과
    # job log 스팸(스케줄러 최소 1분 주기)을 피한다. 한도 도달 상태는 /api/me/usage·관리자
    # 사용량으로 관찰 가능하며, 다음 KST 자정에 자동 재개된다.
    ok, _reason = await _daily_quota_ok(group)
    if not ok:
        return

    async with make_session() as sess:
        async with sess.begin():
            await reset_stale_processing_videos(sess, STALE_PROCESSING_RESET_MINUTES)
            await reset_eligible_failed_videos(sess)
            claimed = await claim_pending_video_pks(sess, 1)
    if not claimed:
        return

    await _run_analysis(group, make_session, claimed[0])


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


async def run_stats_refresh_once() -> None:
    """게시 후 N일 이내 영상의 view_count·like_count를 YouTube API로 갱신한다.

    그룹별 polling.stats_refresh_days(0이면 비활성)에 따라 윈도우 내 영상의
    video_id를 모아 get_video_details로 fresh stats를 받아 제자리 UPDATE한다.
    """
    mgr = get_settings_manager()
    groups = await _active_groups()
    for group in groups:
        try:
            polling = await mgr.get_polling(group.group_id)
            if int(polling.stats_refresh_days or 0) <= 0:
                continue
            try:
                api_key = await resolve_youtube_key(group.group_id)
            except YouTubeQuotaExceededError as e:
                print(f"[{group.slug}] 시스템 키 쿼터 소진 - stats 갱신 SKIP: {e}")
                continue
            if not api_key:
                continue
            polling = replace(polling, youtube_api_key=api_key)
            try:
                await dpm.ensure_schema(group)
                engine = await dpm.get_engine_for_group(group)
            except DBNotConfiguredError:
                continue

            make_session = _make_session_factory(engine, group.schema_name)
            cutoff = _stats_window_cutoff(
                datetime.now(timezone.utc), int(polling.stats_refresh_days)
            )

            async with make_session() as sess:
                rows = (
                    await sess.execute(
                        select(Video.video_pk, Video.video_id).where(
                            Video.published_at >= cutoff
                        )
                    )
                ).all()
            if not rows:
                continue
            id_to_pk = {vid: pk for (pk, vid) in rows if vid}
            if not id_to_pk:
                continue

            api_client = YouTubeAPIClient(polling, recorder=make_recorder(polling.youtube_api_key))
            timer = JobTimer()
            updated = 0
            status = STATUS_SUCCESS
            message = ""
            try:
                with timer:
                    try:
                        metas = await api_client.get_video_details(list(id_to_pk.keys()))
                    except YouTubeQuotaExceededError as exc:
                        print(f"[{group.slug}] stats 갱신: quota 초과 — {exc}")
                        status = STATUS_SKIP
                        message = f"쿼터 초과: {exc}"
                    else:
                        stats_map = _build_stats_map(metas)
                        async with make_session() as sess:
                            async with sess.begin():
                                for video_id, (vc, lc) in stats_map.items():
                                    pk = id_to_pk.get(video_id)
                                    if pk is None:
                                        continue
                                    await sess.execute(
                                        update(Video)
                                        .where(Video.video_pk == pk)
                                        .values(view_count=vc, like_count=lc)
                                    )
                                    updated += 1
                        message = f"stats 갱신: {updated}/{len(id_to_pk)}건"
            except Exception as exc:
                status = STATUS_FAIL
                message = f"stats 갱신 실패: {exc}"
                print(f"[{group.slug}] stats 갱신 실패: {exc}")
            finally:
                await api_client.aclose()
                await write_job_log(
                    make_session,
                    job_type=JOB_TYPE_STATS,
                    status=status,
                    message=message,
                    duration_ms=timer.elapsed_ms,
                )
        except DBNotConfiguredError:
            continue
        except Exception as e:
            print(f"[{group.slug}] stats 갱신 실패: {e}")


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
    try:
        api_key = await resolve_youtube_key(group.group_id)
    except YouTubeQuotaExceededError as e:
        print(f"[{group.slug}] 시스템 키 쿼터 소진 - 단건 폴링 SKIP: {e}")
        return
    if not api_key:
        print(f"[{group.slug}] YouTube API 키 미설정(그룹·시스템 모두) - 단건 폴링 SKIP")
        return
    polling = replace(polling, youtube_api_key=api_key)
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

    api_client = YouTubeAPIClient(polling, recorder=make_recorder(polling.youtube_api_key))
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


async def analyze_specific_video(group: Group, video_pk: int, custom_prompt: Optional[str] = None) -> None:
    """단일 그룹에서 특정 영상 1건을 즉시 분석한다(수동 등록용)."""
    try:
        await dpm.ensure_schema(group)
        engine = await dpm.get_engine_for_group(group)
    except DBNotConfiguredError:
        return

    make_session = _make_session_factory(engine, group.schema_name)
    await _run_analysis(
        group, make_session, video_pk, custom_prompt=custom_prompt, label="즉시 분석"
    )
