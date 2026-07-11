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


class GlobalSettingItem(BaseModel):
    key: str
    value: str  # 시크릿은 마스킹 반환
    is_secret: bool = False


class GlobalSettingsUpdate(BaseModel):
    items: list[GlobalSettingItem]


class AdminUserPatch(BaseModel):
    status: Optional[str] = None      # 'active' | 'suspended'
    plan_id: Optional[int] = None


class UserLimitsIn(BaseModel):
    """NULL 필드 = 플랜 값 사용."""

    max_groups: Optional[int] = Field(default=None, ge=0)
    max_channels_total: Optional[int] = Field(default=None, ge=0)
    max_analyses_per_day: Optional[int] = Field(default=None, ge=0)
    max_video_minutes: Optional[int] = Field(default=None, ge=0)
    min_poll_interval_min: Optional[int] = Field(default=None, ge=1)
    note: Optional[str] = None


class UserLimitsOut(UserLimitsIn):
    model_config = ConfigDict(from_attributes=True)

    user_id: int
    updated_at: datetime


class TempPasswordOut(BaseModel):
    temp_password: str               # 평문은 이 응답에 1회만 노출


class PlanPatch(BaseModel):
    """slug/is_default는 불변(시드 정합성). 한도값만 편집."""

    name: Optional[str] = None
    max_groups: Optional[int] = Field(default=None, ge=0)
    max_channels_total: Optional[int] = Field(default=None, ge=0)
    max_analyses_per_day: Optional[int] = Field(default=None, ge=0)
    max_video_minutes: Optional[int] = Field(default=None, ge=0)
    min_poll_interval_min: Optional[int] = Field(default=None, ge=1)


class AdminUserUsage(BaseModel):
    group_count: int
    channel_count: int
    today_analyses: int
    has_override: bool


class AdminUserOutV2(AdminUserOut):
    usage: Optional[AdminUserUsage] = None


class AdminUsageRow(BaseModel):
    model_config = ConfigDict(protected_namespaces=())

    user_id: Optional[int] = None     # None = 시스템 몫(공유 캐시 분석)
    email: Optional[str] = None
    model: str
    purpose: str
    calls: int
    input_tokens: int
    output_tokens: int
    cost_usd: Optional[float] = None  # 전 행 단가 미상이면 None
    null_cost_calls: int = 0          # 단가 미상 호출 수(경고 표시용)


class AdminUsageResponse(BaseModel):
    window: str
    start: datetime
    end: datetime
    rows: list[AdminUsageRow]
    total_cost_usd: float
    null_cost_row_count: int          # 단가 미상 원장 행 총수 (스펙 §2.4 경고)
