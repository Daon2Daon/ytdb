"""digest structured 모드 생성·모드 분기 테스트."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from app.services import digest_service
from app.services.digest_service import DigestAggregate, synthesize_with_llm


def _agg():
    return DigestAggregate(
        video_count=2, sentiment_breakdown={"긍정": 2},
        top_tags=[{"name": "AI", "count": 3}], top_channels=[{"name": "KT", "count": 2}],
        videos=[],
    )


@pytest.mark.anyio
async def test_synthesize_structured_builds_sections(monkeypatch):
    class _Chat:
        content = '{"headline":"H","sections":[{"key":"overview","body_md":"본문"}],' \
                  '"telegram_summary":"T"}'
        input_tokens = 1; output_tokens = 1

    class _Client:
        def __init__(self, *a, **k): pass
        async def chat(self, **k): return _Chat()
        async def aclose(self): pass

    class _AI:
        digest_model = ""; primary_model = "m"; base_url = "u"; max_tokens = 2048

    class _Prof:
        persona = "지식 큐레이터다."
        digest_sections = [{"key": "overview", "kind": "llm", "title": "요약", "guide": "핵심"}]

    monkeypatch.setattr(digest_service, "LiteLLMClient", _Client)
    async def _ai(_g): return _AI()
    monkeypatch.setattr(digest_service, "resolve_ai_gateway", _ai)
    async def _rec(**k): return None
    monkeypatch.setattr(digest_service, "record_usage", _rec)

    class _Mgr:
        async def get_profile(self, _g): return _Prof()
    monkeypatch.setattr(digest_service, "get_settings_manager", lambda: _Mgr())

    from app.services import preset_service
    async def _rp(_g):
        from app.services.preset_service import ResolvedPrompts
        return ResolvedPrompts(analysis_prompt="", digest_prompt="", preset_id=None)
    monkeypatch.setattr(preset_service, "resolve_prompts", _rp)

    start = datetime(2026, 7, 1, tzinfo=timezone.utc)
    end = datetime(2026, 7, 8, tzinfo=timezone.utc)
    gen = await synthesize_with_llm(
        group_id=1, aggregate=_agg(), period_start=start, period_end=end,
        category="", digest_prompt="",
        period_days=7, owner_user_id=1,
    )
    assert gen.headline == "H"
    keys = [s["key"] for s in gen.sections]
    assert "overview" in keys
    assert gen.summary_md
