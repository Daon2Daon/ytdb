"""그룹별 설정 조회/저장 API."""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select

from app.control_db import get_sessionmaker
from app.models.control.group import Group
from app.models.control.prompt_preset import PromptPreset
from app.models.control.telegram_destination import TelegramDestination
from app.routers.auth import CurrentUser, require_user
from app.routers.deps import get_group_or_404
from app.schemas.setting import SettingItem, SettingsUpdate
from app.services.llm_client import LiteLLMClient, LiteLLMError
from app.config import settings as app_settings
from app.services.channel_registry_service import resync_group as registry_resync_group
from app.services.notify_service import _should_stamp_on_save
from app.services.quota_service import limits_for_group_owner, validate_poll_interval
from app.services.scheduler import apply_pending_analysis_schedule
from app.services.settings_manager import get_settings_manager

router = APIRouter(prefix="/api/groups/{slug}/settings", tags=["settings"])

ALLOWED_CATEGORIES = {
    "database",
    "ai_gateway",
    "prompts",
    "polling",
    "notification",
    "digest",
}


def _check_category(category: str) -> None:
    if category not in ALLOWED_CATEGORIES:
        raise HTTPException(
            status_code=400,
            detail=f"허용되지 않은 카테고리: {category}. 가능: {sorted(ALLOWED_CATEGORIES)}",
        )


# §3.3 설정 카테고리 권한 (설계 §6). admin은 전체, user는 아래 제한.
ADMIN_ONLY_CATEGORIES = {"database", "ai_gateway"}
# user에게 허용되는 키만 나열(화이트리스트) — 그 외 키는 GET 제외·PUT 400
USER_FIELD_ALLOWLIST: dict[str, set[str]] = {"prompts": {"preset_id"}}
# user에게 차단되는 키(블랙리스트) — 나머지는 허용.
# 불변식: 화이트리스트가 블랙리스트보다 우선하므로 한 카테고리가 두 dict에 동시에 있으면 안 된다.
USER_FIELD_BLOCKLIST: dict[str, set[str]] = {
    "polling": {"youtube_api_key"},
    "notification": {"bot_token", "chat_ids"},
}


def _check_user_category(category: str, user: CurrentUser) -> None:
    if not user.is_admin and category in ADMIN_ONLY_CATEGORIES:
        # 타 카테고리 존재를 노출하지 않도록 미존재와 동일 취급 (§3.3 은닉)
        raise HTTPException(status_code=404, detail="설정을 찾을 수 없습니다.")


def _filter_items_for_user(category: str, user: CurrentUser, items: list[dict]) -> list[dict]:
    if user.is_admin:
        return items
    allow = USER_FIELD_ALLOWLIST.get(category)
    if allow is not None:
        return [i for i in items if i["key"] in allow]
    block = USER_FIELD_BLOCKLIST.get(category, set())
    return [i for i in items if i["key"] not in block]


def _reject_blocked_puts(category: str, user: CurrentUser, items: list[SettingItem]) -> None:
    if user.is_admin:
        return
    allow = USER_FIELD_ALLOWLIST.get(category)
    block = USER_FIELD_BLOCKLIST.get(category, set())
    for item in items:
        if allow is not None and item.key not in allow:
            raise HTTPException(status_code=400, detail=f"수정 권한이 없는 항목: {item.key}")
        if item.key in block:
            raise HTTPException(status_code=400, detail=f"수정 권한이 없는 항목: {item.key}")


async def _dest_owned_and_active(dest_id: int, owner_user_id: int) -> bool:
    async with get_sessionmaker()() as session:
        dest = await session.get(TelegramDestination, dest_id)
        return bool(dest is not None and dest.user_id == owner_user_id and dest.is_active)


# 주의: 이 라우트는 @router.get("/{category}")보다 먼저 선언되어야 한다 (FastAPI 선언 순서 매칭).
@router.get("/prompts/presets")
async def list_active_presets(group: Group = Depends(get_group_or_404)) -> list[dict]:
    """활성 프리셋 id/이름/설명 — 사용자 프리셋 선택용(본문 비노출)."""
    async with get_sessionmaker()() as session:
        rows = (
            await session.execute(
                select(PromptPreset)
                .where(PromptPreset.is_active.is_(True))
                .order_by(PromptPreset.preset_id)
            )
        ).scalars().all()
    return [
        {"preset_id": p.preset_id, "name": p.name, "description": p.description or ""}
        for p in rows
    ]


