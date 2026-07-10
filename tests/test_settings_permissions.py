"""§3.3 설정 카테고리 권한 분리: user 차단/필드 필터, admin 전체."""

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.routers.auth import CurrentUser, require_user
from app.routers.deps import get_group_or_404
from app.services.auth_service import set_users_exist

ADMIN = CurrentUser(user_id=1, email="a@x.com", display_name="A", role="admin")
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


def _as(user):
    async def _u():
        return user
    async def _g():
        return _FakeGroup()
    app.dependency_overrides[require_user] = _u
    app.dependency_overrides[get_group_or_404] = _g


def test_user_blocked_categories_404():
    _as(USER)
    c = TestClient(app, raise_server_exceptions=False)
    for cat in ("database", "ai_gateway"):
        assert c.get(f"/api/groups/g1/settings/{cat}").status_code == 404, cat
        r = c.put(f"/api/groups/g1/settings/{cat}", json={"items": []})
        assert r.status_code == 404, cat


def test_user_blocked_fields_put_400(monkeypatch):
    _as(USER)
    c = TestClient(app, raise_server_exceptions=False)
    r = c.put(
        "/api/groups/g1/settings/polling",
        json={"items": [{"key": "youtube_api_key", "value": "x", "value_type": "string"}]},
    )
    assert r.status_code == 400
    r2 = c.put(
        "/api/groups/g1/settings/notification",
        json={"items": [{"key": "bot_token", "value": "x", "value_type": "string"}]},
    )
    assert r2.status_code == 400
    r3 = c.put(
        "/api/groups/g1/settings/prompts",
        json={"items": [{"key": "analysis_prompt", "value": "x", "value_type": "string"}]},
    )
    assert r3.status_code == 400


def test_user_get_filters_secret_fields(monkeypatch):
    _as(USER)

    async def _fake_list(group_id, category):
        return [
            {"key": "youtube_api_key", "value": "***", "value_type": "string"},
            {"key": "window_hours", "value": "48", "value_type": "int"},
        ]

    class _Mgr:
        list_for_api = staticmethod(_fake_list)

    monkeypatch.setattr("app.routers.settings.get_settings_manager", lambda: _Mgr())
    c = TestClient(app, raise_server_exceptions=False)
    keys = {i["key"] for i in c.get("/api/groups/g1/settings/polling").json()}
    assert "youtube_api_key" not in keys
    assert "window_hours" in keys


def test_admin_keeps_full_access(monkeypatch):
    _as(ADMIN)

    async def _fake_list(group_id, category):
        return [{"key": "base_url", "value": "http://x", "value_type": "string"}]

    class _Mgr:
        list_for_api = staticmethod(_fake_list)

    monkeypatch.setattr("app.routers.settings.get_settings_manager", lambda: _Mgr())
    c = TestClient(app, raise_server_exceptions=False)
    assert c.get("/api/groups/g1/settings/ai_gateway").status_code == 200


def test_presets_route_registered():
    paths = {r.path for r in app.routes}
    assert "/api/groups/{slug}/settings/prompts/presets" in paths
