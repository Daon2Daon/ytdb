"""계정 인증 서비스: 비밀번호 해시, 초대 토큰, 부트스트랩 시드, 인증 상태.

인증 활성 여부는 "users 행 존재 여부(부팅 시 캐시) 또는 AUTH_PASSWORD 설정"으로
판정한다. 둘 다 없으면 개발 모드(인증 비활성)로 기존 동작을 유지한다.
"""

from __future__ import annotations

import secrets

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerifyMismatchError

from app.config import settings

_ph = PasswordHasher()


def hash_password(password: str) -> str:
    return _ph.hash(password)


def verify_password(password: str, hashed: str) -> bool:
    try:
        return _ph.verify(hashed, password)
    except (VerifyMismatchError, InvalidHashError, ValueError):
        return False


def admin_bootstrap_email(username: str) -> str:
    """AUTH_USERNAME이 이메일 형식이면 그대로, 아니면 {username}@local (스펙 §3.1)."""
    u = (username or "admin").strip().lower()
    return u if "@" in u else f"{u}@local"


def generate_invite_token() -> str:
    return secrets.token_urlsafe(32)


# ---- 인증 상태 (부팅 시 refresh, 가입 시 set) ----

_users_exist = False


def set_users_exist(value: bool) -> None:
    global _users_exist
    _users_exist = value


def users_exist() -> bool:
    return _users_exist


def is_auth_enabled() -> bool:
    return _users_exist or bool((settings.AUTH_PASSWORD or "").strip())
