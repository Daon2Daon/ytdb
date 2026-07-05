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


class PresetCreate(BaseModel):
    name: str
    description: Optional[str] = None
    analysis_prompt: str
    digest_prompt: str = ""


class PresetPatch(BaseModel):
    """프리셋 본문(analysis_prompt/digest_prompt)은 불변 — 여기 두지 않는다(스펙 §8).

    본문 변경은 새 프리셋 생성 + 구버전 is_active=false로 처리한다.
    """

    name: Optional[str] = None
    description: Optional[str] = None
    is_active: Optional[bool] = None


class PresetOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    preset_id: int
    name: str
    description: Optional[str]
    analysis_prompt: str
    digest_prompt: str
    is_active: bool
    created_at: datetime
    updated_at: datetime
