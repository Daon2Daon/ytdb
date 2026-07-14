"""기록 지점 배선 테스트 — 다이제스트 경로 (monkeypatch, DB·LLM 불필요)."""

from types import SimpleNamespace

from app.services.llm_client import ChatResult
from app.services.settings_types import AIGatewaySettings

FAKE_AI = AIGatewaySettings(
    base_url="http://x:4000", api_key="k",
    primary_model="gemini/m", tagging_model="gemini/m", digest_model="",
    temperature=0.2, max_tokens=1024, daily_budget_usd=2.0,
)


async def test_synthesize_records_user_attributed_usage(monkeypatch):
    from datetime import datetime, timezone

    from app.services import digest_service as ds

    recorded = {}

    async def _fake_resolve(group_id):
        return FAKE_AI

    class _FakeClient:
        def __init__(self, ai): pass
        async def chat(self, **kw):
            return ChatResult(
                content='{"headline":"h","summary_md":"s","telegram_summary":"t"}',
                raw={}, input_tokens=500, output_tokens=200,
            )
        async def aclose(self): pass

    async def _fake_record(**kw):
        recorded.update(kw)

    async def _fake_prompts(group_id):
        return SimpleNamespace(digest_prompt="", analysis_prompt="")

    monkeypatch.setattr(ds, "LiteLLMClient", _FakeClient)
    monkeypatch.setattr(ds, "record_usage", _fake_record)
    # digest_service는 resolve_ai_gateway를 from-import로 직접 바인딩하므로
    # global_settings 모듈이 아니라 ds 쪽 심볼을 패치해야 실제로 대체된다.
    # (모듈 경로 패치는 무효 — DB 도달 가능할 때만 우연히 통과하던 잠복 버그)
    monkeypatch.setattr(ds, "resolve_ai_gateway", _fake_resolve)
    monkeypatch.setattr("app.services.preset_service.resolve_prompts", _fake_prompts)

    agg = SimpleNamespace(
        video_count=1, sentiment_breakdown={}, top_tags=[], top_channels=[], videos=[],
    )
    now = datetime.now(timezone.utc)
    await ds.synthesize_with_llm(
        group_id=10, aggregate=agg, period_start=now, period_end=now,
        owner_user_id=2,
    )
    assert recorded["user_id"] == 2          # 사용자 귀속 (스펙 §4 표 3행)
    assert recorded["group_id"] == 10
    assert recorded["purpose"] == "digest"
    assert recorded["input_tokens"] == 500 and recorded["output_tokens"] == 200
