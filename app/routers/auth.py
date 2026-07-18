"""DB 기반 다중 사용자 인증 (httpOnly 세션 쿠키).

- 개발 모드(users 없음 + AUTH_PASSWORD 미설정): 인증 비활성. require_user는 가상 admin.
- 활성 모드: 세션의 user_id로 매 요청 사용자 로드(정지 계정 즉시 차단).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.control_db import get_session, get_sessionmaker
from app.models.control.invitation import Invitation
from app.models.control.telegram_destination import TelegramDestination
from app.models.control.user import User
from app.schemas.auth import (
    LoginRequest,
    MeResponse,
    MyLimits,
    MyUsage,
    MyUsageResponse,
    SignupRequest,
    TelegramDestinationOut,
    TelegramLinkResponse,
    UserOut,
)
from app.services.ai_usage_service import month_cost_usd
from app.services.auth_service import hash_password, is_auth_enabled, set_users_exist, verify_password
from app.services.global_settings import get_global_telegram_bot_token
from app.services.quota_service import (
    count_daily_deliveries,
    count_owned_channels,
    count_owned_groups,
    effective_limits,
)
from app.services.telegram_link_service import (
    LINK_TOKEN_TTL_SEC,
    build_deep_link,
    get_bot_username,
    issue_link_token,
)

router = APIRouter(prefix="/api/auth", tags=["auth"])


@dataclass(frozen=True)
class CurrentUser:
    user_id: int
    email: str
    display_name: str | None
    role: str

    @property
    def is_admin(self) -> bool:
        return self.role == "admin"


# 개발 모드(인증 비활성)에서 모든 요청에 부여되는 가상 관리자.
DEV_ADMIN = CurrentUser(user_id=0, email="dev@local", display_name="개발 모드", role="admin")


async def _load_user(user_id: int) -> User | None:
    """세션 user_id → users 행. 테스트에서 monkeypatch 대상."""
    async with get_sessionmaker()() as session:
        return await session.get(User, user_id)


def _to_current(user: User) -> CurrentUser:
    return CurrentUser(
        user_id=user.user_id, email=user.email,
        display_name=user.display_name, role=user.role,
    )


async def require_user(request: Request) -> CurrentUser:
    """보호 라우터 의존성. 인증 비활성이면 가상 admin, 활성이면 세션+DB 검증."""
    if not is_auth_enabled():
        return DEV_ADMIN
    user_id = request.session.get("user_id")
    if not user_id:
        raise HTTPException(status_code=401, detail="인증이 필요합니다.")
    user = await _load_user(int(user_id))
    if user is None:
        request.session.clear()
        raise HTTPException(status_code=401, detail="인증이 필요합니다.")
    if user.status != "active":
        raise HTTPException(status_code=403, detail="정지된 계정입니다.")
    return _to_current(user)


async def require_admin(user: CurrentUser = Depends(require_user)) -> CurrentUser:
    if not user.is_admin:
        raise HTTPException(status_code=403, detail="관리자 권한이 필요합니다.")
    return user


@router.get("/me", response_model=MeResponse)
async def me(request: Request) -> MeResponse:
    if not is_auth_enabled():
        return MeResponse(
            auth_enabled=False, authenticated=True,
            user=UserOut(email=DEV_ADMIN.email, display_name=DEV_ADMIN.display_name, role="admin"),
        )
    user_id = request.session.get("user_id")
    user = await _load_user(int(user_id)) if user_id else None
    if user is None or user.status != "active":
        return MeResponse(auth_enabled=True, authenticated=False, user=None)
    return MeResponse(
        auth_enabled=True, authenticated=True,
        user=UserOut(email=user.email, display_name=user.display_name, role=user.role),
    )


@router.post("/login", response_model=UserOut)
async def login(
    payload: LoginRequest, request: Request, session: AsyncSession = Depends(get_session)
) -> UserOut:
    if not is_auth_enabled():
        raise HTTPException(status_code=400, detail="인증이 설정되지 않았습니다.")
    result = await session.execute(select(User).where(User.email == payload.email))
    user = result.scalar_one_or_none()
    if user is None or not verify_password(payload.password, user.password_hash):
        raise HTTPException(status_code=401, detail="이메일 또는 비밀번호가 올바르지 않습니다.")
    if user.status != "active":
        raise HTTPException(status_code=403, detail="정지된 계정입니다.")
    user.last_login_at = datetime.now(timezone.utc)
    await session.commit()
    request.session["user_id"] = user.user_id
    return UserOut(email=user.email, display_name=user.display_name, role=user.role)


@router.post("/logout", status_code=204)
async def logout(request: Request) -> None:
    request.session.clear()


@router.post("/signup", response_model=UserOut, status_code=201)
async def signup(
    payload: SignupRequest, request: Request, session: AsyncSession = Depends(get_session)
) -> UserOut:
    """초대 토큰으로 가입. 성공 시 초대 소진 + 자동 로그인."""
    result = await session.execute(
        select(Invitation).where(Invitation.token == payload.token)
    )
    invite = result.scalar_one_or_none()
    now = datetime.now(timezone.utc)
    if invite is None or invite.used_at is not None or invite.expires_at <= now:
        raise HTTPException(status_code=400, detail="유효하지 않거나 만료된 초대입니다.")

    dup = await session.execute(select(User).where(User.email == payload.email))
    if dup.scalar_one_or_none() is not None:
        raise HTTPException(status_code=409, detail="이미 가입된 이메일입니다.")

    user = User(
        email=payload.email,
        password_hash=hash_password(payload.password),
        display_name=payload.display_name,
        role="user",
        status="active",
        plan_id=invite.plan_id,
    )
    session.add(user)
    await session.flush()
    invite.used_by = user.user_id
    invite.used_at = now
    await session.commit()

    set_users_exist(True)
    request.session["user_id"] = user.user_id
    return UserOut(email=user.email, display_name=user.display_name, role=user.role)


me_router = APIRouter(prefix="/api/me", tags=["me"])


@me_router.get("/usage", response_model=MyUsageResponse)
async def my_usage(
    user: CurrentUser = Depends(require_user),
    session: AsyncSession = Depends(get_session),
) -> MyUsageResponse:
    limits = await effective_limits(session, user.user_id)
    usage = MyUsage(
        group_count=await count_owned_groups(session, user.user_id),
        channel_count=await count_owned_channels(session, user.user_id),
        today_analyses=await count_daily_deliveries(session, user.user_id),
        month_cost_usd=float(await month_cost_usd(session, user.user_id)),
    )
    db_user = await session.get(User, user.user_id)
    expires = db_user.plan_expires_at if db_user else None
    if limits is None:
        # admin 또는 개발 모드 — 무제한
        return MyUsageResponse(
            plan_name="Unlimited", plan_slug="unlimited", unlimited=True, usage=usage,
            plan_expires_at=expires,
        )
    return MyUsageResponse(
        plan_name=limits.plan_name,
        plan_slug=limits.plan_slug,
        limits=MyLimits(
            max_groups=limits.max_groups,
            max_channels_total=limits.max_channels_total,
            max_analyses_per_day=limits.max_analyses_per_day,
            max_video_minutes=limits.max_video_minutes,
            min_poll_interval_min=limits.min_poll_interval_min,
            monthly_cost_budget_usd=limits.monthly_cost_budget_usd,
        ),
        usage=usage,
        plan_expires_at=expires,
    )


@me_router.post("/telegram/link-token", response_model=TelegramLinkResponse)
async def create_telegram_link_token(
    user: CurrentUser = Depends(require_user),
    session: AsyncSession = Depends(get_session),
) -> TelegramLinkResponse:
    bot_token = await get_global_telegram_bot_token()
    if not bot_token:
        raise HTTPException(status_code=400, detail="관리자가 공용 봇을 설정해야 합니다.")
    username = await get_bot_username(bot_token)
    if not username:
        raise HTTPException(status_code=400, detail="봇 정보 조회에 실패했습니다. 잠시 후 다시 시도하세요.")
    token, _ = await issue_link_token(session, user.user_id)
    await session.commit()
    return TelegramLinkResponse(
        deep_link=build_deep_link(username, token), expires_in_sec=LINK_TOKEN_TTL_SEC
    )


@me_router.get("/telegram/destinations", response_model=list[TelegramDestinationOut])
async def list_telegram_destinations(
    user: CurrentUser = Depends(require_user),
    session: AsyncSession = Depends(get_session),
) -> list[TelegramDestination]:
    rows = (
        await session.execute(
            select(TelegramDestination)
            .where(TelegramDestination.user_id == user.user_id)
            .order_by(TelegramDestination.dest_id)
        )
    ).scalars().all()
    return list(rows)


@me_router.delete("/telegram/destinations/{dest_id}", status_code=204)
async def delete_telegram_destination(
    dest_id: int,
    user: CurrentUser = Depends(require_user),
    session: AsyncSession = Depends(get_session),
) -> None:
    dest = await session.get(TelegramDestination, dest_id)
    if dest is None or dest.user_id != user.user_id:
        raise HTTPException(status_code=404, detail="연결을 찾을 수 없습니다.")
    await session.delete(dest)
    await session.commit()
