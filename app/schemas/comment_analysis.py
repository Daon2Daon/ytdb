"""댓글 반응 분석 입출력 스키마."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel, Field


class StartCommentAnalysisRequest(BaseModel):
    limit: int = Field(default=1000, ge=100, le=100000)


class StartByUrlRequest(BaseModel):
    video_url: str
    limit: int = Field(default=1000, ge=100, le=100000)


class StartCommentAnalysisResponse(BaseModel):
    video_pk: int
    status: str
    queued: bool


class CommentAnalysisOut(BaseModel):
    video_pk: int
    status: str
    requested_limit: int
    fetched_count: Optional[int] = None
    total_count: Optional[int] = None
    current_comment_count: Optional[int] = None  # videos.comment_count (신선도 비교용)
    positive_count: Optional[int] = None
    negative_count: Optional[int] = None
    neutral_count: Optional[int] = None
    result: Optional[dict[str, Any]] = None
    model: Optional[str] = None
    partial: bool = False
    error: Optional[str] = None
    analyzed_at: Optional[datetime] = None


class CommentAnalysisListItem(BaseModel):
    video_pk: int
    video_id: str
    title: str
    thumbnail_url: Optional[str] = None
    status: str
    positive_count: Optional[int] = None
    negative_count: Optional[int] = None
    neutral_count: Optional[int] = None
    analyzed_at: Optional[datetime] = None


class CommentCreditsOut(BaseModel):
    used: int
    limit: int
    unlimited: bool
    per_analysis_max: int
