"""인증 입출력 스키마."""

from __future__ import annotations

import re
from typing import Optional

from pydantic import BaseModel, field_validator

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+$")


def normalize_email(v: str) -> str:
    v = (v or "").strip().lower()
    if not EMAIL_RE.fullmatch(v):
        raise ValueError("올바른 이메일 형식이 아닙니다.")
    return v


class LoginRequest(BaseModel):
    email: str
    password: str

    @field_validator("email")
    @classmethod
    def _email(cls, v: str) -> str:
        return normalize_email(v)


class SignupRequest(BaseModel):
    token: str
    email: str
    password: str
    display_name: Optional[str] = None

    @field_validator("email")
    @classmethod
    def _email(cls, v: str) -> str:
        return normalize_email(v)

    @field_validator("password")
    @classmethod
    def _password(cls, v: str) -> str:
        if len(v) < 8:
            raise ValueError("비밀번호는 8자 이상이어야 합니다.")
        return v


class UserOut(BaseModel):
    email: str
    display_name: Optional[str]
    role: str


class MeResponse(BaseModel):
    auth_enabled: bool
    authenticated: bool
    user: Optional[UserOut] = None
