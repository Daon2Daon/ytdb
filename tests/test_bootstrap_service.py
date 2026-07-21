"""그룹 프로필 부트스트랩 테스트."""

from __future__ import annotations

import pytest

from app.services.bootstrap_service import normalize_bootstrap_output


def test_normalize_bootstrap_output_valid():
    raw = (
        '{"persona":"지식 큐레이터다.",'
        '"digest_sections":[{"key":"overview","kind":"llm","title":"요약","guide":"핵심"},'
        '{"key":"top_tags","kind":"computed","title":"태그"}]}'
    )
    persona, sections = normalize_bootstrap_output(raw)
    assert persona == "지식 큐레이터다."
    assert [s["key"] for s in sections] == ["overview", "top_tags"]


def test_normalize_bootstrap_output_too_few_sections_uses_default():
    raw = '{"persona":"P","digest_sections":[{"key":"overview","kind":"llm","title":"요약"}]}'
    persona, sections = normalize_bootstrap_output(raw)
    assert persona == "P"
    assert len(sections) >= 2


def test_normalize_bootstrap_output_bad_json_raises():
    with pytest.raises(ValueError):
        normalize_bootstrap_output("not json")


@pytest.mark.anyio
async def test_bootstrap_profile_budget_gate_records_failed(monkeypatch):
    from app.services import bootstrap_service
    from app.services.group_profile import GroupProfile

    captured = {}

    class _Mgr:
        async def get_profile(self, _g):
            return GroupProfile()
        async def set_values(self, gid, cat, items):
            captured.update({i["key"]: i["value"] for i in items})

    monkeypatch.setattr(bootstrap_service, "get_settings_manager", lambda: _Mgr())
    async def _budget(_g): return (False, "예산 초과")
    monkeypatch.setattr(bootstrap_service, "budget_ok_for_group", _budget)

    class _Group:
        group_id = 1; name = "G"; slug = "g"; description = ""; owner_user_id = 1

    await bootstrap_service.bootstrap_profile(_Group(), force=True)
    assert captured["bootstrap_status"] == "failed"
