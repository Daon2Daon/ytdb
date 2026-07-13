"""중앙 폴링 (스펙 B-0b §3): 채널당 1회 API 조회 → 구독 그룹 푸시 팬아웃.

- 항상 시스템 키 사용 (그룹 키 폴백 없음 — 그룹 스코프 호출과 구별).
- 그룹 단위 try/except 격리: 한 그룹 실패가 다른 그룹을 막지 않는다.
- 쿼터 초과는 틱 전체 중단 (best-effort — 이미 세마포어를 통과해 진행 중인
  채널은 완주 후 드레인; 강제 취소 없음, idempotent라 안전).
  last_polled_at 미갱신 채널은 다음 틱 재폴링되며
  이미 삽입된 그룹은 _filter_new_videos가 중복을 막는다 (idempotent).
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from typing import Optional, Sequence

from sqlalchemy import select

from app.control_db import get_sessionmaker
from app.models.control.group import Group
from app.models.pg.channel import Channel
from app.services.channel_registry_service import (
    DueChannel,
    list_due_channels,
    mark_polled,
    subscriptions_for_channels,
)
from app.services.db_engine import DBNotConfiguredError, data_plane_engine_manager as dpm
from app.services.global_settings import get_central_poll_floor_min, get_system_youtube_key
from app.services.job_logger import (
    JOB_TYPE_CHANNEL_POLL,
    STATUS_FAIL,
    STATUS_SUCCESS,
    JobTimer,
    write_job_log,
)
from app.services.monitor_service import (
    MonitorService,
    _make_session_factory,
    fetch_channel_updates,
)
from app.services.settings_manager import get_settings_manager
from app.services.settings_types import PollingSettings
from app.services import yt_quota_service as yq
from app.services.youtube_api import YouTubeAPIClient, YouTubeQuotaExceededError
from app.services.yt_parsing import parse_iso_datetime

# 중앙 폴러 동시 채널 상한. 그룹별 max_concurrent_channels는 그룹 폴링용 설정이라
# 부적합 — 전역 설정 키로 승격은 필요해질 때 (스펙 §3).
CENTRAL_MAX_CONCURRENT_CHANNELS = 5

_last_gate_state = yq.GATE_OK


def _log_gate_transition(state: str, used: int, limit: int) -> None:
    """상태 전환 시 1회만 stdout 경고 — 틱마다 스팸 방지 (스펙 §1.4)."""
    global _last_gate_state
    if state == _last_gate_state:
        return
    print(
        f"[central-poll] 쿼터 게이트 {_last_gate_state} → {state}: "
        f"시스템 키 당일 {used}/{limit} 유닛"
    )
    _last_gate_state = state


async def _prepare_tick():
    """due 채널 + 채널별 구독 + 활성 그룹 맵을 한 번에 준비한다."""
    now = datetime.now(timezone.utc)
    sf = get_sessionmaker()
    async with sf() as session:
        floor = await get_central_poll_floor_min(session)
        due = await list_due_channels(session, now=now, floor_min=floor)
        subs = await subscriptions_for_channels(session, [d.channel_id for d in due])
        groups = {
            g.group_id: g
            for g in (
                await session.execute(select(Group).where(Group.is_active.is_(True)))
            ).scalars()
        }
    return due, subs, groups


async def _fan_out_group(
    group: Group,
    channel_id: str,
    metas: Sequence,
    window_hours: int,
    now: datetime,
) -> Optional[int]:
    """한 그룹 스키마에 삽입. 반환: 신규 영상 수(그룹 채널 미존재 시 None)."""
    await dpm.ensure_schema(group)
    engine = await dpm.get_engine_for_group(group)
    make_session = _make_session_factory(engine, group.schema_name)
    polling = await get_settings_manager().get_polling(group.group_id)
    service = MonitorService(polling=polling)
    cutoff = now - timedelta(hours=int(window_hours))

    timer = JobTimer()
    channel_pk = None
    try:
        with timer:
            async with make_session() as session:
                async with session.begin():
                    channel = (
                        await session.execute(
                            select(Channel).where(Channel.channel_id == channel_id)
                        )
                    ).scalar_one_or_none()
                    if channel is None or not channel.is_active:
                        return None  # 구독 테이블과 그룹 스키마 불일치 — 다음 resync가 복구
                    new_pks = await service.insert_group_videos(
                        channel, session, metas, cutoff, now=now
                    )
                    channel_pk = channel.channel_pk
    except Exception as e:
        # 관찰성: 기존 _poll_group과 동일하게 실패도 그룹 job log에 남긴다.
        await write_job_log(
            make_session,
            job_type=JOB_TYPE_CHANNEL_POLL,
            status=STATUS_FAIL,
            message=f"중앙폴링 팬아웃 실패: {e}",
            duration_ms=timer.elapsed_ms,
            channel_pk=channel_pk,
        )
        raise
    await write_job_log(
        make_session,
        job_type=JOB_TYPE_CHANNEL_POLL,
        status=STATUS_SUCCESS,
        message=f"중앙폴링 신규 영상 {len(new_pks)}건" if new_pks else "중앙폴링 신규 영상 없음",
        duration_ms=timer.elapsed_ms,
        channel_pk=channel_pk,
    )
    return len(new_pks)


async def _mark_polled(
    channel_id: str, now: datetime, last_video_at: Optional[datetime]
) -> None:
    sf = get_sessionmaker()
    async with sf() as session:
        async with session.begin():
            await mark_polled(session, channel_id, now, last_video_at)


async def run_central_poll_once() -> None:
    """전역 중앙 폴링 틱: registry 기준 채널당 1회 폴링 후 구독 그룹 팬아웃."""
    system_key = await get_system_youtube_key()
    if not system_key:
        print("[central-poll] 시스템 YouTube 키 미설정 - 중앙 폴링 SKIP")
        return

    state, used, limit = await yq.system_gate_state()
    _log_gate_transition(state, used, limit)
    if state != yq.GATE_OK:
        return

    due, subs_by_channel, groups = await _prepare_tick()
    if not due:
        return
    print(f"[central-poll] 폴링 시작: {len(due)}개 채널")

    now = datetime.now(timezone.utc)
    api_client = YouTubeAPIClient(PollingSettings(youtube_api_key=system_key))
    sem = asyncio.Semaphore(CENTRAL_MAX_CONCURRENT_CHANNELS)
    quota_hit = asyncio.Event()

    async def _one(d: DueChannel) -> None:
        async with sem:
            if quota_hit.is_set():
                return
            if not d.upload_playlist_id:
                print(f"[central-poll] {d.channel_id} 플레이리스트 미상 - SKIP")
                return
            cutoff = now - timedelta(hours=d.fetch_window_hours)
            try:
                metas = await fetch_channel_updates(api_client, d.upload_playlist_id, cutoff)
            except YouTubeQuotaExceededError as e:
                print(f"[central-poll] 쿼터 초과 - 틱 중단: {e}")
                quota_hit.set()
                return
            except Exception as e:
                print(f"[central-poll] {d.channel_id} 조회 실패: {e}")
                return  # last_polled_at 미갱신 → 다음 틱 재시도

            for sub in subs_by_channel.get(d.channel_id, []):
                group = groups.get(sub.group_id)
                if group is None:
                    continue  # 비활성 그룹 — 구독은 남아 있어도 팬아웃 제외
                try:
                    await _fan_out_group(
                        group, d.channel_id, metas, sub.window_hours, now
                    )
                except DBNotConfiguredError:
                    continue
                except Exception as e:
                    print(f"[central-poll] [{group.slug}] {d.channel_id} 팬아웃 실패: {e}")

            last_video_at = (
                max(parse_iso_datetime(m.published_at) for m in metas) if metas else None
            )
            await _mark_polled(d.channel_id, now, last_video_at)

    try:
        await asyncio.gather(*[_one(d) for d in due], return_exceptions=True)
    finally:
        await api_client.aclose()
