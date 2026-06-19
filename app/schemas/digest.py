"""주간 리뷰(digest) 입출력 스키마."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict


class DigestOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    digest_pk: int
    period_type: str
    period_weeks: int
    period_days: Optional[int] = None
    digest_config_id: Optional[str] = None
    config_name: Optional[str] = None
    period_start: datetime
    period_end: datetime
    category: Optional[str]
    video_count: int
    headline: Optional[str]
    summary_md: Optional[str]
    telegram_summary: Optional[str]
    sentiment_breakdown: Optional[Any]
    top_tags: Optional[Any]
    top_channels: Optional[Any]
    model_name: Optional[str]
    token_input: Optional[int]
    token_output: Optional[int]
    cost_usd: Optional[float]
    status: str
    error: Optional[str]
    created_at: datetime
    updated_at: datetime


class DigestGenerateRequest(BaseModel):
    save: bool = True
    digest_config_id: Optional[str] = None
    period_days: Optional[int] = None
    category: Optional[str] = None
