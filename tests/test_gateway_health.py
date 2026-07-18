"""게이트웨이 헬스체크·모델 목록의 전역 폴백 검증 (2026-07-18 회귀).

전역 ai_* 설정만 있고 그룹 ai_gateway가 비어 있어도 "미설정" 오류가 나면 안 된다
— 실행 파이프라인(resolve_ai_gateway)과 판정이 일치해야 한다.
"""

from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.services.settings_types import AIGatewaySettings

GROUP = SimpleNamespace(group_id=1, slug="g")
ADMIN = SimpleNamespace(user_id=1, is_admin=True)


def _resolved(api_key: str) -> AIGatewaySettings:
    return AIGatewaySettings(base_url="http://litellm:4000", api_key=api_key)


class _FakeClient:
    def __init__(self, cfg):
        self.cfg = cfg

    async def get_models(self, force_refresh=False):
        return ["gemini/gemini-2.5-flash"]

    async def aclose(self):
        pass


async def test_gateway_health_uses_global_fallback(monkeypatch):
    """그룹 설정 없이 전역 키만 있어도 healthy — get_ai_gateway가 아닌 resolve 사용."""
    from app.routers import health as health_router

    async def fake_resolve(group_id):
        return _resolved("global-key")

    monkeypatch.setattr(health_router, "resolve_ai_gateway", fake_resolve)
    monkeypatch.setattr(health_router, "LiteLLMClient", _FakeClient)

    out = await health_router.gateway_health(group=GROUP)
    assert out.success
    assert "1개" in out.message


async def test_gateway_health_unconfigured_when_no_key_anywhere(monkeypatch):
    from app.routers import health as health_router

    async def fake_resolve(group_id):
        return _resolved("")

    monkeypatch.setattr(health_router, "resolve_ai_gateway", fake_resolve)
    out = await health_router.gateway_health(group=GROUP)
    assert not out.success
    assert "미설정" in out.message


async def test_model_list_uses_global_fallback(monkeypatch):
    from app.routers import settings as settings_router

    async def fake_resolve(group_id):
        return _resolved("global-key")

    monkeypatch.setattr(settings_router, "resolve_ai_gateway", fake_resolve)
    monkeypatch.setattr(settings_router, "LiteLLMClient", _FakeClient)

    models = await settings_router.list_ai_gateway_models(group=GROUP, user=ADMIN)
    assert models == ["gemini/gemini-2.5-flash"]


async def test_model_list_400_when_no_key_anywhere(monkeypatch):
    from app.routers import settings as settings_router

    async def fake_resolve(group_id):
        return _resolved("")

    monkeypatch.setattr(settings_router, "resolve_ai_gateway", fake_resolve)
    with pytest.raises(HTTPException) as exc:
        await settings_router.list_ai_gateway_models(group=GROUP, user=ADMIN)
    assert exc.value.status_code == 400
