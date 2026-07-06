"""중앙 채널 레지스트리·구독 동기화 (스펙 B-0b §2·§4).

동기화 원칙: channel_subscriptions에는 해석 완료된 유효값만 저장한다
(채널 주기 NULL → 그룹 default_channel_interval_min). subscriber_groups는
참고용 캐시 — 변경 지점마다 COUNT(*) 재계산.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Iterable, Optional, Sequence

from sqlalchemy import delete, func, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.control_db import get_sessionmaker
from app.models.control.channel_registry import ChannelRegistry
from app.models.control.channel_subscription import ChannelSubscription
from app.models.control.group import Group
from app.models.pg.channel import Channel
from app.services.db_engine import DBNotConfiguredError, data_plane_engine_manager as dpm
from app.services.settings_manager import get_settings_manager


@dataclass(frozen=True)
class DueChannel:
    channel_id: str
    upload_playlist_id: Optional[str]
    effective_interval_min: int
    fetch_window_hours: int


def desired_subscription_values(channels: Iterable, polling) -> dict[str, tuple[int, int]]:
    """그룹 스키마 channels + 그룹 polling 설정 → {channel_id: (유효 주기, 윈도)}.

    비활성 채널은 제외(중앙 폴링 대상 아님). 순수 함수 — 단위 테스트 대상.
    """
    default_interval = int(polling.default_channel_interval_min or 720)
    window = int(polling.window_hours or 24)
    return {
        ch.channel_id: (int(ch.poll_interval_min or default_interval), window)
        for ch in channels
        if ch.is_active
    }


def filter_due(rows: Sequence, now: datetime, floor_min: int) -> list[DueChannel]:
    """집계 행(interval_min=MIN, window_hours=MAX) → due 채널 목록. 순수 함수."""
    due: list[DueChannel] = []
    for r in rows:
        interval = max(int(r.interval_min), int(floor_min))
        lp = r.last_polled_at
        if lp is not None and lp.tzinfo is None:
            lp = lp.replace(tzinfo=timezone.utc)
        if lp is None or now - lp >= timedelta(minutes=interval):
            due.append(
                DueChannel(
                    channel_id=r.channel_id,
                    upload_playlist_id=r.upload_playlist_id,
                    effective_interval_min=int(r.interval_min),
                    fetch_window_hours=int(r.window_hours),
                )
            )
    return due


async def list_due_channels(
    session: AsyncSession, now: datetime, floor_min: int
) -> list[DueChannel]:
    """구독 있는 채널만 join으로 자연 포함(구독 0 채널 제외 — 스펙 §2)."""
    rows = (
        await session.execute(
            select(
                ChannelRegistry.channel_id,
                ChannelRegistry.upload_playlist_id,
                ChannelRegistry.last_polled_at,
                func.min(ChannelSubscription.poll_interval_min).label("interval_min"),
                func.max(ChannelSubscription.window_hours).label("window_hours"),
            )
            .join(
                ChannelSubscription,
                ChannelSubscription.channel_id == ChannelRegistry.channel_id,
            )
            .group_by(ChannelRegistry.channel_id)
        )
    ).all()
    return filter_due(rows, now=now, floor_min=floor_min)


async def subscriptions_for_channels(
    session: AsyncSession, channel_ids: Sequence[str]
) -> dict[str, list[ChannelSubscription]]:
    """중앙 폴러용: due 채널들의 구독을 한 번에 조회해 channel_id로 묶는다."""
    if not channel_ids:
        return {}
    rows = (
        await session.execute(
            select(ChannelSubscription).where(
                ChannelSubscription.channel_id.in_(list(channel_ids))
            )
        )
    ).scalars()
    out: dict[str, list[ChannelSubscription]] = {}
    for s in rows:
        out.setdefault(s.channel_id, []).append(s)
    return out


async def upsert_registry(
    session: AsyncSession,
    channel_id: str,
    title: Optional[str] = None,
    upload_playlist_id: Optional[str] = None,
) -> None:
    """registry 행 멱등 생성. 기존 행의 메타는 새 값이 있을 때만 갱신."""
    stmt = pg_insert(ChannelRegistry).values(
        channel_id=channel_id, title=title, upload_playlist_id=upload_playlist_id
    )
    stmt = stmt.on_conflict_do_update(
        index_elements=[ChannelRegistry.channel_id],
        set_={
            "title": func.coalesce(stmt.excluded.title, ChannelRegistry.title),
            "upload_playlist_id": func.coalesce(
                stmt.excluded.upload_playlist_id, ChannelRegistry.upload_playlist_id
            ),
        },
    )
    await session.execute(stmt)


async def _recount(session: AsyncSession, channel_id: str) -> None:
    count = (
        await session.execute(
            select(func.count())
            .select_from(ChannelSubscription)
            .where(ChannelSubscription.channel_id == channel_id)
        )
    ).scalar_one()
    await session.execute(
        update(ChannelRegistry)
        .where(ChannelRegistry.channel_id == channel_id)
        .values(subscriber_groups=int(count))
    )


async def subscribe(
    session: AsyncSession,
    channel_id: str,
    group_id: int,
    poll_interval_min: int,
    window_hours: int,
) -> None:
    """구독 upsert (registry 행이 이미 있어야 한다 — 호출자가 upsert_registry 선행)."""
    stmt = pg_insert(ChannelSubscription).values(
        channel_id=channel_id,
        group_id=group_id,
        poll_interval_min=poll_interval_min,
        window_hours=window_hours,
    )
    stmt = stmt.on_conflict_do_update(
        index_elements=[ChannelSubscription.channel_id, ChannelSubscription.group_id],
        set_={
            "poll_interval_min": stmt.excluded.poll_interval_min,
            "window_hours": stmt.excluded.window_hours,
        },
    )
    await session.execute(stmt)
    await _recount(session, channel_id)


async def unsubscribe(session: AsyncSession, channel_id: str, group_id: int) -> None:
    await session.execute(
        delete(ChannelSubscription).where(
            ChannelSubscription.channel_id == channel_id,
            ChannelSubscription.group_id == group_id,
        )
    )
    await _recount(session, channel_id)


async def remove_group_subscriptions(session: AsyncSession, group_id: int) -> None:
    """그룹 비활성/삭제 시: 구독 제거 + 영향받은 채널 재계산."""
    affected = [
        cid
        for (cid,) in (
            await session.execute(
                select(ChannelSubscription.channel_id).where(
                    ChannelSubscription.group_id == group_id
                )
            )
        ).all()
    ]
    await session.execute(
        delete(ChannelSubscription).where(ChannelSubscription.group_id == group_id)
    )
    for cid in affected:
        await _recount(session, cid)


async def mark_polled(
    session: AsyncSession,
    channel_id: str,
    polled_at: datetime,
    last_video_at: Optional[datetime] = None,
) -> None:
    values: dict = {"last_polled_at": polled_at}
    if last_video_at is not None:
        values["last_video_at"] = last_video_at
    await session.execute(
        update(ChannelRegistry)
        .where(ChannelRegistry.channel_id == channel_id)
        .values(**values)
    )


async def resync_group(group: Group) -> None:
    """그룹 스키마 channels → 구독 테이블을 원하는 상태로 수렴시킨다. 멱등.

    사용처: 부팅 백필, polling 설정 변경, 그룹 재활성 (스펙 §4·§6).
    """
    try:
        await dpm.ensure_schema(group)
        engine = await dpm.get_engine_for_group(group)
    except DBNotConfiguredError:
        return  # DB 미설정 그룹은 폴링 대상이 아니므로 구독도 없음
    from app.services.monitor_service import _make_session_factory

    make_session = _make_session_factory(engine, group.schema_name)
    async with make_session() as gsession:
        channels = list(
            (await gsession.execute(select(Channel))).scalars().all()
        )
    polling = await get_settings_manager().get_polling(group.group_id)
    desired = desired_subscription_values(channels, polling)
    meta = {ch.channel_id: ch for ch in channels}

    sf = get_sessionmaker()
    async with sf() as session:
        async with session.begin():
            current = {
                s.channel_id
                for s in (
                    await session.execute(
                        select(ChannelSubscription).where(
                            ChannelSubscription.group_id == group.group_id
                        )
                    )
                ).scalars()
            }
            for channel_id, (interval, window) in desired.items():
                ch = meta[channel_id]
                await upsert_registry(
                    session, channel_id,
                    title=ch.channel_name, upload_playlist_id=ch.upload_playlist_id,
                )
                await subscribe(session, channel_id, group.group_id, interval, window)
            for stale in current - set(desired):
                await unsubscribe(session, stale, group.group_id)


async def backfill_channel_registry() -> None:
    """부팅 시 전 활성 그룹의 채널을 레지스트리·구독에 백필. 멱등 (스펙 §6)."""
    sf = get_sessionmaker()
    async with sf() as session:
        groups = list(
            (
                await session.execute(select(Group).where(Group.is_active.is_(True)))
            ).scalars().all()
        )
    for group in groups:
        try:
            await resync_group(group)
        except Exception as e:
            print(f"[registry-backfill] 그룹 {group.slug} 동기화 실패: {e}")
