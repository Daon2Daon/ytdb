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


# ---- 부트스트랩 시드 (lifespan에서 ensure_control_schema 이후 호출) ----

PLAN_SEEDS: list[dict] = [
    {
        "slug": "free", "name": "Free", "max_groups": 1, "max_channels_total": 5,
        "max_analyses_per_day": 10, "max_video_minutes": 60,
        "monthly_cost_budget_usd": "5.0", "min_poll_interval_min": 60, "is_default": True,
    },
    {
        "slug": "unlimited", "name": "Unlimited", "max_groups": 1000,
        "max_channels_total": 100000, "max_analyses_per_day": 100000,
        # NUMERIC(10,4) 컬럼의 최대값(10^6 미만)에 맞춘 사실상의 무제한 값.
        "max_video_minutes": 100000, "monthly_cost_budget_usd": "999999.9999",
        "min_poll_interval_min": 1, "is_default": False,
    },
]


async def bootstrap_auth() -> None:
    """플랜 시드 → admin 시드 → 그룹 소유자 백필 → 인증 상태 캐시. 멱등."""
    from decimal import Decimal

    from sqlalchemy import select, update

    from app.control_db import get_sessionmaker
    from app.models.control.group import Group
    from app.models.control.plan import Plan
    from app.models.control.user import User

    async with get_sessionmaker()() as session:
        # 1) 플랜 시드 (slug 기준 멱등)
        existing = {
            s for (s,) in (await session.execute(select(Plan.slug))).all()
        }
        for seed in PLAN_SEEDS:
            if seed["slug"] not in existing:
                session.add(Plan(**{**seed, "monthly_cost_budget_usd": Decimal(seed["monthly_cost_budget_usd"])}))
        await session.flush()

        # 2) users 비어있고 AUTH_PASSWORD 설정 시 admin 시드
        has_user = (await session.execute(select(User.user_id).limit(1))).first() is not None
        if not has_user and (settings.AUTH_PASSWORD or "").strip():
            plan_id = (
                await session.execute(select(Plan.plan_id).where(Plan.slug == "unlimited"))
            ).scalar_one()
            session.add(
                User(
                    email=admin_bootstrap_email(settings.AUTH_USERNAME),
                    password_hash=hash_password(settings.AUTH_PASSWORD),
                    display_name=settings.AUTH_USERNAME,
                    role="admin",
                    status="active",
                    plan_id=plan_id,
                )
            )
            await session.flush()
            has_user = True

        # 3) 소유자 없는 기존 그룹은 첫 admin 소유로 백필 (스펙 §2.8)
        admin_id = (
            await session.execute(
                select(User.user_id).where(User.role == "admin").order_by(User.user_id).limit(1)
            )
        ).scalar_one_or_none()
        if admin_id is not None:
            await session.execute(
                update(Group).where(Group.owner_user_id.is_(None)).values(owner_user_id=admin_id)
            )

        await session.commit()
        set_users_exist(has_user)
