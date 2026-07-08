"""관리자 API 권한(비관리자 403)과 라우트 등록 검증."""

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


def test_admin_routes_registered():
    paths = {r.path for r in app.routes}
    assert "/api/admin/users" in paths
    assert "/api/admin/invitations" in paths
    assert "/api/admin/invitations/{invite_id}" in paths
    assert "/api/admin/plans" in paths
    assert "/api/admin/global-settings" in paths          # B-0b


def test_non_admin_forbidden():
    async def _dep():
        return USER
    app.dependency_overrides[require_user] = _dep
    c = TestClient(app, raise_server_exceptions=False)
    assert c.get("/api/admin/users").status_code == 403
    assert c.get("/api/admin/invitations").status_code == 403
    assert c.post("/api/admin/invitations", json={}).status_code == 403


def test_non_admin_forbidden_global_settings():
    async def _dep():
        return USER
    app.dependency_overrides[require_user] = _dep
    c = TestClient(app, raise_server_exceptions=False)
    assert c.get("/api/admin/global-settings").status_code == 403
    assert c.put("/api/admin/global-settings", json={"items": []}).status_code == 403


def test_unauthenticated_401():
    c = TestClient(app, raise_server_exceptions=False)
    assert c.get("/api/admin/users").status_code == 401
