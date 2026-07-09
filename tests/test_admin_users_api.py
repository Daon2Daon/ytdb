"""관리자 사용자 관리 API: 라우트 등록·자기 정지 가드·권한."""

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.routers.auth import CurrentUser, require_user
from app.services.auth_service import set_users_exist

ADMIN = CurrentUser(user_id=1, email="a@x.com", display_name="A", role="admin")
USER = CurrentUser(user_id=2, email="b@x.com", display_name="B", role="user")


@pytest.fixture(autouse=True)
def _cleanup():
    set_users_exist(True)
    yield
    set_users_exist(False)
    app.dependency_overrides.clear()


def test_admin_user_routes_registered():
    paths = {r.path for r in app.routes}
    assert "/api/admin/users/{user_id}" in paths
    assert "/api/admin/users/{user_id}/limits" in paths
    assert "/api/admin/users/{user_id}/temp-password" in paths
    assert "/api/admin/plans/{plan_id}" in paths


def test_non_admin_forbidden():
    async def _dep():
        return USER
    app.dependency_overrides[require_user] = _dep
    c = TestClient(app, raise_server_exceptions=False)
    assert c.patch("/api/admin/users/2", json={}).status_code == 403
    assert c.put("/api/admin/users/2/limits", json={}).status_code == 403
    assert c.post("/api/admin/users/2/temp-password").status_code == 403
    assert c.patch("/api/admin/plans/1", json={}).status_code == 403


def test_admin_cannot_suspend_self():
    async def _dep():
        return ADMIN
    app.dependency_overrides[require_user] = _dep
    c = TestClient(app, raise_server_exceptions=False)
    resp = c.patch("/api/admin/users/1", json={"status": "suspended"})
    assert resp.status_code == 400
    assert "자기 자신" in resp.json()["detail"]
