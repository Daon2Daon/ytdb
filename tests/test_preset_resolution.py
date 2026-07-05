"""resolve_prompts 분기: 프리셋 우선, 비활성/미존재 폴백, 미지정 직접 프롬프트."""

import pytest

from app.services import preset_service
from app.services.preset_service import PresetData, ResolvedPrompts, resolve_prompts
from app.services.settings_types import PromptSettings


class FakeManager:
    def __init__(self, prompts: PromptSettings):
        self._prompts = prompts

    async def get_prompts(self, group_id: int) -> PromptSettings:
        return self._prompts


def _patch(monkeypatch, prompts: PromptSettings, preset: PresetData | None):
    monkeypatch.setattr(preset_service, "get_settings_manager", lambda: FakeManager(prompts))

    async def fake_get_preset(preset_id: int):
        return preset

    monkeypatch.setattr(preset_service, "get_preset", fake_get_preset)


async def test_preset_active_wins(monkeypatch):
    _patch(
        monkeypatch,
        PromptSettings(analysis_prompt="직접", digest_prompt="직접d", preset_id=7),
        PresetData(preset_id=7, analysis_prompt="프리셋", digest_prompt="프리셋d", is_active=True),
    )
    r = await resolve_prompts(1)
    assert r == ResolvedPrompts(analysis_prompt="프리셋", digest_prompt="프리셋d", preset_id=7)


async def test_inactive_preset_falls_back_to_direct(monkeypatch):
    _patch(
        monkeypatch,
        PromptSettings(analysis_prompt="직접", digest_prompt="", preset_id=7),
        PresetData(preset_id=7, analysis_prompt="프리셋", digest_prompt="", is_active=False),
    )
    r = await resolve_prompts(1)
    assert r.preset_id is None and r.analysis_prompt == "직접"


async def test_missing_preset_falls_back(monkeypatch):
    _patch(monkeypatch, PromptSettings(analysis_prompt="직접", preset_id=99), None)
    r = await resolve_prompts(1)
    assert r.preset_id is None and r.analysis_prompt == "직접"


async def test_no_preset_id_uses_direct(monkeypatch):
    _patch(monkeypatch, PromptSettings(analysis_prompt="직접", digest_prompt="d"), None)
    r = await resolve_prompts(1)
    assert r == ResolvedPrompts(analysis_prompt="직접", digest_prompt="d", preset_id=None)


def test_prompt_settings_has_preset_id_field():
    assert PromptSettings().preset_id is None
