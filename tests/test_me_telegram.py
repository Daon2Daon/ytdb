"""GET/POST/DELETE /api/me/telegram/* — 연결 토큰·destination 관리."""

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.routers.auth import CurrentUser, require_user
from app.services.auth_service import set_users_exist

USER = CurrentUser(user_id=2, email="u@x.com", display_name="U", role="user")


@pytest.fixture(autouse=True)
def _cleanup():
    set_users_exist(True)
    yield
    set_users_exist(False)
    app.dependency_overrides.clear()


def _as_user():
    async def _u():
        return USER
    app.dependency_overrides[require_user] = _u


def test_routes_registered():
    paths = {r.path for r in app.routes}
    assert "/api/me/telegram/link-token" in paths
    assert "/api/me/telegram/destinations" in paths
    assert "/api/me/telegram/destinations/{dest_id}" in paths


def test_link_token_400_when_bot_unset(monkeypatch):
    _as_user()

    async def _no_token():
        return ""

    monkeypatch.setattr("app.routers.auth.get_global_telegram_bot_token", _no_token)
    c = TestClient(app, raise_server_exceptions=False)
    resp = c.post("/api/me/telegram/link-token")
    assert resp.status_code == 400
    assert "공용 봇" in resp.json()["detail"]


def test_link_token_returns_deep_link(monkeypatch):
    _as_user()

    async def _token():
        return "BT"

    async def _username(bot_token):
        return "my_bot"

    async def _issue(session, user_id):
        from datetime import datetime, timezone
        return "tok123", datetime.now(timezone.utc)

    monkeypatch.setattr("app.routers.auth.get_global_telegram_bot_token", _token)
    monkeypatch.setattr("app.routers.auth.get_bot_username", _username)
    monkeypatch.setattr("app.routers.auth.issue_link_token", _issue)
    c = TestClient(app, raise_server_exceptions=False)
    data = c.post("/api/me/telegram/link-token").json()
    assert data["deep_link"] == "https://t.me/my_bot?start=tok123"
    assert data["expires_in_sec"] == 600
