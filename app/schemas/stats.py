"""대시보드용 통계/헬스/페이지네이션 스키마."""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel

from app.schemas.video import VideoListItem


class StatsOut(BaseModel):
    total_channels: int
    active_channels: int
    total_videos: int
    analyzed_videos: int
    pending_videos: int
    failed_videos: int
    notified_videos: int
    total_tags: int


class DBHealthOut(BaseModel):
    healthy: bool
    message: str
    latency_ms: Optional[int] = None


class GatewayHealthOut(BaseModel):
    success: bool
    message: str
    latency_ms: Optional[int] = None


class PaginatedVideos(BaseModel):
    total: int
    page: int
    page_size: int
    items: list[VideoListItem]
