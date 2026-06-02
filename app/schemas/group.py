"""그룹 입출력 스키마."""

from __future__ import annotations

import re
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict, field_validator

SLUG_RE = re.compile(r"^[a-z0-9_]+$")


class GroupCreate(BaseModel):
    slug: str
    name: str
    # 미지정 시 'youtube_{slug}' 로 자동 생성
    schema_name: Optional[str] = None
    description: Optional[str] = None

    @field_validator("slug")
    @classmethod
    def _check_slug(cls, v: str) -> str:
        v = v.strip().lower()
        if not SLUG_RE.fullmatch(v):
            raise ValueError("slug는 소문자/숫자/밑줄(a-z0-9_)만 허용합니다.")
        return v

    @field_validator("schema_name")
    @classmethod
    def _check_schema(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return v
        v = v.strip().lower()
        if not SLUG_RE.fullmatch(v):
            raise ValueError("schema_name은 소문자/숫자/밑줄(a-z0-9_)만 허용합니다.")
        return v


class GroupUpdate(BaseModel):
    name: Optional[str] = None
    is_active: Optional[bool] = None
    description: Optional[str] = None


class GroupOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    group_id: int
    slug: str
    name: str
    schema_name: str
    is_active: bool
    description: Optional[str]
    created_at: datetime
    updated_at: datetime