@router.get("/{category}", response_model=list[SettingItem])
async def get_settings(
    category: str,
    group: Group = Depends(get_group_or_404),
    user: CurrentUser = Depends(require_user),
) -> list[dict]:
    _check_category(category)
    _check_user_category(category, user)
    mgr = get_settings_manager()
    items = await mgr.list_for_api(group.group_id, category)
    return _filter_items_for_user(category, user, items)


@router.put("/{category}", response_model=list[SettingItem])
async def put_settings(
    category: str,
    payload: SettingsUpdate,
    group: Group = Depends(get_group_or_404),
    user: CurrentUser = Depends(require_user),
) -> list[dict]:
    _check_category(category)
    _check_user_category(category, user)
    _reject_blocked_puts(category, user, payload.items)

    if category == "polling":
        limits = await limits_for_group_owner(group)
        if limits is not None:
            for item in payload.items:
                if item.key != "default_channel_interval_min":
                    continue
                try:
                    interval = int(item.value)
                except (TypeError, ValueError):
                    continue  # 타입 오류는 기존 set_values 검증에 맡긴다
                if not validate_poll_interval(limits, interval):
                    raise HTTPException(
                        status_code=400,
                        detail=(
                            f"폴링 주기는 플랜 하한({limits.min_poll_interval_min}분) "
                            "이상이어야 합니다."
                        ),
                    )

    if category == "notification":
        for item in payload.items:
            if item.key != "dest_id":
                continue
            raw = str(item.value or "").strip()
            if raw in ("", "0"):
                continue  # 클리어 허용
            try:
                did = int(raw)
            except ValueError:
                continue  # 타입 오류는 set_values 검증에 맡김
            if group.owner_user_id is None:
                raise HTTPException(
                    status_code=400,
                    detail="이 그룹은 직접 봇 설정을 사용합니다(텔레그램 연결 선택 불가).",
                )
            if not await _dest_owned_and_active(did, group.owner_user_id):
                raise HTTPException(status_code=400, detail="유효하지 않은 텔레그램 연결입니다.")

    mgr = get_settings_manager()

    before_sendable = False
    if category == "notification":
        before_sendable = (await mgr.get_notification(group.group_id)).is_sendable

    await mgr.set_values(
        group.group_id,
        category,
        [item.model_dump() for item in payload.items],
    )

    if category == "notification":
        after = await mgr.get_notification(group.group_id)
        if _should_stamp_on_save(
            before_sendable=before_sendable, after_sendable=after.is_sendable
        ):
            now_iso = datetime.now(timezone.utc).isoformat()
            await mgr.set_values(
                group.group_id,
                "notification",
                [{"key": "notify_baseline_at", "value": now_iso, "value_type": "string"}],
            )

    if category == "polling":
        # default_channel_interval_min/window_hours 변경이 구독 유효값에 반영되도록
        await registry_resync_group(group)
        if app_settings.SCHEDULER_ENABLED:
            await apply_pending_analysis_schedule()
    # PUT 응답도 GET과 동일하게 user 필드 필터 적용 — 차단 필드 값 유출 방지 (§3.3)
    items = await mgr.list_for_api(group.group_id, category)
    return _filter_items_for_user(category, user, items)


@router.get("/ai_gateway/models", response_model=list[str])
async def list_ai_gateway_models(
    group: Group = Depends(get_group_or_404),
    user: CurrentUser = Depends(require_user),
) -> list[str]:
    """저장된 ai_gateway(base_url/api_key)로 모델 목록을 조회한다."""
    if not user.is_admin:
        raise HTTPException(status_code=404, detail="설정을 찾을 수 없습니다.")
    mgr = get_settings_manager()
    cfg = await mgr.get_ai_gateway(group.group_id)
    if not cfg.base_url or not cfg.api_key:
        raise HTTPException(
            status_code=400,
            detail="ai_gateway의 base_url/api_key를 먼저 저장하세요.",
        )
    client = LiteLLMClient(cfg)
    try:
        models = await client.get_models(force_refresh=True)
        return sorted(models)
    except LiteLLMError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    finally:
        await client.aclose()
