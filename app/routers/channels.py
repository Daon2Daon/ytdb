"""그룹 채널 관리 API."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select

from app.models.control.group import Group
from app.models.pg.channel import Channel
from app.routers.deps import get_group_or_404
from app.schemas.channel import ChannelCreate, ChannelOut, ChannelUpdate
from app.services.db_engine import data_plane_engine_manager as dpm
from app.services.monitor_service import MonitorService
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
    polling = await get_settings_manager().get_polling(group.group_id)
    if not polling.youtube_api_key:
        raise HTTPException(
            status_code=400, detail="YouTube API 키(polling.youtube_api_key)를 먼저 설정하세요."
        )
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
                )
                session.add(channel)
                await session.flush()
                channel_pk = channel.channel_pk

                if payload.backfill:
                    service = MonitorService(polling=polling)
                    await service.process_channel(channel, session, api, backfill=True)

            async with dpm.group_session(group) as s2:
                return (
                    await s2.execute(select(Channel).where(Channel.channel_pk == channel_pk))
                ).scalar_one()
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
            for field, value in data.items():
                setattr(channel, field, value)
        return (
            await session.execute(select(Channel).where(Channel.channel_pk == channel_pk))
        ).scalar_one()


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
            await session.delete(channel)
