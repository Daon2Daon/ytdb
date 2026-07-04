"""관리자 API 입출력 스키마."""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field


class AdminUserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    user_id: int
    email: str
    display_name: Optional[str]
    role: str
    status: str
    plan_id: int
    last_login_at: Optional[datetime]
    created_at: datetime


class PlanOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    plan_id: int
    slug: str
    name: str
    max_groups: int
    max_channels_total: int
    max_analyses_per_day: int
    max_video_minutes: int
    min_poll_interval_min: int
    is_default: bool


class InviteCreate(BaseModel):
    plan_slug: Optional[str] = None          # 미지정 시 기본 플랜(free)
    memo: Optional[str] = None
    expires_days: int = Field(default=7, ge=1, le=90)


class InviteOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    invite_id: int
    token: str
    plan_id: int
    memo: Optional[str]
    expires_at: datetime
    used_by: Optional[int]
    used_at: Optional[datetime]
    created_at: datetime


class InviteCreated(InviteOut):
    signup_url: str
