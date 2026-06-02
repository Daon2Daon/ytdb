"""영상/분석 출력 스키마."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict


class AnalysisOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    one_line: str
    headline: Optional[str]
    short_summary_md: str
    bullet_points: Optional[Any]
    full_analysis_md: Optional[str]
    key_points: Optional[Any]
    insights: Optional[Any]
    entities: Optional[Any]
    sentiment: Optional[str]
    confidence_score: Optional[float]
    model_name: Optional[str]
    prompt_version: Optional[str]
    analyzed_at: datetime


class VideoListItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    video_pk: int
    video_id: str
    video_url: str
    title: str
    thumbnail_url: Optional[str]
    published_at: datetime
    duration_seconds: Optional[int]
    analysis_status: str
    notified_at: Optional[datetime]
    # 분석이 있으면 헤드라인/한 줄 요약을 함께 노출
    headline: Optional[str] = None
    one_line: Optional[str] = None


class VideoDetail(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    video_pk: int
    video_id: str
    video_url: str
    title: str
    description: Optional[str]
    thumbnail_url: Optional[str]
    published_at: datetime
    duration_seconds: Optional[int]
    view_count: Optional[int]
    like_count: Optional[int]
    analysis_status: str
    analysis_error: Optional[str]
    notified_at: Optional[datetime]
    tags: list[str] = []
    analysis: Optional[AnalysisOut] = None


class InstantAnalyzeRequest(BaseModel):
    video_url: str


class InstantAnalyzeResponse(BaseModel):
    video_pk: int
    video_id: str
    existing: bool = False
    queued: bool = True
