"""GET /api/me/usage — 본인 플랜·한도·사용량."""

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.routers.auth import CurrentUser, require_user
from app.services.auth_service import set_users_exist

USER = CurrentUser(user_id=2, email="b@x.com", display_name="B", role="user")


@pytest.fixture(autouse=True)
def _cleanup():
    set_users_exist(True)
    yield
    set_users_exist(False)
    app.dependency_overrides.clear()


def test_route_registered():
    assert "/api/me/usage" in {r.path for r in app.routes}


def test_me_usage_shape(monkeypatch):
    async def _dep():
        return USER
    app.dependency_overrides[require_user] = _dep

    from app.services.quota_service import EffectiveLimits

    async def _limits(session, user_id):
        return EffectiveLimits(
            max_groups=1, max_channels_total=5, max_analyses_per_day=10,
            max_video_minutes=60, min_poll_interval_min=60,
            plan_slug="free", plan_name="Free", has_override=False,
        )

    async def _n(session, user_id):
        return 3

    monkeypatch.setattr("app.routers.auth.effective_limits", _limits)
    monkeypatch.setattr("app.routers.auth.count_owned_groups", _n)
    monkeypatch.setattr("app.routers.auth.count_owned_channels", _n)
    monkeypatch.setattr("app.routers.auth.count_daily_deliveries", _n)

    c = TestClient(app, raise_server_exceptions=False)
    data = c.get("/api/me/usage").json()
    assert data["plan_name"] == "Free"
    assert data["limits"]["max_groups"] == 1
    assert data["usage"]["group_count"] == 3
