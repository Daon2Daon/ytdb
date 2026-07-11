"""월 예산 강제 지점 테스트 (설계 §7 — 사용자 귀속 비용 행위만 차단)."""

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.routers.auth import CurrentUser, require_user
from app.routers.deps import get_group_or_404
from app.services.ai_usage_service import BudgetExceeded
from app.services.auth_service import set_users_exist

USER = CurrentUser(user_id=2, email="u@x.com", display_name="U", role="user")


class _FakeGroup:
    group_id = 10
    slug = "g1"
    owner_user_id = 2
    schema_name = "youtube_g1"


@pytest.fixture(autouse=True)
def _cleanup():
    set_users_exist(True)
    yield
    set_users_exist(False)
    app.dependency_overrides.clear()


def _as_user_with_group():
    async def _u():
        return USER
    async def _g():
        return _FakeGroup()
    app.dependency_overrides[require_user] = _u
    app.dependency_overrides[get_group_or_404] = _g


def test_digest_generate_budget_exceeded_400(monkeypatch):
    _as_user_with_group()

    async def _deny(group):
        raise BudgetExceeded("월 AI 예산 초과: 당월 $5.10 / 예산 $5.00", limit=5.0, current=5.1)

    monkeypatch.setattr("app.routers.digests._budget_gate", _deny)
    c = TestClient(app, raise_server_exceptions=False)
    resp = c.post("/api/groups/g1/digests/generate", json={})
    assert resp.status_code == 400
    assert "월 AI 예산 초과" in resp.json()["detail"]


def test_analyze_now_custom_prompt_budget_400(monkeypatch):
    _as_user_with_group()

    async def _deny(group):
        raise BudgetExceeded("월 AI 예산 초과", limit=5.0, current=5.1)

    monkeypatch.setattr("app.routers.videos._budget_gate", _deny)
    c = TestClient(app, raise_server_exceptions=False)
    resp = c.post(
        "/api/groups/g1/videos/1/analyze-now",
        json={"custom_prompt": "요약해줘"},
    )
    assert resp.status_code == 400
    assert "월 AI 예산" in resp.json()["detail"]


async def test_budget_gate_helper_passes_without_budget(monkeypatch):
    """owner 없음/예산 없음이면 통과."""
    from app.services import ai_usage_service as aus

    class _G:
        owner_user_id = None

    ok, reason = await aus.budget_ok_for_group(_G())
    assert ok is True and reason == ""
