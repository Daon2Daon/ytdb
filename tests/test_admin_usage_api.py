"""GET /api/admin/usage — 사용자·모델·purpose별 집계."""

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.routers.auth import CurrentUser, require_user
from app.services.auth_service import set_users_exist

ADMIN = CurrentUser(user_id=1, email="a@x.com", display_name="A", role="admin")
USER = CurrentUser(user_id=2, email="u@x.com", display_name="U", role="user")


@pytest.fixture(autouse=True)
def _cleanup():
    set_users_exist(True)
    yield
    set_users_exist(False)
    app.dependency_overrides.clear()


def test_route_registered():
    assert "/api/admin/usage" in {r.path for r in app.routes}


def test_non_admin_403():
    async def _u():
        return USER
    app.dependency_overrides[require_user] = _u
    c = TestClient(app, raise_server_exceptions=False)
    assert c.get("/api/admin/usage").status_code == 403


def test_invalid_window_400():
    async def _a():
        return ADMIN
    app.dependency_overrides[require_user] = _a
    c = TestClient(app, raise_server_exceptions=False)
    assert c.get("/api/admin/usage?window=yesterday").status_code == 400
