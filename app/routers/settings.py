"""그룹별 설정 조회/저장 API."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from app.models.control.group import Group
from app.routers.deps import get_group_or_404
from app.schemas.setting import SettingItem, SettingsUpdate
from app.services.llm_client import LiteLLMClient, LiteLLMError
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


@router.get("/{category}", response_model=list[SettingItem])
async def get_settings(
    category: str, group: Group = Depends(get_group_or_404)
) -> list[dict]:
    _check_category(category)
    mgr = get_settings_manager()
    return await mgr.list_for_api(group.group_id, category)


@router.put("/{category}", response_model=list[SettingItem])
async def put_settings(
    category: str,
    payload: SettingsUpdate,
    group: Group = Depends(get_group_or_404),
) -> list[dict]:
    _check_category(category)
    mgr = get_settings_manager()
    await mgr.set_values(
        group.group_id,
        category,
        [item.model_dump() for item in payload.items],
    )
    return await mgr.list_for_api(group.group_id, category)


@router.get("/ai_gateway/models", response_model=list[str])
async def list_ai_gateway_models(group: Group = Depends(get_group_or_404)) -> list[str]:
    """저장된 ai_gateway(base_url/api_key)로 모델 목록을 조회한다."""
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
