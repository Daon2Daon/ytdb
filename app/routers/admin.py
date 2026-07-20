"""관리자 전용 API: 사용자 목록, 초대 발급/회수, 플랜 조회."""

from __future__ import annotations

import json as _json
import secrets as _secrets
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func as sa_func
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings as app_settings
from app.control_db import get_session
from app.models.control.ai_usage import AIUsage
from app.models.control.analysis_delivery import AnalysisDelivery
from app.models.control.channel_subscription import ChannelSubscription
from app.models.control.group import Group
from app.models.control.invitation import Invitation
from app.models.control.plan import Plan
from app.models.control.prompt_preset import PromptPreset
from app.models.control.user import User
from app.models.control.user_limit import UserLimit
from app.models.control.yt_quota_usage import YtQuotaUsage
from app.routers.auth import CurrentUser, require_admin
from app.schemas.admin import (
    AdminBackfillCostsResponse,
    AdminGroupOut,
    AdminUsageResponse,
    AdminUsageRow,
    AdminUserOut,
    AdminUserOutV2,
    AdminUserPatch,
    AdminUserUsage,
    GlobalSettingItem,
    GlobalSettingsUpdate,
    InviteCreate,
    InviteCreated,
    InviteOut,
    MigrateSchemasResponse,
    MigrationResultOut,
    PlanOut,
    PlanPatch,
    PresetCreate,
    PresetOut,
    PresetPatch,
    TempPasswordOut,
    UserLimitsIn,
    UserLimitsOut,
    YtQuotaEntry,
    YtQuotaStatus,
)
from app.services.ai_usage_service import backfill_null_costs, kst_month_start_utc
from app.services.auth_service import generate_invite_token, hash_password
from app.services.global_settings import (
    GLOBAL_AI_API_KEY,
    GLOBAL_AI_BASE_URL,
    GLOBAL_AI_DIGEST_MODEL,
    GLOBAL_AI_MODEL_PRICES,
    GLOBAL_AI_PRIMARY_MODEL,
    GLOBAL_CENTRAL_POLL_FLOOR_MIN,
    GLOBAL_DB_HOST,
    GLOBAL_DB_NAME,
    GLOBAL_DB_PASSWORD,
    GLOBAL_DB_PORT,
    GLOBAL_DB_SSLMODE,
    GLOBAL_DB_USERNAME,
    GLOBAL_TELEGRAM_BOT_TOKEN,
    GLOBAL_YOUTUBE_API_KEY,
    GLOBAL_YOUTUBE_DAILY_QUOTA,
    SECRET_KEYS,
    get_global,
    get_system_youtube_key,
    get_youtube_daily_quota,
    set_global,
)
from app.services.preset_service import invalidate_preset_cache
from app.services.schema_migrator import migrate_all_schemas, summarize
from app.services.quota_service import kst_day_start_utc
from app.services.settings_manager import mask_secret
from app.services.yt_quota_service import key_fingerprint, pt_today

router = APIRouter(
    prefix="/api/admin", tags=["admin"], dependencies=[Depends(require_admin)]
)

_GLOBAL_KEYS = (
    GLOBAL_YOUTUBE_API_KEY,
    GLOBAL_CENTRAL_POLL_FLOOR_MIN,
    GLOBAL_YOUTUBE_DAILY_QUOTA,
    GLOBAL_AI_BASE_URL,
    GLOBAL_AI_API_KEY,
    GLOBAL_AI_PRIMARY_MODEL,
    GLOBAL_AI_DIGEST_MODEL,
    GLOBAL_AI_MODEL_PRICES,
    GLOBAL_TELEGRAM_BOT_TOKEN,
    GLOBAL_DB_HOST,
    GLOBAL_DB_PORT,
    GLOBAL_DB_NAME,
    GLOBAL_DB_USERNAME,
    GLOBAL_DB_PASSWORD,
    GLOBAL_DB_SSLMODE,
)


def _signup_url(token: str) -> str:
    base = (app_settings.PUBLIC_BASE_URL or "").rstrip("/")
    return f"{base}/signup?token={token}"


def build_yt_quota_entries(
    rows: list[tuple[str, int]], daily_quota: int, system_fp: str
) -> list[YtQuotaEntry]:
    """(key_fp, units) 행 → 엔트리. 시스템 키 우선 정렬, pct는 소수 1자리."""
    entries = [
        YtQuotaEntry(
            key_fp=fp,
            units=units,
            pct=round(units * 100.0 / daily_quota, 1) if daily_quota > 0 else 0.0,
            is_system_key=(fp == system_fp),
        )
        for fp, units in rows
    ]
    entries.sort(key=lambda e: (not e.is_system_key, -e.units))
    return entries


@router.post("/migrate-schemas", response_model=MigrateSchemasResponse)
async def migrate_schemas() -> MigrateSchemasResponse:
    """전 그룹 스키마 순회 마이그레이션 — 동기 실행, 그룹별 리포트 반환."""
    results = await migrate_all_schemas()
    return MigrateSchemasResponse(
        results=[MigrationResultOut(**vars(r)) for r in results],
        summary=summarize(results),
    )


async def list_all_groups_with_owner(session: AsyncSession) -> list[AdminGroupOut]:
    """전체 그룹 + 소유자 이메일. 사이드바(본인 소유만)와 달리 운영 열람용."""
    rows = (
        await session.execute(
            select(Group, User.email)
            .outerjoin(User, User.user_id == Group.owner_user_id)
            .order_by(Group.group_id)
        )
    ).all()
    return [
        AdminGroupOut(
            group_id=g.group_id, slug=g.slug, name=g.name,
            schema_name=g.schema_name, is_active=g.is_active,
            owner_user_id=g.owner_user_id, owner_email=email,
        )
        for g, email in rows
    ]


