"""관리자 전용 API: 사용자 목록, 초대 발급/회수, 플랜 조회."""

from __future__ import annotations

import secrets as _secrets
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func as sa_func
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings as app_settings
from app.control_db import get_session
from app.models.control.analysis_delivery import AnalysisDelivery
from app.models.control.channel_subscription import ChannelSubscription
from app.models.control.group import Group
from app.models.control.invitation import Invitation
from app.models.control.plan import Plan
from app.models.control.prompt_preset import PromptPreset
from app.models.control.user import User
from app.models.control.user_limit import UserLimit
from app.routers.auth import CurrentUser, require_admin
from app.schemas.admin import (
    AdminUserOut,
    AdminUserOutV2,
    AdminUserPatch,
    AdminUserUsage,
    GlobalSettingItem,
    GlobalSettingsUpdate,
    InviteCreate,
    InviteCreated,
    InviteOut,
    PlanOut,
    PlanPatch,
    PresetCreate,
    PresetOut,
    PresetPatch,
    TempPasswordOut,
    UserLimitsIn,
    UserLimitsOut,
)
from app.services.auth_service import generate_invite_token, hash_password
from app.services.global_settings import (
    GLOBAL_CENTRAL_POLL_FLOOR_MIN,
    GLOBAL_YOUTUBE_API_KEY,
    SECRET_KEYS,
    get_global,
    set_global,
)
from app.services.preset_service import invalidate_preset_cache
from app.services.quota_service import kst_day_start_utc
from app.services.settings_manager import mask_secret

router = APIRouter(
    prefix="/api/admin", tags=["admin"], dependencies=[Depends(require_admin)]
)

_GLOBAL_KEYS = (GLOBAL_YOUTUBE_API_KEY, GLOBAL_CENTRAL_POLL_FLOOR_MIN)


def _signup_url(token: str) -> str:
    base = (app_settings.PUBLIC_BASE_URL or "").rstrip("/")
    return f"{base}/signup?token={token}"


@router.get("/users", response_model=list[AdminUserOutV2])
async def list_users(session: AsyncSession = Depends(get_session)) -> list[AdminUserOutV2]:
    users = list((await session.execute(select(User).order_by(User.user_id))).scalars().all())

    group_counts = dict(
        (await session.execute(
            select(Group.owner_user_id, sa_func.count())
            .where(Group.owner_user_id.is_not(None))
            .group_by(Group.owner_user_id)
        )).all()
    )
    channel_counts = dict(
        (await session.execute(
            select(Group.owner_user_id, sa_func.count())
            .select_from(ChannelSubscription)
            .join(Group, Group.group_id == ChannelSubscription.group_id)
            .where(Group.owner_user_id.is_not(None))
            .group_by(Group.owner_user_id)
        )).all()
    )
    since = kst_day_start_utc(datetime.now(timezone.utc))
    today_counts = dict(
        (await session.execute(
            select(AnalysisDelivery.user_id, sa_func.count())
            .where(AnalysisDelivery.created_at >= since)
            .group_by(AnalysisDelivery.user_id)
        )).all()
    )
    override_ids = {
        uid for (uid,) in (await session.execute(select(UserLimit.user_id))).all()
    }

    out = []
    for u in users:
        item = AdminUserOutV2.model_validate(u)
        item.usage = AdminUserUsage(
            group_count=group_counts.get(u.user_id, 0),
            channel_count=channel_counts.get(u.user_id, 0),
            today_analyses=today_counts.get(u.user_id, 0),
            has_override=u.user_id in override_ids,
        )
        out.append(item)
    return out


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


@router.get("/presets", response_model=list[PresetOut])
async def list_presets(session: AsyncSession = Depends(get_session)) -> list[PromptPreset]:
    result = await session.execute(select(PromptPreset).order_by(PromptPreset.preset_id.desc()))
    return list(result.scalars().all())


@router.post("/presets", response_model=PresetOut, status_code=201)
async def create_preset(
    payload: PresetCreate, session: AsyncSession = Depends(get_session)
) -> PromptPreset:
    preset = PromptPreset(
        name=payload.name,
        description=payload.description,
        analysis_prompt=payload.analysis_prompt,
        digest_prompt=payload.digest_prompt,
        is_active=True,
    )
    session.add(preset)
    await session.commit()
    await session.refresh(preset)
    invalidate_preset_cache(preset.preset_id)
    return preset


