"""GET/POST/DELETE /api/me/telegram/* — 연결 토큰·destination 관리."""

from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.control_db import get_session
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


def test_link_token_400_when_getme_fails(monkeypatch):
    _as_user()

    async def _token():
        return "BT"

    async def _no_username(bot_token):
        return ""

    monkeypatch.setattr("app.routers.auth.get_global_telegram_bot_token", _token)
    monkeypatch.setattr("app.routers.auth.get_bot_username", _no_username)
    c = TestClient(app, raise_server_exceptions=False)
    resp = c.post("/api/me/telegram/link-token")
    assert resp.status_code == 400
    assert "봇 정보" in resp.json()["detail"]


class _FakeSession:
    """delete 라우트용 최소 세션: get/delete/commit만 async로 흉내."""

    def __init__(self, dest):
        self.dest = dest
        self.deleted = False
        self.committed = False

    async def get(self, model, pk):
        return self.dest if (self.dest is not None and self.dest.dest_id == pk) else None

    async def delete(self, obj):
        self.deleted = True

    async def commit(self):
        self.committed = True


def _with_fake_session(dest) -> _FakeSession:
    fake = _FakeSession(dest)

    async def _dep():
        yield fake

    app.dependency_overrides[get_session] = _dep
    return fake


def test_delete_foreign_destination_404():
    _as_user()
    fake = _with_fake_session(SimpleNamespace(dest_id=5, user_id=999))  # 남의 destination
    c = TestClient(app, raise_server_exceptions=False)
    resp = c.delete("/api/me/telegram/destinations/5")
    assert resp.status_code == 404
    assert fake.deleted is False
    assert fake.committed is False


def test_delete_own_destination_204():
    _as_user()
    fake = _with_fake_session(SimpleNamespace(dest_id=5, user_id=USER.user_id))
    c = TestClient(app, raise_server_exceptions=False)
    resp = c.delete("/api/me/telegram/destinations/5")
    assert resp.status_code == 204
    assert fake.deleted is True
    assert fake.committed is True
