"""관리자 전용 API: 사용자 목록, 초대 발급/회수, 플랜 조회."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings as app_settings
from app.control_db import get_session
from app.models.control.invitation import Invitation
from app.models.control.plan import Plan
from app.models.control.user import User
from app.routers.auth import CurrentUser, require_admin
from app.schemas.admin import AdminUserOut, InviteCreate, InviteCreated, InviteOut, PlanOut
from app.services.auth_service import generate_invite_token

router = APIRouter(
    prefix="/api/admin", tags=["admin"], dependencies=[Depends(require_admin)]
)


def _signup_url(token: str) -> str:
    base = (app_settings.PUBLIC_BASE_URL or "").rstrip("/")
    return f"{base}/signup?token={token}"


@router.get("/users", response_model=list[AdminUserOut])
async def list_users(session: AsyncSession = Depends(get_session)) -> list[User]:
    result = await session.execute(select(User).order_by(User.user_id))
    return list(result.scalars().all())


@router.get("/plans", response_model=list[PlanOut])
async def list_plans(session: AsyncSession = Depends(get_session)) -> list[Plan]:
    result = await session.execute(select(Plan).order_by(Plan.plan_id))
    return list(result.scalars().all())


@router.get("/invitations", response_model=list[InviteOut])
async def list_invitations(session: AsyncSession = Depends(get_session)) -> list[Invitation]:
    result = await session.execute(select(Invitation).order_by(Invitation.invite_id.desc()))
    return list(result.scalars().all())


@router.post("/invitations", response_model=InviteCreated, status_code=201)
async def create_invitation(
    payload: InviteCreate,
    admin: CurrentUser = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
) -> InviteCreated:
    if payload.plan_slug:
        stmt = select(Plan).where(Plan.slug == payload.plan_slug)
    else:
        stmt = select(Plan).where(Plan.is_default.is_(True))
    plan = (await session.execute(stmt)).scalar_one_or_none()
    if plan is None:
        raise HTTPException(status_code=400, detail="플랜을 찾을 수 없습니다.")
    if admin.user_id == 0:
        raise HTTPException(
            status_code=400, detail="개발 모드에서는 초대를 발급할 수 없습니다."
        )
    invite = Invitation(
        token=generate_invite_token(),
        plan_id=plan.plan_id,
        memo=payload.memo,
        invited_by=admin.user_id,
        expires_at=datetime.now(timezone.utc) + timedelta(days=payload.expires_days),
    )
    session.add(invite)
    await session.commit()
    await session.refresh(invite)
    return InviteCreated(
        **{c.name: getattr(invite, c.name) for c in Invitation.__table__.columns},
        signup_url=_signup_url(invite.token),
    )


@router.delete("/invitations/{invite_id}", status_code=204)
async def revoke_invitation(
    invite_id: int, session: AsyncSession = Depends(get_session)
) -> None:
    invite = await session.get(Invitation, invite_id)
    if invite is None:
        raise HTTPException(status_code=404, detail="초대를 찾을 수 없습니다.")
    if invite.used_at is not None:
        raise HTTPException(status_code=400, detail="이미 사용된 초대는 회수할 수 없습니다.")
    await session.delete(invite)
    await session.commit()
