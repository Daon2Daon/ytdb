"""프리셋 로드/해석. 분석 경로가 영상마다 제어 DB를 치지 않도록 TTL 캐시를 둔다.

resolve_prompts()가 그룹 프롬프트의 단일 진입점이다:
- preset_id가 설정되고 프리셋이 활성이면 프리셋 본문 사용 (캐시 참여 대상)
- 그 외(직접 프롬프트/비활성/미존재)는 직접 프롬프트 폴백 (캐시 비참여)
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Optional

from sqlalchemy import select

from app.control_db import get_sessionmaker
from app.models.control.prompt_preset import PromptPreset
from app.services.settings_manager import get_settings_manager

_CACHE_TTL_SEC = 60.0
_cache: dict[int, tuple[float, Optional["PresetData"]]] = {}


@dataclass(frozen=True)
class PresetData:
    preset_id: int
    analysis_prompt: str
    digest_prompt: str
    is_active: bool


@dataclass(frozen=True)
class ResolvedPrompts:
    analysis_prompt: str
    digest_prompt: str
    # None = 직접 프롬프트(공유 캐시 비참여). int = 캐시 키로 쓰는 프리셋.
    preset_id: Optional[int]


def invalidate_preset_cache(preset_id: Optional[int] = None) -> None:
    if preset_id is None:
        _cache.clear()
    else:
        _cache.pop(preset_id, None)


async def get_preset(preset_id: int) -> Optional[PresetData]:
    now = time.monotonic()
    hit = _cache.get(preset_id)
    if hit is not None and now < hit[0]:
        return hit[1]
    async with get_sessionmaker()() as session:
        row = (
            await session.execute(
                select(PromptPreset).where(PromptPreset.preset_id == preset_id)
            )
        ).scalar_one_or_none()
    data = (
        PresetData(
            preset_id=row.preset_id,
            analysis_prompt=row.analysis_prompt,
            digest_prompt=row.digest_prompt,
            is_active=row.is_active,
        )
        if row is not None
        else None
    )
    _cache[preset_id] = (now + _CACHE_TTL_SEC, data)
    return data


async def resolve_prompts(group_id: int) -> ResolvedPrompts:
    prompts = await get_settings_manager().get_prompts(group_id)
    if prompts.preset_id is not None:
        preset = await get_preset(prompts.preset_id)
        if preset is not None and preset.is_active:
            return ResolvedPrompts(
                analysis_prompt=preset.analysis_prompt,
                digest_prompt=preset.digest_prompt,
                preset_id=preset.preset_id,
            )
        # 비활성/미존재 프리셋 → 직접 프롬프트 폴백(분석은 계속되게, 캐시 비참여)
    return ResolvedPrompts(
        analysis_prompt=prompts.analysis_prompt,
        digest_prompt=prompts.digest_prompt,
        preset_id=None,
    )
