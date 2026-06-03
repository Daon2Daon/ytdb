"""채널 입출력 스키마."""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict


class ChannelCreate(BaseModel):
    # 채널 URL / @handle / UC id 중 무엇이든 허용. youtube_api로 정규화한다.
    channel_input: str
    poll_interval_min: Optional[int] = None
    category: Optional[str] = None
    # 등록 직후 과거 영상까지 1회 수집할지 여부.
    backfill: bool = False


class ChannelUpdate(BaseModel):
    is_active: Optional[bool] = None
    notify_enabled: Optional[bool] = None
    poll_interval_min: Optional[int] = None
    category: Optional[str] = None


class ChannelOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    channel_pk: int
    channel_id: str
    channel_name: str
    channel_handle: Optional[str]
    thumbnail_url: Optional[str]
    category: Optional[str]
    poll_interval_min: int
    is_active: bool
    notify_enabled: bool
    notify_from: Optional[datetime]
    last_checked_at: Optional[datetime]
    last_video_id: Optional[str]
    created_at: datetime
