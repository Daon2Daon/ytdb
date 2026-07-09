"""그룹 채널 관리 API."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from sqlalchemy import select

from app.control_db import get_sessionmaker
from app.models.control.group import Group
from app.models.pg.channel import Channel
from app.routers.deps import get_group_or_404
from app.schemas.channel import ChannelCreate, ChannelOut, ChannelUpdate
from app.services import channel_registry_service as registry
from app.services.db_engine import data_plane_engine_manager as dpm
from app.services.global_settings import resolve_youtube_key
from app.services.monitor_service import MonitorService, poll_single_channel
from app.services.quota_service import (
    QuotaExceeded,
    check_channel_quota,
    limits_for_group_owner,
    validate_poll_interval,
)
from app.services.settings_manager import get_settings_manager
from app.services.youtube_api import YouTubeAPIError, YouTubeAPIClient

router = APIRouter(prefix="/api/groups/{slug}/channels", tags=["channels"])


@router.get("", response_model=list[ChannelOut])
async def list_channels(group: Group = Depends(get_group_or_404)) -> list[Channel]:
    async with dpm.group_session(group) as session:
        result = await session.execute(select(Channel).order_by(Channel.channel_pk))
        return list(result.scalars().all())


@router.post("", response_model=ChannelOut, status_code=201)
async def add_channel(
    payload: ChannelCreate, group: Group = Depends(get_group_or_404)
) -> Channel:
    limits = await limits_for_group_owner(group)
    if limits is not None:
        async with get_sessionmaker()() as qs:
            try:
                await check_channel_quota(qs, group.owner_user_id)
            except QuotaExceeded as e:
                raise HTTPException(status_code=400, detail=e.detail)
        if not validate_poll_interval(limits, payload.poll_interval_min):
            raise HTTPException(
                status_code=400,
                detail=f"폴링 주기는 플랜 하한({limits.min_poll_interval_min}분) 이상이어야 합니다.",
            )

    polling = await get_settings_manager().get_polling(group.group_id)
    api_key = await resolve_youtube_key(group.group_id)
    if not api_key:
        raise HTTPException(
            status_code=400,
            detail="YouTube API 키가 없습니다. 그룹 polling 설정 또는 시스템 전역 키를 설정하세요.",
        )
    polling = replace(polling, youtube_api_key=api_key)
    api = YouTubeAPIClient(polling)
    try:
        meta = await api.resolve_channel(payload.channel_input)
    except YouTubeAPIError as e:
        await api.aclose()
        raise HTTPException(status_code=400, detail=f"채널 확인 실패: {e}")

    async with dpm.group_session(group) as session:
        try:
            async with session.begin():
                exists = (
                    await session.execute(
                        select(Channel).where(Channel.channel_id == meta.channel_id)
                    )
                ).scalar_one_or_none()
                if exists is not None:
                    raise HTTPException(status_code=409, detail="이미 등록된 채널입니다.")
                channel = Channel(
                    channel_id=meta.channel_id,
                    channel_name=meta.channel_name,
                    channel_handle=meta.channel_handle,
                    upload_playlist_id=meta.upload_playlist_id,
                    thumbnail_url=meta.thumbnail_url,
                    description=meta.description,
                    poll_interval_min=payload.poll_interval_min
                    or polling.default_channel_interval_min,
                    category=payload.category,
                    # 알림 기본 ON으로 생성 → 기준 시점을 지금으로. 과거 백로그는
                    # 분석/저장만 되고 자동 발송되지 않는다(이후 게시분부터 발송).
                    notify_from=datetime.now(timezone.utc),
                )
                session.add(channel)
                await session.flush()
                channel_pk = channel.channel_pk

                if payload.backfill:
                    service = MonitorService(polling=polling)
                    await service.process_channel(channel, session, api)

            async with dpm.group_session(group) as s2:
                result = (
                    await s2.execute(select(Channel).where(Channel.channel_pk == channel_pk))
                ).scalar_one()

            try:
                async with get_sessionmaker()() as cs:
                    async with cs.begin():
                        await registry.upsert_registry(
                            cs, meta.channel_id,
                            title=meta.channel_name,
                            upload_playlist_id=meta.upload_playlist_id,
                        )
                        await registry.subscribe(
                            cs, meta.channel_id, group.group_id,
                            poll_interval_min=payload.poll_interval_min
                            or polling.default_channel_interval_min,
                            window_hours=polling.window_hours,
                        )
            except Exception as e:
                # 훅 실패로 201을 막지 않는다 — 채널은 이미 생성됐고 재시도는 409로
                # 막히므로, 구독 누락은 부팅 백필(resync)이 복구한다 (스펙 §8).
                print(f"[{group.slug}] 채널 구독 동기화 실패(백필이 복구): {e}")

            return result
        finally:
            await api.aclose()


@router.patch("/{channel_pk}", response_model=ChannelOut)
async def update_channel(
    channel_pk: int,
    payload: ChannelUpdate,
    group: Group = Depends(get_group_or_404),
) -> Channel:
    data = payload.model_dump(exclude_unset=True)
    async with dpm.group_session(group) as session:
        async with session.begin():
            channel = (
                await session.execute(select(Channel).where(Channel.channel_pk == channel_pk))
            ).scalar_one_or_none()
            if channel is None:
                raise HTTPException(status_code=404, detail="채널을 찾을 수 없습니다.")
            was_notify = channel.notify_enabled
            for field, value in data.items():
                setattr(channel, field, value)
            # 알림 OFF→ON 전환 시 기준 시점을 지금으로 재설정("알림 켠 이후"부터 발송).
            if data.get("notify_enabled") is True and not was_notify:
                channel.notify_from = datetime.now(timezone.utc)
        channel = (
            await session.execute(select(Channel).where(Channel.channel_pk == channel_pk))
        ).scalar_one()

        # channel_name 변경은 registry.title에 전파하지 않음 — title은 표시용 캐시,
        # 다음 폴링/백필이 갱신 (스펙 §4)
        if "poll_interval_min" in data or "is_active" in data:
            polling = await get_settings_manager().get_polling(group.group_id)
            async with get_sessionmaker()() as cs:
                async with cs.begin():
                    if channel.is_active:
                        await registry.upsert_registry(cs, channel.channel_id)
                        await registry.subscribe(
                            cs, channel.channel_id, group.group_id,
                            poll_interval_min=channel.poll_interval_min
                            or polling.default_channel_interval_min,
                            window_hours=polling.window_hours,
                        )
                    else:
                        await registry.unsubscribe(cs, channel.channel_id, group.group_id)

        return channel


@router.delete("/{channel_pk}", status_code=204)
async def delete_channel(
    channel_pk: int, group: Group = Depends(get_group_or_404)
) -> None:
    async with dpm.group_session(group) as session:
        async with session.begin():
            channel = (
                await session.execute(select(Channel).where(Channel.channel_pk == channel_pk))
            ).scalar_one_or_none()
            if channel is None:
                raise HTTPException(status_code=404, detail="채널을 찾을 수 없습니다.")
            deleted_channel_id = channel.channel_id
            await session.delete(channel)

    async with get_sessionmaker()() as cs:
        async with cs.begin():
            await registry.unsubscribe(cs, deleted_channel_id, group.group_id)


@router.post("/{channel_pk}/poll", status_code=202)
async def poll_channel(
    channel_pk: int,
    background: BackgroundTasks,
    group: Group = Depends(get_group_or_404),
) -> dict:
    """단일 채널을 백그라운드에서 즉시 폴링한다."""
    background.add_task(poll_single_channel, group, channel_pk)
    return {"status": "started", "channel_pk": channel_pk, "message": "폴링을 시작했습니다. 잠시 후 영상/로그를 확인하세요."}