@router.patch("/presets/{preset_id}", response_model=PresetOut)
async def patch_preset(
    preset_id: int, payload: PresetPatch, session: AsyncSession = Depends(get_session)
) -> PromptPreset:
    preset = await session.get(PromptPreset, preset_id)
    if preset is None:
        raise HTTPException(status_code=404, detail="프리셋을 찾을 수 없습니다.")
    data = payload.model_dump(exclude_unset=True)
    for field, value in data.items():
        setattr(preset, field, value)
    await session.commit()
    await session.refresh(preset)
    invalidate_preset_cache(preset.preset_id)
    return preset


@router.get("/global-settings", response_model=list[GlobalSettingItem])
async def list_global_settings(
    session: AsyncSession = Depends(get_session),
) -> list[GlobalSettingItem]:
    out = []
    for key in _GLOBAL_KEYS:
        raw = await get_global(session, key)
        is_secret = key in SECRET_KEYS
        value = mask_secret(raw) if (raw and is_secret) else (raw or "")
        out.append(GlobalSettingItem(key=key, value=value, is_secret=is_secret))
    return out


@router.put("/global-settings", response_model=list[GlobalSettingItem])
async def put_global_settings(
    payload: GlobalSettingsUpdate,
    session: AsyncSession = Depends(get_session),
) -> list[GlobalSettingItem]:
    for item in payload.items:
        if item.key not in _GLOBAL_KEYS:
            raise HTTPException(status_code=400, detail=f"허용되지 않은 키: {item.key}")
        value = item.value.strip()
        if not value:
            continue
        if item.key in SECRET_KEYS:
            current = await get_global(session, item.key)
            # GET이 돌려준 마스킹 값을 그대로 재전송한 경우 — 변경 아님 (set_values와 동일 가드)
            if current and value == mask_secret(current):
                continue
        if item.key == GLOBAL_CENTRAL_POLL_FLOOR_MIN:
            try:
                floor = int(value)
            except ValueError:
                floor = 0
            if floor <= 0:
                raise HTTPException(
                    status_code=400, detail="central_poll_floor_min은 양의 정수여야 합니다."
                )
        await set_global(session, item.key, value)
    await session.commit()
    return await list_global_settings(session)


@router.patch("/users/{user_id}", response_model=AdminUserOut)
async def patch_user(
    user_id: int,
    payload: AdminUserPatch,
    admin: CurrentUser = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
) -> User:
    if payload.status is not None:
        if payload.status not in ("active", "suspended"):
            raise HTTPException(status_code=400, detail="status는 active|suspended만 허용됩니다.")
        if user_id == admin.user_id and payload.status == "suspended":
            raise HTTPException(status_code=400, detail="자기 자신은 정지할 수 없습니다.")
    user = await session.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="사용자를 찾을 수 없습니다.")
    if payload.status is not None:
        user.status = payload.status
    if payload.plan_id is not None:
        plan = await session.get(Plan, payload.plan_id)
        if plan is None:
            raise HTTPException(status_code=400, detail="플랜을 찾을 수 없습니다.")
        user.plan_id = payload.plan_id
    await session.commit()
    await session.refresh(user)
    return user


@router.put("/users/{user_id}/limits", response_model=UserLimitsOut)
async def put_user_limits(
    user_id: int,
    payload: UserLimitsIn,
    session: AsyncSession = Depends(get_session),
) -> UserLimit:
    user = await session.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="사용자를 찾을 수 없습니다.")
    limit = await session.get(UserLimit, user_id)
    if limit is None:
        limit = UserLimit(user_id=user_id)
        session.add(limit)
    for field, value in payload.model_dump().items():
        setattr(limit, field, value)
    await session.commit()
    await session.refresh(limit)
    return limit


@router.delete("/users/{user_id}/limits", status_code=204)
async def delete_user_limits(
    user_id: int, session: AsyncSession = Depends(get_session)
) -> None:
    limit = await session.get(UserLimit, user_id)
    if limit is not None:
        await session.delete(limit)
        await session.commit()


@router.post("/users/{user_id}/temp-password", response_model=TempPasswordOut)
async def issue_temp_password(
    user_id: int, session: AsyncSession = Depends(get_session)
) -> TempPasswordOut:
    user = await session.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="사용자를 찾을 수 없습니다.")
    temp = _secrets.token_urlsafe(9)
    user.password_hash = hash_password(temp)
    await session.commit()
    return TempPasswordOut(temp_password=temp)


@router.patch("/plans/{plan_id}", response_model=PlanOut)
async def patch_plan(
    plan_id: int, payload: PlanPatch, session: AsyncSession = Depends(get_session)
) -> Plan:
    plan = await session.get(Plan, plan_id)
    if plan is None:
        raise HTTPException(status_code=404, detail="플랜을 찾을 수 없습니다.")
    for field, value in payload.model_dump(exclude_unset=True).items():
        if value is not None:
            setattr(plan, field, value)
    await session.commit()
    await session.refresh(plan)
    return plan