@router.get("/groups", response_model=list[AdminGroupOut])
async def admin_list_groups(
    session: AsyncSession = Depends(get_session),
) -> list[AdminGroupOut]:
    return await list_all_groups_with_owner(session)


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
        if item.key == GLOBAL_YOUTUBE_DAILY_QUOTA:
            try:
                quota = int(value)
            except ValueError:
                quota = 0
            if quota <= 0:
                raise HTTPException(
                    status_code=400, detail="youtube_daily_quota는 양의 정수여야 합니다."
                )
        if item.key == GLOBAL_AI_MODEL_PRICES:
            try:
                parsed = _json.loads(value)
            except ValueError:
                raise HTTPException(status_code=400, detail="ai_model_prices는 JSON이어야 합니다.")
            if not isinstance(parsed, dict):
                raise HTTPException(status_code=400, detail="ai_model_prices는 JSON 객체여야 합니다.")
        if item.key == GLOBAL_DB_PORT:
            try:
                port = int(value)
            except ValueError:
                port = 0
            if port <= 0:
                raise HTTPException(status_code=400, detail="db_port는 양의 정수여야 합니다.")
        if item.key == GLOBAL_DB_SSLMODE and value not in ("disable", "prefer", "require"):
            raise HTTPException(
                status_code=400, detail="db_sslmode는 disable/prefer/require 중 하나여야 합니다."
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
        user.plan_expiry_notified_at = None  # 플랜 변경 → D-7 가드 리셋
    # E-1: 만료일 tri-state — 생략(변경 없음) / null(해제) / 값(설정). 플랜 수명이
    # 바뀌면 임박 알림 가드를 리셋해 다음 주기 알림을 다시 연다.
    if "plan_expires_at" in payload.model_fields_set:
        user.plan_expires_at = payload.plan_expires_at
        user.plan_expiry_notified_at = None
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


@router.get("/usage", response_model=AdminUsageResponse)
async def usage_summary(
    window: str = "this_month",
    session: AsyncSession = Depends(get_session),
) -> AdminUsageResponse:
    now = datetime.now(timezone.utc)
    if window == "this_month":
        start, end = kst_month_start_utc(now), now
    elif window == "last_month":
        end = kst_month_start_utc(now)
        start = kst_month_start_utc(end - timedelta(seconds=1))
    elif window == "30d":
        start, end = now - timedelta(days=30), now
    else:
        raise HTTPException(status_code=400, detail="window는 this_month|last_month|30d")

    rows_q = (
        await session.execute(
            # columns: user_id, email, model, purpose, calls, input_tokens, output_tokens, cost_usd, null_cost_calls
            select(
                AIUsage.user_id,
                User.email,
                AIUsage.model,
                AIUsage.purpose,
                sa_func.count(AIUsage.usage_id),
                sa_func.coalesce(sa_func.sum(AIUsage.input_tokens), 0),
                sa_func.coalesce(sa_func.sum(AIUsage.output_tokens), 0),
                sa_func.sum(AIUsage.cost_usd),
                sa_func.count(AIUsage.usage_id).filter(AIUsage.cost_usd.is_(None)),
            )
            .outerjoin(User, User.user_id == AIUsage.user_id)
            .where(AIUsage.created_at >= start, AIUsage.created_at < end)
            .group_by(AIUsage.user_id, User.email, AIUsage.model, AIUsage.purpose)
            .order_by(AIUsage.user_id.asc().nulls_first(), AIUsage.model)
        )
    ).all()

    out_rows = [
        AdminUsageRow(
            user_id=r[0], email=r[1], model=r[2], purpose=r[3], calls=r[4],
            input_tokens=r[5], output_tokens=r[6],
            cost_usd=float(r[7]) if r[7] is not None else None,
            null_cost_calls=r[8],
        )
        for r in rows_q
    ]
    # D-2: 당일(PT) YouTube 쿼터 현황
    today_pt = pt_today()
    yt_rows = (
        await session.execute(
            select(YtQuotaUsage.key_fp, YtQuotaUsage.units).where(
                YtQuotaUsage.usage_date == today_pt
            )
        )
    ).all()
    daily_quota = await get_youtube_daily_quota(session)
    system_key = await get_system_youtube_key()
    system_fp = key_fingerprint(system_key) if system_key else ""
    youtube = YtQuotaStatus(
        usage_date=today_pt,
        daily_quota=daily_quota,
        entries=build_yt_quota_entries([(r[0], r[1]) for r in yt_rows], daily_quota, system_fp),
    )

    return AdminUsageResponse(
        window=window, start=start, end=end, rows=out_rows,
        total_cost_usd=sum(r.cost_usd or 0.0 for r in out_rows),
        null_cost_row_count=sum(r.null_cost_calls for r in out_rows),
        youtube=youtube,
    )


@router.post("/usage/backfill-costs", response_model=AdminBackfillCostsResponse)
async def backfill_costs(
    session: AsyncSession = Depends(get_session),
) -> AdminBackfillCostsResponse:
    """단가 등록 전 기록된 cost_usd NULL 원장 행을 현재 단가표로 소급 계산한다."""
    updated, remaining = await backfill_null_costs(session)
    return AdminBackfillCostsResponse(updated=updated, remaining_null=remaining)
